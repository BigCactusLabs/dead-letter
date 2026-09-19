from __future__ import annotations

import json
from pathlib import Path

import pytest

from dead_letter.backend import cli, mbox_cli
from dead_letter.core.mbox_import import MboxConversion

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Same\n\nHello\n\n"


def report_at(root):
    return json.loads((root / ".dead-letter-report.json").read_text())


def test_cli_partial_failure_report_and_source_preservation(tmp_path):
    source = tmp_path / "mail.MBOX"
    data = POSTMARK + MESSAGE + POSTMARK + POSTMARK + MESSAGE
    source.write_bytes(data)
    root = tmp_path / "out"
    assert cli.main(["convert", str(source), "--output", str(root), "--report"]) == 1
    report = report_at(root)
    assert report["job"]["status"] == "completed_with_errors"
    assert report["summary"] == {"total": 3, "written": 2, "skipped": 0, "errors": 1}
    assert [row["mbox"]["index"] for row in report["results"]] == [1, 2, 3]
    assert report["results"][1]["error"]["code"] == "mbox_empty_message"
    assert all("dead-letter-mbox-" not in str(row) for row in report["results"])
    assert source.read_bytes() == data


def test_cli_default_output_and_bare_path(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    assert cli.main([str(source), "--report"]) == 0
    assert report_at(source.with_suffix(".markdown"))["summary"]["written"] == 1


def test_dry_run_report_is_explicit_only_output(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--dry-run"]) == 0
    assert not root.exists()
    assert cli.main([str(source), "--output", str(root), "--dry-run", "--report"]) == 0
    assert list(root.iterdir()) == [root / ".dead-letter-report.json"]
    assert report_at(root)["summary"]["skipped"] == 1


@pytest.mark.parametrize("extra", [
    ["--delete-eml"], ["--delete-eml", "--dry-run"], ["--max-message-mib", "0"],
])
def test_cli_rejects_destructive_or_invalid_options(tmp_path, extra):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    assert cli.main([str(source), *extra]) == 1
    assert source.exists()


def test_output_file_rejected(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    assert cli.main([str(source), "--output", str(tmp_path / "one.md")]) == 1
    assert not (tmp_path / "one.md").exists()


def test_missing_archive_fatal_report(tmp_path):
    root = tmp_path / "out"
    assert cli.main([str(tmp_path / "missing.mbox"), "--output", str(root), "--report"]) == 1
    assert report_at(root)["job"]["status"] == "failed"
    assert report_at(root)["results"][0]["error"]["code"] == "mbox_archive_error"


def test_bundles_and_unescape_options(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE + b">From quoted\n")
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--mbox-bundles", "--mbox-unescape", "mboxrd", "--report"]) == 0
    report = report_at(root)
    assert report["mbox_options"]["unescape"] == "mboxrd"
    assert report["mbox_options"]["bundles"] is True
    assert b"\nFrom quoted\n" in (root / report["results"][0]["output"]).with_name("source.eml").read_bytes()


def test_interruption_publishes_partial_report_and_closes_iterator(tmp_path, monkeypatch):
    closed = []

    def interrupted(*args, **kwargs):
        try:
            yield MboxConversion("mail.mbox#message-00000001", None, True, {"index": 1})
            raise KeyboardInterrupt
        finally:
            closed.append(True)

    monkeypatch.setattr(mbox_cli, "convert_mbox", interrupted)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--report"]) == 130
    report = report_at(root)
    assert report["job"]["status"] == "interrupted"
    assert len(report["results"]) == 1
    assert closed == [True]


def test_mbox_flags_are_not_silently_ignored_for_eml(tmp_path):
    source = tmp_path / "mail.eml"
    source.write_bytes(MESSAGE)
    assert cli.main([str(source), "--mbox-bundles"]) == 1
