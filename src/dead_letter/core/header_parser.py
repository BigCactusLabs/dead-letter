"""Header parsing helpers shared across MIME and rendering stages."""

from __future__ import annotations

from datetime import timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime


def parse_subject(raw_subject: str | None) -> str:
    """Parse and decode Subject header content."""
    if not raw_subject:
        return ""

    parts: list[str] = []
    for value, encoding in decode_header(raw_subject):
        if isinstance(value, bytes):
            codec = encoding or "utf-8"
            parts.append(value.decode(codec, errors="replace"))
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
