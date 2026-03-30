"""Tests for the MCP server."""

from __future__ import annotations

from dead_letter.core.types import ConvertOptions


def test_resolve_options_default_preset():
    from dead_letter.backend.mcp_server import _resolve_options

    opts = _resolve_options("default")
    assert opts.strip_signatures is True
    assert opts.strip_tracking_pixels is True
    assert opts.strip_signature_images is True
    assert opts.strip_disclaimers is False
    assert opts.include_all_headers is False
    # MCP resilience defaults always on
    assert opts.allow_fallback_on_html_error is True
    assert opts.allow_html_repair_on_panic is True


def test_resolve_options_clean_preset():
    from dead_letter.backend.mcp_server import _resolve_options

    opts = _resolve_options("clean")
    assert opts.strip_signatures is True
    assert opts.strip_disclaimers is True
    assert opts.strip_quoted_headers is True
    assert opts.strip_tracking_pixels is True
    assert opts.strip_signature_images is True
    assert opts.embed_inline_images is False


def test_resolve_options_verbose_preset():
    from dead_letter.backend.mcp_server import _resolve_options

    opts = _resolve_options("verbose")
    assert opts.include_all_headers is True
    assert opts.include_raw_html is True
    assert opts.strip_signatures is False


def test_resolve_options_raw_preset():
    from dead_letter.backend.mcp_server import _resolve_options

    opts = _resolve_options("raw")
    assert opts.strip_signatures is False
    assert opts.strip_tracking_pixels is False
    assert opts.include_all_headers is False
    assert opts.include_raw_html is False


def test_resolve_options_override_beats_preset():
    from dead_letter.backend.mcp_server import _resolve_options

    opts = _resolve_options("default", strip_signatures=False)
    assert opts.strip_signatures is False
    # Other preset values unchanged
    assert opts.strip_tracking_pixels is True


def test_resolve_options_none_override_ignored():
    from dead_letter.backend.mcp_server import _resolve_options

    opts = _resolve_options("default", strip_signatures=None)
    assert opts.strip_signatures is True  # Preset value preserved
