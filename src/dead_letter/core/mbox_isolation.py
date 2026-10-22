"""Opt-in per-record process containment, not an OS security or memory sandbox.

The worker only writes in a private temporary directory. The parent publishes
validated output after a clean exit; neither mail nor MIME objects cross a pipe.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import shutil
import signal
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING, Any

from dead_letter import _mbox_worker
from dead_letter._mbox_worker import BUDGET_STATUS_FILE, EXIT_BUDGET_APPLY_FAILED, EXIT_MEMORY_LIMIT

if TYPE_CHECKING:
    from dead_letter.core.mbox import MboxRecord, UnescapeMode
    from dead_letter.core.mbox_import import MboxConversion
    from dead_letter.core.types import ConvertOptions

MAX_RECEIPT_BYTES = 1024 * 1024
_ERROR_MESSAGES = {
    "mbox_message_timeout": "Message worker exceeded its configured time budget",
    "mbox_worker_crashed": "Message worker exited abnormally; no output published",
    "mbox_worker_invalid_result": "Message worker returned an invalid or oversized result",
    "conversion_error": "Message conversion failed in worker",
    "html_markdown_failed": "HTML-to-Markdown conversion failed in worker",
    "mbox_publish_failed": "Completed worker output could not be published",
}
_RESOURCE_MESSAGES = {
    "memory": "Message worker exceeded its memory budget; no output published",
    "cpu": "Message worker exceeded its CPU-time budget; no output published",
    "output": "Message worker exceeded its per-file output budget; no output published",
}

# control -> (Python parameter, CLI flag, worker argument, unit multiplier)
_BUDGET_CONTROLS = {
    "memory": ("memory_limit_mib", "--mbox-memory-mib", "memory_mib", 1024 * 1024),
    "cpu": ("cpu_seconds", "--mbox-cpu-seconds", "cpu_seconds", 1),
    "output": ("max_output_mib", "--mbox-max-output-mib", "max_output_mib", 1024 * 1024),
}
# Decided before any conversion. macOS xnu counts RLIMIT_AS against a baseline
# virtual size of hundreds of GiB, so an absolute memory budget is unavailable
# there. Windows Jobs support committed memory and user-mode CPU, not FSIZE.
_SUPPORTED_BUDGETS = {
    "linux": {"memory", "cpu", "output"}, "darwin": {"cpu", "output"},
    "win32": {"memory", "cpu"},
}
_PLATFORM_NAMES = {"linux": "Linux", "darwin": "macOS", "win32": "Windows"}


class MboxBudgetError(ValueError):
    """A requested worker budget is unsupported or could not be applied."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class WorkerBudgets:
    """Opt-in per-worker resource limits; ``None`` leaves a control unset."""

    memory_limit_mib: int | None = None
    cpu_seconds: int | None = None
    max_output_mib: int | None = None

    def requested(self) -> list[str]:
        return [name for name, (field, *_rest) in _BUDGET_CONTROLS.items() if getattr(self, field) is not None]

    def worker_args(self) -> tuple[str, ...]:
        return tuple(
            f"{arg}={getattr(self, field)}"
            for field, _flag, arg, _unit in _BUDGET_CONTROLS.values() if getattr(self, field) is not None
        )


def validate_budgets(budgets: WorkerBudgets, *, worker_mode: bool) -> None:
    """Reject invalid, worker-less or platform-unsupported budgets before conversion."""
    requested = budgets.requested()
    for control in requested:
        field, flag, _arg, unit = _BUDGET_CONTROLS[control]
        value = getattr(budgets, field)
        maximum = 2**62
        if sys.platform == "win32":
            if control == "cpu":
                unit, maximum = 10_000_000, 2**63 - 1  # signed 100-ns ticks
            elif control == "memory":
                maximum = min(maximum, 2 * sys.maxsize + 1)  # SIZE_T bytes
        if type(value) is not int or value <= 0 or value * unit > maximum:
            raise ValueError(f"MBOX {field} ({flag}) must be a positive integer")
    if requested and not worker_mode:
        raise ValueError("MBOX resource budgets require worker mode (timeout_seconds / --mbox-timeout)")
    supported = _SUPPORTED_BUDGETS.get(sys.platform, set())
    platform = _PLATFORM_NAMES.get(sys.platform, sys.platform)
    for control in requested:
        if control not in supported:
            field, flag, _arg, _unit = _BUDGET_CONTROLS[control]
            raise MboxBudgetError(
                "mbox_budget_unsupported",
                f"the {control} budget ({field} / {flag}) is not available on {platform}",
            )


def _budget_outcome(workspace: Path, returncode: int, nonce: str) -> str | None:
    """Accept a budget exit status only with a matching control file.

    An ordinary exit (or native ``_exit``) with the same number cannot supply
    the per-launch nonce, so it remains an abnormal worker exit.
    """
    expected = {EXIT_BUDGET_APPLY_FAILED: "apply_failed", EXIT_MEMORY_LIMIT: "memory_limit"}.get(returncode)
    if expected is None:
        return None
    path = workspace / BUDGET_STATUS_FILE
    try:
        _regular(path)
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return None
        data = json.loads(raw, object_pairs_hook=_json_object)
    except (OSError, ValueError, RecursionError):
        return None
    if (
        isinstance(data, dict) and set(data) == {"nonce", "outcome"}
        and isinstance(data["nonce"], str) and data["nonce"].isascii()
        # compare_digest raises TypeError on non-ASCII str; a malformed file is a crash.
        and secrets.compare_digest(data["nonce"], nonce)
        and data["outcome"] == expected
    ):
        return expected
    return None


def _exceeded_limit(returncode: int, budgets: WorkerBudgets, outcome: str | None = None) -> str | None:
    if budgets.memory_limit_mib is not None and outcome == "memory_limit":
        return "memory"
    xcpu, xfsz = getattr(signal, "SIGXCPU", None), getattr(signal, "SIGXFSZ", None)
    if budgets.cpu_seconds is not None and xcpu is not None and returncode == -xcpu:
        return "cpu"
    if budgets.max_output_mib is not None and xfsz is not None and returncode == -xfsz:
        return "output"
    return None


def validate_timeout(value: float | None) -> None:
    if value is None:
        return
    try:
        valid = type(value) in {int, float} and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("MBOX timeout must be a finite positive number of seconds")


def _worker_command(request: Path, *budget_args: str) -> list[str]:
    # -I excludes CWD/PYTHONPATH shadowing. The package must be installed in this
    # interpreter (including uv's editable install); do not invoke a shell/uvx.
    if not budget_args:
        return [sys.executable, "-I", "-m", "dead_letter._mbox_worker", str(request)]
    # Budgeted: run the worker file as a script so importing the dead_letter
    # package (and its parsers) waits until limits are applied. -I keeps the
    # script directory off sys.path; -B disables bytecode writes.
    script = str(Path(_mbox_worker.__file__).resolve())
    return [sys.executable, "-I", "-B", script, str(request), *budget_args]


class _WorkerResourceLimit(Exception):
    """A resource outcome established by the parent, not a child's exit code."""

    def __init__(self, control: str) -> None:
        self.control = control
        super().__init__(control)


def _run_worker(request: Path, timeout: float, budget_args: tuple[str, ...] = ()) -> int:
    job = None
    if sys.platform == "win32" and budget_args:
        from dead_letter._mbox_windows import WindowsJob

        try:
            budgets, nonce = _mbox_worker._parse_budgets(list(budget_args))
            job = WindowsJob(budgets, nonce)
        except (OSError, ValueError, OverflowError) as exc:
            raise MboxBudgetError(
                "mbox_budget_apply_failed", "could not configure the message worker job; import aborted",
            ) from exc
    process = None
    try:
        # DEVNULL avoids unbounded captured logs and leaking private parser errors.
        # A new group/session leaves terminal Ctrl-C handling to the parent.
        process = subprocess.Popen(
            _worker_command(request, *budget_args), cwd=request.parent,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=os.name == "posix",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        deadline = monotonic() + timeout
        while True:
            # Windows waits use bounded integer milliseconds. Chunking avoids
            # overflow for a valid but large budget without imposing a new cap.
            remaining = max(0.0, deadline - monotonic())
            try:
                returncode = process.wait(timeout=min(remaining, 60.0))
                break
            except subprocess.TimeoutExpired:
                if monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(process.args, timeout) from None
        if job is not None:
            try:
                exceeded = job.cpu_exceeded()
            except OSError as exc:
                raise MboxBudgetError(
                    "mbox_budget_apply_failed", "could not read the message worker job; import aborted",
                ) from exc
            if exceeded:
                raise _WorkerResourceLimit("cpu")
        return returncode
    finally:
        try:
            # Reap even on cancellation or a failed job-state query. Keep the
            # parent-owned job handle until the worker can no longer publish.
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
        finally:
            if job is not None:
                try:
                    job.close()
                except OSError as exc:
                    raise MboxBudgetError(
                        "mbox_budget_apply_failed", "could not close the message worker job; import aborted",
                    ) from exc


def _regular(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Expected a regular worker artifact")


def _directory(path: Path) -> None:
    if not stat.S_ISDIR(path.lstat().st_mode) or path.is_junction():
        raise ValueError("Expected a non-linked worker directory")


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate worker receipt field")
        result[key] = value
    return result


def _json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Non-finite worker receipt number")
    return parsed


def _read_receipt(path: Path, record: MboxRecord) -> dict[str, Any]:
    _regular(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("Oversized worker receipt")
    data = json.loads(raw, object_pairs_hook=_json_object, parse_float=_json_float, parse_constant=_json_float)
    if not isinstance(data, dict) or set(data) != {
        "schema_version", "index", "sha256", "success", "diagnostics", "error_code",
    }:
        raise ValueError("Invalid worker receipt fields")
    if (
        type(data["schema_version"]) is not int or data["schema_version"] != 1
        or type(data["index"]) is not int or data["index"] != record.index
        or data["sha256"] != record.sha256 or type(data["success"]) is not bool
        or (data["diagnostics"] is not None and not isinstance(data["diagnostics"], dict))
    ):
        raise ValueError("Invalid worker receipt types or identity")
    code = data["error_code"]
    if data["success"]:
        if code is not None:
            raise ValueError("Successful worker receipt has an error")
    elif code not in ("conversion_error", "html_markdown_failed"):
        raise ValueError("Invalid worker error code")
    return data


def _publish(staged: Path, root: Path, *, bundles: bool) -> Path:
    from dead_letter.core._pipeline import _collision_safe_bundle_dir, _open_collision_safe_output

    # Never use a path supplied in a receipt. Validate the bounded-depth bundle
    # layout, including links/special files, before creating a final output.
    attachments: list[Path] = []
    if bundles:
        _directory(staged)
        if {p.name for p in staged.iterdir()} - {"message.md", "source.eml", "attachments"}:
            raise ValueError("Unexpected worker bundle member")
        for name in ("message.md", "source.eml"):
            _regular(staged / name)
        if (staged / "attachments").exists() or (staged / "attachments").is_symlink():
            _directory(staged / "attachments")
            attachments = list((staged / "attachments").iterdir())
            for attachment in attachments:
                _regular(attachment)
    else:
        _regular(staged)
    created: Path | None = None
    try:
        if bundles:
            created = _collision_safe_bundle_dir(root / staged.name)
            for name in ("message.md", "source.eml"):
                shutil.copyfile(staged / name, created / name)
            if attachments:
                (created / "attachments").mkdir()
                for attachment in attachments:
                    shutil.copyfile(attachment, created / "attachments" / attachment.name)
            return (created / "message.md").resolve()
        handle, created = _open_collision_safe_output(root / staged.name)
        with handle, staged.open("r", encoding="utf-8") as source:
            shutil.copyfileobj(source, handle, length=64 * 1024)
        return created.resolve()
    except BaseException:
        if created is not None:
            if bundles:
                shutil.rmtree(created, ignore_errors=True)
            else:
                try:
                    created.unlink(missing_ok=True)
                except OSError:
                    pass
        raise


def convert_record_isolated(
    record: MboxRecord, source: Path, root: Path, options: ConvertOptions, *,
    bundles: bool, unescape: UnescapeMode, timeout: float, budgets: WorkerBudgets | None = None,
    archive: dict[str, Any] | None = None,
) -> MboxConversion:
    from dead_letter.core.mbox_import import MboxConversion

    validate_timeout(timeout)
    locator = f"{source.name}#message-{record.index:08d}"
    provenance = {**record.provenance(source), "unescape": unescape}

    if archive is not None:
        provenance["archive"] = archive["container_basename"]
        provenance["container"] = dict(archive)

    budgets = budgets or WorkerBudgets()

    def failed(code: str, message: str | None = None) -> MboxConversion:
        return MboxConversion(locator, None, False, provenance, error={
            "code": code, "stage": "worker", "message": message or _ERROR_MESSAGES[code],
        })

    with TemporaryDirectory(prefix="dead-letter-worker-") as temporary:
        workspace = Path(temporary)
        request = workspace / "request.json"
        record_data = asdict(record)
        record_data["path"] = str(record.path)
        request.write_text(json.dumps({
            "record": record_data, "archive_name": source.name, "archive": archive,
            "options": asdict(options), "bundles": bundles, "unescape": unescape,
        }, ensure_ascii=True), encoding="utf-8")
        try:
            # Without budgets, launch exactly as before this option existed.
            nonce = secrets.token_hex(16)
            budget_args = (*budgets.worker_args(), f"nonce={nonce}") if budgets.requested() else ()
            returncode = (_run_worker(request, timeout, budget_args) if budget_args
                          else _run_worker(request, timeout))
        except subprocess.TimeoutExpired:
            return failed("mbox_message_timeout")
        except _WorkerResourceLimit as exc:
            return failed("mbox_message_resource_limit", _RESOURCE_MESSAGES[exc.control])
        # Launch failures propagate as archive errors rather than trying to start
        # an unavailable interpreter once for every remaining message. So does a
        # requested budget the worker could not apply: the guarantee is unmet.
        outcome = _budget_outcome(workspace, returncode, nonce) if budget_args else None
        if outcome == "apply_failed":
            raise MboxBudgetError(
                "mbox_budget_apply_failed",
                "a message worker could not apply the requested resource budgets; import aborted",
            )
        limit = _exceeded_limit(returncode, budgets, outcome)
        if limit is not None:
            # Withheld exactly like a timeout: the private workspace, including
            # any partial artifact, is discarded without publication.
            return failed("mbox_message_resource_limit", _RESOURCE_MESSAGES[limit])
        if returncode != 0:
            return failed("mbox_worker_crashed")
        try:
            data = _read_receipt(workspace / "receipt.json", record)
        except (OSError, ValueError, RecursionError):
            return failed("mbox_worker_invalid_result")
        if not data["success"]:
            return failed(data["error_code"])
        published = None
        if not options.dry_run:
            stem = f"{record.index:08d}-{record.sha256[:16]}"
            staged = workspace / "artifacts" / (stem if bundles else f"{stem}.md")
            try:
                _directory(workspace / "artifacts")
                published = _publish(staged, root, bundles=bundles)
            except (OSError, ValueError, RuntimeError):
                return failed("mbox_publish_failed")
        return MboxConversion(locator, published, True, provenance, data["diagnostics"])
