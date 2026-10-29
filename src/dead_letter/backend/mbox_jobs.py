"""Bounded, copy-only MBOX execution for local web jobs."""

from __future__ import annotations

import os
import stat
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import (
    ErrorItem,
    JobStatus,
    MboxJobOptions,
    MboxProgress,
    Summary,
)
from dead_letter.core.mbox import MboxCancelled, MboxLimits
from dead_letter.core.mbox_import import MboxConversion, convert_mbox
from dead_letter.core.stream_report import StreamingReport
from dead_letter.core.types import ConvertOptions

MAX_MBOX_ARCHIVE_BYTES = 16 * 1024 * 1024 * 1024
MAX_MBOX_FAILURES_RETURNED = 20
_FATAL_RESULT_CODES = frozenset({"mbox_publish_failed", "mbox_storage_error"})


class MboxBusyError(RuntimeError):
    """All archive slots are reserved by queued or running jobs."""


class MboxArchiveTooLargeError(ValueError):
    """The flat archive exceeds the web job admission limit."""


class _SourceChangedError(RuntimeError):
    pass


class _ReportError(RuntimeError):
    pass


def _source_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@dataclass(frozen=True, slots=True)
class MboxAdmission:
    source: Path
    source_root: Path
    identity: tuple[int, ...]
    size: int


def admit_mbox_source(
    input_path: str, *, source_root: Path, cabinet_root: Path
) -> MboxAdmission:
    """Resolve one browser-relative regular file without reading its contents."""
    browser = FilesystemBrowser(root=source_root)
    source = browser.resolve_relative(input_path)
    if source.is_relative_to(cabinet_root):
        raise ValueError("Cabinet sources are not allowed")
    if source.suffix.lower() != ".mbox":
        raise ValueError(
            "MBOX jobs accept one flat .mbox file; compressed archives are not supported"
        )
    try:
        admitted = source.stat()
    except OSError as exc:
        raise ValueError("MBOX input file is missing or inaccessible") from exc
    if not stat.S_ISREG(admitted.st_mode):
        raise ValueError(
            "MBOX input must be a regular file, not a directory or live mail spool"
        )
    if admitted.st_size > MAX_MBOX_ARCHIVE_BYTES:
        raise MboxArchiveTooLargeError("MBOX web jobs accept archives up to 16 GiB")
    return MboxAdmission(
        source, browser.root, _source_identity(admitted), admitted.st_size
    )


@dataclass(frozen=True, slots=True)
class MboxJobOutcome:
    status: JobStatus
    report_path: str | None


class MboxJobRunner:
    """One worker thread owns the iterator/report; status retains only a sample.

    The small lock protects snapshots and the cancellation/publication boundary.
    It is never held while reading, parsing, writing or waiting for a subprocess.
    """

    def __init__(
        self,
        *,
        admission: MboxAdmission,
        cabinet_root: Path,
        job_id: str,
        options: MboxJobOptions,
    ) -> None:
        self.admission = admission
        self.cabinet_root = cabinet_root
        self.output = cabinet_root / f"mbox-{job_id}"
        self.job_id = job_id
        self.options = options.model_copy(deep=True)
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._cancel_open = True
        self._progress = MboxProgress(
            source_path=str(admission.source), bytes_total=admission.size
        )
        self._summary = Summary()
        self._errors: list[ErrorItem] = []
        self._report_incomplete = False

    def request_cancel(self) -> bool:
        with self._lock:
            if not self._cancel_open:
                return False
            self.cancel_event.set()
            return True

    def snapshot(self) -> tuple[MboxProgress, Summary, list[ErrorItem]]:
        with self._lock:
            return (
                self._progress.model_copy(deep=True),
                self._summary.model_copy(deep=True),
                [error.model_copy(deep=True) for error in self._errors],
            )

    def fail_unexpected(self) -> None:
        """Expose infrastructure failures even if executor startup itself failed."""
        self._failure(
            "mbox_job_error",
            "Archive worker could not complete; any completed output was retained",
        )
        with self._lock:
            self._cancel_open = False
            self._progress.phase = "finished"

    def close_unstarted(self) -> None:
        """Finish a runner whose worker was cancelled before it started."""
        with self._lock:
            self._cancel_open = False
            self._progress.phase = "finished"

    def _check_source(self) -> None:
        source = self.admission.source
        try:
            unchanged = (
                source.resolve() == source
                and source.is_relative_to(self.admission.source_root)
                and _source_identity(source.stat()) == self.admission.identity
            )
        except OSError:
            unchanged = False
        if not unchanged:
            raise _SourceChangedError(
                "MBOX changed after admission; use an immutable export and rerun"
            )

    def _add_error_locked(
        self,
        *,
        path: str | None,
        code: str,
        message: str,
        backend: bool,
        fatal: bool = False,
    ) -> None:
        self._summary.errors += 1
        error = ErrorItem(
            path=None if path is None else path[:512],
            code=code[:64],
            message=message[:512],
            stage="backend" if backend else "core",
        )
        if len(self._errors) < MAX_MBOX_FAILURES_RETURNED:
            self._errors.append(error)
        elif fatal:
            # Keep a terminal reason visible after a long recoverable prefix.
            self._errors[-1] = error
        self._progress.failures_omitted = self._summary.errors - len(self._errors)

    def _failure(self, code: str, message: str) -> None:
        with self._lock:
            self._add_error_locked(
                path=str(self.admission.source),
                code=code,
                message=message,
                backend=True,
                fatal=True,
            )

    def _record(self, item: MboxConversion, report: StreamingReport) -> None:
        entry: dict[str, Any] = {
            "source": item.source,
            "output": None,
            "success": item.success,
        }
        if item.output is not None:
            entry["output"] = item.output.relative_to(self.output).as_posix()
        for key in ("mbox", "diagnostics", "error"):
            value = getattr(item, key)
            if value is not None:
                entry[key] = value
        with self._lock:
            if item.mbox is not None:
                self._progress.records_processed += 1
                self._progress.bytes_processed = min(
                    self.admission.size,
                    max(self._progress.bytes_processed, int(item.mbox["end_offset"])),
                )
            if not item.success:
                error = item.error or {}
                self._add_error_locked(
                    path=item.source,
                    code=error.get("code", "conversion_error"),
                    message=error.get("message", "Message conversion failed"),
                    backend=False,
                    fatal=item.mbox is None or error.get("code") in _FATAL_RESULT_CODES,
                )
            elif item.output is None:
                self._summary.skipped += 1
            else:
                self._summary.written += 1
        try:
            report.append(entry)
        except Exception as exc:
            self._report_incomplete = True
            raise _ReportError(
                "Could not append to the MBOX report; partial outputs were retained"
            ) from exc

    def _convert(self, report: StreamingReport, options: ConvertOptions) -> bool:
        """Return whether an archive/report failure occurred; close before publish."""
        fatal = False
        try:
            self._check_source()
            with closing(
                convert_mbox(
                    self.admission.source,
                    output=self.output,
                    options=options,
                    limits=MboxLimits(),
                    bundles=True,
                    timeout_seconds=self.options.timeout_seconds,
                    cancel_requested=self.cancel_event.is_set,
                )
            ) as results:
                for item in results:
                    self._record(item, report)
                    if (
                        item.mbox is None
                        or (item.error or {}).get("code") in _FATAL_RESULT_CODES
                    ):
                        fatal = True
                        break
                    self._check_source()
            self._check_source()
        except MboxCancelled:
            # Worker termination and generator cleanup precede this exception.
            try:
                self._check_source()
            except _SourceChangedError as exc:
                fatal = True
                self._failure("mbox_source_changed", str(exc))
                try:
                    report.append(
                        {
                            "source": self.admission.source.name,
                            "output": None,
                            "success": False,
                            "error": {
                                "code": "mbox_source_changed",
                                "message": str(exc),
                                "stage": "backend",
                            },
                        }
                    )
                except Exception:
                    self._report_incomplete = True
                    self._failure(
                        "mbox_report_error",
                        "Could not append the archive failure to the report",
                    )
        except Exception as exc:
            fatal = True
            code = (
                "mbox_source_changed"
                if isinstance(exc, _SourceChangedError)
                else "mbox_report_error"
                if isinstance(exc, _ReportError)
                else "mbox_job_error"
            )
            message = (
                str(exc)
                if isinstance(exc, (_SourceChangedError, _ReportError))
                else ("Archive conversion failed; partial outputs were retained")
            )
            self._failure(code, message)
            if not isinstance(exc, _ReportError):
                try:
                    report.append(
                        {
                            "source": self.admission.source.name,
                            "output": None,
                            "success": False,
                            "error": {
                                "code": code,
                                "message": message,
                                "stage": "backend",
                            },
                        }
                    )
                except Exception:
                    self._report_incomplete = True
                    self._failure(
                        "mbox_report_error",
                        "Could not append the archive failure to the report",
                    )
        return fatal

    def _publish(
        self,
        report: StreamingReport,
        options: ConvertOptions,
        *,
        fatal: bool,
        started: float,
    ) -> MboxJobOutcome:
        with self._lock:
            self._progress.phase = "publishing"
        pending = self.output / ".dead-letter-report.pending.json"
        target = self.output / ".dead-letter-report.json"
        while True:
            with self._lock:
                status: JobStatus = (
                    "failed"
                    if fatal
                    else "cancelled"
                    if self.cancel_event.is_set()
                    else "completed_with_errors"
                    if self._summary.errors
                    else "succeeded"
                )
                archive_summary = {
                    "records_total": None,
                    "records_processed": self._progress.records_processed,
                    "bytes_processed": self._progress.bytes_processed,
                    "observed_summary": self._summary.model_dump(),
                    "report_complete": not self._report_incomplete,
                }
            try:
                report.finish(
                    self.output,
                    filename=pending.name,
                    options=options,
                    input_path=str(self.admission.source),
                    duration_ms=int((monotonic() - started) * 1000),
                    status=status,
                    job_id=self.job_id,
                    archive_summary=archive_summary,
                    import_options={
                        "bundles": True,
                        "unescape": "preserve",
                        "source_handling": "copy",
                        "max_message_bytes": MboxLimits().max_message_bytes,
                        "max_line_bytes": MboxLimits().max_line_bytes,
                        "timeout_seconds": self.options.timeout_seconds,
                        "archive_bytes": self.admission.size,
                    },
                )
            except Exception:
                self._failure(
                    "mbox_report_error",
                    "Could not publish the MBOX report; partial outputs were retained",
                )
                with self._lock:
                    self._cancel_open = False
                try:
                    pending.unlink(missing_ok=True)
                except OSError:
                    self._failure(
                        "mbox_report_cleanup_error",
                        "An unfinished report could not be removed",
                    )
                return MboxJobOutcome("failed", None)
            with self._lock:
                if self.cancel_event.is_set() and status not in {"failed", "cancelled"}:
                    # Cancellation accepted during publication must be reflected
                    # in the durable report before polling becomes terminal.
                    continue
                self._cancel_open = False
            try:
                # No canonical success report exists while cancellation can
                # still require another report write (which itself may fail).
                pending.replace(target)
            except OSError:
                self._failure(
                    "mbox_report_error",
                    "Could not finalize the MBOX report; partial outputs were retained",
                )
                try:
                    pending.unlink(missing_ok=True)
                except OSError:
                    self._failure(
                        "mbox_report_cleanup_error",
                        "An unfinished report could not be removed",
                    )
                return MboxJobOutcome("failed", None)
            return MboxJobOutcome(status, str(target))

    def run(self) -> MboxJobOutcome:
        started = monotonic()
        options = ConvertOptions(**self.options.model_dump(exclude={"timeout_seconds"}))
        outcome = MboxJobOutcome("failed", None)
        try:
            self._check_source()
            if self.cabinet_root.resolve() != self.cabinet_root:
                raise OSError("Cabinet path changed after admission")
            self.cabinet_root.mkdir(parents=True, exist_ok=True)
            self.output.mkdir(exist_ok=False)
            with self._lock:
                self._progress.phase = "converting"
            with StreamingReport() as report:
                fatal = self._convert(report, options)
                outcome = self._publish(report, options, fatal=fatal, started=started)
        except Exception as exc:
            if isinstance(exc, _SourceChangedError):
                self._failure("mbox_source_changed", str(exc))
            else:
                self._failure(
                    "mbox_job_error",
                    "Archive job could not complete; inspect any retained partial output",
                )
            outcome = MboxJobOutcome("failed", None)
        finally:
            with self._lock:
                self._cancel_open = False
                self._progress.phase = "finished"
        return outcome
