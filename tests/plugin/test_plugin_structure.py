"""Structural tests for the dead-letter Claude plugin."""

import json
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


def test_mcp_json_exists_and_uses_uvx():
    mcp_path = PLUGIN_ROOT / ".mcp.json"
    assert mcp_path.is_file(), f"missing {mcp_path}"
    data = json.loads(mcp_path.read_text(encoding="utf-8"))

    server = data["mcpServers"]["dead-letter"]
    assert server["command"] == "uvx"
    args = server["args"]
    assert args[0] == "--from"
    assert args[-1] == "dead-letter-mcp", "entry-point must be dead-letter-mcp"


def test_mcp_json_pins_exact_dead_letter_version():
    """The `.mcp.json` must use an exact `==X.Y.Z` pin (not `>=`, not unpinned)
    so a future PyPI release of dead-letter cannot silently break installed plugins.

    This test does NOT assert equality with `plugin.json:version`. The spec
    defines those as independent semver: a plugin-only patch can bump the
    plugin asset version while keeping the same runtime pin.
    """
    import re

    mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())
    from_arg = mcp["mcpServers"]["dead-letter"]["args"][1]
    match = re.fullmatch(r"dead-letter\[mcp\]==(\d+\.\d+\.\d+(?:[-+.][\w.]+)?)", from_arg)
    assert match, (
        f"`--from` argument must be of the form `dead-letter[mcp]==X.Y.Z`, "
        f"got {from_arg!r}. Other pin styles (>=, ~=, unpinned) are not allowed "
        "because they expose users to silent breakage on PyPI release."
    )
