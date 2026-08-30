from __future__ import annotations

import base64
from email.message import EmailMessage
import threading
from pathlib import Path

import pytest

from dead_letter.core import BundleResult, ConvertOptions, convert_to_bundle
from dead_letter.core._pipeline import convert_to_bundle_with_diagnostics

# Decodes the base64 payload embedded in outlook_attachment_with_cid.eml.
EXPECTED_XLSX_BYTES = base64.b64decode(
    "UEsDBGRlYWQtbGV0dGVyIHJlZ3Jlc3Npb24geGxzeCBwYXlsb2FkAAECA/8="
)


def _front_matter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.find("\n---\n", 4)
    assert end != -1
    return __import__("yaml").safe_load(text[4:end])


def _write_inline_attachment_email(
    path: Path,
    *,
    filename: str,
    content_type: str,
    content_id: str,
    payload: bytes,
    html: str,
) -> Path:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["To"] = "recipient@example.com"
    message["Subject"] = "Inline attachment fixture"
    message.set_content("Please review the attached file.")
    message.add_alternative(html, subtype="html")
    maintype, subtype = content_type.split("/", 1)
    message.get_payload()[1].add_related(
        payload,
        maintype=maintype,
        subtype=subtype,
        cid=f"<{content_id}>",
        filename=filename,
        disposition="inline",
    )
    path.write_bytes(message.as_bytes())
    return path


def test_core_api_exports_bundle_converter() -> None:
    assert callable(convert_to_bundle)


def test_convert_to_bundle_writes_bundle_with_markdown_source_and_attachments(
    copy_fixture, tmp_path: Path
) -> None:
    source = copy_fixture("with_attachment.eml")
    cabinet = tmp_path / "cabinet"

    result = convert_to_bundle(source, bundle_root=cabinet)

    assert isinstance(result, BundleResult)
    assert result.success is True
    assert result.error is None
    assert result.bundle == cabinet / "with_attachment"
    assert result.markdown == result.bundle / "message.md"
    assert result.markdown is not None and result.markdown.exists()
    assert result.source_artifact == result.bundle / "with_attachment.eml"
    assert result.source_artifact is not None and result.source_artifact.exists()
    assert source.exists() is False
    assert result.attachments == [result.bundle / "attachments" / "agenda.txt"]
    assert result.attachments[0].read_text(encoding="utf-8") == "Team agenda\n- Item 1\n- Item 2\n"

    front = _front_matter(result.markdown)
    assert front["attachments"] == ["agenda.txt"]
    assert front["attachment_files"] == ["attachments/agenda.txt"]


def test_convert_to_bundle_respects_source_handling_modes(copy_fixture, tmp_path: Path) -> None:
    cabinet = tmp_path / "cabinet"

    moved = copy_fixture("plain_text.eml", "moved/plain_text.eml")
    moved_result = convert_to_bundle(moved, bundle_root=cabinet)
    assert moved.exists() is False
    assert moved_result.source_artifact == moved_result.bundle / "plain_text.eml"

    copied = copy_fixture("plain_text.eml", "copied/plain_text.eml")
    copied_result = convert_to_bundle(copied, bundle_root=cabinet, source_handling="copy")
    assert copied.exists() is True
    assert copied_result.source_artifact == copied_result.bundle / "plain_text.eml"

    deleted = copy_fixture("plain_text.eml", "deleted/plain_text.eml")
    deleted_result = convert_to_bundle(deleted, bundle_root=cabinet, source_handling="delete")
    assert deleted.exists() is False
    assert deleted_result.source_artifact is None


def test_convert_to_bundle_concurrent_same_stem_uses_distinct_bundles(
    copy_fixture, tmp_path: Path, monkeypatch
) -> None:
    cabinet = tmp_path / "cabinet"
    source_one = copy_fixture("plain_text.eml", "a/same.eml")
    source_two = copy_fixture("plain_text.eml", "b/same.eml")

    import dead_letter.core._pipeline as pipeline

    barrier = threading.Barrier(2)
    original = pipeline._collision_safe_bundle_dir

    def synchronized_bundle_dir(target: Path) -> Path:
        barrier.wait(timeout=1)
        return original(target)

    monkeypatch.setattr(pipeline, "_collision_safe_bundle_dir", synchronized_bundle_dir)

    results: list[BundleResult] = []
    failures: list[BaseException] = []

    def worker(source: Path) -> None:
        try:
            results.append(convert_to_bundle(source, bundle_root=cabinet, source_handling="copy"))
        except BaseException as exc:  # pragma: no cover - defensive worker capture
            failures.append(exc)

    threads = [
        threading.Thread(target=worker, args=(source_one,)),
        threading.Thread(target=worker, args=(source_two,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len(results) == 2
    assert all(result.success for result in results)
    assert sorted(path.name for path in cabinet.iterdir()) == ["same", "same-2"]


@pytest.mark.parametrize(
    ("fixture_name", "attachment_name"),
    [("with_inline_cid.eml", "logo.png"), ("calendar_invite.eml", "invite.ics")],
)
def test_convert_to_bundle_extracts_inline_and_calendar_attachments(
    copy_fixture, tmp_path: Path, fixture_name: str, attachment_name: str
) -> None:
    source = copy_fixture(fixture_name)

    result = convert_to_bundle(source, bundle_root=tmp_path / "cabinet", source_handling="copy")

    assert result.success is True
    assert [path.name for path in result.attachments] == [attachment_name]
    assert result.attachments[0].exists()
    assert result.markdown is not None
    front = _front_matter(result.markdown)
    assert front["attachment_files"] == [f"attachments/{attachment_name}"]


def test_convert_to_bundle_omits_stripped_inline_signature_attachments(
    copy_fixture, tmp_path: Path
) -> None:
    source = copy_fixture("with_inline_cid.eml")

    result = convert_to_bundle(
        source,
        bundle_root=tmp_path / "cabinet",
        source_handling="copy",
        options=ConvertOptions(strip_signature_images=True),
    )

    assert result.success is True
    assert result.attachments == []
    assert result.markdown is not None
    front = _front_matter(result.markdown)
    assert front["attachments"] == []
    assert "attachment_files" not in front
    assert "cid:logo.png" not in result.markdown.read_text(encoding="utf-8")


def test_convert_to_bundle_retains_attachment_with_content_id(
    copy_fixture, tmp_path: Path
) -> None:
    # Outlook/Exchange stamps a Content-ID on real disposition=attachment parts.
    # The inline-image retention pass must not mistake that for an unreferenced
    # inline image and drop it.
    source = copy_fixture("outlook_attachment_with_cid.eml")

    result = convert_to_bundle(
        source,
        bundle_root=tmp_path / "cabinet",
        source_handling="copy",
        options=ConvertOptions(strip_signature_images=True),
    )

    assert result.success is True
    assert [path.name for path in result.attachments] == ["report.xlsx"]
    assert result.attachments[0].read_bytes() == EXPECTED_XLSX_BYTES

    front = _front_matter(result.markdown)
    assert front["attachments"] == ["report.xlsx"]
    assert front["attachment_files"] == ["attachments/report.xlsx"]
    # The inline signature image is still stripped, not retained as an attachment.
    assert "logo.png" not in [path.name for path in result.attachments]


def test_convert_to_bundle_retains_inline_non_image_attachment(tmp_path: Path) -> None:
    source = _write_inline_attachment_email(
        tmp_path / "apple.eml",
        filename="invoice-q3.pdf",
        content_type="application/pdf",
        content_id="invoice-q3",
        payload=b"%PDF-fake-invoice",
        html="<html><body><p>The invoice is attached.</p></body></html>",
    )

    result = convert_to_bundle(
        source,
        bundle_root=tmp_path / "cabinet",
        source_handling="copy",
        options=ConvertOptions(strip_signature_images=True, strip_tracking_pixels=True),
    )

    assert result.success is True
    assert [path.name for path in result.attachments] == ["invoice-q3.pdf"]
    assert result.attachments[0].read_bytes() == b"%PDF-fake-invoice"
    assert result.markdown is not None
    front = _front_matter(result.markdown)
    assert front["attachments"] == ["invoice-q3.pdf"]
    assert front["attachment_files"] == ["attachments/invoice-q3.pdf"]


def test_convert_to_bundle_strips_unreferenced_inline_image_attachment(tmp_path: Path) -> None:
    source = _write_inline_attachment_email(
        tmp_path / "orphan-image.eml",
        filename="orphan.png",
        content_type="image/png",
        content_id="orphan-image",
        payload=b"fake-png",
        html="<html><body><p>No image reference remains.</p></body></html>",
    )

    result, diagnostics = convert_to_bundle_with_diagnostics(
        source,
        bundle_root=tmp_path / "cabinet",
        source_handling="copy",
        options=ConvertOptions(strip_signature_images=True, strip_tracking_pixels=True),
    )

    assert result.success is True
    assert result.attachments == []
    assert diagnostics is not None
    assert diagnostics["attachments"] == {"referenced": 1, "retained": 0}


def test_delete_source_warns_when_inline_image_attachment_is_discarded(tmp_path: Path) -> None:
    source = _write_inline_attachment_email(
        tmp_path / "deleted-orphan-image.eml",
        filename="orphan.png",
        content_type="image/png",
        content_id="orphan-image",
        payload=b"fake-png",
        html="<html><body><p>No image reference remains.</p></body></html>",
    )

    result, diagnostics = convert_to_bundle_with_diagnostics(
        source,
        bundle_root=tmp_path / "cabinet",
        source_handling="delete",
        options=ConvertOptions(strip_signature_images=True, strip_tracking_pixels=True),
    )

    assert result.success is True
    assert source.exists() is False
    assert diagnostics is not None
    assert diagnostics["state"] == "degraded"
    assert any(
        warning["code"] == "attachment_discarded_with_source_deleted"
        for warning in diagnostics["warnings"]
    )


def test_convert_to_bundle_retains_large_inline_logo_draft(tmp_path: Path) -> None:
    source = _write_inline_attachment_email(
        tmp_path / "logo-draft.eml",
        filename="logo-draft-v3.png",
        content_type="image/png",
        content_id="logo-draft-v3",
        payload=b"fake-large-png",
        html=(
            '<html><body><img src="cid:logo-draft-v3" alt="new logo draft" '
            'width="800" height="600" /></body></html>'
        ),
    )

    result, diagnostics = convert_to_bundle_with_diagnostics(
        source,
        bundle_root=tmp_path / "cabinet",
        source_handling="copy",
        options=ConvertOptions(strip_signature_images=True),
    )

    assert result.success is True
    assert [path.name for path in result.attachments] == ["logo-draft-v3.png"]
    assert result.attachments[0].read_bytes() == b"fake-large-png"
    assert diagnostics is not None
    assert diagnostics["attachments"] == {"referenced": 1, "retained": 1}


def test_convert_to_bundle_diagnostics_count_signature_filter_discarded_attachment(
    tmp_path: Path,
) -> None:
    source = _write_inline_attachment_email(
        tmp_path / "small-logo.eml",
        filename="logo.gif",
        content_type="image/gif",
        content_id="small-logo",
        payload=b"fake-small-gif",
        html='<html><body><img src="cid:small-logo" width="16" height="16" /></body></html>',
    )

    result, diagnostics = convert_to_bundle_with_diagnostics(
        source,
        bundle_root=tmp_path / "cabinet",
        source_handling="delete",
        options=ConvertOptions(strip_signature_images=True),
    )

    assert result.success is True
    assert result.attachments == []
    assert source.exists() is False
    assert diagnostics is not None
    assert diagnostics["attachments"] == {"referenced": 1, "retained": 0}
    assert diagnostics["state"] == "degraded"
    assert any(
        warning["code"] == "attachment_discarded_with_source_deleted"
        for warning in diagnostics["warnings"]
    )


def test_convert_to_bundle_diagnostics_report_referenced_and_retained_counts(
    copy_fixture, tmp_path: Path
) -> None:
    source = copy_fixture("outlook_attachment_with_cid.eml")

    _result, diagnostics = convert_to_bundle_with_diagnostics(
        source,
        bundle_root=tmp_path / "cabinet",
        options=ConvertOptions(strip_signature_images=True),
        source_handling="copy",
    )

    assert diagnostics is not None
    assert diagnostics["attachments"] == {"referenced": 2, "retained": 1}


def test_convert_to_bundle_diagnostics_omit_attachment_counts_when_none_present(
    copy_fixture, tmp_path: Path
) -> None:
    source = copy_fixture("html_only.eml")

    _result, diagnostics = convert_to_bundle_with_diagnostics(
        source,
        bundle_root=tmp_path / "cabinet",
        source_handling="copy",
    )

    assert diagnostics is not None
    assert "attachments" not in diagnostics


def test_convert_to_bundle_cleans_partial_bundle_after_write_failure(
    copy_fixture, monkeypatch, tmp_path: Path
) -> None:
    source = copy_fixture("with_attachment.eml")

    import dead_letter.core._pipeline as pipeline

    def boom(_parts: object, _target_dir: Path) -> list[Path]:
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "_write_attachment_parts", boom)

    result = convert_to_bundle(source, bundle_root=tmp_path / "cabinet")

    assert result.success is False
    assert result.error is not None
    assert "disk full" in result.error
    assert result.bundle is None
    assert source.exists() is True
    assert list((tmp_path / "cabinet").glob("*")) == []


def test_convert_to_bundle_returns_failure_for_missing_input(tmp_path: Path) -> None:
    result = convert_to_bundle(tmp_path / "missing.eml", bundle_root=tmp_path / "cabinet")

    assert result.success is False
    assert result.error is not None
    assert "missing.eml" in result.error
