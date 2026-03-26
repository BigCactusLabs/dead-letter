from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import ErrorItem


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    (tmp_path / "mail").mkdir()
    (tmp_path / "mail" / "hello.eml").write_text("hello", encoding="utf-8")
    (tmp_path / "mail" / "notes.txt").write_text("notes", encoding="utf-8")
    (tmp_path / "mail" / "nested").mkdir()
    return tmp_path


@pytest.fixture
def browser(sample_tree: Path) -> FilesystemBrowser:
    return FilesystemBrowser(root=sample_tree)


@pytest.fixture
def app(browser: FilesystemBrowser):
    return create_app(browser=browser, worker_count=1)


@pytest.mark.anyio
async def test_browse_lists_entries_with_safe_paths(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/fs/list", params={"path": "mail", "filter": ".eml"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "mail"
    by_name = {entry["name"]: entry for entry in payload["entries"]}
    assert by_name["hello.eml"]["path"] == "mail/hello.eml"
    assert Path(by_name["hello.eml"]["input_path"]).is_absolute()
    assert by_name["nested"]["path"] == "mail/nested"
    assert by_name["nested"]["type"] == "directory"
    assert "notes.txt" not in by_name


@pytest.mark.anyio
async def test_browse_errors_use_top_level_error_envelope(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/fs/list", params={"path": "../../etc"})

    assert response.status_code == 403
    payload = response.json()
    assert "errors" in payload
    assert payload["errors"][0]["stage"] == "validation"


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
        self._status = _WatchStatus()

    async def start(self, path: str, _options: dict[str, object], _job_manager: object) -> None:
        if self._status.active:
            raise RuntimeError("watch already active")
        self._status = _WatchStatus(
            active=True,
            path=path,
            failed_events=1,
            last_error=ErrorItem(
                path="mail/failed.eml",
                code="watch_processing_error",
                message="boom",
                stage="backend",
            ),
        )

    async def stop(self) -> None:
        self._status = _WatchStatus()

    def status(self) -> _WatchStatus:
        return self._status


@pytest.mark.anyio
async def test_watch_api_start_stop_and_conflict_use_error_envelope(browser: FilesystemBrowser) -> None:
    app = create_app(
        browser=browser,
        settings_path=browser.root / "settings.json",
        watch_manager=_StubWatchManager(),
        worker_count=1,
    )
    app.state.settings.save(
        inbox_path=browser.root / "Inbox",
        cabinet_path=browser.root / "Cabinet",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        start = await client.post("/api/watch", json={"path": "mail", "options": {}})
        conflict = await client.post("/api/watch", json={"path": "mail", "options": {}})
        stop = await client.delete("/api/watch")

    assert start.status_code == 200
    assert start.json()["active"] is True
    assert start.json()["path"] == "mail"
    assert start.json()["failed_events"] == 1
    assert start.json()["last_error"]["code"] == "watch_processing_error"
    assert start.json()["latest_job_id"] is None
    assert conflict.status_code == 409
    assert "errors" in conflict.json()
    assert stop.status_code == 200
    assert stop.json()["active"] is False
    assert stop.json()["failed_events"] == 0
    assert stop.json()["last_error"] is None


@pytest.mark.anyio
async def test_browse_skips_escaping_symlink_entries(app, sample_tree: Path) -> None:
    outside = (sample_tree.parent / "outside-mail.eml").resolve()
    outside.write_text("secret", encoding="utf-8")
    (sample_tree / "mail" / "escape.eml").symlink_to(outside)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/fs/list", params={"path": "mail"})

    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()["entries"]}
    assert "hello.eml" in names
    assert "escape.eml" not in names
