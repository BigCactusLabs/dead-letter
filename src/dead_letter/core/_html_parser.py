"""Single construction point for selectolax Lexbor documents."""

from __future__ import annotations

from selectolax.lexbor import LexborDocumentOptions, LexborHTMLParser


def parse_html(html: str) -> LexborHTMLParser:
    """Parse ``html`` as a full document without Lexbor DOM events.

    Lexbor's default events mutate the tree while parsing (for example,
    cloning the selected ``<option>`` into ``<selectedcontent>``), which would
    duplicate email text. ``WO_EVENTS`` keeps the tree as written.
    """
    return LexborHTMLParser(html, options=LexborDocumentOptions.WO_EVENTS)
