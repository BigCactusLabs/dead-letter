from __future__ import annotations

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
