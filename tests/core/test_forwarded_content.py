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


def test_apple_bold_header_block_ends_at_plain_header_like_line() -> None:
    text = (
        "**From:** Shop <shop@example.com>\n\n**Subject:** **Receipt**\n\n"
        "Date: Friday is the pickup day\n\nThank you."
    )
    parsed = parse_forward_headers(text)
    assert parsed is not None
    fields, rest = parsed
    assert fields == {"from": "Shop <shop@example.com>", "subject": "Receipt"}
    assert rest == "Date: Friday is the pickup day\n\nThank you."


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


@pytest.mark.parametrize(
    "quote",
    [
        # Attribution plus a reply blockquote that is unclassed or not a direct child.
        '<div class="gmail_attr">On Wed, Mar 4, 2026 Bob &lt;<a>bob@example.com</a>&gt; wrote:<br></div>'
        '<blockquote style="margin:0">Old.</blockquote>',
        '<div class="gmail_attr">Le mer. 4 mars 2026, Bob a\u00a0écrit\u00a0:<br></div>'
        '<div><blockquote class="gmail_quote">Old.</blockquote></div>',
        # Chinese attribution ending in a fullwidth colon.
        '<div class="gmail_attr">Bob &lt;bob@example.com&gt; 于2026年3月4日周三 09:00写道：<br></div>'
        '<blockquote style="margin:0">Old.</blockquote>',
    ],
)
def test_gmail_attr_ending_in_colon_is_reply(quote: str) -> None:
    html = f'<div>Note.</div><div class="gmail_quote">{quote}</div>'
    assert _kinds(html) == [ZoneKind.BODY, ZoneKind.QUOTED]


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


def test_html_forwards_past_cap_are_kept_once_in_order(tmp_path: Path) -> None:
    html = "<div>Note.</div>" + "".join(
        f'<div class="gmail_quote"><div class="gmail_attr">{MARKER_LINE}<br>'
        f"From: P{n} &lt;p{n}@example.com&gt;<br></div><div>Note body from person {n}.</div></div>"
        for n in range(1, 71)
    )
    source = tmp_path / "many.eml"
    source.write_text(
        "From: a@example.com\nTo: b@example.com\nSubject: Fwd\n"
        "Date: Thu, 05 Mar 2026 10:00:00 +0000\nMIME-Version: 1.0\n"
        f"Content-Type: text/html; charset=utf-8\n\n{html}\n",
        encoding="utf-8",
    )
    for mode in ("latest", "structured"):
        result = convert(source, output=tmp_path / f"{mode}.md", options=ConvertOptions(thread_mode=mode))
        assert result.success, result.error
        body = Path(result.output).read_text(encoding="utf-8")
        needles = [f"Note body from person {n}." for n in range(1, 71)]
        for needle in needles:
            assert body.count(needle) == 1, (mode, needle)
        _positions(body, needles)


def test_marker_patterns_do_not_span_lines() -> None:
    assert not FORWARD_MARKER_RE.search("----\n\nForwarded message\n----")


@pytest.mark.parametrize(
    "text",
    [
        # Outlook reply history containing a localized separator.
        "Danke!\n\n________________________________\nFrom: Bob <b@example.com>\n"
        "Sent: Wednesday\nTo: Alice\nSubject: WG: P\n\nOld.\n\n"
        "-------- Weitergeleitete Nachricht --------\nVon: Erin\n\nText",
        # Separator-shaped line in prose, not followed by headers.
        "Hello,\nthe old subject said\n----- Mensaje reenviado -----\nand nothing else.\n\n"
        "On Wed, Mar 4, 2026 at 9:00 AM Bob <b@example.com> wrote:\n> Old.",
        # Indented separator inside quoted history.
        "Thanks\n\nOn Wed, Mar 4, 2026 at 9:00 AM Bob <b@example.com> wrote:\n> hi\n"
        f"    {MARKER_LINE}\n> more",
    ],
)
def test_plain_markers_after_reply_boundary_or_in_prose_are_not_forwards(text: str) -> None:
    for split in (False, True):
        zones = segment_text_conversation(text, split_forwards=split).zones
        assert not any(zone.kind is ZoneKind.FORWARD_HEADER for zone in zones)


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
    # Emphasis from <strong class="gmail_sendername"> is not carried into the
    # heading; To stays in the section content.
    assert (
        "## Forwarded from Vendor &lt;vendor@example.net&gt; "
        "(Thu, Mar 5, 2026 at 8:00 AM) — Vendor Note\n\n"
        "To: Alice <alice@example.com>  \n\nPlease review the attached quote."
    ) in body
    _positions(body, ["FYI, see the vendor note below.", "## Forwarded from"])


def test_real_gmail_sender_markup_gives_clean_heading(tmp_path: Path) -> None:
    _, body = _body("gmail_forward_body_headers.eml", tmp_path, thread_mode="structured")

    assert (
        "## Forwarded from Dave &lt;dave@example.com&gt; (Wed, Mar 4, 2026 at 10:00 AM) — Budget"
    ) in body
    assert "**Dave**" not in body.split("\n\n", 2)[1]


def test_forward_body_header_lines_and_recipients_are_kept(tmp_path: Path) -> None:
    _, body = _body("gmail_forward_body_headers.eml", tmp_path, thread_mode="structured")

    section = body.split("## Forwarded from Dave", 1)[1]
    _positions(
        section,
        [
            "To: <alice@example.com>",
            "Cc: Carol <carol@example.com>",
            "To: All staff",
            "Date: Friday is a holiday",
            "From: HR",
            "Office closed.",
        ],
    )
    assert "Subject: Budget" not in section
    assert "Date: Wed, Mar 4" not in section


@pytest.mark.parametrize(
    ("name", "heading", "kept"),
    [
        (
            "gmail_forward_empty_body.eml",
            "## Forwarded from Dave &lt;dave@example.com&gt; (Wed, Mar 4, 2026 at 10:00 AM) — Budget",
            ["To: <alice@example.com>", "Cc: Carol <carol@example.com>"],
        ),
        (
            "plain_forward_empty_body.eml",
            "## Forwarded from Dave &lt;dave@example.com&gt; (Wed, Mar 4, 2026) — Invoice",
            ["To: alice@example.com", "Cc: Carol &lt;carol@example.com&gt;"],
        ),
    ],
)
def test_empty_body_forward_keeps_heading_and_headers(
    name: str, heading: str, kept: list[str], tmp_path: Path
) -> None:
    front, body = _body(name, tmp_path, thread_mode="structured")

    assert "thread_messages: 1" in front
    _positions(body, [heading, *kept])


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "forwarded.eml",
            ["From: Vendor <vendor@example.net>", "Date: Thu, Mar 5, 2026 at 8:00 AM", "Subject: Vendor Note"],
        ),
        (
            "gmail_forward_body_headers.eml",
            ["From: **Dave** <<dave@example.com>>", "Date: Wed, Mar 4, 2026 at 10:00 AM", "Subject: Budget"],
        ),
    ],
)
def test_snapshot_keeps_forwarded_header_text(name: str, expected: list[str]) -> None:
    from dead_letter.core.snapshot import read_snapshot

    snapshot = read_snapshot(FIXTURES / name)
    forwarded = [zone for zone in snapshot.zones if zone.kind == "forwarded_body"]

    assert len(forwarded) == 1
    text = forwarded[0].text.replace("\r\n", "\n")
    _positions(text, expected)
    # Forwarded authors stay unknown to analysis readers.
    assert forwarded[0].author is None


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
        ("apple_mail_forward.eml", "## Forwarded from Shop &lt;shop@example.com&gt; — Receipt",
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


# Option sets the goldens were captured with (keys end in the preset name).
_PRESETS: dict[str, dict[str, bool]] = {
    "none": {},
    "default": {"strip_signatures": True, "strip_tracking_pixels": True, "strip_signature_images": True},
    "clean": {
        "strip_signatures": True,
        "strip_tracking_pixels": True,
        "strip_signature_images": True,
        "strip_disclaimers": True,
        "strip_quoted_headers": True,
    },
    "embed": {"embed_inline_images": True},
}
_GOLDEN = json.loads((FIXTURES / "pre_forward_fix_golden.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", sorted(_GOLDEN))
def test_output_that_must_not_change_is_byte_identical(key: str, tmp_path: Path) -> None:
    """Goldens were captured from main before forward handling changed.

    Covers every fixture without a forward (including reply markup that must
    not be mistaken for a forward, and separators inside reply history or
    prose), a reply quote that contains a forward, and latest-mode output for
    plain-text, Thunderbird and Apple Mail forwards, across option presets.
    """
    name, mode, order, preset = key.split("|")
    output = tmp_path / "out.md"
    result = convert(
        FIXTURES / name,
        output=output,
        options=ConvertOptions(
            thread_mode=ThreadMode(mode), thread_order=ThreadOrder(order), **_PRESETS[preset]
        ),
    )
    assert result.success, result.error
    assert output.read_text(encoding="utf-8") == _GOLDEN[key]
