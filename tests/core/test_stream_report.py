from __future__ import annotations

import json

import pytest

from dead_letter.core import stream_report
from dead_letter.core.stream_report import StreamingReport
from dead_letter.core.types import ConvertOptions


def test_streamed_report_counts_and_unicode(tmp_path):
    with StreamingReport() as report:
        report.append({"source": "Café\x00", "output": "one.md", "success": True})
        report.append({"source": "two", "output": None, "success": False,
                       "error": {"code": "mbox_empty_message"}})
        report.append({"source": "three", "output": None, "success": True})
        target = report.finish(tmp_path, options=ConvertOptions(), input_path="mail.mbox", duration_ms=1)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["job"]["input_mode"] == "mbox"
    assert data["job"]["status"] == "completed_with_errors"
    assert data["summary"] == {"total": 3, "written": 1, "errors": 1, "skipped": 1}
    assert data["results"][0]["source"] == "Café"


def test_report_is_disk_backed_and_empty_report_valid(tmp_path):
    with StreamingReport() as report:
        assert report._entries.fileno() >= 0
        target = report.finish(tmp_path, options=ConvertOptions(), input_path="mail.mbox", duration_ms=0)
    assert json.loads(target.read_text(encoding="utf-8"))["results"] == []


def test_failed_publication_keeps_old_report_and_cleans_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / ".dead-letter-report.json"
    target.write_text("old report", encoding="utf-8")

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(stream_report.os, "replace", fail)
    with StreamingReport() as report:
        report.append({"source": "one", "output": "one.md", "success": True})
        with pytest.raises(OSError):
            report.finish(tmp_path, options=ConvertOptions(), input_path="mail.mbox", duration_ms=0)
    assert target.read_text(encoding="utf-8") == "old report"
    assert list(tmp_path.iterdir()) == [target]
