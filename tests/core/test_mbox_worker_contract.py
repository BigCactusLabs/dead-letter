from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from dead_letter.core import mbox_isolation as isolation
from dead_letter.core.mbox import MboxRecord


def record_at(tmp_path):
    raw = b"Subject: Worker contract\n\nBody\n"
    path = tmp_path / "input.eml"
    path.write_bytes(raw)
    return MboxRecord(1, 0, 0, len(raw), hashlib.sha256(raw).hexdigest(), path)


@pytest.mark.parametrize("timeout", [1e20, 1e308])
def test_large_worker_timeout_uses_platform_safe_waits(tmp_path, monkeypatch, timeout):
    waits = []
    class FinishedProcess:
        args = ["worker"]
        returncode = 0
        def wait(self, timeout=None):
            waits.append(timeout)
            if timeout is not None:
                assert 0 <= timeout <= 60
            return self.returncode
        def poll(self):
            return self.returncode
    monkeypatch.setattr(isolation.subprocess, "Popen", lambda *a, **kw: FinishedProcess())
    assert isolation._run_worker(tmp_path / "request.json", timeout) == 0
    assert waits == [60.0, None]


def test_wait_slice_expiry_is_not_the_message_deadline(tmp_path, monkeypatch):
    waits = []
    class FinishedProcess:
        args = ["worker"]
        returncode = 0
        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired(self.args, timeout)
            return self.returncode
        def poll(self):
            return self.returncode
    monkeypatch.setattr(isolation.subprocess, "Popen", lambda *a, **kw: FinishedProcess())
    # Budget starts at 10, first wait ends at 70, second ends successfully.
    clock = iter([10.0, 10.0, 70.0, 70.0])
    monkeypatch.setattr(isolation, "monotonic", lambda: next(clock))
    assert isolation._run_worker(tmp_path / "request.json", 120) == 0
    assert waits == [60.0, 60.0, None]


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_receipt_rejects_nonfinite_diagnostics(tmp_path, number):
    record = record_at(tmp_path)
    data = {"schema_version": 1, "index": 1, "sha256": record.sha256,
            "success": True, "diagnostics": {"score": "PLACEHOLDER"}, "error_code": None}
    raw = json.dumps(data).replace('"PLACEHOLDER"', number)
    path = tmp_path / "receipt.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="Non-finite"):
        isolation._read_receipt(path, record)


def test_receipt_rejects_duplicate_fields(tmp_path):
    record = record_at(tmp_path)
    data = {"schema_version": 1, "index": 1, "sha256": record.sha256,
            "success": True, "diagnostics": None, "error_code": None}
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(data)[:-1] + ', "success": false}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        isolation._read_receipt(path, record)
