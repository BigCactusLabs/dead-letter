"""Forwarded content is kept in latest mode and sectioned in structured mode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dead_letter.core import convert
from dead_letter.core.forwarding import (
    FORWARD_MARKER_PATTERNS,
    FORWARD_MARKER_RE,
    is_forward_marker_line,
    parse_forward_headers,
)
from dead_letter.core.html_conversation import segment_html_conversation
from dead_letter.core.text_conversation import segment_text_conversation
from dead_letter.core.types import ConvertOptions, ThreadMode, ThreadOrder, ZoneKind

FIXTURES = Path(__file__).parent / "fixtures"
MARKER_LINE = "---------- Forwarded message ---------"


def _body(name: str, tmp_path: Path, **options: object) -> tuple[str, str]:
    output = tmp_path / f"{name}.md"
    result = convert(FIXTURES / name, output=output, options=ConvertOptions(**options))
    assert result.success, result.error
    assert result.output is not None
    _, front_matter, body = Path(result.output).read_text(encoding="utf-8").split("---\n", 2)
    return front_matter, body


def _positions(text: str, needles: list[str]) -> list[int]:
    positions = [text.index(needle) for needle in needles]
    assert positions == sorted(positions), positions
    return positions


# --- Shared marker constant -------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "---------- Forwarded message ---------",
        "---------- Forwarded message ----------",
        "-------- Forwarded Message --------",
        "-- Forwarded message --",
        "Begin forwarded message:",
        "---------- Forwarded message ---------  ",
        "---------- Forwarded message ---------\\",
        "---------- Forwarded\u00a0message ---------",
        "> Begin forwarded message:",
        "-------- Weitergeleitete Nachricht --------",
        "-------- Message transféré --------",
        "----- Messaggio inoltrato -----",
        "-------- 转发的消息 --------",
        "Anfang der weitergeleiteten Nachricht:",
        "Début du message réexpédié :",
    ],
)
def test_forward_marker_lines_recognized(line: str) -> None:
    assert is_forward_marker_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "Forwarded message",
        "- Forwarded message -",
        "On Thu, Mar 5, 2026 Alice wrote:",
        "-----Original Message-----",
        "Fwd: Vendor Note",
        "",
    ],
)
def test_non_marker_lines_rejected(line: str) -> None:
    assert not is_forward_marker_line(line)


def test_marker_patterns_drive_plain_regex() -> None:
    # Adding a marker is one tuple entry; the compiled regex must be built from it.
    assert len(FORWARD_MARKER_PATTERNS) >= 2
    for pattern in FORWARD_MARKER_PATTERNS:
        assert pattern in FORWARD_MARKER_RE.pattern


def test_parse_forward_headers_requires_from() -> None:
    parsed = parse_forward_headers("From: A <a@example.com>  \nDate: today  \nSubject: Hi\n\nBody")
    assert parsed is not None
    fields, rest = parsed
    assert fields == {"from": "A <a@example.com>", "date": "today", "subject": "Hi"}
    assert rest == "Body"
    assert parse_forward_headers("Subject: Hi\n\nBody") is None


# --- Segmentation units ------------------------------------------------------


def _fwd(n: int, inner: str = "") -> str:
    return (
        f'<div class="gmail_quote"><div class="gmail_attr">{MARKER_LINE}<br>'
        f"From: P{n} &lt;p{n}@example.com&gt;<br>Subject: S{n}<br></div>"
        f"<div>Body {n}.</div>{inner}</div>"
    )


def _kinds(html: str) -> list[ZoneKind]:
    return [zone.kind for zone in segment_html_conversation(html).zones]


def test_gmail_attr_without_direct_blockquote_is_forward_in_any_language() -> None:
    # Structural rule: the attribution wording plays no part.
    html = (
        '<div>Note.</div><div class="gmail_quote gmail_quote_container">'
        '<div class="gmail_attr">Nachricht weitergeleitet<br>Von: A</div><div>Body.</div></div>'
    )
    assert _kinds(html) == [ZoneKind.BODY, ZoneKind.FORWARDED_BODY]


def test_gmail_attr_with_direct_blockquote_is_reply() -> None:
    html = (
        '<div>Note.</div><div class="gmail_quote gmail_quote_container">'
        f'<div class="gmail_attr">{MARKER_LINE}<br></div>'
        '<blockquote class="gmail_quote">Old.</blockquote></div>'
    )
    assert _kinds(html) == [ZoneKind.BODY, ZoneKind.QUOTED]


def test_legacy_gmail_forward_is_leading_separator_text() -> None:
    html = (
        f'<div>Note.</div><div class="gmail_quote">{MARKER_LINE}<br>'
        'From: <b class="gmail_sendername">Bob</b><br><div>Body.</div></div>'
    )
    assert _kinds(html) == [ZoneKind.BODY, ZoneKind.FORWARDED_BODY]


def test_gmail_quote_without_attr_or_separator_keeps_reply_handling() -> None:
    html = '<div>Note.</div><div class="gmail_quote"><div>Body.</div></div>'
    assert _kinds(html) == [ZoneKind.BODY, ZoneKind.QUOTED]


def test_html_segmentation_splits_nested_forwards_outermost_first() -> None:
    html = "<div>Note.</div>" + _fwd(1, _fwd(2, _fwd(3)))
    result = segment_html_conversation(html)

    kinds = [zone.kind for zone in result.zones]
    assert kinds == [ZoneKind.BODY, *[ZoneKind.FORWARDED_BODY] * 3]
    for index, zone in enumerate(result.zones[1:], start=1):
        assert f"Body {index}." in zone.content
        # Each block excludes the nested forwards that were split out.
        for other in {1, 2, 3} - {index}:
            assert f"Body {other}." not in zone.content
    assert "gmail_forward" in result.rules_triggered


def test_html_segmentation_reply_quote_unaffected() -> None:
    html = '<div>Hi</div><div class="gmail_quote">On Mon Alice wrote:<br>Old</div>'
    result = segment_html_conversation(html)

    assert [zone.kind for zone in result.zones] == [ZoneKind.BODY, ZoneKind.QUOTED]
    assert result.rules_triggered == ["gmail_quote"]


def test_html_segmentation_survives_deep_forward_nesting() -> None:
    html = "<div>Note.</div>"
    for n in range(300, 0, -1):
        html = _fwd(n, html if n != 300 else "")
    result = segment_html_conversation("<div>Top.</div>" + html)

    forwards = [zone for zone in result.zones if zone.kind is ZoneKind.FORWARDED_BODY]
    assert 1 <= len(forwards) <= 64
    # Past the cap, nested forwards stay inside the last block: nothing is lost.
    assert "Body 300." in forwards[-1].content


def test_plain_segmentation_splits_every_marker_when_requested() -> None:
    text = f"Note.\n\n{MARKER_LINE}\nFrom: A\n\nOne.\n\nBegin forwarded message:\n\nFrom: B\n\nTwo."
    split = segment_text_conversation(text, split_forwards=True)
    single = segment_text_conversation(text)

    assert [zone.kind for zone in split.zones] == [
        ZoneKind.BODY,
        ZoneKind.FORWARD_HEADER,
        ZoneKind.FORWARDED_BODY,
        ZoneKind.FORWARD_HEADER,
        ZoneKind.FORWARDED_BODY,
    ]
    assert "Two." not in split.zones[2].content
    assert [zone.kind for zone in single.zones] == [
        ZoneKind.BODY,
        ZoneKind.FORWARD_HEADER,
        ZoneKind.FORWARDED_BODY,
    ]
    assert "Two." in single.zones[2].content


def test_plain_quoted_marker_split_only_without_attribution() -> None:
    apple = "See below.\n\n> Begin forwarded message:\n>\n> From: Shop <s@example.com>\n>\n> Thanks."
    reply = f"Thanks.\n\nOn Mon, Alice wrote:\n> {MARKER_LINE}\n> From: Shop\n>\n> Old."

    split = segment_text_conversation(apple, split_forwards=True)
    assert [zone.kind for zone in split.zones] == [
        ZoneKind.BODY,
        ZoneKind.FORWARD_HEADER,
        ZoneKind.FORWARDED_BODY,
    ]
    assert split.zones[2].content == "From: Shop <s@example.com>\n\nThanks."
    # Latest mode keeps its previous handling of quoted markers.
    assert not any(
        zone.kind is ZoneKind.FORWARD_HEADER for zone in segment_text_conversation(apple).zones
    )
    # A reply that quotes a forward stays reply history.
    assert not any(
        zone.kind is ZoneKind.FORWARD_HEADER
        for zone in segment_text_conversation(reply, split_forwards=True).zones
    )


# --- End-to-end table rows ---------------------------------------------------


def test_gmail_reply_quote_dropped_in_latest_and_sectioned_in_structured(tmp_path: Path) -> None:
    _, latest = _body("gmail_quote.eml", tmp_path, thread_mode="latest")
    front, structured = _body("gmail_quote.eml", tmp_path, thread_mode="structured")

    assert "Latest response" in latest
    assert "On prior mail wrote" not in latest
    assert "thread_messages: 1" in front
    assert "## Earlier message" in structured


def test_single_gmail_forward_latest_keeps_marker_headers_and_body(tmp_path: Path) -> None:
    front, body = _body("gmail_forward_single.eml", tmp_path)

    assert "thread_messages" not in front
    _positions(
        body,
        [
            "FYI, see the vendor note below.",
            MARKER_LINE,
            "Date: Thu, Mar 5, 2026 at 8:00 AM",
            "Subject: Vendor Note",
            "Please review the attached quote.",
        ],
    )


def test_single_gmail_forward_structured_has_one_section(tmp_path: Path) -> None:
    front, body = _body("gmail_forward_single.eml", tmp_path, thread_mode="structured")

    assert "thread_messages: 1" in front
    assert "Forwarded message ---" not in body
    assert body.count("## ") == 1
    assert (
        "## Forwarded from **Vendor** &lt;vendor@example.net&gt; "
        "(Thu, Mar 5, 2026 at 8:00 AM) — Vendor Note\n\nPlease review the attached quote."
    ) in body
    _positions(body, ["FYI, see the vendor note below.", "## Forwarded from"])


def test_french_gmail_forward_detected_structurally(tmp_path: Path) -> None:
    _, latest = _body("gmail_forward_french.eml", tmp_path)
    front, structured = _body("gmail_forward_french.eml", tmp_path, thread_mode="structured")

    _positions(latest, ["Pour info.", MARKER_LINE, "De : **Expéditeur**", "Le programme du stage est joint."])
    assert "thread_messages: 1" in front
    assert "Forwarded message ---" not in structured
    # The localized From label is not parsed, so the header lines stay as text.
    _positions(
        structured,
        ["Pour info.", "## Forwarded message", "De : **Expéditeur**", "Le programme du stage est joint."],
    )


@pytest.mark.parametrize(
    ("name", "needles"),
    [
        (
            "thunderbird_forward.eml",
            ["Minutes attached below.", "-------- Forwarded Message --------", "Erin", "Minutes from the Wednesday"],
        ),
        (
            "apple_mail_forward.eml",
            ["See below.", "Begin forwarded message:", "Shop", "Thank you for your order."],
        ),
    ],
)
def test_thunderbird_and_apple_forwards_kept_in_latest(
    name: str, needles: list[str], tmp_path: Path
) -> None:
    _, body = _body(name, tmp_path)
    _positions(body, needles)


@pytest.mark.parametrize(
    ("name", "header", "last_line"),
    [
        ("thunderbird_forward.eml", "## Forwarded message", "Minutes from the Wednesday meeting."),
        ("apple_mail_forward.eml", "## Forwarded from Shop &lt;shop@example.com&gt; — **Receipt**",
         "Thank you for your order."),
    ],
)
def test_thunderbird_and_apple_forwards_sectioned_in_structured(
    name: str, header: str, last_line: str, tmp_path: Path
) -> None:
    front, body = _body(name, tmp_path, thread_mode="structured")

    assert "thread_messages: 1" in front
    assert header in body
    assert body.strip().splitlines()[-1] == last_line


@pytest.mark.parametrize("name", ["gmail_forwards_sequential.eml", "gmail_forwards_nested.eml"])
def test_multiple_gmail_forwards_latest_keeps_all_in_document_order(name: str, tmp_path: Path) -> None:
    _, body = _body(name, tmp_path)

    assert body.count(MARKER_LINE) == 3
    _positions(
        body,
        [
            "Person One",
            "Body from person one.",
            "Person Two",
            "Body from person two.",
            "Person Three",
            "Body from person three.",
        ],
    )


@pytest.mark.parametrize("name", ["gmail_forwards_sequential.eml", "gmail_forwards_nested.eml"])
@pytest.mark.parametrize("order", list(ThreadOrder))
def test_multiple_gmail_forwards_structured_one_section_each_in_document_order(
    name: str, order: ThreadOrder, tmp_path: Path
) -> None:
    front, body = _body(name, tmp_path, thread_mode="structured", thread_order=order)

    assert "thread_messages: 3" in front
    assert "Forwarded message" not in body
    assert body.count("## Forwarded from ") == 3
    _positions(
        body,
        [
            "## Forwarded from Person One",
            "Body from person one.",
            "## Forwarded from Person Two",
            "Body from person two.",
            "## Forwarded from Person Three",
            "Body from person three.",
        ],
    )
    # Every section ends with its own body, never a stranded marker.
    for section in body.split("## ")[1:]:
        assert section.strip().splitlines()[-1].startswith("Body from person")


def test_forward_containing_reply_quote_keeps_inner_reply(tmp_path: Path) -> None:
    _, latest = _body("gmail_forward_with_reply_quote.eml", tmp_path)
    front, structured = _body("gmail_forward_with_reply_quote.eml", tmp_path, thread_mode="structured")

    for body in (latest, structured):
        _positions(body, ["Dave approves the budget.", "Carol asks about the budget."])
    assert "thread_messages: 1" in front
    section = structured.split("## Forwarded from Dave", 1)[1]
    assert "Carol asks about the budget." in section


def test_plain_text_multiple_markers(tmp_path: Path) -> None:
    front_latest, latest = _body("plain_forwards_multiple.eml", tmp_path)
    front, structured = _body("plain_forwards_multiple.eml", tmp_path, thread_mode="structured")

    assert "thread_messages" not in front_latest
    _positions(latest, ["Body from person one.", "Body from person two.", "Body from person three."])
    assert "thread_messages: 3" in front
    assert "Forwarded message" not in structured
    assert "Begin forwarded message" not in structured
    _positions(
        structured,
        [
            "Digest of notes below.",
            "## Forwarded from Person One &lt;one@example.com&gt; (Mon, Mar 2, 2026 at 9:00 AM) — Note one",
            "## Forwarded from Person Two",
            "## Forwarded from Person Three &lt;three@example.com&gt; (March 4, 2026 at 9:00:00 AM EST)",
        ],
    )


# --- Byte identity for output that must not change ---------------------------


_GOLDEN = json.loads((FIXTURES / "pre_forward_fix_golden.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", sorted(_GOLDEN))
def test_output_that_must_not_change_is_byte_identical(key: str, tmp_path: Path) -> None:
    """Goldens were captured from main before forward handling changed.

    Covers every fixture without a forward, a reply quote that contains a
    forward (still reply history), and latest-mode output for plain-text,
    Thunderbird and Apple Mail forwards, which were already kept.
    """
    name, mode, order = key.split("|")
    output = tmp_path / "out.md"
    result = convert(
        FIXTURES / name,
        output=output,
        options=ConvertOptions(thread_mode=ThreadMode(mode), thread_order=ThreadOrder(order)),
    )
    assert result.success, result.error
    assert output.read_text(encoding="utf-8") == _GOLDEN[key]
