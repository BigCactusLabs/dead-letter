"""Compressed web imports and reserve stops use synthetic, isolated storage."""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from dead_letter.backend import mbox_jobs
from dead_letter.backend.schemas import MboxJobCreateRequest, MboxJobOptions
from dead_letter.backend.uploads import MBOX_UPLOAD_DISK_RESERVE_BYTES
from dead_letter.core.mbox import MboxCancelled
from dead_letter.core.mbox_import import convert_mbox

from .helpers import csrf_headers
from .test_mbox_jobs import MESSAGE, POSTMARK, _app, _event, _manager, _success
from .test_mbox_upload import _Body, _headers, _post

MAIL = POSTMARK + MESSAGE + POSTMARK + MESSAGE.replace(b"Hello", b"Second")
RESERVE = MBOX_UPLOAD_DISK_RESERVE_BYTES


def _archive(path: Path, entries=None) -> Path:
    entries = entries or [("Takeout/mail.mbox", MAIL)]
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
    else:
        with tarfile.open(path, "w:gz") as archive:
            for name, data in entries:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return path


def _clean_staging(root: Path) -> None:
    assert not list((root / "Inbox").rglob("mbox-job-*"))
    assert not list((root / "Inbox").rglob("member.mbox"))


async def _run(root: Path, source: Path, *, options=None):
    manager = _manager(root)
    created = await manager.create_mbox_job(
        MboxJobCreateRequest(input_path=source.name, options=options or MboxJobOptions()),
        source_root=root,
    )
    terminal = await manager.wait_for_terminal(created.id, timeout=20)
    assert manager._active_mbox_jobs == 0
    _clean_staging(root)
    return terminal


@pytest.mark.anyio
@pytest.mark.parametrize("suffix", [".zip", ".tgz", ".tar.gz", ".ZIP", ".TGZ", ".TAR.GZ"])
async def test_local_compressed_job_has_flat_bundle_parity(tmp_path, suffix):
    source = _archive(tmp_path / ("takeout" + suffix))
    before = source.read_bytes()
    flat = tmp_path / "mail.mbox"
    flat.write_bytes(MAIL)
    expected = tmp_path / "expected"
    assert all(row.success for row in convert_mbox(flat, output=expected, bundles=True))
    terminal = await _run(tmp_path, source)
    assert terminal.status == "succeeded"
    assert terminal.summary.written == 2
    assert terminal.mbox.bytes_total == len(MAIL)
    assert terminal.mbox.bytes_processed == len(MAIL)
    actual = Path(terminal.output_location.job_path)
    baseline = {p.relative_to(expected): p for p in expected.rglob("*") if p.is_file()}
    output = {
        p.relative_to(actual): p for p in actual.rglob("*")
        if p.is_file() and p.name not in {".dead-letter-report.json", ".dead-letter-web-job.json"}
        and ".dead-letter-resume" not in p.relative_to(actual).parts
    }
    assert baseline.keys() == output.keys()
    for name, path in baseline.items():
        if name.name == "message.md":
            left, right = path.read_text().split("---", 2), output[name].read_text().split("---", 2)
            assert left[2] == right[2]
            a, b = yaml.safe_load(left[1]), yaml.safe_load(right[1])
            a["source_mbox"].pop("archive")
            assert b["source_mbox"].pop("archive") == source.name
            assert b["source_mbox"].pop("container")["member_name"] == "Takeout/mail.mbox"
            assert a == b
        else:
            assert path.read_bytes() == output[name].read_bytes()
    report = json.loads(Path(terminal.report_path).read_text())
    assert report["mbox_options"]["source_handling"] == "copy"
    assert report["archive"]["container_size"] == len(before)
    assert report["archive"]["staged_bytes"] == len(MAIL)
    assert source.read_bytes() == before


@pytest.mark.anyio
@pytest.mark.parametrize("suffix", [".zip", ".tgz"])
@pytest.mark.parametrize("member", [None, "Second.mbox", "missing.mbox"])
async def test_member_selection_uses_core_errors(tmp_path, suffix, member):
    source = _archive(tmp_path / ("takeout" + suffix), [("First.mbox", MAIL), ("Second.mbox", POSTMARK + MESSAGE)])
    terminal = await _run(tmp_path, source, options=MboxJobOptions(member=member))
    if member == "Second.mbox":
        assert terminal.status == "succeeded"
        assert terminal.summary.written == 1
    else:
        assert terminal.status == "failed"
        assert terminal.summary.written == 0
        assert terminal.errors[0].code == ("mbox_archive_multiple_mbox" if member is None else "mbox_archive_no_mbox")
        assert Path(terminal.report_path).is_file()


@pytest.mark.anyio
async def test_member_on_flat_job_and_upload_is_422_before_body(tmp_path):
    app = _app(tmp_path)
    (tmp_path / "mail.mbox").write_bytes(MAIL)
    (tmp_path / "alias.zip").symlink_to(tmp_path / "mail.mbox")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        reply = await client.post("/api/mbox/jobs", json={"input_path": "mail.mbox", "options": {"member": "a.mbox"}}, headers=await csrf_headers(client))
        assert reply.status_code == 422
        alias = await client.post("/api/mbox/jobs", json={"input_path": "alias.zip", "options": {"member": "a.mbox"}}, headers=await csrf_headers(client))
        assert alias.status_code == 422
    body = _Body([MAIL])
    status, _, _ = await _post(app, _headers(app, len(MAIL)), body, query="options=" + quote('{"member":"a.mbox"}'))
    assert status == 422
    assert body.calls == 0
    assert not app.state.job_manager._jobs


@pytest.mark.anyio
@pytest.mark.parametrize("suffix", [".zip", ".tgz", ".tar.gz", ".TAR.GZ"])
async def test_compressed_upload_preserves_suffix_and_completes(tmp_path, suffix):
    app = _app(tmp_path)
    source = _archive(tmp_path / ("takeout" + suffix))
    data = source.read_bytes()
    staging = tmp_path / "Inbox" / "mbox-uploads"
    staging.mkdir()
    existing = staging / source.name
    existing.write_bytes(b"keep existing source")
    status, _, payload = await _post(app, _headers(app, len(data), name=source.name), _Body([data]))
    assert status == 202
    terminal = await app.state.job_manager.wait_for_terminal(payload["id"], timeout=20)
    assert terminal.status == "succeeded"
    assert Path(terminal.mbox.source_path).name == "takeout-2" + suffix
    assert Path(terminal.mbox.source_path).read_bytes() == data
    assert existing.read_bytes() == b"keep existing source"
    _clean_staging(tmp_path)


@pytest.mark.anyio
@pytest.mark.parametrize("volume", ["Cabinet", "staging"])
@pytest.mark.parametrize("raises", [False, True])
async def test_reserve_fails_before_conversion_and_measurement_fails_closed(tmp_path, monkeypatch, volume, raises):
    source = _archive(tmp_path / "takeout.zip")
    def disk(path):
        selected = ("Cabinet" in Path(path).parts) == (volume == "Cabinet")
        if selected and raises:
            raise OSError("cannot measure")
        return SimpleNamespace(free=RESERVE - 1 if selected else RESERVE * 4)
    monkeypatch.setattr(mbox_jobs.shutil, "disk_usage", disk)
    monkeypatch.setattr(mbox_jobs, "convert_mbox_archive", lambda *a, **k: pytest.fail("conversion started"))
    terminal = await _run(tmp_path, source)
    assert terminal.status == "failed"
    assert terminal.errors[0].code == "mbox_disk_reserve"
    assert terminal.summary.written == 0
    assert ("could not be measured" in terminal.errors[0].message) == raises
    assert json.loads(Path(terminal.report_path).read_text())["results"][0]["error"]["code"] == "mbox_disk_reserve"


@pytest.mark.anyio
@pytest.mark.parametrize("suffix", [".zip", ".tgz"])
@pytest.mark.parametrize("raises", [False, True])
async def test_reserve_stops_during_staging_at_64_mib(tmp_path, monkeypatch, suffix, raises):
    source = _archive(tmp_path / ("takeout" + suffix), [("mail.mbox", b"x" * (65 * 1024 * 1024))])
    observed = []
    def disk(path):
        staged = Path(path) / "member.mbox"
        size = staged.stat().st_size if staged.exists() else 0
        observed.append(size)
        if size > 0:
            if raises:
                raise OSError("cannot measure")
            return SimpleNamespace(free=RESERVE - 1)
        return SimpleNamespace(free=RESERVE * 4)
    monkeypatch.setattr(mbox_jobs.shutil, "disk_usage", disk)
    terminal = await _run(tmp_path, source)
    assert terminal.status == "failed"
    assert terminal.errors[0].code == "mbox_disk_reserve"
    assert terminal.summary.written == 0
    assert 63 * 1024 * 1024 < max(observed) <= 64 * 1024 * 1024
    assert Path(terminal.report_path).is_file()


@pytest.mark.anyio
@pytest.mark.parametrize("suffix", [".mbox", ".zip"])
@pytest.mark.parametrize("raises", [False, True])
async def test_reserve_stops_after_two_published_messages_and_keeps_report(tmp_path, monkeypatch, suffix, raises):
    source = tmp_path / ("mail" + suffix)
    data = MAIL + POSTMARK + MESSAGE.replace(b"Hello", b"Third")
    if suffix == ".mbox":
        source.write_bytes(data)
    else:
        _archive(source, [("mail.mbox", data)])
    def disk(path):
        count = len(list((tmp_path / "Cabinet").rglob("message.md")))
        if "Cabinet" in Path(path).parts and count >= 2:
            if raises:
                raise OSError("cannot measure")
            return SimpleNamespace(free=RESERVE - 1)
        return SimpleNamespace(free=RESERVE * 4)
    monkeypatch.setattr(mbox_jobs.shutil, "disk_usage", disk)
    terminal = await _run(tmp_path, source)
    assert terminal.status == "failed"
    assert terminal.summary.written == 2
    assert terminal.errors[-1].code == "mbox_disk_reserve"
    assert len(list((tmp_path / "Cabinet").rglob("message.md"))) == 2
    report = json.loads(Path(terminal.report_path).read_text())
    assert report["job"]["status"] == "failed"
    assert [entry["success"] for entry in report["results"]] == [True, True, False]
    assert report["results"][-1]["error"]["code"] == "mbox_disk_reserve"


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["before_first_step", "staging", "conversion"])
async def test_compressed_cancellation_cleans_staging_and_releases_slot(tmp_path, monkeypatch, phase):
    manager = _manager(tmp_path)
    source = _archive(tmp_path / "takeout.zip")
    entered, release = threading.Event(), threading.Event()
    if phase == "staging":
        original = mbox_jobs.MboxJobRunner._check_staging
        def hold(self, path, written):
            assert path.is_relative_to(tmp_path / "Inbox" / "mbox-uploads")
            if os.name != "nt":  # Windows reports ACL-backed dirs as 0o777
                assert path.parent.stat().st_mode & 0o077 == 0
            entered.set()
            assert release.wait(5)
            original(self, path, written)
        monkeypatch.setattr(mbox_jobs.MboxJobRunner, "_check_staging", hold)
    elif phase == "conversion":
        from dead_letter.core import mbox_archive
        def results(path, *, output, cancel_requested, **kwargs):
            yield _success(Path(path), Path(output))
            entered.set()
            assert release.wait(5)
            if cancel_requested():
                raise MboxCancelled()
            pytest.fail("worker was not cancelled")
        monkeypatch.setattr(mbox_archive, "_convert_mbox", results)
    request = MboxJobCreateRequest(input_path=source.name)
    created = await manager.create_mbox_job(request, source_root=tmp_path)
    task = next(iter(manager._mbox_tasks))
    try:
        if phase == "before_first_step":
            task.cancel()
        else:
            await _event(entered)
            assert (await manager.cancel_job(created.id)).accepted
    finally:
        release.set()
    await asyncio.gather(task, return_exceptions=True)
    terminal = await manager.get_job(created.id)
    assert terminal.status == "cancelled"
    assert manager._active_mbox_jobs == 0
    assert not manager._mbox_tasks
    _clean_staging(tmp_path)
    assert source.is_file()
    if phase != "before_first_step":
        report = json.loads(Path(terminal.report_path).read_text())
        assert report["job"]["status"] == "cancelled"
        assert terminal.summary.written == (1 if phase == "conversion" else 0)
    else:
        assert terminal.report_path is None
        assert not Path(terminal.output_location.job_path).exists()


@pytest.mark.anyio
@pytest.mark.parametrize("suffix", [".zip", ".tgz"])
@pytest.mark.parametrize("case, expected", [("escape", 403), ("cabinet", 400), ("directory", 400), ("oversize", 413)])
async def test_compressed_api_admission_keeps_existing_boundaries(tmp_path, monkeypatch, suffix, case, expected):
    root = tmp_path / "browser"
    app = _app(root)
    source = _archive(root / ("mail" + suffix))
    path = source.name
    if case == "escape":
        outside = _archive(tmp_path / ("outside" + suffix))
        (root / ("alias" + suffix)).symlink_to(outside)
        path = "alias" + suffix
    elif case == "cabinet":
        source = _archive(root / "Cabinet" / source.name)
        path = "Cabinet/" + source.name
    elif case == "directory":
        path = "folder" + suffix
        (root / path).mkdir()
    else:
        monkeypatch.setattr(mbox_jobs, "MAX_MBOX_ARCHIVE_BYTES", source.stat().st_size - 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post("/api/mbox/jobs", json={"input_path": path}, headers=await csrf_headers(client))
    assert response.status_code == expected
    assert not app.state.job_manager._jobs
    _clean_staging(root)


@pytest.mark.anyio
async def test_cancel_wins_reserve_failure_and_keeps_partial_report(tmp_path, monkeypatch):
    source = _archive(tmp_path / "takeout.zip")
    manager = _manager(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def disk(path):
        if (Path(path) / "member.mbox").exists():
            entered.set()
            assert release.wait(5)
            return SimpleNamespace(free=RESERVE - 1)
        return SimpleNamespace(free=RESERVE * 4)
    monkeypatch.setattr(mbox_jobs.shutil, "disk_usage", disk)
    created = await manager.create_mbox_job(MboxJobCreateRequest(input_path=source.name), source_root=tmp_path)
    try:
        await _event(entered)
        assert (await manager.cancel_job(created.id)).accepted
    finally:
        release.set()
    terminal = await manager.wait_for_terminal(created.id, timeout=20)
    assert terminal.status == "cancelled"
    assert terminal.errors[-1].code == "mbox_disk_reserve"
    assert json.loads(Path(terminal.report_path).read_text())["job"]["status"] == "cancelled"
    assert manager._active_mbox_jobs == 0
    _clean_staging(tmp_path)


@pytest.mark.parametrize("cancel_at", ["reserve_check", "reserve_row", "publication"])
@pytest.mark.parametrize("report_error", [False, True])
def test_reserve_failure_order_and_report_error_precedence(tmp_path, monkeypatch, cancel_at, report_error):
    from dead_letter.core.stream_report import StreamingReport

    source = _archive(tmp_path / "takeout.zip", [("mail.mbox", MAIL + POSTMARK + MESSAGE)])
    cabinet = tmp_path / "Cabinet"
    runner = mbox_jobs.MboxJobRunner(
        admission=mbox_jobs.admit_mbox_source(source.name, source_root=tmp_path, cabinet_root=cabinet),
        cabinet_root=cabinet, inbox_root=tmp_path / "Inbox", job_id="precedence",
        options=MboxJobOptions(),
    )
    append, finish = StreamingReport.append, StreamingReport.finish
    statuses = []

    def disk(path):
        if Path(path) == cabinet and len(list(cabinet.rglob("message.md"))) == 2:
            if cancel_at == "reserve_check":
                assert runner.request_cancel()
            return SimpleNamespace(free=RESERVE - 1)
        return SimpleNamespace(free=RESERVE * 4)

    def append_row(self, entry):
        if (entry.get("error") or {}).get("code") == "mbox_disk_reserve":
            if cancel_at == "reserve_row":
                assert runner.request_cancel()
            if report_error:
                raise OSError("synthetic report append failure")
        return append(self, entry)

    def publish(self, *args, **kwargs):
        statuses.append(kwargs["status"])
        if cancel_at == "publication":
            assert runner.request_cancel()
        return finish(self, *args, **kwargs)

    monkeypatch.setattr(mbox_jobs.shutil, "disk_usage", disk)
    monkeypatch.setattr(StreamingReport, "append", append_row)
    monkeypatch.setattr(StreamingReport, "finish", publish)
    outcome = runner.run()
    expected = "cancelled" if cancel_at == "reserve_check" and not report_error else "failed"
    assert outcome.status == expected
    assert statuses == [expected]
    _, summary, errors = runner.snapshot()
    assert summary.written == 2
    assert len(list(cabinet.rglob("message.md"))) == 2
    assert [error.code for error in errors] == ["mbox_disk_reserve"] + (["mbox_report_error"] if report_error else [])
    report = json.loads(Path(outcome.report_path).read_text())
    assert report["job"]["status"] == expected
    assert report["archive"]["report_complete"] is not report_error
    _clean_staging(tmp_path)


@pytest.mark.anyio
@pytest.mark.parametrize("flow", ["path", "upload"])
@pytest.mark.parametrize("unsafe", ["symlink", "dangling_symlink", "file"])
async def test_compressed_staging_parent_rejected_before_slot_or_output(tmp_path, monkeypatch, flow, unsafe):
    app = _app(tmp_path)
    source = _archive(tmp_path / "takeout.zip")
    data = source.read_bytes()
    target = tmp_path / "foreign"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("unrelated")
    staging = tmp_path / "Inbox" / "mbox-uploads"
    if unsafe == "file":
        staging.write_text("keep existing file")
    else:
        staging.symlink_to(target if unsafe == "symlink" else tmp_path / "missing")
    manager = app.state.job_manager
    monkeypatch.setattr(manager, "_reserve_mbox_slot_locked", lambda: pytest.fail("slot reserved before staging validation"))
    if flow == "path":
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
            response = await client.post("/api/mbox/jobs", json={"input_path": source.name}, headers=await csrf_headers(client))
        status, payload = response.status_code, response.json()
    else:
        body = _Body([data])
        status, _, payload = await _post(app, _headers(app, len(data), name=source.name), body)
        assert body.calls == 0
    assert status == 403
    assert payload["errors"][0]["path"] == "mbox-uploads"
    assert "must be a real folder inside the Inbox" in payload["errors"][0]["message"]
    assert not manager._jobs
    assert manager._active_mbox_jobs == 0
    assert not list((tmp_path / "Cabinet").glob("mbox-*"))
    assert sentinel.read_text() == "unrelated"
    assert source.read_bytes() == data


@pytest.mark.anyio
async def test_compressed_admission_creates_missing_staging_parent_before_worker(tmp_path):
    manager = _manager(tmp_path)
    source = _archive(tmp_path / "takeout.zip")
    assert not (tmp_path / "Inbox").exists()
    created = await manager.create_mbox_job(MboxJobCreateRequest(input_path=source.name), source_root=tmp_path)
    # No event-loop turn has started the worker yet.
    assert (tmp_path / "Inbox" / "mbox-uploads").is_dir()
    assert not Path(created.output_location.job_path).exists()
    terminal = await manager.wait_for_terminal(created.id, timeout=20)
    assert terminal.status == "succeeded"
    _clean_staging(tmp_path)


@pytest.mark.anyio
@pytest.mark.parametrize("member", ["x" * 1025, "a\x00.mbox", "a\n.mbox", "a\t.mbox", "a\x7f.mbox", "a\x85.mbox", "a\x9f.mbox"])
async def test_invalid_member_rejected_with_422_before_admission_or_upload_body(tmp_path, monkeypatch, member):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MboxJobOptions(member=member)
    app = _app(tmp_path)
    source = _archive(tmp_path / "takeout.zip")
    data = source.read_bytes()
    manager = app.state.job_manager
    monkeypatch.setattr(manager, "_reserve_mbox_slot_locked", lambda: pytest.fail("invalid member reached admission"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        response = await client.post("/api/mbox/jobs", json={"input_path": source.name, "options": {"member": member}}, headers=await csrf_headers(client))
    assert response.status_code == 422
    assert "1024 characters" in response.json()["errors"][0]["message"]
    body = _Body([data])
    status, _, payload = await _post(app, _headers(app, len(data), name=source.name), body, query="options=" + quote(json.dumps({"member": member})))
    assert status == 422
    assert "1024 characters" in payload["errors"][0]["message"]
    assert body.calls == 0
    assert not manager._jobs
    assert not (tmp_path / "Inbox" / "mbox-uploads").exists()


@pytest.mark.parametrize("member", [None, "", " ", "Takeout/ Mail.mbox", "é" * 1019 + ".mbox"])
def test_member_bounds_preserve_exact_valid_names(member):
    assert MboxJobOptions(member=member).member == member
    assert MboxJobOptions.model_json_schema()["properties"]["member"]["anyOf"][0]["maxLength"] == 1024


@pytest.mark.anyio
async def test_compressed_upload_staging_setup_error_keeps_error_envelope(tmp_path, monkeypatch):
    from dead_letter.backend import api

    app = _app(tmp_path)
    data = _archive(tmp_path / "takeout.zip").read_bytes()
    def unavailable(path):
        raise OSError("synthetic inaccessible staging parent")
    monkeypatch.setattr(api, "prepare_staging_dir", unavailable)
    body = _Body([data])
    status, _, payload = await _post(app, _headers(app, len(data), name="takeout.zip"), body)
    assert status == 500
    assert payload["errors"][0]["code"] == "backend_error"
    assert body.calls == 0
    assert app.state.job_manager._active_mbox_jobs == 0
