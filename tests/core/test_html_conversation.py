from __future__ import annotations

from dead_letter.core.html_conversation import (
    _iter_nodes_in_document_order,
    segment_html_conversation,
)
from dead_letter.core.types import ZoneKind


def _deeply_nested_html(depth: int, quote_html: str) -> str:
    return "<div>Latest response</div>" + "<div>" * depth + quote_html + "</div>" * depth


def test_iter_nodes_in_document_order_short_circuits_over_wide_container() -> None:
    """An early match must not force-walk a wide container's siblings.

    Regression guard for issue #51 PR review r3582791847: the iterative
    traversal pushes sibling/child pointers lazily, so a caller that stops
    at the first matching child never reads past it. The previous version
    pre-loaded every sibling into a list before yielding any child.
    """
    sibling_next_reads = 0

    class _Node:
        def __init__(self, name):
            self.name = name
            self.child: object = None
            self._next: object = None

        @property
        def next(self):
            nonlocal sibling_next_reads
            sibling_next_reads += 1
            return self._next

        @next.setter
        def next(self, value):
            self._next = value

    nodes = [_Node("first")] + [_Node(f"sibling-{i}") for i in range(500)]
    for left, right in zip(nodes, nodes[1:]):
        left.next = right  # setter, not counted
    container = _Node("container")
    container.child = nodes[0]

    walk = _iter_nodes_in_document_order(container)
    assert next(walk).name == "container"
    assert next(walk).name == "first"
    # Boundary matched at the first child; nothing past it should be touched.
    # The old list-preloading traversal read every sibling's `.next` (~500).
    assert sibling_next_reads == 0


def test_segment_html_conversation_handles_deeply_nested_gmail_quote() -> None:
    html = _deeply_nested_html(2_000, '<div class="gmail_quote">Quoted message</div>')

    result = segment_html_conversation(html, client_hint="gmail")

    assert any(zone.kind is ZoneKind.BODY and "Latest response" in zone.content for zone in result.zones)
    assert any(zone.kind is ZoneKind.QUOTED and "Quoted message" in zone.content for zone in result.zones)


def test_segment_html_conversation_handles_deeply_nested_outlook_quote() -> None:
    html = _deeply_nested_html(2_000, '<div id="divRplyFwdMsg">Quoted message</div>')

    result = segment_html_conversation(html, client_hint="outlook")

    assert any(zone.kind is ZoneKind.BODY and "Latest response" in zone.content for zone in result.zones)
    assert any(zone.kind is ZoneKind.QUOTED and "Quoted message" in zone.content for zone in result.zones)


def test_segment_html_conversation_preserves_nested_outlook_split_output() -> None:
    html = (
        "<html><body><div>Top reply</div>"
        '<div><div><div id="divRplyFwdMsg">Original message</div></div>'
        "<div>Older thread line</div></div></body></html>"
    )

    result = segment_html_conversation(html, client_hint="outlook")

    body_zone, quoted_zone = result.zones
    assert body_zone.kind is ZoneKind.BODY
    assert body_zone.content == "<body><div>Top reply</div></body>"
    assert quoted_zone.kind is ZoneKind.QUOTED
    assert quoted_zone.content == (
        '<body><div><div><div id="divRplyFwdMsg">Original message</div></div>'
        "<div>Older thread line</div></div></body>"
    )


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


def test_segment_html_conversation_extracts_front_reply_before_blockquote() -> None:
    html = (
        "<html><body>"
        "<div>Latest Front response</div>"
        '<blockquote type="cite" class="front-blockquote">Older Front message</blockquote>'
        "</body></html>"
    )

    result = segment_html_conversation(html)

    assert result.rules_triggered == ["front_blockquote"]
    assert result.client_hint == "front"
    assert result.zones[0].kind is ZoneKind.BODY
    assert "Latest Front response" in result.zones[0].content
    assert "Older Front message" not in result.zones[0].content
    assert any(
        zone.kind is ZoneKind.QUOTED
        and 'class="front-blockquote"' in zone.content
        and "Older Front message" in zone.content
        for zone in result.zones
    )


def test_segment_html_conversation_handles_front_generated_classes_and_nested_quotes() -> None:
    html = (
        "<html><body>"
        "<div>Latest Front response</div>"
        '<img alt="Sent from Front" src="https://app.frontapp.com/seen.gif" '
        'style="width:1px;height:1px">'
        "<br>"
        '<blockquote type="cite" class="fa-731mdn fanx2yle front-blockquote">'
        "On November 30, 2023 someone wrote:"
        '<div id="divRplyFwdMsg">Nested Outlook quote header</div>'
        '<blockquote type="cite" class="front-blockquote">Nested Front quote</blockquote>'
        "</blockquote>"
        "</body></html>"
    )

    result = segment_html_conversation(html)

    assert result.client_hint == "front"
    assert result.rules_triggered == ["front_blockquote"]
    assert result.zones[0].kind is ZoneKind.BODY
    assert "Latest Front response" in result.zones[0].content
    assert "Nested Outlook quote header" not in result.zones[0].content
    assert any(
        zone.kind is ZoneKind.QUOTED
        and "Nested Outlook quote header" in zone.content
        and "Nested Front quote" in zone.content
        for zone in result.zones
    )


def test_segment_html_conversation_preserves_front_trailing_siblings_as_body() -> None:
    html = (
        "<html><body>"
        "<div>Latest Front response</div>"
        '<blockquote type="cite" class="front-blockquote">Older Front message</blockquote>'
        '<div class="moz-signature">Author signature after quote</div>'
        "</body></html>"
    )

    result = segment_html_conversation(html)

    assert result.client_hint == "front"
    assert "Latest Front response" in result.zones[0].content
    assert "Author signature after quote" in result.zones[0].content
    assert "Older Front message" not in result.zones[0].content
    assert any(
        zone.kind is ZoneKind.QUOTED
        and "Older Front message" in zone.content
        and "Author signature after quote" not in zone.content
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
