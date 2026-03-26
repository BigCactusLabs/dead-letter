"""Conversion report writer for dead-letter."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ReportEntry:
    """Per-file result for the conversion report."""

    source: str
    output: str | None
    success: bool
    diagnostics: dict[str, Any] | None = None
    error: dict[str, str] | None = None


def sanitize_string(value: str) -> str:
    """Remove null bytes and replace lone surrogates for safe JSON serialization."""
    value = value.replace("\x00", "")
    return value.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")


def _sanitize_value(obj: Any) -> Any:
    if isinstance(obj, str):
        return sanitize_string(obj)
    if isinstance(obj, dict):
        return {sanitize_string(k) if isinstance(k, str) else k: _sanitize_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_value(item) for item in obj]
    return obj


def build_report(
    *,
    entries: list[ReportEntry],
    options: Any,
    job_id: str,
    job_status: str,
    duration_ms: int,
    input_path: str,
    input_mode: str,
    total: int,
) -> dict[str, Any]:
    """Assemble the report structure from accumulated entries."""
    from dead_letter import __version__

    options_dict = asdict(options)
    options_dict.pop("report", None)

    written = sum(1 for e in entries if e.success and e.output is not None)
    skipped = sum(1 for e in entries if e.success and e.output is None)
    errors = sum(1 for e in entries if not e.success)

    results = []
    for entry in entries:
        item: dict[str, Any] = {
            "source": entry.source,
            "output": entry.output,
            "success": entry.success,
        }
        if entry.success and entry.diagnostics is not None:
            item["diagnostics"] = entry.diagnostics
        if not entry.success and entry.error is not None:
            item["error"] = entry.error
        results.append(item)

    report = {
        "schema_version": 1,
        "generator": {"name": "dead-letter", "version": __version__},
        "created_at": datetime.now(UTC).isoformat(),
        "job": {
            "id": job_id,
            "status": job_status,
            "duration_ms": duration_ms,
            "input_path": input_path,
            "input_mode": input_mode,
        },
        "options": options_dict,
        "summary": {
            "total": total,
            "written": written,
            "skipped": skipped,
            "errors": errors,
        },
        "results": results,
    }

    return _sanitize_value(report)


def write_report(
    report: dict[str, Any],
    cabinet_path: Path,
    *,
    filename: str = ".dead-letter-report.json",
) -> Path:
    """Atomically write report JSON to cabinet directory."""
    if Path(filename).name != filename:
        raise ValueError("report filename must be a basename")
    target = cabinet_path / filename
    fd, tmp = tempfile.mkstemp(dir=str(cabinet_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(target))
        # mkstemp creates 0o600; restore umask-derived permissions
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(str(target), 0o666 & ~umask)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target
