"""Cleanup helpers for typed conversation zones."""

from __future__ import annotations

import re

from dead_letter.core.text_conversation import forward_matches
from dead_letter.core.types import ConversationZone, ConvertOptions, ZoneKind

# html-to-markdown 3.17+ escapes a leading "--" as "\--", and a <br> after the
# delimiter renders as a Markdown hard break (two trailing spaces).
_SIGNATURE_RE = re.compile(r"\n\\?--[ \t]*\n.*$", re.DOTALL)
_DISCLAIMER_RE = re.compile(r"\n(?:confidentiality notice|disclaimer):?.*$", re.DOTALL | re.IGNORECASE)


def _forward_segments(content: str) -> list[str]:
    """Split ``content`` before each forward separator that segmentation
    would accept: one followed by a header line (``forward_matches``)."""
    starts = [match.start() for match in forward_matches(content, quoted_forwards=True)]
    bounds = [0, *[start for start in starts if start > 0], len(content)]
    return [content[begin:end] for begin, end in zip(bounds, bounds[1:])]


def _strip_content(content: str, options: ConvertOptions) -> str:
    """Strip signatures and disclaimers within each forwarded section.

    The patterns run to the end of their input, so each section between
    forward separators is stripped on its own; a ``-- `` line in one forward
    never removes a later forward.
    """
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
