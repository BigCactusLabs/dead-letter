from __future__ import annotations

from email import policy
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import dead_letter.core.mime as mime_module

from dead_letter.core.attachments import collect_attachment_names
from dead_letter.core import convert
from dead_letter.core.mime import _normalize_header_value, parse_eml


REQUIRED_FIXTURES = {
    "calendar_invite.eml",
    "forwarded.eml",
    "gmail_quote.eml",
    "html_only.eml",
    "malformed_empty.eml",
    "multipart_alternative.eml",
    "non_utf8_iso8859.eml",
    "outlook_quote.eml",
    "plain_text.eml",
    "reply_chain.eml",
    "threaded.eml",
    "with_attachment.eml",
    "with_inline_cid.eml",
}


def _fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


def _mixed_related_with_top_level_attachments_bytes() -> bytes:
    root = EmailMessage()
    root["From"] = "alice@example.com"
    root["To"] = "bob@example.com"
    root["Subject"] = "Top-level attachments with related body"
    root.set_type("multipart/mixed")

    related = EmailMessage()
    related.set_type("multipart/related")

    alternative = EmailMessage()
    alternative.set_type("multipart/alternative")

    plain = EmailMessage()
    plain.set_content("Please find attached.")

    html = EmailMessage()
    html.set_content('<p>Please find attached.<img src="cid:image1"></p>', subtype="html")

    alternative.attach(plain)
    alternative.attach(html)
    related.attach(alternative)

    inline = EmailMessage()
    inline.set_type("image/png")
    inline["Content-ID"] = "<image1>"
    inline["Content-Disposition"] = 'inline; filename="image003.png"'
    inline["Content-Transfer-Encoding"] = "base64"
    inline.set_payload("AA==")
    related.attach(inline)

    root.attach(related)

    for filename, content_type, payload in (
        ("contract.pdf", "application/pdf", "JVBERi0="),
        ("contract.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "UEsDBA=="),
    ):
        part = EmailMessage()
        part.set_type(content_type)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        part["Content-Transfer-Encoding"] = "base64"
        part.set_payload(payload)
        root.attach(part)

    return root.as_bytes(policy=policy.default)


def test_fixture_corpus_contains_required_categories() -> None:
    fixture_dir = _fixture_dir()
    existing = {path.name for path in fixture_dir.glob("*.eml")}

    assert fixture_dir.exists()
    assert REQUIRED_FIXTURES.issubset(existing)


def test_non_utf8_fixture_declares_legacy_charset() -> None:
    payload = (_fixture_dir() / "non_utf8_iso8859.eml").read_bytes().lower()

    assert b"charset=iso-8859-1" in payload


def test_fixture_attachment_name_parity_between_mailparser_and_stdlib_extractors() -> None:
    fixture_dir = _fixture_dir()

    for source in sorted(fixture_dir.glob("*.eml")):
        raw = source.read_bytes()
        mailparser_attachments = list(mime_module.mailparser.parse_from_bytes(raw).attachments or [])
        stdlib_attachments = mime_module._extract_raw_attachments_from_stdlib(raw)

        assert collect_attachment_names(mailparser_attachments) == collect_attachment_names(
            stdlib_attachments
        ), source.name


def test_parse_eml_plain_text_fixture() -> None:
    parsed = parse_eml(_fixture_dir() / "plain_text.eml")

    assert parsed.subject == "Plain Text Fixture"
    assert parsed.sender == "alice@example.com"
    assert parsed.html_body is None
    assert "plain text fixture" in parsed.text_body.lower()
    assert parsed.attachments == []


def test_parse_eml_extracts_inline_cid_and_attachments() -> None:
    parsed = parse_eml(_fixture_dir() / "with_inline_cid.eml")

    assert "logo.png" in parsed.attachments
    assert parsed.inline_cid_to_filename == {"image1": "logo.png"}
    assert parsed.inline_cid_to_data_uri == {
        "image1": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Zk8kAAAAASUVORK5CYII="
    }
    assert parsed.html_body is not None
    assert "cid:image1" in parsed.html_body


def test_parse_eml_extracts_calendar_parts() -> None:
    parsed = parse_eml(_fixture_dir() / "calendar_invite.eml")

    assert len(parsed.calendar_parts) == 1
    assert "BEGIN:VCALENDAR" in parsed.calendar_parts[0]


def test_normalize_header_value_skips_none_items() -> None:
    assert _normalize_header_value(["alice@example.com", None, "bob@example.com"]) == (
        "alice@example.com, bob@example.com"
    )


def test_convert_handles_duplicate_subject_headers(tmp_path: Path) -> None:
    source = tmp_path / "duplicate-subject.eml"
    source.write_text(
        "From: alice@example.com\n"
        "To: bob@example.com\n"
        "Subject: First subject\n"
        "Subject: Second subject\n"
        "\n"
        "Body\n",
        encoding="utf-8",
    )

    result = convert(source, output=tmp_path / "output")

    assert result.success is True
    assert result.subject == "First subject, Second subject"


def test_parse_eml_builds_body_candidates_for_multipart_alternative() -> None:
    parsed = parse_eml(_fixture_dir() / "multipart_alternative.eml")

    assert parsed.selected_body_kind == "html"
    assert [candidate.kind for candidate in parsed.body_candidates] == ["html", "plain"]
    assert parsed.html_body is not None
    assert "Preferred html body" in parsed.html_body
    assert parsed.text_body == "Plain body\n"


def test_parse_eml_extracts_top_level_attachments_next_to_related_body(tmp_path: Path) -> None:
    source = tmp_path / "top-level-attachments.eml"
    source.write_bytes(_mixed_related_with_top_level_attachments_bytes())

    parsed = parse_eml(source)

    assert {"contract.pdf", "contract.xlsx"}.issubset(set(parsed.attachments))


def test_parse_eml_falls_back_when_mailparser_misses_attachments(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "mailparser-missing-attachments.eml"
    source.write_bytes(_mixed_related_with_top_level_attachments_bytes())

    original_parse = mime_module.mailparser.parse_from_bytes

    def parse_without_attachments(raw: bytes) -> SimpleNamespace:
        parsed = original_parse(raw)
        return SimpleNamespace(
            subject=parsed.subject,
            from_=parsed.from_,
            date=parsed.date,
            headers=parsed.headers,
            text_plain=parsed.text_plain,
            body=parsed.body,
            text_html=parsed.text_html,
            defects=getattr(parsed, "defects", []),
            attachments=[],
        )

    monkeypatch.setattr(mime_module.mailparser, "parse_from_bytes", parse_without_attachments)

    parsed = parse_eml(source)

    assert {"contract.pdf", "contract.xlsx"}.issubset(set(parsed.attachments))
    disagreement = [defect for defect in parsed.defects if defect.code == "attachment_parser_disagreement"]
    assert len(disagreement) == 1
    assert disagreement[0].severity == "warning"
    assert "mailparser=0" in disagreement[0].message
