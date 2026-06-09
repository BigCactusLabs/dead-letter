"""Content tests for the dead-letter auto-trigger skill."""

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugin"
    / "skills"
    / "dead-letter-context"
    / "SKILL.md"
)


def _read_skill() -> tuple[dict, str]:
    """Return (frontmatter dict, body text)."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must begin with YAML frontmatter"
    end = text.find("\n---\n", 4)
    assert end != -1, "SKILL.md frontmatter must end with `---`"
    fm_text = text[4:end]
    body = text[end + 5 :]
    # Minimal YAML parse — only key: value pairs on single lines
    fm: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, body


def test_skill_has_required_frontmatter():
    fm, _ = _read_skill()
    assert fm["name"] == "dead-letter-context"
    assert fm["description"], "description is required for auto-trigger"
    # Trigger phrases that should appear in the description
    desc = fm["description"].lower()
    for trigger in [".eml", "email"]:
        assert trigger in desc, f"description should mention {trigger!r}"


@pytest.mark.parametrize(
    "command",
    ["/dead-letter:convert", "/dead-letter:summarize", "/dead-letter:triage", "/dead-letter:cabinet"],
)
def test_skill_lists_all_four_commands(command):
    _, body = _read_skill()
    assert command in body, f"SKILL.md must reference {command}"


@pytest.mark.parametrize("preset", ["default", "clean", "verbose", "raw"])
def test_skill_documents_all_presets(preset):
    _, body = _read_skill()
    assert preset in body.lower(), f"SKILL.md must document the {preset!r} preset"


def test_skill_covers_runtime_detection():
    _, body = _read_skill()
    body_lower = body.lower()
    # Runtime detection signals
    assert "uploads" in body_lower
    assert "outputs" in body_lower
    # Cowork remediations
    assert "upload" in body_lower
    assert "directory access" in body_lower or "grant" in body_lower


def test_skill_notes_mbox_out_of_scope():
    _, body = _read_skill()
    assert ".mbox" in body or "mbox" in body.lower(), (
        "SKILL.md must clarify .mbox is out of scope to prevent Claude from "
        "trying to use dead-letter on mbox files."
    )


def test_skill_documents_direct_mcp_tool_mapping():
    """Because slash commands are user-only (disable-model-invocation: true),
    the skill must teach Claude how to handle natural-language requests by
    calling MCP tools directly for read-only operations.
    """
    _, body = _read_skill()
    # All four MCP tools should be named in the mapping
    assert "convert_eml" in body, "skill must map natural-language asks to convert_eml"
    assert "convert_eml_to_bundle" in body, "skill must mention convert_eml_to_bundle (side-effecting; redirect to slash command)"
    assert "convert_directory" in body, "skill must mention convert_directory (batch; redirect to slash command)"


def test_skill_redirects_side_effecting_asks_to_slash_commands():
    """For side-effecting natural-language asks (archive/bundle, triage/folder),
    the skill must redirect to the slash command rather than invoking MCP tools
    directly. This is the safety contract from the disable-model-invocation
    decision.
    """
    _, body = _read_skill()
    body_lower = body.lower()
    # Must explicitly redirect cabinet-style asks
    assert "/dead-letter:cabinet" in body, "skill must redirect bundle asks to /dead-letter:cabinet"
    # Must explicitly redirect triage-style asks
    assert "/dead-letter:triage" in body, "skill must redirect folder asks to /dead-letter:triage"
    # Must use "do not" language for at least one of the side-effecting cases
    assert "do not invoke" in body_lower or "not invoke" in body_lower or "don't invoke" in body_lower, (
        "skill must use explicit do-not-invoke language for side-effecting MCP tools"
    )


def test_skill_reflects_server_side_mcp_batch_safety():
    _, body = _read_skill()
    body_lower = body.lower()

    assert "server-side cap" in body_lower or "built-in cap" in body_lower
    assert "no built-in cap" not in body_lower


def test_skill_treats_converted_email_content_as_untrusted():
    _, body = _read_skill()
    body_lower = body.lower()

    assert "untrusted" in body_lower
    assert "data, not instructions" in body_lower
    assert "do not follow" in body_lower
    assert "tool" in body_lower
    assert "credential" in body_lower or "secret" in body_lower
