"""CLI recovery receipts survive report loss without duplicate Markdown."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dead_letter.backend.cli import main

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"


def archive(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(b"".join(
        POSTMARK + f"From: sender@example.test\nSubject: Example {n}\n\nBody {n}\n\n".encode()
        for n in range(3)
    ))
    return source


def test_report_can_be_added_on_rerun_without_conversion(tmp_path):
    source = archive(tmp_path)
    root = tmp_path / "out"
    args = ["convert", str(source), "--output", str(root), "--mbox-resume"]
    assert main(args) == 0
    assert not list(root.glob(".dead-letter-report*"))
    assert main([*args, "--report"]) == 0
    report = json.loads((root / ".dead-letter-report.json").read_text())
    assert report["summary"] == {"total": 3, "written": 3, "skipped": 0, "errors": 0}
    assert [row["mbox"]["index"] for row in report["results"]] == [1, 2, 3]
    assert [row["recovery"] for row in report["results"]] == [{"status": "reused", "attempt": 1}] * 3
    assert report["mbox_options"]["resume"] is True
    assert report["mbox_options"]["recovery_counts"] == {"new": 0, "reused": 3, "recovered": 0, "retried": 0}
    assert len(list(root.glob("*.md"))) == 3


def test_earlier_user_modified_report_is_not_overwritten(tmp_path):
    source = archive(tmp_path)
    root = tmp_path / "out"
    args = [str(source), "--output", str(root), "--mbox-resume", "--report"]
    assert main(args) == 0  # Bare-path compatibility.
    old = root / ".dead-letter-report.json"
    old.write_text("user notes")
    assert main(args) == 0
    assert old.read_text() == "user notes"
    report = json.loads((root / ".dead-letter-report-2.json").read_text())
    assert report["summary"]["total"] == 3
    assert len(list(root.glob("*.md"))) == 3


def test_ctrl_c_between_journal_and_report_commit_is_reconciled(tmp_path, monkeypatch):
    from dead_letter.backend import mbox_cli
    real = mbox_cli.StreamingReport

    class InterruptedReport(real):
        def append(self, entry):
            if self.total == 1:
                raise KeyboardInterrupt()
            return super().append(entry)

    source = archive(tmp_path)
    root = tmp_path / "out"
    args = ["convert", str(source), "--output", str(root), "--mbox-resume", "--report"]
    with monkeypatch.context() as patch:
        patch.setattr(mbox_cli, "StreamingReport", InterruptedReport)
        assert main(args) == 130
    partial = json.loads((root / ".dead-letter-report.json").read_text())
    assert partial["job"]["status"] == "interrupted"
    assert partial["summary"]["total"] == 1
    assert len(list(root.glob("*.md"))) == 2  # Second journal receipt survived.
    assert main(args) == 0
    complete = json.loads((root / ".dead-letter-report-2.json").read_text())
    assert complete["summary"]["total"] == 3
    assert [row["recovery"]["status"] for row in complete["results"]] == ["reused", "reused", "new"]
    assert len(list(root.glob("*.md"))) == 3


@pytest.mark.parametrize("mode", ["direct", "worker"])
def test_process_death_before_report_append_rebuilds_full_report(tmp_path, mode):
    source = archive(tmp_path)
    before = source.read_bytes()
    root = tmp_path / "out"
    args = ["convert", str(source), "--output", str(root), "--mbox-resume", "--report"]
    if mode == "worker":
        args += ["--mbox-timeout", "30"]
    script = """
import os, sys
from dead_letter.backend import mbox_cli
from dead_letter.backend.cli import main
real = mbox_cli.StreamingReport
class Crash(real):
    def append(self, entry):
        if self.total == 1: os._exit(77)
        return super().append(entry)
mbox_cli.StreamingReport = Crash
raise SystemExit(main(sys.argv[1:]))
"""
    child = subprocess.run([sys.executable, "-c", script, *args], capture_output=True, timeout=60)
    assert child.returncode == 77, child.stderr.decode()
    assert len(list(root.glob("*.md"))) == 2
    assert not list(root.glob(".dead-letter-report*"))
    assert main(args) == 0
    report = json.loads((root / ".dead-letter-report.json").read_text())
    assert report["job"]["status"] == "succeeded"
    assert [row["mbox"]["index"] for row in report["results"]] == [1, 2, 3]
    assert [row["recovery"]["status"] for row in report["results"]] == ["reused", "reused", "new"]
    assert len(list(root.glob("*.md"))) == 3
    assert source.read_bytes() == before


def test_report_write_failure_preserves_journal_and_completed_files(tmp_path, monkeypatch):
    from dead_letter.backend import mbox_cli
    source = archive(tmp_path)
    root = tmp_path / "out"
    args = ["convert", str(source), "--output", str(root), "--mbox-resume", "--report"]
    def failed(*args, **kwargs):
        raise OSError("synthetic disk error")
    with monkeypatch.context() as patch:
        patch.setattr(mbox_cli.StreamingReport, "finish", failed)
        assert main(args) == 1
    assert len(list(root.glob("*.md"))) == 3
    assert not list(root.glob(".dead-letter-report*"))
    assert main(args) == 0
    report = json.loads((root / ".dead-letter-report.json").read_text())
    assert report["mbox_options"]["recovery_counts"]["reused"] == 3


@pytest.mark.parametrize("flag", ["--mbox-bundles", "--dry-run", "--delete-eml"])
def test_unsupported_combinations_write_nothing(tmp_path, flag):
    source = archive(tmp_path)
    root = tmp_path / "out"
    assert main(["convert", str(source), "--output", str(root), "--mbox-resume", flag]) == 1
    assert not root.exists()


@pytest.mark.parametrize("name", ["message.eml", "export.zip", "export.tgz"])
def test_non_flat_input_is_not_silently_resumed(tmp_path, name):
    source = tmp_path / name
    source.write_bytes(b"synthetic input")
    root = tmp_path / "out"
    assert main(["convert", str(source), "--output", str(root), "--mbox-resume"]) == 1
    assert not root.exists()
