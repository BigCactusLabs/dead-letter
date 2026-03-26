from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import JobCreateRequest, JobCreateResponse, OutputLocation


class _StubJobManager:
    def __init__(self) -> None:
        self.requests: list[JobCreateRequest] = []
        self.root_updates: list[tuple[str, str]] = []

    def update_roots(self, *, inbox_root: str | Path, cabinet_root: str | Path) -> None:
        self.root_updates.append((str(Path(inbox_root).resolve()), str(Path(cabinet_root).resolve())))

    async def create_job(self, request: JobCreateRequest) -> JobCreateResponse:
        self.requests.append(request)
        return JobCreateResponse(
            id="job-456",
            status="queued",
            output_location=OutputLocation(
                strategy="cabinet",
                cabinet_path=self.root_updates[-1][1],
                bundle_path=str(Path(self.root_updates[-1][1]) / "manual"),
            ),
        )


@pytest.mark.anyio
async def test_create_job_requires_configured_settings(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )
    source = tmp_path / "outside.eml"
    source.write_text("x", encoding="utf-8")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/jobs", json={"mode": "file", "input_path": str(source), "options": {}})

    assert response.status_code == 409
    payload = response.json()
    assert "errors" in payload
    assert payload["errors"][0]["path"] == "settings"


@pytest.mark.anyio
async def test_create_job_uses_saved_settings_roots(tmp_path: Path) -> None:
    stub_manager = _StubJobManager()
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=stub_manager,
        worker_count=1,
    )
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)
    stub_manager.update_roots(inbox_root=inbox, cabinet_root=cabinet)

    source = tmp_path / "outside.eml"
    source.write_text("x", encoding="utf-8")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/jobs", json={"mode": "file", "input_path": str(source), "options": {}})

    assert response.status_code == 202
    assert stub_manager.requests[0].input_path == str(source.resolve())
    assert response.json()["output_location"]["cabinet_path"] == str(cabinet.resolve())
