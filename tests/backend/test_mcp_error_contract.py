"""Regression coverage for SDK 2.1+ ToolError handling at the protocol boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from dead_letter.backend import mcp_server


def _text(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


@pytest.mark.parametrize(
    ("error_class", "builtin"),
    [
        (mcp_server._MCPFileNotFoundError, FileNotFoundError),
        (mcp_server._MCPValueError, ValueError),
        (mcp_server._MCPRuntimeError, RuntimeError),
    ],
)
def test_actionable_errors_preserve_direct_call_types(error_class, builtin):
    error = error_class("actionable message")
    assert isinstance(error, builtin)
    assert isinstance(error, ToolError)
    assert str(error) == "actionable message"


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ["convert_eml", "convert_eml_to_bundle", "get_diagnostics"])
async def test_missing_file_message_survives_protocol(tool: str, tmp_path: Path):
    source = tmp_path / "not present.eml"
    output = tmp_path / "cabinet"
    arguments = {"eml_path": str(source)}
    if tool == "convert_eml_to_bundle":
        arguments["bundle_root"] = str(output)

    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool(tool, arguments)

    assert result.is_error is True
    assert _text(result) == f"File not found: {source}"
    assert not output.exists()


@pytest.mark.anyio
async def test_missing_directory_message_survives_protocol(tmp_path: Path):
    source = tmp_path / "missing"
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("convert_directory", {"directory": str(source)})
    assert result.is_error is True
    assert _text(result) == f"Directory not found: {source}"


@pytest.mark.anyio
async def test_required_destination_message_survives_protocol(tmp_path: Path):
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("convert_directory", {"directory": str(tmp_path)})
    assert result.is_error is True
    assert _text(result) == "output_directory is required for MCP directory conversion"
    assert not list(tmp_path.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["move", "delete"])
async def test_copy_only_message_and_source_safety_survive_protocol(mode: str, tmp_path: Path):
    source = tmp_path / "source.eml"
    original = b"Subject: Keep me\n\nOriginal bytes\n"
    source.write_bytes(original)
    output = tmp_path / "cabinet"
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool(
            "convert_eml_to_bundle",
            {"eml_path": str(source), "bundle_root": str(output), "source_handling": mode},
        )
    assert result.is_error is True
    assert "only supports source_handling='copy'" in _text(result)
    assert source.read_bytes() == original
    assert not output.exists()


@pytest.mark.anyio
async def test_batch_cap_message_survives_protocol_without_writes(tmp_path: Path):
    source = tmp_path / "inbox"
    source.mkdir()
    for index in range(51):
        (source / f"{index}.eml").write_text("Subject: Test\n\nBody\n", encoding="utf-8")
    output = tmp_path / "output"
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool(
            "convert_directory", {"directory": str(source), "output_directory": str(output)}
        )
    assert result.is_error is True
    assert _text(result) == "MCP directory conversion supports at most 50 .eml files; found 51."
    assert not output.exists()
    assert len(list(source.iterdir())) == 51


@pytest.mark.anyio
async def test_conversion_failure_keeps_recovery_hints(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.eml"
    source.write_text("Subject: Test\n\nBody\n", encoding="utf-8")
    monkeypatch.setattr(
        mcp_server,
        "convert",
        lambda *args, **kwargs: SimpleNamespace(
            success=False,
            error="HTML conversion failed",
            plain_text_fallback_available=True,
            html_repair_available=True,
        ),
    )
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("convert_eml", {"eml_path": str(source)})
    assert result.is_error is True
    assert _text(result) == (
        "Conversion failed: HTML conversion failed "
        "Plain text fallback is available. HTML repair is available."
    )


@pytest.mark.anyio
@pytest.mark.parametrize("error_class", [RuntimeError, ValueError, FileNotFoundError])
async def test_unexpected_parser_exceptions_remain_masked(error_class, monkeypatch, tmp_path: Path):
    """Do not solve the SDK change by leaking every exception of a builtin type."""
    source = tmp_path / "source.eml"
    source.write_text("Subject: Test\n\nBody\n", encoding="utf-8")
    private_detail = "private-parser-detail-not-for-the-model"

    def crash(*args, **kwargs):
        raise error_class(private_detail)

    monkeypatch.setattr(mcp_server, "convert", crash)
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("convert_eml", {"eml_path": str(source)})
    assert result.is_error is True
    assert private_detail not in _text(result)
    assert "Error executing tool convert_eml" in _text(result)
