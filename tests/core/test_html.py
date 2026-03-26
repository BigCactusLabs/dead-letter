from __future__ import annotations

from dead_letter.core.html import html_to_markdown


def test_html_to_markdown_sanitizes_and_converts() -> None:
    html = '<div><script>alert(1)</script><p>Hello <strong>World</strong></p></div>'

    result = html_to_markdown(html)

    assert "script" not in result.markdown.lower()
    assert "Hello" in result.markdown
    assert "World" in result.markdown


def test_html_to_markdown_collects_quote_patterns() -> None:
    html = '<div class="gmail_quote">Quoted</div>'

    result = html_to_markdown(html)

    assert "gmail" in result.quote_patterns


def test_html_to_markdown_optionally_keeps_raw_html() -> None:
    html = '<p>Body</p>'

    result = html_to_markdown(html, include_raw_html=True)

    assert result.raw_html is not None
    assert "<p>Body</p>" in result.raw_html
