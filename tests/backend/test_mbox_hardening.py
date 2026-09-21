from __future__ import annotations

import json

from dead_letter.backend import cli
from dead_letter.core import mbox_import, stream_report
from dead_letter.core.stream_report import StreamingReport

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: hello\n\nBody\n\n"


def test_cli_folded_storage_header_text_does_not_stop_archive(tmp_path):
    source = tmp_path / "mail.mbox"
    data = POSTMARK + b"From: alice@example.test\nSubject: hello\n Content-Length: folded text\n\nBody\n" + POSTMARK + MESSAGE
    source.write_bytes(data)
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--report"]) == 0
    report = json.loads((root / ".dead-letter-report.json").read_text())
    assert report["summary"]["written"] == 2
    assert report["summary"]["errors"] == 0
    assert source.read_bytes() == data


def test_source_mutation_keeps_prior_output_and_reports_archive_failure(tmp_path, monkeypatch):
    source = tmp_path / "mail.mbox"
    data = (POSTMARK + MESSAGE) * 3
    source.write_bytes(data)
    root = tmp_path / "out"
    original = mbox_import._convert_record

    def mutating_conversion(*args, **kwargs):
        result = original(*args, **kwargs)
        with source.open("ab") as out:
            out.write(POSTMARK + MESSAGE)
        return result

    monkeypatch.setattr(mbox_import, "_convert_record", mutating_conversion)
    assert cli.main([str(source), "--output", str(root), "--report"]) == 1
    report = json.loads((root / ".dead-letter-report.json").read_text())
    assert report["job"]["status"] == "failed"
    assert report["summary"] == {"total": 2, "written": 1, "errors": 1, "skipped": 0}
    assert len(list(root.glob("*.md"))) == 1
    assert report["results"][1]["error"]["code"] == "mbox_archive_error"
    assert "changed during import" in report["results"][1]["error"]["message"]


def test_ctrl_c_inside_report_append_publishes_valid_prefix(tmp_path, monkeypatch):
    source = tmp_path / "mail.mbox"
    source.write_bytes((POSTMARK + MESSAGE) * 3)
    root = tmp_path / "out"
    original = StreamingReport.append

    def partial_append(self, entry):
        if self.total == 1:
            self._entries.write(',\n{"source":"unfinished')
            raise KeyboardInterrupt
        original(self, entry)

    monkeypatch.setattr(StreamingReport, "append", partial_append)
    assert cli.main([str(source), "--output", str(root), "--report"]) == 130
    report = json.loads((root / ".dead-letter-report.json").read_text())
    assert report["job"]["status"] == "interrupted"
    assert report["summary"]["total"] == len(report["results"]) == 1
    assert "unfinished" not in str(report)
    # Files and receipts are not one atomic transaction: the second file
    # finished before Ctrl-C, but its incomplete receipt must not be published.
    assert len(list(root.glob("*.md"))) == 2


def test_ctrl_c_during_report_publication_returns_130_without_traceback(tmp_path, monkeypatch, capsys):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    root.mkdir()
    target = root / ".dead-letter-report.json"
    target.write_text("previous report")

    def interrupted_copy(source, output, **kwargs):
        output.write(source.read(8))
        raise KeyboardInterrupt

    monkeypatch.setattr(stream_report.shutil, "copyfileobj", interrupted_copy)
    assert cli.main([str(source), "--output", str(root), "--report"]) == 130
    assert target.read_text() == "previous report"
    assert not list(root.glob("*.tmp"))
    assert "Traceback" not in capsys.readouterr().err
