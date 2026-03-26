from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.jobs import JobManager


def _sample_eml_bytes() -> bytes:
    return (
        b"From: sender@example.com\n"
        b"To: recipient@example.com\n"
        b"Subject: Uploaded Email\n"
        b"Date: Thu, 1 Jan 2026 00:00:00 +0000\n"
        b"Content-Type: text/plain\n"
        b"\n"
        b"Uploaded body.\n"
    )


async def _wait_for_terminal(client: AsyncClient, job_id: str) -> dict[str, object]:
    payload: dict[str, object] = {}
    for _ in range(100):
        response = await client.get(f"/api/jobs/{job_id}")
        payload = response.json()
        if payload["status"] in {"succeeded", "completed_with_errors", "failed", "cancelled"}:
            return payload
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach a terminal state")


@pytest.fixture
def integration_app(tmp_path: Path):
    (tmp_path / "mail").mkdir()
    (tmp_path / "mail" / "hello.eml").write_bytes(_sample_eml_bytes())
    browser = FilesystemBrowser(root=tmp_path)
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    manager = JobManager(worker_count=1, inbox_root=inbox, cabinet_root=cabinet)
    app = create_app(
        browser=browser,
        manager=manager,
        settings_path=tmp_path / "settings.json",
        worker_count=1,
    )
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)
    return app, browser, inbox, cabinet


@pytest.mark.anyio
async def test_browse_entry_input_path_can_be_submitted_to_jobs(integration_app) -> None:
    app, _browser, _inbox, cabinet = integration_app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        browse = await client.get("/api/fs/list", params={"path": "mail", "filter": ".eml"})
        entry = browse.json()["entries"][0]
        create = await client.post(
            "/api/jobs",
            json={
                "mode": "file",
                "input_path": entry["input_path"],
                "options": {"dry_run": True},
            },
        )

        assert create.status_code == 202
        terminal = await _wait_for_terminal(client, create.json()["id"])

    assert terminal["status"] == "succeeded"
    assert terminal["output_location"]["strategy"] == "cabinet"
    assert terminal["output_location"]["cabinet_path"] == str(cabinet.resolve())


@pytest.mark.anyio
async def test_import_then_convert_moves_inbox_copy_into_cabinet_bundle(integration_app) -> None:
    app, _browser, inbox, cabinet = integration_app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        imported = await client.post(
            "/api/import",
            files={"file": ("uploaded.eml", _sample_eml_bytes(), "message/rfc822")},
        )
        assert imported.status_code == 202
        imported_path = Path(imported.json()["imported_path"])
        terminal = await _wait_for_terminal(client, imported.json()["id"])

    assert terminal["status"] == "succeeded"
    assert imported_path.parent == inbox.resolve()
    assert imported_path.exists() is False
    assert terminal["output_location"]["strategy"] == "cabinet"
    assert terminal["output_location"]["cabinet_path"] == str(cabinet.resolve())
    assert Path(terminal["output_location"]["bundle_path"]).exists()
