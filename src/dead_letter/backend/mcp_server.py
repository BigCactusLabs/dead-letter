"""MCP server for dead-letter .eml-to-markdown conversion."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from dead_letter.core import convert, convert_dir
from dead_letter.core._pipeline import convert_to_bundle_with_diagnostics
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


@mcp.tool()
def convert_eml_to_bundle(
    eml_path: str,
    bundle_root: str,
    source_handling: Literal["copy", "move", "delete"] = "copy",
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
    """Convert a .eml file to a self-contained bundle with markdown and attachments.

    Creates a directory containing the converted markdown, extracted attachments,
    and optionally the original .eml source. Returns JSON with paths and diagnostics.
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

    bundle_path = Path(bundle_root)
    bundle_path.mkdir(parents=True, exist_ok=True)

    result, diagnostics = convert_to_bundle_with_diagnostics(
        source,
        bundle_root=bundle_path,
        options=options,
        source_handling=source_handling,
    )
    _raise_on_failure(result)

    response: dict[str, object] = {
        "bundle_path": str(result.bundle),
        "markdown_path": str(result.markdown),
        "attachment_paths": [str(p) for p in result.attachments],
    }
    if diagnostics is not None:
        response["diagnostics"] = diagnostics
    return json.dumps(response, indent=2)


@mcp.tool()
def convert_directory(
    directory: str,
    output_directory: str | None = None,
    dry_run: bool = False,
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
    """Batch convert all .eml files in a directory to Markdown.

    Returns a JSON summary with counts and file paths. Use convert_eml
    to retrieve the content of individual converted files.
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
        dry_run=dry_run,
    )
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    out = Path(output_directory) if output_directory else None
    results = convert_dir(dir_path, output=out, options=options)

    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]

    summary = {
        "total": len(results),
        "successes": len(successes),
        "failures": len(failures),
        "output_paths": [str(r.output) for r in successes if r.output],
        "errors": [{"file": str(r.source), "error": r.error} for r in failures],
    }
    return json.dumps(summary, indent=2)


def main() -> None:
    """Entry point for the dead-letter-mcp console script."""
    mcp.run()
