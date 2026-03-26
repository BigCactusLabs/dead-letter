from __future__ import annotations

from pathlib import Path
import json

import pytest
from httpx import ASGITransport, AsyncClient

from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser


@pytest.mark.anyio
async def test_get_settings_reports_unconfigured_when_missing(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings")

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "inbox_path": None,
        "cabinet_path": None,
    }


@pytest.mark.anyio
async def test_put_settings_persists_and_creates_directories(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    settings_path = tmp_path / "settings.json"
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=settings_path,
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put(
            "/api/settings",
            json={
                "inbox_path": str(inbox),
                "cabinet_path": str(cabinet),
            },
        )
        loaded = await client.get("/api/settings")

    assert saved.status_code == 200
    assert loaded.status_code == 200
    assert inbox.is_dir()
    assert cabinet.is_dir()
    assert settings_path.is_file()
    assert loaded.json() == {
        "configured": True,
        "inbox_path": str(inbox.resolve()),
        "cabinet_path": str(cabinet.resolve()),
    }


@pytest.mark.anyio
async def test_put_settings_rejects_overlapping_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/settings",
            json={
                "inbox_path": str(workspace / "Inbox"),
                "cabinet_path": str(workspace / "Inbox" / "Cabinet"),
            },
        )

    assert response.status_code == 400
    payload = response.json()
    assert "errors" in payload
    assert payload["errors"][0]["path"] == "cabinet_path"


@pytest.mark.anyio
async def test_get_settings_treats_malformed_persisted_file_as_unconfigured(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{bad json", encoding="utf-8")
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=settings_path,
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings")

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "inbox_path": None,
        "cabinet_path": None,
    }


@pytest.mark.anyio
async def test_get_settings_treats_file_backed_paths_as_unconfigured(tmp_path: Path) -> None:
    inbox_file = tmp_path / "InboxFile"
    cabinet_file = tmp_path / "CabinetFile"
    inbox_file.write_text("x", encoding="utf-8")
    cabinet_file.write_text("y", encoding="utf-8")
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "inbox_path": str(inbox_file),
                "cabinet_path": str(cabinet_file),
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=settings_path,
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings")

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "inbox_path": None,
        "cabinet_path": None,
    }


@pytest.mark.anyio
async def test_open_folder_returns_error_envelope_when_cabinet_mkdir_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox_dir = tmp_path / "Inbox"
    cabinet_dir = tmp_path / "Cabinet"
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox_dir, cabinet_path=cabinet_dir)

    real_mkdir = Path.mkdir

    def failing_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self.resolve() == cabinet_dir.resolve():
            raise OSError("mkdir failed")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/open-folder")

    assert response.status_code == 500
    assert response.json() == {
        "errors": [
            {
                "path": str(cabinet_dir.resolve()),
                "code": "backend_error",
                "message": "mkdir failed",
                "stage": "backend",
            }
        ]
    }
