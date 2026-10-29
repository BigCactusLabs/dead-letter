"""Cooperative flat-MBOX cancellation preserves sources and published results."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from dead_letter.core import mbox as framing
from dead_letter.core import mbox_isolation as isolation
from dead_letter.core.mbox import MboxCancelled, MboxLimits, MboxRecord, iter_mbox
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.types import ConvertOptions

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: sender@example.test\nSubject: Synthetic cancellation\n\nComplete body\n\n"


@pytest.mark.parametrize("unescape", ["preserve", "mboxcl2"])
def test_cancel_during_oversized_framing_drain_closes_staging(tmp_path, monkeypatch, unescape):
    source = tmp_path / "archive.mbox"
    body = b"x" * (1024 * 1024) + b"\n"
    header = b"Subject: Oversized\n"
    if unescape == "mboxcl2":
        header += f"Content-Length: {len(body)}\n".encode()
    original = POSTMARK + header + b"\n" + body + b"\n" + POSTMARK + MESSAGE
    source.write_bytes(original)
    cancelled = threading.Event()
    read_bytes = 0
    handles = []
    staging = []
    real_open = Path.open
    real_temporary = framing.TemporaryDirectory

    class ObservedReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def readline(self, size):
            nonlocal read_bytes
            value = self.handle.readline(size)
            read_bytes += len(value)
            if read_bytes >= 8192:
                cancelled.set()
            return value

    def open_observed(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        if path == source and args == ("rb",):
            handles.append(handle)
            return ObservedReader(handle)
        return handle

    def temporary(*args, **kwargs):
        result = real_temporary(*args, **kwargs)
        staging.append(Path(result.name))
        return result

    monkeypatch.setattr(Path, "open", open_observed)
    monkeypatch.setattr(framing, "TemporaryDirectory", temporary)
    with closing(iter_mbox(
        source, limits=MboxLimits(max_message_bytes=1024, max_line_bytes=1024),
        unescape=unescape, cancel_requested=cancelled.is_set,
    )) as records:
        with pytest.raises(MboxCancelled):
            next(records)
    assert 8192 <= read_bytes <= 8192 + 1025
    assert handles and all(handle.closed for handle in handles)
    assert staging and all(not path.exists() for path in staging)
    assert source.read_bytes() == original


def test_cancellation_is_not_an_archive_failure_result(tmp_path):
    source = tmp_path / "archive.mbox"
    original = (POSTMARK + MESSAGE) * 2
    source.write_bytes(original)
    out = tmp_path / "out"
    cancelled = threading.Event()
    with closing(convert_mbox(source, output=out, cancel_requested=cancelled.is_set)) as records:
        first = next(records)
        assert first.success
        cancelled.set()
        with pytest.raises(MboxCancelled):
            next(records)
    assert first.output.is_file()
    assert len(list(out.glob("*.md"))) == 1
    assert source.read_bytes() == original


def test_cancellation_before_worker_launch_creates_no_workspace(tmp_path, monkeypatch):
    source = tmp_path / "input.eml"
    source.write_bytes(MESSAGE)
    record = MboxRecord(1, 0, 0, len(MESSAGE), hashlib.sha256(MESSAGE).hexdigest(), source)

    def unexpected(*args, **kwargs):
        pytest.fail("A cancelled conversion must not create a workspace or launch a worker")

    monkeypatch.setattr(isolation, "TemporaryDirectory", unexpected)
    monkeypatch.setattr(isolation.subprocess, "Popen", unexpected)
    with pytest.raises(MboxCancelled):
        isolation.convert_record_isolated(
            record, tmp_path / "archive.mbox", tmp_path / "out", ConvertOptions(),
            bundles=False, unescape="preserve", timeout=30, cancel_requested=lambda: True,
        )
    assert source.read_bytes() == MESSAGE


def _successful_worker(request, timeout, budget_args=(), **kwargs):
    """Deterministic receipt/artifacts; real process cancellation is tested below."""
    data = json.loads(request.read_text(encoding="utf-8"))
    record = data["record"]
    stem = f"{record['index']:08d}-{record['sha256'][:16]}"
    artifacts = request.parent / "artifacts"
    artifacts.mkdir()
    if data["bundles"]:
        bundle = artifacts / stem
        bundle.mkdir()
        (bundle / "message.md").write_text("Complete worker output\n", encoding="utf-8")
        (bundle / "source.eml").write_bytes(Path(record["path"]).read_bytes())
    else:
        (artifacts / f"{stem}.md").write_text("Complete worker output\n", encoding="utf-8")
    (request.parent / "receipt.json").write_text(json.dumps({
        "schema_version": 1, "index": record["index"], "sha256": record["sha256"],
        "success": True, "diagnostics": {"state": "normal"}, "error_code": None,
    }), encoding="utf-8")
    return 0


@pytest.mark.parametrize("bundles", [False, True])
def test_cancel_after_worker_receipt_withholds_publication(tmp_path, monkeypatch, bundles):
    source = tmp_path / "archive.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    cancelled = threading.Event()
    receipts = []
    real_receipt = isolation._read_receipt

    def read_and_cancel(path, record):
        result = real_receipt(path, record)
        receipts.append(path)
        cancelled.set()
        return result

    monkeypatch.setattr(isolation, "_run_worker", _successful_worker)
    monkeypatch.setattr(isolation, "_read_receipt", read_and_cancel)
    out = tmp_path / "out"
    with closing(convert_mbox(
        source, output=out, bundles=bundles, timeout_seconds=30,
        cancel_requested=cancelled.is_set,
    )) as records:
        with pytest.raises(MboxCancelled):
            next(records)
    assert receipts and all(not path.parent.exists() for path in receipts)
    assert not out.exists()
    assert source.read_bytes() == POSTMARK + MESSAGE


@pytest.mark.parametrize("bundles", [False, True])
def test_cancellation_during_publication_yields_completed_result(tmp_path, monkeypatch, bundles):
    source = tmp_path / "archive.mbox"
    original = (POSTMARK + MESSAGE) * 2
    source.write_bytes(original)
    cancelled = threading.Event()
    real_publish = isolation._publish

    def publish_and_cancel(*args, **kwargs):
        cancelled.set()
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(isolation, "_run_worker", _successful_worker)
    monkeypatch.setattr(isolation, "_publish", publish_and_cancel)
    out = tmp_path / "out"
    with closing(convert_mbox(
        source, output=out, bundles=bundles, timeout_seconds=30,
        cancel_requested=cancelled.is_set,
    )) as records:
        first = next(records)
        assert first.success and first.mbox["index"] == 1
        assert first.output.read_text(encoding="utf-8") == "Complete worker output\n"
        with pytest.raises(MboxCancelled):
            next(records)
    assert len(list(out.glob("*/message.md" if bundles else "*.md"))) == 1
    assert source.read_bytes() == original


def test_closing_callback_enabled_iterator_removes_staged_message(tmp_path):
    source = tmp_path / "archive.mbox"
    source.write_bytes((POSTMARK + MESSAGE) * 2)
    with closing(iter_mbox(source, cancel_requested=lambda: False)) as records:
        record = next(records)
        assert record.path.is_file()
    assert not record.path.exists()


@pytest.mark.parametrize("kwargs", [{"resume": True, "cancel_requested": lambda: False},
                                  {"cancel_requested": True}])
def test_unsupported_callback_configuration_is_rejected_before_output(tmp_path, kwargs):
    source = tmp_path / "archive.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="cancellation requires|must be callable"):
        next(convert_mbox(source, output=out, **kwargs))
    assert not out.exists()


def _worker_cancellation_probe():
    """Run separately so an unresponsive worker/supervisor cannot hang pytest."""
    with TemporaryDirectory(prefix="dead-letter-cancel-probe-") as directory:
        root = Path(directory)
        source = root / "archive.mbox"
        source.write_bytes(POSTMARK + MESSAGE)
        ready = root / "worker-ready"
        processes = []
        requests = []
        real_popen = subprocess.Popen

        def command(request, *budget_args):
            requests.append(request)
            # A bounded sleep also lets the worker exit if the outer safety
            # timeout must kill this probe before its finally can run.
            program = "import sys,time; from pathlib import Path; Path(sys.argv[1]).touch(); time.sleep(5)"
            return [sys.executable, "-I", "-c", program, str(ready)]

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(isolation, "_worker_command", command), patch.object(isolation.subprocess, "Popen", spawn):
            with closing(convert_mbox(
                source, output=root / "out", timeout_seconds=10, cancel_requested=ready.exists,
            )) as records:
                try:
                    next(records)
                except MboxCancelled:
                    pass
                else:
                    raise AssertionError("Expected cooperative cancellation, not a timeout/crash result")
        assert ready.exists()
        assert processes and all(process.returncode is not None for process in processes)
        assert all(process.returncode != 0 for process in processes)  # Killed before its natural exit.
        assert requests and all(not request.parent.exists() for request in requests)
        assert not (root / "out").exists()
        assert source.read_bytes() == POSTMARK + MESSAGE
        print("worker cancelled, reaped, and staging removed")


def test_running_worker_is_cancelled_and_reaped_in_killable_process():
    result = subprocess.run(
        [sys.executable, "-c",
         "import runpy,sys; runpy.run_path(sys.argv[1])['_worker_cancellation_probe']()", str(Path(__file__))],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "worker cancelled, reaped, and staging removed" in result.stdout
