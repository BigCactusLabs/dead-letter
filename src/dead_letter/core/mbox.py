"""Bounded, read-only framing of exported MBOX files (not a MIME parser)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

UnescapeMode = Literal["preserve", "mboxrd", "mboxo"]
# ctime-style postmarks, plus Gmail's numeric timezone before the year.
# Requiring a full postmark avoids splitting ordinary prose beginning "From ".
_ENVELOPE = re.compile(
    rb"From [^\s]+[ \t]+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[ \t]+"
    rb"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[ \t]+"
    rb"[0-3]?\d[ \t]+[0-2]\d:[0-5]\d:[0-6]\d[ \t]+"
    rb"(?:(?:[+-]\d{4}|[A-Z]{1,5})[ \t]+)?\d{4}"
    rb"(?:[ \t]+(?:[+-]\d{4}|[A-Z]{1,5}))?"
    rb"(?:[ \t]+remote from [^\r\n]+)?[ \t]*(?:\r?\n)?\Z"
)
_RD_QUOTE = re.compile(rb"^>+From ")


@dataclass(frozen=True, slots=True)
class MboxLimits:
    """Limits apply to one stored message/physical line, never the archive."""

    max_message_bytes: int = 64 * 1024 * 1024
    max_line_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
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
    """

    index: int
    envelope_offset: int
    message_offset: int
    end_offset: int
    sha256: str
    path: Path | None
    error_code: str | None = None

    def provenance(self, source: Path) -> dict[str, str | int]:
        return {
            "archive": source.name,
            "index": self.index,
            "envelope_offset": self.envelope_offset,
            "message_offset": self.message_offset,
            "end_offset": self.end_offset,
            "stored_bytes": self.end_offset - self.message_offset,
            "sha256": self.sha256,
        }


def iter_mbox(
    path: str | Path,
    *,
    limits: MboxLimits | None = None,
    unescape: UnescapeMode = "preserve",
) -> Iterator[MboxRecord]:
    """Yield one disk-backed EML at a time, with no mailbox-wide index.

    Use ``contextlib.closing`` when stopping early. Oversized records are drained
    in bounded reads and yielded as failures; scanning resumes at the next
    postmark. Content-Length framing is deliberately refused before reading the
    body: ignoring it could silently invent messages from body text. Read only
    immutable exported files, not live mail spools. An unescaped, valid postmark
    in body text is inherently ambiguous in delimiter-framed MBOX.
    """
    if unescape not in {"preserve", "mboxrd", "mboxo"}:
        raise ValueError(f"Unsupported MBOX unescape mode: {unescape}")
    limits = limits or MboxLimits()
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Expected a regular exported MBOX file: {source}")

    with source.open("rb") as stream, TemporaryDirectory(prefix="dead-letter-mbox-") as temp:
        initial = source.stat()
        staged = Path(temp) / "message.eml"
        index = 0
        offset = 0
        line_start = True
        envelope_offset = message_offset = 0
        in_headers = True
        error: str | None = None
        digest = hashlib.sha256()
        size = 0
        with staged.open("wb") as output:
            while True:
                start = offset
                fragment = stream.readline(limits.max_line_bytes + 1)
                offset += len(fragment)
                complete_line = fragment.endswith(b"\n")
                envelope = (
                    line_start and len(fragment) <= limits.max_line_bytes
                    and bool(_ENVELOPE.fullmatch(fragment))
                )
                if envelope or not fragment:
                    if index:
                        output.flush()
                        yield MboxRecord(
                            index, envelope_offset, message_offset, start,
                            digest.hexdigest(), staged if error is None and size else None,
                            error or ("mbox_empty_message" if not size else None),
                        )
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
                    continue
                if not index:
                    raise MboxFormatError("Non-empty MBOX must begin with a supported From postmark")

                digest.update(fragment)
                size += len(fragment)
                if size > limits.max_message_bytes:
                    error = error or "mbox_message_too_large"
                if len(fragment) > limits.max_line_bytes or not line_start:
                    error = error or "mbox_line_too_long"
                if in_headers and line_start:
                    # Header names are ASCII and case-insensitive. Do not trust a
                    # Content-Length received from a sender or silently ignore it.
                    name, colon, _ = fragment.partition(b":")
                    if colon and name.strip().lower() == b"content-length":
                        raise MboxFormatError(
                            f"Message {index} at byte {envelope_offset} uses Content-Length; "
                            "mboxcl/mboxcl2 framing is not supported"
                        )
                if error is None:
                    data = fragment
                    if not in_headers and line_start:
                        if unescape == "mboxrd" and _RD_QUOTE.match(data):
                            data = data[1:]
                        elif unescape == "mboxo" and data.startswith(b">From "):
                            data = data[1:]
                    output.write(data)
                if in_headers and fragment in {b"\n", b"\r\n"} and line_start:
                    in_headers = False
                line_start = complete_line
        final = source.stat()
        if (initial.st_size, initial.st_mtime_ns, initial.st_ino) != (
            final.st_size, final.st_mtime_ns, final.st_ino
        ):
            raise MboxFormatError("MBOX changed during import; use an immutable export and rerun")
