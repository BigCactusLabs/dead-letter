"""Tests for the MCP server."""

from __future__ import annotations

import json
import shutil

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


def test_convert_eml_to_bundle_returns_json(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_eml_to_bundle

    source = tmp_path / "input" / "plain_text.eml"
    source.parent.mkdir()
    shutil.copy2(FIXTURES / "plain_text.eml", source)

    cabinet = tmp_path / "cabinet"
    result_str = convert_eml_to_bundle(
        eml_path=str(source),
        bundle_root=str(cabinet),
    )
    result = json.loads(result_str)
    assert "bundle_path" in result
    assert "markdown_path" in result
    assert "attachment_paths" in result
    assert Path(result["markdown_path"]).exists()


def test_convert_eml_to_bundle_with_attachments(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_eml_to_bundle

    source = tmp_path / "input" / "with_attachment.eml"
    source.parent.mkdir()
    shutil.copy2(FIXTURES / "with_attachment.eml", source)

    cabinet = tmp_path / "cabinet"
    result_str = convert_eml_to_bundle(
        eml_path=str(source),
        bundle_root=str(cabinet),
    )
    result = json.loads(result_str)
    assert len(result["attachment_paths"]) > 0


def test_convert_eml_to_bundle_default_source_handling_is_copy(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_eml_to_bundle

    source = tmp_path / "input" / "plain_text.eml"
    source.parent.mkdir()
    shutil.copy2(FIXTURES / "plain_text.eml", source)

    cabinet = tmp_path / "cabinet"
    convert_eml_to_bundle(
        eml_path=str(source),
        bundle_root=str(cabinet),
    )
    # MCP wrapper defaults to copy; source must remain in place.
    assert source.exists()


def test_convert_eml_to_bundle_source_handling_delete(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_eml_to_bundle

    source = tmp_path / "input" / "plain_text.eml"
    source.parent.mkdir()
    shutil.copy2(FIXTURES / "plain_text.eml", source)

    cabinet = tmp_path / "cabinet"
    convert_eml_to_bundle(
        eml_path=str(source),
        bundle_root=str(cabinet),
        source_handling="delete",
    )
    assert source.exists() is False


def test_convert_eml_to_bundle_file_not_found():
    import pytest
    from dead_letter.backend.mcp_server import convert_eml_to_bundle

    with pytest.raises(FileNotFoundError):
        convert_eml_to_bundle(
            eml_path="/tmp/not_real.eml",
            bundle_root="/tmp/cabinet",
        )


def _make_eml_dir(tmp_path: Path, count: int = 2) -> Path:
    """Copy fixture .eml files into a temp directory for batch testing."""
    eml_dir = tmp_path / "emails"
    eml_dir.mkdir()
    fixtures = ["plain_text.eml", "html_only.eml", "multipart_alternative.eml"]
    for name in fixtures[:count]:
        shutil.copy2(FIXTURES / name, eml_dir / name)
    return eml_dir


def test_convert_directory_returns_summary(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_directory

    eml_dir = _make_eml_dir(tmp_path, count=2)
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    result_str = convert_directory(
        directory=str(eml_dir),
        output_directory=str(out_dir),
    )
    result = json.loads(result_str)
    assert result["total"] == 2
    assert result["successes"] == 2
    assert result["failures"] == 0
    assert len(result["output_paths"]) == 2
    assert result["errors"] == []


def test_convert_directory_dry_run(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_directory

    eml_dir = _make_eml_dir(tmp_path, count=1)

    result_str = convert_directory(
        directory=str(eml_dir),
        dry_run=True,
    )
    result = json.loads(result_str)
    assert result["total"] == 1


def test_convert_directory_not_found():
    import pytest
    from dead_letter.backend.mcp_server import convert_directory

    with pytest.raises(FileNotFoundError, match="not_a_real_dir"):
        convert_directory(directory="/tmp/not_a_real_dir")


def test_convert_directory_with_preset(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_directory

    eml_dir = _make_eml_dir(tmp_path, count=1)
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    result_str = convert_directory(
        directory=str(eml_dir),
        output_directory=str(out_dir),
        preset="clean",
    )
    result = json.loads(result_str)
    assert result["successes"] == 1


def test_get_diagnostics_returns_json():
    from dead_letter.backend.mcp_server import get_diagnostics

    result_str = get_diagnostics(eml_path=str(FIXTURES / "plain_text.eml"))
    result = json.loads(result_str)
    assert "state" in result
    assert result["state"] in ("normal", "degraded", "review_recommended")
    assert result["selected_body"] in ("html", "plain")
    assert result["segmentation_path"] in ("html", "plain_fallback")
    assert result["confidence"] in ("high", "medium", "low")
    assert isinstance(result["warnings"], list)


def test_get_diagnostics_html_email():
    from dead_letter.backend.mcp_server import get_diagnostics

    result_str = get_diagnostics(eml_path=str(FIXTURES / "html_only.eml"))
    result = json.loads(result_str)
    assert "selected_body" in result
    assert "warnings" in result
    assert isinstance(result["warnings"], list)


def test_get_diagnostics_default_preset_reports_stripped_images(tmp_path: Path):
    from dead_letter.backend.mcp_server import get_diagnostics

    eml_path = tmp_path / "pixel.eml"
    eml_path.write_text(
        "From: test@example.com\n"
        "Subject: Pixel Test\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/html; charset=utf-8\n"
        "\n"
        '<html><body><p>Hello</p><img src="https://t.example.com/open.gif" width="1" height="1" /></body></html>',
        encoding="utf-8",
    )

    result = json.loads(get_diagnostics(eml_path=str(eml_path)))
    assert result["stripped_images"][0]["category"] == "tracking_pixel"


def test_get_diagnostics_file_not_found():
    import pytest
    from dead_letter.backend.mcp_server import get_diagnostics

    with pytest.raises(FileNotFoundError):
        get_diagnostics(eml_path="/tmp/not_real.eml")
