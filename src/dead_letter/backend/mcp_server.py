"""MCP server for dead-letter .eml-to-markdown conversion."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from dead_letter.core import convert
from dead_letter.core.types import ConvertOptions

mcp = FastMCP("dead-letter")

PRESETS: dict[str, dict[str, bool]] = {
    "default": {
        "strip_signatures": True,
        "strip_tracking_pixels": True,
        "strip_signature_images": True,
    },
    "clean": {
        "strip_signatures": True,
        "strip_disclaimers": True,
        "strip_quoted_headers": True,
        "strip_tracking_pixels": True,
        "strip_signature_images": True,
    },
    "verbose": {
        "include_all_headers": True,
        "include_raw_html": True,
    },
    "raw": {},
}


def _resolve_options(preset: str = "default", **overrides: bool | None) -> ConvertOptions:
    """Build ConvertOptions from a preset name with optional flag overrides."""
    base = dict(PRESETS.get(preset, PRESETS["default"]))
    for key, value in overrides.items():
        if value is not None:
            base[key] = value
    # MCP operations always enable resilience
    base["allow_fallback_on_html_error"] = True
    base["allow_html_repair_on_panic"] = True
    return ConvertOptions(**base)


def _raise_on_failure(result: object) -> None:
    """Raise RuntimeError if a ConvertResult or BundleResult indicates failure."""
    if getattr(result, "success", True):
        return
    parts = [f"Conversion failed: {getattr(result, 'error', 'unknown error')}"]
    if getattr(result, "plain_text_fallback_available", None):
        parts.append("Plain text fallback is available.")
    if getattr(result, "html_repair_available", None):
        parts.append("HTML repair is available.")
    raise RuntimeError(" ".join(parts))


@mcp.tool()
def convert_eml(
    eml_path: str,
    output_path: str | None = None,
    preset: Literal["default", "clean", "verbose", "raw"] = "default",
    strip_signatures: bool | None = None,
    strip_disclaimers: bool | None = None,
    strip_tracking_pixels: bool | None = None,
    strip_signature_images: bool | None = None,
    strip_quoted_headers: bool | None = None,
    embed_inline_images: bool | None = None,
    include_all_headers: bool | None = None,
    include_raw_html: bool | None = None,
    no_calendar_summary: bool | None = None,
) -> str:
    """Convert a .eml email file to Markdown with YAML front matter.

    Returns the full Markdown content (front matter + body). When output_path
    is provided, also writes the file to disk.
    """
    options = _resolve_options(
        preset,
        strip_signatures=strip_signatures,
        strip_disclaimers=strip_disclaimers,
        strip_tracking_pixels=strip_tracking_pixels,
        strip_signature_images=strip_signature_images,
        strip_quoted_headers=strip_quoted_headers,
        embed_inline_images=embed_inline_images,
        include_all_headers=include_all_headers,
        include_raw_html=include_raw_html,
        no_calendar_summary=no_calendar_summary,
    )
    source = Path(eml_path)
    if not source.exists():
        raise FileNotFoundError(f"File not found: {eml_path}")

    if output_path is not None:
        result = convert(source, output=Path(output_path), options=options)
        _raise_on_failure(result)
        return result.output.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        result = convert(source, output=Path(tmp), options=options)
        _raise_on_failure(result)
        return result.output.read_text(encoding="utf-8")


def main() -> None:
    """Entry point for the dead-letter-mcp console script."""
    mcp.run()
