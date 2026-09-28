"""Synthetic regressions for directory intent and retained inline images."""

from __future__ import annotations

import base64
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest
import yaml

from dead_letter.core import ConvertOptions, convert, convert_dir
from dead_letter.core._pipeline import (
    _resolve_output_target,
    _retain_referenced_inline_attachments,
    convert_to_bundle_with_diagnostics,
)
from dead_letter.core.types import AttachmentPart, ParsedEmail


def _write_message(path: Path, subject: str) -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "sender@example.com"
    message["To"] = "reader@example.com"
    message["Subject"] = subject
    message.set_content("Synthetic conversion test.")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = message.as_bytes()
    path.write_bytes(raw)
    return raw


@pytest.mark.parametrize("name", ["out.md", "OUT.MD"])
def test_existing_md_directory_is_not_an_explicit_file(tmp_path: Path, name: str) -> None:
    output = tmp_path / name
    output.mkdir()
    assert _resolve_output_target(tmp_path / "input.eml", "Readable subject", output) == (
        output / "readable-subject.md"
    )


def test_trailing_separator_preserves_directory_intent(tmp_path: Path) -> None:
    output = tmp_path / "new.md"
    assert _resolve_output_target(tmp_path / "input.eml", "Readable subject", f"{output}/") == (
        output / "readable-subject.md"
    )
    assert not output.exists()


@pytest.mark.parametrize("output_name", ["out", "out.md", "OUT.MD"])
@pytest.mark.parametrize("preexisting", [False, True])
def test_directory_conversion_preserves_md_suffixed_subdirectories(
    tmp_path: Path, output_name: str, preexisting: bool,
) -> None:
    inbox = tmp_path / "inbox"
    sources = {
        inbox / "root.eml": _write_message(inbox / "root.eml", "Root message"),
        inbox / "project.md" / "nested.eml": _write_message(
            inbox / "project.md" / "nested.eml", "Nested message",
        ),
    }
    output = tmp_path / output_name
    if preexisting:
        output.mkdir()

    results = convert_dir(inbox, output=output)

    assert len(results) == 2
    assert all(result.success for result in results), [result.error for result in results]
    assert {result.output for result in results} == {
        output / "root-message.md", output / "project.md" / "nested-message.md",
    }
    assert (output / "root-message.md").is_file()
    assert (output / "project.md" / "nested-message.md").is_file()
    assert {path: path.read_bytes() for path in sources} == sources


def test_explicit_markdown_file_and_collision_behavior_are_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "input.eml"
    raw = _write_message(source, "Do not use this name")
    target = tmp_path / "chosen.md"
    first = convert(source, output=target)
    second = convert(source, output=target)
    assert first.success and second.success
    assert first.output == target
    assert second.output == tmp_path / "chosen-2.md"
    assert source.read_bytes() == raw


def test_single_conversion_accepts_an_explicit_md_directory_string(tmp_path: Path) -> None:
    source = tmp_path / "input.eml"
    _write_message(source, "Readable subject")
    output = tmp_path / "new.md"
    result = convert(source, output=f"{output}/")
    assert result.success, result.error
    assert result.output == output / "readable-subject.md"
    assert result.output.is_file()


def test_directory_dry_run_does_not_create_output_or_delete_sources(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "project.md" / "input.eml"
    raw = _write_message(source, "Dry run")
    output = tmp_path / "out.md"
    results = convert_dir(
        source.parent.parent, output=output,
        options=ConvertOptions(dry_run=True, delete_eml=True),
    )
    assert len(results) == 1 and results[0].success
    assert results[0].output is None
    assert not output.exists()
    assert source.read_bytes() == raw


def _parsed_inline_image(tmp_path: Path) -> ParsedEmail:
    return ParsedEmail(
        source=tmp_path / "input.eml", subject="Chart", sender="sender@example.com",
        date=None, text_body="", html_body=None, headers={}, attachments=["chart.png"],
        attachment_parts=[AttachmentPart(
            filename="chart.png", content_type="image/png", payload=b"chart",
            content_id="chart", disposition="inline",
        )],
        inline_cid_to_filename={"chart": "chart.png"},
        inline_cid_to_data_uri={"chart": "data:image/png;base64,Y2hhcnQ="},
    )


@pytest.mark.parametrize("destination", ["cid:chart", "data:image/png;base64,Y2hhcnQ="])
def test_inline_retention_recognizes_rendered_image_destination(tmp_path: Path, destination: str) -> None:
    parsed = _parsed_inline_image(tmp_path)
    retained = _retain_referenced_inline_attachments(parsed, reference_text=f"![Chart]({destination})")
    assert retained.attachments == ["chart.png"]
    assert retained.attachment_parts == parsed.attachment_parts
    assert retained.inline_cid_to_filename == parsed.inline_cid_to_filename
    assert retained.inline_cid_to_data_uri == parsed.inline_cid_to_data_uri
    assert parsed.attachments == ["chart.png"]


def test_unreferenced_inline_image_is_still_removed(tmp_path: Path) -> None:
    parsed = _parsed_inline_image(tmp_path)
    retained = _retain_referenced_inline_attachments(parsed, reference_text="No retained image here.")
    assert retained.attachments == []
    assert retained.attachment_parts == []
    assert retained.inline_cid_to_filename == {}
    assert retained.inline_cid_to_data_uri == {}
    assert parsed.attachments == ["chart.png"]


def _write_inline_message(path: Path, *, shared_signature: bool = False, only_signature: bool = False) -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "sender@example.com"
    message["To"] = "reader@example.com"
    message["Subject"] = "Retain the chart"
    html = '<p>Current message.</p>'
    if not only_signature:
        html += '<p><img src="cid:chart" alt="Chart" width="640" height="480"></p>'
    if shared_signature or only_signature:
        html += '<div class="gmail_signature"><img src="cid:chart" alt="Signature"></div>'
    message.set_content(html, subtype="html")
    message.add_related(
        bytes(range(256)), maintype="image", subtype="png", cid="<chart>",
        filename="chart.png", disposition="inline", cte="base64",
    )
    raw = message.as_bytes()
    path.write_bytes(raw)
    return raw


@pytest.mark.parametrize("strip_signature, strip_tracking, embed", [
    (False, False, True),
    (False, True, False),
    (False, True, True),
    (True, False, True),
    (True, True, True),
])
def test_bundle_keeps_visible_inline_image_when_embedding_and_filtering(
    tmp_path: Path, strip_signature: bool, strip_tracking: bool, embed: bool,
) -> None:
    source = tmp_path / "input.eml"
    raw = _write_inline_message(source)
    result, diagnostics = convert_to_bundle_with_diagnostics(
        source, bundle_root=tmp_path / "cabinet", source_handling="copy",
        options=ConvertOptions(
            embed_inline_images=embed, strip_signature_images=strip_signature,
            strip_tracking_pixels=strip_tracking,
        ),
    )
    assert result.success, result.error
    assert [(path.name, path.read_bytes()) for path in result.attachments] == [
        ("chart.png", bytes(range(256))),
    ]
    assert result.markdown is not None
    markdown = result.markdown.read_text(encoding="utf-8")
    front_matter = yaml.safe_load(markdown.split("---", 2)[1])
    assert front_matter["attachments"] == ["chart.png"]
    assert front_matter["attachment_files"] == ["attachments/chart.png"]
    assert diagnostics is not None
    assert diagnostics["attachments"] == {"referenced": 1, "retained": 1}
    assert source.read_bytes() == raw


@pytest.mark.parametrize("embed", [False, True])
def test_signature_image_sharing_a_cid_does_not_remove_body_image(tmp_path: Path, embed: bool) -> None:
    source = tmp_path / "input.eml"
    raw = _write_inline_message(source, shared_signature=True)
    result, diagnostics = convert_to_bundle_with_diagnostics(
        source, bundle_root=tmp_path / "cabinet", source_handling="copy",
        options=ConvertOptions(strip_signature_images=True, embed_inline_images=embed),
    )
    assert result.success, result.error
    assert len(result.attachments) == 1
    assert result.attachments[0].read_bytes() == bytes(range(256))
    assert result.markdown is not None
    markdown = result.markdown.read_text(encoding="utf-8")
    destination = (
        "data:image/png;base64," + base64.b64encode(bytes(range(256))).decode("ascii")
        if embed else "cid:chart"
    )
    assert f"({destination})" in markdown
    assert "![Signature]" not in markdown
    assert diagnostics is not None
    assert len(diagnostics["stripped_images"]) == 1
    assert diagnostics["attachments"] == {"referenced": 1, "retained": 1}
    assert source.read_bytes() == raw


def test_signature_only_image_is_still_removed(tmp_path: Path) -> None:
    source = tmp_path / "input.eml"
    raw = _write_inline_message(source, only_signature=True)
    result, diagnostics = convert_to_bundle_with_diagnostics(
        source, bundle_root=tmp_path / "cabinet", source_handling="copy",
        options=ConvertOptions(strip_signature_images=True, embed_inline_images=True),
    )
    assert result.success, result.error
    assert result.attachments == []
    assert result.markdown is not None
    assert "data:image/png;base64," not in result.markdown.read_text(encoding="utf-8")
    assert diagnostics is not None
    assert diagnostics["attachments"] == {"referenced": 1, "retained": 0}
    assert source.read_bytes() == raw
