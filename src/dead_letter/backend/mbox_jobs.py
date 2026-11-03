"""Bounded, copy-only MBOX execution for local web jobs."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import threading
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import (
    ErrorItem,
    JobStatus,
    MboxJobOptions,
    MboxProgress,
    MboxResumableJob,
    Summary,
)
from dead_letter.core.mbox import MboxCancelled, MboxLimits
from dead_letter.core.mbox_archive import ArchiveLimits, convert_mbox_archive
from dead_letter.core.mbox_import import MboxConversion, convert_mbox
from dead_letter.core.mbox_resume import supports_bundle_resume
from dead_letter.core.stream_report import StreamingReport
from dead_letter.core.types import ConvertOptions

MBOX_INPUT_SUFFIXES = (".mbox", ".zip", ".tgz", ".tar.gz")
MAX_MBOX_ARCHIVE_BYTES = 16 * 1024 * 1024 * 1024
MAX_MBOX_FAILURES_RETURNED = 20
_STAGING_SPACE_RECHECK_BYTES = 64 * 1024 * 1024
_FATAL_RESULT_CODES = frozenset({"mbox_publish_failed", "mbox_storage_error", "mbox_disk_reserve"})
# Local, private job state beside the core resume journal in Cabinet/mbox-<id>.
MBOX_JOB_DESCRIPTOR = ".dead-letter-web-job.json"
_MAX_DESCRIPTOR_BYTES = 64 * 1024
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
_RECOVERY_STATUSES = ("new", "reused", "recovered", "retried")
# Stops that leave nothing for a rerun to distrust: the journal retries the
# record in progress. Source changes, report and journal failures do not resume.
_RESUMABLE_STOP_CODES = frozenset({"mbox_disk_reserve", "mbox_storage_error"})


def mbox_input_suffix(name: str) -> str:
    return next((suffix for suffix in MBOX_INPUT_SUFFIXES if name.lower().endswith(suffix)), "")


class MboxBusyError(RuntimeError):
    """All archive slots are reserved by queued or running jobs."""


class MboxMemberError(ValueError):
    """Member selection was requested for a resolved flat input."""


class MboxArchiveTooLargeError(ValueError):
    """The input archive exceeds the web job admission limit."""


class MboxResumeUnavailableError(RuntimeError):
    """The job is unknown, still active, finished, or stopped for a non-resumable reason."""


class MboxResumeSourceChangedError(RuntimeError):
    """The original source is missing or no longer has its admitted identity."""


class _SourceChangedError(RuntimeError):
    pass


class _DiskReserveError(RuntimeError):
    def __init__(self, message: str, *, cancel_requested: bool) -> None:
        super().__init__(message)
        self.cancel_requested = cancel_requested


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
    return _admit_resolved(source, source_root=browser.root, cabinet_root=cabinet_root)


def admit_staged_mbox_source(
    source: Path, *, staging_root: Path, cabinet_root: Path
) -> MboxAdmission:
    """Admit a server-staged upload by absolute path inside its staging folder.

    The browser-root check does not apply; every other admission check does.
    """
    root = staging_root.resolve()
    resolved = source.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Staged MBOX upload is outside the upload staging folder")
    return _admit_resolved(resolved, source_root=root, cabinet_root=cabinet_root)


def _admit_resolved(
    source: Path, *, source_root: Path, cabinet_root: Path
) -> MboxAdmission:
    if source.is_relative_to(cabinet_root):
        raise ValueError("Cabinet sources are not allowed")
    if not mbox_input_suffix(source.name):
        raise ValueError(
            "MBOX jobs accept one .mbox, .zip, .tgz or .tar.gz file"
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
        source, source_root, _source_identity(admitted), admitted.st_size
    )


class MboxJobDescriptor(BaseModel):
    """What a later process needs to re-admit and resume one web job.

    It is written beside the output, never trusted for paths: resume re-admits
    the source through the same browser/staging checks as a new job.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    # Server jobs use 32 hex digits; only those folders are listed or resumed.
    job_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    origin: Literal["manual", "upload"]
    input_path: str = Field(min_length=1, max_length=4096)
    source: str = Field(min_length=1, max_length=4096)
    identity: tuple[int, int, int, int]
    options: MboxJobOptions
    state: Literal["running", "succeeded", "completed_with_errors", "failed", "cancelled"]
    resume_supported: bool
    resumable: bool = False
    stop_code: str | None = Field(default=None, max_length=64)
    records_processed: int | None = Field(default=None, ge=0)
    runs: int = Field(default=1, ge=1)
    updated_at: datetime

    def resume_reason(self) -> Literal["cancelled", "disk_space", "interrupted"] | None:
        """Why this job may resume; a ``running`` job must be known to be inactive."""
        if not self.resume_supported:
            return None
        if self.state == "running":
            return "interrupted"
        if not self.resumable:
            return None
        return "disk_space" if self.stop_code in _RESUMABLE_STOP_CODES else "cancelled"


def valid_job_id(job_id: str) -> bool:
    return bool(_JOB_ID.match(job_id))


def resume_identity(identity: tuple[int, ...]) -> tuple[int, int, int, int]:
    """Device, inode, size and mtime: the identity a later process can re-check."""
    return identity[0], identity[1], identity[2], identity[3]


def mbox_job_folder(cabinet_root: Path, job_id: str) -> Path:
    if not valid_job_id(job_id):
        raise MboxResumeUnavailableError("Unknown MBOX job")
    return cabinet_root / f"mbox-{job_id}"


def read_job_descriptor(folder: Path) -> MboxJobDescriptor | None:
    """Return a valid descriptor from a real job folder, or ``None``."""
    try:
        if not stat.S_ISDIR(folder.lstat().st_mode) or folder.is_junction():
            return None
        path = folder / MBOX_JOB_DESCRIPTOR
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            raw = stream.read(_MAX_DESCRIPTOR_BYTES + 1)
        if len(raw) > _MAX_DESCRIPTOR_BYTES:
            return None
        descriptor = MboxJobDescriptor.model_validate_json(raw)
    except (OSError, ValueError, ValidationError):
        return None
    return descriptor if folder.name == f"mbox-{descriptor.job_id}" else None


def write_job_descriptor(folder: Path, descriptor: MboxJobDescriptor) -> None:
    """Atomically replace the descriptor; the core journal stays the source of truth."""
    encoded = descriptor.model_dump_json().encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".dead-letter-web-job-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(encoded)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, folder / MBOX_JOB_DESCRIPTOR)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _source_matches(descriptor: MboxJobDescriptor) -> bool:
    try:
        current = Path(descriptor.source).stat()
    except OSError:
        return False
    return stat.S_ISREG(current.st_mode) and resume_identity(_source_identity(current)) == descriptor.identity


def scan_resumable_jobs(
    cabinet_root: Path, *, active: set[str], limit: int = 20,
) -> list[MboxResumableJob]:
    """List stopped jobs whose source still has its admitted identity.

    ``active`` names jobs this process is running; their ``running`` state is live.
    """
    found: list[tuple[MboxJobDescriptor, str]] = []
    try:
        entries = list(os.scandir(cabinet_root))
    except OSError:
        return []
    for entry in entries:
        name = entry.name
        if not name.startswith("mbox-") or not valid_job_id(name[5:]) or name[5:] in active:
            continue
        descriptor = read_job_descriptor(cabinet_root / name)
        if descriptor is None:
            continue
        reason = descriptor.resume_reason()
        if reason is not None and _source_matches(descriptor):
            found.append((descriptor, reason))
    found.sort(key=lambda item: item[0].updated_at, reverse=True)
    return [
        MboxResumableJob(
            id=descriptor.job_id,
            origin=descriptor.origin,
            source_name=Path(descriptor.source).name,
            member=descriptor.options.member,
            reason=reason,
            # A killed run never wrote its final count; the journal holds the truth.
            records_processed=None if reason == "interrupted" else descriptor.records_processed,
            updated_at=descriptor.updated_at,
            job_path=str(cabinet_root / f"mbox-{descriptor.job_id}"),
        )
        for descriptor, reason in found[:limit]
    ]


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
        inbox_root: Path | None = None,
        origin: Literal["manual", "upload"] = "manual",
        input_path: str | None = None,
        resume_of: MboxJobDescriptor | None = None,
    ) -> None:
        self.inbox_root = inbox_root
        self.admission = admission
        self.cabinet_root = cabinet_root
        self.output = cabinet_root / f"mbox-{job_id}"
        self.job_id = job_id
        self.options = options.model_copy(deep=True)
        self.origin = origin
        self.input_path = input_path or str(admission.source)
        self.resumed = resume_of is not None
        self._runs = 1 if resume_of is None else resume_of.runs + 1
        # Decided once the output folder exists; a resumed job already proved it.
        self._resume_supported: bool | None = True if self.resumed else None
        self.resumable = False
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._cancel_open = True
        self._progress = MboxProgress(
            source_path=str(admission.source), bytes_total=admission.size, resumed=self.resumed,
        )
        self._archive_summary: dict[str, Any] = {}
        self._summary = Summary()
        self._errors: list[ErrorItem] = []
        self._fatal_codes: set[str] = set()
        self._recovery_counts = dict.fromkeys(_RECOVERY_STATUSES, 0)
        self._report_incomplete = False
        self._next_staging_check = 0

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
            self._fatal_codes.add(code)
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
        for key in ("mbox", "diagnostics", "error", "recovery"):
            value = getattr(item, key)
            if value is not None:
                entry[key] = value
        with self._lock:
            if item.recovery is not None and item.recovery.get("status") in self._recovery_counts:
                status = item.recovery["status"]
                self._recovery_counts[status] += 1
                if status in {"reused", "recovered"}:
                    self._progress.records_reused += 1
            if item.mbox is not None:
                container = item.mbox.get("container")
                if container is not None:
                    self._archive_summary = {
                        **container,
                        "container_path": str(self.admission.source),
                        "container_size": self.admission.size,
                    }
                    self._progress.bytes_total = container["staged_bytes"]
                self._progress.records_processed += 1
                self._progress.bytes_processed = min(
                    self._progress.bytes_total,
                    max(self._progress.bytes_processed, int(item.mbox["end_offset"])),
                )
            if not item.success:
                error = item.error or {}
                code = error.get("code", "conversion_error")
                fatal = item.mbox is None or code in _FATAL_RESULT_CODES
                if fatal:
                    self._fatal_codes.add(code)
                self._add_error_locked(
                    path=item.source,
                    code=code,
                    message=error.get("message", "Message conversion failed"),
                    backend=False,
                    fatal=fatal,
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

    def _check_disk(self, path: Path) -> None:
        from dead_letter.backend.uploads import MBOX_UPLOAD_DISK_RESERVE_BYTES

        try:
            free = shutil.disk_usage(path).free
        except Exception as exc:
            raise _DiskReserveError(
                "Free space could not be measured; the import stopped to protect the 1 GiB reserve",
                cancel_requested=self.cancel_event.is_set(),
            ) from exc
        if free < MBOX_UPLOAD_DISK_RESERVE_BYTES:
            raise _DiskReserveError(
                "Less than 1 GiB is free on the staging or Cabinet volume; free space before importing again",
                cancel_requested=self.cancel_event.is_set(),
            )

    def _check_staging(self, path: Path, written: int) -> None:
        if self.cancel_event.is_set():
            raise MboxCancelled()
        if written >= self._next_staging_check:
            self._check_disk(path.parent)
            self._next_staging_check = written + _STAGING_SPACE_RECHECK_BYTES

    def _convert(self, report: StreamingReport, options: ConvertOptions) -> bool:
        """Return whether an archive/report failure occurred; close before publish."""
        fatal = False
        try:
            self._check_source()
            self._check_disk(self.cabinet_root)
            with ExitStack() as stack:
                convert = convert_mbox
                archive_options: dict[str, Any] = {}
                if mbox_input_suffix(self.admission.source.name) != ".mbox":
                    from dead_letter.backend.uploads import prepare_staging_dir

                    parent = prepare_staging_dir(self.inbox_root) if self.inbox_root is not None else None
                    staging = Path(stack.enter_context(TemporaryDirectory(prefix="mbox-job-", dir=parent)))
                    self._check_disk(staging)
                    convert = convert_mbox_archive
                    archive_options = {
                        "member": self.options.member,
                        "staging_dir": staging,
                        "archive_limits": ArchiveLimits(),
                        "staging_check": self._check_staging,
                    }
                results = stack.enter_context(closing(convert(
                    self.admission.source,
                    output=self.output,
                    options=options,
                    limits=MboxLimits(),
                    bundles=True,
                    timeout_seconds=self.options.timeout_seconds,
                    cancel_requested=self.cancel_event.is_set,
                    resume=bool(self._resume_supported),
                    **archive_options,
                )))
                for item in results:
                    self._record(item, report)
                    if (
                        item.mbox is None
                        or (item.error or {}).get("code") in _FATAL_RESULT_CODES
                    ):
                        fatal = True
                        break
                    self._check_source()
                    self._check_disk(self.cabinet_root)
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
            fatal = not (isinstance(exc, _DiskReserveError) and exc.cancel_requested)
            code = (
                "mbox_disk_reserve"
                if isinstance(exc, _DiskReserveError)
                else "mbox_source_changed"
                if isinstance(exc, _SourceChangedError)
                else "mbox_report_error"
                if isinstance(exc, _ReportError)
                else "mbox_job_error"
            )
            message = (
                str(exc)
                if isinstance(exc, (_SourceChangedError, _ReportError, _DiskReserveError))
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
        return fatal or self._report_incomplete

    def _publish(
        self,
        report: StreamingReport,
        options: ConvertOptions,
        *,
        fatal: bool,
        started: float,
    ) -> MboxJobOutcome:
        from dead_letter.core._pipeline import _open_collision_safe_output

        with self._lock:
            self._progress.phase = "publishing"
        pending = self.output / ".dead-letter-report.pending.json"
        # A resumed job keeps every earlier report: the next free name is
        # reserved as an empty file and replaced only by this job's report.
        try:
            handle, target = _open_collision_safe_output(self.output / ".dead-letter-report.json")
            handle.close()
        except (OSError, RuntimeError):
            self._failure(
                "mbox_report_error",
                "Could not publish the MBOX report; partial outputs were retained",
            )
            with self._lock:
                self._cancel_open = False
            return MboxJobOutcome("failed", None)
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
                    **self._archive_summary,
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
                        **({"member": self.options.member} if self.options.member is not None else {}),
                        **(
                            {"resume": True, "recovery_counts": dict(self._recovery_counts)}
                            if self._resume_supported else {}
                        ),
                    },
                )
            except Exception:
                self._failure(
                    "mbox_report_error",
                    "Could not publish the MBOX report; partial outputs were retained",
                )
                with self._lock:
                    self._cancel_open = False
                self._remove_unfinished_report(pending, target)
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
                self._remove_unfinished_report(pending, target)
                return MboxJobOutcome("failed", None)
            return MboxJobOutcome(status, str(target))

    def _remove_unfinished_report(self, pending: Path, reserved: Path) -> None:
        try:
            pending.unlink(missing_ok=True)
            # Only the empty name reservation, never an earlier report.
            if reserved.is_file() and not reserved.is_symlink() and reserved.stat().st_size == 0:
                reserved.unlink()
        except OSError:
            self._failure(
                "mbox_report_cleanup_error",
                "An unfinished report could not be removed",
            )

    def _descriptor(self, state: str) -> MboxJobDescriptor:
        with self._lock:
            records = self._progress.records_processed
        return MboxJobDescriptor(
            job_id=self.job_id,
            origin=self.origin,
            input_path=self.input_path,
            source=str(self.admission.source),
            identity=resume_identity(self.admission.identity),
            options=self.options,
            state=state,
            resume_supported=bool(self._resume_supported),
            records_processed=records,
            runs=self._runs,
            updated_at=datetime.now(UTC),
        )

    def _finish_descriptor(self, status: JobStatus) -> None:
        """Record whether this stop can resume; failures only lose resumability."""
        with self._lock:
            codes = set(self._fatal_codes)
        if "mbox_resume_busy" in codes:
            # Another process owns this folder's journal and its descriptor.
            return
        resumable = bool(self._resume_supported) and (
            status == "cancelled" and codes <= _RESUMABLE_STOP_CODES
            or status == "failed" and bool(codes) and codes <= _RESUMABLE_STOP_CODES
        )
        descriptor = self._descriptor(status).model_copy(update={
            "resumable": resumable,
            "stop_code": min(codes & _RESUMABLE_STOP_CODES, default=None) if resumable else None,
        })
        try:
            write_job_descriptor(self.output, descriptor)
        except OSError:
            return
        self.resumable = resumable

    def run(self) -> MboxJobOutcome:
        started = monotonic()
        options = ConvertOptions(**self.options.model_dump(exclude={"timeout_seconds", "member"}))
        outcome = MboxJobOutcome("failed", None)
        described = False
        try:
            self._check_source()
            if self.cabinet_root.resolve() != self.cabinet_root:
                raise OSError("Cabinet path changed after admission")
            self.cabinet_root.mkdir(parents=True, exist_ok=True)
            if self.resumed:
                # The original folder, never a link or a replacement file.
                if not stat.S_ISDIR(self.output.lstat().st_mode) or self.output.is_junction():
                    raise OSError("MBOX job folder is not a directory")
            else:
                self.output.mkdir(exist_ok=False)
                self._resume_supported = supports_bundle_resume(self.output)
            write_job_descriptor(self.output, self._descriptor("running"))
            described = True
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
            if described:
                self._finish_descriptor(outcome.status)
            with self._lock:
                self._cancel_open = False
                self._progress.phase = "finished"
        return outcome
