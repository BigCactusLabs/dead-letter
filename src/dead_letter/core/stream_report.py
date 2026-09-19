"""Disk-backed conversion reports for batches whose result count is unbounded."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from dead_letter.core.report import _sanitize_value, build_report


class StreamingReport:
    """Keep only counters in memory; spool JSON entries in a private temp file.

    The published report uses the existing schema with an optional ``mbox``
    provenance field per result. Closing without finishing discards the spool.
    A previously published report survives a failed/interrupted final write.
    """

    def __init__(self) -> None:
        self._entries = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        # One assignment commits the byte position and its counters together.
        # A signal or write error before it leaves only an uncommitted tail.
        self._checkpoint = (0, {"total": 0, "written": 0, "skipped": 0, "errors": 0})

    @property
    def total(self) -> int:
        return self._checkpoint[1]["total"]

    @property
    def written(self) -> int:
        return self._checkpoint[1]["written"]

    @property
    def skipped(self) -> int:
        return self._checkpoint[1]["skipped"]

    @property
    def errors(self) -> int:
        return self._checkpoint[1]["errors"]

    def __enter__(self) -> StreamingReport:
        return self

    def __exit__(self, *_: object) -> None:
        self._entries.close()

    def append(self, entry: dict[str, Any]) -> None:
        encoded = json.dumps(_sanitize_value(entry), ensure_ascii=False)
        summary = dict(self._checkpoint[1])
        summary["total"] += 1
        if not entry["success"]:
            summary["errors"] += 1
        elif entry["output"] is None:
            summary["skipped"] += 1
        else:
            summary["written"] += 1
        self._entries.seek(self._checkpoint[0])
        self._entries.truncate()
        self._entries.write((",\n" if self.total else "") + encoded)
        self._entries.flush()
        self._checkpoint = (self._entries.tell(), summary)

    def finish(
        self,
        directory: Path,
        *,
        options: Any,
        input_path: str,
        duration_ms: int,
        status: str | None = None,
        import_options: dict[str, Any] | None = None,
    ) -> Path:
        # Ctrl-C may have stopped an append halfway through a JSON token/comma.
        # Publish the committed prefix, never arbitrary bytes beyond it.
        self._entries.seek(self._checkpoint[0])
        self._entries.truncate()
        if status is None:
            status = (
                "completed_with_errors" if self.errors and self.errors < self.total
                else "failed" if self.errors else "succeeded"
            )
        report = build_report(
            entries=[], options=options, job_id="cli", job_status=status,
            duration_ms=duration_ms, input_path=input_path, input_mode="mbox", total=self.total,
        )
        report["summary"] = {
            "total": self.total, "written": self.written,
            "skipped": self.skipped, "errors": self.errors,
        }
        if import_options is not None:
            report["mbox_options"] = _sanitize_value(import_options)
        del report["results"]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / ".dead-letter-report.json"
        fd, temporary = tempfile.mkstemp(prefix=".dead-letter-report-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                # Remove only the closing object brace, then stream the array.
                out.write(json.dumps(report, ensure_ascii=False, indent=2)[:-1])
                out.write(',\n"results": [\n')
                self._entries.seek(0)
                shutil.copyfileobj(self._entries, out, length=64 * 1024)
                out.write("\n]}\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return target
