"""Adapter around the html-to-markdown package."""

from __future__ import annotations

from html_to_markdown import ConversionOptions, convert


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


def convert_html_to_markdown(html: str) -> str:
    """Convert sanitized HTML to Markdown using dead-letter's stable options."""
    return _markdown_content(
        convert(
            html,
            options=ConversionOptions(
                heading_style="atx",
                code_block_style="backticks",
                output_format="markdown",
            ),
        )
    )
