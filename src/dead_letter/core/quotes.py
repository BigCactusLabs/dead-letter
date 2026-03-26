"""HTML quote-pattern detection via html-to-markdown visitor callbacks."""

from __future__ import annotations

import re

from html_to_markdown import convert_with_visitor

_ON_WROTE_RE = re.compile(r"\bon\s+.+\bwrote:\s*$", re.IGNORECASE)


class QuoteDetectorVisitor:
    """Collect quote patterns encountered during conversion traversal."""

    def __init__(self) -> None:
        self.patterns: set[str] = set()

    def visit_element_start(self, ctx: dict[str, object]) -> dict[str, str]:
        tag = str(ctx.get("tag_name", "")).lower()
        attrs = {str(k).lower(): str(v) for k, v in (ctx.get("attributes") or {}).items()}

        element_id = attrs.get("id", "").lower()
        element_type = attrs.get("type", "").lower()
        classes = {part.strip().lower() for part in attrs.get("class", "").split() if part.strip()}
        style = attrs.get("style", "").lower()

        if tag == "div" and ({"gmail_quote", "gmail_attr"} & classes):
            self.patterns.add("gmail")
        if tag == "blockquote" and "gmail_quote" in classes:
            self.patterns.add("gmail")

        if (tag == "div" and element_id == "divrplyfwdmsg") or (
            tag == "span" and element_id == "olk_src_body_section"
        ):
            self.patterns.add("outlook")

        if tag == "hr" and "border-top" in style and ("#b5c4df" in style or "#e1e1e1" in style):
            self.patterns.add("outlook")

        if tag == "div" and "yahoo_quoted" in classes:
            self.patterns.add("yahoo")

        if tag == "blockquote":
            self.patterns.add("generic")
            if element_type == "cite":
                self.patterns.add("thunderbird")
                self.patterns.add("apple_mail")

        return {"type": "continue"}

    def visit_text(self, _ctx: dict[str, object], text: str) -> dict[str, str]:
        if _ON_WROTE_RE.search(text.strip()):
            self.patterns.add("generic")
        return {"type": "continue"}


def detect_quote_patterns(html: str) -> set[str]:
    """Detect known quoted-content patterns in HTML email bodies."""
    if not html:
        return set()

    visitor = QuoteDetectorVisitor()
    # We intentionally use html-to-markdown visitor traversal so detection runs in
    # the same pass family as conversion.
    convert_with_visitor(html, visitor=visitor)
    return visitor.patterns
