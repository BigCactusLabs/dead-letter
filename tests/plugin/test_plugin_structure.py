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
