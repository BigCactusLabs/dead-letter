from __future__ import annotations

import json
from pathlib import Path

import pytest

from dead_letter.core.report import (
    ReportEntry,
    build_report,
    sanitize_string,
    write_report,
)
from dead_letter.core.types import ConvertOptions


def test_sanitize_string_clean() -> None:
    assert sanitize_string("hello world") == "hello world"


def test_sanitize_string_null_bytes() -> None:
    assert sanitize_string("hello\x00world") == "helloworld"


def test_sanitize_string_non_ascii() -> None:
    assert sanitize_string("Prüfung der Unterlagen") == "Prüfung der Unterlagen"


def test_build_report_structure() -> None:
    entries = [
        ReportEntry(
            source="test.eml",
            output="test/message.md",
            success=True,
            diagnostics={"state": "normal", "confidence": "high"},
        ),
    ]
    options = ConvertOptions(strip_signatures=True)
    report = build_report(
        entries=entries,
        options=options,
        job_id="abc",
        job_status="succeeded",
        duration_ms=500,
        input_path="/inbox",
        input_mode="file",
        total=1,
    )
    assert report["schema_version"] == 1
    assert report["generator"]["name"] == "dead-letter"
    assert report["job"]["id"] == "abc"
    assert report["summary"]["total"] == 1
    assert report["summary"]["written"] == 1
    assert len(report["results"]) == 1
    assert report["results"][0]["success"] is True
    assert report["options"]["strip_signatures"] is True
    assert "report" not in report["options"]


def test_build_report_with_failure() -> None:
    entries = [
        ReportEntry(
            source="bad.eml",
            output=None,
            success=False,
            error={"code": "mime_error", "message": "parse failed", "stage": "core"},
        ),
    ]
    report = build_report(
        entries=entries,
        options=ConvertOptions(),
        job_id="def",
        job_status="failed",
        duration_ms=100,
        input_path="/inbox/bad.eml",
        input_mode="file",
        total=1,
    )
    assert report["summary"]["errors"] == 1
    assert report["results"][0]["error"]["code"] == "mime_error"


def test_write_report_creates_file(tmp_path: Path) -> None:
    report = {"schema_version": 1, "test": True}
    path = write_report(report, tmp_path)
    assert path.exists()
    assert path.name == ".dead-letter-report.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_write_report_overwrites(tmp_path: Path) -> None:
    write_report({"version": 1}, tmp_path)
    write_report({"version": 2}, tmp_path)
    data = json.loads((tmp_path / ".dead-letter-report.json").read_text(encoding="utf-8"))
    assert data["version"] == 2


def test_write_report_supports_custom_filename(tmp_path: Path) -> None:
    path = write_report({"version": 1}, tmp_path, filename=".dead-letter-report-job-123.json")

    assert path == tmp_path / ".dead-letter-report-job-123.json"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
