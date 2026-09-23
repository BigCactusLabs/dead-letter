"""Bounded, read-only framing of exported MBOX files (not a MIME parser)."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO, Literal

UnescapeMode = Literal["preserve", "mboxrd", "mboxo", "mboxcl", "mboxcl2"]
# Opt-in Content-Length dialects; never auto-detected from the archive.
_LENGTH_FRAMED = ("mboxcl", "mboxcl2")
_LENGTH_WSP = b" \t\r"
_MAX_LENGTH_DIGITS = 20
# ctime-style postmarks, plus Gmail's numeric timezone before the year.
# Requiring a full postmark avoids splitting ordinary prose beginning "From ".
# The remote-host tail is possessive: its trailing whitespace must not be
# redistributed quadratically against the optional padding on malformed input.
_ENVELOPE = re.compile(
    rb"From [^\s]+[ \t]+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[ \t]+"
    rb"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[ \t]+"
    rb"[0-3]?\d[ \t]+[0-2]\d:[0-5]\d:[0-6]\d[ \t]+"
    rb"(?:(?:[+-]\d{4}|[A-Z]{1,5})[ \t]+)?\d{4}"
    rb"(?:[ \t]+(?:[+-]\d{4}|[A-Z]{1,5}))?"
    rb"(?:[ \t]+remote from [^\r\n]++)?[ \t]*(?:\r?\n)?\Z"
)
_RD_QUOTE = re.compile(rb"^>+From ")


class _ContentLength:
    """Parse top-level Content-Length fields one read fragment at a time.

    The unfolded value, trimmed of SP/HTAB/CR, must be 1-20 ASCII digits: no
    sign or inner whitespace. Duplicates must agree; a missing or conflicting
    field is invalid. Memory is bounded by the digit limit, not the field size.
    """

    __slots__ = ("digits", "phase", "valid", "value")

    def __init__(self) -> None:
        self.value: int | None = None
        self.valid = True
        self.digits = b""
        # None: outside the field; 0 leading WSP; 1 digits; 2 trailing WSP; -1 invalid.
        self.phase: int | None = None

    def open(self) -> None:
        self.close()
        self.digits = b""
        self.phase = 0

    def feed(self, chunk: bytes) -> None:
        if self.phase is None or self.phase < 0:
            return
        # Unfolding removes only the line break; the continuation WSP remains.
        chunk = chunk.removesuffix(b"\n")
        core = chunk.strip(_LENGTH_WSP)
        if not core:
            if chunk and self.phase == 1:
                self.phase = 2
            return
        if (
            not core.isdigit() or self.phase == 2
            or (self.phase == 1 and chunk[0] in _LENGTH_WSP)
            or len(self.digits) + len(core) > _MAX_LENGTH_DIGITS
        ):
            self.phase = -1
            return
        self.digits += core
        self.phase = 2 if chunk[-1] in _LENGTH_WSP else 1

    def close(self) -> None:
        if self.phase is None:
            return
        if self.phase in (1, 2):
            length = int(self.digits)
            if self.value is not None and self.value != length:
                self.valid = False
            self.value = length
        else:
            self.valid = False
        self.phase = None

    def result(self) -> int | None:
        self.close()
        return self.value if self.valid else None


def _framed_end(stream: BinaryIO, body_start: int, length: int, size: int, max_line: int) -> int | None:
    """Return the validated end of a length-framed record, or None.

    The integer range check precedes any seek. At most two separator bytes and
    one postmark line are read, never the declared body. The caller restores
    the stream position. The record keeps its one separator EOL, like the
    trailing storage blank line of delimiter-framed records.
    """
    end = body_start + length
    if end > size:
        return None
    stream.seek(end)
    tail = stream.read(min(2, size - end))
    if size - end <= 2 and tail in (b"\n", b"\r\n"):
        return size
    separator = 1 if tail[:1] == b"\n" else 2 if tail == b"\r\n" else 0
    if not separator or end + separator >= size:
        return None
    stream.seek(end + separator)
    line = stream.readline(min(max_line + 1, size - end - separator))
    if len(line) > max_line or not _ENVELOPE.fullmatch(line):
        return None
    return end + separator


def _length_fallback(unescape: UnescapeMode, index: int, envelope_offset: int) -> str:
    if unescape == "mboxcl2":
        # Unquoted body postmarks make scanning unsafe; earlier records stand.
        raise MboxFormatError(
            f"Message {index} at byte {envelope_offset} has a missing or invalid "
            "Content-Length; mboxcl2 cannot fall back to postmark scanning"
        )
    return "mbox_content_length_fallback"


@dataclass(frozen=True, slots=True)
class MboxLimits:
    """Limits apply to one stored message/physical line, never the archive."""

    max_message_bytes: int = 64 * 1024 * 1024
    max_line_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if type(self.max_message_bytes) is not int or type(self.max_line_bytes) is not int:
            raise ValueError("MBOX limits must be integer byte counts")
        if self.max_message_bytes < 1 or self.max_line_bytes < 128:
            raise ValueError("MBOX limits require message bytes >= 1 and line bytes >= 128")


class MboxFormatError(ValueError):
    """Archive-level framing failure; earlier yielded messages remain usable."""


@dataclass(frozen=True, slots=True)
class MboxRecord:
    """A staged message; its path is valid only until the iterator advances.

    Offsets and SHA-256 refer to the ORIGINAL stored bytes, before unquoting.
    The range excludes the envelope, includes any trailing storage blank line,
    and uses an exclusive end offset. No cross-export deduplication is implied.
    ``framing_diagnostic`` names a non-fatal framing fallback: an mboxcl record
    whose Content-Length was missing or invalid and was scanned by postmark.
    """

    index: int
    envelope_offset: int
    message_offset: int
    end_offset: int
    sha256: str
    path: Path | None
    error_code: str | None = None
    framing_diagnostic: str | None = None

    def provenance(self, source: Path) -> dict[str, str | int]:
        provenance: dict[str, str | int] = {
            "archive": source.name,
            "index": self.index,
            "envelope_offset": self.envelope_offset,
            "message_offset": self.message_offset,
            "end_offset": self.end_offset,
            "stored_bytes": self.end_offset - self.message_offset,
            "sha256": self.sha256,
        }
        if self.framing_diagnostic is not None:
            provenance["framing_diagnostic"] = self.framing_diagnostic
        return provenance


def _source_signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _check_source(
    fd: int, source: Path, initial: os.stat_result, initial_path: os.stat_result,
) -> None:
    """Bind the opened bytes to the named export, not just two path stats.

    These metadata checks detect ordinary mutation/replacement, not malicious
    metadata restoration or a filesystem snapshot. Callers still need an
    immutable export. Previously emitted results cannot be retracted.
    """
    try:
        unchanged = (
            os.path.samestat(initial, initial_path)
            and _source_signature(os.fstat(fd)) == _source_signature(initial)
            and _source_signature(source.stat()) == _source_signature(initial_path)
        )
    except OSError:
        unchanged = False
    if not unchanged:
        raise MboxFormatError("MBOX changed during import; use an immutable export and rerun")


def iter_mbox(
    path: str | Path,
    *,
    limits: MboxLimits | None = None,
    unescape: UnescapeMode = "preserve",
) -> Iterator[MboxRecord]:
    """Yield one disk-backed EML at a time, with no mailbox-wide index.

    Use ``contextlib.closing`` when stopping early. Oversized records are drained
    in bounded reads and yielded as failures; scanning resumes at the next
    postmark or validated length-framed boundary. In ``preserve``, ``mboxrd``
    and ``mboxo`` modes a top-level Content-Length field is refused before
    reading the body: ignoring it could silently invent messages from body text.
    The opt-in ``mboxcl``/``mboxcl2`` modes trust it only when the declared body
    ends with one EOL followed by a valid postmark, or by EOF; that body is never
    scanned for postmarks. A missing or invalid length falls back to postmark
    scanning with a ``framing_diagnostic`` for mboxcl and raises
    ``MboxFormatError`` for mboxcl2. Read only immutable exported files, not
    live mail spools. An unescaped, valid postmark in body text is inherently
    ambiguous in delimiter-framed MBOX.
    """
    if unescape not in {"preserve", "mboxrd", "mboxo", "mboxcl", "mboxcl2"}:
        raise ValueError(f"Unsupported MBOX unescape mode: {unescape}")
    limits = limits or MboxLimits()
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Expected a regular exported MBOX file: {source}")

    with source.open("rb") as stream, TemporaryDirectory(prefix="dead-letter-mbox-") as temp:
        fd = stream.fileno()
        initial = os.fstat(fd)
        # Keep independent baselines: Windows stat/fstat can expose different
        # ctime semantics. Compare identity across APIs, timestamps within each.
        initial_path = source.stat()
        if not stat.S_ISREG(initial.st_mode):
            raise MboxFormatError("Expected a regular exported MBOX file")
        _check_source(fd, source, initial, initial_path)
        staged = Path(temp) / "message.eml"
        index = 0
        offset = 0
        line_start = True
        envelope_offset = message_offset = 0
        in_headers = True
        error: str | None = None
        digest = hashlib.sha256()
        size = 0
        framed = unescape in _LENGTH_FRAMED
        lengths = _ContentLength()
        framing: str | None = None
        # Validated end of a length-framed record; its body is never scanned.
        record_end: int | None = None
        with staged.open("wb") as output:
            while True:
                start = offset
                # Never follow an export that is being appended indefinitely.
                remaining = initial.st_size - offset
                if record_end is not None and start < record_end:
                    remaining = record_end - start
                fragment = stream.readline(min(limits.max_line_bytes + 1, remaining)) if remaining else b""
                offset += len(fragment)
                complete_line = fragment.endswith(b"\n")
                envelope = (
                    line_start and len(fragment) <= limits.max_line_bytes
                    and bool(_ENVELOPE.fullmatch(fragment))
                    and (record_end is None or start == record_end)
                )
                if record_end is not None and start == record_end:
                    if fragment and not envelope:
                        raise MboxFormatError("MBOX changed during import; use an immutable export and rerun")
                    record_end = None
                if envelope or not fragment:
                    _check_source(fd, source, initial, initial_path)
                    if not fragment and offset != initial.st_size:
                        raise MboxFormatError("MBOX ended before its original size")
                    if index and framed and in_headers:
                        # No header-ending blank line, so no declared body start.
                        framing = _length_fallback(unescape, index, envelope_offset)
                    if index:
                        output.flush()
                        yield MboxRecord(
                            index, envelope_offset, message_offset, start,
                            digest.hexdigest(), staged if error is None and size else None,
                            error or ("mbox_empty_message" if not size else None),
                            framing,
                        )
                        _check_source(fd, source, initial, initial_path)
                        output.seek(0)
                        output.truncate()
                    if not fragment:
                        break
                    index += 1
                    envelope_offset, message_offset = start, offset
                    digest = hashlib.sha256()
                    size = 0
                    error = None
                    in_headers = True
                    line_start = True
                    lengths = _ContentLength()
                    framing = None
                    continue
                if not index:
                    raise MboxFormatError("Non-empty MBOX must begin with a supported From postmark")

                digest.update(fragment)
                size += len(fragment)
                if size > limits.max_message_bytes:
                    error = error or "mbox_message_too_large"
                if len(fragment) > limits.max_line_bytes or not line_start:
                    error = error or "mbox_line_too_long"
                if in_headers and line_start and not fragment.startswith((b" ", b"\t")):
                    # RFC 5322 section 2.2.3: WSP starts a continuation, not a new field.
                    # Header names are ASCII and case-insensitive. Do not trust a
                    # Content-Length received from a sender or silently ignore it.
                    name, colon, value = fragment.partition(b":")
                    field = bool(colon) and name.rstrip(b" \t").lower() == b"content-length"
                    if field and not framed:
                        raise MboxFormatError(
                            f"Message {index} at byte {envelope_offset} uses Content-Length; "
                            "mboxcl/mboxcl2 framing requires selecting that dialect explicitly"
                        )
                    if framed:
                        lengths.close()
                        if field:
                            lengths.open()
                            lengths.feed(value)
                elif in_headers and framed:
                    # A folded continuation, or the rest of an over-long line.
                    lengths.feed(fragment)
                if error is None:
                    data = fragment
                    if not in_headers and line_start:
                        if unescape == "mboxrd" and _RD_QUOTE.match(data):
                            data = data[1:]
                        elif unescape in ("mboxo", "mboxcl") and data.startswith(b">From "):
                            data = data[1:]
                    output.write(data)
                if in_headers and fragment in {b"\n", b"\r\n"} and line_start:
                    in_headers = False
                    if framed:
                        length = lengths.result()
                        if length is not None:
                            try:
                                record_end = _framed_end(
                                    stream, offset, length, initial.st_size, limits.max_line_bytes,
                                )
                            finally:
                                stream.seek(offset)
                            _check_source(fd, source, initial, initial_path)
                        if record_end is None:
                            framing = _length_fallback(unescape, index, envelope_offset)
                line_start = complete_line
        _check_source(fd, source, initial, initial_path)
