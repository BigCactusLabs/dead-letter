"""Tests for attribution-line parsing."""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
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


@pytest.mark.parametrize("recipients", [25, 400], ids=["25-recipients", "400-recipients"])
def test_parses_outlook_block_with_long_recipient_lists(recipients: int) -> None:
    to_line = "; ".join(f"Person {i} <person{i}@example.com>" for i in range(recipients))
    header = (
        "From: Alice Example <alice@example.com>\n"
        "Sent: Thursday, March 5, 2026 10:23 AM\n"
        f"To: {to_line}\n"
        f"Cc: {to_line}\n"
        "Subject: Re: project status\n"
    )
    assert len(header) > 1000  # Beyond the single-line pattern budget.
    text = header + "\nBody text\n"

    result = parse_attribution_line(text)

    assert result is not None
    assert result.metadata.from_ == "Alice Example <alice@example.com>"
    assert result.metadata.subject == "Re: project status"
    assert text[result.consumed_end:] == "Body text\n"


def test_outlook_block_beyond_its_window_is_unrecognized() -> None:
    to_line = "Bob <bob@example.com>; " * 3000  # About 69 KiB, past the 64 KiB window.
    text = (
        "From: Alice <alice@example.com>\nSent: Thursday, March 5, 2026 10:23 AM\n"
        f"To: {to_line}\nSubject: Re: project status\n\nBody text\n"
    )

    assert parse_attribution_line(text) is None


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


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
@pytest.mark.parametrize("header", [
    "On Thu, Mar 5, 2026 at 10:23 AM Alice Verylongname\n"
    "<alice.verylongname@example.com> wrote:\n",
    "From: Alice <alice@example.com>\nSent: Thursday, March 5, 2026 10:23 AM\n"
    "To: Bob <bob@example.com>\n"
    + "Cc: Carol <carol@example.com>\n" * 8 + "Subject: Re: project status\n",
    "**From:** Alice <alice@example.com>\n**Sent:** Thursday, March 5, 2026 10:23 AM\n"
    "**To:** Bob <bob@example.com>\n**Subject:** Re: project status\n",
], ids=["wrapped-gmail", "outlook-eight-intermediate-headers", "bold-outlook"])
def test_short_complete_headers_before_large_body_keep_original_offsets(header, line_ending):
    header = header.replace("\n", line_ending)
    body = "BODY_START " + "x" * 128_000 + " BODY_END\n"
    text = header + body

    match = parse_attribution_line(text)

    assert match is not None
    assert match.consumed_end == len(header)
    assert match.matched_text == header == text[:match.consumed_end]
    assert text[match.consumed_end:] == body
    assert "Alice" in match.metadata.from_


def _german_header_of_length(length: int) -> str:
    prefix = "Am Donnerstag schrieb "
    return prefix + "A" * (length - len(prefix) - 1) + ":"


def test_complete_line_at_character_budget_is_accepted():
    header = _german_header_of_length(999) + "\n"
    text = header + "BODY\n"

    match = parse_attribution_line(text)

    assert match is not None
    assert match.consumed_end == 1000
    assert match.matched_text == header
    assert text[match.consumed_end:] == "BODY\n"


def test_line_ending_beyond_character_budget_is_not_invented():
    header = _german_header_of_length(1000)
    assert parse_attribution_line(header) is not None  # A genuine EOF at the budget.
    assert parse_attribution_line(header + "\nBODY\n") is None  # LF is character 1001.
    assert parse_attribution_line(header + "not an attribution\n") is None


def test_cutoff_inside_wrapped_header_preserves_it_as_unrecognized():
    text = (
        "On Thu, Mar 5, 2026 at 10:23 AM Alice\n"
        + "Verylongname " * 100 + "wrote:\nBODY\n"
    )

    assert parse_attribution_line(text) is None


def test_valid_header_skips_long_blank_prefix_without_consuming_indented_payload():
    header = "Am Donnerstag schrieb Alice:\n"
    blanks = "\n" * 2000
    body = "    INDENTED_PAYLOAD\n"
    text = header + blanks + body

    match = parse_attribution_line(text)

    assert match is not None
    assert match.consumed_end == len(header + blanks)
    assert match.matched_text == header + blanks
    assert text[match.consumed_end:] == body


def test_adversarial_attribution_candidates_finish_in_killable_process():
    # Timing an in-process call cannot interrupt a catastrophic regex. This
    # child exercises both direct callers and structured quote annotation.
    script = textwrap.dedent("""\
        from dead_letter.core.attribution import parse_attribution_line, annotate_quoted_zones
        from dead_letter.core.types import ConversationZone, ConvertOptions, ThreadMode, ZoneKind

        candidates = [
            'Am X' + ' ' * 128_000 + ':',
            'Am ' + 'schrieb ' * 16_000 + 'x:',
            'Le X,' + ' ' * 128_000 + ':',
            'On Mar 5, 2026, at 10:23 AM, ' + '<' * 128_000 + ':',
            'On Thu, Mar 5, 2026 at 10:23 AM Alice\\n' + 'sender ' * 32_000 + ':',
            'From: Alice\\nSent: Thursday\\nTo: ' + 'Bob ' * 32_000 + '\\nSubject: X\\n',
            # Inside the 64 KiB Outlook window; quadratic+ before possessive fields.
            'From:' + ' ' * 64_000,
            'From: Alice\\nSent:' + ' ' * 64_000,
            'From: Alice\\nSent: Thursday\\nTo:' + ' \\t' * 32_000,
            'From:' + ' \\n' * 32_000,
            'From: Alice\\nSent: Thursday\\nTo: Bob\\n' + 'Cc:  \\n' * 10_000,
        ]
        options = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)
        for text in candidates:
            assert parse_attribution_line(text) is None
            original = '> ' + text + '\\n> QUOTED_PAYLOAD'
            zones = [
                ConversationZone(kind=ZoneKind.BODY, content='AUTHORED_PAYLOAD', source_kind='plain'),
                ConversationZone(kind=ZoneKind.QUOTED, content=original, source_kind='plain'),
            ]
            annotated = annotate_quoted_zones(zones, options)
            assert annotated[1].content == text + '\\nQUOTED_PAYLOAD'
            assert annotated[1].metadata['_quoted_original'] == original
            assert 'attribution_from' not in annotated[1].metadata
        print('bounded candidates and annotation passed')
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "bounded candidates and annotation passed" in result.stdout


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


@pytest.mark.parametrize("quoted_only", [False, True])
def test_oversized_attribution_preserves_quote_fallback(quoted_only):
    text = "> " + _german_header_of_length(1001) + "\n> QUOTED_PAYLOAD\n"
    quoted = _quoted(text)
    zones = [quoted] if quoted_only else [_body("AUTHORED_PAYLOAD"), quoted]

    result = annotate_quoted_zones(zones, ConvertOptions(thread_mode=ThreadMode.STRUCTURED))

    assert "attribution_from" not in result[-1].metadata
    if quoted_only:
        assert result[-1] is quoted
        assert result[-1].content == text
    else:
        assert result[-1].content == text.replace("> ", "")
        assert result[-1].metadata["_quoted_original"] == text


def test_oversized_attribution_reaches_rendered_output_once(tmp_path):
    from dead_letter.core import convert

    # Two explicit quote zones bypass the separate mail-parser-reply splitter;
    # this regression owns attribution's budget, not that dependency's parser.
    header = "Am CANDIDATE_START " + "date-token " * 1000 + "schrieb Alice:"
    html = (
        '<p>AUTHORED_PAYLOAD</p><blockquote class="gmail_quote">'
        + header + '<br>FIRST_QUOTED_PAYLOAD</blockquote>'
        '<blockquote class="gmail_quote">SECOND_QUOTED_PAYLOAD</blockquote>'
    )
    source = tmp_path / "synthetic.eml"
    output = tmp_path / "out.md"
    source.write_text(
        "From: synthetic@example.test\nSubject: Bounded attribution\n"
        "Content-Type: text/html; charset=utf-8\n\n" + html,
        encoding="utf-8",
    )

    result = convert(source, output=output, options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED))

    assert result.success, result.error
    rendered = output.read_text(encoding="utf-8")
    for sentinel in ("AUTHORED_PAYLOAD", "CANDIDATE_START", "FIRST_QUOTED_PAYLOAD", "SECOND_QUOTED_PAYLOAD"):
        assert rendered.count(sentinel) == 1
    assert rendered.count("date-token") == 1000
    assert "## From " not in rendered
