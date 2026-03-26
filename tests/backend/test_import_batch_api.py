from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import JobCreateRequest, JobCreateResponse, OutputLocation


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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import-batch",
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
async def test_import_batch_rejects_empty_file_list(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/import-batch", files=[])

    assert response.status_code in (400, 422)


@pytest.mark.anyio
async def test_import_batch_rejects_non_eml_files(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import-batch",
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import-batch",
            files=[("files", ("a.eml", b"From: a@b\n\n", "message/rfc822"))],
        )

    assert response.status_code == 409


@pytest.mark.anyio
async def test_import_batch_passes_options(tmp_path: Path) -> None:
    manager = _StubJobManager()
    app = _make_app(tmp_path, manager)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import-batch",
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import-batch",
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import-batch",
            files=[
                ("files", ("a.eml", b"From: a@b\n\nOne\n", "message/rfc822")),
                ("files", ("b.eml", b"From: c@d\n\nTwo\n", "message/rfc822")),
            ],
        )

    assert response.status_code == 500
    assert sorted(inbox.iterdir()) == []
