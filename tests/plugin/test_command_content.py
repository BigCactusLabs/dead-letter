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


def test_triage_command_uses_convert_directory():
    fm, body = _read_command("triage")
    assert fm["description"]
    assert "convert_directory" in body


def test_triage_command_enforces_recursive_count_cap():
    """Cap must be enforced via recursive counting (matching _iter_source_eml_files),
    not shallow ls."""
    _, body = _read_command("triage")
    body_lower = body.lower()
    # Must mention the cap
    assert "50" in body
    # Must instruct recursive counting (find -type -iname OR recursive Glob)
    has_find = "find" in body_lower and "-iname" in body_lower
    has_recursive_glob = "**/*.eml" in body or "**/*.EML" in body
    assert has_find or has_recursive_glob, (
        "triage.md must instruct a recursive count of .eml files (find -iname or **/*.eml)"
    )
    # Must NOT suggest cabinet as the bulk path
    assert "/dead-letter:cabinet" not in body or "cabinet is not" in body_lower or "cabinet is single" in body_lower, (
        "triage.md must not direct users to cabinet for bulk — cabinet is single-email only."
    )


def test_triage_command_passes_output_directory():
    """convert_directory without output_directory writes beside source files
    (src/dead_letter/backend/mcp_server.py:216, src/dead_letter/core/_pipeline.py:86).
    That fails in read-only Cowork uploads/ and pollutes user folders elsewhere.
    The command MUST pass an explicit output_directory.
    """
    _, body = _read_command("triage")
    assert "output_directory" in body, (
        "triage.md must pass an explicit `output_directory` to convert_directory; "
        "otherwise core writes converted files beside the source .eml files."
    )
    body_lower = body.lower()
    # Must define a runtime-specific destination
    assert "outputs/triage" in body, (
        "triage.md must specify the Cowork output destination (outputs/triage/<run-id>)"
    )
    assert "mktemp" in body_lower or "tempfile" in body_lower or "temp dir" in body_lower, (
        "triage.md must specify a temp-dir output destination for Claude Code (mktemp or similar)"
    )


def test_triage_command_reflects_server_side_output_requirement():
    _, body = _read_command("triage")
    body_lower = body.lower()

    assert "`output_directory` is required" in body_lower or "output_directory is required" in body_lower
    assert "server passes `out=none`" not in body_lower
    assert "writes beside the source" not in body_lower


def test_cabinet_command_uses_correct_mcp_tool():
    fm, body = _read_command("cabinet")
    assert fm["description"]
    assert "convert_eml_to_bundle" in body


def test_cabinet_command_documents_source_stem_naming():
    _, body = _read_command("cabinet")
    body_lower = body.lower()
    assert "source-stem" in body_lower or "source stem" in body_lower or "source.stem" in body_lower, (
        "cabinet.md must document that bundle directory is named after the source filename stem"
    )
    # Default bundle_root semantics
    assert "outputs" in body_lower  # Cowork default


@pytest.mark.parametrize("name", ["convert", "summarize", "triage", "cabinet"])
def test_all_commands_disable_model_invocation(name):
    """Commands must not be model-invocable.

    In current Claude plugin docs, commands act as skills and can be invoked
    by the model without the user typing the slash command. That's unsafe for
    side-effecting commands (cabinet writes files; triage runs batch
    conversions) and contradicts the plugin's design where the
    `dead-letter-context` skill is the only auto-triggered surface.
    """
    fm, _ = _read_command(name)
    assert fm.get("disable-model-invocation") == "true", (
        f"{name}.md frontmatter must include `disable-model-invocation: true` "
        "to prevent the model from invoking it without an explicit slash command."
    )


@pytest.mark.parametrize("name", ["convert", "summarize", "triage", "cabinet"])
def test_all_commands_have_argument_hint(name):
    """Each command should declare its argument shape so the UI can render hints."""
    fm, _ = _read_command(name)
    assert fm.get("argument-hint"), (
        f"{name}.md frontmatter must include an `argument-hint` field describing "
        "the expected slash-command arguments."
    )
