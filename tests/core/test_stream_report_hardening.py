"""Fault injection at the report spool/publication boundary."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from dead_letter.core import stream_report
from dead_letter.core.report import sanitize_string
from dead_letter.core.stream_report import StreamingReport


@dataclass
class Options:
    report: bool = True


def finish(report, root):
    target = report.finish(root, options=Options(), input_path="mail.mbox", duration_ms=1, status="interrupted")
    return json.loads(target.read_text(encoding="utf-8"))


@pytest.mark.parametrize("prefix_entries", [0, 1])
@pytest.mark.parametrize("failure", [KeyboardInterrupt, OSError])
@pytest.mark.parametrize("point", ["write", "flush", "tell"])
def test_partial_append_cannot_corrupt_published_json(tmp_path, monkeypatch, prefix_entries, failure, point):
    with StreamingReport() as report:
        if prefix_entries:
            report.append({"source": "Café", "output": "one.md", "success": True})
        spool = report._entries
        original_write = spool.write
        original_flush = spool.flush
        original_tell = spool.tell

        def fail_write(value):
            original_write(value[:max(1, len(value) // 2)])
            raise failure("injected append failure")

        def fail_flush():
            original_flush()
            raise failure("injected flush failure")

        def fail_tell():
            original_tell()
            raise failure("injected pre-commit failure")

        with monkeypatch.context() as patch:
            patch.setattr(spool, point, {"write": fail_write, "flush": fail_flush, "tell": fail_tell}[point])
            with pytest.raises(failure):
                report.append({"source": "incomplete", "output": None, "success": False})
        data = finish(report, tmp_path)
        assert data["summary"] == {"total": prefix_entries, "written": prefix_entries, "skipped": 0, "errors": 0}
        assert len(data["results"]) == prefix_entries
        assert "incomplete" not in str(data["results"])
        # A subsequent append must overwrite the uncommitted tail, too.
        report.append({"source": "next", "output": None, "success": True})
        data = finish(report, tmp_path)
        assert data["summary"]["total"] == prefix_entries + 1
        assert data["summary"]["skipped"] == 1
        assert data["results"][-1]["source"] == "next"


def test_interrupted_final_copy_leaves_previous_report_intact(tmp_path, monkeypatch):
    target = tmp_path / ".dead-letter-report.json"
    target.write_text("previous report")

    def interrupted_copy(source, output, **kwargs):
        output.write(source.read(12))
        raise KeyboardInterrupt

    monkeypatch.setattr(stream_report.shutil, "copyfileobj", interrupted_copy)
    with StreamingReport() as report:
        report.append({"source": "one", "output": "one.md", "success": True})
        with pytest.raises(KeyboardInterrupt):
            finish(report, tmp_path)
    assert target.read_text() == "previous report"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("value", ["\ud800", "\udfff", "\udc80", "\ud800\udfff", "A\x00\ud900Z"])
def test_all_lone_surrogates_are_safe_for_reports(tmp_path, value):
    clean = sanitize_string(value)
    assert "\x00" not in clean
    assert not any(0xD800 <= ord(char) <= 0xDFFF for char in clean)
    clean.encode("utf-8")
    with StreamingReport() as report:
        report.append({"source": value, "output": None, "success": False, "error": {value: value}})
        data = finish(report, tmp_path)
    assert data["results"][0]["source"] == clean


def test_valid_unicode_and_surrogateescaped_utf8_keep_existing_semantics():
    assert sanitize_string("Caf\u00e9 \U0001f600") == "Caf\u00e9 \U0001f600"
    assert sanitize_string("\udcc3\udca9") == "\u00e9"
