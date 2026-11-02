"""HTML sanitization for safe markdown conversion."""

from __future__ import annotations

import re

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

# These containers lose their tags, but must not join adjacent rendered lines.
_STRIPPED_BLOCK_TAGS = (
    "address", "article", "aside", "caption", "center", "dd", "details", "dialog",
    "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "header",
    "legend", "main", "menu", "nav", "section", "summary",
)
_STRIPPED_BLOCK_RE = re.compile(
    r"<(/?)(" + "|".join(_STRIPPED_BLOCK_TAGS) + r")>"
)


def sanitize_html(html: str) -> str:
    """Sanitize incoming HTML while preserving quote-detection metadata."""
    if not html:
        return ""

    cleaner = nh3.Cleaner(
        tags=_ALLOWED_TAGS | set(_STRIPPED_BLOCK_TAGS),
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        strip_comments=True,
    )
    cleaned = cleaner.clean(html)
    # nh3 emits these tags attribute-free and escapes literal '<' characters.
    normalized = _STRIPPED_BLOCK_RE.sub(r"<\1div>", cleaned)
    # Renaming caption/form inside a table requires HTML foster parenting.
    return cleaner.clean(normalized) if normalized != cleaned else cleaned
