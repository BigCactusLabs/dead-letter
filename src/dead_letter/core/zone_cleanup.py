"""Cleanup helpers for typed conversation zones."""

from __future__ import annotations

import re

from dead_letter.core.forwarding import (
    FORWARD_MARKER_RE,
    followed_by_header_line,
    normalize_marker_text,
)
from dead_letter.core.types import ConversationZone, ConvertOptions, ZoneKind

# html-to-markdown 3.17+ escapes a leading "--" as "\--", and a <br> after the
# delimiter renders as a Markdown hard break (two trailing spaces).
_SIGNATURE_RE = re.compile(r"\n\\?--[ \t]*\n.*$", re.DOTALL)
_DISCLAIMER_RE = re.compile(r"\n(?:confidentiality notice|disclaimer):?.*$", re.DOTALL | re.IGNORECASE)


def _forward_segments(content: str) -> list[str]:
    """Split ``content`` before each forward separator, ``>``-quoted or not,
    whose next non-blank line is a header line.

    Only the header check applies. The reply-boundary rules of segmentation
    (``text_conversation``) decide whether a forward starts a new zone; here
    the content is already one zone, and a separator after a line ending in
    ``:`` (such as a signature's ``Contact:``) still opens a forward.
    """
    normalized = normalize_marker_text(content)
    starts = [
        match.start()
        for match in FORWARD_MARKER_RE.finditer(normalized)
        if followed_by_header_line(normalized, match.end(), quoted=bool(match.group("quote")))
    ]
    bounds = [0, *[start for start in starts if start > 0], len(content)]
    return [content[begin:end] for begin, end in zip(bounds, bounds[1:])]


def _strip_content(content: str, options: ConvertOptions) -> str:
    """Strip signatures and disclaimers within each forwarded section.

    The patterns run to the end of their input, so each section between
    forward separators is stripped on its own; a ``-- `` line in one forward
    never removes a later forward.
    """
    if not (options.strip_signatures or options.strip_disclaimers):
        return content.strip()
    segments = _forward_segments(content)
    for index, segment in enumerate(segments):
        value = segment
        if options.strip_signatures:
            value = _SIGNATURE_RE.sub("", value)
        if options.strip_disclaimers:
            value = _DISCLAIMER_RE.sub("", value)
        if value != segment and index + 1 < len(segments):
            value = value.rstrip() + "\n\n"
        segments[index] = value
    return "".join(segments).strip()


def cleanup_zones(zones: list[ConversationZone], options: ConvertOptions) -> list[ConversationZone]:
    """Apply configured cleanup rules to typed zones."""
    cleaned: list[ConversationZone] = []

    for zone in zones:
        content = zone.content
        if zone.kind is ZoneKind.QUOTED and options.strip_quoted_headers:
            lines = content.splitlines()
            if lines and lines[0].lower().startswith("on ") and lines[0].lower().endswith("wrote:"):
                content = "\n".join(lines[1:])

        content = _strip_content(content, options)
        if not content:
            continue

        cleaned.append(
            ConversationZone(
                kind=zone.kind,
                content=content,
                source_kind=zone.source_kind,
                client_hint=zone.client_hint,
                confidence=zone.confidence,
                metadata=zone.metadata,
            )
        )

    return cleaned
