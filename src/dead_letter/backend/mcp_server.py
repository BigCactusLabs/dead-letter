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


_CONVERSION_FLAGS = frozenset({
    "strip_signatures", "strip_disclaimers", "strip_tracking_pixels",
    "strip_signature_images", "strip_quoted_headers", "embed_inline_images",
    "include_all_headers", "include_raw_html", "no_calendar_summary",
    "dry_run",
})


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


def _build_options(local_vars: dict) -> ConvertOptions:
    """Build ConvertOptions from a tool function's local variables.

    Extracts ``preset`` and any recognized conversion flags from the dict
    (typically ``locals()``), ignoring unrelated tool parameters.
    """
    return _resolve_options(
        local_vars.get("preset", "default"),
        **{k: local_vars[k] for k in _CONVERSION_FLAGS if k in local_vars},
    )


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

    Presets bundle common flag combinations:
    - default: strips signatures, tracking pixels, signature images
    - clean: default + strips disclaimers and quoted headers
    - verbose: includes all headers and raw HTML
    - raw: no stripping, preserves everything

    Individual flags override the preset when provided.
    """
    options = _build_options(locals())
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
    and optionally the original .eml source.

    source_handling controls the original .eml:
    - copy (default): copy into bundle, leave original untouched
    - move: move original into bundle
    - delete: remove original after successful conversion

    Returns JSON with bundle_path, markdown_path, attachment_paths, and
    optional diagnostics.
    """
    options = _build_options(locals())
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

    Recursively finds all .eml files and converts them. Returns a JSON
    summary with total, successes, failures, output_paths, and errors.

    Use convert_eml to retrieve individual converted file content.
    """
    options = _build_options(locals())
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


@mcp.tool()
def get_diagnostics(
    eml_path: str,
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
    """Inspect email quality and structure without writing permanent files.

    Use this to assess conversion quality before committing, or to
    troubleshoot problematic .eml files.

    Returns JSON with: state (normal/degraded/review_recommended),
    selected_body, segmentation_path, client_hint, confidence,
    warnings, and stripped_images.
    """
    source = Path(eml_path)
    if not source.exists():
        raise FileNotFoundError(f"File not found: {eml_path}")

    options = _build_options(locals())

    with tempfile.TemporaryDirectory() as tmp:
        result, diagnostics = convert_to_bundle_with_diagnostics(
            source,
            bundle_root=Path(tmp) / "bundle",
            options=options,
            source_handling="copy",
        )
        _raise_on_failure(result)

    if diagnostics is None:
        raise RuntimeError("Diagnostics unavailable for successful conversion.")

    return json.dumps(diagnostics, indent=2, default=str)


def main() -> None:
    """Entry point for the dead-letter-mcp console script."""
    mcp.run()
