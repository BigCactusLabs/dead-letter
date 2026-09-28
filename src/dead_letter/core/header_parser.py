"""Header parsing helpers shared across MIME and rendering stages."""

from __future__ import annotations

from datetime import timezone
from email.errors import HeaderParseError
from email.header import decode_header
from email.utils import parsedate_to_datetime


def parse_subject(raw_subject: str | None) -> str:
    """Parse and decode Subject header content."""
    if not raw_subject:
        return ""

    try:
        decoded_parts = decode_header(raw_subject)
    except HeaderParseError:
        # Preserve malformed encoded words rather than aborting conversion.
        return raw_subject.strip()

    parts: list[str] = []
    for value, encoding in decoded_parts:
        if isinstance(value, bytes):
            codec = encoding or "utf-8"
            try:
                decoded = value.decode(codec, errors="replace")
            except (LookupError, UnicodeError):
                # Email-supplied charset names can be unknown or name codecs
                # that do not support text decoding with replacement.
                decoded = value.decode("utf-8", errors="replace")
            parts.append(decoded)
        else:
            parts.append(value)
    return "".join(parts).strip()


def parse_date(raw_date: str | None) -> str | None:
    """Parse RFC-2822 Date header into ISO-8601 string."""
    if not raw_date:
        return None

    try:
        dt = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError):
        return None

    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.isoformat()
