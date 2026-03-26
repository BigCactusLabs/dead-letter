from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from watchfiles import Change

from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import ErrorItem, JobCreateRequest
from dead_letter.backend.watch import WatchManager


@pytest.fixture
def browser(tmp_path: Path) -> FilesystemBrowser:
    (tmp_path / "mail").mkdir()
    return FilesystemBrowser(root=tmp_path)


class _NoopJobManager:
    async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> object:
        _ = request
        return object()


@pytest.mark.anyio
async def test_watch_manager_start_and_stop_uses_absolute_status_path(
    browser: FilesystemBrowser, monkeypatch
) -> None:
    stop_started = asyncio.Event()

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        stop_started.set()
        await stop_event.wait()
        if False:  # pragma: no cover - async generator marker
            yield set()

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)

    manager = WatchManager(browser=browser)
    await manager.start("mail", {}, _NoopJobManager())
    await asyncio.wait_for(stop_started.wait(), timeout=1.0)

    status = manager.status()
    assert status.active is True
    assert status.path == str((browser.root / "mail").resolve())

    await manager.stop()
    assert manager.status().active is False


@pytest.mark.anyio
async def test_watch_manager_dedupes_duplicate_events_for_same_file(
    browser: FilesystemBrowser, monkeypatch
) -> None:
    source = browser.root / "mail" / "new.eml"
    source.write_text("From: a@b\nSubject: hi\n\nHello\n", encoding="utf-8")
    created: list[object] = []
    processed = asyncio.Event()

    class JobManagerStub:
        async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> object:
            created.append(request)
            processed.set()
            return object()

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        yield {
            (Change.added, str(source)),
            (Change.modified, str(source)),
            (Change.added, str(source)),
        }
        await stop_event.wait()

    async def fake_wait_for_stable_file(self, path: Path) -> Path:
        return path

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)
    monkeypatch.setattr(WatchManager, "_wait_for_stable_file", fake_wait_for_stable_file)

    manager = WatchManager(browser=browser, dedupe_window_seconds=60.0)
    await manager.start("mail", {}, JobManagerStub())
    await asyncio.wait_for(processed.wait(), timeout=1.0)
    await manager.stop()

    assert len(created) == 1
    assert created[0].mode == "file"
    assert created[0].input_path == str(source.resolve())
    assert manager.status().files_detected == 1
    assert manager.status().jobs_created == 1


@pytest.mark.anyio
async def test_watch_manager_rejects_paths_outside_root(browser: FilesystemBrowser) -> None:
    manager = WatchManager(browser=browser)

    with pytest.raises(PermissionError):
        await manager.start("../elsewhere", {}, _NoopJobManager())


@pytest.mark.anyio
async def test_watch_manager_resolves_symlink_target_path(browser: FilesystemBrowser, monkeypatch) -> None:
    target_dir = browser.root / "mail" / "nested"
    target_dir.mkdir()
    symlink_dir = browser.root / "mail" / "alias"
    symlink_dir.symlink_to(target_dir, target_is_directory=True)
    stop_started = asyncio.Event()

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        stop_started.set()
        await stop_event.wait()
        if False:  # pragma: no cover - async generator marker
            yield set()

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)

    manager = WatchManager(browser=browser)
    await manager.start("mail/alias", {}, _NoopJobManager())
    await asyncio.wait_for(stop_started.wait(), timeout=1.0)

    assert manager.status().path == str(target_dir.resolve())

    await manager.stop()


@pytest.mark.anyio
async def test_watch_manager_records_failures_and_keeps_watching(
    browser: FilesystemBrowser, monkeypatch
) -> None:
    failed = browser.root / "mail" / "failed.eml"
    succeeded = browser.root / "mail" / "succeeded.eml"
    failed.write_text("From: a@b\nSubject: failed\n\nHello\n", encoding="utf-8")
    succeeded.write_text("From: a@b\nSubject: ok\n\nHello\n", encoding="utf-8")
    created: list[object] = []
    processed = asyncio.Event()

    class JobManagerStub:
        async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> object:
            if Path(request.input_path).name == failed.name:
                raise RuntimeError("boom")
            created.append(request)
            processed.set()
            return object()

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        yield {(Change.added, str(failed))}
        yield {(Change.added, str(succeeded))}
        await stop_event.wait()

    async def fake_wait_for_stable_file(self, path: Path) -> Path:
        return path

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)
    monkeypatch.setattr(WatchManager, "_wait_for_stable_file", fake_wait_for_stable_file)

    manager = WatchManager(browser=browser, dedupe_window_seconds=60.0)
    await manager.start("mail", {}, JobManagerStub())
    await asyncio.wait_for(processed.wait(), timeout=1.0)

    status = manager.status()
    assert status.active is True
    assert status.files_detected == 2
    assert status.jobs_created == 1
    assert status.failed_events == 1
    assert status.last_error == ErrorItem(
        path=str(failed.resolve()),
        code="watch_processing_error",
        message="boom",
        stage="backend",
    )

    await manager.stop()


@pytest.mark.anyio
async def test_watch_manager_skips_files_that_resolve_outside_active_watch_root(
    browser: FilesystemBrowser, monkeypatch
) -> None:
    inside_link = browser.root / "mail" / "escape.eml"
    outside_target = browser.root / "outside.eml"
    outside_target.write_text("From: a@b\nSubject: outside\n\nHello\n", encoding="utf-8")
    inside_link.symlink_to(outside_target)
    stop_started = asyncio.Event()
    created: list[JobCreateRequest] = []

    class JobManagerStub:
        async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> object:
            _ = origin
            created.append(request)
            return object()

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        stop_started.set()
        await stop_event.wait()
        if False:  # pragma: no cover - async generator marker
            yield set()

    async def fake_wait_for_stable_file(self, path: Path) -> Path:
        return path.resolve()

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)
    monkeypatch.setattr(WatchManager, "_wait_for_stable_file", fake_wait_for_stable_file)

    manager = WatchManager(browser=browser)
    await manager.start("mail", {}, _NoopJobManager())
    await asyncio.wait_for(stop_started.wait(), timeout=1.0)

    await manager._process_candidate(inside_link.resolve(), {}, JobManagerStub())

    assert created == []
    assert manager.status().files_detected == 0
    assert manager.status().jobs_created == 0

    await manager.stop()


@pytest.mark.anyio
async def test_watch_manager_stop_clears_completed_task_state(
    browser: FilesystemBrowser, monkeypatch
) -> None:
    started = asyncio.Event()

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        started.set()
        if False:  # pragma: no cover - async generator marker
            yield set()

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)

    manager = WatchManager(browser=browser)
    await manager.start("mail", {}, _NoopJobManager())
    await asyncio.wait_for(started.wait(), timeout=1.0)

    for _ in range(100):
        if not manager.status().active:
            break
        await asyncio.sleep(0.01)

    assert manager.status().active is False

    await manager.stop()

    assert manager._task is None
    assert manager._stop_event is None


@pytest.mark.anyio
async def test_watch_manager_tracks_latest_created_job_id(
    browser: FilesystemBrowser, monkeypatch
) -> None:
    source = browser.root / "mail" / "new.eml"
    source.write_text("From: a@b\nSubject: hi\n\nHello\n", encoding="utf-8")
    processed = asyncio.Event()

    class JobManagerStub:
        async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> object:
            _ = request
            processed.set()
            return type("_Job", (), {"id": "job-123"})()

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        yield {(Change.added, str(source))}
        await stop_event.wait()

    async def fake_wait_for_stable_file(self, path: Path) -> Path:
        return path

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)
    monkeypatch.setattr(WatchManager, "_wait_for_stable_file", fake_wait_for_stable_file)

    manager = WatchManager(browser=browser)
    await manager.start("mail", {}, JobManagerStub())
    await asyncio.wait_for(processed.wait(), timeout=1.0)
    await manager.stop()

    assert manager.status().latest_job_id == "job-123"


@pytest.mark.anyio
async def test_watch_manager_processes_existing_eml_files_on_start(
    browser: FilesystemBrowser, monkeypatch
) -> None:
    existing = browser.root / "mail" / "existing.eml"
    nested = browser.root / "mail" / "nested" / "older.eml"
    ignored = browser.root / "mail" / "notes.txt"
    existing.write_text("From: a@b\nSubject: existing\n\nHello\n", encoding="utf-8")
    nested.parent.mkdir()
    nested.write_text("From: a@b\nSubject: older\n\nHello\n", encoding="utf-8")
    ignored.write_text("notes", encoding="utf-8")
    created: list[JobCreateRequest] = []
    processed = asyncio.Event()

    class JobManagerStub:
        async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> object:
            created.append(request)
            if len(created) == 2:
                processed.set()
            return object()

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        await stop_event.wait()
        if False:  # pragma: no cover - async generator marker
            yield set()

    async def fake_wait_for_stable_file(self, path: Path) -> Path:
        return path

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)
    monkeypatch.setattr(WatchManager, "_wait_for_stable_file", fake_wait_for_stable_file)

    manager = WatchManager(browser=browser, dedupe_window_seconds=60.0)
    await manager.start("mail", {}, JobManagerStub())
    await asyncio.wait_for(processed.wait(), timeout=1.0)
    await manager.stop()

    assert [request.input_path for request in created] == [
        str(existing.resolve()),
        str(nested.resolve()),
    ]
    assert manager.status().files_detected == 2
    assert manager.status().jobs_created == 2


@pytest.mark.anyio
async def test_watch_manager_freezes_startup_snapshot_at_start_time(
    browser: FilesystemBrowser, monkeypatch
) -> None:
    late = browser.root / "mail" / "late.eml"
    created: list[JobCreateRequest] = []
    processed = asyncio.Event()
    emit_event = asyncio.Event()

    class JobManagerStub:
        async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> object:
            created.append(request)
            processed.set()
            return object()

    async def fake_awatch(_directory: Path, *, stop_event: asyncio.Event, **_kwargs):
        await emit_event.wait()
        yield {(Change.added, str(late))}
        await stop_event.wait()

    async def fake_wait_for_stable_file(self, path: Path) -> Path:
        return path

    import dead_letter.backend.watch as watch_mod

    monkeypatch.setattr(watch_mod, "awatch", fake_awatch)
    monkeypatch.setattr(WatchManager, "_wait_for_stable_file", fake_wait_for_stable_file)

    manager = WatchManager(browser=browser, dedupe_window_seconds=60.0)
    await manager.start("mail", {}, JobManagerStub())
    late.write_text("From: a@b\nSubject: late\n\nHello\n", encoding="utf-8")
    await asyncio.sleep(0.05)

    assert created == []

    emit_event.set()
    await asyncio.wait_for(processed.wait(), timeout=1.0)
    await manager.stop()

    assert len(created) == 1
    assert created[0].input_path == str(late.resolve())
