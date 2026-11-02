from __future__ import annotations

import pytest

from dead_letter.core.sanitize import sanitize_html


def test_sanitize_html_blocks_unsafe_schemes_and_scripts() -> None:
    html = (
        '<div><a href="javascript:alert(1)">bad</a>'
        '<a href="https://example.com">good</a>'
        '<img src="file:///etc/passwd" alt="x"/>'
        '<script>alert(1)</script></div>'
    )

    cleaned = sanitize_html(html)

    assert "javascript:" not in cleaned
    assert "<script" not in cleaned
    assert 'href="https://example.com"' in cleaned
    assert "file:///" not in cleaned


def test_sanitize_html_preserves_quote_detection_attributes() -> None:
    html = '<div class="gmail_quote" id="dq">x</div><blockquote type="cite" class="y">z</blockquote>'

    cleaned = sanitize_html(html)

    assert 'class="gmail_quote"' in cleaned
    assert 'id="dq"' in cleaned
    assert 'type="cite"' in cleaned


def test_sanitize_html_allows_cid_and_mailto_links() -> None:
    html = '<a href="mailto:person@example.com">mail</a><img src="cid:image1" alt="logo" />'

    cleaned = sanitize_html(html)

    assert 'href="mailto:person@example.com"' in cleaned
    assert 'src="cid:image1"' in cleaned


@pytest.mark.parametrize("tag", [
    "address", "article", "aside", "center", "dd", "details", "dialog", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "header", "legend",
    "main", "menu", "nav", "section", "summary",
])
def test_sanitize_html_preserves_stripped_block_boundaries(tag) -> None:
    html = f'Before<{tag} class="gmail_quote" onclick="alert(1)">Inside</{tag}>After'

    assert sanitize_html(html) == "Before<div>Inside</div>After"


def test_sanitize_html_preserves_caption_boundary() -> None:
    assert sanitize_html("<table><caption>Caption</caption></table>") == "<div>Caption</div><table></table>"


def test_sanitize_html_keeps_inline_tags_inline() -> None:
    assert sanitize_html("Be<small>fore</small><wbr>After") == "BeforeAfter"


def test_sanitize_html_cleans_unsafe_content_inside_stripped_blocks() -> None:
    html = (
        '<center id="divRplyFwdMsg" onclick="alert(1)">Safe'
        '<details><summary>Summary</summary><script>alert(1)</script>'
        '<a href="javascript:alert(1)">bad</a><img src="file:///etc/passwd">'
        '</details></center>'
    )

    cleaned = sanitize_html(html)

    assert "Safe<div><div>Summary</div>" in cleaned
    for unsafe in ("divRplyFwdMsg", "onclick", "<script", "alert(1)", "javascript:", "file:///"):
        assert unsafe not in cleaned


def test_sanitize_html_does_not_rewrite_block_names_in_attributes_or_text() -> None:
    html = '<a title="<center>literal</center>">Link</a><center>&lt;details&gt;</center>'

    cleaned = sanitize_html(html)

    assert 'title="&lt;center&gt;literal&lt;/center&gt;"' in cleaned
    assert "<div>&lt;details&gt;</div>" in cleaned
    assert sanitize_html(cleaned) == cleaned


@pytest.mark.parametrize("namespace", ["svg", "math"])
@pytest.mark.parametrize("tag", ["style", "script", "iframe", "xmp", "noembed", "noframes"])
def test_sanitize_html_does_not_activate_foreign_raw_text(namespace, tag) -> None:
    payload = (
        '&lt;img src="https://tracker.example/p.gif?u=victim"&gt;'
        '&lt;p&gt;&lt;a href="https://evil.example/login"&gt;Reset your password&lt;/a&gt;&lt;/p&gt;'
    )
    html = f'<p>Hello</p><!-- <center> --><{namespace}><{tag}>{payload}</{tag}></{namespace}>'

    cleaned = sanitize_html(html)

    assert cleaned == "<p>Hello</p>"
    assert "<img" not in cleaned and "<a " not in cleaned


def test_sanitize_html_plaintext_does_not_gain_document_closing_tags() -> None:
    assert sanitize_html("<!-- <center> --><plaintext><b>p") == "&lt;b&gt;p"


def test_sanitize_html_strips_form_attributes_before_normalization() -> None:
    html = (
        '<form id="divRplyFwdMsg" class="gmail_quote" action="https://evil.example/" '
        'onsubmit="alert(1)">Text</form>'
    )

    assert sanitize_html(html) == "<div>Text</div>"
