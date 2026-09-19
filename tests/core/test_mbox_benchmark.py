"""Contract tests for the full-pipeline measurement/audit helper."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import mailbox
from contextlib import closing
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_mbox_import.py"
spec = importlib.util.spec_from_file_location("mbox_benchmark", SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def summary():
    return {
        "schema_version": 1, "kind": "mbox_import_measurement", "mode": "direct",
        "python": "3.12", "platform": "linux", "versions": {"dead-letter": "test"},
        "corpus": {"sha256": "a" * 64, "bytes": 100, "kind": "synthetic_mixed_v1"},
        "config": {"bundles": True},
        "counts": {"records": 1, "written": 1, "errors": 0, "archive_errors": 0,
                   "artifact_files": 2, "artifact_bytes": 100, "ranges_verified": 1,
                   "source_copies_verified": 1},
        "checksums": {"ordered_results": "b" * 64, "ordered_artifacts": "c" * 64},
        "timing": {"conversion_seconds": 1, "report_seconds": .1, "audit_seconds": .1,
                   "measured_wall_seconds": 1.2},
        "memory": {"tracemalloc_enabled": False}, "clean": True,
    }


def test_corpus_reproducible_and_readable_by_independent_mailbox(tmp_path):
    first, second = tmp_path / "one.mbox", tmp_path / "two.mbox"
    bench.write_corpus(first, 8, 2)
    bench.write_corpus(second, 8, 2)
    assert bench.hash_file(first) == bench.hash_file(second)
    assert first.stat().st_size > 4096
    with closing(mailbox.mbox(first, create=False)) as archive:
        messages = list(archive)
        assert len(messages) == 8
        assert all(m["X-Gmail-Labels"] == "Inbox,Projects/Benchmark,Important" for m in messages)
        parts = list(messages[2].walk())
        attached = [p.get_payload(decode=True) for p in parts if p.get_filename()]
        assert attached == [bytes(range(256)) * 8]
        assert "multipart/alternative" == messages[1].get_content_type()
    with pytest.raises(FileExistsError):
        bench.write_corpus(first, 1, 1)


def test_all_hash_reads_bounded():
    class Guard(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= bench.CHUNK
            return super().read(size)
    data = b"a" * (bench.CHUNK * 3 + 1)
    assert bench.hash_stream(Guard(data)) == (hashlib.sha256(data).hexdigest(), len(data))
    with pytest.raises(ValueError, match="Short"):
        bench.hash_stream(Guard(data), len(data) + 1)


def test_digest_values_are_unambiguously_delimited():
    left, right = hashlib.sha256(), hashlib.sha256()
    for value in ("ab", "c"):
        bench.add_digest(left, value)
    for value in ("a", "bc"):
        bench.add_digest(right, value)
    assert left.digest() != right.digest()


@pytest.mark.parametrize("field,value", [("index", 2), ("index", True), ("message_offset", -1),
    ("end_offset", 1000), ("stored_bytes", 0), ("sha256", "wrong"), ("envelope_offset", 2)])
def test_range_audit_rejects_corruption(field, value):
    body = b"Subject: check\n\nhello\n"
    source = io.BytesIO(bench.POSTMARK + body)
    record = {"index": 1, "envelope_offset": 0, "message_offset": len(bench.POSTMARK),
              "end_offset": len(bench.POSTMARK + body), "stored_bytes": len(body),
              "sha256": hashlib.sha256(body).hexdigest()}
    assert bench.verify_range(source, record, 1, 0) == record["sha256"]
    record[field] = value
    with pytest.raises(ValueError):
        bench.verify_range(source, record, 1, 0)


def test_bundle_audit_checks_original_source_and_all_attachment_bytes(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "message.md").write_bytes(b"Markdown")
    (bundle / "source.eml").write_bytes(b"original")
    (bundle / "attachments").mkdir()
    (bundle / "attachments" / "data.bin").write_bytes(b"\x00\xff")
    digest = hashlib.sha256()
    expected = hashlib.sha256(b"original").hexdigest()
    assert bench.artifact_digest(bundle / "message.md", tmp_path, True, digest, 1, expected) == (3, 18, 1)
    (bundle / "source.eml").write_bytes(b"modified")
    with pytest.raises(ValueError, match="source differs"):
        bench.artifact_digest(bundle / "message.md", tmp_path, True, hashlib.sha256(), 1, expected)


def test_linked_artifact_rejected(tmp_path):
    source, link = tmp_path / "original", tmp_path / "message.md"
    source.write_bytes(b"data")
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("Host lacks symlink privileges")
    with pytest.raises(ValueError):
        bench.artifact_digest(link, tmp_path, False, hashlib.sha256(), 1, None)


@pytest.mark.parametrize("key", ["corpus", "config", "versions", "python", "platform"])
def test_incompatible_runs_have_no_performance_ratio(key):
    first, second = summary(), summary()
    second[key] = "different"
    result = bench.compare(first, second)
    assert not result["compatible"] and not result["parity"]
    assert result["right_over_left_conversion_ratio"] is None


def test_equal_failures_are_not_success_or_speedup():
    first, second = summary(), summary()
    for value in (first, second):
        value["clean"] = False
        value["counts"]["errors"] = 1
    result = bench.compare(first, second)
    assert result["parity"] and not result["clean"]
    assert result["right_over_left_conversion_ratio"] is None


def test_output_mismatch_is_not_rewarded_with_ratio():
    first, second = summary(), summary()
    second["checksums"]["ordered_artifacts"] = "d" * 64
    assert not bench.compare(first, second)["parity"]
    assert bench.compare(first, second)["right_over_left_conversion_ratio"] is None


def test_tracing_and_untraced_timings_not_compared():
    first, second = summary(), summary()
    second["memory"]["tracemalloc_enabled"] = True
    result = bench.compare(first, second)
    assert result["parity"] and not result["timing_comparable"]
    assert result["right_over_left_conversion_ratio"] is None


def test_compare_valid_zero_and_positive_timings(tmp_path):
    first, second = summary(), summary()
    second["mode"] = "worker"
    second["timing"]["conversion_seconds"] = 2
    assert bench.compare(first, second)["right_over_left_conversion_ratio"] == 2
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(first), encoding="utf-8")
    assert bench.read_summary(path) == first
    first["timing"]["conversion_seconds"] = 0
    assert bench.compare(first, second)["right_over_left_conversion_ratio"] is None


@pytest.mark.parametrize("payload", [b"{}", b"[]", b"not JSON", b"{" * (bench.SUMMARY_LIMIT + 1),
    b'{"schema_version": 1, "schema_version": 1}', b'{"number": NaN}'])
def test_invalid_summary_rejected(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        bench.read_summary(path)


@pytest.mark.parametrize("path,value", [(("counts", "errors"), -1), (("counts", "records"), True),
    (("timing", "conversion_seconds"), float("inf")), (("timing", "audit_seconds"), -1),
    (("checksums", "ordered_results"), ""), (("memory", "tracemalloc_enabled"), "false")])
def test_invalid_measurement_values_rejected(tmp_path, path, value):
    data = summary()
    data[path[0]][path[1]] = value
    file = tmp_path / "bad.json"
    file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        bench.read_summary(file)


def test_false_success_flag_rejected(tmp_path):
    data = summary()
    data["counts"]["errors"] = 1
    path = tmp_path / "false.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="success"):
        bench.read_summary(path)


def test_no_rss_estimate_on_unsupported_platform(monkeypatch):
    monkeypatch.setattr(bench.sys, "platform", "win32")
    result = bench.rss_metrics()
    assert result["importer_peak_rss_bytes"] is None
    assert result["largest_reaped_worker_peak_rss_bytes"] is None


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_invalid_time_budget(value):
    with pytest.raises(bench.argparse.ArgumentTypeError):
        bench.positive_seconds(value)


def test_summary_failure_does_not_leak_paths(tmp_path, capsys):
    assert bench.main(["--compare", str(tmp_path / "PRIVATE-NAME"), "also-private"]) == 1
    value = capsys.readouterr().out
    assert "PRIVATE" not in value and "also-private" not in value
    assert json.loads(value)["error"] == "FileNotFoundError"


@pytest.mark.parametrize("bundles", [False, True])
@pytest.mark.parametrize("report", [False, True])
def test_installed_cli_direct_worker_parity(tmp_path, bundles, report):
    import subprocess
    import sys

    results = []
    work = tmp_path / "work"
    work.mkdir()
    for mode in ("direct", "worker"):
        command = [sys.executable, str(SCRIPT), "--mode", mode, "--messages", "4",
                   "--attachment-kib", "2", "--work-dir", str(work)]
        if bundles:
            command.append("--bundles")
        if not report:
            command.append("--no-report")
        proc = subprocess.run(command, capture_output=True, timeout=90)
        assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")
        result = json.loads(proc.stdout)
        assert result["clean"]
        assert result["counts"]["records"] == result["counts"]["written"] == 4
        assert result["counts"]["ranges_verified"] == 4
        assert result["counts"]["source_copies_verified"] == (4 if bundles else 0)
        assert bool(result["report_bytes"]) is report
        assert result["counts"]["artifact_files"] == (9 if bundles else 4)
        assert result["timing"]["first_result_seconds"] > 0
        assert "alice@example.test" not in proc.stdout.decode("utf-8")
        assert str(work) not in proc.stdout.decode("utf-8")
        assert list(work.iterdir()) == []
        results.append(result)
    assert bench.compare(*results)["parity"]


@pytest.mark.parametrize("mode", ["direct", "worker"])
def test_installed_local_archive_failure_is_not_hidden(tmp_path, mode):
    import subprocess
    import sys

    source = tmp_path / "PRIVATE-ARCHIVE.mbox"
    source.write_bytes(bench.POSTMARK + b"Subject: oversized\n\n" + b"x\n" * 600000
                       + bench.POSTMARK + b"Subject: valid\n\nhello\n")
    original = bench.hash_file(source)
    work = tmp_path / "work"
    work.mkdir()
    proc = subprocess.run([sys.executable, str(SCRIPT), "--archive", str(source), "--mode", mode,
                           "--max-message-mib", "1", "--work-dir", str(work)],
                          capture_output=True, timeout=90)
    assert proc.returncode == 1
    result = json.loads(proc.stdout)
    assert result["counts"]["records"] == 2
    assert result["counts"]["written"] == result["counts"]["errors"] == 1
    assert not result["clean"]
    assert result["counts"]["ranges_verified"] == 2
    assert b"PRIVATE-ARCHIVE" not in proc.stdout
    assert bench.hash_file(source) == original
    assert list(work.iterdir()) == []
