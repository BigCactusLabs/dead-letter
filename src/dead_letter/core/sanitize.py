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


# Preserve nh3 0.3.7's generic attributes per existing tag without Python callbacks.
_CLEANER = nh3.Cleaner(
    tags=_ALLOWED_TAGS | set(_STRIPPED_BLOCK_TAGS),
    attributes={
        "*": set(),
        **{tag: _ALLOWED_ATTRIBUTES.get(tag, set()) | {"title", "lang"} for tag in _ALLOWED_TAGS},
    },
    url_schemes=_ALLOWED_URL_SCHEMES,
    strip_comments=True,
)


def sanitize_html(html: str) -> str:
    """Sanitize incoming HTML while preserving quote-detection metadata."""
    if not html:
        return ""

    cleaned = _CLEANER.clean(html)
    # The explicit attribute policy leaves transient tags bare; nh3 escapes '<'.
    normalized = _STRIPPED_BLOCK_RE.sub(r"<\1div>", cleaned)
    # Renaming caption/form inside a table requires HTML foster parenting.
    return _CLEANER.clean(normalized) if normalized != cleaned else cleaned
