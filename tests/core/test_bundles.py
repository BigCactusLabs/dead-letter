from __future__ import annotations

from pathlib import Path

import pytest

from dead_letter.core import BundleResult, ConvertOptions, convert_to_bundle


def _front_matter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.find("\n---\n", 4)
    assert end != -1
    return __import__("yaml").safe_load(text[4:end])


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
