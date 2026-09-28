"""Compatibility wrapper around conversation segmentation utilities."""

from __future__ import annotations

from dataclasses import replace

from dead_letter.core.attribution import annotate_quoted_zones
from dead_letter.core.text_conversation import segment_text_conversation
from dead_letter.core.types import ConvertOptions, ThreadedContent, ThreadMode, Zone
from dead_letter.core.zone_cleanup import cleanup_zones


def build_zones(
    plain_text: str,
    *,
    quote_patterns: set[str] | None = None,
    options: ConvertOptions | None = None,
    source_kind: str = "plain",
) -> ThreadedContent:
    """Split message text into body and quoted zones."""
    opts = options or ConvertOptions()
    result = segment_text_conversation(
        plain_text, split_forwards=opts.thread_mode is ThreadMode.STRUCTURED,
    )
    # Label zones with the real source before annotation: forward headings
    # unwrap Markdown emphasis only in HTML-derived text.
    zones_in = [replace(zone, source_kind=source_kind) for zone in result.zones]
    annotated = annotate_quoted_zones(zones_in, opts)
    cleaned = cleanup_zones(annotated, opts)

    zones: list[Zone] = []
    for zone in cleaned:
        metadata = dict(zone.metadata)
        if zone.kind.value == "body" and quote_patterns:
            metadata["quote_patterns"] = ",".join(sorted(quote_patterns))
        zones.append(
            Zone(
                kind=zone.kind,
                content=zone.content,
                source_kind=source_kind,
                metadata=metadata,
            )
        )

    return ThreadedContent(zones=zones)
