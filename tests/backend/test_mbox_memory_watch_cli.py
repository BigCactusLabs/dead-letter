"""CLI surface of the optional macOS soft memory watch (#220)."""
from __future__ import annotations

import json
import sys

import pytest

from dead_letter.backend import cli

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Same\n\nHello\n\n"
FLAG = ["--mbox-memory-watch-mib", "1024"]


def test_eml_rejects_memory_watch_flag(tmp_path):
    source = tmp_path / "mail.eml"
    source.write_bytes(MESSAGE)
    assert cli.main([str(source), "--mbox-timeout", "30", *FLAG]) == 1


def test_memory_watch_without_timeout_is_rejected_before_outputs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--report", *FLAG]) == 1
    assert "require worker mode" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.parametrize("platform, name", [("linux", "Linux"), ("win32", "Windows"), ("freebsd14", "freebsd14")])
def test_memory_watch_off_macos_is_rejected_before_outputs(tmp_path, monkeypatch, capsys, platform, name):
    monkeypatch.setattr(sys, "platform", platform)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--report", "--mbox-timeout", "30", *FLAG]) == 1
    err = capsys.readouterr().err
    assert "mbox_budget_unsupported" in err and FLAG[0] in err and name in err
    assert not root.exists()


def test_default_report_records_unset_memory_watch(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--report"]) == 0
    options = json.loads((root / ".dead-letter-report.json").read_text(encoding="utf-8"))["mbox_options"]
    assert options["memory_watch_mib"] is None


@pytest.mark.skipif(sys.platform != "darwin", reason="The soft memory watch reads macOS proc_pid_rusage footprints")
def test_memory_watch_reaches_real_worker_and_report(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    argv = [str(source), "--output", str(root), "--report", "--mbox-timeout", "30", *FLAG]
    assert cli.main(argv) == 0
    report = json.loads((root / ".dead-letter-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["written"] == 1
    assert report["mbox_options"]["memory_watch_mib"] == 1024
    assert report["mbox_options"]["memory_limit_mib"] is None


@pytest.mark.skipif(sys.platform != "darwin", reason="The soft memory watch reads macOS proc_pid_rusage footprints")
def test_overshoot_is_reported_and_printed(tmp_path, monkeypatch, capsys):
    from dead_letter.core import mbox_isolation as isolation

    # A fixed overshoot from the watch: the CLI must keep the output, put the
    # diagnostic in the report entry and print it, without counting a failure.
    class Overshooting(isolation.FootprintWatch):
        def overshoot(self):
            return {"code": "mbox_memory_watch_overshoot", "limit_mib": self.limit_mib, "peak_bytes": 3 * 1024**3}

    monkeypatch.setattr(isolation, "FootprintWatch", Overshooting)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--report", "--mbox-timeout", "30", *FLAG]) == 0
    report = json.loads((root / ".dead-letter-report.json").read_text(encoding="utf-8"))
    [entry] = report["results"]
    assert entry["success"] is True and entry["output"]
    assert entry["diagnostics"]["memory_watch"] == {
        "code": "mbox_memory_watch_overshoot", "limit_mib": 1024, "peak_bytes": 3 * 1024**3,
    }
    err = capsys.readouterr().err
    assert "mbox_memory_watch_overshoot" in err and "3072.0 MiB" in err and "1024 MiB" in err
