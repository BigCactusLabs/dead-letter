"""HTML normalization stage for conversion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from dead_letter.core._html_parser import parse_html
from dead_letter.core.html_to_markdown_adapter import convert_html_to_markdown
from dead_letter.core.quotes import detect_quote_patterns
from dead_letter.core.sanitize import sanitize_html


@dataclass(slots=True)
class HtmlConversionResult:
    markdown: str
    quote_patterns: set[str]
    raw_html: str | None
    warnings: list[dict[str, str]] = field(default_factory=list)


def html_to_markdown(html: str, *, include_raw_html: bool = False) -> HtmlConversionResult:
    """Sanitize then convert HTML body to markdown while collecting quote hints."""
    cleaned = sanitize_html(html)
    patterns = detect_quote_patterns(cleaned)

    markdown = ""
    warnings: list[dict[str, str]] = []
    if cleaned:
        markdown = convert_html_to_markdown(cleaned, warnings=warnings)

    return HtmlConversionResult(
        markdown=markdown,
        quote_patterns=patterns,
        raw_html=cleaned if include_raw_html else None,
        warnings=warnings,
    )


def html_has_italic_nodes(html: str) -> bool:
    cleaned = sanitize_html(html)
    if not cleaned:
        return False
    return parse_html(cleaned).css_first("i") is not None


def unwrap_italic_tags(html: str) -> str:
    cleaned = sanitize_html(html)
    if not cleaned:
        return ""

    parser = parse_html(cleaned)
    for node in list(parser.css("i")):
        node.unwrap()
    return parser.html or ""
