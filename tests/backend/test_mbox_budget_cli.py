from __future__ import annotations

import json
import sys

import pytest

from dead_letter.backend import cli

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Same\n\nHello\n\n"
BUDGET_FLAGS = [
    ["--mbox-memory-mib", "512"], ["--mbox-cpu-seconds", "5"], ["--mbox-max-output-mib", "8"],
]


@pytest.mark.parametrize("flag", BUDGET_FLAGS)
def test_eml_rejects_mbox_budget_flags(tmp_path, flag):
    source = tmp_path / "mail.eml"
    source.write_bytes(MESSAGE)
    assert cli.main([str(source), "--mbox-timeout", "30", *flag]) == 1


@pytest.mark.parametrize("flag", BUDGET_FLAGS)
def test_budget_without_timeout_is_rejected_before_outputs(tmp_path, capsys, flag):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--report", *flag]) == 1
    assert "require worker mode" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.parametrize("flag", BUDGET_FLAGS)
def test_unsupported_platform_budget_is_rejected_before_outputs(tmp_path, monkeypatch, capsys, flag):
    monkeypatch.setattr(sys, "platform", "win32")
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--report", "--mbox-timeout", "30", *flag]) == 1
    err = capsys.readouterr().err
    assert "mbox_budget_unsupported" in err and "Windows" in err and flag[0] in err
    assert not root.exists()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_cli_budget_values_must_be_positive(tmp_path, value):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--mbox-timeout", "30",
                     "--mbox-cpu-seconds", value]) == 1
    assert not root.exists()


def test_default_report_records_unset_budgets(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--report"]) == 0
    options = json.loads((root / ".dead-letter-report.json").read_text(encoding="utf-8"))["mbox_options"]
    assert options["memory_limit_mib"] is None
    assert options["cpu_seconds"] is None
    assert options["max_output_mib"] is None


@pytest.mark.skipif(sys.platform not in ("linux", "darwin"),
                    reason="CPU/output worker budgets are implemented only on Linux and macOS in this slice")
def test_cli_budgets_reach_real_worker_and_report(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    argv = [str(source), "--output", str(root), "--report", "--mbox-timeout", "30",
            "--mbox-cpu-seconds", "30", "--mbox-max-output-mib", "16"]
    if sys.platform == "linux":
        argv += ["--mbox-memory-mib", "4096"]
    assert cli.main(argv) == 0
    report = json.loads((root / ".dead-letter-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["written"] == 1
    assert report["mbox_options"]["cpu_seconds"] == 30
    assert report["mbox_options"]["max_output_mib"] == 16
    assert report["mbox_options"]["memory_limit_mib"] == (4096 if sys.platform == "linux" else None)
