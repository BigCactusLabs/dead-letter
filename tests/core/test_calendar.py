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


def test_summarize_calendar_parts_does_not_read_a_path_from_mail(tmp_path) -> None:
    # icalendar before 7.3.1 read a single-line str argument as a file path.
    local = tmp_path / "local.ics"
    local.write_bytes(
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        b"SUMMARY:Local file event\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )

    assert summarize_calendar_parts([str(local)]) == []


def test_summarize_calendar_parts_keeps_non_ascii_summary() -> None:
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "DTSTART:20260306T140000Z\r\nSUMMARY:Réunion 会議\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )

    assert summarize_calendar_parts([ics]) == ["Réunion 会議 (2026-03-06T14:00:00+00:00)"]
