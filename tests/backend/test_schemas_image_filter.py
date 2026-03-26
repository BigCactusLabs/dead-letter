from __future__ import annotations

from dead_letter.backend.schemas import JobOptions, QualityDiagnostics, StrippedImageInfo


def test_job_options_image_stripping_defaults() -> None:
    opts = JobOptions()
    assert opts.strip_signature_images is False
    assert opts.strip_tracking_pixels is False


def test_job_options_round_trip() -> None:
    opts = JobOptions(strip_signature_images=True, strip_tracking_pixels=True)
    dumped = opts.model_dump()
    assert dumped["strip_signature_images"] is True
    assert dumped["strip_tracking_pixels"] is True


def test_stripped_image_info_model() -> None:
    info = StrippedImageInfo(
        category="signature_image",
        reason="gmail_signature_wrapper",
        reference="cid:logo.png",
    )
    assert info.category == "signature_image"


def test_quality_diagnostics_with_stripped_images() -> None:
    diag = QualityDiagnostics(
        state="normal",
        selected_body="html",
        segmentation_path="html",
        confidence="high",
        stripped_images=[
            StrippedImageInfo(
                category="tracking_pixel",
                reason="dimension_heuristic",
                reference="https://t.example.com/pixel.gif",
            )
        ],
    )
    assert len(diag.stripped_images) == 1


def test_quality_diagnostics_empty_stripped_images_default() -> None:
    diag = QualityDiagnostics(
        state="normal",
        selected_body="html",
        segmentation_path="html",
        confidence="high",
    )
    assert diag.stripped_images == []
