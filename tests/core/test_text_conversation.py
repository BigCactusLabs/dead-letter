from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

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


def test_overlong_line_uses_body_preserving_no_split_fallback() -> None:
    text = "x" * 4097 + "\n\nOn Thu someone wrote:\n> OLDER_PAYLOAD"
    zones = segment_text_conversation(text).zones
    assert len(zones) == 1
    assert zones[0].kind is ZoneKind.BODY
    assert zones[0].content == text


def test_reply_at_line_length_budget_still_splits() -> None:
    authored = "x" * 4096
    zones = segment_text_conversation(f"{authored}\n\nOn Thu someone wrote:\n> OLDER_PAYLOAD").zones
    assert zones[0].kind is ZoneKind.BODY
    assert zones[0].content == authored
    assert any(zone.kind is ZoneKind.QUOTED and "OLDER_PAYLOAD" in zone.content for zone in zones)


# A subprocess can kill a dependency regex that holds the GIL indefinitely.
@pytest.mark.parametrize("thread_mode", ["latest", "structured"])
@pytest.mark.parametrize("with_forward", [False, True])
def test_pathological_plain_line_converts_in_killable_process(
    thread_mode: str, with_forward: bool,
) -> None:
    script = textwrap.dedent(r'''
        import sys
        import tempfile
        from email.message import EmailMessage
        from pathlib import Path
        from dead_letter.core import convert
        from dead_letter.core.text_conversation import segment_text_conversation
        from dead_letter.core.types import ConvertOptions, ZoneKind

        mode, with_forward = sys.argv[1], sys.argv[2] == 'True'
        line = 'Am X' + ' ' * 128_000 + ':'
        body = 'AUTHORED_PAYLOAD\n' + line + '\nENDING_PAYLOAD'
        if with_forward:
            # The dependency is also called by the reply-history guard when
            # the line immediately before a forward separator ends in a colon.
            body = 'AUTHORED_PAYLOAD\n' + line + '\n\n' + (
                '---------- Forwarded message ----------\n'
                'From: Bob <bob@example.com>\nSubject: Plan\n\nFORWARDED_PAYLOAD'
            )
        zones = segment_text_conversation(body, split_forwards=mode == 'structured').zones
        authored = next(zone.content for zone in zones if zone.kind is ZoneKind.BODY)
        assert line in authored
        if not with_forward:
            assert len(zones) == 1
            assert authored == body
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'synthetic.eml'
            output = Path(directory) / 'synthetic.md'
            message = EmailMessage()
            message['From'] = 'Alice <alice@example.com>'
            message['To'] = 'Bob <bob@example.com>'
            message['Subject'] = 'Synthetic long line'
            message.set_content(body)
            source.write_bytes(bytes(message))
            result = convert(source, output=output, options=ConvertOptions(thread_mode=mode))
            assert result.success, result.error
            rendered = output.read_text()
            assert line in rendered
            if with_forward:
                assert 'FORWARDED_PAYLOAD' in rendered
                assert 'Bob <bob@example.com>' in rendered
            else:
                assert body in rendered
            print('full text preserved')
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script, thread_mode, str(with_forward)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "full text preserved" in result.stdout


def test_large_message_with_short_lines_still_splits_ordinary_reply() -> None:
    authored = ("x" * 512 + "\n") * 100
    zones = segment_text_conversation(f"{authored}\nOn Thu someone wrote:\n> OLDER_PAYLOAD").zones
    assert zones[0].kind is ZoneKind.BODY
    assert zones[0].content == authored.strip()
    assert any(zone.kind is ZoneKind.QUOTED and "OLDER_PAYLOAD" in zone.content for zone in zones)


def test_shared_html_reply_parser_keeps_large_preamble_before_outlook_headers() -> None:
    # The plain-text line cap must not change HTML's shared parser contract:
    # its caller can fall through to an Outlook splitter that discards prefixes.
    from dead_letter.core.text_conversation import parse_email_replies

    preamble = "QUOTED_PREAMBLE_" + "X" * 5000
    text = f"{preamble}\nFrom: Bob\nSent: Wednesday\nTo: Alice\nSubject: Plan\n\nQUOTED_PAYLOAD"
    replies = parse_email_replies(text)
    assert len(replies) >= 2
    assert replies[0].content == preamble
    assert "QUOTED_PAYLOAD" in replies[-1].content
