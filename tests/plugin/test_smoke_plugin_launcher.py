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


@pytest.mark.parametrize("version", ["3.12.13", "3.14.5", ""])
def test_active_server_environment_must_use_python_312(tmp_path, version):
    environment = tmp_path / "builds-v0" / ".tmp-fixture"
    environment.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text(f"implementation = CPython\nversion_info = {version}\n")
    if version == "3.12.13":
        assert smoke.running_python_version(tmp_path) == version
    else:
        with pytest.raises(smoke.SmokeFailure, match="expected 3.12"):
            smoke.running_python_version(tmp_path)


def test_missing_builds_directory_cannot_pass_cleanup_check(tmp_path):
    with pytest.raises(smoke.SmokeFailure, match="no builds-v0 directory"):
        smoke.temporary_environments(tmp_path)
    (tmp_path / "builds-v0").mkdir()
    assert smoke.temporary_environments(tmp_path) == []


@pytest.mark.parametrize("count", [0, 2])
def test_active_server_environment_must_be_unambiguous(tmp_path, count):
    builds = tmp_path / "builds-v0"
    builds.mkdir()
    for index in range(count):
        environment = builds / f".tmp-{index}"
        environment.mkdir()
        (environment / "pyvenv.cfg").write_text("version_info = 3.12.13\n")
    with pytest.raises(smoke.SmokeFailure, match="expected one active uv environment"):
        smoke.running_python_version(tmp_path)
