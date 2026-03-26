from __future__ import annotations

from dead_letter.core.header_parser import parse_date, parse_subject


def test_parse_subject_decodes_rfc2047_words() -> None:
    assert parse_subject("=?utf-8?b?SGVsbG8gV29ybGQ=?=") == "Hello World"


def test_parse_date_returns_iso8601() -> None:
    assert parse_date("Thu, 05 Mar 2026 09:00:00 +0000") == "2026-03-05T09:00:00+00:00"


def test_parse_date_returns_none_for_invalid_values() -> None:
    assert parse_date("not-a-real-date") is None
