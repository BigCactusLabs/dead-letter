"""Content tests for the portable cross-host Agent Skill.

This skill lives at the repo root under `skills/` (not `plugin/skills/`) so no
host auto-loads it during development. It targets the agentskills.io spec and
must stay free of Claude-plugin-only assumptions — see
`tests/plugin/test_skill_content.py` for the Claude-specific skill.
"""

from pathlib import Path
import re

import pytest
import yaml

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "dead-letter"
SKILL_PATH = SKILL_DIR / "SKILL.md"

NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _read_skill() -> tuple[dict, str]:
    """Return (frontmatter dict, body text).

    Unlike the Claude skill, this frontmatter carries a nested `metadata` map,
    so it is parsed with pyyaml (a package dependency) rather than by hand.
    """
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must begin with YAML frontmatter"
    end = text.find("\n---\n", 4)
    assert end != -1, "SKILL.md frontmatter must end with `---`"
    fm = yaml.safe_load(text[4:end])
    assert isinstance(fm, dict), "SKILL.md frontmatter must parse to a mapping"
    return fm, text[end + 5 :]


def test_skill_name_matches_directory():
    fm, _ = _read_skill()
    assert fm["name"] == "dead-letter"
    assert fm["name"] == SKILL_DIR.name, (
        "`gh skill publish` requires the frontmatter name to match the skill "
        "directory name."
    )


def test_skill_name_is_a_valid_slug():
    fm, _ = _read_skill()
    name = fm["name"]
    assert NAME_PATTERN.match(name), f"name {name!r} must be a lowercase hyphen slug"
    assert len(name) <= 64, "name must be at most 64 characters"


def test_skill_description_is_present_and_bounded():
    fm, _ = _read_skill()
    description = fm["description"]
    assert description, "description is required for skill discovery"
    assert len(description) <= 1024, "description must be at most 1024 characters"
    desc = description.lower()
    for trigger in [".eml", "email"]:
        assert trigger in desc, f"description should mention {trigger!r}"


def test_skill_declares_a_license():
    fm, _ = _read_skill()
    assert fm.get("license"), "the portable skill must declare a license"


def test_skill_body_stays_short():
    _, body = _read_skill()
    assert len(body.splitlines()) < 500, (
        "SKILL.md body must stay under 500 lines so hosts can load it cheaply."
    )


def test_skill_documents_the_uvx_invocation():
    _, body = _read_skill()
    assert "uvx --python 3.12" in body, (
        "the CLI path must pin Python 3.12 via uvx, since dead-letter requires it."
    )


@pytest.mark.parametrize(
    "tool",
    ["convert_eml", "convert_directory", "convert_eml_to_bundle", "get_diagnostics"],
)
def test_skill_names_every_mcp_tool(tool):
    _, body = _read_skill()
    assert tool in body, f"SKILL.md must document the {tool!r} MCP tool"


@pytest.mark.parametrize("preset", ["default", "clean", "verbose", "raw"])
def test_skill_documents_all_presets(preset):
    _, body = _read_skill()
    assert f"`{preset}`" in body, f"SKILL.md must document the {preset!r} preset"


def test_skill_treats_email_content_as_untrusted():
    _, body = _read_skill()
    assert "untrusted" in body.lower(), (
        "SKILL.md must carry the untrusted-email-content rule."
    )


def test_skill_notes_mbox_is_unsupported():
    _, body = _read_skill()
    assert "mbox" in body.lower(), (
        "SKILL.md must state that .mbox containers are not supported so agents "
        "do not point dead-letter at them."
    )


@pytest.mark.parametrize("marker", ["/dead-letter:", "Cowork", "uploads/"])
def test_skill_is_free_of_claude_plugin_assumptions(marker):
    """The portable skill runs on Codex, Copilot, Cursor, and others.

    Slash commands, Cowork, and the Cowork sandbox `uploads/` directory only
    exist in the Claude plugin, so referencing them here would give every other
    host instructions it cannot follow.
    """
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert marker not in text, (
        f"SKILL.md must not reference {marker!r}; that is Claude-plugin-only. "
        "Claude-specific guidance belongs in plugin/skills/dead-letter-context/."
    )


def test_allowed_tools_is_absent_or_a_string():
    """`gh skill publish` rejects a list-valued `allowed-tools`."""
    fm, _ = _read_skill()
    if "allowed-tools" in fm:
        assert isinstance(fm["allowed-tools"], str), (
            "allowed-tools must be a comma-separated string, not a list."
        )
