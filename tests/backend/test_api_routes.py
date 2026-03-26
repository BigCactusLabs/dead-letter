from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import JobCreateRequest, JobCreateResponse


def _configured_app(tmp_path: Path, *, manager: object | None = None):
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
        manager=manager,
        worker_count=1,
    )
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    app.state.settings.save(inbox_path=inbox, cabinet_path=cabinet)
    if manager is not None and hasattr(manager, "update_roots"):
        manager.update_roots(inbox_root=inbox, cabinet_root=cabinet)
    return app


@pytest.mark.anyio
async def test_validation_errors_are_400() -> None:
    app = create_app(worker_count=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/jobs", json={})

    assert response.status_code == 400


@pytest.mark.anyio
async def test_create_poll_and_cancel_unknown_job(tmp_path: Path) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("placeholder", encoding="utf-8")

    app = _configured_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create = await client.post(
            "/api/jobs",
            json={
                "mode": "file",
                "input_path": str(source),
                "options": {
                    "dry_run": True,
                    "delete_eml": True,
                },
            },
        )
        assert create.status_code == 202
        payload = create.json()
        assert payload["status"] == "queued"
        assert payload["output_location"]["strategy"] == "cabinet"
        assert payload["output_location"]["cabinet_path"] == str((tmp_path / "Cabinet").resolve())
        job_id = payload["id"]

        terminal = None
        for _ in range(100):
            get_resp = await client.get(f"/api/jobs/{job_id}")
            assert get_resp.status_code == 200
            terminal = get_resp.json()
            assert "cancel_requested" in terminal
            assert isinstance(terminal["cancel_requested"], bool)
            assert "diagnostics" in terminal
            assert terminal["output_location"]["strategy"] == "cabinet"
            if terminal["status"] in {"succeeded", "completed_with_errors", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.01)

        assert terminal is not None
        assert terminal["status"] in {"succeeded", "completed_with_errors"}
        assert terminal["diagnostics"] is not None
        assert terminal["diagnostics"]["state"] in {"normal", "degraded", "review_recommended"}
        assert terminal["diagnostics"]["selected_body"] in {"html", "plain"}
        assert terminal["diagnostics"]["segmentation_path"] in {"html", "plain_fallback"}

        missing_get = await client.get("/api/jobs/missing")
        assert missing_get.status_code == 404

        missing_cancel = await client.post("/api/jobs/missing/cancel")
        assert missing_cancel.status_code == 404


@pytest.mark.anyio
async def test_cancel_terminal_job_returns_409(tmp_path: Path) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("placeholder", encoding="utf-8")

    app = _configured_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create = await client.post(
            "/api/jobs",
            json={
                "mode": "file",
                "input_path": str(source),
                "options": {"dry_run": True},
            },
        )
        job_id = create.json()["id"]

        for _ in range(100):
            get_resp = await client.get(f"/api/jobs/{job_id}")
            payload = get_resp.json()
            assert "cancel_requested" in payload
            assert isinstance(payload["cancel_requested"], bool)
            status = payload["status"]
            if status in {"succeeded", "completed_with_errors", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.01)

        cancel = await client.post(f"/api/jobs/{job_id}/cancel")
        assert cancel.status_code == 409
        payload = cancel.json()
        assert "errors" in payload
        assert payload["errors"][0]["code"] == "invalid_request"


@pytest.mark.anyio
async def test_create_job_invalid_path_returns_top_level_errors(tmp_path: Path) -> None:
    app = _configured_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/jobs",
            json={
                "mode": "file",
                "input_path": "/tmp/does-not-exist.eml",
                "options": {},
            },
        )

    assert response.status_code == 400
    payload = response.json()
    assert "errors" in payload
    assert isinstance(payload["errors"], list)
    assert payload["errors"][0]["code"] == "invalid_request"
    assert payload["errors"][0]["stage"] == "validation"


@pytest.mark.anyio
async def test_create_job_unexpected_error_returns_top_level_errors() -> None:
    class BrokenManager:
        def update_roots(self, *, inbox_root: str | Path, cabinet_root: str | Path) -> None:
            _ = (inbox_root, cabinet_root)

        async def create_job(self, _request: JobCreateRequest) -> JobCreateResponse:
            raise RuntimeError("boom")

    app = _configured_app(Path("/tmp"), manager=BrokenManager())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/jobs",
            json={
                "mode": "file",
                "input_path": "/tmp/anything.eml",
                "options": {},
            },
        )

    assert response.status_code == 500
    payload = response.json()
    assert "errors" in payload
    assert isinstance(payload["errors"], list)
    assert payload["errors"][0]["code"] == "backend_error"
    assert payload["errors"][0]["stage"] == "backend"


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["retry_with_html_repair", "retry_with_html_fallback"])
async def test_retry_job_uses_retry_action_contract(tmp_path: Path, action: str) -> None:
    class RetryStubManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def update_roots(self, *, inbox_root: str | Path, cabinet_root: str | Path) -> None:
            _ = (inbox_root, cabinet_root)

        async def retry_job(self, job_id: str, action: str) -> JobCreateResponse:
            self.calls.append((job_id, action))
            return JobCreateResponse(
                id="retry-1",
                status="queued",
                output_location={
                    "strategy": "cabinet",
                    "cabinet_path": str((tmp_path / "Cabinet").resolve()),
                    "bundle_path": str((tmp_path / "Cabinet" / "retry-1").resolve()),
                },
            )

    manager = RetryStubManager()
    app = _configured_app(tmp_path, manager=manager)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/jobs/job-123/retry",
            json={"action": action},
        )

    assert response.status_code == 202
    assert manager.calls == [("job-123", action)]
    payload = response.json()
    assert payload["id"] == "retry-1"


@pytest.mark.anyio
async def test_record_run_failure_flattens_exception_group(tmp_path: Path) -> None:
    from dead_letter.backend.schemas import JobCreateRequest, JobOptions

    app = _configured_app(tmp_path)
    manager = app.state.job_manager

    source = tmp_path / "mail.eml"
    source.write_text("placeholder", encoding="utf-8")

    resp = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(source), options=JobOptions(dry_run=True))
    )
    job_id = resp.id

    # Wait for the real job to finish so it's terminal
    await manager.wait_for_terminal(job_id, timeout=5.0)

    # Reset to "running" so _record_run_failure can transition to "failed"
    async with manager._lock:
        record = manager._jobs[job_id]
        record.status = "running"
        record.finished_at = None
        record.errors.clear()
        record.summary.errors = 0
        record.progress.failed = 0

    exc = ExceptionGroup("worker failures", [
        ValueError("disk full"),
        RuntimeError("lock poisoned"),
    ])
    await manager._record_run_failure(job_id, exc)

    snapshot = await manager.get_job(job_id)
    assert snapshot is not None
    assert snapshot.status == "failed"
    job_errors = [e for e in snapshot.errors if e.code == "job_failure"]
    assert len(job_errors) == 2
    messages = {e.message for e in job_errors}
    assert "disk full" in messages
    assert "lock poisoned" in messages
    assert snapshot.summary.errors == 2
    assert snapshot.progress.failed == 2


@pytest.mark.anyio
async def test_record_run_failure_handles_plain_exception(tmp_path: Path) -> None:
    from dead_letter.backend.schemas import JobCreateRequest, JobOptions

    app = _configured_app(tmp_path)
    manager = app.state.job_manager

    source = tmp_path / "mail.eml"
    source.write_text("placeholder", encoding="utf-8")

    resp = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(source), options=JobOptions(dry_run=True))
    )
    job_id = resp.id

    await manager.wait_for_terminal(job_id, timeout=5.0)

    async with manager._lock:
        record = manager._jobs[job_id]
        record.status = "running"
        record.finished_at = None
        record.errors.clear()
        record.summary.errors = 0
        record.progress.failed = 0

    await manager._record_run_failure(job_id, RuntimeError("something broke"))

    snapshot = await manager.get_job(job_id)
    assert snapshot is not None
    assert snapshot.status == "failed"
    job_errors = [e for e in snapshot.errors if e.code == "job_failure"]
    assert len(job_errors) == 1
    assert job_errors[0].message == "something broke"
    assert snapshot.summary.errors == 1
    assert snapshot.progress.failed == 1


@pytest.mark.anyio
async def test_history_endpoint_returns_terminal_jobs(tmp_path: Path) -> None:
    app = _configured_app(tmp_path)
    inbox = tmp_path / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        source = inbox / "test.eml"
        source.write_text("placeholder", encoding="utf-8")
        create = await client.post(
            "/api/jobs",
            json={"mode": "file", "input_path": str(source), "options": {"dry_run": True}},
        )
        assert create.status_code == 202
        job_id = create.json()["id"]

        for _ in range(100):
            poll = await client.get(f"/api/jobs/{job_id}")
            if poll.json()["status"] in {"succeeded", "completed_with_errors", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.01)

        history = await client.get("/api/jobs/history")
        assert history.status_code == 200
        body = history.json()

        assert "jobs" in body
        assert "totals" in body
        assert len(body["jobs"]) >= 1
        assert body["jobs"][0]["id"] == job_id
        assert body["jobs"][0]["origin"] == "manual"
        assert body["totals"]["jobs_completed"] >= 1


@pytest.mark.anyio
async def test_history_endpoint_respects_limit(tmp_path: Path) -> None:
    app = _configured_app(tmp_path)
    inbox = tmp_path / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for i in range(3):
            source = inbox / f"test{i}.eml"
            source.write_text("placeholder", encoding="utf-8")
            create = await client.post(
                "/api/jobs",
                json={"mode": "file", "input_path": str(source), "options": {"dry_run": True}},
            )
            job_id = create.json()["id"]
            for _ in range(100):
                poll = await client.get(f"/api/jobs/{job_id}")
                if poll.json()["status"] in {"succeeded", "completed_with_errors", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.01)

        history = await client.get("/api/jobs/history", params={"limit": 2})
        body = history.json()
        assert len(body["jobs"]) == 2
        assert body["totals"]["jobs_completed"] == 3
