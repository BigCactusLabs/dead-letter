from __future__ import annotations

from pathlib import Path

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


def test_fixture_corpus_contains_required_categories() -> None:
    fixture_dir = _fixture_dir()
    existing = {path.name for path in fixture_dir.glob("*.eml")}

    assert fixture_dir.exists()
    assert REQUIRED_FIXTURES.issubset(existing)


def test_non_utf8_fixture_declares_legacy_charset() -> None:
    payload = (_fixture_dir() / "non_utf8_iso8859.eml").read_bytes().lower()

    assert b"charset=iso-8859-1" in payload


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


def test_parse_eml_builds_body_candidates_for_multipart_alternative() -> None:
    parsed = parse_eml(_fixture_dir() / "multipart_alternative.eml")

    assert parsed.selected_body_kind == "html"
    assert [candidate.kind for candidate in parsed.body_candidates] == ["html", "plain"]
    assert parsed.html_body is not None
    assert "Preferred html body" in parsed.html_body
    assert parsed.text_body == "Plain body\n"
