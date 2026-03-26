from __future__ import annotations

from dead_letter.core.calendar import summarize_calendar_parts


def test_summarize_calendar_parts_extracts_event_summary() -> None:
    ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:fixture-event-1
DTSTART:20260306T140000Z
DTEND:20260306T143000Z
SUMMARY:Fixture Meeting
END:VEVENT
END:VCALENDAR
"""

    summaries = summarize_calendar_parts([ics])

    assert len(summaries) == 1
    assert "Fixture Meeting" in summaries[0]
    assert "2026-03-06" in summaries[0]


def test_summarize_calendar_parts_ignores_invalid_payloads() -> None:
    summaries = summarize_calendar_parts(["not-an-ics"])

    assert summaries == []
