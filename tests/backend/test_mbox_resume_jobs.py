"""Durable resume for web MBOX jobs, with synthetic archives only (#272)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from dead_letter.backend import jobs, mbox_jobs
from dead_letter.backend.schemas import MboxJobCreateRequest, MboxJobOptions
from dead_letter.core import mbox_resume
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.stream_report import StreamingReport

from .helpers import csrf_headers
from .test_mbox_compressed_jobs import RESERVE, _archive, _clean_staging
from .test_mbox_jobs import MESSAGE, POSTMARK, _app, _event
from .test_mbox_upload import _Body, _headers, _post

THREE = (
    POSTMARK + MESSAGE
    + POSTMARK + MESSAGE.replace(b"Hello", b"Second")
    + POSTMARK + MESSAGE.replace(b"Hello", b"Third")
)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765")


def _source(root: Path, suffix: str, *, member: str | None = None) -> tuple[Path, MboxJobOptions]:
    if suffix == ".mbox":
        source = root / "export.mbox"
        source.write_bytes(THREE)
        return source, MboxJobOptions()
    entries = [("Takeout/mail.mbox", THREE)]
    if member is not None:
        entries.append(("Takeout/other.mbox", POSTMARK + MESSAGE.replace(b"Hello", b"Other")))
    return _archive(root / ("takeout" + suffix), entries), MboxJobOptions(member=member)


def _stop_first_run_after(monkeypatch, count: int, action) -> None:
    """Act after ``count`` records of a fresh (not resumed) run have been recorded."""
    original = mbox_jobs.MboxJobRunner._record

    def record(self, item, report):
        original(self, item, report)
        if not self.resumed and item.mbox is not None and self._progress.records_processed == count:
            action(self)

    monkeypatch.setattr(mbox_jobs.MboxJobRunner, "_record", record)


def _bundles(folder: Path) -> dict[str, os.stat_result]:
    return {path.parent.name: path.stat() for path in folder.glob("*/message.md")}


def _expected_bundles(root: Path) -> dict[str, bytes]:
    flat = root / "baseline" / "mail.mbox"
    flat.parent.mkdir()
    flat.write_bytes(THREE)
    output = root / "baseline" / "out"
    assert all(row.success for row in convert_mbox(flat, output=output, bundles=True))
    return {path.parent.name: path.read_bytes() for path in output.glob("*/source.eml")}


async def _create(client: AsyncClient, source: Path, options: MboxJobOptions) -> str:
    response = await client.post(
        "/api/mbox/jobs",
        json=MboxJobCreateRequest(
            input_path=source.name, options=options,
        ).model_dump(mode="json", exclude_none=True),
        headers=await csrf_headers(client),
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def _resume(client: AsyncClient, job_id: str):
    return await client.post(f"/api/mbox/jobs/{job_id}/resume", headers=await csrf_headers(client))


async def _resumable(client: AsyncClient) -> list[dict]:
    response = await client.get("/api/mbox/resumable")
    assert response.status_code == 200
    return response.json()["jobs"]


@pytest.mark.anyio
@pytest.mark.parametrize("suffix, member", [(".mbox", None), (".zip", "Takeout/mail.mbox"), (".tgz", None)])
async def test_resume_after_cancel_reuses_completed_bundles(tmp_path, monkeypatch, suffix, member):
    app = _app(tmp_path)
    source, options = _source(tmp_path, suffix, member=member)
    original = source.read_bytes()
    _stop_first_run_after(monkeypatch, 1, lambda runner: runner.request_cancel())
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        first = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        assert first.status == "cancelled"
        assert first.resumable is True
        folder = Path(first.output_location.job_path)
        done = _bundles(folder)
        assert len(done) == 1
        [listed] = await _resumable(client)
        assert listed["id"] == job_id
        assert listed["reason"] == "cancelled"
        assert listed["records_processed"] == 1
        assert listed["member"] == member
        first_report = Path(first.report_path).read_bytes()

        response = await _resume(client, job_id)
        assert response.status_code == 202, response.text
        assert response.json()["id"] == job_id
        assert response.json()["output_location"]["job_path"] == str(folder)
        resumed = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        assert await _resumable(client) == []

    assert resumed.status == "succeeded"
    assert resumed.resumable is False
    assert resumed.mbox.resumed is True
    assert resumed.mbox.records_reused == 1
    assert resumed.mbox.records_processed == 3
    # Written counts reused outputs, as the CLI resume report does.
    assert resumed.summary.written == 3
    after = _bundles(folder)
    assert len(after) == 3
    for name, before in done.items():
        # The completed bundle was verified and kept, not rewritten.
        assert after[name].st_ino == before.st_ino
        assert after[name].st_mtime_ns == before.st_mtime_ns
    expected = _expected_bundles(tmp_path)
    assert {name: (folder / name / "source.eml").read_bytes() for name in after} == expected
    assert Path(first.report_path).read_bytes() == first_report
    assert Path(resumed.report_path) == folder / ".dead-letter-report-2.json"
    report = json.loads(Path(resumed.report_path).read_text())
    assert report["job"]["status"] == "succeeded"
    assert report["mbox_options"]["resume"] is True
    assert report["mbox_options"]["recovery_counts"] == {"new": 2, "reused": 1, "recovered": 0, "retried": 0}
    assert [entry["recovery"]["status"] for entry in report["results"]] == ["reused", "new", "new"]
    descriptor = mbox_jobs.read_job_descriptor(folder)
    assert descriptor is not None and descriptor.state == "succeeded" and descriptor.runs == 2
    assert source.read_bytes() == original
    if suffix != ".mbox":
        _clean_staging(tmp_path)
        if member is not None:
            assert report["mbox_options"]["member"] == member


@pytest.mark.anyio
async def test_resume_of_upload_readmits_the_staged_copy(tmp_path, monkeypatch):
    app = _app(tmp_path)
    _stop_first_run_after(monkeypatch, 2, lambda runner: runner.request_cancel())
    status, _, payload = await _post(app, _headers(app, len(THREE)), _Body([THREE]))
    assert status == 202
    first = await app.state.job_manager.wait_for_terminal(payload["id"], timeout=20)
    assert first.status == "cancelled" and first.resumable
    staged = Path(first.mbox.source_path)
    assert staged.parent == tmp_path / "Inbox" / "mbox-uploads"
    async with _client(app) as client:
        [listed] = await _resumable(client)
        assert listed["origin"] == "upload"
        assert listed["source_name"] == "All mail.mbox"
        assert (await _resume(client, first.id)).status_code == 202
        resumed = await app.state.job_manager.wait_for_terminal(first.id, timeout=20)
    assert resumed.status == "succeeded"
    assert resumed.origin == "upload"
    assert resumed.mbox.records_reused == 2
    assert len(_bundles(Path(resumed.output_location.job_path))) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("suffix", [".mbox", ".zip"])
async def test_resume_after_disk_reserve_stop(tmp_path, monkeypatch, suffix):
    app = _app(tmp_path)
    source, options = _source(tmp_path, suffix)
    cabinet = tmp_path / "Cabinet"
    low = {"on": True}

    def disk(path):
        published = len(list(cabinet.glob("mbox-*/*/message.md")))
        if low["on"] and "Cabinet" in Path(path).parts and published >= 2:
            return SimpleNamespace(free=RESERVE - 1)
        return SimpleNamespace(free=RESERVE * 4)

    monkeypatch.setattr(mbox_jobs.shutil, "disk_usage", disk)
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        first = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        assert first.status == "failed"
        assert first.errors[-1].code == "mbox_disk_reserve"
        assert first.resumable is True
        [listed] = await _resumable(client)
        assert listed["reason"] == "disk_space"
        # Still below the reserve: the resumed job stops the same way, no duplicates.
        assert (await _resume(client, job_id)).status_code == 202
        again = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        assert again.status == "failed" and again.resumable is True
        assert len(_bundles(Path(again.output_location.job_path))) == 2
        low["on"] = False
        assert (await _resume(client, job_id)).status_code == 202
        resumed = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
    assert resumed.status == "succeeded"
    assert resumed.mbox.records_reused == 2
    folder = Path(resumed.output_location.job_path)
    assert len(_bundles(folder)) == 3
    assert sorted(path.name for path in folder.glob(".dead-letter-report*.json")) == [
        ".dead-letter-report-2.json", ".dead-letter-report-3.json", ".dead-letter-report.json",
    ]
    assert app.state.job_manager._active_mbox_jobs == 0


_CRASH_SCRIPT = textwrap.dedent(
    """
    import asyncio, os, sys
    from pathlib import Path

    from dead_letter.backend import jobs, mbox_jobs
    from dead_letter.backend.jobs import JobManager
    from dead_letter.backend.schemas import MboxJobCreateRequest

    root = Path(sys.argv[1])
    original = mbox_jobs.MboxJobRunner._record

    def record(self, item, report):
        original(self, item, report)
        if self._progress.records_processed == 1:
            os._exit(17)  # No cleanup, report or final job state: a killed server.

    mbox_jobs.MboxJobRunner._record = record

    async def main():
        manager = JobManager(inbox_root=root / "Inbox", cabinet_root=root / "Cabinet")
        created = await manager.create_mbox_job(
            MboxJobCreateRequest(input_path=sys.argv[2]), source_root=root,
        )
        print(created.id, flush=True)
        await manager.wait_for_terminal(created.id, timeout=60)

    asyncio.run(main())
    """
)


@pytest.mark.anyio
@pytest.mark.parametrize("suffix", [".mbox", ".zip"])
async def test_resume_after_simulated_server_restart(tmp_path, suffix):
    root = tmp_path / "home"
    root.mkdir()
    source, _ = _source(root, suffix)
    script = tmp_path / "scripts" / "crash_job.py"
    script.parent.mkdir()
    script.write_text(_CRASH_SCRIPT)
    crashed = subprocess.run(
        [sys.executable, str(script), str(root), source.name],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert crashed.returncode == 17, crashed.stderr
    job_id = crashed.stdout.strip()
    folder = root / "Cabinet" / f"mbox-{job_id}"
    assert len(_bundles(folder)) == 1
    assert not list(folder.glob(".dead-letter-report*.json"))
    descriptor = mbox_jobs.read_job_descriptor(folder)
    assert descriptor is not None and descriptor.state == "running"

    # A new server process: nothing about the job is in memory.
    app = _app(root)
    async with _client(app) as client:
        [listed] = await _resumable(client)
        assert listed["id"] == job_id
        assert listed["reason"] == "interrupted"
        assert listed["records_processed"] is None
        assert (await client.get(f"/api/jobs/{job_id}")).status_code == 404
        response = await _resume(client, job_id)
        assert response.status_code == 202, response.text
        resumed = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        assert await _resumable(client) == []
    assert resumed.status == "succeeded"
    assert resumed.mbox.records_reused == 1
    assert len(_bundles(folder)) == 3
    report = json.loads(Path(resumed.report_path).read_text())
    assert Path(resumed.report_path).name == ".dead-letter-report.json"
    assert [entry["recovery"]["status"] for entry in report["results"]] == ["reused", "new", "new"]
    if suffix != ".mbox":
        # A killed process cannot remove its private staging copy; the resumed
        # run staged the member again and removed only its own copy.
        assert len(list((root / "Inbox" / "mbox-uploads").glob("mbox-job-*"))) == 1


def _change(source: Path, how: str) -> None:
    if how == "append":
        with source.open("ab") as handle:
            handle.write(POSTMARK + MESSAGE)
    elif how == "mtime":
        info = source.stat()
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000_000))
    elif how == "replace":
        replacement = source.with_name("replacement.tmp")
        replacement.write_bytes(source.read_bytes())
        os.replace(replacement, source)
    else:
        source.unlink()


@pytest.mark.anyio
@pytest.mark.parametrize("how", ["append", "mtime", "replace", "missing"])
@pytest.mark.parametrize("suffix", [".mbox", ".zip"])
async def test_resume_refused_after_source_change(tmp_path, monkeypatch, how, suffix):
    app = _app(tmp_path)
    source, options = _source(tmp_path, suffix)
    _stop_first_run_after(monkeypatch, 1, lambda runner: runner.request_cancel())
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        first = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        assert first.resumable is True
        folder = Path(first.output_location.job_path)
        before = sorted(path.relative_to(folder) for path in folder.rglob("*"))
        descriptor = (folder / mbox_jobs.MBOX_JOB_DESCRIPTOR).read_bytes()
        _change(source, how)
        assert await _resumable(client) == []
        response = await _resume(client, job_id)
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "mbox_source_changed"
    assert sorted(path.relative_to(folder) for path in folder.rglob("*")) == before
    assert (folder / mbox_jobs.MBOX_JOB_DESCRIPTOR).read_bytes() == descriptor
    assert (await app.state.job_manager.get_job(job_id)).status == "cancelled"
    assert app.state.job_manager._active_mbox_jobs == 0


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["succeeded", "source_changed", "report_error"])
async def test_finished_or_untrustworthy_jobs_are_not_resumable(tmp_path, monkeypatch, ending):
    app = _app(tmp_path)
    source, options = _source(tmp_path, ".mbox")
    if ending == "source_changed":
        _stop_first_run_after(monkeypatch, 1, lambda runner: _change(source, "append"))
    elif ending == "report_error":
        append = StreamingReport.append

        def failing(self, entry):
            if self.total == 1:
                raise OSError("synthetic report failure")
            return append(self, entry)

        monkeypatch.setattr(StreamingReport, "append", failing)
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        terminal = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        assert await _resumable(client) == []
        response = await _resume(client, job_id)
    assert terminal.status == ("succeeded" if ending == "succeeded" else "failed")
    assert terminal.resumable is False
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "mbox_resume_unavailable"


@pytest.mark.anyio
async def test_resume_keeps_slot_and_identifier_protections(tmp_path, monkeypatch):
    app = _app(tmp_path)
    source, options = _source(tmp_path, ".mbox")
    _stop_first_run_after(monkeypatch, 1, lambda runner: runner.request_cancel())
    other = tmp_path / "other.mbox"
    other.write_bytes(THREE)
    entered, release = threading.Event(), threading.Event()
    original_run = mbox_jobs.MboxJobRunner.run

    def held_run(self):
        if Path(self.admission.source) == other:
            entered.set()
            assert release.wait(10)
        return original_run(self)

    monkeypatch.setattr(mbox_jobs.MboxJobRunner, "run", held_run)
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        assert (await app.state.job_manager.wait_for_terminal(job_id, timeout=20)).resumable
        running = await _create(client, other, MboxJobOptions())
        try:
            await _event(entered)
            busy = await _resume(client, job_id)
            active = await _resume(client, running)
            listed = await _resumable(client)
        finally:
            release.set()
        await app.state.job_manager.wait_for_terminal(running, timeout=20)
        unknown = await _resume(client, "0" * 32)
        malformed = await _resume(client, "not-a-job")
        no_csrf = await client.post(f"/api/mbox/jobs/{job_id}/resume")
    assert busy.status_code == 409 and busy.json()["errors"][0]["code"] == "mbox_busy"
    assert active.status_code == 409 and active.json()["errors"][0]["code"] == "mbox_resume_unavailable"
    assert [job["id"] for job in listed] == [job_id]
    assert unknown.status_code == 404
    assert malformed.status_code == 404
    assert no_csrf.status_code == 403
    assert (await app.state.job_manager.get_job(job_id)).status == "cancelled"
    assert app.state.job_manager._active_mbox_jobs == 0


@pytest.mark.anyio
async def test_resume_blocked_by_another_journal_owner_writes_nothing(tmp_path, monkeypatch):
    app = _app(tmp_path)
    source, options = _source(tmp_path, ".mbox")
    _stop_first_run_after(monkeypatch, 1, lambda runner: runner.request_cancel())
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        first = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        folder = Path(first.output_location.job_path)
        descriptor = (folder / mbox_jobs.MBOX_JOB_DESCRIPTOR).read_bytes()
        reports = sorted(path.name for path in folder.glob(".dead-letter-report*"))
        # Another server process converting into the same folder holds this lock.
        with mbox_resume._lock(folder / ".dead-letter-resume" / "lock"):
            response = await _resume(client, job_id)
            assert response.status_code == 202, response.text
            busy = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        assert (folder / mbox_jobs.MBOX_JOB_DESCRIPTOR).read_bytes() == descriptor
        assert sorted(path.name for path in folder.glob(".dead-letter-report*")) == reports
        assert [job["id"] for job in await _resumable(client)] == [job_id]
        again = await _resume(client, job_id)
        assert again.status_code == 202, again.text
        resumed = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
    assert busy.status == "failed" and busy.report_path is None
    assert "mbox_resume_busy" in {error.code for error in busy.errors}
    # The stop this run started from is still recorded, so it can still resume.
    assert busy.resumable is True
    assert resumed.status == "succeeded"
    assert resumed.mbox.records_reused == 1
    assert Path(resumed.report_path) == folder / ".dead-letter-report-2.json"


@pytest.mark.anyio
async def test_resume_refuses_a_descriptor_changed_since_it_was_read(tmp_path, monkeypatch):
    app = _app(tmp_path)
    source, options = _source(tmp_path, ".mbox")
    _stop_first_run_after(monkeypatch, 1, lambda runner: runner.request_cancel())
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        read = jobs.read_job_descriptor
        seen = []

        def racing(folder):
            # A concurrent resume ran between this request's two reads.
            descriptor = read(folder)
            seen.append(descriptor)
            return descriptor if len(seen) == 1 else descriptor.model_copy(update={"runs": descriptor.runs + 1})

        monkeypatch.setattr(jobs, "read_job_descriptor", racing)
        response = await _resume(client, job_id)
        monkeypatch.setattr(jobs, "read_job_descriptor", read)
        listed = await _resumable(client)
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "mbox_resume_unavailable"
    assert len(seen) == 2
    assert (await app.state.job_manager.get_job(job_id)).status == "cancelled"
    assert app.state.job_manager._active_mbox_jobs == 0
    assert [job["id"] for job in listed] == [job_id]


@pytest.mark.anyio
async def test_unrecorded_stop_is_not_offered_as_interrupted(tmp_path, monkeypatch):
    app = _app(tmp_path)
    source, options = _source(tmp_path, ".mbox")
    _stop_first_run_after(monkeypatch, 1, lambda runner: runner.request_cancel())
    write = mbox_jobs.write_job_descriptor

    def failing(folder, descriptor):
        if descriptor.state != "running":
            raise OSError("synthetic descriptor failure")
        write(folder, descriptor)

    monkeypatch.setattr(mbox_jobs, "write_job_descriptor", failing)
    app.state.job_manager._max_retained_terminal_jobs = 1
    other = tmp_path / "other.mbox"
    other.write_bytes(THREE)
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        terminal = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        # On disk the job still says "running", which reads as a killed server.
        assert mbox_jobs.read_job_descriptor(Path(terminal.output_location.job_path)).state == "running"
        listed = await _resumable(client)
        response = await _resume(client, job_id)
        monkeypatch.setattr(mbox_jobs, "write_job_descriptor", write)
        # Pruning the finished job from history must not lift the guard.
        later = await _create(client, other, MboxJobOptions())
        await app.state.job_manager.wait_for_terminal(later, timeout=20)
        assert job_id not in app.state.job_manager._jobs
        pruned_list = await _resumable(client)
        pruned_resume = await _resume(client, job_id)
    assert terminal.status == "cancelled" and terminal.resumable is False
    assert listed == []
    assert job_id not in {job["id"] for job in pruned_list}
    for refused in (response, pruned_resume):
        assert refused.status_code == 409
        assert refused.json()["errors"][0]["code"] == "mbox_resume_unavailable"


@pytest.mark.anyio
async def test_job_owned_by_another_server_is_never_resumed_here(tmp_path, monkeypatch):
    app = _app(tmp_path)
    source, options = _source(tmp_path, ".mbox")
    _stop_first_run_after(monkeypatch, 1, lambda runner: runner.request_cancel())
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        first = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        folder = Path(first.output_location.job_path)
        path = folder / mbox_jobs.MBOX_JOB_DESCRIPTOR
        stopped = path.read_bytes()
        reports = sorted(item.name for item in folder.glob(".dead-letter-report*"))
        # A second server holds the job lock: it has claimed the job but not yet
        # recorded "running", then records it.
        with mbox_resume._lock(folder / mbox_jobs.MBOX_JOB_LOCK):
            response = await _resume(client, job_id)
            assert response.status_code == 202, response.text
            raced = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
            assert path.read_bytes() == stopped
            assert sorted(item.name for item in folder.glob(".dead-letter-report*")) == reports
            descriptor = mbox_jobs.read_job_descriptor(folder)
            mbox_jobs.write_job_descriptor(folder, descriptor.model_copy(update={"state": "running"}))
            live_list = await _resumable(client)
            live = await _resume(client, job_id)
        # Once that server dies, the OS releases its lock: now it was interrupted.
        [listed] = await _resumable(client)
    assert raced.status == "failed" and raced.report_path is None
    assert "mbox_resume_busy" in {error.code for error in raced.errors}
    assert live_list == []
    assert live.status_code == 409
    assert live.json()["errors"][0]["code"] == "mbox_resume_unavailable"
    assert listed["id"] == job_id and listed["reason"] == "interrupted"


@pytest.mark.anyio
async def test_filesystem_without_exclusive_publication_runs_without_resume(tmp_path, monkeypatch):
    app = _app(tmp_path)
    source, options = _source(tmp_path, ".mbox")
    monkeypatch.setattr(mbox_jobs, "supports_bundle_resume", lambda _path: False)
    _stop_first_run_after(monkeypatch, 1, lambda runner: runner.request_cancel())
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        terminal = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
        assert await _resumable(client) == []
        response = await _resume(client, job_id)
    folder = Path(terminal.output_location.job_path)
    assert terminal.status == "cancelled"
    assert terminal.resumable is False
    assert response.status_code == 409
    assert not (folder / ".dead-letter-resume").exists()
    assert "resume" not in json.loads(Path(terminal.report_path).read_text())["mbox_options"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.anyio
async def test_job_descriptor_is_private(tmp_path):
    app = _app(tmp_path)
    source, options = _source(tmp_path, ".mbox")
    async with _client(app) as client:
        job_id = await _create(client, source, options)
        terminal = await app.state.job_manager.wait_for_terminal(job_id, timeout=20)
    descriptor = Path(terminal.output_location.job_path) / mbox_jobs.MBOX_JOB_DESCRIPTOR
    assert descriptor.stat().st_mode & 0o077 == 0


def test_descriptor_reader_rejects_links_oversize_and_foreign_ids(tmp_path, monkeypatch):
    cabinet = tmp_path / "Cabinet"
    folder = cabinet / f"mbox-{'a' * 32}"
    folder.mkdir(parents=True)
    source = tmp_path / "export.mbox"
    source.write_bytes(THREE)
    descriptor = mbox_jobs.MboxJobDescriptor(
        job_id="b" * 32, origin="manual", input_path="export.mbox", source=str(source),
        identity=mbox_jobs.resume_identity(mbox_jobs._source_identity(source.stat())),
        options=MboxJobOptions(), state="cancelled", resume_supported=True, resumable=True,
        updated_at=datetime(2026, 11, 3, tzinfo=UTC),
    )
    mbox_jobs.write_job_descriptor(folder, descriptor)
    # The id inside must name the folder it sits in.
    assert mbox_jobs.read_job_descriptor(folder) is None
    assert mbox_jobs.scan_resumable_jobs(cabinet, active=set()) == []
    mbox_jobs.write_job_descriptor(folder, descriptor.model_copy(update={"job_id": "a" * 32}))
    assert mbox_jobs.read_job_descriptor(folder) is not None
    assert [job.id for job in mbox_jobs.scan_resumable_jobs(cabinet, active=set())] == ["a" * 32]
    assert mbox_jobs.scan_resumable_jobs(cabinet, active={"a" * 32}) == []
    (folder / mbox_jobs.MBOX_JOB_DESCRIPTOR).write_bytes(b" " * (64 * 1024 + 1))
    assert mbox_jobs.read_job_descriptor(folder) is None
    if os.name != "nt":
        target = tmp_path / "elsewhere.json"
        target.write_text(descriptor.model_copy(update={"job_id": "a" * 32}).model_dump_json())
        (folder / mbox_jobs.MBOX_JOB_DESCRIPTOR).unlink()
        (folder / mbox_jobs.MBOX_JOB_DESCRIPTOR).symlink_to(target)
        assert mbox_jobs.read_job_descriptor(folder) is None
        linked = cabinet / f"mbox-{'c' * 32}"
        linked.symlink_to(folder, target_is_directory=True)
        assert mbox_jobs.read_job_descriptor(linked) is None
        # Without O_NOFOLLOW (Windows) the lstat and same-file checks still refuse a link.
        monkeypatch.delattr(os, "O_NOFOLLOW")
        assert mbox_jobs.read_job_descriptor(folder) is None
