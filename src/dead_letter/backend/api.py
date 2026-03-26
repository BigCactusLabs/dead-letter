"""FastAPI app exposing async backend job APIs."""

from __future__ import annotations

import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.jobs import JobManager
from dead_letter.backend.settings import SettingsStore
from dead_letter.backend.schemas import (
    BatchImportStartResponse,
    ErrorItem,
    FsEntryResponse,
    FsListResponse,
    ImportStartResponse,
    JobCancelResponse,
    JobCreateRequest,
    JobCreateResponse,
    JobHistoryResponse,
    JobOptions,
    JobRetryRequest,
    JobStatusResponse,
    OpenFolderResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    WatchStartRequest,
    WatchStatusResponse,
)
from dead_letter.backend.settings import WorkflowSettings
from dead_letter.backend.watch import WatchManager

ERROR_RESPONSES = {
    400: {"description": "Bad request"},
    403: {"description": "Forbidden"},
    404: {"description": "Not found"},
    409: {"description": "Conflict"},
    500: {"description": "Internal server error"},
}

_MAX_IMPORT_COLLISION_INDEX = 10_000
_IMPORT_STREAM_CHUNK_SIZE = 1024 * 1024


class _UploadReader(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


def _import_target_path(settings: WorkflowSettings, filename: str, *, index: int = 1) -> Path:
    safe_name = Path(filename).name or "upload.eml"
    stem = Path(safe_name).stem or "upload"
    suffix = f"-{index}" if index > 1 else ""
    return (settings.inbox_path / f"{stem}{suffix}.eml").resolve()


def _batch_import_target_path(batch_dir: Path, filename: str, *, index: int = 1) -> Path:
    safe_name = Path(filename).name or "upload.eml"
    stem = Path(safe_name).stem or "upload"
    suffix = f"-{index}" if index > 1 else ""
    return (batch_dir / f"{stem}{suffix}.eml").resolve()


async def _stream_upload_to_file(candidate: Path, upload: _UploadReader) -> None:
    with candidate.open("xb") as handle:
        while True:
            chunk = await upload.read(_IMPORT_STREAM_CHUNK_SIZE)
            if not chunk:
                return
            handle.write(chunk)


async def _write_import_file(settings: WorkflowSettings, filename: str, content: _UploadReader) -> Path:
    settings.inbox_path.mkdir(parents=True, exist_ok=True)
    index = 1
    while index <= _MAX_IMPORT_COLLISION_INDEX:
        candidate = _import_target_path(settings, filename, index=index)
        try:
            await _stream_upload_to_file(candidate, content)
            return candidate
        except FileExistsError:
            index += 1
        except Exception:
            candidate.unlink(missing_ok=True)
            raise
    raise RuntimeError(
        f"could not find unique filename after {_MAX_IMPORT_COLLISION_INDEX} attempts"
    )


async def _write_batch_import_file(batch_dir: Path, filename: str, content: _UploadReader) -> Path:
    batch_dir.mkdir(parents=True, exist_ok=True)
    index = 1
    while index <= _MAX_IMPORT_COLLISION_INDEX:
        candidate = _batch_import_target_path(batch_dir, filename, index=index)
        try:
            await _stream_upload_to_file(candidate, content)
            return candidate
        except FileExistsError:
            index += 1
        except Exception:
            candidate.unlink(missing_ok=True)
            raise
    raise RuntimeError(
        f"could not find unique filename after {_MAX_IMPORT_COLLISION_INDEX} attempts"
    )


def _validation_error_payload(exc: RequestValidationError) -> dict[str, object]:
    errors = [
        ErrorItem(
            path=".".join(str(part) for part in err.get("loc", [])[1:]) or None,
            code="validation_error",
            message=str(err.get("msg", "invalid request")),
            stage="validation",
        ).model_dump()
        for err in exc.errors()
    ]
    return {"errors": errors}


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
    path: str | None = None,
    stage: str = "validation",
) -> JSONResponse:
    payload = ErrorItem(path=path, code=code, message=message, stage=stage).model_dump()
    return JSONResponse(status_code=status_code, content={"errors": [payload]})


def create_app(
    *,
    worker_count: int = 8,
    manager: JobManager | None = None,
    browser: FilesystemBrowser | None = None,
    settings_path: str | Path | None = None,
    watch_manager: WatchManager | None = None,
) -> FastAPI:
    fs_browser = browser or FilesystemBrowser()
    settings = SettingsStore(settings_path)
    configured = settings.load()
    job_manager = manager or JobManager(
        worker_count=worker_count,
        inbox_root=configured.inbox_path if configured is not None else None,
        cabinet_root=configured.cabinet_path if configured is not None else None,
    )
    if configured is not None and hasattr(job_manager, "update_roots"):
        job_manager.update_roots(
            inbox_root=configured.inbox_path,
            cabinet_root=configured.cabinet_path,
        )
    watcher = watch_manager or WatchManager(browser=fs_browser)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await watcher.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.job_manager = job_manager
    app.state.fs_browser = fs_browser
    app.state.settings = settings
    app.state.watch_manager = watcher

    def _current_settings() -> WorkflowSettings | None:
        return cast(WorkflowSettings | None, app.state.settings.load())

    def _resolve_watch_target(path: str) -> Path:
        raw_path = Path(path).expanduser()
        if raw_path.is_absolute():
            return raw_path.resolve()
        normalized_path = fs_browser.normalize_relative(path)
        return fs_browser.resolve_relative(normalized_path)

    def _parse_import_options(raw_options: str) -> JobOptions | JSONResponse:
        try:
            return JobOptions.model_validate_json(raw_options)
        except ValidationError as exc:
            return _error_response(
                400,
                code="invalid_request",
                message=str(exc),
                path="options",
                stage="validation",
            )

    async def _watch_status_response() -> WatchStatusResponse:
        status = watcher.status()
        latest_job_status = None
        if status.latest_job_id is not None:
            snapshot = await job_manager.get_job(status.latest_job_id)
            if snapshot is not None:
                latest_job_status = snapshot.status
        return WatchStatusResponse(
            active=status.active,
            path=status.path,
            files_detected=status.files_detected,
            jobs_created=status.jobs_created,
            failed_events=status.failed_events,
            last_error=status.last_error,
            latest_job_id=status.latest_job_id,
            latest_job_status=latest_job_status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request, exc: RequestValidationError):
        return JSONResponse(status_code=400, content=_validation_error_payload(exc))

    @app.post(
        "/api/jobs",
        response_model=JobCreateResponse,
        status_code=202,
        responses={400: ERROR_RESPONSES[400], 500: ERROR_RESPONSES[500]},
    )
    async def create_job(request: JobCreateRequest) -> JobCreateResponse:
        configured = _current_settings()
        if configured is None:
            return _error_response(
                409,
                code="invalid_request",
                message="workflow settings are not configured",
                path="settings",
                stage="validation",
            )
        if hasattr(app.state.job_manager, "update_roots"):
            app.state.job_manager.update_roots(
                inbox_root=configured.inbox_path,
                cabinet_root=configured.cabinet_path,
            )
        try:
            return await job_manager.create_job(request)
        except ValueError as exc:
            return _error_response(
                400,
                path=request.input_path,
                code="invalid_request",
                message=str(exc),
                stage="validation",
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            return _error_response(
                500,
                path=None,
                code="backend_error",
                message=str(exc),
                stage="backend",
            )

    @app.post(
        "/api/jobs/{job_id}/retry",
        response_model=JobCreateResponse,
        status_code=202,
        responses={400: ERROR_RESPONSES[400], 404: ERROR_RESPONSES[404], 409: ERROR_RESPONSES[409]},
    )
    async def retry_job(job_id: str, request: JobRetryRequest) -> JobCreateResponse:
        try:
            return await job_manager.retry_job(job_id, request.action)
        except KeyError:
            return _error_response(404, code="invalid_request", message="unknown job", path=job_id)
        except ValueError as exc:
            return _error_response(
                400,
                path=job_id,
                code="invalid_request",
                message=str(exc),
                stage="validation",
            )
        except RuntimeError as exc:
            return _error_response(409, code="invalid_request", message=str(exc), path=job_id)

    @app.get("/api/settings", response_model=SettingsResponse, status_code=200)
    async def get_settings() -> SettingsResponse:
        configured = app.state.settings.load()
        if configured is None:
            return SettingsResponse(configured=False, inbox_path=None, cabinet_path=None)
        return SettingsResponse(
            configured=True,
            inbox_path=str(configured.inbox_path),
            cabinet_path=str(configured.cabinet_path),
        )

    @app.put(
        "/api/settings",
        response_model=SettingsResponse,
        status_code=200,
        responses={400: ERROR_RESPONSES[400], 500: ERROR_RESPONSES[500]},
    )
    async def put_settings(request: SettingsUpdateRequest) -> SettingsResponse:
        try:
            configured = app.state.settings.save(
                inbox_path=request.inbox_path,
                cabinet_path=request.cabinet_path,
            )
        except ValueError as exc:
            field = "cabinet_path" if "Cabinet" in str(exc) else "inbox_path"
            return _error_response(
                400,
                path=field,
                code="invalid_request",
                message=str(exc),
                stage="validation",
            )
        except OSError as exc:
            return _error_response(
                500,
                path=None,
                code="backend_error",
                message=str(exc),
                stage="backend",
            )

        if hasattr(app.state.job_manager, "update_roots"):
            app.state.job_manager.update_roots(
                inbox_root=configured.inbox_path,
                cabinet_root=configured.cabinet_path,
            )

        return SettingsResponse(
            configured=True,
            inbox_path=str(configured.inbox_path),
            cabinet_path=str(configured.cabinet_path),
        )

    @app.get(
        "/api/jobs/history",
        response_model=JobHistoryResponse,
        status_code=200,
    )
    async def get_job_history(limit: int = 50) -> JobHistoryResponse:
        jobs, totals = await job_manager.list_terminal_jobs(limit=max(1, min(limit, 200)))
        return JobHistoryResponse(jobs=jobs, totals=totals)

    @app.get(
        "/api/jobs/{job_id}",
        response_model=JobStatusResponse,
        status_code=200,
        responses={404: ERROR_RESPONSES[404]},
    )
    async def get_job(job_id: str) -> JobStatusResponse:
        snapshot = await job_manager.get_job(job_id)
        if snapshot is None:
            return _error_response(404, code="invalid_request", message="unknown job", path=job_id)
        return snapshot

    @app.post(
        "/api/jobs/{job_id}/cancel",
        response_model=JobCancelResponse,
        status_code=202,
        responses={404: ERROR_RESPONSES[404], 409: ERROR_RESPONSES[409]},
    )
    async def cancel_job(job_id: str) -> JobCancelResponse:
        outcome = await job_manager.cancel_job(job_id)
        if outcome is None:
            return _error_response(404, code="invalid_request", message="unknown job", path=job_id)
        if not outcome.accepted:
            return _error_response(
                409,
                code="invalid_request",
                message="job is already terminal",
                path=job_id,
            )
        return outcome

    @app.get(
        "/api/fs/list",
        response_model=FsListResponse,
        status_code=200,
        responses={
            400: ERROR_RESPONSES[400],
            403: ERROR_RESPONSES[403],
            404: ERROR_RESPONSES[404],
        },
    )
    async def list_directory(path: str = "", filter: str | None = None) -> FsListResponse:
        try:
            entries = fs_browser.list_dir(path, filter_ext=filter)
            normalized_path = fs_browser.normalize_relative(path)
        except PermissionError as exc:
            return _error_response(403, code="invalid_request", message=str(exc), path=path)
        except FileNotFoundError as exc:
            return _error_response(404, code="invalid_request", message=str(exc), path=path)
        except ValueError as exc:
            return _error_response(400, code="invalid_request", message=str(exc), path=path)

        return FsListResponse(
            path=normalized_path,
            entries=[
                FsEntryResponse(
                    name=entry.name,
                    path=entry.path,
                    input_path=entry.input_path,
                    type=entry.type,
                    size=entry.size,
                    modified=entry.modified,
                )
                for entry in entries
            ],
        )

    @app.post(
        "/api/import",
        response_model=ImportStartResponse,
        status_code=202,
        responses={400: ERROR_RESPONSES[400], 409: ERROR_RESPONSES[409], 500: ERROR_RESPONSES[500]},
    )
    async def import_file(
        file: UploadFile = File(...),
        options: str = Form("{}"),
    ) -> ImportStartResponse:
        configured = _current_settings()
        if configured is None:
            return _error_response(
                409,
                code="invalid_request",
                message="workflow settings are not configured",
                path="settings",
                stage="validation",
            )
        parsed_options = _parse_import_options(options)
        if isinstance(parsed_options, JSONResponse):
            return parsed_options

        filename = file.filename or "upload.eml"
        if not filename.lower().endswith(".eml"):
            return _error_response(
                400,
                code="invalid_request",
                message="only .eml files are accepted",
                path=filename,
            )

        imported_path: Path | None = None
        try:
            imported_path = await _write_import_file(configured, filename, file)
            job = await app.state.job_manager.create_job(
                JobCreateRequest(mode="file", input_path=str(imported_path), options=parsed_options),
                origin="import",
            )
        except (OSError, RuntimeError) as exc:
            if imported_path is not None:
                try:
                    imported_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return _error_response(
                500,
                code="backend_error",
                message=str(exc),
                path=filename,
                stage="backend",
            )

        watch_status = watcher.status()
        if watch_status.active and watch_status.path:
            watch_path = Path(watch_status.path).expanduser().resolve()
            if imported_path.is_relative_to(watch_path) and hasattr(watcher, "suppress_path"):
                watcher.suppress_path(imported_path)

        return ImportStartResponse(
            imported_path=str(imported_path),
            id=job.id,
            status=job.status,
            output_location=job.output_location,
        )

    @app.post(
        "/api/import-batch",
        response_model=BatchImportStartResponse,
        status_code=202,
        responses={
            400: ERROR_RESPONSES[400],
            409: ERROR_RESPONSES[409],
            422: {"description": "Unprocessable Entity"},
            500: ERROR_RESPONSES[500],
        },
    )
    async def import_batch(
        files: list[UploadFile] = File(...),
        options: str = Form("{}"),
    ) -> BatchImportStartResponse:
        configured = _current_settings()
        if configured is None:
            return _error_response(
                409,
                code="invalid_request",
                message="workflow settings are not configured",
                path="settings",
                stage="validation",
            )
        parsed_options = _parse_import_options(options)
        if isinstance(parsed_options, JSONResponse):
            return parsed_options
        if not files:
            return _error_response(
                400,
                code="invalid_request",
                message="no files provided",
                path="files",
            )

        invalid_names = [
            upload.filename or "unknown"
            for upload in files
            if not (upload.filename or "").lower().endswith(".eml")
        ]
        if invalid_names:
            return _error_response(
                422,
                code="invalid_request",
                message=f"non-.eml files are not accepted: {', '.join(invalid_names)}",
                path="files",
            )

        batch_dir = configured.inbox_path / f"_batch-{uuid4()}"
        imported_paths: list[str] = []
        try:
            for upload in files:
                written = await _write_batch_import_file(
                    batch_dir,
                    upload.filename or "upload.eml",
                    upload,
                )
                imported_paths.append(str(written))

            job = await app.state.job_manager.create_job(
                JobCreateRequest(
                    mode="directory",
                    input_path=str(batch_dir.resolve()),
                    options=parsed_options,
                ),
                origin="import",
            )
        except (OSError, RuntimeError) as exc:
            shutil.rmtree(batch_dir, ignore_errors=True)
            return _error_response(
                500,
                code="backend_error",
                message=str(exc),
                path="files",
                stage="backend",
            )

        return BatchImportStartResponse(
            imported_paths=imported_paths,
            id=job.id,
            status=job.status,
            output_location=job.output_location,
        )

    @app.get("/api/watch", response_model=WatchStatusResponse, status_code=200)
    async def get_watch_status() -> WatchStatusResponse:
        return await _watch_status_response()

    @app.post(
        "/api/watch",
        response_model=WatchStatusResponse,
        status_code=200,
        responses={
            400: ERROR_RESPONSES[400],
            403: ERROR_RESPONSES[403],
            404: ERROR_RESPONSES[404],
            409: ERROR_RESPONSES[409],
        },
    )
    async def start_watch(request: WatchStartRequest) -> WatchStatusResponse:
        configured = _current_settings()
        if configured is None:
            return _error_response(
                409,
                code="invalid_request",
                message="workflow settings are not configured",
                path="settings",
                stage="validation",
            )
        watch_path = request.path or str(configured.inbox_path)
        try:
            resolved_watch_path = _resolve_watch_target(watch_path)
        except PermissionError as exc:
            return _error_response(403, code="invalid_request", message=str(exc), path=watch_path)
        if resolved_watch_path.is_relative_to(configured.cabinet_path):
            return _error_response(
                400,
                code="invalid_request",
                message="watch target cannot be inside Cabinet",
                path=watch_path,
            )
        try:
            await watcher.start(watch_path, request.options.model_dump(), job_manager)
        except PermissionError as exc:
            return _error_response(403, code="invalid_request", message=str(exc), path=watch_path)
        except FileNotFoundError as exc:
            return _error_response(404, code="invalid_request", message=str(exc), path=watch_path)
        except ValueError as exc:
            return _error_response(400, code="invalid_request", message=str(exc), path=watch_path)
        except RuntimeError as exc:
            return _error_response(409, code="invalid_request", message=str(exc), path=watch_path)

        return await _watch_status_response()

    @app.delete("/api/watch", response_model=WatchStatusResponse, status_code=200)
    async def stop_watch() -> WatchStatusResponse:
        await watcher.stop()
        return await _watch_status_response()

    @app.post(
        "/api/open-folder",
        response_model=OpenFolderResponse,
        status_code=200,
        responses={409: ERROR_RESPONSES[409], 500: ERROR_RESPONSES[500]},
    )
    async def open_folder() -> OpenFolderResponse:
        configured = _current_settings()
        if configured is None:
            return _error_response(
                409,
                code="invalid_request",
                message="workflow settings are not configured",
                path="settings",
                stage="validation",
            )
        cabinet_path = configured.cabinet_path
        try:
            cabinet_path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(cabinet_path)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(cabinet_path)])
            else:
                subprocess.Popen(["xdg-open", str(cabinet_path)])
        except OSError as exc:
            return _error_response(
                500,
                code="backend_error",
                message=str(exc),
                path=str(cabinet_path),
                stage="backend",
            )
        return OpenFolderResponse(path=str(cabinet_path))

    return app
