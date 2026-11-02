"""Bounded raw-body staging for browser MBOX uploads."""

from __future__ import annotations

import asyncio
import errno
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from io import BufferedWriter
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dead_letter.backend import mbox_jobs
from dead_letter.backend.schemas import MboxJobOptions

MBOX_UPLOAD_STAGING_DIRNAME = "mbox-uploads"
MBOX_UPLOAD_FILENAME_HEADER = "x-dead-letter-filename"
MBOX_UPLOAD_LENGTH_HEADER = "x-dead-letter-upload-length"
# Free space that must remain after the declared upload is staged. The job that
# follows needs room for its report spool and first bundles, and the host needs
# room to keep working. Free space cannot be reserved portably, so the check runs
# before the body and again while it arrives; it is not a quota.
MBOX_UPLOAD_DISK_RESERVE_BYTES = 1024 * 1024 * 1024
# Longest wait for the next body chunk before the upload is abandoned with 408.
MBOX_UPLOAD_IDLE_TIMEOUT_SECONDS = 60.0
# Room above the archive cap for the route's body-limit backstop, so the
# endpoint's own checks answer near-cap requests with the error envelope.
MBOX_UPLOAD_BODY_HEADROOM_BYTES = 1024 * 1024

_WRITE_BATCH_BYTES = 1024 * 1024
_SPACE_RECHECK_BYTES = 256 * 1024 * 1024
# Longest length header accepted, checked before int(). 20 digits holds any
# 64-bit byte count; longer values are malformed, not merely too large.
_MAX_LENGTH_DIGITS = 20
_MAX_FILENAME_HEADER_CHARS = 4096
_MAX_STEM_BYTES = 200
_MAX_COLLISION_INDEX = 10_000
_UNSAFE_NAME_CHARS = frozenset('<>:"|?*')
_NO_SPACE_ERRNOS = frozenset(
    code for code in (getattr(errno, "ENOSPC", None), getattr(errno, "EDQUOT", None)) if code is not None
)


class UploadRejected(Exception):
    """An upload failure with its HTTP status and stable error code."""

    def __init__(self, status_code: int, *, code: str, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.path = path


@dataclass(frozen=True, slots=True)
class MboxUploadRequest:
    filename: str
    stem: str
    length: int
    options: MboxJobOptions


def parse_mbox_upload_request(request: Request) -> MboxUploadRequest:
    """Validate everything about an upload that is known before its body."""
    headers = request.headers
    length = _parse_length(headers)
    filename, stem = _parse_filename(headers.get(MBOX_UPLOAD_FILENAME_HEADER))
    options = _parse_options(request)
    media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/octet-stream":
        raise UploadRejected(
            415,
            code="mbox_upload_unsupported_media_type",
            message="Send the archive bytes as application/octet-stream",
            path="Content-Type",
        )
    return MboxUploadRequest(filename=filename, stem=stem, length=length, options=options)


def _parse_length(headers: Headers) -> int:
    content_length = headers.get("content-length")
    if "transfer-encoding" in headers or content_length is None:
        raise UploadRejected(
            411,
            code="mbox_upload_length_required",
            message="MBOX uploads need a Content-Length; chunked bodies are not accepted",
            path="Content-Length",
        )
    declared = headers.get(MBOX_UPLOAD_LENGTH_HEADER)
    if not _is_decimal(content_length) or declared is None or not _is_decimal(declared):
        raise UploadRejected(
            400,
            code="mbox_upload_length_mismatch",
            message="Content-Length and X-Dead-Letter-Upload-Length must both be byte counts "
            f"of at most {_MAX_LENGTH_DIGITS} digits",
            path="X-Dead-Letter-Upload-Length",
        )
    length = int(content_length)
    if int(declared) != length:
        raise UploadRejected(
            400,
            code="mbox_upload_length_mismatch",
            message="Content-Length does not match X-Dead-Letter-Upload-Length",
            path="X-Dead-Letter-Upload-Length",
        )
    if length > mbox_jobs.MAX_MBOX_ARCHIVE_BYTES:
        raise UploadRejected(
            413,
            code="mbox_too_large",
            message="MBOX web jobs accept archives up to 16 GiB",
            path="Content-Length",
        )
    return length


def _is_decimal(value: str) -> bool:
    return value.isascii() and value.isdigit() and len(value) <= _MAX_LENGTH_DIGITS


def _parse_filename(raw: str | None) -> tuple[str, str]:
    """Decode the percent-encoded UTF-8 name and reduce it to a safe stem."""
    invalid = UploadRejected(
        400,
        code="invalid_request",
        message="X-Dead-Letter-Filename must be a percent-encoded UTF-8 .mbox file name",
        path="X-Dead-Letter-Filename",
    )
    if not raw or len(raw) > _MAX_FILENAME_HEADER_CHARS or not raw.isascii():
        raise invalid
    try:
        decoded = unquote(raw, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise invalid from exc
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded):
        raise invalid
    name = decoded.replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(".mbox"):
        raise UploadRejected(
            400,
            code="invalid_request",
            message="MBOX uploads accept one flat .mbox file; compressed archives are not supported",
            path=name or "X-Dead-Letter-Filename",
        )
    stem = "".join("_" if char in _UNSAFE_NAME_CHARS else char for char in name[: -len(".mbox")])
    stem = stem.encode("utf-8")[:_MAX_STEM_BYTES].decode("utf-8", errors="ignore")
    stem = stem.lstrip(". ").rstrip(" ") or "upload"
    return name, stem


def _parse_options(request: Request) -> MboxJobOptions:
    items = request.query_params.multi_items()
    unknown = sorted({key for key, _ in items if key != "options"})
    if unknown:
        raise UploadRejected(
            400,
            code="invalid_request",
            message=f"unknown query parameter: {unknown[0]}",
            path=unknown[0],
        )
    values = [value for _, value in items]
    if len(values) > 1:
        raise UploadRejected(
            400, code="invalid_request", message="options may be given only once", path="options",
        )
    try:
        return MboxJobOptions.model_validate_json(values[0] if values else "{}")
    except ValidationError as exc:
        raise UploadRejected(400, code="invalid_request", message=str(exc), path="options") from exc


def prepare_staging_dir(inbox_root: Path) -> Path:
    """Create or reuse ``<Inbox>/mbox-uploads`` and confirm it stays in the Inbox.

    A symlink, a non-directory, or a directory that resolves anywhere else is
    rejected before any upload byte is written. Callers stage under the
    returned path only and never resolve it again.
    """
    inbox = inbox_root.resolve()
    staging = inbox / MBOX_UPLOAD_STAGING_DIRNAME
    unsafe = UploadRejected(
        403,
        code="invalid_request",
        message="The Inbox's mbox-uploads entry must be a real folder inside the Inbox",
        path=MBOX_UPLOAD_STAGING_DIRNAME,
    )
    try:
        with _storage_errors():
            staging.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise unsafe from exc
    if staging.is_symlink() or staging.resolve() != staging:
        raise unsafe
    return staging


@contextmanager
def _storage_errors() -> Iterator[None]:
    """Report a full disk or exhausted quota as 507 rather than a backend error."""
    try:
        yield
    except OSError as exc:
        if exc.errno in _NO_SPACE_ERRNOS:
            raise UploadRejected(
                507,
                code="mbox_upload_insufficient_storage",
                message="The Inbox ran out of space while staging this archive",
                path=MBOX_UPLOAD_STAGING_DIRNAME,
            ) from exc
        raise


def ensure_upload_space(staging_dir: Path, still_to_write: int) -> None:
    """Reject the upload unless the reserve stays free after the rest is written."""
    free = shutil.disk_usage(staging_dir).free
    if free - still_to_write < MBOX_UPLOAD_DISK_RESERVE_BYTES:
        raise UploadRejected(
            507,
            code="mbox_upload_insufficient_storage",
            message="Not enough free space in the Inbox to stage this archive",
            path=MBOX_UPLOAD_STAGING_DIRNAME,
        )


async def stage_mbox_upload(
    request: Request, *, staging_dir: Path, stem: str, length: int,
) -> Path:
    """Stream the body to a private part file, then publish it without overwriting.

    The part file is removed on every failure, including cancellation.
    """
    part = staging_dir / f".upload-{uuid4().hex}.part"
    try:
        # Creating the part file, writing, closing and publishing can each hit
        # a full disk or quota (bytes or inodes).
        with _storage_errors():
            with part.open("xb") as handle:
                await _receive_body(request, handle, staging_dir=staging_dir, length=length)
                await asyncio.to_thread(_flush_and_sync, handle)
            return _publish(part, staging_dir, stem)
    except BaseException:
        with suppress(OSError):
            part.unlink(missing_ok=True)
        raise


async def _receive_body(
    request: Request, handle: BufferedWriter, *, staging_dir: Path, length: int,
) -> None:
    stream = request.stream()
    received = 0
    written = 0
    pending = bytearray()
    next_space_check = _SPACE_RECHECK_BYTES
    while True:
        try:
            async with asyncio.timeout(MBOX_UPLOAD_IDLE_TIMEOUT_SECONDS):
                chunk = await anext(stream)
        except StopAsyncIteration:
            break
        except TimeoutError as exc:
            raise UploadRejected(
                408,
                code="mbox_upload_timeout",
                message="The upload stalled; no data arrived for "
                f"{MBOX_UPLOAD_IDLE_TIMEOUT_SECONDS:g} seconds",
            ) from exc
        except ClientDisconnect as exc:
            raise UploadRejected(
                400, code="mbox_upload_incomplete", message="The upload ended before the archive arrived",
            ) from exc
        except HTTPException as exc:
            if exc.status_code != 413:
                raise
            raise _too_long() from exc
        received += len(chunk)
        if received > length or received > mbox_jobs.MAX_MBOX_ARCHIVE_BYTES:
            raise _too_long()
        pending += chunk
        if len(pending) >= _WRITE_BATCH_BYTES:
            data, pending = pending, bytearray()
            await asyncio.to_thread(_write_chunk, handle, data)
            written += len(data)
        if received >= next_space_check:
            next_space_check += _SPACE_RECHECK_BYTES
            ensure_upload_space(staging_dir, length - written)
    if received != length:
        raise UploadRejected(
            400,
            code="mbox_upload_incomplete",
            message=f"Received {received} of {length} declared bytes",
        )
    if pending:
        await asyncio.to_thread(_write_chunk, handle, pending)


def _too_long() -> UploadRejected:
    return UploadRejected(
        413, code="mbox_upload_too_long", message="The upload sent more bytes than it declared",
    )


def _write_chunk(handle: BufferedWriter, data: bytes | bytearray) -> None:
    handle.write(data)


def _flush_and_sync(handle: BufferedWriter) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _publish(part: Path, staging_dir: Path, stem: str) -> Path:
    """Rename the part file to the first free ``<stem>[-n].mbox`` name.

    An exclusive placeholder claims the name first, so publication never replaces
    a file that existed when the claim was made. The claim and the rename are two
    steps: another local writer that replaces the claimed placeholder in between
    would be overwritten. Such a concurrent writer is out of scope.
    """
    for index in range(1, _MAX_COLLISION_INDEX + 1):
        suffix = f"-{index}" if index > 1 else ""
        candidate = staging_dir / f"{stem}{suffix}.mbox"
        try:
            candidate.open("xb").close()
        except FileExistsError:
            continue
        try:
            os.replace(part, candidate)
        except BaseException:
            with suppress(OSError):
                candidate.unlink(missing_ok=True)
            raise
        return candidate
    raise RuntimeError(f"could not find unique filename after {_MAX_COLLISION_INDEX} attempts")


class CloseUnreadBodyMiddleware:
    """Close the connection when a response starts before the request body ends.

    Rejections that skip the body would otherwise leave a keep-alive connection
    waiting for the client to finish sending bytes the server will discard.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _declares_body(Headers(scope=scope)):
            await self.app(scope, receive, send)
            return

        body_finished = False

        async def tracking_receive() -> Message:
            nonlocal body_finished
            message = await receive()
            if message["type"] == "http.disconnect" or (
                message["type"] == "http.request" and not message.get("more_body", False)
            ):
                body_finished = True
            return message

        async def closing_send(message: Message) -> None:
            if message["type"] == "http.response.start" and not body_finished:
                headers = list(message.get("headers", []))
                if not any(name.lower() == b"connection" for name, _ in headers):
                    headers.append((b"connection", b"close"))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, tracking_receive, closing_send)


def _declares_body(headers: Headers) -> bool:
    if "transfer-encoding" in headers:
        return True
    return headers.get("content-length", "0").strip() not in ("", "0")
