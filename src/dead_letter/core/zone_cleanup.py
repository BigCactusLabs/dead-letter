"""Cleanup helpers for typed conversation zones."""

from __future__ import annotations

import re

from dead_letter.core.types import ConversationZone, ConvertOptions, ZoneKind

_SIGNATURE_RE = re.compile(r"\n-- ?\n.*$", re.DOTALL)
_DISCLAIMER_RE = re.compile(r"\n(?:confidentiality notice|disclaimer):?.*$", re.DOTALL | re.IGNORECASE)


def _strip_content(content: str, options: ConvertOptions) -> str:
    value = content
    if options.strip_signatures:
        value = _SIGNATURE_RE.sub("", value)
    if options.strip_disclaimers:
        value = _DISCLAIMER_RE.sub("", value)
    return value.strip()


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
