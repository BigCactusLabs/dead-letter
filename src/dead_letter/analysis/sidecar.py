"""Single-message persistence. Email evidence never supplies paths or instructions."""

from __future__ import annotations

import asyncio
import errno
import json
import math
import os
import re
import stat
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dead_letter.analysis.contracts import DEFAULT_MODEL, AnalysisError, canonical_json, digest
from dead_letter.analysis.eml import PreparedEmail, prepare_eml
from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.providers.typesafe import (
    SDK_VERSION, TypeSafeConfig, emit_disclosure, preflight,
)
from dead_letter.analysis.responses import validate_response
from dead_letter.analysis.service import RESULT_SCHEMA_VERSION, _result_envelope, analyze_prepared

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
_LINK_UNSUPPORTED = {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM}
_MAX_SIDECAR_BYTES = 1_000_000


class _DiscardedResultError(AnalysisError):
    """A competing output was refused after our provider result was discarded."""

    discarded_fresh_result = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _binding(result: dict) -> dict:
    # The existing request fingerprint already binds profile/model/endpoint/state.
    # Include the explicit provenance too, so a changed field cannot pass unnoticed.
    return {
        "schema_version": result["schema_version"],
        "source_sha256": result["source"]["sha256"],
        "source_size_bytes": result["source"]["size_bytes"],
        **{key: result[key] for key in (
            "request_fingerprint", "state_sha256", "normalization_version",
            "state_builder_version", "provider", "endpoint", "requested_model",
            "adapter_version",
        )},
        "sdk_version": result.get("sdk_version"),
        "profile_sha256": result["profile"]["sha256"],
    }


def _reuse_key(result: dict) -> str:
    return digest(canonical_json(_binding(result)))


def _target_exists(path: Path) -> bool:
    try:
        if not path.parent.is_dir():
            raise AnalysisError("analysis_output_invalid")
        info = path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        raise AnalysisError("analysis_output_invalid") from None
    if not stat.S_ISREG(info.st_mode):
        raise AnalysisError("analysis_output_invalid")
    return True


def _fsync_directory(path: Path) -> None:
    # Windows does not expose directory fsync through this API.
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EBADF}:
            raise


def _probe_directory(directory: Path, name: str) -> None:
    probe = directory / f".{name}.{uuid.uuid4().hex}.tmp"
    fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.close(fd)
    finally:
        probe.unlink()


def _validate_destination(path: Path) -> bool:
    """Fail before any source read or provider preflight on unusable outputs."""
    exists = _target_exists(path)
    attempts = path.with_name(path.name + ".attempts")
    try:
        try:
            info = attempts.lstat()
        except FileNotFoundError:
            info = None
        if info is not None and not stat.S_ISDIR(info.st_mode):
            raise AnalysisError("analysis_output_invalid")
        _probe_directory(path.parent, path.name)
        if info is not None:
            _probe_directory(attempts, path.name)
    except OSError:
        raise AnalysisError("analysis_output_invalid") from None
    return exists


def _fsync_file(fd: int) -> None:
    os.fsync(fd)
    if sys.platform == "darwin":
        import fcntl
        command = getattr(fcntl, "F_FULLFSYNC", None)
        if command is not None:
            try:
                # The temp inode becomes the published file through the hard link.
                fcntl.fcntl(fd, command)
            except OSError as exc:
                if exc.errno not in {
                    errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.ENOTTY,
                }:
                    raise
                # Ordinary fsync above remains the fallback on unsupported volumes.


def _write_file(fd: int, data: bytes) -> None:
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        _fsync_file(handle.fileno())


def _publish(path: Path, result: dict) -> None:
    """Publish without replacing anything. FileExistsError is a competing winner."""
    data = (canonical_json(result) + "\n").encode("utf-8")
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        _write_file(fd, data)
        try:
            os.link(tmp, path)
        except OSError as exc:
            if exc.errno not in _LINK_UNSUPPORTED:
                raise
            # No hard links: exclusive creation still prevents clobber, but readers
            # can observe an incomplete file until close. Never treat it as reusable.
            target_fd = os.open(path, flags, 0o600)
            owned = os.fstat(target_fd)
            try:
                _write_file(target_fd, data)
            except BaseException:
                # Remove only the inode we created, never a subsequent replacement.
                try:
                    current = path.lstat()
                    if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                        path.unlink()
                except OSError:
                    pass
                raise
        tmp.unlink()
        _fsync_directory(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _shape(value: object, template: object) -> None:
    """Strict recursive metadata shape; no extra fields or bool-as-int coercion."""
    if type(value) is not type(template):
        raise ValueError("invalid field type")
    if isinstance(template, dict):
        if value.keys() != template.keys():
            raise ValueError("invalid fields")
        for key in template:
            _shape(value[key], template[key])
    elif isinstance(template, list):
        if not template and value:
            raise ValueError("unexpected list")
        for item in value:
            _shape(item, template[0])


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must be UTC")
    return parsed


def _metadata(value: object) -> None:
    if value is not None and (not isinstance(value, str) or not _ID.fullmatch(value)):
        raise ValueError("invalid metadata")


def _validate(result: dict, prepared: PreparedEmail) -> None:
    """Validate the complete safe envelope, including native answer distributions."""
    template = _result_envelope(prepared)
    dynamic = {"answers", "usage", "returned_model", "request_id", "attempts", "error_code",
               "evaluated_at", "assessment_status", "focus_identity"}
    required = set(template) | {"reuse_key", "created_at"}
    if not required <= result.keys() or result.keys() - required - {"sdk_version", "reason"}:
        raise ValueError("invalid envelope fields")
    _shape({k: result[k] for k in template if k not in dynamic},
           {k: template[k] for k in template if k not in dynamic})
    if type(result["schema_version"]) is not int or result["schema_version"] != RESULT_SCHEMA_VERSION:
        raise ValueError("unknown schema")
    if result["artifact_type"] != "message_analysis" or result["experimental"] is not True:
        raise ValueError("invalid artifact")
    if result["execution_status"] not in {"succeeded", "failed", "skipped"}:
        raise ValueError("invalid status")
    if result["billing_status"] not in {"unknown", "not_attempted"}:
        raise ValueError("invalid billing status")
    if result["assessment_status"] not in {None, "review_suggested", "insufficient_context"}:
        raise ValueError("invalid assessment")
    for key in ("returned_model", "request_id", "error_code"):
        _metadata(result[key])
    for key in ("sdk_version", "reason"):
        if key in result:
            if not isinstance(result[key], str):
                raise ValueError("invalid metadata")
            _metadata(result[key])
    for value in (result["reuse_key"], result["source"]["sha256"], result["profile"]["sha256"],
                  result["state_sha256"], result["request_fingerprint"]):
        if not isinstance(value, str) or not _HASH.fullmatch(value):
            raise ValueError("invalid hash")
    if result["source"]["size_bytes"] < 0 or result["retry_count"] < 0:
        raise ValueError("negative count")
    for key in ("started_at", "created_at"):
        _timestamp(result[key])
    if result["evaluated_at"] is not None:
        _timestamp(result["evaluated_at"])
    if result["focus_identity"] is not None:
        _shape(result["focus_identity"], {"aliases": [""]})
    attempts = result["attempts"]
    if type(attempts) is not list:
        raise ValueError("invalid attempts")
    for index, attempt in enumerate(attempts, 1):
        if type(attempt) is not dict or set(attempt) != {
            "number", "http_status", "request_id", "wire_sha256", "status", "duration_ms",
        }:
            raise ValueError("invalid attempt")
        if type(attempt["number"]) is not int or attempt["number"] != index:
            raise ValueError("invalid attempt number")
        if type(attempt["duration_ms"]) is not int or attempt["duration_ms"] < 0:
            raise ValueError("invalid duration")
        status = attempt["http_status"]
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError("invalid HTTP status")
        _metadata(attempt["request_id"])
        if not isinstance(attempt["wire_sha256"], str) or not _HASH.fullmatch(attempt["wire_sha256"]):
            raise ValueError("invalid wire hash")
        if attempt["status"] not in {"started", "response_received", "interrupted_or_failed"}:
            raise ValueError("invalid attempt status")
    if result["retry_count"] != max(0, len(attempts) - 1):
        raise ValueError("invalid retry count")
    if result["execution_status"] == "succeeded":
        if (result["billing_status"] != "unknown"
                or not any(attempt["status"] == "response_received" for attempt in attempts)):
            raise ValueError("inconsistent successful execution")
        if (result["error_code"] is not None or result["evaluated_at"] is None
                or result["assessment_status"] is None or "sdk_version" not in result):
            raise ValueError("incomplete success")
        request = replace(prepared.request, profile=get_profile(result["profile"]["name"]))
        raw = {"answers": result["answers"], "usage": result["usage"],
               "model": result["returned_model"], "request_id": result["request_id"]}
        if validate_response(request, raw) != raw:
            raise ValueError("unexpected response fields")
    elif result["answers"] != {} or result["usage"] != {}:
        raise ValueError("non-success answers")


def _reuse(path: Path, prepared: PreparedEmail, alias_max_age: float) -> dict:
    if not _target_exists(path):
        raise FileNotFoundError
    try:
        before = path.lstat()
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            after = path.lstat()
            if (not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(after.st_mode)
                    or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                    or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)):
                raise AnalysisError("analysis_output_invalid")
            raw = handle.read(_MAX_SIDECAR_BYTES + 1)
        if len(raw) > _MAX_SIDECAR_BYTES:
            raise ValueError("oversized sidecar")
        result = json.loads(raw, object_pairs_hook=_pairs)
        canonical_json(result)  # Reject non-finite values and excessive nesting.
        _validate(result, prepared)
        created = _timestamp(result["created_at"])
        age = (_now() - created).total_seconds()
        if age < -300:
            raise ValueError("future creation time")
        age = max(0.0, age)
    except FileNotFoundError:
        # The caller can re-enter the absent-output path once, before preflight.
        raise
    except AnalysisError as exc:
        if exc.code == "analysis_output_invalid":
            raise
        raise AnalysisError("analysis_output_corrupt") from None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise AnalysisError("analysis_output_corrupt") from None
    expected = _result_envelope(prepared)
    expected["sdk_version"] = SDK_VERSION
    if (result["execution_status"] != "succeeded" or result["reuse_key"] != _reuse_key(result)
            or result["reuse_key"] != _reuse_key(expected)):
        raise AnalysisError("analysis_output_mismatch")
    # Check the non-hashed provenance against the effective inputs too.
    for key in ("profile", "normalization", "coverage", "focus_identity", "request_scope",
                "reference_time_policy", "assessment_policy"):
        if result[key] != expected[key]:
            raise AnalysisError("analysis_output_mismatch")
    # Unknown returned models cannot establish that the request used an exact pin.
    if result["returned_model"] != result["requested_model"] and age > alias_max_age:
        raise AnalysisError("analysis_output_stale_alias")
    result["sidecar"] = {"path": str(path), "outcome": "reused", "created_at": result["created_at"],
                         "age_seconds": age, "returned_model": result["returned_model"],
                         "stored_source_name": result["source"]["reference"]}
    result["source"]["reference"] = prepared.snapshot.source.name
    return result


def _record_attempt(target: Path, result: dict) -> None:
    attempts = target.with_name(target.name + ".attempts")
    attempts.mkdir(mode=0o700, exist_ok=True)
    if attempts.is_symlink() or not attempts.is_dir():
        raise AnalysisError("analysis_output_invalid")
    _fsync_directory(attempts.parent)
    while True:
        attempt = attempts / f"{uuid.uuid4().hex}.json"
        try:
            _publish(attempt, result)
            break
        except FileExistsError:
            continue
    result["sidecar"] = {"path": str(attempt), "outcome": "attempt_recorded"}


async def analyze_to_sidecar(
    path: str | Path, output: str | Path, *, provider: str, allow_remote: bool = False,
    profile_name: str = "triage-v1", focus_identity: tuple[str, ...] = (),
    max_context_segments: int = 3, model: str = DEFAULT_MODEL,
    config: TypeSafeConfig | None = None, alias_max_age: float = 86400,
    on_disclosure: Callable[[dict], None] = emit_disclosure,
    _preflight=None, _provider=None,
) -> dict:
    """Reuse a validated success offline, or execute and persist one prepared email.

    Remote work still requires allow_remote=True. Existing outputs are never
    replaced. Execution failures/skips go to output + '.attempts/'. New inference
    requires preflight before source reads; reuse needs no SDK/key. A vanished
    reusable output can restart the absent-output flow once and reread the source.
    """
    if provider != "typesafe":
        raise AnalysisError("unsupported_analysis_provider")
    if type(alias_max_age) not in (int, float) or not math.isfinite(alias_max_age) or alias_max_age < 0:
        raise AnalysisError("invalid_alias_max_age")
    try:
        target = Path(output).expanduser().absolute()  # Never resolve the target symlink.
    except (RuntimeError, OSError):
        raise AnalysisError("analysis_output_invalid") from None
    effective = config or TypeSafeConfig.from_environment()
    options = dict(profile_name=profile_name, focus_identity=focus_identity,
                   max_context_segments=max_context_segments, model=model,
                   base_url=effective.base_url)
    for check in range(2):
        if not _validate_destination(target):
            (_preflight or preflight)(allow_remote=allow_remote)
            break
        prepared = prepare_eml(path, **options)
        try:
            return _reuse(target, prepared, alias_max_age)
        except FileNotFoundError:
            if check:
                raise AnalysisError("analysis_output_corrupt") from None
    prepared = prepare_eml(path, **options)
    try:
        result = await analyze_prepared(prepared, allow_remote=True, config=effective,
                                        on_disclosure=on_disclosure, _provider=_provider)
    except asyncio.CancelledError as exc:
        result = getattr(exc, "dead_letter_result", None)
        if result is not None:
            result.update(created_at=_now().isoformat(), reuse_key=_reuse_key(result))
            try:
                _record_attempt(target, result)
            except Exception:
                result["sidecar"] = {"outcome": "write_failed", "error_code": "analysis_output_write_failed"}
        raise
    except AnalysisError as exc:
        result = _result_envelope(prepared)
        result.update(execution_status="failed", assessment_status=None, error_code=exc.code)
    result.update(created_at=_now().isoformat(), reuse_key=_reuse_key(result))
    try:
        if result["execution_status"] != "succeeded":
            _record_attempt(target, result)
        else:
            try:
                _publish(target, result)
                result["sidecar"] = {"path": str(target), "outcome": "written"}
            except FileExistsError:
                try:
                    result = _reuse(target, prepared, alias_max_age)
                except AnalysisError as exc:
                    raise _DiscardedResultError(exc.code) from None
                result["sidecar"]["discarded_fresh_result"] = True
        return result
    except _DiscardedResultError:
        raise
    except Exception:
        # A paid result must survive disk/serialization failures. Do not expose
        # private exception details or turn persistence failure into inference failure.
        result["sidecar"] = {"outcome": "write_failed", "error_code": "analysis_output_write_failed"}
        return result
