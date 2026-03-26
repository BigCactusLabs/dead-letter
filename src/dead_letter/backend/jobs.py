"""In-memory job orchestration for backend API."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dead_letter.backend.schemas import (
    AggregateTotals,
    ErrorItem,
    JobCancelResponse,
    JobCreateRequest,
    JobCreateResponse,
    JobStatus,
    JobStatusResponse,
    OutputLocation,
    Progress,
    QualityDiagnostics,
    Summary,
)
from dead_letter.core import convert_to_bundle as core_convert_to_bundle
from dead_letter.core._pipeline import convert_to_bundle_with_diagnostics as core_convert_to_bundle_with_diagnostics
from dead_letter.core.types import BundleResult, ConvertOptions

logger = logging.getLogger(__name__)

ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "completed_with_errors", "failed", "cancelled"},
    "succeeded": set(),
    "completed_with_errors": set(),
    "failed": set(),
    "cancelled": set(),
}

TERMINAL_STATUSES: set[JobStatus] = {"succeeded", "completed_with_errors", "failed", "cancelled"}
_DEFAULT_CORE_CONVERT_TO_BUNDLE = core_convert_to_bundle
_RETRY_WITH_HTML_REPAIR = "retry_with_html_repair"
_RETRY_WITH_HTML_FALLBACK = "retry_with_html_fallback"
_HTML_REPAIR_ACTION = {
    "kind": _RETRY_WITH_HTML_REPAIR,
    "label": "Retry with HTML repair",
    "message": "HTML conversion failed in strict mode. Retry this file with the targeted HTML repair enabled.",
}
_HTML_FALLBACK_ACTION = {
    "kind": _RETRY_WITH_HTML_FALLBACK,
    "label": "Retry with plain-text fallback",
    "message": "HTML conversion failed after repair. Retry this file with plain-text fallback enabled.",
}


def _report_filename_for_job(job_id: str) -> str:
    return f".dead-letter-report-{job_id}.json"


@dataclass(slots=True)
class _JobRecord:
    id: str
    request: JobCreateRequest
    files: list[Path]
    cabinet_root: Path
    output_location: OutputLocation
    status: JobStatus = "queued"
    progress: Progress = field(default_factory=Progress)
    summary: Summary = field(default_factory=Summary)
    errors: list[ErrorItem] = field(default_factory=list)
    recovery_actions: list[dict[str, str]] = field(default_factory=list)
    diagnostics: QualityDiagnostics | None = None
    report_entries: list = field(default_factory=list)
    report_path: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    origin: str = "manual"
    cancel_requested: bool = False


def bundle_result_to_error(result: BundleResult) -> ErrorItem:
    return ErrorItem(
        path=str(result.source),
        code="conversion_error",
        message=result.error or "unknown conversion error",
        stage="core",
    )


def report_source_for_path(path: Path, *, input_path: str, mode: str) -> str:
    if mode == "directory":
        try:
            return path.relative_to(Path(input_path).expanduser().resolve()).as_posix()
        except ValueError:
            pass
    return path.name


def run_bundle_conversion(
    path: str | Path,
    *,
    bundle_root: str | Path,
    options: ConvertOptions,
    source_handling: str,
) -> tuple[BundleResult, dict[str, object] | None]:
    if core_convert_to_bundle is not _DEFAULT_CORE_CONVERT_TO_BUNDLE:
        return (
            core_convert_to_bundle(
                path,
                bundle_root=bundle_root,
                options=options,
                source_handling=source_handling,
            ),
            None,
        )
    return core_convert_to_bundle_with_diagnostics(
        path,
        bundle_root=bundle_root,
        options=options,
        source_handling=source_handling,
    )


class JobManager:
    """Coordinates asynchronous conversion jobs with poll/cancel semantics."""

    def __init__(
        self,
        *,
        worker_count: int = 8,
        max_retained_terminal_jobs: int = 2000,
        inbox_root: str | Path | None = None,
        cabinet_root: str | Path | None = None,
    ) -> None:
        self._worker_count = max(1, int(worker_count))
        self._max_retained_terminal_jobs = max(1, int(max_retained_terminal_jobs))
        self._inbox_root = Path(inbox_root or (Path.home() / "Inbox")).expanduser().resolve()
        self._cabinet_root = Path(cabinet_root or (Path.home() / "Cabinet")).expanduser().resolve()
        self._jobs: dict[str, _JobRecord] = {}
        self._lock = asyncio.Lock()

    def update_roots(self, *, inbox_root: str | Path, cabinet_root: str | Path) -> None:
        self._inbox_root = Path(inbox_root).expanduser().resolve()
        self._cabinet_root = Path(cabinet_root).expanduser().resolve()

    async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> JobCreateResponse:
        files = self._resolve_files(request)
        job_id = uuid4().hex
        cabinet_root = self._cabinet_root
        output_location = self._output_location_for_request(request, cabinet_root=cabinet_root)
        record = _JobRecord(
            id=job_id,
            request=request,
            files=files,
            cabinet_root=cabinet_root,
            output_location=output_location,
            progress=Progress(total=len(files), completed=0, failed=0, current=None),
            origin=origin,
        )

        async with self._lock:
            self._jobs[job_id] = record

        asyncio.create_task(self._run_job(job_id))
        return JobCreateResponse(
            id=job_id,
            status=record.status,
            output_location=record.output_location.model_copy(deep=True),
        )

    async def get_job(self, job_id: str) -> JobStatusResponse | None:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None
            return self._to_response(record)

    async def list_terminal_jobs(self, limit: int = 50) -> tuple[list[JobStatusResponse], AggregateTotals]:
        async with self._lock:
            terminal = [
                record for record in self._jobs.values()
                if record.status in TERMINAL_STATUSES
            ]
            terminal.sort(
                key=lambda r: (r.finished_at or r.created_at, r.created_at, r.id),
                reverse=True,
            )

            total_written = 0
            total_skipped = 0
            total_errors = 0
            for record in terminal:
                total_written += record.summary.written
                total_skipped += record.summary.skipped
                total_errors += record.summary.errors

            totals = AggregateTotals(
                jobs_completed=len(terminal),
                total_written=total_written,
                total_skipped=total_skipped,
                total_errors=total_errors,
            )

            responses = [self._to_response(record) for record in terminal[:limit]]
            return responses, totals

    async def cancel_job(self, job_id: str) -> JobCancelResponse | None:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None

            if record.status in TERMINAL_STATUSES:
                return JobCancelResponse(id=job_id, status=record.status, accepted=False)

            record.cancel_requested = True
            if record.status == "queued":
                self._transition(record, "cancelled")

            return JobCancelResponse(id=job_id, status=record.status, accepted=True)

    async def retry_job(self, job_id: str, action: str) -> JobCreateResponse:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise KeyError(job_id)
            if record.status not in TERMINAL_STATUSES:
                raise RuntimeError("job is not terminal")
            if action not in {_RETRY_WITH_HTML_REPAIR, _RETRY_WITH_HTML_FALLBACK}:
                raise ValueError(f"unsupported retry action: {action}")
            if not any(item.get("kind") == action for item in record.recovery_actions):
                raise RuntimeError("retry action is not available for this job")

            option_updates = (
                {"allow_html_repair_on_panic": True}
                if action == _RETRY_WITH_HTML_REPAIR
                else {"allow_fallback_on_html_error": True}
            )
            request = record.request.model_copy(
                update={"options": record.request.options.model_copy(update=option_updates)}
            )
            origin = record.origin

        return await self.create_job(request, origin=origin)

    async def wait_for_terminal(self, job_id: str, *, timeout: float = 5.0) -> JobStatusResponse:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            snapshot = await self.get_job(job_id)
            if snapshot is None:
                raise KeyError(job_id)
            if snapshot.status in TERMINAL_STATUSES:
                return snapshot

            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(job_id)

            await asyncio.sleep(0.01)

    def _resolve_files(self, request: JobCreateRequest) -> list[Path]:
        input_path = Path(request.input_path).expanduser().resolve()
        if input_path.is_relative_to(self._cabinet_root):
            raise ValueError(f"Cabinet sources are not allowed: {input_path}")

        if request.mode == "file":
            if not input_path.exists() or not input_path.is_file():
                raise ValueError(f"Input file does not exist: {input_path}")
            if input_path.suffix.lower() != ".eml":
                raise ValueError(f"Input must be a .eml file: {input_path}")
            return [input_path]

        if not input_path.exists() or not input_path.is_dir():
            raise ValueError(f"Input directory does not exist: {input_path}")

        files: list[Path] = []
        for candidate in input_path.rglob("*"):
            if candidate.suffix.lower() != ".eml" or not candidate.is_file():
                continue
            try:
                if not candidate.resolve().is_relative_to(input_path):
                    continue
            except OSError:
                continue
            files.append(candidate)
        return sorted(files)

    def _output_location_for_request(
        self, request: JobCreateRequest, *, cabinet_root: Path
    ) -> OutputLocation:
        input_path = Path(request.input_path).expanduser().resolve()
        bundle_path = None
        if request.mode == "file":
            bundle_path = str((cabinet_root / (input_path.stem or "message")).resolve())
        return OutputLocation(
            strategy="cabinet",
            cabinet_path=str(cabinet_root),
            bundle_path=bundle_path,
        )

    def _transition(self, record: _JobRecord, new_status: JobStatus) -> None:
        allowed = ALLOWED_TRANSITIONS.get(record.status, set())
        if new_status not in allowed:
            raise ValueError(f"Invalid status transition {record.status} -> {new_status}")

        record.status = new_status
        now = datetime.now(UTC)

        if new_status == "running" and record.started_at is None:
            record.started_at = now

        if new_status in TERMINAL_STATUSES:
            record.finished_at = now
            record.progress.current = None
            self._prune_terminal_jobs_locked()

    def _prune_terminal_jobs_locked(self) -> None:
        terminal_records = [record for record in self._jobs.values() if record.status in TERMINAL_STATUSES]
        if len(terminal_records) <= self._max_retained_terminal_jobs:
            return

        terminal_records.sort(
            key=lambda record: (
                record.finished_at or record.created_at,
                record.created_at,
                record.id,
            )
        )
        to_remove = len(terminal_records) - self._max_retained_terminal_jobs
        for record in terminal_records[:to_remove]:
            self._jobs.pop(record.id, None)

    def _to_response(self, record: _JobRecord) -> JobStatusResponse:
        return JobStatusResponse(
            id=record.id,
            status=record.status,
            origin=record.origin,
            output_location=record.output_location.model_copy(deep=True),
            cancel_requested=record.cancel_requested,
            progress=record.progress.model_copy(deep=True),
            summary=record.summary.model_copy(deep=True),
            errors=[item.model_copy(deep=True) for item in record.errors],
            recovery_actions=[dict(item) for item in record.recovery_actions],
            diagnostics=None if record.diagnostics is None else record.diagnostics.model_copy(deep=True),
            report_path=record.report_path,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )

    async def _record_run_failure(self, job_id: str, exc: Exception) -> None:
        async with self._lock:
            record = self._jobs[job_id]
            if isinstance(exc, ExceptionGroup):
                for sub_exc in exc.exceptions:
                    record.errors.append(
                        ErrorItem(
                            path=None,
                            code="job_failure",
                            message=str(sub_exc),
                            stage="backend",
                        )
                    )
                error_count = len(exc.exceptions)
                record.summary.errors += error_count
                record.progress.failed += error_count
            else:
                record.errors.append(
                    ErrorItem(path=None, code="job_failure", message=str(exc), stage="backend")
                )
                record.summary.errors += 1
                record.progress.failed += 1
            if record.status == "running":
                self._transition(record, "failed")

    async def _run_job(self, job_id: str) -> None:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.status != "queued":
                return
            if record.cancel_requested:
                self._transition(record, "cancelled")
                return
            self._transition(record, "running")

        queue: asyncio.Queue[Path | None] = asyncio.Queue()

        async with self._lock:
            record = self._jobs[job_id]
            files = list(record.files)

        for item in files:
            await queue.put(item)
        for _ in range(self._worker_count):
            await queue.put(None)

        async def worker() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    return

                async with self._lock:
                    record = self._jobs[job_id]
                    if record.cancel_requested:
                        return
                    record.progress.current = str(item)
                    request = record.request

                core_options = ConvertOptions(**request.options.model_dump())
                source_handling = "delete" if request.options.delete_eml else "move"

                try:
                    result, diagnostics = await asyncio.to_thread(
                        run_bundle_conversion,
                        item,
                        bundle_root=record.cabinet_root,
                        options=core_options,
                        source_handling=source_handling,
                    )
                except Exception as exc:
                    async with self._lock:
                        record = self._jobs[job_id]
                        record.progress.failed += 1
                        record.summary.errors += 1
                        record.errors.append(
                            ErrorItem(
                                path=str(item),
                                code="backend_exception",
                                message=str(exc),
                                stage="backend",
                            )
                        )
                        if record.request.options.report:
                            from dead_letter.core.report import ReportEntry
                            record.report_entries.append(ReportEntry(
                                source=report_source_for_path(
                                    item,
                                    input_path=record.request.input_path,
                                    mode=record.request.mode,
                                ),
                                output=None,
                                success=False,
                                error={"code": "backend_exception", "message": str(exc), "stage": "backend"},
                            ))
                        if record.request.mode == "file":
                            if record.status == "running":
                                self._transition(record, "failed")
                    continue

                async with self._lock:
                    record = self._jobs[job_id]
                    if record.request.mode == "file":
                        record.diagnostics = None if diagnostics is None else QualityDiagnostics.model_validate(diagnostics)

                    if result.success:
                        record.progress.completed += 1
                        if result.dry_run:
                            record.summary.skipped += 1
                        else:
                            record.summary.written += 1
                        if record.request.mode == "file" and result.bundle is not None:
                            record.output_location.bundle_path = str(result.bundle)
                        if record.request.options.report:
                            from dead_letter.core.report import ReportEntry
                            diag_dict = diagnostics if isinstance(diagnostics, dict) else None
                            record.report_entries.append(ReportEntry(
                                source=report_source_for_path(
                                    item,
                                    input_path=record.request.input_path,
                                    mode=record.request.mode,
                                ),
                                output=(
                                    None
                                    if result.dry_run or result.bundle is None
                                    else str(result.bundle.relative_to(record.cabinet_root))
                                ),
                                success=True,
                                diagnostics=diag_dict,
                            ))
                    else:
                        record.progress.failed += 1
                        record.summary.errors += 1
                        record.errors.append(bundle_result_to_error(result))
                        if record.request.options.report:
                            from dead_letter.core.report import ReportEntry
                            record.report_entries.append(ReportEntry(
                                source=report_source_for_path(
                                    item,
                                    input_path=record.request.input_path,
                                    mode=record.request.mode,
                                ),
                                output=None,
                                success=False,
                                error={"code": result.error_code or "unknown", "message": result.error or "", "stage": "core"},
                            ))
                        if record.request.mode == "file" and result.error_code == "html_markdown_failed":
                            if (
                                result.html_repair_available is True
                                and not record.request.options.allow_html_repair_on_panic
                            ):
                                record.recovery_actions = [dict(_HTML_REPAIR_ACTION)]
                            elif (
                                result.plain_text_fallback_available is True
                                and not record.request.options.allow_fallback_on_html_error
                            ):
                                record.recovery_actions = [dict(_HTML_FALLBACK_ACTION)]
                            else:
                                record.recovery_actions = []
                        else:
                            record.recovery_actions = []
                        if record.request.mode == "file":
                            if record.status == "running":
                                self._transition(record, "failed")

                    if record.cancel_requested:
                        return

        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(self._worker_count):
                    group.create_task(worker())
        except Exception as exc:
            await self._record_run_failure(job_id, exc)
        else:
            async with self._lock:
                record = self._jobs[job_id]
                if record.status in TERMINAL_STATUSES:
                    pass
                elif record.cancel_requested:
                    self._transition(record, "cancelled")
                elif record.request.mode == "file" and record.summary.errors > 0:
                    self._transition(record, "failed")
                elif record.summary.errors > 0:
                    self._transition(record, "completed_with_errors")
                else:
                    self._transition(record, "succeeded")

        # Write report if enabled
        report_data = None
        cabinet_root = None
        report_filename = None
        async with self._lock:
            record = self._jobs[job_id]
            if record.request.options.report:
                try:
                    from dead_letter.core.report import build_report

                    core_opts = ConvertOptions(**record.request.options.model_dump())
                    started = record.started_at or record.created_at
                    duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
                    report_data = build_report(
                        entries=record.report_entries,
                        options=core_opts,
                        job_id=record.id,
                        job_status=record.status,
                        duration_ms=duration_ms,
                        input_path=record.request.input_path,
                        input_mode=record.request.mode,
                        total=record.progress.total,
                    )
                    cabinet_root = Path(record.cabinet_root)
                    report_filename = _report_filename_for_job(record.id)
                except Exception:
                    logger.warning("Report build failed for job %s", job_id, exc_info=True)

        if report_data is not None and cabinet_root is not None and report_filename is not None:
            try:
                from dead_letter.core.report import write_report

                report_file = await asyncio.to_thread(
                    write_report,
                    report_data,
                    cabinet_root,
                    filename=report_filename,
                )
                async with self._lock:
                    self._jobs[job_id].report_path = str(report_file)
            except Exception:
                logger.warning("Report write failed for job %s", job_id, exc_info=True)

        async with self._lock:
            record = self._jobs.get(job_id)
            input_path = None if record is None else Path(record.request.input_path)
        if input_path is not None and input_path.name.startswith("_batch-") and input_path.is_dir():
            try:
                input_path.rmdir()
            except OSError:
                pass
