"""Exercise MIME edge cases through extraction and on-disk conversion."""

from __future__ import annotations

import base64
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest
import yaml

from dead_letter.core.mime import _extract_raw_attachments_from_stdlib


def _message_with_empty_attachment(encoding: str) -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "sender@example.com"
    message["To"] = "reader@example.com"
    message["Subject"] = "Attachment preservation"
    message["Date"] = "Mon, 28 Sep 2026 12:00:00 +0000"
    message.set_content("Two files are attached, including an intentionally empty file.")
    message.add_attachment(
        b"", maintype="application", subtype="octet-stream",
        filename="empty.bin", cte=encoding,
    )
    message.add_attachment(
        b"retained bytes\n", maintype="application", subtype="octet-stream",
        filename="notes.bin", cte="base64",
    )
    return message.as_bytes()


@pytest.mark.parametrize("encoding", ["base64", "quoted-printable", "7bit", "8bit"])
def test_stdlib_fallback_keeps_zero_byte_attachment(encoding: str) -> None:
    attachments = _extract_raw_attachments_from_stdlib(
        _message_with_empty_attachment(encoding)
    )

    assert [part["filename"] for part in attachments] == ["empty.bin", "notes.bin"]
    assert attachments[0]["payload"] == ""
    assert base64.b64decode(attachments[1]["payload"]) == b"retained bytes\n"


def test_stdlib_fallback_does_not_invent_attachment_for_empty_body() -> None:
    assert _extract_raw_attachments_from_stdlib(
        b"From: sender@example.com\r\nContent-Type: text/plain\r\n\r\n"
    ) == []


@pytest.mark.parametrize("encoding", ["base64", "quoted-printable", "7bit", "8bit"])
def test_empty_attachment_survives_parser_and_bundle(tmp_path: Path, encoding: str) -> None:
    from dead_letter.core import convert_to_bundle
    from dead_letter.core.mime import parse_eml_bytes

    raw = _message_with_empty_attachment(encoding)
    source = tmp_path / "message.eml"
    source.write_bytes(raw)

    parsed = parse_eml_bytes(raw, source=source)
    assert parsed.attachments == ["empty.bin", "notes.bin"]
    assert [(part.filename, part.payload) for part in parsed.attachment_parts] == [
        ("empty.bin", b""), ("notes.bin", b"retained bytes\n"),
    ]

    result = convert_to_bundle(
        source, bundle_root=tmp_path / "cabinet", source_handling="copy",
    )

    assert result.success, result.error
    assert [(path.name, path.read_bytes()) for path in result.attachments] == [
        ("empty.bin", b""), ("notes.bin", b"retained bytes\n"),
    ]
    assert source.read_bytes() == raw
    assert result.source_artifact is not None
    assert result.source_artifact.read_bytes() == raw
    assert result.markdown is not None
    front_matter = yaml.safe_load(result.markdown.read_text().split("---", 2)[1])
    assert front_matter["attachment_files"] == [
        "attachments/empty.bin", "attachments/notes.bin",
    ]


def test_folded_inline_image_is_embedded_without_markdown_whitespace(tmp_path: Path) -> None:
    from dead_letter.core import ConvertOptions, convert

    image_bytes = bytes(range(256))
    encoded = base64.b64encode(image_bytes).decode("ascii")
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "sender@example.com"
    message["To"] = "reader@example.com"
    message["Subject"] = "Inline chart"
    message["Date"] = "Mon, 28 Sep 2026 12:00:00 +0000"
    message.set_content('<p>Chart:</p><img src="cid:chart" alt="Chart">', subtype="html")
    message.add_related(
        image_bytes, maintype="image", subtype="png", cid="<chart>",
        filename="chart.png", disposition="inline", cte="base64",
    )
    raw = message.as_bytes()
    # The MIME fixture really contains folded base64, not a one-line stand-in.
    assert encoded[:76].encode() + b"\r\n" in raw
    source = tmp_path / "inline.eml"
    source.write_bytes(raw)

    result = convert(
        source, output=tmp_path / "out", options=ConvertOptions(embed_inline_images=True),
    )

    assert result.success, result.error
    assert result.output is not None
    markdown = result.output.read_text(encoding="utf-8")
    assert f"(data:image/png;base64,{encoded})" in markdown
    assert "(cid:chart)" not in markdown
    assert source.read_bytes() == raw
