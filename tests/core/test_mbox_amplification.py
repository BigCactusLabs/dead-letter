"""Fast measurement contracts; the six-case resource matrix is opt-in."""
from __future__ import annotations

import importlib.util
import json
import mailbox
import os
import subprocess
import sys
from contextlib import closing
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "measure_mbox_amplification.py"
spec = importlib.util.spec_from_file_location("mbox_amplification", SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


@pytest.mark.parametrize("system,expected", [("darwin", 12345), ("linux", 12345 * 1024)])
def test_wait4_cpu_and_native_rss_units(system, expected):
    metrics = bench.usage_metrics(SimpleNamespace(ru_utime=1.25, ru_stime=0.5, ru_maxrss=12345), system)
    assert metrics == {"child_user_cpu_seconds": 1.25, "child_system_cpu_seconds": 0.5,
                       "child_cpu_seconds": 1.75, "worker_peak_rss_bytes": expected}


def test_unknown_rss_units_are_rejected():
    with pytest.raises(ValueError, match="Unsupported RSS units"):
        bench.rss_bytes(1, "win32")


def test_wait4_preserves_poll_and_reap_status():
    usage = object()
    replies = iter([(0, 0, None), (42, 256, usage)])
    calls, samples = [], []

    def wait4(pid, options):
        calls.append((pid, options))
        return next(replies)

    waitpid = bench.observe_wait4(wait4, samples)
    assert waitpid(42, 1) == (0, 0)
    assert samples == []
    assert waitpid(42, 0) == (42, 256)
    assert samples == [(42, 256, usage)]
    assert calls == [(42, 1), (42, 0)]


@pytest.mark.parametrize("case", bench.CASES)
def test_synthetic_mbox_cases_are_deterministic_bounded_and_single_record(tmp_path, case):
    one, two = tmp_path / "one.mbox", tmp_path / "two.mbox"
    bench.write_archive(one, case)
    bench.write_archive(two, case)
    assert one.read_bytes() == two.read_bytes()
    raw = bench.synthetic_message(case)
    assert one.stat().st_size == len(bench.POSTMARK) + len(raw) + 1
    assert 0 < one.stat().st_size < 6 * 1024**2
    with closing(mailbox.mbox(one, create=False)) as box:
        assert len(box) == 1
    message = BytesParser(policy=policy.default).parsebytes(raw)
    if case in {"base64-attachment", "many-attachments"}:
        attachments = [part for part in message.walk() if part.get_filename()]
        count = 1 if case == "base64-attachment" else 64
        size = 4 * 1024**2 if count == 1 else 1024
        assert len(attachments) == count
        assert all(part["Content-Transfer-Encoding"] == "base64" for part in attachments)
        assert all(part.get_payload(decode=True) == bytes(range(256)) * (size // 256)
                   for part in attachments)
    elif case == "nested-multipart":
        assert sum(part.is_multipart() for part in message.walk()) == 64
        assert list(message.walk())[-1].get_content() == "A bounded deeply nested body.\r\n"
    elif case == "large-html":
        content = message.get_content()
        assert message.get_content_type() == "text/html"
        assert content.count("<div>") == content.count("</div>") == 32
        assert content.count("<tr>") == 512
        assert content.count("&amp;") == 512 * 4 * 8
    elif case == "quoted-printable":
        assert message["Content-Transfer-Encoding"] == "quoted-printable"
        decoded = message.get_payload(decode=True)
        assert decoded == (("=é" * 24 + "\r\n") * 4096).encode()
        assert len(message.get_payload()) / len(decoded) > 2.8
    else:
        assert message.get_content() == "A short synthetic message.\r\n"


def test_output_inventory_separates_attachments_source_and_markdown(tmp_path):
    files = {"message.md": b"markdown", "source.eml": b"email",
             "attachments/data.bin": bytes(range(256)), "metadata.json": b"{}"}
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    inventory = bench.output_inventory(tmp_path)
    assert inventory["markdown_bytes"] == 8
    assert inventory["attachment_bytes"] == 256
    assert inventory["source_copy_bytes"] == 5
    assert inventory["other_bytes"] == 2
    assert inventory["total_bytes"] == bench.tree_bytes(tmp_path) == 271
    assert len(inventory["files"]) == 4
    assert all(len(item["sha256"]) == 64 for item in inventory["files"])


@pytest.mark.skipif(not hasattr(os, "wait4"), reason="wait4 unavailable")
def test_measurement_rejects_an_unobserved_reap(tmp_path, monkeypatch):
    from dead_letter.core import mbox_isolation as isolation

    source = tmp_path / "small-text.mbox"
    bench.write_archive(source, "small-text")
    monkeypatch.setattr(isolation, "_run_worker", lambda *args, **kwargs: 0)
    with pytest.raises(RuntimeError, match="Expected one observed worker reap, got 0"):
        bench.measure(source, tmp_path / "out", bundles=False)


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"} or not hasattr(os, "wait4"),
                    reason="wait4 CPU/RSS observation requires Linux/macOS")
@pytest.mark.parametrize("bundles", [False, True])
def test_actual_timed_worker_cpu_rss_and_disk_observation(tmp_path, bundles):
    source = tmp_path / "small-text.mbox"
    bench.write_archive(source, "small-text")
    args = [sys.executable, str(SCRIPT), "--trial", str(source), "--output", str(tmp_path / "out")]
    if bundles:
        args.append("--bundles")
    process = subprocess.run(args, capture_output=True, text=True, check=True, timeout=60)
    result = json.loads(process.stdout)
    assert result["worker_launches"] == 1
    assert result["worker_returncode"] == 0
    assert result["worker_peak_rss_bytes"] > 0
    assert result["child_cpu_seconds"] > 0
    assert result["child_cpu_seconds"] == (
        result["child_user_cpu_seconds"] + result["child_system_cpu_seconds"])
    assert result["wall_seconds"] >= result["worker_wall_seconds"] > 0
    assert result["temporary_checkpoint_bytes"] > result["stored_eml_bytes"]
    assert result["peak_temporary_directory_bytes"] is None
    assert result["output"]["total_bytes"] == bench.tree_bytes(tmp_path / "out")
    assert result["output"]["attachment_bytes"] == 0
    assert result["output"]["source_copy_bytes"] == (result["stored_eml_bytes"] if bundles else 0)
