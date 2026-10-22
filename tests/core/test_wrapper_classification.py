"""Issue #233 acceptance: positive evidence on the original HTML tree."""

from email.message import EmailMessage
from pathlib import Path

import pytest

from dead_letter.core import convert
from dead_letter.core._html_parser import parse_html
from dead_letter.core.html_conversation import segment_html_conversation
from dead_letter.core.quotes import detect_quote_patterns
from dead_letter.core.types import ConvertOptions, ZoneKind

MARKER = "----- Forwarded Message -----"
BOB = "On Mon, Mar 2, 2026 at 9:00 AM Bob wrote:"
CAROL = "On Sun, Mar 1, 2026 at 9:00 AM Carol wrote:"
CLASSES = ["gmail_quote", "protonmail_quote", "yahoo_quoted"]


def _render(tmp_path, html, mode):
    source = tmp_path / "synthetic.eml"
    output = tmp_path / "out.md"
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["Subject"] = "Synthetic wrapper"
    message.set_content(html, subtype="html")
    source.write_bytes(bytes(message))
    result = convert(source, output=output, options=ConvertOptions(thread_mode=mode))
    assert result.success, result.error
    _, meta, body = output.read_text().split("---\n", 2)
    return meta, body.strip()


def _forward(payload="KEEP PAYLOAD."):
    return f'<div class="gmail_quote"><div class="gmail_attr"><b>{MARKER}</b><br>From: Bob<br>Date: Wed, Mar 4, 2026 at 9:00 AM<br>Subject: Report<br></div><div>{payload}</div></div>'


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_r1_f1_body_wrapper_note_before_nested_forward(tmp_path, mode):
    html = (
        '<div>Hi</div><div class="gmail_quote"><div>KEEP THIS NOTE.</div>'
        + _forward("FORWARD PAYLOAD. " * 30)
        + "</div>"
    )
    meta, body = _render(tmp_path, html, mode)
    assert (
        body.index("Hi")
        < body.index("KEEP THIS NOTE.")
        < body.index("FORWARD PAYLOAD.")
    )
    if mode == "structured":
        assert "thread_messages: 1" in meta
        assert body.index("KEEP THIS NOTE.") < body.index("## Forwarded from Bob")
        assert "Earlier message" not in body


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("interleaved", [False, True])
def test_r1_f2_and_case_j_independent_sibling_replies(tmp_path, mode, cls, interleaved):
    html = f'<div>Thanks.</div><div class="{cls}">{BOB}<br><blockquote>BOB HISTORY</blockquote></div><div>BETWEEN.</div><div class="{cls}">{CAROL}<br><blockquote>CAROL HISTORY</blockquote></div><div>AFTER.</div>'
    if not interleaved:
        html = html.replace("<div>BETWEEN.</div>", "").replace("<div>AFTER.</div>", "")
    meta, body = _render(tmp_path, html, mode)
    if interleaved:
        assert body.index("Thanks.") < body.index("BETWEEN.") < body.index("AFTER.")
    if mode == "latest":
        assert "HISTORY" not in body
        if not interleaved:
            assert body == "Thanks."
    else:
        assert "thread_messages: 2" in meta
        assert body.index("CAROL HISTORY") < body.index("BOB HISTORY")
        if interleaved:
            assert body.index("AFTER.") < body.index("CAROL HISTORY")


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("cls", ["protonmail_quote", "yahoo_quoted", "x_gmail_quote"])
def test_r2_f1_sibling_current_answer(tmp_path, mode, cls):
    answer = (
        "<div>My answer:</div><div>APPROVE THE ORDER.</div>"
        if cls != "x_gmail_quote"
        else "MY CURRENT ANSWER."
    )
    html = f'<div>Please see my answer below.</div><div class="{cls}">{BOB}<br><blockquote>OLD HISTORY</blockquote></div><div class="{cls}">{answer}</div>'
    meta, body = _render(tmp_path, html, mode)
    current = "APPROVE THE ORDER." if cls != "x_gmail_quote" else "MY CURRENT ANSWER."
    assert current in body
    if mode == "latest":
        assert "OLD HISTORY" not in body
    else:
        assert "thread_messages: 1" in meta
        assert (
            body.index(current) < body.index("## From Bob") < body.index("OLD HISTORY")
        )


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("cls", ["protonmail_quote", "yahoo_quoted"])
@pytest.mark.parametrize("count", [64, 65])
def test_r2_f3_no_first_line_scan_cap(tmp_path, mode, cls, count):
    html = (
        f'<div>FYI.</div><div class="{cls}">'
        + "<span></span>" * count
        + f'{MARKER}<br>From: Bob<br><br><blockquote class="gmail_quote">KEEP FORWARDED PAYLOAD.</blockquote></div>'
    )
    meta, body = _render(tmp_path, html, mode)
    assert "KEEP FORWARDED PAYLOAD." in body
    assert "Bob" in body
    assert "Earlier message" not in body
    if mode == "structured":
        assert "thread_messages: 1" in meta
        assert "## Forwarded from Bob" in body


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("count", [0, 64, 65])
def test_r3_f1_bold_separator_all_18_combinations(tmp_path, mode, cls, count):
    html = (
        f'<div>FYI.</div><div class="{cls}">'
        + "<span></span>" * count
        + _forward()
        + "</div>"
    )
    meta, body = _render(tmp_path, html, mode)
    assert "KEEP PAYLOAD." in body and "Bob" in body and "Report" in body
    assert "Earlier message" not in body
    if mode == "structured":
        assert "thread_messages: 1" in meta
        assert body.index("## Forwarded from Bob") < body.index("KEEP PAYLOAD.")
    else:
        assert "From: Bob" in body and "Date:" in body and "Subject: Report" in body


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("cls", CLASSES)
def test_r4_f1_heading_is_not_attribution(tmp_path, mode, cls):
    html = (
        f'<div>Hi.</div><div class="{cls}"><div>My answer:</div>'
        + "KEEP CURRENT ANSWER. " * 20
        + "</div>"
    )
    meta, body = _render(tmp_path, html, mode)
    assert "My answer:" in body and body.count("KEEP CURRENT ANSWER.") == 20
    assert "thread_messages:" not in meta


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_r4_f2_attribution_in_nested_wrapper(tmp_path, mode):
    html = (
        f'<div>Thanks.</div><div class="gmail_quote"><div class="gmail_quote">{BOB}</div><div>'
        + "OLD HISTORY. " * 30
        + "</div></div>"
    )
    meta, body = _render(tmp_path, html, mode)
    if mode == "latest":
        assert body == "Thanks."
    else:
        assert "thread_messages: 1" in meta
        assert "## From Bob (Mon, Mar 2, 2026 at 9:00 AM)" in body
        assert body.count("OLD HISTORY.") == 30


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize(
    "cls",
    [
        "gmail_quote",
        "x_gmail_quote",
        "x_x_gmail_quote",
        "protonmail_quote",
        "yahoo_quoted",
        "ydp123yahoo_quoted",
        "prefixyahoo_quotedsuffix",
    ],
)
def test_research_a_f_g_h_i_structural_reply(tmp_path, mode, cls):
    attr = (
        "x_x_gmail_attr"
        if cls.startswith("x_x_")
        else "x_gmail_attr"
        if cls.startswith("x_")
        else "gmail_attr"
    )
    html = f'<div>Thanks!</div><div class="{cls}"><div class="{attr}">{BOB}</div><blockquote>OLD HISTORY</blockquote><div>KEEP AFTER.</div></div>'
    meta, body = _render(tmp_path, html, mode)
    assert "Thanks!" in body and "KEEP AFTER." in body
    if mode == "latest":
        assert "OLD HISTORY" not in body
    else:
        assert "thread_messages: 1" in meta
        assert body.index("KEEP AFTER.") < body.index("## From Bob")


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_research_c_gogcli_descendant_separator(tmp_path, mode):
    meta, body = _render(
        tmp_path,
        '<div>FYI</div><div class="gmail_quote"><div>---------- Forwarded message ---------<br>From: Bob<br>Subject: Report<br><br>PAYLOAD</div></div>',
        mode,
    )
    assert "PAYLOAD" in body and "Bob" in body
    if mode == "structured":
        assert "thread_messages: 1" in meta and "## Forwarded from Bob" in body


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_research_d_nested_signature_wrappers_are_body(tmp_path, mode):
    meta, body = _render(
        tmp_path,
        '<div class="gmail_quote"><div class="gmail_quote"><div class="gmail_signature">CURRENT SIGNATURE</div></div></div>',
        mode,
    )
    assert body == "CURRENT SIGNATURE"
    assert "thread_messages:" not in meta


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_research_e_proton_container_note(tmp_path, mode):
    html = '<div class="protonmail_quote"><div>Note</div><div class="protonmail_quote">------- Forwarded Message -------<br>From: Bob<br><blockquote class="protonmail_quote" type="cite">PAYLOAD</blockquote></div></div>'
    meta, body = _render(tmp_path, html, mode)
    assert body.index("Note") < body.index("PAYLOAD")
    if mode == "structured":
        assert "thread_messages: 1" in meta
        assert body.index("Note") < body.index("## Forwarded from Bob")


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize(
    "attrs",
    [
        'class="gmail_quote"',
        'class="protonmail_quote"',
        'class="yahoo_quoted"',
        'id="yahoo_quoted_123"',
        'class="gmail_quote_container"',
    ],
)
def test_research_h_k_no_signal_wrappers_are_body(tmp_path, mode, attrs):
    meta, body = _render(
        tmp_path,
        f"<div>Thanks!</div><div {attrs}>" + "UNATTRIBUTED TEXT. " * 100 + "</div>",
        mode,
    )
    assert body.startswith("Thanks!") and body.count("UNATTRIBUTED TEXT.") == 100
    assert "thread_messages:" not in meta


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize(
    "name", ["proton_forward", "proton_reply", "yahoo_forward", "yahoo_reply"]
)
def test_research_h_i_synthetic_client_fixtures(tmp_path, mode, name):
    source = Path(__file__).parent / "fixtures" / f"{name}.eml"
    output = tmp_path / "fixture.md"
    result = convert(source, output=output, options=ConvertOptions(thread_mode=mode))
    assert result.success, result.error
    text = output.read_text()
    if "forward" in name:
        assert "FORWARDED BODY" in text
        if mode == "structured":
            assert "thread_messages: 1" in text and "## Forwarded from" in text
    elif mode == "latest":
        assert "QUOTED REPLY" not in text
    else:
        assert "QUOTED REPLY" in text and "thread_messages: 1" in text


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_mobile_gmail_reply(tmp_path, mode):
    html = '<div dir="auto">Reply</div><div dir="auto"><div class="gmail_quote gmail_quote_container" dir="auto"><div dir="ltr" class="gmail_attr">On Thu, Oct 22, 2026 at 11:51 Quinn &lt;q@example.com&gt; wrote:<br></div><blockquote class="gmail_quote" style="margin:0 0 0 .8ex;border-left:1px #ccc solid;padding-left:1ex"><div>HISTORY</div></blockquote></div></div>'
    meta, body = _render(tmp_path, html, mode)
    if mode == "latest":
        assert body == "Reply"
    else:
        assert "thread_messages: 1" in meta and "HISTORY" in body


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_mobile_gmail_forward_without_body(tmp_path, mode):
    html = '<div><div><div class="gmail_quote"><div dir="ltr" class="gmail_attr">---------- Forwarded message ---------<br>From: <strong class="gmail_sendername" dir="auto">Sender</strong> <span dir="auto">&lt;s@example.com&gt;</span><br>Date: Wed, Oct 21, 2026 at 21:37<br>Subject: Report<br>To: &lt;q@example.com&gt;<br></div><div>PAYLOAD</div></div></div></div>'
    meta, body = _render(tmp_path, html, mode)
    assert "PAYLOAD" in body
    if mode == "structured":
        assert "thread_messages: 1" in meta and "## Forwarded from Sender" in body


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_r9_owa_boundary_owns_prefixed_wrapper(tmp_path, mode):
    html = '<div>Current</div><div id="divRplyFwdMsg">From: Bob</div><div class="x_x_gmail_quote">HISTORY</div><div>MORE HISTORY</div>'
    _, body = _render(tmp_path, html, mode)
    if mode == "latest":
        assert body == "Current"
    else:
        assert "HISTORY" in body and "MORE HISTORY" in body


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_r10_reply_only_render_fallback_preserves_quoted_provenance(tmp_path, mode):
    html = (
        f'<div class="gmail_quote">{BOB}<br><blockquote>ONLY CONTENT</blockquote></div>'
    )
    zones = segment_html_conversation(html).zones
    assert any(zone.kind is ZoneKind.QUOTED and "ONLY CONTENT" in zone.content for zone in zones)
    assert all(
        not parse_html(zone.content).text(strip=True)
        for zone in zones if zone.kind is ZoneKind.BODY
    )
    meta, body = _render(tmp_path, html, mode)
    assert "ONLY CONTENT" in body and "Bob" in body
    assert "thread_messages:" not in meta


@pytest.mark.parametrize(
    "cls,hint",
    [
        ("x_x_gmail_quote", "gmail"),
        ("protonmail_quote", "proton"),
        ("ydpABCyahoo_quoted", "yahoo"),
    ],
)
def test_new_quote_pattern_classes(cls, hint):
    assert hint in detect_quote_patterns(f'<div class="{cls}">Body</div>')


def test_yahoo_id_quote_pattern():
    assert "yahoo" in detect_quote_patterns('<div id="yahoo_quoted_1">Body</div>')


@pytest.mark.parametrize("count", [1000, 2000, 4000])
def test_r1_f3_deep_no_signal_wrappers(count):
    result = segment_html_conversation(
        '<div class="gmail_quote">' * count + "PAYLOAD" + "</div>" * count
    )
    assert len(result.zones) == 1 and result.zones[0].kind is ZoneKind.BODY
    assert result.zones[0].content == "<body>PAYLOAD</body>"


@pytest.mark.parametrize("count", [8000, 16000, 32000])
def test_r1_f4_long_leading_br_run(count):
    html = (
        '<div>Note.</div><div class="yahoo_quoted">'
        + " <br>" * count
        + f"{MARKER}<br>From: Bob<br><br>PAYLOAD</div>"
    )
    result = segment_html_conversation(html)
    assert [z.kind for z in result.zones] == [ZoneKind.BODY, ZoneKind.FORWARDED_BODY]
    assert "PAYLOAD" in result.zones[1].content


@pytest.mark.parametrize("cls", ["yahoo_quoted", "protonmail_quote"])
def test_r3_perf_deep_wrappers_long_br_run(cls):
    html = (
        "<div>Note.</div>"
        + f'<div class="{cls}">' * 60
        + " <br>" * 32000
        + f"{MARKER}<br>From: Bob<br><br>PAYLOAD"
        + "</div>" * 60
    )
    result = segment_html_conversation(html)
    assert [z.kind for z in result.zones] == [ZoneKind.BODY, ZoneKind.FORWARDED_BODY]
    assert result.zones[1].content.count("PAYLOAD") == 1


@pytest.mark.parametrize("cls", ["yahoo_quoted", "protonmail_quote"])
@pytest.mark.parametrize("count", [1000, 2000, 4000, 8000])
def test_r4_f3_whitespace_only_nested_wrappers(cls, count):
    html = (
        "<div>Note.</div>"
        + f'<div class="{cls}">' * count
        + (" " * 40 + "</div>") * count
        + "<div>END.</div>"
    )
    result = segment_html_conversation(html)
    assert len(result.zones) == 1
    assert "Note." in result.zones[0].content and "END." in result.zones[0].content


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("attrs", ['class="gmail_quote"', 'id="yahoo_quoted_123"'])
def test_structural_reply_keeps_other_wrapper_text(tmp_path, mode, attrs):
    html = (
        f"<div {attrs}><div>CURRENT BEFORE</div>"
        f'<div class="gmail_attr">{BOB}</div>'
        '<blockquote class="arbitrary-class">OLD HISTORY</blockquote>'
        "<div>CURRENT AFTER</div></div>"
    )
    meta, body = _render(tmp_path, html, mode)
    assert body.index("CURRENT BEFORE") < body.index("CURRENT AFTER")
    if mode == "latest":
        assert "OLD HISTORY" not in body and "Bob" not in body
    else:
        assert "thread_messages: 1" in meta
        assert body.index("CURRENT AFTER") < body.index("## From Bob")


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_yahoo_id_forward(tmp_path, mode):
    html = f'<div>FYI.</div><div id="yahoo_quoted_123">{MARKER}<br>From: Bob<br><br>PAYLOAD</div>'
    meta, body = _render(tmp_path, html, mode)
    assert "PAYLOAD" in body
    if mode == "structured":
        assert "thread_messages: 1" in meta and "## Forwarded from Bob" in body


@pytest.mark.parametrize(
    "marker", ["**----- Forwarded Message -----**", "__----- Forwarded Message -----__"]
)
def test_emphasized_separator_projection(marker):
    from dead_letter.core.forwarding import split_forward_marker

    assert split_forward_marker(marker + "\nFrom: Bob\nPAYLOAD") == (
        marker,
        "From: Bob\nPAYLOAD",
    )
