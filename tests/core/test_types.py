from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from dead_letter.core.types import (
    BodyCandidate,
    ConversationTrace,
    ConversationZone,
    ConvertOptions,
    ConvertResult,
    MimeModel,
    PartDefect,
    StrippedImage,
    StrippedImageCategory,
    Zone,
    ZoneKind,
)


def test_convert_result_normalizes_paths_to_absolute() -> None:
    result = ConvertResult(
        source=Path("fixtures/mail.eml"),
        output=Path("out/mail.md"),
        subject="Subject",
        sender="sender@example.com",
        date="2026-03-05T12:00:00+00:00",
        attachments=["agenda.pdf"],
        success=True,
        error=None,
        error_code=None,
        plain_text_fallback_available=None,
        dry_run=False,
    )

    assert result.source.is_absolute()
    assert result.output is not None
    assert result.output.is_absolute()


def test_convert_result_allows_dry_run_without_output() -> None:
    result = ConvertResult(
        source=Path("fixtures/mail.eml"),
        output=None,
        subject="Subject",
        sender="sender@example.com",
        date=None,
        attachments=[],
        success=True,
        error=None,
        error_code=None,
        plain_text_fallback_available=None,
        dry_run=True,
    )

    assert result.output is None
    assert result.dry_run is True


def test_zone_defaults_and_metadata_copy() -> None:
    metadata = {"client": "gmail"}
    zone = Zone(kind=ZoneKind.QUOTED, content="quoted text", metadata=metadata)

    assert zone.kind is ZoneKind.QUOTED
    assert zone.metadata["client"] == "gmail"

    metadata["client"] = "outlook"
    assert zone.metadata["client"] == "gmail"


def test_convert_options_contract_defaults() -> None:
    options = ConvertOptions()

    assert options.strip_signatures is False
    assert options.strip_disclaimers is False
    assert options.strip_quoted_headers is False
    assert options.embed_inline_images is False
    assert options.include_all_headers is False
    assert options.include_raw_html is False
    assert options.no_calendar_summary is False
    assert options.allow_fallback_on_html_error is False
    assert options.allow_html_repair_on_panic is False
    assert options.delete_eml is False
    assert options.dry_run is False


def test_convert_options_contract_fields() -> None:
    assert [field.name for field in fields(ConvertOptions)] == [
        "strip_signatures",
        "strip_disclaimers",
        "strip_quoted_headers",
        "strip_signature_images",
        "strip_tracking_pixels",
        "embed_inline_images",
        "include_all_headers",
        "include_raw_html",
        "no_calendar_summary",
        "allow_fallback_on_html_error",
        "allow_html_repair_on_panic",
        "delete_eml",
        "dry_run",
        "report",
    ]


def test_convert_result_exposes_structured_error_metadata() -> None:
    result = ConvertResult(
        source=Path("fixtures/mail.eml"),
        output=None,
        subject="Subject",
        sender="sender@example.com",
        date=None,
        attachments=[],
        success=False,
        error="html-to-markdown panic during conversion: boom",
        error_code="html_markdown_failed",
        plain_text_fallback_available=True,
        dry_run=False,
    )

    assert result.error_code == "html_markdown_failed"
    assert result.plain_text_fallback_available is True


def test_zone_kind_supports_forward_and_candidate_kinds() -> None:
    assert ZoneKind.FORWARD_HEADER.value == "forward_header"
    assert ZoneKind.FORWARDED_BODY.value == "forwarded_body"
    assert ZoneKind.SIGNATURE_CANDIDATE.value == "signature_candidate"
    assert ZoneKind.DISCLAIMER_CANDIDATE.value == "disclaimer_candidate"


def test_mime_model_copies_mutable_inputs() -> None:
    defect = PartDefect(part_id="1", code="bad_header", message="x", severity="warning")
    candidate = BodyCandidate(kind="html", content="<p>x</p>", source_part_id="html-1")
    model = MimeModel(parts=["1"], defects=[defect], body_candidates=[candidate])

    assert model.parts == ["1"]
    assert model.defects[0].code == "bad_header"
    assert model.body_candidates[0].source_part_id == "html-1"


def test_conversation_trace_defaults_are_stable() -> None:
    trace = ConversationTrace(selected_body_kind="html")

    assert trace.selected_body_kind == "html"
    assert trace.rules_triggered == []
    assert trace.fallback_used is None


def test_conversation_zone_copies_metadata() -> None:
    metadata = {"client_hint": "gmail"}

    zone = ConversationZone(
        kind=ZoneKind.BODY,
        content="Latest reply",
        source_kind="html",
        metadata=metadata,
    )

    metadata["client_hint"] = "outlook"

    assert zone.metadata["client_hint"] == "gmail"


def test_stripped_image_category_values() -> None:
    assert StrippedImageCategory.SIGNATURE_IMAGE == "signature_image"
    assert StrippedImageCategory.TRACKING_PIXEL == "tracking_pixel"


def test_stripped_image_dataclass() -> None:
    img = StrippedImage(
        category=StrippedImageCategory.SIGNATURE_IMAGE,
        reason="gmail_signature_wrapper",
        reference="cid:logo.png",
    )
    assert img.category == "signature_image"
    assert img.reason == "gmail_signature_wrapper"
    assert img.reference == "cid:logo.png"


def test_convert_options_image_stripping_defaults() -> None:
    opts = ConvertOptions()
    assert opts.strip_signature_images is False
    assert opts.strip_tracking_pixels is False
