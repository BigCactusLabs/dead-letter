"""Integration tests for Path B (DOM-segmented HTML) thread splitting."""

from __future__ import annotations

import pathlib

import pytest

from dead_letter.core import _pipeline as pipeline_module
from dead_letter.core.types import (
    ConvertOptions,
    ParsedEmail,
    ThreadMode,
    ZoneKind,
)


def _multi_message_html_quote() -> str:
    return (
        "<div>Latest reply body</div>"
        '<div class="gmail_quote">'
        "On Thu, Mar 5, 2026 at 10:23 AM Alice &lt;alice@example.com&gt; wrote:<br>"
        "Reply level 1<br>"
        "On Wed, Mar 4, 2026 at 9:50 AM Bob &lt;bob@example.com&gt; wrote:<br>"
        "Original message<br>"
        "</div>"
    )


def _make_parsed(html: str, tmp_path: pathlib.Path) -> ParsedEmail:
    fake = tmp_path / "dummy.eml"
    fake.write_text("")
    return ParsedEmail(
        source=fake,
        subject="Re: thread test",
        sender="carol@example.com",
        date="Thu, 05 Mar 2026 10:30:00 +0000",
        text_body="",
        html_body=html,
        headers={},
        attachments=[],
        selected_body_kind="html",
    )


def test_path_b_splits_single_quoted_zone_into_per_reply_zones_in_structured(tmp_path: pathlib.Path) -> None:
    parsed = _make_parsed(_multi_message_html_quote(), tmp_path)
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    threaded, _raw, _diag = pipeline_module._threaded_content_from_conversation(parsed, opts)

    assert threaded is not None
    quoted_zones = [z for z in threaded.zones if z.kind is ZoneKind.QUOTED]
    assert len(quoted_zones) >= 2
    assert any(z.metadata.get("attribution_from") for z in quoted_zones)


def test_path_b_zero_replies_marks_degenerate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(pipeline_module, "parse_email_replies", lambda _text: [])
    parsed = _make_parsed(_multi_message_html_quote(), tmp_path)
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    threaded, _raw, _diag = pipeline_module._threaded_content_from_conversation(parsed, opts)

    assert threaded is not None
    quoted = [z for z in threaded.zones if z.kind is ZoneKind.QUOTED]
    assert len(quoted) == 1
    assert quoted[0].metadata.get("thread_render") == "degenerate"


def test_path_b_parser_raises_marks_degenerate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    def _boom(_text: str) -> list:
        raise RuntimeError("parser blew up")

    monkeypatch.setattr(pipeline_module, "parse_email_replies", _boom)
    parsed = _make_parsed(_multi_message_html_quote(), tmp_path)
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    threaded, _raw, _diag = pipeline_module._threaded_content_from_conversation(parsed, opts)

    assert threaded is not None
    quoted = [z for z in threaded.zones if z.kind is ZoneKind.QUOTED]
    assert len(quoted) == 1
    assert quoted[0].metadata.get("thread_render") == "degenerate"


def test_path_b_latest_mode_does_not_split(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    called = []
    real = pipeline_module.parse_email_replies

    def _spy(text: str):
        called.append(text)
        return real(text)

    monkeypatch.setattr(pipeline_module, "parse_email_replies", _spy)
    parsed = _make_parsed(_multi_message_html_quote(), tmp_path)

    threaded, _raw, _diag = pipeline_module._threaded_content_from_conversation(
        parsed, ConvertOptions()
    )

    assert threaded is not None
    assert called == []


def _outlook_html_quote() -> str:
    return (
        "<div>Carol latest reply.</div>"
        '<div id="divRplyFwdMsg">'
        "<hr>"
        "<p><b>From:</b> Bob &lt;bob@example.com&gt;<br>"
        "<b>Sent:</b> Wednesday, March 4, 2026 9:55 AM<br>"
        "<b>To:</b> Team &lt;team@example.com&gt;<br>"
        "<b>Subject:</b> RE: project status</p>"
        "<p>Bob reply text.</p>"
        "<p><b>From:</b> Alice &lt;alice@example.com&gt;<br>"
        "<b>Sent:</b> Tuesday, March 3, 2026 9:50 AM<br>"
        "<b>To:</b> Team &lt;team@example.com&gt;<br>"
        "<b>Subject:</b> project status</p>"
        "<p>Alice original.</p>"
        "</div>"
    )


def _single_outlook_html_quote() -> str:
    return (
        "<div>Carol latest reply.</div>"
        '<div id="divRplyFwdMsg">'
        "<hr>"
        "<p><b>From:</b> Bob &lt;bob@example.com&gt;<br>"
        "<b>Sent:</b> Wednesday, March 4, 2026 9:55 AM<br>"
        "<b>To:</b> Team &lt;team@example.com&gt;<br>"
        "<b>Subject:</b> RE: project status</p>"
        "<p>Bob reply text.</p>"
        "</div>"
    )


def test_path_b_single_outlook_block_renders_normal_section(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(pipeline_module, "parse_email_replies", lambda _t: [])
    parsed = _make_parsed(_single_outlook_html_quote(), tmp_path)
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    threaded, _raw, _diag = pipeline_module._threaded_content_from_conversation(parsed, opts)

    assert threaded is not None
    quoted = [z for z in threaded.zones if z.kind is ZoneKind.QUOTED]
    assert len(quoted) == 1
    assert quoted[0].metadata.get("thread_render") != "degenerate"


def test_path_b_outlook_splitter_handles_markdown_bold_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(pipeline_module, "parse_email_replies", lambda _t: [])
    parsed = _make_parsed(_outlook_html_quote(), tmp_path)
    opts = ConvertOptions(thread_mode=ThreadMode.STRUCTURED)

    threaded, _raw, _diag = pipeline_module._threaded_content_from_conversation(parsed, opts)

    assert threaded is not None
    quoted = [z for z in threaded.zones if z.kind is ZoneKind.QUOTED]
    assert len(quoted) == 2
    assert all(z.metadata.get("thread_render") != "degenerate" for z in quoted)
    for z in quoted:
        assert not z.content.startswith("---")


def test_path_b_strip_leading_hr_helper() -> None:
    from dead_letter.core._pipeline import _strip_leading_hr_projections

    assert _strip_leading_hr_projections("---\n\nbody") == "body"
    assert _strip_leading_hr_projections("---\n---\n\nbody") == "body"
    assert _strip_leading_hr_projections("body") == "body"
    assert _strip_leading_hr_projections("") == ""


def test_path_b_outlook_block_splitter_semantics() -> None:
    from dead_letter.core._pipeline import _split_outlook_blocks

    assert _split_outlook_blocks("just some plain markdown\n\nmore text") == []

    one_block = (
        "**From:** Alice <alice@example.com>\n"
        "**Sent:** Tuesday, March 3, 2026 9:50 AM\n"
        "**To:** Team\n"
        "**Subject:** x\n\n"
        "body\n"
    )
    assert len(_split_outlook_blocks(one_block)) == 1

    two_blocks = (
        "**From:** Bob <bob@example.com>\n"
        "**Sent:** Wed\n"
        "**To:** Team\n"
        "**Subject:** y\n\n"
        "Bob text.\n\n"
        "**From:** Alice <alice@example.com>\n"
        "**Sent:** Tue\n"
        "**To:** Team\n"
        "**Subject:** x\n\n"
        "Alice text.\n"
    )
    blocks = _split_outlook_blocks(two_blocks)
    assert len(blocks) == 2
    assert blocks[0].startswith("**From:** Bob")
    assert blocks[1].startswith("**From:** Alice")


def test_path_b_outlook_splitter_discards_preamble_before_first_boundary() -> None:
    from dead_letter.core._pipeline import _split_outlook_blocks

    with_preamble = (
        "**External Sender Warning:** verify before replying\n\n"
        "**From:** Bob <bob@example.com>\n"
        "**Sent:** Wed\n"
        "**To:** Team\n"
        "**Subject:** y\n\n"
        "Bob text.\n"
    )
    blocks = _split_outlook_blocks(with_preamble)
    assert len(blocks) == 1
    assert blocks[0].startswith("**From:** Bob")
    assert "External Sender Warning" not in blocks[0]


def test_path_b_skips_split_when_only_quoted_content_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Regression for Codex P1 (PR #24 r3301133479).

    When Path B segmentation produces only QUOTED zones (no body), the
    STRUCTURED-mode splitter must not mutate them. Otherwise it strips
    leading ``---`` projections and ``.strip()``s the content without
    leaving a ``_quoted_original`` snapshot, and the render fallback
    diverges from LATEST output.
    """
    from dead_letter.core.conversation import ConversationResult
    from dead_letter.core.types import ConversationZone

    # Synthetic QUOTED-only result with content that would be mutated by
    # ``_split_path_b_quoted_zone`` (leading ``---`` projection + outer
    # whitespace) if the guard were absent.
    quoted_content = "---\n\nQuoted body without a latest message.\n"

    def _only_quoted_result(_html: str, *, client_hint: str | None = None) -> ConversationResult:
        return ConversationResult(
            zones=[
                ConversationZone(
                    kind=ZoneKind.QUOTED,
                    content=quoted_content,
                    source_kind="plain",
                    confidence=0.8,
                )
            ],
            client_hint="generic",
            rules_triggered=["test-only-quoted"],
        )

    monkeypatch.setattr(pipeline_module, "segment_html_conversation", _only_quoted_result)
    parsed = _make_parsed("<html><body>doesn't matter — segmenter is patched</body></html>", tmp_path)

    latest_threaded, _r1, _d1 = pipeline_module._threaded_content_from_conversation(
        parsed, ConvertOptions()
    )
    structured_threaded, _r2, _d2 = pipeline_module._threaded_content_from_conversation(
        parsed, ConvertOptions(thread_mode=ThreadMode.STRUCTURED)
    )

    assert latest_threaded is not None and structured_threaded is not None
    # Same zone count and same content — STRUCTURED must not mutate when
    # there's nothing structured to render.
    assert len(latest_threaded.zones) == len(structured_threaded.zones)
    for latest_zone, structured_zone in zip(latest_threaded.zones, structured_threaded.zones):
        assert latest_zone.kind is structured_zone.kind
        assert latest_zone.content == structured_zone.content, (
            "STRUCTURED mutated QUOTED-only content: LATEST=%r STRUCTURED=%r"
            % (latest_zone.content, structured_zone.content)
        )
