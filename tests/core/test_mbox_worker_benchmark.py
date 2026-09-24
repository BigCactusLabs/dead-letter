"""The matrix must measure actual workers and reject failed or changed output."""
from __future__ import annotations

import importlib.util
import json
import mailbox
import subprocess
import sys
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_mbox_workers.py"
sys.path.insert(0, str(SCRIPT.parent))
try:
    spec = importlib.util.spec_from_file_location("worker_benchmark", SCRIPT)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
finally:
    sys.path.pop(0)


@pytest.mark.parametrize("profile", bench.PROFILES)
def test_profiles_are_deterministic_and_bounded(tmp_path, profile):
    one, two = tmp_path / "one.mbox", tmp_path / "two.mbox"
    bench.write_corpus(one, profile, 1)
    bench.write_corpus(two, profile, 1)
    assert bench.audit.hash_file(one) == bench.audit.hash_file(two)
    with closing(mailbox.mbox(one, create=False)) as box:
        messages = list(box)
        assert len(messages) == 1
        message = messages[0]
        if profile == "attachment-heavy":
            attachment = next(part for part in message.walk() if part.get_filename())
            assert len(attachment.get_payload(decode=True)) == 8 * 1024**2
        elif profile == "large":
            assert 1024**2 < one.stat().st_size < 2 * 1024**2
        elif profile == "html-thread-heavy":
            assert any(part.get_content_type() == "text/html" for part in message.walk())
        else:
            assert one.stat().st_size < 1024


@pytest.mark.parametrize("bundles", [False, True])
def test_real_fresh_worker_parity_and_measurement(tmp_path, bundles):
    source = tmp_path / "small.mbox"
    bench.write_corpus(source, "small", 2)
    pair = {}
    for mode in ("direct", "worker"):
        args = [sys.executable, str(SCRIPT), "--trial", str(source), "--output",
                str(tmp_path / mode), "--mode", mode]
        if bundles:
            args.append("--bundles")
        result = subprocess.run(args, capture_output=True, text=True, timeout=60, check=True)
        pair[mode] = json.loads(result.stdout)
    assert bench.summarize([pair])["parity"]
    assert not bench.summarize([pair], expected_records=3)["parity"]
    changed_repeat = deepcopy(pair)
    for mode in changed_repeat:
        changed_repeat[mode]["checksums"]["ordered_artifacts"] = "0" * 64
    assert not bench.summarize([pair, changed_repeat])["parity"]
    measurement = pair["worker"]["worker_measurement"]
    assert measurement["worker_launches"] == 2
    assert measurement["worker_lifetime_seconds"] > 0
    assert measurement["parent_publication_seconds"] > 0
    assert measurement["temporary_checkpoint_max_logical_bytes"] > 0
    assert pair["direct"]["worker_measurement"]["worker_launches"] == 0
    assert pair["direct"]["worker_measurement"]["direct_record_conversion_seconds"] > 0
    for mode in pair:
        assert pair[mode]["final_disk"]["logical_bytes"] == (
            pair[mode]["counts"]["artifact_bytes"] + pair[mode]["report_bytes"])
    for mutation in ("checksums", "errors"):
        invalid = deepcopy(pair)
        if mutation == "checksums":
            invalid["worker"]["checksums"]["ordered_artifacts"] = "0" * 64
        else:
            for mode in invalid:
                invalid[mode]["counts"]["errors"] = 1
                invalid[mode]["clean"] = False
        summary = bench.summarize([invalid])
        assert not summary["parity"]
        assert summary["worker_over_direct_conversion"] is None
        assert summary["conversion_seconds"] is None


def test_darwin_rss_is_bytes_and_child_value_unknown(monkeypatch):
    resource = pytest.importorskip("resource")
    from types import SimpleNamespace
    monkeypatch.setattr(bench.audit.sys, "platform", "darwin")
    monkeypatch.setattr(resource, "getrusage", lambda who: SimpleNamespace(ru_maxrss=123456))
    result = bench.audit.rss_metrics()
    assert result["importer_peak_rss_bytes"] == 123456
    assert result["largest_reaped_worker_peak_rss_bytes"] is None


@pytest.mark.parametrize("value", ["0", "33"])
def test_matrix_rejects_unbounded_record_counts(value):
    with pytest.raises(Exception, match="1 to 32"):
        bench.bounded_messages(value)
