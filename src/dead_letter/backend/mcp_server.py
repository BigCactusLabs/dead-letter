"""MCP server for dead-letter .eml-to-markdown conversion."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

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


def main() -> None:
    """Entry point for the dead-letter-mcp console script."""
    mcp.run()
