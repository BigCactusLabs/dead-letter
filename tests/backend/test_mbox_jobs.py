"""Local MBOX jobs: bounded status, filesystem boundaries and honest completion."""

from __future__ import annotations

import asyncio
import json
import threading
from email.message import EmailMessage
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from dead_letter.backend import mbox_jobs
from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.jobs import JobManager
from dead_letter.backend.mbox_jobs import MboxBusyError, MboxJobRunner
from dead_letter.backend.schemas import MboxJobCreateRequest, MboxJobOptions
from dead_letter.core.mbox import MboxCancelled
from dead_letter.core.mbox_import import MboxConversion, convert_mbox
from dead_letter.core.stream_report import StreamingReport

from .helpers import csrf_headers

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Synthetic\n\nHello\n\n"


def _manager(root: Path, *, max_mbox_jobs: int = 1) -> JobManager:
    root.mkdir(parents=True, exist_ok=True)
    return JobManager(
        inbox_root=root / "Inbox",
        cabinet_root=root / "Cabinet",
        max_mbox_jobs=max_mbox_jobs,
    )


def _app(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    manager = _manager(root)
    app = create_app(
        browser=FilesystemBrowser(root=root),
        manager=manager,
        settings_path=root / "settings.json",
    )
    app.state.settings.save(inbox_path=root / "Inbox", cabinet_path=root / "Cabinet")
    return app


async def _event(event: threading.Event) -> None:
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, 5), timeout=6), (
        "worker did not reach checkpoint"
    )


def _success(source: Path, output: Path, *, index: int = 1) -> MboxConversion:
    bundle = output / f"message-{index}"
    bundle.mkdir()
    markdown = bundle / "message.md"
    markdown.write_text("Complete published output\n")
    return MboxConversion(
        f"{source.name}#message-{index:08d}",
        markdown,
        True,
        mbox={"index": index, "end_offset": source.stat().st_size},
    )


@pytest.mark.anyio
async def test_api_streams_real_bundles_with_core_parity_and_shared_history(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    source = tmp_path / "export.mbox"
    message = EmailMessage()
    message["From"] = "alice@example.test"
    message["To"] = "reader@example.test"
    message["Subject"] = "Synthetic archive attachment"
    message["X-Gmail-Labels"] = "Inbox,Project"
    message["X-GM-THRID"] = "123456789"
    message.set_content("A synthetic attachment follows.\n")
    message.add_attachment(
        b"original attachment\x00\xff",
        maintype="application",
        subtype="octet-stream",
        filename="data.bin",
    )
    data = POSTMARK + message.as_bytes() + b"\n" + POSTMARK + POSTMARK + MESSAGE
    source.write_bytes(data)
    expected_root = tmp_path / "reference"
    expected = list(convert_mbox(source, output=expected_root, bundles=True))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as client:
        create = await client.post(
            "/api/mbox/jobs",
            json={"input_path": "export.mbox"},
            headers=await csrf_headers(client),
        )
        assert create.status_code == 202
        created = create.json()
        terminal = await app.state.job_manager.wait_for_terminal(
            created["id"], timeout=20
        )
        response = (await client.get(f"/api/jobs/{created['id']}")).json()
        history = (await client.get("/api/jobs/history")).json()
        retry = await client.post(
            f"/api/jobs/{created['id']}/retry",
            json={"action": "retry_with_html_repair"},
            headers=await csrf_headers(client),
        )
        assert retry.status_code == 409
        assert "MBOX" in retry.json()["errors"][0]["message"]

    assert terminal.status == "completed_with_errors"
    assert response["kind"] == "mbox"
    assert response["mbox"] == {
        "source_path": str(source),
        "records_total": None,
        "records_processed": 3,
        "bytes_total": len(data),
        "bytes_processed": len(data),
        "failures_omitted": 0,
        "phase": "finished",
    }
    assert response["summary"] == {"written": 2, "skipped": 0, "errors": 1}
    assert response["diagnostics"] is None
    assert response["recovery_actions"] == []
    assert history["jobs"][0]["id"] == created["id"]
    assert history["totals"]["total_written"] == 2
    destination = Path(created["output_location"]["job_path"])
    assert destination.parent == tmp_path / "Cabinet"
    assert created["output_location"]["bundle_path"] is None
    report = json.loads(Path(response["report_path"]).read_text())
    assert report["job"]["id"] == created["id"]
    assert report["job"]["status"] == terminal.status
    assert report["mbox_options"]["source_handling"] == "copy"
    assert report["mbox_options"]["timeout_seconds"] == 30
    assert report["archive"]["report_complete"] is True
    assert [row["success"] for row in report["results"]] == [
        row.success for row in expected
    ]
    for artifact in expected_root.rglob("*"):
        if artifact.is_file():
            assert (
                destination / artifact.relative_to(expected_root)
            ).read_bytes() == artifact.read_bytes()
    assert source.read_bytes() == data


@pytest.mark.parametrize(
    "unsupported",
    [
        "delete_eml",
        "dry_run",
        "report",
        "source_handling",
        "output",
        "resume",
        "bundles",
        "unescape",
    ],
)
def test_mbox_options_reject_other_surface_switches(unsupported: str) -> None:
    with pytest.raises(ValidationError):
        MboxJobCreateRequest.model_validate(
            {"input_path": "export.mbox", "options": {unsupported: True}}
        )


@pytest.mark.parametrize(
    "timeout", [0, -1, 301, float("nan"), float("inf"), None, True, False, "30"]
)
def test_mbox_timeout_must_be_a_bounded_positive_deadline(timeout) -> None:
    with pytest.raises(ValidationError):
        MboxJobOptions(timeout_seconds=timeout)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case, expected_status",
    [
        ("escape", 403),
        ("absolute", 403),
        ("symlink", 403),
        ("cabinet", 400),
        ("directory", 400),
        ("compressed", 400),
        ("missing", 400),
        ("oversize", 413),
    ],
)
async def test_api_rejects_unapproved_sources_without_staging(
    tmp_path: Path,
    monkeypatch,
    case: str,
    expected_status: int,
) -> None:
    root = tmp_path / "approved"
    app = _app(root)
    outside = tmp_path / "outside.mbox"
    outside.write_bytes(POSTMARK + MESSAGE)
    source = root / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    path = "mail.mbox"
    if case == "escape":
        path = "../outside.mbox"
    elif case == "absolute":
        path = str(source)
    elif case == "symlink":
        (root / "alias.mbox").symlink_to(outside)
        path = "alias.mbox"
    elif case == "cabinet":
        (root / "Cabinet" / "mail.mbox").write_bytes(MESSAGE)
        path = "Cabinet/mail.mbox"
    elif case == "directory":
        (root / "Apple.mbox").mkdir()
        path = "Apple.mbox"
    elif case == "compressed":
        (root / "mail.mbox.gz").write_bytes(MESSAGE)
        path = "mail.mbox.gz"
    elif case == "missing":
        path = "missing.mbox"
    elif case == "oversize":
        monkeypatch.setattr(
            mbox_jobs, "MAX_MBOX_ARCHIVE_BYTES", source.stat().st_size - 1
        )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as client:
        response = await client.post(
            "/api/mbox/jobs",
            json={"input_path": path},
            headers=await csrf_headers(client),
        )
    assert response.status_code == expected_status
    assert not list((root / "Cabinet").glob("mbox-*"))
    assert not app.state.job_manager._jobs


@pytest.mark.anyio
async def test_mbox_route_requires_configuration_and_csrf(tmp_path: Path) -> None:
    app = create_app(
        browser=FilesystemBrowser(root=tmp_path),
        settings_path=tmp_path / "settings.json",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as client:
        missing_token = await client.post(
            "/api/mbox/jobs", json={"input_path": "mail.mbox"}
        )
        assert missing_token.status_code == 403
        unconfigured = await client.post(
            "/api/mbox/jobs",
            json={"input_path": "mail.mbox"},
            headers=await csrf_headers(client),
        )
        assert unconfigured.status_code == 409
        assert unconfigured.json()["errors"][0]["path"] == "settings"


@pytest.mark.anyio
async def test_fatal_suffix_preserves_prior_output_without_counting_a_message(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(
        POSTMARK + MESSAGE + POSTMARK + b"Content-Length: 8\n\nUnsafe\n\n"
    )
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    terminal = await manager.wait_for_terminal(created.id, timeout=20)
    assert terminal.status == "failed"
    assert terminal.summary.written == 1
    assert terminal.summary.errors == 1
    assert terminal.mbox.records_processed == 1
    assert terminal.mbox.bytes_processed < terminal.mbox.bytes_total
    report = json.loads(Path(terminal.report_path).read_text())
    assert report["job"]["status"] == "failed"
    assert len(report["results"]) == 2
    assert "mbox" not in report["results"][-1]
    assert list(Path(created.output_location.job_path).glob("*/message.md"))


@pytest.mark.anyio
async def test_status_memory_is_bounded_but_report_keeps_every_failure(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)

    def failures(path, **kwargs):
        for index in range(1000):
            yield MboxConversion(
                "mail.mbox#" + str(index),
                None,
                False,
                mbox={"index": index + 1, "end_offset": source.stat().st_size},
                error={
                    "code": "conversion_error",
                    "message": "x" * 4096,
                    "stage": "core",
                },
            )

    monkeypatch.setattr(mbox_jobs, "convert_mbox", failures)
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    terminal = await manager.wait_for_terminal(created.id)
    assert terminal.status == "completed_with_errors"
    assert terminal.mbox.records_processed == 1000
    assert terminal.mbox.records_total is None
    assert terminal.summary.errors == 1000
    assert len(terminal.errors) == 20
    assert terminal.mbox.failures_omitted == 980
    assert len(terminal.model_dump_json()) < 20_000
    assert len(json.loads(Path(terminal.report_path).read_text())["results"]) == 1000


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code, expected_status, expected_records",
    [
        ("mbox_message_timeout", "completed_with_errors", 2),
        ("conversion_error", "completed_with_errors", 2),
        ("mbox_worker_crashed", "completed_with_errors", 2),
        ("mbox_publish_failed", "failed", 1),
        ("mbox_storage_error", "failed", 1),
    ],
)
async def test_message_failures_continue_but_output_failures_stop(
    tmp_path: Path,
    monkeypatch,
    code: str,
    expected_status: str,
    expected_records: int,
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)

    def results(path, *, output, **kwargs):
        yield MboxConversion(
            "mail.mbox#1",
            None,
            False,
            mbox={"index": 1, "end_offset": 1},
            error={"code": code, "message": "Synthetic failure", "stage": "worker"},
        )
        yield _success(Path(path), Path(output), index=2)

    monkeypatch.setattr(mbox_jobs, "convert_mbox", results)
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    terminal = await manager.wait_for_terminal(created.id)
    assert terminal.status == expected_status
    assert terminal.mbox.records_processed == expected_records
    assert terminal.summary.written == expected_records - 1


@pytest.mark.anyio
async def test_changed_queued_source_is_rejected_before_output_allocation(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    entered, release = threading.Event(), threading.Event()
    original = MboxJobRunner.run

    def paused(self):
        entered.set()
        assert release.wait(5)
        return original(self)

    monkeypatch.setattr(MboxJobRunner, "run", paused)
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    try:
        await _event(entered)
        source.write_bytes(POSTMARK + MESSAGE + b"changed\n")
    finally:
        release.set()
    terminal = await manager.wait_for_terminal(created.id)
    assert terminal.status == "failed"
    assert terminal.errors[0].code == "mbox_source_changed"
    assert not Path(created.output_location.job_path).exists()
    assert terminal.report_path is None


@pytest.mark.anyio
async def test_changed_source_mid_import_preserves_record_and_fails(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)

    def results(path, *, output, **kwargs):
        row = _success(Path(path), Path(output))
        source.write_bytes(source.read_bytes() + b"mutated\n")
        yield row
        pytest.fail("changed source must not proceed to another record")

    monkeypatch.setattr(mbox_jobs, "convert_mbox", results)
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    terminal = await manager.wait_for_terminal(created.id)
    assert terminal.status == "failed"
    assert terminal.summary.written == 1
    assert terminal.mbox.records_processed == 1
    assert terminal.errors[0].code == "mbox_source_changed"
    assert (
        json.loads(Path(terminal.report_path).read_text())["job"]["status"] == "failed"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("slots", [1, 2])
async def test_global_admission_waits_for_real_cleanup_before_releasing_slot(
    tmp_path: Path, monkeypatch, slots: int
) -> None:
    manager = _manager(tmp_path, max_mbox_jobs=slots)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    release = threading.Event()
    cancelled = threading.Event()

    def waiting(path, *, cancel_requested, **kwargs):
        while not cancel_requested():
            release.wait(0.01)
        cancelled.set()
        assert release.wait(5)
        raise MboxCancelled()
        yield  # generator without a fabricated result

    monkeypatch.setattr(mbox_jobs, "convert_mbox", waiting)
    request = MboxJobCreateRequest(input_path="mail.mbox")
    created = [
        await manager.create_mbox_job(request, source_root=tmp_path)
        for _ in range(slots)
    ]
    # No event-loop turn has started the workers: queued jobs already reserve slots.
    assert not (tmp_path / "Cabinet").exists()
    with pytest.raises(MboxBusyError):
        await manager.create_mbox_job(request, source_root=tmp_path)
    try:
        for job in created:
            cancel = await manager.cancel_job(job.id)
            assert cancel.accepted and cancel.status == "queued"
        await _event(cancelled)
        with pytest.raises(MboxBusyError):
            await manager.create_mbox_job(request, source_root=tmp_path)
        statuses = [(await manager.get_job(job.id)).status for job in created]
        assert statuses == ["running"] * slots
    finally:
        release.set()
    for job in created:
        terminal = await manager.wait_for_terminal(job.id)
        assert terminal.status == "cancelled"
        assert terminal.mbox.records_processed == 0
        assert (
            json.loads(Path(terminal.report_path).read_text())["job"]["status"]
            == "cancelled"
        )
    monkeypatch.setattr(
        mbox_jobs, "convert_mbox", lambda *args, **kwargs: (row for row in ())
    )
    next_job = await manager.create_mbox_job(request, source_root=tmp_path)
    assert (await manager.wait_for_terminal(next_job.id)).status == "succeeded"


@pytest.mark.anyio
@pytest.mark.parametrize("failure_on_call", [None, 1, 2])
async def test_cancellation_during_report_publication_stays_nonterminal(
    tmp_path: Path,
    monkeypatch,
    failure_on_call: int | None,
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    entered, release = threading.Event(), threading.Event()
    original = StreamingReport.finish
    statuses = []

    def results(path, *, output, **kwargs):
        yield _success(Path(path), Path(output))

    def finish(self, *args, **kwargs):
        statuses.append(kwargs["status"])
        if len(statuses) == 1:
            entered.set()
            assert release.wait(5)
        if len(statuses) == failure_on_call:
            raise OSError("synthetic disk full")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(mbox_jobs, "convert_mbox", results)
    monkeypatch.setattr(StreamingReport, "finish", finish)
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    try:
        await _event(entered)
        cancel = await manager.cancel_job(created.id)
        assert cancel.accepted
        progress = await manager.get_job(created.id)
        assert progress.status == "running"
        assert progress.cancel_requested
        assert progress.finished_at is None
        assert progress.mbox.phase == "publishing"
        assert progress.report_path is None
    finally:
        release.set()
    terminal = await manager.wait_for_terminal(created.id)
    assert terminal.summary.written == 1
    assert list(Path(created.output_location.job_path).glob("*/message.md"))
    if failure_on_call is not None:
        assert terminal.status == "failed"
        assert terminal.report_path is None
        assert terminal.errors[0].code == "mbox_report_error"
        assert not (
            Path(created.output_location.job_path) / ".dead-letter-report.json"
        ).exists()
        assert not (
            Path(created.output_location.job_path) / ".dead-letter-report.pending.json"
        ).exists()
    else:
        assert terminal.status == "cancelled"
        assert statuses == ["succeeded", "cancelled"]
        report = json.loads(Path(terminal.report_path).read_text())
        assert report["job"]["status"] == "cancelled"
        assert report["summary"]["written"] == 1


@pytest.mark.anyio
async def test_report_append_failure_preserves_actual_output_counts(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)

    def results(path, *, output, **kwargs):
        yield _success(Path(path), Path(output))
        pytest.fail("report append failure must stop conversion")

    def fail_append(self, entry):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(mbox_jobs, "convert_mbox", results)
    monkeypatch.setattr(StreamingReport, "append", fail_append)
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    terminal = await manager.wait_for_terminal(created.id)
    assert terminal.status == "failed"
    assert terminal.summary.written == 1
    assert terminal.summary.errors == 1
    assert terminal.errors[0].code == "mbox_report_error"
    assert list(Path(created.output_location.job_path).glob("*/message.md"))
    report = json.loads(Path(terminal.report_path).read_text())
    assert report["job"]["status"] == "failed"
    assert report["results"] == []
    assert report["archive"]["observed_summary"]["written"] == 1
    assert report["archive"]["report_complete"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("shutdown", ["lifespan", "task_cancel"])
async def test_shutdown_waits_for_generator_cleanup_and_report(
    tmp_path: Path, monkeypatch, shutdown: str
) -> None:
    app = _app(tmp_path)
    manager = app.state.job_manager
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    entered, cancelled, release, closed = (threading.Event() for _ in range(4))

    def waiting(path, *, cancel_requested, output, **kwargs):
        try:
            yield _success(Path(path), Path(output))
            entered.set()
            while not cancel_requested():
                release.wait(0.01)
            cancelled.set()
            assert release.wait(5)
            raise MboxCancelled()
        finally:
            closed.set()

    monkeypatch.setattr(mbox_jobs, "convert_mbox", waiting)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    try:
        await _event(entered)
        if shutdown == "lifespan":
            stopping = asyncio.create_task(lifespan.__aexit__(None, None, None))
        else:
            stopping = next(iter(manager._mbox_tasks))
            stopping.cancel()
        await _event(cancelled)
        assert not stopping.done()
        assert not closed.is_set()
        assert (await manager.get_job(created.id)).status == "running"
    finally:
        release.set()
    await asyncio.wait_for(stopping, timeout=6)
    terminal = await manager.wait_for_terminal(created.id)
    assert closed.is_set()
    assert terminal.status == "cancelled"
    assert terminal.summary.written == 1
    assert (
        json.loads(Path(terminal.report_path).read_text())["job"]["status"]
        == "cancelled"
    )
    if shutdown == "task_cancel":
        await lifespan.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_fatal_report_error_remains_in_a_full_failure_sample(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    original = StreamingReport.append

    def results(path, *, output, **kwargs):
        for index in range(20):
            yield MboxConversion(
                f"mail.mbox#{index}",
                None,
                False,
                mbox={"index": index + 1, "end_offset": 1},
                error={
                    "code": "conversion_error",
                    "message": "Recoverable",
                    "stage": "core",
                },
            )
        yield _success(Path(path), Path(output), index=21)

    def append(self, entry):
        if entry["success"]:
            raise OSError("synthetic disk full")
        original(self, entry)

    monkeypatch.setattr(mbox_jobs, "convert_mbox", results)
    monkeypatch.setattr(StreamingReport, "append", append)
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    terminal = await manager.wait_for_terminal(created.id)
    assert terminal.status == "failed"
    assert terminal.summary.errors == 21
    assert terminal.summary.written == 1
    assert len(terminal.errors) == 20
    assert terminal.errors[-1].code == "mbox_report_error"
    assert terminal.mbox.failures_omitted == 1
    assert (
        json.loads(Path(terminal.report_path).read_text())["archive"]["report_complete"]
        is False
    )


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["before_submission", "terminal_commit"])
async def test_direct_task_cancel_during_manager_lock_wait_commits_outcome(
    tmp_path: Path,
    monkeypatch,
    phase: str,
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    entered, release, finished = (threading.Event() for _ in range(3))
    original_run = MboxJobRunner.run

    def results(path, *, output, cancel_requested, **kwargs):
        if cancel_requested():
            raise MboxCancelled()
        yield _success(Path(path), Path(output))
        entered.set()
        assert release.wait(5)

    def run(self):
        outcome = original_run(self)
        finished.set()
        return outcome

    monkeypatch.setattr(mbox_jobs, "convert_mbox", results)
    monkeypatch.setattr(MboxJobRunner, "run", run)
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path="mail.mbox"), source_root=tmp_path
    )
    task = next(iter(manager._mbox_tasks))
    if phase == "terminal_commit":
        await _event(entered)
    await manager._lock.acquire()
    try:
        if phase == "terminal_commit":
            release.set()
            await _event(finished)
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        manager._lock.release()
        release.set()
    await asyncio.wait_for(task, timeout=6)
    terminal = await manager.get_job(created.id)
    expected = "cancelled" if phase == "before_submission" else "succeeded"
    assert terminal.status == expected
    assert (
        json.loads(Path(terminal.report_path).read_text())["job"]["status"] == expected
    )
    assert manager._active_mbox_jobs == 0


@pytest.mark.anyio
@pytest.mark.parametrize("yield_before_cancel", [False, True])
async def test_task_cancel_at_any_point_releases_the_slot_exactly_once(
    tmp_path: Path,
    monkeypatch,
    yield_before_cancel: bool,
) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)

    def results(path, *, output, cancel_requested, **kwargs):
        if cancel_requested():
            raise MboxCancelled()
        yield _success(Path(path), Path(output))

    monkeypatch.setattr(mbox_jobs, "convert_mbox", results)
    request = MboxJobCreateRequest(input_path="mail.mbox")
    created = await manager.create_mbox_job(request, source_root=tmp_path)
    task = next(iter(manager._mbox_tasks))
    if yield_before_cancel:
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    terminal = await manager.get_job(created.id)
    assert terminal.status == "cancelled"
    assert terminal.mbox.phase == "finished"
    assert manager._active_mbox_jobs == 0
    assert not manager._mbox_tasks
    if not yield_before_cancel:
        # The worker never started, so it allocated no job folder.
        assert terminal.report_path is None
        assert not Path(terminal.output_location.job_path).exists()
    late = await manager.cancel_job(created.id)
    assert (late.status, late.accepted) == ("cancelled", False)

    # One slot remains: a double release would admit two concurrent archives.
    following = await manager.create_mbox_job(request, source_root=tmp_path)
    with pytest.raises(MboxBusyError):
        await manager.create_mbox_job(request, source_root=tmp_path)
    finished = await manager.wait_for_terminal(following.id)
    assert finished.status == "succeeded"
    assert manager._active_mbox_jobs == 0
