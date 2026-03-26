from __future__ import annotations

from dead_letter.core.threads import build_zones
from dead_letter.core.types import ConvertOptions, ZoneKind


def test_build_zones_splits_reply_and_quoted_text() -> None:
    text = "Hello team\n\nOn Thu someone wrote:\n> older context\n"

    threaded = build_zones(text)

    assert threaded.zones[0].kind is ZoneKind.BODY
    assert "Hello team" in threaded.zones[0].content
    assert any(zone.kind is ZoneKind.QUOTED for zone in threaded.zones)


def test_build_zones_strips_signature_when_requested() -> None:
    text = "Body line\n--\nSignature"

    threaded = build_zones(text, options=ConvertOptions(strip_signatures=True))

    assert all("Signature" not in zone.content for zone in threaded.zones)


def test_build_zones_strips_rfc3676_signature_with_trailing_space() -> None:
    """Regression: RFC 3676 signature delimiter is '-- \\n' (with trailing space)."""
    text = "Body line\n-- \nSignature"

    threaded = build_zones(text, options=ConvertOptions(strip_signatures=True))

    assert all("Signature" not in zone.content for zone in threaded.zones)
