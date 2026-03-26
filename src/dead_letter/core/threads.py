"""Compatibility wrapper around conversation segmentation utilities."""

from __future__ import annotations

from dead_letter.core.text_conversation import segment_text_conversation
from dead_letter.core.types import ConvertOptions, ThreadedContent, Zone
from dead_letter.core.zone_cleanup import cleanup_zones


def build_zones(
    plain_text: str,
    *,
    quote_patterns: set[str] | None = None,
    options: ConvertOptions | None = None,
) -> ThreadedContent:
    """Split message text into body and quoted zones."""
    opts = options or ConvertOptions()
    result = segment_text_conversation(plain_text)
    cleaned = cleanup_zones(result.zones, opts)

    zones: list[Zone] = []
    for zone in cleaned:
        metadata = dict(zone.metadata)
        if zone.kind.value == "body" and quote_patterns:
            metadata["quote_patterns"] = ",".join(sorted(quote_patterns))
        zones.append(Zone(kind=zone.kind, content=zone.content, metadata=metadata))

    return ThreadedContent(zones=zones)
