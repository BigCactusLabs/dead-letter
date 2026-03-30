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


from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "core" / "fixtures"


def test_convert_eml_returns_markdown():
    from dead_letter.backend.mcp_server import convert_eml

    result = convert_eml(eml_path=str(FIXTURES / "plain_text.eml"))
    assert result.startswith("---")  # YAML front matter
    assert "subject:" in result.lower() or "sender:" in result.lower()


def test_convert_eml_writes_to_output_path(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_eml

    out = tmp_path / "output"
    out.mkdir()
    result = convert_eml(
        eml_path=str(FIXTURES / "plain_text.eml"),
        output_path=str(out),
    )
    assert result.startswith("---")
    md_files = list(out.glob("*.md"))
    assert len(md_files) == 1


def test_convert_eml_with_preset():
    from dead_letter.backend.mcp_server import convert_eml

    result = convert_eml(
        eml_path=str(FIXTURES / "html_only.eml"),
        preset="verbose",
    )
    assert result.startswith("---")
    assert "raw_html:" in result  # verbose preset includes raw HTML


def test_convert_eml_with_flag_override():
    from dead_letter.backend.mcp_server import convert_eml

    result = convert_eml(
        eml_path=str(FIXTURES / "plain_text.eml"),
        preset="default",
        include_all_headers=True,
    )
    assert result.startswith("---")


def test_convert_eml_file_not_found():
    import pytest
    from dead_letter.backend.mcp_server import convert_eml

    with pytest.raises(FileNotFoundError, match="not_real.eml"):
        convert_eml(eml_path="/tmp/not_real.eml")
