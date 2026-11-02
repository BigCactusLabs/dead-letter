"""Offline launcher expansion contracts; the CI smoke tests the actual runtime."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("smoke_plugin_launcher", ROOT / "scripts/smoke_plugin_launcher.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_expands_plugin_root_without_splitting_arguments_or_modifying_manifest(tmp_path):
    plugin = tmp_path / "plugin with spaces"
    plugin.mkdir()
    original = json.dumps({"mcpServers": {"dead-letter": {
        "command": "${CLAUDE_PLUGIN_ROOT}/bin/uv",
        "args": ["run", "--frozen", "--isolated", "--project", "${CLAUDE_PLUGIN_ROOT}", "dead-letter-mcp"],
    }}})
    manifest = plugin / ".mcp.json"
    manifest.write_text(original)
    assert smoke.launcher_command(plugin) == [
        f"{plugin}/bin/uv", "run", "--frozen", "--isolated", "--project", str(plugin), "dead-letter-mcp",
    ]
    assert manifest.read_text() == original


@pytest.mark.parametrize("value", [None, "", 12])
def test_invalid_launcher_argument_fails_before_spawning(tmp_path, value):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"dead-letter": {"command": "uv", "args": [value]}}}))
    with pytest.raises(smoke.SmokeFailure, match="nonempty strings"):
        smoke.launcher_command(tmp_path)
