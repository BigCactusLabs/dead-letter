"""HTML quote-pattern detection."""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

_ON_WROTE_RE = re.compile(r"\bon\s+.+\bwrote:\s*$", re.IGNORECASE)


def _detect_element_patterns(tag: str, attrs: dict[str, str], patterns: set[str]) -> None:
    element_id = attrs.get("id", "").lower()
    element_type = attrs.get("type", "").lower()
    classes = {part.strip().lower() for part in attrs.get("class", "").split() if part.strip()}
    style = attrs.get("style", "").lower()

    if tag == "div" and ({"gmail_quote", "gmail_attr"} & classes):
        patterns.add("gmail")
    if tag == "blockquote" and "gmail_quote" in classes:
        patterns.add("gmail")

    if (tag == "div" and element_id == "divrplyfwdmsg") or (
        tag == "span" and element_id == "olk_src_body_section"
    ):
        patterns.add("outlook")

    if tag == "hr" and "border-top" in style and ("#b5c4df" in style or "#e1e1e1" in style):
        patterns.add("outlook")

    if tag == "div" and "yahoo_quoted" in classes:
        patterns.add("yahoo")

    if tag == "blockquote":
        patterns.add("generic")
        if element_type == "cite":
            patterns.add("thunderbird")
            patterns.add("apple_mail")


def detect_quote_patterns(html: str) -> set[str]:
    """Detect known quoted-content patterns in HTML email bodies."""
    if not html:
        return set()

    patterns: set[str] = set()
    parser = HTMLParser(html)
    root = parser.root
    if root is None:
        return patterns
    for node in root.traverse():
        tag = (node.tag or "").lower()
        attrs = {str(k).lower(): str(v) for k, v in node.attributes.items()}
        _detect_element_patterns(tag, attrs, patterns)
        if _ON_WROTE_RE.search(node.text(deep=False, strip=True)):
            patterns.add("generic")

    return patterns
