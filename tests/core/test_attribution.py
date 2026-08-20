"""Tests for attribution-line parsing."""

from __future__ import annotations

import logging
import time

import pytest

from dead_letter.core.attribution import (
    parse_attribution_line,
)


def test_returns_none_for_empty_input() -> None:
    assert parse_attribution_line("") is None


def test_returns_none_for_garbage_input() -> None:
    assert parse_attribution_line("not an attribution line at all\n") is None


@pytest.mark.parametrize(
    "text",
    [
        "On Thu, Mar 5, 2026 at 10:23 AM Alice Smith wrote: I think we should ship it Friday.",
        "On Thu, Mar 5, 2026 at 10:23 AM Alice Smith\nHi there, see below.",
    ],
)
def test_rejects_non_attribution_gmail_short_forms_without_backtracking(text: str) -> None:
    started = time.perf_counter()

    result = parse_attribution_line(text)

    elapsed = time.perf_counter() - started
    assert result is None
    assert elapsed < 2


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


from dead_letter.core.attribution import annotate_quoted_zones
from dead_letter.core.types import ConversationZone, ConvertOptions, ThreadMode, ZoneKind


def _quoted(content: str) -> ConversationZone:
    return ConversationZone(kind=ZoneKind.QUOTED, content=content, source_kind="plain", confidence=0.8)


def _body(content: str) -> ConversationZone:
    return ConversationZone(kind=ZoneKind.BODY, content=content, source_kind="plain", confidence=0.8)


def test_annotate_quoted_zones_is_noop_in_latest_mode() -> None:
    zones = [_body("hello"), _quoted("On X wrote:\n> old")]
    result = annotate_quoted_zones(zones, ConvertOptions())

    out = list(result)
    assert out[1].content == "On X wrote:\n> old"
    assert "attribution_from" not in out[1].metadata


def test_annotate_strips_attribution_and_caches_metadata() -> None:
    zones = [
        _body("Hello"),
        _quoted("On Thu, Mar 5, 2026 at 10:23 AM Alice <alice@example.com> wrote:\nold body\n"),
    ]
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    out = annotate_quoted_zones(zones, opts)

    assert out[1].content == "old body\n"
    assert out[1].metadata["attribution_from"] == "Alice <alice@example.com>"
    assert "Mar 5, 2026" in out[1].metadata["attribution_date"]
    assert out[1].metadata["_quoted_original"].startswith("On Thu")


def test_annotate_preserves_keys_only_for_populated_fields() -> None:
    zones = [
        _body("Hello"),
        _quoted("On Thu, Mar 5, 2026 at 10:23 AM Alice <alice@example.com> wrote:\nold\n"),
    ]
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    out = annotate_quoted_zones(zones, opts)

    assert "attribution_from" in out[1].metadata
    assert "attribution_date" in out[1].metadata
    assert "attribution_subject" not in out[1].metadata


def test_annotate_leaves_zone_unchanged_when_attribution_fails() -> None:
    zones = [
        _body("Hello"),
        _quoted("no attribution at all here\nmore body\n"),
    ]
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    out = annotate_quoted_zones(zones, opts)

    assert out[1].content == "no attribution at all here\nmore body\n"
    assert "attribution_from" not in out[1].metadata
    assert "_quoted_original" not in out[1].metadata


def test_annotate_skips_when_no_non_quoted_content_exists() -> None:
    zones = [_quoted("On Thu Alice wrote:\nold body\n")]
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    out = annotate_quoted_zones(zones, opts)

    assert out[0].content == "On Thu Alice wrote:\nold body\n"
    assert "attribution_from" not in out[0].metadata


def test_annotate_parses_attribution_through_leading_quote_markers() -> None:
    quoted_text = (
        "> On Thu, Mar 5, 2026 at 9:50 AM Alice <alice@example.com> wrote:\n"
        "> Original message from Alice.\n"
    )
    zones = [_body("Hello"), _quoted(quoted_text)]
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    out = annotate_quoted_zones(zones, opts)

    assert out[1].metadata["attribution_from"] == "Alice <alice@example.com>"
    assert "> Original" not in out[1].content
    assert "Original message from Alice." in out[1].content
    assert out[1].metadata["_quoted_original"] == quoted_text


def test_annotate_parses_attribution_through_double_nested_quote_markers() -> None:
    quoted_text = (
        "> > On Thu, Mar 5, 2026 at 9:50 AM Alice <alice@example.com> wrote:\n"
        "> > Original message from Alice.\n"
    )
    zones = [_body("Hello"), _quoted(quoted_text)]
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    out = annotate_quoted_zones(zones, opts)

    assert out[1].metadata["attribution_from"] == "Alice <alice@example.com>"
    for line in out[1].content.splitlines():
        assert not line.startswith(">"), f"residual marker on line: {line!r}"
    assert "Original message from Alice." in out[1].content


def test_annotate_strips_quote_markers_even_when_attribution_fails() -> None:
    zones = [_body("Hello"), _quoted("> some unrecognized prefix\n> more body\n")]
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    out = annotate_quoted_zones(zones, opts)

    assert out[1].content == "some unrecognized prefix\nmore body\n"
    assert "attribution_from" not in out[1].metadata
    assert out[1].metadata["_quoted_original"] == "> some unrecognized prefix\n> more body\n"
