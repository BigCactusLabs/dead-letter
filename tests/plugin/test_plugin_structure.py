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
    assert "--from" in args, "args must include `--from <package>`"
    assert args[-1] == "dead-letter-mcp", "entry-point must be dead-letter-mcp"


def test_mcp_json_pins_python_at_least_312():
    """The `.mcp.json` must pin --python so uvx auto-fetches a managed CPython
    when the user's default interpreter is older than dead-letter's
    requires-python (>=3.12). Without this, users on systems with Python 3.10
    or 3.11 see a confusing uv dependency-resolution error instead of the
    server starting.
    """
    mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())
    args = mcp["mcpServers"]["dead-letter"]["args"]
    assert "--python" in args, (
        "`.mcp.json` must include `--python <ver>` so uvx auto-fetches a "
        "compatible interpreter when system Python is too old."
    )
    python_value = args[args.index("--python") + 1]
    # Accept exact minor (3.12, 3.13...) or any value containing 3.12/3.13/etc.
    assert python_value.startswith("3.") and int(python_value.split(".")[1]) >= 12, (
        f"--python must request 3.12 or newer; got {python_value!r}"
    )


def test_mcp_json_pins_exact_dead_letter_version():
    """The `.mcp.json` must use an exact `==X.Y.Z` pin (not `>=`, not unpinned)
    so a future PyPI release of dead-letter cannot silently break installed plugins.

    This test does NOT assert equality with `plugin.json:version`. The spec
    defines those as independent semver: a plugin-only patch can bump the
    plugin asset version while keeping the same runtime pin.
    """
    import re

    mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())
    args = mcp["mcpServers"]["dead-letter"]["args"]
    from_arg = args[args.index("--from") + 1]
    match = re.fullmatch(r"dead-letter\[mcp\]==(\d+\.\d+\.\d+(?:[-+.][\w.]+)?)", from_arg)
    assert match, (
        f"`--from` argument must be of the form `dead-letter[mcp]==X.Y.Z`, "
        f"got {from_arg!r}. Other pin styles (>=, ~=, unpinned) are not allowed "
        "because they expose users to silent breakage on PyPI release."
    )
