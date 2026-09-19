"""Opt-in per-record process containment, not an OS security or memory sandbox.

The worker only writes in a private temporary directory. The parent publishes
validated output after a clean exit; neither mail nor MIME objects cross a pipe.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING, Any

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


def validate_timeout(value: float | None) -> None:
    if value is None:
        return
    try:
        valid = type(value) in {int, float} and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("MBOX timeout must be a finite positive number of seconds")


def _worker_command(request: Path) -> list[str]:
    # -I excludes CWD/PYTHONPATH shadowing. The package must be installed in this
    # interpreter (including uv's editable install); do not invoke a shell/uvx.
    return [sys.executable, "-I", "-m", "dead_letter._mbox_worker", str(request)]


def _run_worker(request: Path, timeout: float) -> int:
    # DEVNULL avoids unbounded captured logs and leaking private parser errors.
    # A new group/session leaves terminal Ctrl-C handling to the parent.
    process = subprocess.Popen(
        _worker_command(request), cwd=request.parent,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, start_new_session=os.name == "posix",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        deadline = monotonic() + timeout
        while True:
            # Windows waits use bounded integer milliseconds. Chunking avoids
            # overflow for a valid but large budget without imposing a new cap.
            remaining = max(0.0, deadline - monotonic())
            try:
                return process.wait(timeout=min(remaining, 60.0))
            except subprocess.TimeoutExpired:
                if monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(process.args, timeout) from None
    finally:
        # Popen's context manager alone waits forever on an uncooperative child.
        # Reap before the scanner can reuse the source EML or staging is removed.
        if process.poll() is None:
            process.kill()
        process.wait()


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
    bundles: bool, unescape: UnescapeMode, timeout: float,
) -> MboxConversion:
    from dead_letter.core.mbox_import import MboxConversion

    validate_timeout(timeout)
    locator = f"{source.name}#message-{record.index:08d}"
    provenance = {**record.provenance(source), "unescape": unescape}

    def failed(code: str) -> MboxConversion:
        return MboxConversion(locator, None, False, provenance, error={
            "code": code, "stage": "worker", "message": _ERROR_MESSAGES[code],
        })

    with TemporaryDirectory(prefix="dead-letter-worker-") as temporary:
        workspace = Path(temporary)
        request = workspace / "request.json"
        record_data = asdict(record)
        record_data["path"] = str(record.path)
        request.write_text(json.dumps({
            "record": record_data, "archive_name": source.name,
            "options": asdict(options), "bundles": bundles, "unescape": unescape,
        }, ensure_ascii=True), encoding="utf-8")
        try:
            returncode = _run_worker(request, timeout)
        except subprocess.TimeoutExpired:
            return failed("mbox_message_timeout")
        # Launch failures propagate as archive errors rather than trying to start
        # an unavailable interpreter once for every remaining message.
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
