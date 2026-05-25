from __future__ import annotations

from types import SimpleNamespace

from dead_letter.core import html_to_markdown_adapter
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
