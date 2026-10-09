"""Regression guards for parser-sensitive behavior on selectolax's Lexbor backend.

selectolax 1.0 removed the Modest backend. These cases pin the tree behavior
the core relies on (attribute reconstruction, comment and text-node handling,
HTML5 error recovery, deletion during traversal, and node identity across
separate queries) so a parser change shows up as a test failure.
"""

from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

from dead_letter.core.html import html_has_italic_nodes, html_to_markdown, unwrap_italic_tags
from dead_letter.core.html_conversation import (
    _iter_nodes_in_document_order,
    _split_node_html,
    _wrap_node_html,
    segment_html_conversation,
)
from dead_letter.core.image_filter import filter_images
from dead_letter.core.quotes import detect_quote_patterns
from dead_letter.core.types import StrippedImageCategory, ZoneKind


def _zones(result) -> list[tuple[ZoneKind, str]]:
    return [(zone.kind, zone.content) for zone in result.zones]


def test_wrap_node_html_keeps_namespaced_and_drops_empty_attributes() -> None:
    tree = LexborHTMLParser('<div xml:lang="en" data-empty="" title="a&quot;b<" hidden>x</div>')
    node = tree.css_first("div")

    # Lexbor reports valueless attributes as None and empty ones as "".
    # Both are dropped, matching the output from selectolax's Modest backend.
    assert node.attributes["data-empty"] == ""
    assert node.attributes["hidden"] is None
    assert _wrap_node_html(node, "inner") == (
        '<div xml:lang="en" title="a&quot;b&lt;">inner</div>'
    )


def test_outlook_split_rebuilds_wrapper_without_empty_class() -> None:
    html = (
        '<html><body><div>Top reply</div><div class=""><div class="outer"><p>keep</p>'
        '<div id="divRplyFwdMsg">Original</div><p>After</p></div></div></body></html>'
    )

    result = segment_html_conversation(html, client_hint="outlook")

    assert _zones(result) == [
        (ZoneKind.BODY, '<body><div>Top reply</div><div><div class="outer"><p>keep</p></div></div></body>'),
        (
            ZoneKind.QUOTED,
            '<body><div><div class="outer"><div id="divRplyFwdMsg">Original</div>'
            "<p>After</p></div></div></body>",
        ),
    ]


def test_tracking_pixel_with_empty_and_namespaced_dimension_attributes() -> None:
    html = (
        '<img src="https://t.example.test/open.gif" width="" height="1">'
        '<img src="https://t.example.test/vml.gif" v:shapes="_x0000_i1025" width="1" height="1">'
        '<img src="https://cdn.example.test/photo.png" o:width="1" width="300" height="200">'
    )

    result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)

    assert [(s.reason, s.reference) for s in stripped] == [
        ("dimension_heuristic", "https://t.example.test/open.gif"),
        ("dimension_heuristic", "https://t.example.test/vml.gif"),
    ]
    assert "photo.png" in result


def test_comment_before_reply_attribution_is_still_detected() -> None:
    html = "<div><!-- client marker -->On Mon, Jan 1, 2024, Alice &lt;a@example.test&gt; wrote:</div>"

    assert detect_quote_patterns(html) == {"generic"}


def test_comment_between_signature_wrapper_and_trailing_image() -> None:
    html = (
        '<div class="gmail_signature"><div class="moz-signature"><img src="cid:logo"></div></div>'
        '<!-- note --><img src="https://x.example.test/a.png"><p>Body text</p>'
        '<img src="https://x.example.test/b.png">'
    )

    result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=True)

    # Overlapping wrappers record the inner image once; the walk crosses the
    # comment node and stops at the first block with text.
    assert [(s.category, s.reason, s.reference) for s in stripped] == [
        (StrippedImageCategory.SIGNATURE_IMAGE, "gmail_signature_wrapper", "cid:logo"),
        (StrippedImageCategory.SIGNATURE_IMAGE, "structural_boundary_extension", "https://x.example.test/a.png"),
    ]
    assert result == (
        '<body><div class="gmail_signature"><div class="moz-signature"></div></div>'
        '<!-- note --><p>Body text</p><img src="https://x.example.test/b.png"></body>'
    )


def test_adjacent_signature_images_are_all_removed_while_walking_siblings() -> None:
    html = (
        '<div class="gmail_signature">Sig</div>'
        '<img src="https://x.example.test/1.png"> <img src="https://x.example.test/2.png">'
        '<img src="https://x.example.test/3.png"><p>Body</p>'
    )

    result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)

    assert [s.reference for s in stripped] == [
        "https://x.example.test/1.png",
        "https://x.example.test/2.png",
        "https://x.example.test/3.png",
    ]
    assert result == '<body><div class="gmail_signature">Sig</div> <p>Body</p></body>'


def test_legacy_forward_after_whitespace_text_nodes() -> None:
    html = '<div class="gmail_quote">\n  \n---------- Forwarded message ---------<br>From: A</div>'

    result = segment_html_conversation(html)

    assert _zones(result) == [
        (ZoneKind.BODY, "<body></body>"),
        (
            ZoneKind.FORWARDED_BODY,
            '<div class="gmail_quote">\n  \n---------- Forwarded message ---------<br>From: A</div>',
        ),
    ]
    assert result.rules_triggered == ["gmail_forward"]


def test_gmail_attr_first_line_joins_split_text_nodes() -> None:
    html = (
        "<div>Reply</div>"
        '<div class="gmail_quote"><div class="gmail_attr">On Mon, <span>Alice</span>&nbsp;wrote:<br>'
        "extra</div>Quoted text</div>"
    )

    result = segment_html_conversation(html)

    # "wrote:" across three text nodes classifies the block as a reply, not a forward.
    assert [kind for kind, _ in _zones(result)] == [ZoneKind.BODY, ZoneKind.QUOTED]
    assert result.rules_triggered == ["gmail_quote"]


def test_malformed_table_foster_parents_stray_text() -> None:
    result = html_to_markdown("<table>stray text<tr><td>cell one<td>cell two</table><p>after</p>")

    assert result.markdown == (
        "stray text\n\n| cell one | cell two |\n| -------- | -------- |\n\nafter"
    )


def test_malformed_table_images_are_filtered_after_foster_parenting() -> None:
    html = (
        '<table>stray<img src="https://t.example.test/p.gif" width="1" height="1">'
        '<tr><td>cell one<td><img src="https://t.example.test/q.gif" width=0 height=0>cell two'
        "</table><p>after</p>"
    )

    result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)

    assert [s.reference for s in stripped] == [
        "https://t.example.test/p.gif",
        "https://t.example.test/q.gif",
    ]
    assert result == (
        "<body>stray<table><tbody><tr><td>cell one</td><td>cell two</td></tr></tbody></table>"
        "<p>after</p></body>"
    )


def test_nested_and_misnested_italics_are_unwrapped() -> None:
    html = "<p><i>a<i>b</i>c</i> <i><b>x</i>y</b></p>"

    assert html_has_italic_nodes(html) is True
    assert html_has_italic_nodes("<p><b>plain</b></p>") is False
    assert unwrap_italic_tags(html) == "<html><head></head><body><p>abc <b>x</b><b>y</b></p></body></html>"


def test_node_identity_holds_across_separate_queries() -> None:
    tree = LexborHTMLParser('<div><p>a</p><div id="divRplyFwdMsg">q</div></div><img src="x">')
    queried = tree.css_first("#divRplyFwdMsg")
    walked = next(
        node for node in _iter_nodes_in_document_order(tree.body) if node.attributes.get("id") == "divRplyFwdMsg"
    )

    # Each query returns a new Python wrapper; mem_id identifies the DOM node.
    assert walked is not queried
    assert walked.mem_id == queried.mem_id
    assert tree.css("img")[0].mem_id == tree.css("img")[0].mem_id
    assert tree.css_first("p").mem_id != queried.mem_id

    body_html, quoted_html, found = _split_node_html(tree.body, queried.mem_id)
    assert found is True
    assert body_html == "<body><div><p>a</p></div></body>"
    assert quoted_html == '<body><div><div id="divRplyFwdMsg">q</div></div><img src="x"></body>'


def test_outlook_split_uses_first_of_identical_boundaries() -> None:
    html = (
        "<div>Reply</div>"
        '<div><div id="divRplyFwdMsg">Same</div><p>Older A</p></div>'
        '<div><div id="divRplyFwdMsg">Same</div><p>Older B</p></div>'
    )

    result = segment_html_conversation(html, client_hint="outlook")

    assert _zones(result) == [
        (ZoneKind.BODY, "<body><div>Reply</div></body>"),
        (
            ZoneKind.QUOTED,
            '<body><div><div id="divRplyFwdMsg">Same</div><p>Older A</p></div>'
            '<div><div id="divRplyFwdMsg">Same</div><p>Older B</p></div></body>',
        ),
    ]
