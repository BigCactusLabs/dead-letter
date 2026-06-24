from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from watchfiles import Change

import dead_letter.backend.jobs as jobs_mod
from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import ErrorItem, JobCreateRequest
from dead_letter.backend.jobs import JobManager
from dead_letter.backend.watch import WatchManager
from dead_letter.core.types import BundleResult

from .helpers import csrf_headers


@dataclass
class _WatchStatus:
    active: bool = False
    path: str | None = None
    files_detected: int = 0
    jobs_created: int = 0
    failed_events: int = 0
    last_error: ErrorItem | None = None
    latest_job_id: str | None = None


class _StubWatchManager:
    def __init__(self) -> None:
        self.started_paths: list[str] = []
        self._status = _WatchStatus()

    async def start(self, path: str, _options: dict[str, object], _job_manager: object) -> None:
        self.started_paths.append(path)
        self._status = _WatchStatus(active=True, path=path)

    async def stop(self) -> None:
        self._status = _WatchStatus()

    def status(self) -> _WatchStatus:
        return self._status


@pytest.mark.anyio
async def test_watch_requires_configured_settings_when_using_default_inbox(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        watch_manager=_StubWatchManager(),
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/watch",
            headers=await csrf_headers(client),
            json={"path": "", "options": {}},
        )

    assert response.status_code == 409
    payload = response.json()
    assert "errors" in payload
    assert payload["errors"][0]["path"] == "settings"


@pytest.mark.anyio
async def test_watch_requires_configured_settings_for_explicit_override(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        watch_manager=_StubWatchManager(),
        worker_count=1,
    )
    (tmp_path / "mail").mkdir()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/watch",
            headers=await csrf_headers(client),
            json={"path": str(tmp_path / "mail"), "options": {}},
        )

    assert response.status_code == 409
    payload = response.json()
    assert "errors" in payload
    assert payload["errors"][0]["path"] == "settings"


@pytest.mark.anyio
async def test_watch_uses_saved_inbox_as_default_target(tmp_path: Path) -> None:
    watcher = _StubWatchManager()
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        watch_manager=watcher,
        worker_count=1,
    )
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/watch",
            headers=await csrf_headers(client),
            json={"path": "", "options": {}},
        )

    assert response.status_code == 200
    assert watcher.started_paths == [str(inbox.resolve())]
    assert response.json()["path"] == str(inbox.resolve())
    assert response.json()["latest_job_id"] is None


@pytest.mark.anyio
async def test_watch_rejects_override_targets_inside_cabinet(tmp_path: Path) -> None:
    watcher = _StubWatchManager()
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        watch_manager=watcher,
        worker_count=1,
    )
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    override = cabinet / "nested"
    override.mkdir(parents=True)
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/api/watch",
            headers=await csrf_headers(client),
            json={"path": str(override), "options": {}},
        )

    assert response.status_code == 400
    payload = response.json()
    assert "errors" in payload
    assert payload["errors"][0]["message"] == "watch target cannot be inside Cabinet"
    assert watcher.started_paths == []


@pytest.mark.anyio
async def test_watch_api_surfaces_startup_backlog_jobs(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    existing = inbox / "existing.eml"
    inbox.mkdir()
    cabinet.mkdir()
    existing.write_text("From: a@b\nSubject: existing\n\nHello\n", encoding="utf-8")

    class JobManagerStub:
        def __init__(self) -> None:
            self.created: list[JobCreateRequest] = []
            self._snapshots: dict[str, object] = {}
            self.processed = asyncio.Event()

        async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> object:
            self.created.append(request)
            snapshot = type(
                "_Job",
                (),
                {
                    "id": f"watch-job-{len(self.created)}",
                    "status": "queued",
                },
            )()
            self._snapshots[snapshot.id] = snapshot
            self.processed.set()
            return snapshot

        async def get_job(self, job_id: str) -> object | None:
            return self._snapshots.get(job_id)

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        await stop_event.wait()
        if False:  # pragma: no cover - async generator marker
            yield set()

    async def fake_wait_for_stable_file(self, path: Path) -> Path:
        return path

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)
    monkeypatch.setattr(WatchManager, "_wait_for_stable_file", fake_wait_for_stable_file)

    browser = FilesystemBrowser(root=tmp_path)
    job_manager = JobManagerStub()
    app = create_app(
        browser=browser,
        manager=job_manager,  # type: ignore[arg-type]
        settings_path=tmp_path / "settings.json",
        watch_manager=WatchManager(browser=browser, dedupe_window_seconds=60.0),
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        start = await client.post(
            "/api/watch",
            headers=await csrf_headers(client),
            json={"path": "", "options": {}},
        )
        await asyncio.wait_for(job_manager.processed.wait(), timeout=1.0)
        status = await client.get("/api/watch")

    assert start.status_code == 200
    assert status.status_code == 200
    payload = status.json()
    assert payload["active"] is True
    assert payload["path"] == str(inbox.resolve())
    assert payload["files_detected"] == 1
    assert payload["jobs_created"] == 1
    assert payload["latest_job_id"] == "watch-job-1"
    assert payload["latest_job_status"] == "queued"
    assert job_manager.created[0].input_path == str(existing.resolve())


@pytest.mark.anyio
async def test_watch_created_jobs_refresh_saved_roots_before_creating_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_inbox = tmp_path / "OldInbox"
    old_cabinet = tmp_path / "OldCabinet"
    new_inbox = tmp_path / "NewInbox"
    new_cabinet = tmp_path / "NewCabinet"
    old_inbox.mkdir()
    old_cabinet.mkdir()
    source = old_inbox / "watched.eml"
    emit_event = asyncio.Event()
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

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        await emit_event.wait()
        yield {(Change.added, str(source))}
        await stop_event.wait()

    async def fake_wait_for_stable_file(self, path: Path) -> Path:
        return path

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", fake_convert)
    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)
    monkeypatch.setattr(WatchManager, "_wait_for_stable_file", fake_wait_for_stable_file)

    browser = FilesystemBrowser(root=tmp_path)
    manager = JobManager(worker_count=1, inbox_root=old_inbox, cabinet_root=old_cabinet)
    app = create_app(
        browser=browser,
        manager=manager,
        settings_path=tmp_path / "settings.json",
        watch_manager=WatchManager(browser=browser, dedupe_window_seconds=60.0),
        worker_count=1,
    )
    app.state.settings.save(inbox_path=old_inbox, cabinet_path=old_cabinet)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        headers = await csrf_headers(client)
        start = await client.post("/api/watch", headers=headers, json={"path": "", "options": {}})
        app.state.settings.save(inbox_path=new_inbox, cabinet_path=new_cabinet)
        source.write_text("From: a@b\nSubject: watched\n\nHello\n", encoding="utf-8")
        emit_event.set()

        latest_job_id = None
        for _ in range(100):
            status = await client.get("/api/watch")
            latest_job_id = status.json()["latest_job_id"]
            if latest_job_id is not None:
                break
            await asyncio.sleep(0.01)

        assert latest_job_id is not None
        terminal = await manager.wait_for_terminal(latest_job_id, timeout=5.0)
        await client.delete("/api/watch", headers=headers)

    assert start.status_code == 200
    assert start.json()["path"] == str(old_inbox.resolve())
    assert terminal.output_location.cabinet_path == str(new_cabinet.resolve())
    assert bundle_roots == [str(new_cabinet.resolve())]
    assert not (old_cabinet / "watched").exists()
