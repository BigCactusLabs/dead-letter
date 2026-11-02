"""HTML text and boundary regressions for issue #268."""

from email.message import EmailMessage
import re
from time import perf_counter
from types import SimpleNamespace

import pytest

from dead_letter.core import convert, html_to_markdown_adapter
from dead_letter.core._pipeline import _build_rendered_markdown
from dead_letter.core.html_conversation import segment_html_conversation
from dead_letter.core.types import ConvertOptions


QUOTE = '<blockquote class="gmail_quote">OLD HISTORY</blockquote>'


def _render(tmp_path, html, mode):
    source = tmp_path / "synthetic.eml"
    output = tmp_path / "synthetic.md"
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["Subject"] = "HTML text preservation"
    message.set_content(html, subtype="html")
    source.write_bytes(bytes(message))
    started = perf_counter()
    result = convert(source, output=output, options=ConvertOptions(thread_mode=mode))
    elapsed = perf_counter() - started
    assert result.success, result.error
    body = result.output.read_text().split("---\n", 2)[2].strip()
    return result, body, elapsed


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("before, after", [
    ("code", "more"),
    ("code\n", "more"),
    ("code", "\nmore"),
    ("<span>code</span><span>", "</span>more"),
    ("<span>code\n</span><span>", "</span>more"),
    ("code<span>", "</span><span>\nmore</span>"),
    ("code\n<span></span>", "more"),
])
def test_removed_pre_quote_preserves_newline(tmp_path, mode, before, after):
    _, body, _ = _render(tmp_path, f"<pre>{before}{QUOTE}{after}</pre>", mode)

    assert body.split("\n\n## ", 1)[0] == "```\ncode\nmore\n```"
    assert body.count("OLD HISTORY") == (mode == "structured")


@pytest.mark.parametrize("before, after", [("code", "\n"), (" \n", "more"), (" ", "more")])
def test_removed_pre_quote_does_not_add_to_blank_edge(before, after):
    body = segment_html_conversation(f"<pre>{before}{QUOTE}{after}</pre>").zones[0].content

    assert body == f"<body><pre>{before}{after}</pre></body>"


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("attrs", [
    'class="gmail_quote"', 'class="protonmail_quote"',
    'class="yahoo_quoted"', 'id="yahoo_quoted_1"',
])
@pytest.mark.parametrize("template", [
    "W24<div{attrs}>W25</div>W26",
    "W24<div{attrs}>W25</div>",
    "<div{attrs}>W25</div>W26",
    "<div{attrs}>A</div><div{attrs}>B</div>",
    "<div{attrs}><div{attrs}>A</div>B</div>",
    "W24<div{attrs}><span>W25</span></div>W26",
    "<span>W24</span><div{attrs}>W25</div><em>W26</em>",
    "W24<div{attrs}><span></span>W25<span></span></div>W26",
    "<p>W24</p><div{attrs}>W25</div><p>W26</p>",
    "<pre>code<div{attrs}>W25</div>more</pre>",
    "<pre><div{attrs}>A</div><div{attrs}>B</div></pre>",
    "<pre><div{attrs}><div{attrs}>A</div>B</div></pre>",
    "W24<div{attrs}></div>W26",
])
def test_unwrapped_body_wrapper_matches_plain_div(tmp_path, mode, attrs, template):
    _, control, _ = _render(tmp_path, template.format(attrs="") + QUOTE, mode)
    _, actual, _ = _render(tmp_path, template.format(attrs=" " + attrs) + QUOTE, mode)

    assert actual == control


def test_unwrapped_body_wrapper_keeps_paragraph_boundaries(tmp_path):
    _, body, _ = _render(
        tmp_path, 'W24<div class="protonmail_quote">W25</div>W26' + QUOTE, "latest"
    )

    assert body == "W24\n\nW25\n\nW26"


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("html, expected", [
    ('W24<br><div class="protonmail_quote">W25</div><br>W26', "W24  \nW25  \nW26"),
    ('<pre>code\n<div class="protonmail_quote">W25</div>\nmore</pre>', "```\ncode\nW25\nmore\n```"),
])
def test_unwrapped_body_wrapper_does_not_duplicate_existing_breaks(tmp_path, mode, html, expected):
    _, body, _ = _render(tmp_path, html + QUOTE, mode)

    assert body.split("\n\n## ", 1)[0] == expected


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("with_quote", [False, True])
@pytest.mark.parametrize("tag", ["div", "section", "span", "blockquote"])
@pytest.mark.parametrize("depth", [1023, 1024, 1200, 2000, 5000])
def test_deep_html_keeps_text_at_every_level(tmp_path, mode, with_quote, tag, depth):
    space = " " if tag == "span" else ""
    html = "".join(f"<{tag}>L{i:05d}{space}" for i in range(depth)) + " PAYLOAD "
    html += "".join(f"{space}R{i:05d}</{tag}>" for i in reversed(range(depth)))
    if with_quote:
        html += QUOTE

    result, body, elapsed = _render(tmp_path, html, mode)

    assert re.findall(r"[LR]\d{5}|PAYLOAD", body) == [
        *(f"L{i:05d}" for i in range(depth)), "PAYLOAD",
        *(f"R{i:05d}" for i in reversed(range(depth))),
    ]
    assert body.count("OLD HISTORY") == (with_quote and mode == "structured")
    assert not re.search(r"[LR]\d{5}(?:[LR]\d{5}|PAYLOAD)|PAYLOAD[LR]\d{5}", body)
    _, _, _, diagnostics = _build_rendered_markdown(result.source, ConvertOptions(thread_mode=mode))
    assert not any(w["code"] == "html_depth_limit" for w in diagnostics["warnings"])
    if depth == 2000:
        assert elapsed < 2.0, f"{elapsed:.3f}s"


@pytest.mark.parametrize("mode", ["latest", "structured"])
def test_deep_extracted_history_keeps_payload(tmp_path, mode):
    html = '<p>CURRENT</p><blockquote class="gmail_quote">' + "<div>" * 2000
    html += "PAYLOAD" + "</div>" * 2000 + "</blockquote>"

    _, body, elapsed = _render(tmp_path, html, mode)

    assert body.count("PAYLOAD") == (mode == "structured")
    assert "CURRENT" in body
    assert elapsed < 2.0


@pytest.mark.parametrize("mode", ["latest", "structured"])
@pytest.mark.parametrize("with_quote", [False, True])
@pytest.mark.parametrize("kind, code", [
    ("depth_limit_exceeded", "html_depth_limit"),
    ("truncated_input", "html_content_skipped"),
])
def test_skipping_warning_falls_back_and_reaches_diagnostics(
    tmp_path, monkeypatch, mode, with_quote, kind, code,
):
    monkeypatch.setattr(html_to_markdown_adapter, "convert", lambda *_args, **_kwargs:
        SimpleNamespace(content="LOSSY", warnings=[SimpleNamespace(kind=kind)]))
    html = '<p>KEEP <em>inline</em> words &amp; entities.</p><div>TAIL</div>'
    if with_quote:
        html += QUOTE

    result, body, _ = _render(tmp_path, html, mode)

    assert "KEEP inline words & entities.\n\nTAIL" in body
    assert "LOSSY" not in body
    assert body.count("OLD HISTORY") == (with_quote and mode == "structured")
    _, _, _, diagnostics = _build_rendered_markdown(result.source, ConvertOptions(thread_mode=mode))
    assert diagnostics["state"] == "degraded"
    assert {w["code"] for w in diagnostics["warnings"]} == {code}
