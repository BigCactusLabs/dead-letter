"""Tests for the MCP server."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dead_letter.core.types import ConvertOptions, ConvertResult


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
    with pytest.raises(ValueError, match="source_handling='copy'"):
        convert_eml_to_bundle(
            eml_path=str(source),
            bundle_root=str(cabinet),
            source_handling="delete",
        )
    assert source.exists()


def test_convert_eml_to_bundle_file_not_found():
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


def _write_minimal_eml(path: Path, body: str = "Body") -> None:
    path.write_text(
        "\n".join(
            [
                "From: Test <test@example.com>",
                "To: Example <example@example.com>",
                "Subject: Minimal",
                "Date: Thu, 05 Mar 2026 10:20:00 +0000",
                "MIME-Version: 1.0",
                "Content-Type: text/plain; charset=utf-8",
                "",
                body,
            ]
        ),
        encoding="utf-8",
    )


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


def test_convert_directory_requires_output_directory(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_directory

    eml_dir = _make_eml_dir(tmp_path, count=1)

    with pytest.raises(ValueError, match="output_directory"):
        convert_directory(directory=str(eml_dir))

    assert not list(eml_dir.glob("*.md"))


def test_convert_directory_dry_run(tmp_path: Path):
    from dead_letter.backend.mcp_server import convert_directory

    eml_dir = _make_eml_dir(tmp_path, count=1)
    out_dir = tmp_path / "output"

    result_str = convert_directory(
        directory=str(eml_dir),
        output_directory=str(out_dir),
        dry_run=True,
    )
    result = json.loads(result_str)
    assert result["total"] == 1


def test_convert_directory_not_found():
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


def test_convert_directory_rejects_more_than_fifty_files_before_writes(
    monkeypatch, tmp_path: Path
):
    from dead_letter.backend import mcp_server

    eml_dir = tmp_path / "emails"
    eml_dir.mkdir()
    for index in range(51):
        _write_minimal_eml(eml_dir / f"{index:02d}.eml")

    def _fail_convert_dir(*_args, **_kwargs):
        pytest.fail("convert_dir should not be called when the MCP cap is exceeded")

    monkeypatch.setattr(mcp_server, "convert_dir", _fail_convert_dir)

    with pytest.raises(ValueError, match="at most 50"):
        mcp_server.convert_directory(
            directory=str(eml_dir),
            output_directory=str(tmp_path / "output"),
        )

    assert not (tmp_path / "output").exists()


def test_convert_directory_counts_with_core_file_scan_before_cap(
    monkeypatch, tmp_path: Path
):
    from dead_letter.backend import mcp_server
    from dead_letter.core._pipeline import _iter_source_eml_files

    eml_dir = tmp_path / "emails"
    eml_dir.mkdir()
    for index in range(50):
        _write_minimal_eml(eml_dir / f"{index:02d}.eml")
    outside = tmp_path / "outside.eml"
    _write_minimal_eml(outside)
    (eml_dir / "outside-link.eml").symlink_to(outside)

    def _fake_convert_dir(directory, *, output, options):
        files = _iter_source_eml_files(Path(directory).resolve())
        return [
            ConvertResult(
                source=file_path,
                output=Path(output) / f"{file_path.stem}.md",
                subject="Minimal",
                sender="test@example.com",
                date=None,
                attachments=[],
                success=True,
                error=None,
                dry_run=options.dry_run,
            )
            for file_path in files
        ]

    monkeypatch.setattr(mcp_server, "convert_dir", _fake_convert_dir)

    result = json.loads(
        mcp_server.convert_directory(
            directory=str(eml_dir),
            output_directory=str(tmp_path / "output"),
        )
    )

    assert result["total"] == 50
    assert result["successes"] == 50


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
    from dead_letter.backend.mcp_server import get_diagnostics

    with pytest.raises(FileNotFoundError):
        get_diagnostics(eml_path="/tmp/not_real.eml")


# ---------------------------------------------------------------------------
# Smoke tests: tool registration and entry point
# ---------------------------------------------------------------------------


def test_mcp_extra_requires_supported_sdk_major():
    from importlib.metadata import requires

    from packaging.requirements import Requirement
    from packaging.version import Version

    requirements = [Requirement(value) for value in requires("dead-letter") or []]
    mcp_requirement = next(
        requirement for requirement in requirements if requirement.name == "mcp"
    )

    assert Version("1.29.0") not in mcp_requirement.specifier
    assert Version("2.0.0") in mcp_requirement.specifier
    assert Version("3.0.0") not in mcp_requirement.specifier


@pytest.mark.anyio
async def test_server_has_all_tools():
    from mcp import Client

    from dead_letter.backend.mcp_server import mcp

    async with Client(mcp) as client:
        result = await client.list_tools()

    tool_names = {tool.name for tool in result.tools}
    assert "convert_eml" in tool_names
    assert "convert_eml_to_bundle" in tool_names
    assert "convert_directory" in tool_names
    assert "get_diagnostics" in tool_names


def test_main_entry_point_is_callable():
    from dead_letter.backend.mcp_server import main

    assert callable(main)


# ---------------------------------------------------------------------------
# MCP round-trip integration test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mcp_client_convert_eml_round_trip():
    """Invoke convert_eml through the public MCP client protocol layer."""
    from mcp import Client

    from dead_letter.backend.mcp_server import mcp

    async with Client(mcp) as client:
        result = await client.call_tool(
            "convert_eml",
            {"eml_path": str(FIXTURES / "plain_text.eml")},
        )

    assert result.content, "Expected at least one content block"
    text = result.content[0].text
    assert text.startswith("---"), "Expected YAML front matter"


@pytest.mark.anyio
async def test_mcp_client_convert_bundle_rejects_delete(tmp_path: Path):
    """The MCP protocol path must enforce copy-only bundle conversion."""
    from mcp import Client

    from dead_letter.backend.mcp_server import mcp

    source = tmp_path / "input" / "plain_text.eml"
    source.parent.mkdir()
    shutil.copy2(FIXTURES / "plain_text.eml", source)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "convert_eml_to_bundle",
            {
                "eml_path": str(source),
                "bundle_root": str(tmp_path / "cabinet"),
                "source_handling": "delete",
            },
        )

    assert result.is_error is True
    assert "source_handling='copy'" in result.content[0].text
    assert source.exists()


# ---------------------------------------------------------------------------
# Thread mode / order — signature, _resolve_options, and per-tool propagation
# ---------------------------------------------------------------------------

import inspect

from dead_letter.backend import mcp_server
from dead_letter.core.types import ThreadMode, ThreadOrder


MCP_FLAG_TOOLS = (
    mcp_server.convert_eml,
    mcp_server.convert_eml_to_bundle,
    mcp_server.convert_directory,
    mcp_server.get_diagnostics,
)


@pytest.mark.parametrize("tool", MCP_FLAG_TOOLS)
def test_mcp_tool_exposes_thread_mode_and_thread_order(tool) -> None:
    fn = getattr(tool, "fn", tool)
    sig = inspect.signature(fn)
    assert "thread_mode" in sig.parameters
    assert "thread_order" in sig.parameters


def test_mcp_resolve_options_normalizes_thread_mode_string_to_enum() -> None:
    opts = mcp_server._resolve_options(thread_mode="structured", thread_order="latest-first")

    assert opts.thread_mode is ThreadMode.STRUCTURED
    assert opts.thread_order is ThreadOrder.LATEST_FIRST


def test_mcp_resolve_options_defaults_to_latest_oldest_first() -> None:
    opts = mcp_server._resolve_options()

    assert opts.thread_mode is ThreadMode.LATEST
    assert opts.thread_order is ThreadOrder.OLDEST_FIRST


def _make_eml(tmp_path: Path, name: str = "msg.eml") -> Path:
    src = (Path(__file__).resolve().parents[1] / "core/fixtures/reply_chain.eml").read_bytes()
    target = tmp_path / name
    target.write_bytes(src)
    return target


def test_convert_eml_threads_options_to_core(monkeypatch, tmp_path) -> None:
    captured: list[ConvertOptions] = []

    real_convert = mcp_server.convert

    def _spy(source, *, output, options):
        captured.append(options)
        return real_convert(source, output=output, options=options)

    monkeypatch.setattr(mcp_server, "convert", _spy)
    eml = _make_eml(tmp_path)

    mcp_server.convert_eml(str(eml), thread_mode="structured")

    assert captured and captured[0].thread_mode is ThreadMode.STRUCTURED


def test_convert_directory_threads_options_to_core(monkeypatch, tmp_path) -> None:
    captured: list[ConvertOptions] = []
    real = mcp_server.convert_dir

    def _spy(directory, *, output, options):
        captured.append(options)
        return real(directory, output=output, options=options)

    monkeypatch.setattr(mcp_server, "convert_dir", _spy)
    _make_eml(tmp_path)

    mcp_server.convert_directory(
        str(tmp_path),
        output_directory=str(tmp_path / "output"),
        thread_mode="structured",
    )

    assert captured and captured[0].thread_mode is ThreadMode.STRUCTURED


def test_convert_eml_to_bundle_threads_options_to_core(monkeypatch, tmp_path) -> None:
    captured: list[ConvertOptions] = []
    real = mcp_server.convert_to_bundle_with_diagnostics

    def _spy(source, *, bundle_root, options, source_handling="copy"):
        captured.append(options)
        return real(source, bundle_root=bundle_root, options=options, source_handling=source_handling)

    monkeypatch.setattr(mcp_server, "convert_to_bundle_with_diagnostics", _spy)
    eml = _make_eml(tmp_path)
    bundle_root = tmp_path / "bundle"

    mcp_server.convert_eml_to_bundle(str(eml), str(bundle_root), thread_mode="structured")

    assert captured and captured[0].thread_mode is ThreadMode.STRUCTURED


def test_get_diagnostics_threads_options_to_core(monkeypatch, tmp_path) -> None:
    captured: list[ConvertOptions] = []
    real = mcp_server.convert_to_bundle_with_diagnostics

    def _spy(source, *, bundle_root, options, source_handling="copy"):
        captured.append(options)
        return real(source, bundle_root=bundle_root, options=options, source_handling=source_handling)

    monkeypatch.setattr(mcp_server, "convert_to_bundle_with_diagnostics", _spy)
    eml = _make_eml(tmp_path)

    result_json = mcp_server.get_diagnostics(str(eml), thread_mode="structured")

    assert captured and captured[0].thread_mode is ThreadMode.STRUCTURED
    parsed = json.loads(result_json)
    assert "state" in parsed or "selected_body" in parsed
