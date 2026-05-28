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


def test_render_markdown_escapes_plain_text_html_tags() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[
            Zone(
                kind=ZoneKind.BODY,
                content="<script>alert(1)</script>\n<img src=x onerror=alert(2)>",
            ),
        ]
    )

    rendered = render_markdown(parsed, threaded)

    assert "<script>" not in rendered.body
    assert "<img" not in rendered.body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered.body
    assert "&lt;img src=x onerror=alert(2)&gt;" in rendered.body


def test_render_markdown_preserves_plain_text_markdown_code_regions() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[
            Zone(
                kind=ZoneKind.BODY,
                content=(
                    "Inline code keeps `<div>` literal.\n\n"
                    "```html\n"
                    "<script>alert('in code')</script>\n"
                    "<div>fixture</div>\n"
                    "```\n\n"
                    "Outside code is still <img src=x onerror=alert(2)>."
                ),
            ),
        ]
    )

    rendered = render_markdown(parsed, threaded)

    assert "`<div>`" in rendered.body
    assert "```html\n<script>alert('in code')</script>\n<div>fixture</div>\n```" in rendered.body
    assert "&lt;img src=x onerror=alert(2)&gt;" in rendered.body
    assert "<img" not in rendered.body


def test_render_structured_escapes_plain_text_thread_metadata_and_body() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[
            Zone(kind=ZoneKind.BODY, content="Latest body"),
            Zone(
                kind=ZoneKind.QUOTED,
                content="<img src=x onerror=alert(2)>",
                metadata={
                    "attribution_from": "<b>Alice</b>",
                    "attribution_date": "<script>alert(1)</script>",
                },
            ),
        ]
    )

    rendered = render_markdown(
        parsed, threaded, options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED)
    )

    assert "## From &lt;b&gt;Alice&lt;/b&gt; (&lt;script&gt;alert(1)&lt;/script&gt;)" in rendered.body
    assert "&lt;img src=x onerror=alert(2)&gt;" in rendered.body
    assert "<b>Alice</b>" not in rendered.body
    assert "<img" not in rendered.body


def test_serialize_markdown_emits_yaml_front_matter() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(zones=[Zone(kind=ZoneKind.BODY, content="Only body")])

    document = serialize_markdown(render_markdown(parsed, threaded))

    assert document.startswith("---\n")
    assert "subject: Plain Text Fixture" in document
    assert "Only body" in document


def test_serialize_markdown_body_hr_does_not_create_second_front_matter() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(zones=[Zone(kind=ZoneKind.BODY, content="---\nnot yaml")])

    document = serialize_markdown(render_markdown(parsed, threaded))

    parts = document.split("---\n", 2)
    assert len(parts) == 3
    assert parts[0] == ""
    assert parts[2] == "\n---\nnot yaml\n"


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
    assert "## From Alice &lt;alice@example.com&gt; (Mar 5, 2026)" in rendered.body
    assert "## From Bob &lt;bob@example.com&gt; (Mar 4, 2026)" in rendered.body
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


from dead_letter.core.types import ThreadOrder


def _two_prior_threaded() -> ThreadedContent:
    return ThreadedContent(
        zones=[
            Zone(kind=ZoneKind.BODY, content="Latest"),
            Zone(
                kind=ZoneKind.QUOTED,
                content="middle body",
                metadata={"attribution_from": "Middle"},
            ),
            Zone(
                kind=ZoneKind.QUOTED,
                content="oldest body",
                metadata={"attribution_from": "Oldest"},
            ),
        ]
    )


def test_render_structured_oldest_first_reverses_zone_order() -> None:
    parsed = _parsed_email()
    rendered = render_markdown(
        parsed,
        _two_prior_threaded(),
        options=ConvertOptions(
            thread_mode=ThreadMode.STRUCTURED, thread_order=ThreadOrder.OLDEST_FIRST
        ),
    )

    oldest = rendered.body.index("## From Oldest")
    middle = rendered.body.index("## From Middle")
    assert oldest < middle


def test_render_structured_latest_first_preserves_zone_order() -> None:
    parsed = _parsed_email()
    rendered = render_markdown(
        parsed,
        _two_prior_threaded(),
        options=ConvertOptions(
            thread_mode=ThreadMode.STRUCTURED, thread_order=ThreadOrder.LATEST_FIRST
        ),
    )

    middle = rendered.body.index("## From Middle")
    oldest = rendered.body.index("## From Oldest")
    assert middle < oldest


def test_render_structured_uses_quoted_original_in_fallback_case() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[
            Zone(
                kind=ZoneKind.QUOTED,
                content="stripped post-annotation body",
                metadata={
                    "_quoted_original": "On X wrote:\nstripped post-annotation body",
                    "attribution_from": "X",
                },
            )
        ]
    )

    rendered = render_markdown(
        parsed, threaded, options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED)
    )

    assert "On X wrote:\nstripped post-annotation body" in rendered.body
    assert "## From X" not in rendered.body
    assert "thread_messages" not in rendered.front_matter


def test_render_structured_skips_empty_quoted_zones() -> None:
    parsed = _parsed_email()
    threaded = ThreadedContent(
        zones=[
            Zone(kind=ZoneKind.BODY, content="Latest body"),
            Zone(
                kind=ZoneKind.QUOTED,
                content="",
                metadata={"attribution_from": "Alice"},
            ),
            Zone(
                kind=ZoneKind.QUOTED,
                content="real body",
                metadata={"attribution_from": "Bob"},
            ),
        ]
    )

    rendered = render_markdown(
        parsed, threaded, options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED)
    )

    assert "## From Alice" not in rendered.body
    assert "## From Bob" in rendered.body
    assert rendered.front_matter["thread_messages"] == 1


def test_render_structured_fallback_byte_identical_to_latest() -> None:
    parsed = _parsed_email()
    original = "On X wrote:\nbody"
    structured_zones = ThreadedContent(
        zones=[
            Zone(
                kind=ZoneKind.QUOTED,
                content="body",
                metadata={"_quoted_original": original, "attribution_from": "X"},
            )
        ]
    )
    latest_zones = ThreadedContent(
        zones=[Zone(kind=ZoneKind.QUOTED, content=original)]
    )

    structured = render_markdown(
        parsed, structured_zones, options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED)
    )
    latest = render_markdown(parsed, latest_zones, options=ConvertOptions())

    assert structured.body == latest.body
