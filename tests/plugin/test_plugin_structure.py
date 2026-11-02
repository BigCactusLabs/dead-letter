"""Structural tests for the dead-letter Claude plugin."""

import json
import re
import tomllib
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugin"


def test_plugin_manifest_exists_and_parses():
    manifest_path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    assert manifest_path.is_file(), f"missing {manifest_path}"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert data["name"] == "dead-letter"
    assert isinstance(data["version"], str) and data["version"], "version must be a non-empty string"
    assert isinstance(data["description"], str) and data["description"]
    assert data["author"]["name"] == "Big Cactus Labs"
    assert data["homepage"].startswith("https://github.com/BigCactusLabs/dead-letter")


def test_manifest_icon_points_at_a_png_inside_the_plugin():
    data = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert data["icon"].startswith("./"), "plugin paths are relative to the plugin root"
    icon = (PLUGIN_ROOT / data["icon"]).resolve()
    assert icon.is_relative_to(PLUGIN_ROOT.resolve()) and icon.is_file(), f"missing icon {icon}"
    assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_mcp_json_runs_shipped_lock_in_isolation():
    mcp_path = PLUGIN_ROOT / ".mcp.json"
    assert mcp_path.is_file(), f"missing {mcp_path}"
    data = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["dead-letter"] == {
        "command": "uv",
        "args": ["run", "--frozen", "--isolated", "--project", "${CLAUDE_PLUGIN_ROOT}", "dead-letter-mcp"],
    }


def test_plugin_project_requires_python_at_least_312():
    project = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["requires-python"] == ">=3.12"
    assert (PLUGIN_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_plugin_project_pins_exact_dead_letter_version():
    """Plugin asset versions are independent; the runtime must stay exactly pinned."""
    project = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert len(project["dependencies"]) == 1
    dependency = project["dependencies"][0]
    match = re.fullmatch(r"dead-letter==(\d+\.\d+\.\d+)", dependency)
    assert match, f"plugin runtime must use dead-letter==X.Y.Z, got {dependency!r}"
    lock = tomllib.loads((PLUGIN_ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = [package for package in lock["package"] if package["name"] == "dead-letter"]
    assert len(packages) == 1 and packages[0]["version"] == match[1]
