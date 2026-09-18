"""Structural tests for the dead-letter MCPB bundle source."""

import json
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MCPB_ROOT = REPO_ROOT / "mcpb"
EXPECTED_TOOLS = [
    "convert_eml",
    "convert_eml_to_bundle",
    "convert_directory",
    "get_diagnostics",
]

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_mcpb import VersionMismatch, check_versions  # noqa: E402


def load_manifest() -> dict:
    return json.loads((MCPB_ROOT / "manifest.json").read_text(encoding="utf-8"))


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def package_version() -> str:
    return load_toml(REPO_ROOT / "pyproject.toml")["project"]["version"]


def test_manifest_parses_and_declares_uv_server():
    manifest = load_manifest()

    assert manifest["manifest_version"] == "0.4"
    assert manifest["name"] == "dead-letter-mcp"
    assert manifest["author"]["name"] == "Big Cactus Labs"

    server = manifest["server"]
    assert server["type"] == "uv"
    entry_point = MCPB_ROOT / server["entry_point"]
    assert entry_point.is_file(), f"missing {entry_point}"

    args = server["mcp_config"]["args"]
    assert server["mcp_config"]["command"] == "uv"
    assert "${__dirname}" in args, (
        "mcp_config args must locate the bundle via ${__dirname}"
    )
    assert args[-1] == server["entry_point"]


def test_manifest_version_matches_package():
    assert load_manifest()["version"] == package_version()


def test_bundle_pins_the_exact_package_version():
    project = load_toml(MCPB_ROOT / "pyproject.toml")["project"]
    version = package_version()

    assert project["version"] == version
    assert project["dependencies"] == [f"dead-letter[mcp]=={version}"]


def test_python_requirements_agree_on_3_12():
    manifest = load_manifest()
    project = load_toml(MCPB_ROOT / "pyproject.toml")["project"]

    assert project["requires-python"].startswith(">=3.12")
    assert manifest["compatibility"]["runtimes"]["python"].startswith(">=3.12")
    assert (MCPB_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_manifest_tools_match_the_registered_server_tools():
    manifest_tools = [tool["name"] for tool in load_manifest()["tools"]]
    assert manifest_tools == EXPECTED_TOOLS
    assert all(tool["description"].strip() for tool in load_manifest()["tools"])

    from dead_letter.backend.mcp_server import mcp

    manager = getattr(mcp, "_tool_manager", None)
    registered = getattr(manager, "_tools", None)
    if registered is None:
        pytest.skip(
            "MCP server does not expose a synchronous tool registry to introspect"
        )
    assert sorted(registered) == sorted(EXPECTED_TOOLS)


def test_check_versions_accepts_the_committed_sources():
    check_versions(
        package_version(),
        load_manifest(),
        load_toml(MCPB_ROOT / "pyproject.toml"),
    )


def test_check_versions_rejects_a_mismatched_version():
    manifest = load_manifest()
    manifest["version"] = "9.9.9"

    with pytest.raises(VersionMismatch) as error:
        check_versions(
            package_version(),
            manifest,
            load_toml(MCPB_ROOT / "pyproject.toml"),
        )

    assert "9.9.9" in str(error.value)


def test_check_versions_rejects_a_loose_dependency_pin():
    bundle_project = load_toml(MCPB_ROOT / "pyproject.toml")
    bundle_project["project"]["dependencies"] = ["dead-letter[mcp]"]

    with pytest.raises(VersionMismatch) as error:
        check_versions(package_version(), load_manifest(), bundle_project)

    assert "must pin" in str(error.value)
