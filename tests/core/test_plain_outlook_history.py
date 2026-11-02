"""Synthetic paired controls for evidence-qualified Outlook history labels."""

from __future__ import annotations

from pathlib import Path

import pytest

from dead_letter.core import convert
from dead_letter.core.mime import parse_eml
from dead_letter.core.text_conversation import segment_text_conversation
from dead_letter.core.types import ConvertOptions, ZoneKind

FIXTURES = Path(__file__).parent / "fixtures"
LOCALES = ("de", "fr", "es", "nl", "it", "ja", "de_english")


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("punctuation", ["original", "fullwidth", "nbsp"])
@pytest.mark.parametrize("separator", ["original", "underscore"])
def test_localized_outlook_reply_keeps_nested_forward_in_history(
    locale: str, punctuation: str, separator: str,
) -> None:
    text = parse_eml(FIXTURES / f"plain_outlook_{locale}_reply_i193.eml").text_body
    if punctuation == "fullwidth":
        text = text.replace(":", "：")
    elif punctuation == "nbsp":
        text = text.replace(" :", ":").replace(":", "\u00a0:")
    if separator == "underscore":
        lines = text.splitlines()
        lines[2] = "_" * 32
        text = "\n".join(lines)
    for structured in (False, True):
        zones = segment_text_conversation(text, split_forwards=structured).zones
        body = "\n".join(zone.content for zone in zones if zone.kind is ZoneKind.BODY)
        quoted = "\n".join(zone.content for zone in zones if zone.kind is ZoneKind.QUOTED)
        assert body == "AUTHORED_PAYLOAD"
        assert "OLD_REPLY_PAYLOAD" in quoted
        assert "NESTED_FORWARD_PAYLOAD" in quoted
        assert not any(zone.kind is ZoneKind.FORWARD_HEADER for zone in zones)


@pytest.mark.parametrize("locale", LOCALES)
def test_localized_outlook_reply_without_nested_forward_is_history(locale: str) -> None:
    text = parse_eml(FIXTURES / f"plain_outlook_{locale}_reply_i193.eml").text_body
    text = text[:text.index("OLD_REPLY_PAYLOAD") + len("OLD_REPLY_PAYLOAD")]
    zones = segment_text_conversation(text).zones
    assert zones[0].kind is ZoneKind.BODY
    assert zones[0].content == "AUTHORED_PAYLOAD"
    assert any(zone.kind is ZoneKind.QUOTED and "OLD_REPLY_PAYLOAD" in zone.content for zone in zones)


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("thread_mode", ["latest", "structured"])
def test_paired_localized_outlook_reply_and_true_forward_convert(
    locale: str, thread_mode: str, tmp_path: Path,
) -> None:
    for kind in ("reply", "forward"):
        source = FIXTURES / f"plain_outlook_{locale}_{kind}_i193.eml"
        output = tmp_path / f"{kind}.md"
        result = convert(source, output=output, options=ConvertOptions(thread_mode=thread_mode))
        assert result.success, result.error
        rendered = output.read_text()
        assert "AUTHORED_PAYLOAD" in rendered
        if kind == "reply":
            assert ("OLD_REPLY_PAYLOAD" in rendered) == (thread_mode == "structured")
            assert ("NESTED_FORWARD_PAYLOAD" in rendered) == (thread_mode == "structured")
            assert "## Forwarded" not in rendered
        else:
            assert "FORWARDED_PAYLOAD" in rendered
            if thread_mode == "structured":
                assert "## Forwarded from Bob <bob@example.com>" in rendered
                assert "thread_messages: 1" in rendered
            zones = segment_text_conversation(parse_eml(source).text_body, split_forwards=True).zones
            assert any(zone.kind is ZoneKind.FORWARDED_BODY for zone in zones)
            assert not any(zone.kind is ZoneKind.QUOTED for zone in zones)


@pytest.mark.parametrize("line", [
    "Von: our team comes the plan.", "De : cette idée vient le plan.",
    "Enviado el: is the label we discussed.", "Van: dit woord komt een zin.",
    "Da: this is an authored label example.", "差出人: is the term we discussed.",
])
def test_authored_localized_label_line_without_header_block_stays_body(line: str) -> None:
    text = f"AUTHORED_PAYLOAD\n{line}\nOrdinary authored prose."
    zones = segment_text_conversation(text).zones
    assert len(zones) == 1
    assert zones[0].kind is ZoneKind.BODY
    assert zones[0].content == text


@pytest.mark.parametrize("header", [
    "Von: Bob",  # No date field.
    "Gesendet: Mittwoch\nAn: Alice",  # No sender field.
    "Inizio: Bob\nInviato: mercoledì\nFine: Alice",  # Erroneous it(OWA) resource row.
    "寄件者: Bob\n寄件日期: Wednesday\n收件者: Alice",  # Not evidence-qualified for history.
])
def test_separator_without_supported_from_and_date_block_stays_body(header: str) -> None:
    text = f"AUTHORED_PAYLOAD\n\n{'_' * 32}\n{header}\n\nOrdinary prose."
    zones = segment_text_conversation(text).zones
    assert len(zones) == 1
    assert zones[0].kind is ZoneKind.BODY
    assert header in zones[0].content
    assert "Ordinary prose." in zones[0].content


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("colon", [":", "：", "\u00a0:"])
def test_true_forward_with_localized_header_punctuation_stays_forward(
    locale: str, colon: str,
) -> None:
    text = parse_eml(FIXTURES / f"plain_outlook_{locale}_forward_i193.eml").text_body
    note, forwarded = text.split("\n\n", 1)
    marker, headers = forwarded.split("\n", 1)
    headers = headers.replace(" :", ":").replace(":", colon)
    text = f"{note}\n\n{marker}\n{headers}"
    for structured in (False, True):
        zones = segment_text_conversation(text, split_forwards=structured).zones
        assert zones[0].kind is ZoneKind.BODY
        assert zones[0].content == "AUTHORED_PAYLOAD"
        assert any(zone.kind is ZoneKind.FORWARDED_BODY and "FORWARDED_PAYLOAD" in zone.content for zone in zones)
        assert not any(zone.kind is ZoneKind.QUOTED for zone in zones)


@pytest.mark.parametrize("thread_mode", ["latest", "structured"])
@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_indented_outlook_example_and_following_authored_text_stay_body(
    tmp_path: Path, thread_mode: str, indent: str,
) -> None:
    from email.message import EmailMessage

    text = (
        "Here is how Outlook formats a reply, for the parser docs:\n\n"
        f"{indent}{'_' * 32}\n{indent}From: Bob\n{indent}Sent: Wed\n\n"
        f"{indent}Example body.\n\nAfter the example: please review by Friday."
    )
    zones = segment_text_conversation(text, split_forwards=thread_mode == "structured").zones
    assert len(zones) == 1
    assert zones[0].kind is ZoneKind.BODY
    assert zones[0].content == text
    message = EmailMessage()
    message["Subject"] = "Synthetic authored example"
    message.set_content(text)
    source, output = tmp_path / "example.eml", tmp_path / "example.md"
    source.write_bytes(bytes(message))
    result = convert(source, output=output, options=ConvertOptions(thread_mode=thread_mode))
    assert result.success, result.error
    rendered = output.read_text()
    assert text in rendered
    assert "thread_messages:" not in rendered


@pytest.mark.parametrize("indented", ["separator", "headers", "both"])
def test_localized_structural_split_requires_column_zero(indented: str) -> None:
    separator_indent = "\t" if indented in {"separator", "both"} else ""
    header_indent = " " if indented in {"headers", "both"} else ""
    text = (
        f"AUTHORED\n\n{separator_indent}-----Ursprüngliche Nachricht-----\n"
        f"{header_indent}Von: Bob\n{header_indent}Gesendet: Mittwoch\n\nEXAMPLE\n\nAFTER"
    )
    for structured in (False, True):
        zones = segment_text_conversation(text, split_forwards=structured).zones
        assert len(zones) == 1
        assert zones[0].kind is ZoneKind.BODY
        assert zones[0].content == text


@pytest.mark.parametrize("locale", ["de", "en"])
@pytest.mark.parametrize("thread_order", ["oldest-first", "latest-first"])
def test_three_outlook_blocks_have_separate_zones_and_sections(
    locale: str, thread_order: str, tmp_path: Path,
) -> None:
    from email.message import EmailMessage

    separator, sender, date, recipient, subject = (
        ("-----Ursprüngliche Nachricht-----", "Von", "Gesendet", "An", "Betreff")
        if locale == "de" else ("-----Original Message-----", "From", "Sent", "To", "Subject")
    )
    blocks = [
        f"{sender}: Sender{i} <s{i}@example.com>\n{date}: Wednesday\n"
        f"{recipient}: Alice\n{subject}: Topic{i}\n\nBLOCK_{i}"
        for i in (1, 2, 3)
    ]
    text = "AUTHORED\n\n" + "\n\n".join(f"{separator}\n{block}" for block in blocks)
    zones = segment_text_conversation(text, split_forwards=True).zones
    assert zones[0].kind is ZoneKind.BODY
    assert zones[0].content == "AUTHORED"
    quoted = [zone.content for zone in zones if zone.kind is ZoneKind.QUOTED]
    assert quoted == blocks
    assert all(separator not in block for block in quoted)
    message = EmailMessage()
    message["Subject"] = "Synthetic three-block Outlook chain"
    message.set_content(text)
    source, output = tmp_path / "chain.eml", tmp_path / "chain.md"
    source.write_bytes(bytes(message))
    result = convert(source, output=output, options=ConvertOptions(
        thread_mode="structured", thread_order=thread_order,
    ))
    assert result.success, result.error
    rendered = output.read_text()
    assert "thread_messages: 3" in rendered
    assert separator not in rendered
    assert rendered.count("\n## ") == 3
    sequence = (3, 2, 1) if thread_order == "oldest-first" else (1, 2, 3)
    offsets = [rendered.index(f"BLOCK_{i}") for i in sequence]
    assert offsets == sorted(offsets)
    if locale == "en":
        for i in sequence:
            assert f"## From Sender{i} <s{i}@example.com> (Wednesday) — Topic{i}" in rendered


def test_french_typographic_apostrophe_separator_is_history() -> None:
    text = "AUTHORED\n\n-----Message d’origine-----\nDe : Marie\nEnvoyé : lundi\n\nOLD"
    zones = segment_text_conversation(text).zones
    assert [zone.kind for zone in zones] == [ZoneKind.BODY, ZoneKind.QUOTED]
    assert zones[0].content == "AUTHORED"
    assert zones[1].content == "De : Marie\nEnvoyé : lundi\n\nOLD"
