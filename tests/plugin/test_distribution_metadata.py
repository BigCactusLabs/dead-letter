"""Offline regression checks for discovery and install metadata.

These cover local invariants and the prior description/Python-selector bugs;
they do not replace complete validation by the MCP publisher or real clients.
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _manifest():
    return json.loads(_read("server.json"))


def _pypi_package():
    return next(package for package in _manifest()["packages"] if package["registryType"] == "pypi")


def test_registry_description_obeys_current_schema_limit():
    manifest = _manifest()
    assert isinstance(manifest["description"], str)
    assert 1 <= len(manifest["description"]) <= 100
    assert manifest["description"].strip() == manifest["description"]


def test_registry_identity_versions_and_entrypoint_are_consistent():
    manifest = _manifest()
    project = tomllib.loads(_read("pyproject.toml"))["project"]
    package = _pypi_package()
    assert manifest["name"] == "io.github.BigCactusLabs/dead-letter"
    assert f'<!-- mcp-name: {manifest["name"]} -->' in _read("README.md")
    assert package["identifier"] == project["name"]
    assert manifest["version"] == package["version"] == project["version"]
    assert package["transport"]["type"] == "stdio"
    arguments = {argument["name"]: argument["value"] for argument in package["runtimeArguments"]}
    assert arguments["--python"] == "3.12"
    assert arguments["--from"] == f'{project["name"]}[mcp]=={project["version"]}'
    assert "dead-letter-mcp" in project["scripts"]
    assert any(argument.get("value") == "dead-letter-mcp" for argument in package["packageArguments"])


@pytest.mark.parametrize("path", ["README.md", "llms-install.md"])
def test_documented_uvx_launches_select_python(path: str):
    launches = []
    for block in re.findall(r"```(?:bash|sh)\s*\n(.*?)```", _read(path), flags=re.DOTALL):
        for line in block.splitlines():
            tokens = shlex.split(line, comments=True)
            if "uvx" not in tokens:
                continue
            arguments = tokens[tokens.index("uvx") + 1:]
            launches.append(arguments)
            assert "--python" in arguments, (path, line)
            assert arguments[arguments.index("--python") + 1] == "3.12", (path, line)
    assert launches, f"No tested uvx example found in {path}"


def test_reach_plan_routes_to_canonical_installation_instead_of_copying_commands():
    plan = _read("docs/project/reach.md")
    assert "../reference/distribution.md" in plan
    assert "../../llms-install.md" in plan
    launches = re.findall(r"```(?:bash|sh)\s*\n(.*?)```", plan, flags=re.DOTALL)
    assert not any("uvx" in shlex.split(line, comments=True) for block in launches for line in block.splitlines())


def test_agent_guide_json_uses_client_specific_wrappers():
    configs = [json.loads(block) for block in re.findall(r"```json\s*\n(.*?)```", _read("llms-install.md"), flags=re.DOTALL)]
    assert len(configs) == 2
    assert "mcpServers" in configs[0]
    assert "servers" in configs[1]
    desktop = configs[0]["mcpServers"]["dead-letter"]
    vscode = configs[1]["servers"]["dead-letter"]
    assert vscode["type"] == "stdio"
    for config in (desktop, vscode):
        assert config["command"] == "uvx"
        assert config["args"] == ["--python", "3.12", "--from", "dead-letter[mcp]", "dead-letter-mcp"]


def test_agent_guide_uses_an_explicit_placeholder_not_a_duplicated_current_pin():
    guide = _read("llms-install.md")
    assert "--from 'dead-letter[mcp]==X.Y.Z'" in guide
    assert "`X.Y.Z` is a placeholder" in guide
    assert "actually published package version" in guide
    assert "docs/reference/distribution.md" in guide
    assert not re.search(r"dead-letter\[mcp\]==\d+\.\d+\.\d+", guide)
    # The exact runnable source pin is still enforced independently above.
    # main may contain unreleased metadata; installing its version blindly
    # would turn a prose example back into another release sync point.
