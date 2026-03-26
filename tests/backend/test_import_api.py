from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import dead_letter.backend.api as api_mod
from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import JobCreateResponse, JobCreateRequest, OutputLocation
from dead_letter.backend.settings import WorkflowSettings


class _StubJobManager:
    def __init__(self) -> None:
        self.requests: list[JobCreateRequest] = []

    async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> JobCreateResponse:
        self.requests.append(request)
        return JobCreateResponse(
            id="job-123",
            status="queued",
            output_location=OutputLocation(
                strategy="cabinet",
                cabinet_path="/tmp/Cabinet",
                bundle_path="/tmp/Cabinet/uploaded",
            ),
        )


class _StubWatchManager:
    def __init__(self, watch_path: str | None = None) -> None:
        self._watch_path = watch_path
        self.suppressed_paths: list[str] = []

    async def start(self, path: str, _options: dict[str, object], _job_manager: object) -> None:
        self._watch_path = path

    async def stop(self) -> None:
        self._watch_path = None

    def status(self):
        return type(
            "_Status",
            (),
            {
                "active": self._watch_path is not None,
                "path": self._watch_path,
                "files_detected": 0,
                "jobs_created": 0,
                "failed_events": 0,
                "last_error": None,
                "latest_job_id": None,
            },
        )()

    def suppress_path(self, path: str | Path) -> None:
        self.suppressed_paths.append(str(Path(path).resolve()))


class _ChunkedUploadFile:
    def __init__(self, chunks: list[bytes], *, fail_after_reads: int | None = None) -> None:
        self._chunks = list(chunks)
        self._fail_after_reads = fail_after_reads
        self.read_sizes: list[int] = []
        self.read_count = 0

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            raise AssertionError("upload staging must read with a bounded chunk size")
        if self._fail_after_reads is not None and self.read_count >= self._fail_after_reads:
            raise OSError("stream interrupted")
        self.read_count += 1
        if self._chunks:
            return self._chunks.pop(0)
        return b""


@pytest.mark.anyio
async def test_write_import_file_raises_after_collision_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_mod, "_MAX_IMPORT_COLLISION_INDEX", 3)
    inbox = tmp_path / "Inbox"
    settings = WorkflowSettings(inbox_path=inbox, cabinet_path=tmp_path / "Cabinet")
    inbox.mkdir(parents=True)
    for i in range(1, 5):
        suffix = f"-{i}" if i > 1 else ""
        (inbox / f"hello{suffix}.eml").write_bytes(b"taken")

    with pytest.raises(RuntimeError, match="could not find unique filename"):
        await api_mod._write_import_file(settings, "hello.eml", _ChunkedUploadFile([b"content", b""]))


@pytest.mark.anyio
async def test_write_import_file_retries_after_exclusive_create_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = tmp_path / "Inbox"
    settings = WorkflowSettings(inbox_path=inbox, cabinet_path=tmp_path / "Cabinet")
    attempts: list[Path] = []
    real_open = Path.open

    def fake_open(self: Path, mode: str = "r", *args: object, **kwargs: object):
        if self == inbox / "hello.eml" and mode == "xb" and not attempts:
            attempts.append(self)
            raise FileExistsError("simulated race")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)

    imported_path = await api_mod._write_import_file(
        settings,
        "hello.eml",
        _ChunkedUploadFile([b"From: a@b\n\nHello\n", b""]),
    )

    assert imported_path == (inbox / "hello-2.eml").resolve()
    assert imported_path.read_bytes() == b"From: a@b\n\nHello\n"


@pytest.mark.anyio
async def test_write_import_file_streams_upload_in_chunks(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    settings = WorkflowSettings(inbox_path=inbox, cabinet_path=tmp_path / "Cabinet")
    upload = _ChunkedUploadFile([b"From: a@b\n", b"\nHello\n", b""])

    imported_path = await api_mod._write_import_file(settings, "hello.eml", upload)

    assert imported_path == (inbox / "hello.eml").resolve()
    assert imported_path.read_bytes() == b"From: a@b\n\nHello\n"
    assert upload.read_sizes == [api_mod._IMPORT_STREAM_CHUNK_SIZE] * 3


@pytest.mark.anyio
async def test_write_batch_import_file_removes_partial_file_when_stream_fails(tmp_path: Path) -> None:
    batch_dir = tmp_path / "_batch-test"
    upload = _ChunkedUploadFile([b"partial"], fail_after_reads=1)

    with pytest.raises(OSError, match="stream interrupted"):
        await api_mod._write_batch_import_file(batch_dir, "hello.eml", upload)

    assert list(batch_dir.iterdir()) == []


@pytest.mark.anyio
async def test_import_requires_configured_settings(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={"file": ("hello.eml", b"From: a@b\nSubject: hi\n\nHello\n", "message/rfc822")},
        )

    assert response.status_code == 409
    payload = response.json()
    assert "errors" in payload
    assert payload["errors"][0]["path"] == "settings"


@pytest.mark.anyio
async def test_import_copies_into_inbox_and_starts_job(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=_StubJobManager(),
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={"file": ("hello.eml", b"From: a@b\nSubject: hi\n\nHello\n", "message/rfc822")},
        )

    assert response.status_code == 202
    payload = response.json()
    imported_path = Path(payload["imported_path"])
    assert imported_path.exists()
    assert imported_path.parent == inbox.resolve()
    assert payload["id"] == "job-123"
    assert payload["output_location"]["strategy"] == "cabinet"

    request = app.state.job_manager.requests[0]
    assert request.mode == "file"
    assert request.input_path == str(imported_path)
    assert request.options.allow_fallback_on_html_error is False


@pytest.mark.anyio
async def test_import_uses_suffixed_filename_without_overwriting_existing_file(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    existing = inbox / "hello.eml"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"original")
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=_StubJobManager(),
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={"file": ("hello.eml", b"new message", "message/rfc822")},
        )

    assert response.status_code == 202
    imported_path = Path(response.json()["imported_path"])
    assert imported_path == (inbox / "hello-2.eml").resolve()
    assert existing.read_bytes() == b"original"
    assert imported_path.read_bytes() == b"new message"


@pytest.mark.anyio
async def test_import_passes_job_options_from_multipart_options_field(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=_StubJobManager(),
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={
                "file": ("hello.eml", b"From: a@b\nSubject: hi\n\nHello\n", "message/rfc822"),
                "options": (None, '{"allow_fallback_on_html_error": true, "dry_run": true}'),
            },
        )

    assert response.status_code == 202
    request = app.state.job_manager.requests[0]
    assert request.options.allow_fallback_on_html_error is True
    assert request.options.dry_run is True


@pytest.mark.anyio
async def test_import_rejects_invalid_options_payload(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=_StubJobManager(),
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={
                "file": ("hello.eml", b"From: a@b\nSubject: hi\n\nHello\n", "message/rfc822"),
                "options": (None, '{"allow_fallback_on_html_error": {"invalid": true}}'),
            },
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["errors"][0]["path"] == "options"


@pytest.mark.anyio
async def test_import_rejects_non_eml_with_error_envelope(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )
    app.state.settings.save(inbox_path=tmp_path / "Inbox", cabinet_path=tmp_path / "Cabinet")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={"file": ("bad.txt", b"not email", "text/plain")},
        )

    assert response.status_code == 400
    payload = response.json()
    assert "errors" in payload
    assert payload["errors"][0]["path"] == "bad.txt"


@pytest.mark.anyio
async def test_import_returns_500_when_collision_cap_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_mod, "_MAX_IMPORT_COLLISION_INDEX", 1)
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    inbox.mkdir(parents=True)
    (inbox / "hello.eml").write_bytes(b"taken")
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=_StubJobManager(),
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={"file": ("hello.eml", b"new content", "message/rfc822")},
        )

    assert response.status_code == 500
    payload = response.json()
    assert payload["errors"][0]["code"] == "backend_error"
    assert "unique filename" in payload["errors"][0]["message"]


@pytest.mark.anyio
async def test_import_suppresses_active_inbox_watch_for_imported_path(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    watcher = _StubWatchManager(str(inbox.resolve()))
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=_StubJobManager(),
        watch_manager=watcher,
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={"file": ("hello.eml", b"From: a@b\nSubject: hi\n\nHello\n", "message/rfc822")},
        )

    assert response.status_code == 202
    imported_path = Path(response.json()["imported_path"]).resolve()
    assert watcher.suppressed_paths == [str(imported_path)]


@pytest.mark.anyio
async def test_import_rolls_back_copied_file_when_job_creation_fails(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async def boom(_request: JobCreateRequest, *, origin: str = "manual") -> JobCreateResponse:
        _ = origin
        raise RuntimeError("boom creating job")

    app.state.job_manager.create_job = boom

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={"file": ("hello.eml", b"From: a@b\nSubject: hi\n\nHello\n", "message/rfc822")},
        )

    assert response.status_code == 500
    assert sorted(path.name for path in inbox.iterdir()) == []


@pytest.mark.anyio
async def test_import_does_not_suppress_watch_path_when_job_creation_fails(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    watcher = _StubWatchManager(str(inbox.resolve()))
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        watch_manager=watcher,
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async def boom(_request: JobCreateRequest, *, origin: str = "manual") -> JobCreateResponse:
        _ = origin
        raise RuntimeError("boom creating job")

    app.state.job_manager.create_job = boom

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            files={"file": ("hello.eml", b"From: a@b\nSubject: hi\n\nHello\n", "message/rfc822")},
        )

    assert response.status_code == 500
    assert watcher.suppressed_paths == []
