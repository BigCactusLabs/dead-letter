from __future__ import annotations

from pathlib import Path

import pytest

from dead_letter.core.render import render_markdown, serialize_markdown
from dead_letter.core.types import (
    ConvertOptions,
    ParsedEmail,
    ThreadedContent,
    ThreadMode,
    Zone,
    ZoneKind,
)


def _parsed_email() -> ParsedEmail:
    return ParsedEmail(
        source=Path("tests/core/fixtures/plain_text.eml"),
        subject="Plain Text Fixture",
        sender="alice@example.com",
        date="2026-03-05T09:00:00+00:00",
        text_body="Hello",
        html_body=None,
        headers={"Subject": "Plain Text Fixture"},
        attachments=["agenda.pdf"],
    )


def test_render_markdown_builds_front_matter_and_body() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[
            Zone(kind=ZoneKind.BODY, content="Body text"),
            Zone(kind=ZoneKind.QUOTED, content="Older message"),
        ]
    )

    rendered = render_markdown(parsed, threaded, calendar_summaries=["Fixture Meeting"])

    assert rendered.front_matter["subject"] == "Plain Text Fixture"
    assert rendered.front_matter["sender"] == "alice@example.com"
    assert rendered.front_matter["attachments"] == ["agenda.pdf"]
    assert "Fixture Meeting" in rendered.front_matter["calendar"]
    assert "Body text" in rendered.body
    assert "Older message" not in rendered.body


def test_render_markdown_falls_back_to_quoted_zones_when_no_body_zones_exist() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[Zone(kind=ZoneKind.QUOTED, content="Only quoted message")]
    )

    rendered = render_markdown(parsed, threaded)

    assert rendered.body == "Only quoted message"


def test_serialize_markdown_emits_yaml_front_matter() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(zones=[Zone(kind=ZoneKind.BODY, content="Only body")])

    document = serialize_markdown(render_markdown(parsed, threaded))

    assert document.startswith("---\n")
    assert "subject: Plain Text Fixture" in document
    assert "Only body" in document


def test_render_markdown_accepts_options_keyword() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(zones=[Zone(kind=ZoneKind.BODY, content="Body")])

    rendered = render_markdown(parsed, threaded, options=ConvertOptions())

    assert "Body" in rendered.body


def test_render_markdown_latest_mode_byte_identical_to_no_options() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[
            Zone(kind=ZoneKind.BODY, content="Body"),
            Zone(kind=ZoneKind.QUOTED, content="Older"),
        ]
    )

    no_opts = render_markdown(parsed, threaded)
    latest = render_markdown(parsed, threaded, options=ConvertOptions(thread_mode=ThreadMode.LATEST))

    assert no_opts.body == latest.body
    assert no_opts.front_matter == latest.front_matter


def test_render_structured_emits_per_message_sections() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[
            Zone(kind=ZoneKind.BODY, content="Latest body"),
            Zone(
                kind=ZoneKind.QUOTED,
                content="prior 1 body",
                metadata={
                    "attribution_from": "Alice <alice@example.com>",
                    "attribution_date": "Mar 5, 2026",
                },
            ),
            Zone(
                kind=ZoneKind.QUOTED,
                content="prior 2 body",
                metadata={
                    "attribution_from": "Bob <bob@example.com>",
                    "attribution_date": "Mar 4, 2026",
                },
            ),
        ]
    )

    rendered = render_markdown(
        parsed, threaded, options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED)
    )

    assert "Latest body" in rendered.body
    assert "## From Alice <alice@example.com> (Mar 5, 2026)" in rendered.body
    assert "## From Bob <bob@example.com> (Mar 4, 2026)" in rendered.body
    assert "prior 1 body" in rendered.body
    assert "prior 2 body" in rendered.body
    assert rendered.front_matter["thread_messages"] == 2


@pytest.mark.parametrize(
    "metadata,expected_header",
    [
        ({"attribution_from": "Alice"}, "## From Alice"),
        ({"attribution_from": "Alice", "attribution_date": "Mar 5"}, "## From Alice (Mar 5)"),
        (
            {"attribution_from": "Alice", "attribution_subject": "Re: x"},
            "## From Alice — Re: x",
        ),
        (
            {"attribution_from": "Alice", "attribution_date": "Mar 5", "attribution_subject": "Re: x"},
            "## From Alice (Mar 5) — Re: x",
        ),
        ({}, "## Earlier message"),
        ({"attribution_date": "Mar 5"}, "## Earlier message"),
        ({"attribution_subject": "Re: x"}, "## Earlier message"),
        ({"thread_render": "degenerate"}, "## Earlier in thread"),
    ],
)
def test_render_section_header_ladder(metadata: dict[str, str], expected_header: str) -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[
            Zone(kind=ZoneKind.BODY, content="Latest"),
            Zone(kind=ZoneKind.QUOTED, content="prior body", metadata=metadata),
        ]
    )

    rendered = render_markdown(
        parsed, threaded, options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED)
    )

    assert expected_header in rendered.body
