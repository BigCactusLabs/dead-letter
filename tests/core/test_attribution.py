"""Tests for attribution-line parsing."""

from __future__ import annotations

import logging

import pytest

from dead_letter.core.attribution import (
    AttributionMatch,
    parse_attribution_line,
)


def test_returns_none_for_empty_input() -> None:
    assert parse_attribution_line("") is None


def test_returns_none_for_garbage_input() -> None:
    assert parse_attribution_line("not an attribution line at all\n") is None


def test_debug_logs_when_no_pattern_matches(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="dead_letter.core.attribution")

    parse_attribution_line("totally unparseable garbage\n")

    assert any("no pattern matched" in record.message for record in caplog.records)


def test_parses_gmail_short_form() -> None:
    text = "On Thu, Mar 5, 2026 at 10:23 AM Alice <alice@example.com> wrote:\n> body\n"

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice <alice@example.com>"
    assert result.metadata.date == "Thu, Mar 5, 2026 at 10:23 AM"
    assert result.metadata.subject is None
    assert text[result.consumed_end:] == "> body\n"


def test_parses_gmail_line_wrapped() -> None:
    text = (
        "On Thu, Mar 5, 2026 at 10:23 AM Alice Verylongname\n"
        "<alice.verylongname@example.com> wrote:\n"
        "> body\n"
    )

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice Verylongname <alice.verylongname@example.com>"
    assert "\n" not in result.metadata.from_
    assert text[result.consumed_end:] == "> body\n"


def test_attribution_metadata_has_no_internal_newlines() -> None:
    samples = [
        "On Thu, Mar 5, 2026 at 10:23 AM Alice Verylongname\n<alice@example.com> wrote:\n> body\n",
        "**From:** Alice <alice@example.com>\n**Sent:** Thursday, March 5, 2026 10:23 AM\n**To:** Bob <bob@example.com>\n**Subject:** Re: x\n\nbody\n",
    ]
    for text in samples:
        result = parse_attribution_line(text)
        assert result is not None, f"no match on {text!r}"
        for field_name in ("from_", "date", "subject"):
            value = getattr(result.metadata, field_name)
            if value is not None:
                assert "\n" not in value, f"{field_name} contains newline: {value!r}"


def test_parses_apple_mail() -> None:
    text = "On Mar 5, 2026, at 10:23 AM, Alice <alice@example.com> wrote:\n> body\n"

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice <alice@example.com>"
    assert "Mar 5, 2026" in result.metadata.date


def test_parses_outlook_block() -> None:
    text = (
        "From: Alice <alice@example.com>\n"
        "Sent: Thursday, March 5, 2026 10:23 AM\n"
        "To: Bob <bob@example.com>\n"
        "Subject: Re: project status\n"
        "\n"
        "Body text\n"
    )

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice <alice@example.com>"
    assert "March 5, 2026" in result.metadata.date
    assert result.metadata.subject == "Re: project status"
    assert text[result.consumed_end:] == "Body text\n"


def test_parses_outlook_block_markdown_bold_variant() -> None:
    text = (
        "**From:** Alice <alice@example.com>\n"
        "**Sent:** Thursday, March 5, 2026 10:23 AM\n"
        "**To:** Bob <bob@example.com>\n"
        "**Subject:** Re: project status\n"
        "\n"
        "Body text\n"
    )

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice <alice@example.com>"
    assert result.metadata.subject == "Re: project status"


def test_parses_outlook_block_with_cc_between_to_and_subject() -> None:
    text = (
        "From: Alice <alice@example.com>\n"
        "Sent: Thursday, March 5, 2026 10:23 AM\n"
        "To: Bob <bob@example.com>\n"
        "Cc: Carol <carol@example.com>\n"
        "Subject: Re: project status\n"
        "\n"
        "Body text\n"
    )

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice <alice@example.com>"
    assert result.metadata.subject == "Re: project status"


def test_parses_german_attribution() -> None:
    text = "Am Donnerstag, 5. März 2026 um 10:23 schrieb Alice <alice@example.com>:\n> body\n"

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice <alice@example.com>"
    assert result.metadata.date == "Donnerstag, 5. März 2026 um 10:23"


def test_parses_french_attribution() -> None:
    text = "Le jeu., 5 mars 2026 à 10:23, Alice <alice@example.com> a écrit :\n> body\n"

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice <alice@example.com>"
    assert result.metadata.date == "jeu., 5 mars 2026 à 10:23"


def test_parses_spanish_attribution() -> None:
    text = "El jue., 5 mar 2026 a las 10:23, Alice <alice@example.com> escribió:\n> body\n"

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice <alice@example.com>"
    assert result.metadata.date == "jue., 5 mar 2026 a las 10:23"


def test_consumed_end_skips_following_blank_lines() -> None:
    text = "On Thu, Mar 5, 2026 at 10:23 AM Alice <alice@example.com> wrote:\n\n\n> body\n"

    result = parse_attribution_line(text)

    assert result is not None
    assert text[result.consumed_end:] == "> body\n"
