from __future__ import annotations

import json

import pytest

from dead_letter.backend import cli
from dead_letter.core import mbox_isolation as isolation

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Same\n\nHello\n\n"


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_timeout_real_worker_and_report(tmp_path, dry_run):
    source = tmp_path / "archive.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    argv = [str(source), "--output", str(root), "--mbox-timeout", "30", "--report"]
    if dry_run:
        argv.append("--dry-run")
    assert cli.main(argv) == 0
    report = json.loads((root / ".dead-letter-report.json").read_text(encoding="utf-8"))
    assert report["mbox_options"]["timeout_seconds"] == 30
    assert report["summary"]["written"] == (0 if dry_run else 1)
    assert report["summary"]["skipped"] == (1 if dry_run else 0)
    assert source.read_bytes() == POSTMARK + MESSAGE


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_cli_timeout_rejected_before_outputs(tmp_path, value):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--mbox-timeout", value]) == 1
    assert not root.exists()


def test_eml_rejects_mbox_timeout_flag(tmp_path):
    source = tmp_path / "mail.eml"
    source.write_bytes(MESSAGE)
    assert cli.main([str(source), "--mbox-timeout", "30"]) == 1


def test_default_path_does_not_start_subprocess(tmp_path, monkeypatch):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    def unexpected(*args, **kwargs):
        pytest.fail("Default conversion must remain in-process")
    monkeypatch.setattr(isolation, "convert_record_isolated", unexpected)
    assert cli.main([str(source), "--output", str(tmp_path / "out")]) == 0
