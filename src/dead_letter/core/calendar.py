"""Calendar extraction and summarization utilities."""

from __future__ import annotations

from datetime import date, datetime

from icalendar import Calendar


def _to_iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()

    dt = getattr(value, "dt", None)
    if isinstance(dt, datetime):
        return dt.isoformat()
    if isinstance(dt, date):
        return dt.isoformat()
    return None


def summarize_calendar_parts(parts: list[str]) -> list[str]:
    """Return compact human-readable summaries for VEVENT items."""
    summaries: list[str] = []

    for raw in parts:
        try:
            cal = Calendar.from_ical(raw)
        except Exception:
            continue

        for component in cal.walk("VEVENT"):
            summary = str(component.get("SUMMARY") or "(no summary)").strip()
            start = _to_iso(component.get("DTSTART"))
            end = _to_iso(component.get("DTEND"))

            if start and end:
                summaries.append(f"{summary} ({start} -> {end})")
            elif start:
                summaries.append(f"{summary} ({start})")
            else:
                summaries.append(summary)

    return summaries
