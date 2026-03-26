from __future__ import annotations

from pathlib import Path

from dead_letter.core.render import render_markdown, serialize_markdown
from dead_letter.core.types import ParsedEmail, ThreadedContent, Zone, ZoneKind


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
