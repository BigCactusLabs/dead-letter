"""Adapter around the html-to-markdown package."""

from __future__ import annotations

import re

from html_to_markdown import ConversionOptions, convert

_LIST_ITEM_RE = re.compile(r"^( *)(?P<marker>[-+*]|\d+[.)])(?P<space> +)")


def _markdown_content(result: object) -> str:
    if isinstance(result, str):
        return result.strip()

    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, str):
            return content.strip()

    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content.strip()

    raise TypeError(f"Unsupported html-to-markdown result: {type(result).__name__}")


def _normalize_list_indentation(markdown: str) -> str:
    """Indent nested list items beneath their parent marker content."""
    list_stack: list[tuple[int, int]] = []
    lines: list[str] = []
    in_fence = False

    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            lines.append(line)
            continue

        match = None if in_fence else _LIST_ITEM_RE.match(line)
        if match is None:
            lines.append(line)
            continue

        indent = len(match.group(1))
        if indent % 2:
            lines.append(line)
            continue

        depth = indent // 2
        if depth and len(list_stack) < depth:
            lines.append(line)
            continue

        if depth:
            parent_indent, parent_marker_width = list_stack[depth - 1]
            normalized_indent = parent_indent + parent_marker_width
        else:
            normalized_indent = 0

        marker_width = len(match.group("marker")) + len(match.group("space"))
        list_stack[depth:] = [(normalized_indent, marker_width)]
        lines.append(" " * normalized_indent + line[indent:])

    return "\n".join(lines)


def convert_html_to_markdown(html: str) -> str:
    """Convert sanitized HTML to Markdown using dead-letter's stable options."""
    markdown = _markdown_content(
        convert(
            html,
            options=ConversionOptions(
                heading_style="atx",
                code_block_style="backticks",
                output_format="markdown",
                list_indent_width=2,
                bullets="-",
            ),
        )
    )
    return _normalize_list_indentation(markdown)
