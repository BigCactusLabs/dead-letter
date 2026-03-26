"""HTML sanitization for safe markdown conversion."""

from __future__ import annotations

import nh3

_ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    # Required for downstream quote detection patterns.
    "div": {"class", "id"},
    "blockquote": {"class", "id", "type"},
    "span": {"id"},
    "hr": {"style"},
}

_ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "cid"}


def sanitize_html(html: str) -> str:
    """Sanitize incoming HTML while preserving quote-detection metadata."""
    if not html:
        return ""

    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        strip_comments=True,
    )
