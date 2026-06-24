from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import dead_letter.backend.api as api_mod
import dead_letter.backend.jobs as jobs_mod
from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.jobs import JobManager
from dead_letter.backend.schemas import JobCreateRequest, JobCreateResponse, OutputLocation
from dead_letter.core.types import BundleResult

from .helpers import csrf_headers


class _StubJobManager:
    def __init__(self) -> None:
        self.requests: list[JobCreateRequest] = []

    async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> JobCreateResponse:
        _ = origin
        self.requests.append(request)
        return JobCreateResponse(
            id="batch-job-1",
            status="queued",
            output_location=OutputLocation(
                strategy="cabinet",
                cabinet_path="/tmp/Cabinet",
                bundle_path=None,
            ),
        )


def _make_app(tmp_path: Path, manager: _StubJobManager | None = None):
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=manager or _StubJobManager(),
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)
    return app


@pytest.mark.anyio
async def test_import_batch_creates_batch_dir_and_directory_mode_job(tmp_path: Path) -> None:
    manager = _StubJobManager()
    app = _make_app(tmp_path, manager)
    inbox = tmp_path / "Inbox"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[
                ("files", ("a.eml", b"From: a@b\n\nOne\n", "message/rfc822")),
                ("files", ("b.eml", b"From: c@d\n\nTwo\n", "message/rfc822")),
            ],
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["id"] == "batch-job-1"
    assert len(payload["imported_paths"]) == 2

    batch_dirs = [path for path in inbox.iterdir() if path.is_dir() and path.name.startswith("_batch-")]
    assert len(batch_dirs) == 1
    batch_dir = batch_dirs[0]
    assert re.fullmatch(r"_batch-[0-9a-f-]+", batch_dir.name)

    written = sorted(path.name for path in batch_dir.iterdir())
    assert written == ["a.eml", "b.eml"]

    request = manager.requests[0]
    assert request.mode == "directory"
    assert request.input_path == str(batch_dir.resolve())


@pytest.mark.anyio
async def test_import_batch_refreshes_saved_roots_before_creating_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_inbox = tmp_path / "OldInbox"
    old_cabinet = tmp_path / "OldCabinet"
    new_inbox = tmp_path / "NewInbox"
    new_cabinet = tmp_path / "NewCabinet"
    manager = JobManager(worker_count=1, inbox_root=old_inbox, cabinet_root=old_cabinet)
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=manager,
        worker_count=1,
    )
    app.state.settings.save(inbox_path=old_inbox, cabinet_path=old_cabinet)
    app.state.settings.save(inbox_path=new_inbox, cabinet_path=new_cabinet)
    bundle_roots: list[str] = []

    def fake_convert(path: str | Path, *, bundle_root: str | Path, options, source_handling):
        _ = (options, source_handling)
        src = Path(path)
        root = Path(bundle_root).resolve()
        bundle_roots.append(str(root))
        bundle = root / src.stem
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        source_artifact = bundle / src.name
        source_artifact.write_text("x", encoding="utf-8")
        src.unlink(missing_ok=True)
        return BundleResult(
            source=src,
            bundle=bundle,
            markdown=markdown,
            source_artifact=source_artifact,
            attachments=[],
            success=True,
            error=None,
            dry_run=False,
        ), None

    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", fake_convert)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[
                ("files", ("a.eml", b"From: a@b\n\nOne\n", "message/rfc822")),
                ("files", ("b.eml", b"From: c@d\n\nTwo\n", "message/rfc822")),
            ],
        )
        terminal = await manager.wait_for_terminal(response.json()["id"], timeout=5.0)

    assert response.status_code == 202
    imported_paths = [Path(path) for path in response.json()["imported_paths"]]
    assert {path.parent.parent for path in imported_paths} == {new_inbox.resolve()}
    assert response.json()["output_location"]["cabinet_path"] == str(new_cabinet.resolve())
    assert terminal.output_location.cabinet_path == str(new_cabinet.resolve())
    assert bundle_roots == [str(new_cabinet.resolve()), str(new_cabinet.resolve())]
    assert sorted(old_cabinet.iterdir()) == []


@pytest.mark.anyio
async def test_import_batch_rejects_empty_file_list(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[],
        )

    assert response.status_code in (400, 422)


@pytest.mark.anyio
async def test_import_batch_rejects_non_eml_files(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[
                ("files", ("good.eml", b"From: a@b\n\n", "message/rfc822")),
                ("files", ("bad.txt", b"not email", "text/plain")),
            ],
        )

    assert response.status_code == 422
    payload = response.json()
    assert "errors" in payload
    assert "bad.txt" in str(payload["errors"])


@pytest.mark.anyio
async def test_import_batch_requires_configured_settings(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[("files", ("a.eml", b"From: a@b\n\n", "message/rfc822"))],
        )

    assert response.status_code == 409


@pytest.mark.anyio
async def test_import_batch_passes_options(tmp_path: Path) -> None:
    manager = _StubJobManager()
    app = _make_app(tmp_path, manager)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[("files", ("a.eml", b"From: a@b\n\n", "message/rfc822"))],
            data={"options": '{"dry_run": true}'},
        )

    assert response.status_code == 202
    request = manager.requests[0]
    assert request.options.dry_run is True


@pytest.mark.anyio
async def test_import_batch_handles_filename_collisions(tmp_path: Path) -> None:
    manager = _StubJobManager()
    app = _make_app(tmp_path, manager)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[
                ("files", ("same.eml", b"From: a@b\n\nOne\n", "message/rfc822")),
                ("files", ("same.eml", b"From: c@d\n\nTwo\n", "message/rfc822")),
            ],
        )

    assert response.status_code == 202
    paths = response.json()["imported_paths"]
    assert len(paths) == 2
    assert paths[0] != paths[1]


@pytest.mark.anyio
async def test_import_batch_rolls_back_reserved_batch_dir_when_job_creation_fails(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    inbox = tmp_path / "Inbox"

    async def boom(_request: JobCreateRequest, *, origin: str = "manual") -> JobCreateResponse:
        _ = origin
        raise RuntimeError("boom creating job")

    app.state.job_manager.create_job = boom

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[
                ("files", ("a.eml", b"From: a@b\n\nOne\n", "message/rfc822")),
                ("files", ("b.eml", b"From: c@d\n\nTwo\n", "message/rfc822")),
            ],
        )

    assert response.status_code == 500
    assert sorted(inbox.iterdir()) == []


@pytest.mark.anyio
async def test_import_batch_rejects_oversized_file_with_413(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_mod, "_MAX_IMPORT_FILE_BYTES", 5)
    manager = _StubJobManager()
    app = _make_app(tmp_path, manager)
    inbox = tmp_path / "Inbox"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[("files", ("big.eml", b"123456", "message/rfc822"))],
        )

    assert response.status_code == 413
    payload = response.json()
    assert payload["errors"][0]["code"] == "invalid_request"
    assert "100 MB limit" in payload["errors"][0]["message"]
    assert manager.requests == []
    assert sorted(inbox.iterdir()) == []


@pytest.mark.anyio
async def test_import_batch_rejects_too_many_files_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_mod, "_MAX_IMPORT_BATCH_FILES", 2)
    manager = _StubJobManager()
    app = _make_app(tmp_path, manager)
    inbox = tmp_path / "Inbox"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[
                ("files", ("a.eml", b"From: a@b\n\nOne\n", "message/rfc822")),
                ("files", ("b.eml", b"From: c@d\n\nTwo\n", "message/rfc822")),
                ("files", ("c.eml", b"From: e@f\n\nThree\n", "message/rfc822")),
            ],
        )

    assert response.status_code == 413
    payload = response.json()
    assert payload["errors"][0]["code"] == "invalid_request"
    assert "at most 2 files" in payload["errors"][0]["message"]
    assert manager.requests == []
    assert sorted(inbox.iterdir()) == []


@pytest.mark.anyio
async def test_import_batch_rejects_aggregate_size_with_413_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_mod, "_MAX_IMPORT_BATCH_BYTES", 10)
    manager = _StubJobManager()
    app = _make_app(tmp_path, manager)
    inbox = tmp_path / "Inbox"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/import-batch",
            headers=await csrf_headers(client),
            files=[
                ("files", ("a.eml", b"12345", "message/rfc822")),
                ("files", ("b.eml", b"678901", "message/rfc822")),
            ],
        )

    assert response.status_code == 413
    payload = response.json()
    assert payload["errors"][0]["code"] == "invalid_request"
    assert "batch upload exceeds 100 MB limit" in payload["errors"][0]["message"]
    assert manager.requests == []
    assert sorted(inbox.iterdir()) == []
