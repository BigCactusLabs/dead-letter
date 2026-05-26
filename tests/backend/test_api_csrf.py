from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import JobCreateRequest, JobCreateResponse, OutputLocation

from .helpers import csrf_headers


class _StubJobManager:
    def __init__(self) -> None:
        self.requests: list[JobCreateRequest] = []

    async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> JobCreateResponse:
        _ = origin
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


@pytest.mark.anyio
async def test_get_routes_remain_ungated(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session = await client.get("/api/session")
        settings = await client.get("/api/settings")

    assert session.status_code == 200
    assert isinstance(session.json()["csrf_token"], str)
    assert settings.status_code == 200


@pytest.mark.anyio
async def test_mutating_json_route_requires_csrf_token(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/settings",
            json={
                "inbox_path": str(tmp_path / "Inbox"),
                "cabinet_path": str(tmp_path / "Cabinet"),
            },
        )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "csrf_validation_failed"


@pytest.mark.anyio
async def test_mutating_json_route_rejects_invalid_csrf_token(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/settings",
            headers={"X-Dead-Letter-CSRF": "not-the-token"},
            json={
                "inbox_path": str(tmp_path / "Inbox"),
                "cabinet_path": str(tmp_path / "Cabinet"),
            },
        )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "csrf_validation_failed"


@pytest.mark.anyio
async def test_mutating_json_route_accepts_session_token(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/settings",
            headers=await csrf_headers(client),
            json={
                "inbox_path": str(tmp_path / "Inbox"),
                "cabinet_path": str(tmp_path / "Cabinet"),
            },
        )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_hostile_origin_import_is_rejected_before_write(tmp_path: Path) -> None:
    manager = _StubJobManager()
    inbox = tmp_path / "Inbox"
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=manager,
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=tmp_path / "Cabinet")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/import",
            headers={"Origin": "https://attacker.test"},
            files={"file": ("hello.eml", b"From: a@b\n\nHello\n", "message/rfc822")},
        )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "csrf_validation_failed"
    assert manager.requests == []
    assert sorted(inbox.iterdir()) == []


@pytest.mark.anyio
async def test_cross_site_fetch_metadata_is_rejected(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=_StubJobManager(),
        worker_count=1,
    )
    app.state.settings.save(inbox_path=tmp_path / "Inbox", cabinet_path=tmp_path / "Cabinet")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await csrf_headers(client)
        headers["Sec-Fetch-Site"] = "cross-site"
        response = await client.post(
            "/api/import",
            headers=headers,
            files={"file": ("hello.eml", b"From: a@b\n\nHello\n", "message/rfc822")},
        )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "csrf_validation_failed"


@pytest.mark.anyio
async def test_same_origin_import_accepts_session_token(tmp_path: Path) -> None:
    manager = _StubJobManager()
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=manager,
        worker_count=1,
    )
    app.state.settings.save(inbox_path=tmp_path / "Inbox", cabinet_path=tmp_path / "Cabinet")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await csrf_headers(client)
        headers["Origin"] = "http://test"
        response = await client.post(
            "/api/import",
            headers=headers,
            files={"file": ("hello.eml", b"From: a@b\n\nHello\n", "message/rfc822")},
        )

    assert response.status_code == 202
    assert len(manager.requests) == 1
