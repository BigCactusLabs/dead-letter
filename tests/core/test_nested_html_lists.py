from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest

from dead_letter.core import convert


@pytest.mark.parametrize(
    ("name", "html", "expected"),
    [
        (
            "unordered-depth-2",
            "<ul><li>Top level one</li><li>Top level two<ul>"
            "<li>Child A</li><li>Child B</li></ul></li>"
            "<li>Top level three</li></ul>",
            "- Top level one\n"
            "- Top level two\n"
            "  - Child A\n"
            "  - Child B\n"
            "- Top level three",
        ),
        (
            "ordered-depth-2",
            "<ol><li>One</li><li>Two<ol><li>Child A</li>"
            "<li>Child B</li></ol></li><li>Three</li></ol>",
            "1. One\n2. Two\n   1. Child A\n   2. Child B\n3. Three",
        ),
        (
            "unordered-containing-ordered",
            "<ul><li>Top level one</li><li>Top level two<ol>"
            "<li>Child A</li><li>Child B</li></ol></li>"
            "<li>Top level three</li></ul>",
            "- Top level one\n"
            "- Top level two\n"
            "  1. Child A\n"
            "  2. Child B\n"
            "- Top level three",
        ),
        (
            "unordered-depth-3",
            "<ul><li>L1<ul><li>L2<ul><li>L3a</li><li>L3b</li>"
            "</ul></li></ul></li><li>L1 second</li></ul>",
            "- L1\n  - L2\n    - L3a\n    - L3b\n- L1 second",
        ),
    ],
)
def test_convert_html_nested_lists_preserve_commonmark_indentation(
    tmp_path: Path,
    name: str,
    html: str,
    expected: str,
) -> None:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["To"] = "recipient@example.com"
    message["Subject"] = "Nested list test"
    message["Date"] = "Wed, 19 Aug 2026 12:00:00 +0000"
    message["Message-ID"] = f"<{name}@example.com>"
    message.set_content("plain fallback")
    message.add_alternative(f"<html><body>{html}</body></html>", subtype="html")

    source = tmp_path / f"{name}.eml"
    source.write_bytes(message.as_bytes())

    result = convert(source, output=tmp_path / "out")

    assert result.success is True
    assert result.output is not None
    assert result.output.read_text(encoding="utf-8").endswith(f"{expected}\n")
