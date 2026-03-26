from __future__ import annotations

from dead_letter.core.html_conversation import segment_html_conversation
from dead_letter.core.types import ZoneKind


def test_segment_html_conversation_extracts_gmail_body_before_quote() -> None:
    html = '<div>Latest response</div><div class="gmail_quote">On prior mail wrote: ...</div>'

    result = segment_html_conversation(html, client_hint="gmail")

    assert result.zones[0].kind is ZoneKind.BODY
    assert "<div>Latest response</div>" in result.zones[0].content
    assert any(
        zone.kind is ZoneKind.QUOTED and 'class="gmail_quote"' in zone.content and "On prior mail wrote" in zone.content
        for zone in result.zones
    )


def test_segment_html_conversation_extracts_outlook_reply_before_divrplyfwdmsg() -> None:
    html = '<html><body><div>Top reply</div><div id="divRplyFwdMsg">Original message content</div></body></html>'

    result = segment_html_conversation(html, client_hint="outlook")

    assert result.zones[0].kind is ZoneKind.BODY
    assert "<div>Top reply</div>" in result.zones[0].content
    assert any(
        zone.kind is ZoneKind.QUOTED and 'id="divRplyFwdMsg"' in zone.content and "Original message content" in zone.content
        for zone in result.zones
    )


def test_segment_html_conversation_removes_outlook_trailing_quoted_siblings() -> None:
    html = (
        "<html><body>"
        "<div>Top reply</div>"
        '<div id="divRplyFwdMsg">From: Person</div>'
        "<div>Older thread line 1</div>"
        "<div>Older thread line 2</div>"
        "</body></html>"
    )

    result = segment_html_conversation(html, client_hint="outlook")

    assert result.zones[0].kind is ZoneKind.BODY
    assert "<div>Top reply</div>" in result.zones[0].content
    assert "Older thread line 1" not in result.zones[0].content
    assert "Older thread line 2" not in result.zones[0].content
    assert any(
        zone.kind is ZoneKind.QUOTED
        and 'id="divRplyFwdMsg"' in zone.content
        and "Older thread line 1" in zone.content
        and "Older thread line 2" in zone.content
        for zone in result.zones
    )


def test_segment_html_conversation_removes_nested_outlook_quoted_containers() -> None:
    html = (
        "<html><body>"
        "<div>Top reply</div>"
        '<table><tr><td><div id="divRplyFwdMsg">From: Person</div></td></tr></table>'
        "<table><tr><td>Older thread line 1</td></tr></table>"
        "<table><tr><td>Older thread line 2</td></tr></table>"
        "</body></html>"
    )

    result = segment_html_conversation(html, client_hint="outlook")

    assert result.zones[0].kind is ZoneKind.BODY
    assert "<div>Top reply</div>" in result.zones[0].content
    assert "Older thread line 1" not in result.zones[0].content
    assert "Older thread line 2" not in result.zones[0].content
    assert any(
        zone.kind is ZoneKind.QUOTED
        and 'id="divRplyFwdMsg"' in zone.content
        and "Older thread line 1" in zone.content
        and "Older thread line 2" in zone.content
        for zone in result.zones
    )


def test_segment_html_conversation_handles_boolean_html_attributes() -> None:
    """Regression: selectolax returns None for boolean attributes; escape(None) crashed."""
    html = (
        "<html><body>"
        "<div contenteditable>Top reply</div>"
        '<div id="divRplyFwdMsg">From: Person</div>'
        "</body></html>"
    )
    result = segment_html_conversation(html, client_hint="outlook")
    assert result.zones[0].kind is ZoneKind.BODY
    assert "Top reply" in result.zones[0].content
