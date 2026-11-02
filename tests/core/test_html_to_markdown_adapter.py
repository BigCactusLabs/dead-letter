from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from html_to_markdown.options import WarningKind

from dead_letter.core import html_to_markdown_adapter
from dead_letter.core._html_parser import parse_html
from dead_letter.core.html_to_markdown_adapter import convert_html_to_markdown


def test_adapter_converts_html_with_dead_letter_markdown_options() -> None:
    markdown = convert_html_to_markdown("<h1>Subject</h1><pre><code>x = 1</code></pre>")

    assert markdown.startswith("# Subject")
    assert "```" in markdown


def test_adapter_accepts_v3_conversion_result_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        html_to_markdown_adapter,
        "convert",
        lambda *_args, **_kwargs: SimpleNamespace(content="Converted\n"),
    )

    markdown = convert_html_to_markdown("<p>Converted</p>")

    assert markdown == "Converted"


def test_adapter_accepts_early_v3_dict_result_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        html_to_markdown_adapter,
        "convert",
        lambda *_args, **_kwargs: {"content": "Converted\n"},
    )

    markdown = convert_html_to_markdown("<p>Converted</p>")

    assert markdown == "Converted"


def test_adapter_recovers_from_real_converter_depth_warning(monkeypatch) -> None:
    real_convert = html_to_markdown_adapter.convert

    def limited_convert(html, *, options):
        return real_convert(html, options=replace(options, max_depth=8))

    monkeypatch.setattr(html_to_markdown_adapter, "convert", limited_convert)
    warnings = []

    markdown = convert_html_to_markdown(
        "<div>" * 20 + "PAYLOAD" + "</div>" * 20, warnings=warnings
    )

    assert markdown == "PAYLOAD"
    assert [warning["code"] for warning in warnings] == ["html_depth_limit"]


@pytest.mark.parametrize("kind", ["depth_limit_exceeded", WarningKind.depth_limit_exceeded])
def test_adapter_accepts_dict_warning_shape(monkeypatch, kind) -> None:
    monkeypatch.setattr(html_to_markdown_adapter, "convert", lambda *_args, **_kwargs: {
        "content": "LOSSY", "warnings": [{"kind": kind}],
    })
    warnings = []

    markdown = convert_html_to_markdown(
        '<p>A <b>word</b> &amp; text.</p><pre>x\ny</pre><img alt="ALT">',
        warnings=warnings,
    )

    assert markdown == "A word & text.\n\nx\ny\n\nALT"
    assert warnings[0]["code"] == "html_depth_limit"


def test_adapter_keeps_non_skipping_warning_output(monkeypatch) -> None:
    monkeypatch.setattr(html_to_markdown_adapter, "convert", lambda *_args, **_kwargs:
        SimpleNamespace(content="**Converted**", warnings=[SimpleNamespace(kind="encoding_fallback")]))
    warnings = []

    markdown = convert_html_to_markdown("<b>Converted</b>", warnings=warnings)

    assert markdown == "**Converted**"
    assert warnings == []


@pytest.mark.parametrize("tag", ["div", "span", "blockquote"])
def test_adapter_bounds_converter_input_depth(monkeypatch, tag) -> None:
    real_convert = html_to_markdown_adapter.convert
    max_depth = 0

    def checked_convert(html, *, options):
        nonlocal max_depth
        root = parse_html(html).root
        stack = [(root, 1)]
        while stack:
            node, depth = stack.pop()
            if not node.tag.startswith("-"):
                max_depth = max(max_depth, depth)
            if node.mem_id != root.mem_id and node.next is not None:
                stack.append((node.next, depth))
            if node.child is not None:
                stack.append((node.child, depth + 1))
        return real_convert(html, options=options)

    monkeypatch.setattr(html_to_markdown_adapter, "convert", checked_convert)

    markdown = convert_html_to_markdown(f"<{tag}>" * 2000 + "PAYLOAD" + f"</{tag}>" * 2000)

    assert "PAYLOAD" in markdown
    assert max_depth <= html_to_markdown_adapter._MAX_HTML_ELEMENT_DEPTH
