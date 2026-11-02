"""Adapter around the html-to-markdown package."""

from __future__ import annotations

import re

from html_to_markdown import ConversionOptions, convert

from dead_letter.core._html_parser import parse_html
from dead_letter.core.html_conversation import _BLOCK_TAGS, _unwrap_preserving_boundaries

_LIST_ITEM_RE = re.compile(r"^( *)(?P<marker>[-+*]|\d+[.)])(?P<space> +)")
# Leave ample room below the converter's hard 1024-element traversal limit.
_MAX_HTML_ELEMENT_DEPTH = 512
_CONTENT_SKIPPING_WARNINGS = {
    "depth_limit_exceeded": "html_depth_limit",
    "truncated_input": "html_content_skipped",
}


def _flatten_deep_html(html: str, *, warnings: list[dict[str, str]] | None = None) -> str:
    tree = parse_html(html)
    stack = [(tree.root, 0, False)]
    unwrap = []
    while stack:
        node, parent_depth, in_pre = stack.pop()
        if node.next is not None:
            stack.append((node.next, parent_depth, in_pre))
        child = node.child
        depth = parent_depth + (not node.tag.startswith("-"))
        child_in_pre = in_pre or node.tag == "pre"
        # Reserve one level for block-boundary markers inserted by unwrapping.
        if depth >= _MAX_HTML_ELEMENT_DEPTH and not node.tag.startswith("-"):
            unwrap.append((node, in_pre))
            depth = parent_depth
        if child is not None:
            stack.append((child, depth, child_in_pre))
    # Plan before mutating: unwrapping changes the last child's next pointer.
    for node, in_pre in unwrap:
        _unwrap_preserving_boundaries(node, in_pre=in_pre)
    if unwrap and warnings is not None:
        warnings.append({
            "code": "html_depth_flattened",
            "message": "Deeply nested HTML was flattened; text was preserved and formatting beyond the depth limit was reduced",
            "severity": "warning",
        })
    return tree.html if unwrap else html


def _text_preserving_fallback(html: str) -> str:
    tree = parse_html(html)
    root = tree.body or tree.root
    stack = [(root, False)]
    parts: list[str] = []
    while stack:
        node, exiting = stack.pop()
        if exiting:
            if parts and parts[-1] != "\n\n":
                parts.append("\n\n")
            continue
        if node.mem_id != root.mem_id and node.next is not None:
            stack.append((node.next, False))
        if node.tag == "-text":
            parts.append(node.text_content or "")
        elif node.tag == "br":
            parts.append("\n")
        elif node.tag == "img":
            parts.append(node.attributes.get("alt") or "")
        else:
            if node.tag in _BLOCK_TAGS:
                if parts and parts[-1] != "\n\n":
                    parts.append("\n\n")
                stack.append((node, True))
            if node.child is not None:
                stack.append((node.child, False))
    return "".join(parts).strip()


def _skipping_warning_kinds(result: object) -> set[str]:
    warnings = result.get("warnings", []) if isinstance(result, dict) else getattr(result, "warnings", [])
    kinds = set()
    for warning in warnings:
        kind = warning.get("kind") if isinstance(warning, dict) else getattr(warning, "kind", "")
        kinds.add(str(getattr(kind, "value", kind)))
    return kinds & _CONTENT_SKIPPING_WARNINGS.keys()


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


def convert_html_to_markdown(
    html: str, *, warnings: list[dict[str, str]] | None = None
) -> str:
    """Convert sanitized HTML to Markdown using dead-letter's stable options."""
    flattened = _flatten_deep_html(html, warnings=warnings)
    result = convert(
        flattened,
        options=ConversionOptions(
            heading_style="atx",
            code_block_style="backticks",
            output_format="markdown",
            list_indent_width=2,
            bullets="-",
        ),
    )
    skipping = _skipping_warning_kinds(result)
    if skipping:
        if warnings is not None:
            for kind in sorted(skipping):
                warnings.append({
                    "code": _CONTENT_SKIPPING_WARNINGS[kind],
                    "message": "HTML rendering reached a content limit; text was preserved with reduced formatting",
                    "severity": "warning",
                })
        return _text_preserving_fallback(flattened)
    markdown = _markdown_content(result)
    return _normalize_list_indentation(markdown)
