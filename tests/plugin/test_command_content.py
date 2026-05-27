"""Content tests for the dead-letter slash commands."""

from pathlib import Path

import pytest

COMMANDS_ROOT = Path(__file__).resolve().parents[2] / "plugin" / "commands"


def _read_command(name: str) -> tuple[dict, str]:
    path = COMMANDS_ROOT / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name}.md must begin with YAML frontmatter"
    end = text.find("\n---\n", 4)
    assert end != -1, f"{name}.md frontmatter must end with `---`"
    fm_text = text[4:end]
    body = text[end + 5 :]
    fm: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, body


def test_convert_command_uses_correct_mcp_tool():
    fm, body = _read_command("convert")
    assert fm["description"], "command must have a description"
    assert "convert_eml" in body, "convert.md must reference the convert_eml MCP tool"
    assert "convert_eml_to_bundle" not in body, (
        "convert.md must NOT reference convert_eml_to_bundle (that's cabinet's job)"
    )


def test_convert_command_documents_default_preset():
    _, body = _read_command("convert")
    assert "default" in body.lower()


def test_summarize_command_uses_convert_eml_with_clean_preset():
    fm, body = _read_command("summarize")
    assert fm["description"]
    assert "convert_eml" in body, "summarize.md must reference convert_eml"
    assert "clean" in body.lower(), "summarize.md must default to the clean preset"


def test_summarize_command_specifies_output_shape():
    _, body = _read_command("summarize")
    body_lower = body.lower()
    # Must mention all three output sections
    assert "summary" in body_lower
    assert "action item" in body_lower
    assert "dates" in body_lower or "people" in body_lower
