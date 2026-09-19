"""Process failure injection plus parity with the normal EML-backed path."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dead_letter.core import mbox_isolation as isolation
from dead_letter.core.mbox import MboxRecord
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.types import ConvertOptions, ThreadMode

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Same\n\nHello\n\n"

# Test-only programs replace a private command-builder, not a production flag,
# environment hook, or any executable instruction supplied by an email.
_FAKE_WORKER = r'''
import json, os, sys, time
from pathlib import Path
request = Path(sys.argv[1])
mode = sys.argv[2]
data = json.loads(request.read_text(encoding="utf-8"))
record = data["record"]
workspace = request.parent
stem = f"{record['index']:08d}-{record['sha256'][:16]}"
artifacts = workspace / "artifacts"
artifacts.mkdir()
(artifacts / "incomplete.tmp").write_bytes(b"private partial output")
if mode == "crash-second" and record["index"] != 2:
    mode = "ok"
if mode in ("crash", "crash-second"):
    os._exit(7)
if mode == "hang":
    while True:
        time.sleep(1)
if mode == "noisy":
    os.write(1, b"PRIVATE STDOUT" * 10000)
    os.write(2, b"PRIVATE STDERR" * 10000)
if data["bundles"]:
    bundle = artifacts / stem
    bundle.mkdir()
    (bundle / "message.md").write_text("worker output\n", encoding="utf-8")
    (bundle / "source.eml").write_bytes(Path(record["path"]).read_bytes())
    (bundle / "attachments").mkdir()
    (bundle / "attachments" / "one.bin").write_bytes(b"\x00\xff")
else:
    (artifacts / f"{stem}.md").write_text("worker output\n", encoding="utf-8")
receipt = {"schema_version": 1, "index": record["index"], "sha256": record["sha256"],
           "success": True, "diagnostics": {"state": "normal"}, "error_code": None}
if mode == "bad-json":
    (workspace / "receipt.json").write_bytes(b'{"success":')
elif mode == "oversized":
    (workspace / "receipt.json").write_bytes(b" " * (1024 * 1024 + 1))
elif mode == "missing":
    pass
else:
    (workspace / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
'''


def fake_worker(monkeypatch, mode):
    requests = []
    def command(request):
        requests.append(request)
        return [sys.executable, "-I", "-c", _FAKE_WORKER, str(request), mode]
    monkeypatch.setattr(isolation, "_worker_command", command)
    return requests


def record_at(tmp_path):
    path = tmp_path / "input.eml"
    path.write_bytes(MESSAGE)
    return MboxRecord(1, 0, len(POSTMARK), len(POSTMARK + MESSAGE), hashlib.sha256(MESSAGE).hexdigest(), path)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf"), True, "30", [], 10**400])
def test_timeout_rejects_non_positive_nonfinite_and_non_numeric(value):
    with pytest.raises(ValueError, match="finite positive"):
        isolation.validate_timeout(value)


@pytest.mark.parametrize("value", [None, 0.1, 1, 60.0])
def test_timeout_accepts_optional_positive_values(value):
    isolation.validate_timeout(value)


@pytest.mark.parametrize("mode, code", [
    ("crash", "mbox_worker_crashed"), ("hang", "mbox_message_timeout"),
    ("bad-json", "mbox_worker_invalid_result"), ("oversized", "mbox_worker_invalid_result"),
    ("missing", "mbox_worker_invalid_result"),
])
def test_bad_workers_leave_no_final_output_and_are_reaped(tmp_path, monkeypatch, mode, code):
    requests = fake_worker(monkeypatch, mode)
    processes = []
    original = subprocess.Popen
    def tracked(*args, **kwargs):
        p = original(*args, **kwargs)
        processes.append(p)
        return p
    monkeypatch.setattr(isolation.subprocess, "Popen", tracked)
    record = record_at(tmp_path)
    root = tmp_path / "out"
    result = isolation.convert_record_isolated(
        record, tmp_path / "archive.mbox", root, ConvertOptions(),
        bundles=False, unescape="preserve", timeout=0.3 if mode == "hang" else 15,
    )
    assert not result.success and result.error["code"] == code
    assert result.mbox["index"] == 1
    assert not root.exists()
    assert all(p.returncode is not None for p in processes)
    assert all(not p.parent.exists() for p in requests)
    assert record.path.read_bytes() == MESSAGE


@pytest.mark.parametrize("bundles", [False, True])
def test_publication_is_collision_safe_and_worker_noise_is_discarded(tmp_path, monkeypatch, capfd, bundles):
    requests = fake_worker(monkeypatch, "noisy")
    record = record_at(tmp_path)
    root = tmp_path / "out"
    first = isolation.convert_record_isolated(record, Path("archive.mbox"), root, ConvertOptions(),
        bundles=bundles, unescape="preserve", timeout=15)
    original = first.output.read_bytes()
    second = isolation.convert_record_isolated(record, Path("archive.mbox"), root, ConvertOptions(),
        bundles=bundles, unescape="preserve", timeout=15)
    assert first.success and second.success and first.output != second.output
    assert first.output.read_bytes() == second.output.read_bytes() == original
    assert first.mbox == second.mbox
    if bundles:
        assert (second.output.parent / "attachments" / "one.bin").read_bytes() == b"\x00\xff"
        assert (second.output.parent / "source.eml").read_bytes() == MESSAGE
    assert not list(root.rglob("*.tmp"))
    assert all(not p.parent.exists() for p in requests)
    assert "PRIVATE" not in "".join(capfd.readouterr())


def test_ctrl_c_kills_worker_before_staging_cleanup(tmp_path, monkeypatch):
    requests = fake_worker(monkeypatch, "hang")
    original = subprocess.Popen
    processes = []
    def interrupted_process(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        real_wait = process.wait
        calls = 0
        def wait(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt
            return real_wait(*args, **kwargs)
        process.wait = wait
        return process
    monkeypatch.setattr(isolation.subprocess, "Popen", interrupted_process)
    with pytest.raises(KeyboardInterrupt):
        isolation.convert_record_isolated(record_at(tmp_path), Path("archive.mbox"), tmp_path / "out",
            ConvertOptions(), bundles=False, unescape="preserve", timeout=15)
    assert len(processes) == 1 and processes[0].returncode is not None
    assert all(not p.parent.exists() for p in requests)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mutation", [
    {"schema_version": True}, {"schema_version": 2}, {"index": 999}, {"index": True},
    {"sha256": "wrong"}, {"success": "yes"}, {"diagnostics": []},
    {"error_code": "private text"}, {"success": False}, {"output": "/outside"},
])
def test_receipt_contract_rejects_malformed_identity_or_types(tmp_path, mutation):
    record = record_at(tmp_path)
    receipt = {"schema_version": 1, "index": 1, "sha256": record.sha256,
               "success": True, "diagnostics": None, "error_code": None}
    receipt.update(mutation)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError):
        isolation._read_receipt(path, record)


@pytest.mark.parametrize("bundles", [False, True])
def test_publication_failure_cleans_only_new_output(tmp_path, monkeypatch, bundles):
    fake_worker(monkeypatch, "ok")
    record = record_at(tmp_path)
    root = tmp_path / "out"
    root.mkdir()
    keep = root / "keep.md"
    keep.write_text("keep", encoding="utf-8")
    def broken(*args, **kwargs):
        raise OSError("PRIVATE ERROR")
    monkeypatch.setattr(isolation.shutil, "copyfile" if bundles else "copyfileobj", broken)
    result = isolation.convert_record_isolated(record, Path("archive.mbox"), root, ConvertOptions(),
        bundles=bundles, unescape="preserve", timeout=15)
    assert result.error["code"] == "mbox_publish_failed"
    assert "PRIVATE" not in str(result.error)
    assert list(root.iterdir()) == [keep]
    assert keep.read_text(encoding="utf-8") == "keep"


def test_linked_worker_artifact_is_not_followed(tmp_path):
    target = tmp_path / "outside.md"
    target.write_text("do not publish", encoding="utf-8")
    staged = tmp_path / "staged.md"
    try:
        staged.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation requires platform privileges")
    with pytest.raises(ValueError):
        isolation._publish(staged, tmp_path / "out", bundles=False)
    assert not (tmp_path / "out").exists()


def test_worker_crash_is_one_record_not_the_entire_archive(tmp_path, monkeypatch):
    fake_worker(monkeypatch, "crash-second")
    source = tmp_path / "mail.mbox"
    source.write_bytes((POSTMARK + MESSAGE) * 3)
    rows = list(convert_mbox(source, output=tmp_path / "out", timeout_seconds=15))
    assert [r.success for r in rows] == [True, False, True]
    assert rows[1].error["code"] == "mbox_worker_crashed"
    assert [r.mbox["index"] for r in rows] == [1, 2, 3]
    assert source.read_bytes() == (POSTMARK + MESSAGE) * 3


def test_launch_failure_is_archive_fatal_not_retried_per_record(tmp_path, monkeypatch):
    source = tmp_path / "mail.mbox"
    source.write_bytes((POSTMARK + MESSAGE) * 3)
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(1)
        raise OSError("worker cannot start")
    monkeypatch.setattr(isolation.subprocess, "Popen", unavailable)
    [row] = list(convert_mbox(source, output=tmp_path / "out", timeout_seconds=15))
    assert not row.success and row.mbox is None
    assert row.error["code"] == "mbox_archive_error"
    assert calls == [1]


def test_rejected_record_never_starts_worker(tmp_path, monkeypatch):
    from dead_letter.core.mbox import MboxLimits
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    def unexpected(*args, **kwargs):
        pytest.fail("size-rejected input must not start a process")
    monkeypatch.setattr(isolation, "convert_record_isolated", unexpected)
    [row] = list(convert_mbox(source, output=tmp_path / "out", timeout_seconds=15,
        limits=MboxLimits(max_message_bytes=1)))
    assert row.error["code"] == "mbox_message_too_large"


@pytest.mark.parametrize("bundles", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_real_worker_matches_existing_pipeline(tmp_path, bundles, dry_run):
    source = tmp_path / "archive.mbox"
    original = (Path(__file__).parent / "fixtures" / "takeout-synthetic.mbox").read_bytes()
    source.write_bytes(original)
    options = ConvertOptions(dry_run=dry_run, thread_mode=ThreadMode.STRUCTURED)
    direct_root, worker_root = tmp_path / "direct", tmp_path / "worker"
    direct = list(convert_mbox(source, output=direct_root, options=options, bundles=bundles))
    isolated = list(convert_mbox(source, output=worker_root, options=options, bundles=bundles,
        timeout_seconds=30))
    assert [r.success for r in isolated] == [r.success for r in direct]
    assert [r.mbox for r in isolated] == [r.mbox for r in direct]
    assert [r.diagnostics for r in isolated] == [r.diagnostics for r in direct]
    if dry_run:
        assert not direct_root.exists() and not worker_root.exists()
    else:
        def contents(root):
            return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        assert contents(worker_root) == contents(direct_root)
    assert source.read_bytes() == original
