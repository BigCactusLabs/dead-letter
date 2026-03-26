"""HTML normalization stage for conversion pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from html_to_markdown import ConversionOptions, convert
from selectolax.parser import HTMLParser

from dead_letter.core.quotes import detect_quote_patterns
from dead_letter.core.sanitize import sanitize_html


@dataclass(slots=True)
class HtmlConversionResult:
    markdown: str
    quote_patterns: set[str]
    raw_html: str | None


def html_to_markdown(html: str, *, include_raw_html: bool = False) -> HtmlConversionResult:
    """Sanitize then convert HTML body to markdown while collecting quote hints."""
    cleaned = sanitize_html(html)
    patterns = detect_quote_patterns(cleaned)

    markdown = ""
    if cleaned:
        markdown = convert(
            cleaned,
            options=ConversionOptions(
                heading_style="atx",
                code_block_style="backticks",
                output_format="markdown",
            ),
        ).strip()

    return HtmlConversionResult(
        markdown=markdown,
        quote_patterns=patterns,
        raw_html=cleaned if include_raw_html else None,
    )


def html_has_italic_nodes(html: str) -> bool:
    cleaned = sanitize_html(html)
    if not cleaned:
        return False
    return HTMLParser(cleaned).css_first("i") is not None


def unwrap_italic_tags(html: str) -> str:
    cleaned = sanitize_html(html)
    if not cleaned:
        return ""

    parser = HTMLParser(cleaned)
    for node in list(parser.css("i")):
        node.unwrap()
    return parser.html or ""
