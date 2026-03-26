from __future__ import annotations

from dead_letter.core.text_conversation import segment_text_conversation
from dead_letter.core.types import ConvertOptions, ZoneKind
from dead_letter.core.zone_cleanup import cleanup_zones


def test_segment_text_conversation_splits_body_and_quoted_text() -> None:
    text = "Hello team\n\nOn Thu someone wrote:\n> older context\n"

    result = segment_text_conversation(text)

    assert result.zones[0].kind is ZoneKind.BODY
    assert "Hello team" in result.zones[0].content
    assert any(zone.kind is ZoneKind.QUOTED for zone in result.zones)


def test_cleanup_zones_removes_signature_candidates_when_requested() -> None:
    result = segment_text_conversation("Body line\n--\nSignature")

    cleaned = cleanup_zones(result.zones, ConvertOptions(strip_signatures=True))

    assert all("Signature" not in zone.content for zone in cleaned)


def test_segment_text_conversation_preserves_forwarded_content_as_forward_zones() -> None:
    text = (
        "---------- Forwarded message ----------\n"
        "From: Vendor <vendor@example.net>\n"
        "Subject: Vendor Note\n\n"
        "Please review the attached quote.\n"
    )

    result = segment_text_conversation(text)

    assert result.zones[0].kind is ZoneKind.FORWARD_HEADER
    assert "Forwarded message" in result.zones[0].content
    assert any(
        zone.kind is ZoneKind.FORWARDED_BODY and "Please review the attached quote." in zone.content
        for zone in result.zones
    )
