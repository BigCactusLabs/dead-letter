"""Structural tests for the dead-letter MCPB bundle source."""

import json
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MCPB_ROOT = REPO_ROOT / "mcpb"
# MCP tools each published package exposes, keyed by the exact version the
# bundle pins. The manifest must list exactly the pinned runtime's tools.
SHIPPED_TOOLS = {
    "0.4.0": [
        "convert_eml",
        "convert_eml_to_bundle",
        "convert_directory",
        "get_diagnostics",
    ],
    # convert_mbox (#145) first ships in 0.4.5.
    "0.4.5": [
        "convert_eml",
        "convert_eml_to_bundle",
        "convert_directory",
        "convert_mbox",
        "get_diagnostics",
    ],
}
# Tools on main but not yet in any release. A new tool is listed here until
# the release that ships it moves it into SHIPPED_TOOLS and mcpb/manifest.json.
UNRELEASED_TOOLS: set[str] = set()

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


def pinned_package_version() -> str:
    (dependency,) = load_toml(MCPB_ROOT / "pyproject.toml")["project"]["dependencies"]
    return dependency.split("==", 1)[1]


def test_manifest_tools_match_the_pinned_runtime():
    """The default bundle installs the pinned PyPI package; list its tools only.

    Releasing a version not in SHIPPED_TOOLS fails here until its tool list is
    recorded, so a new tool reaches the manifest in the release that ships it.
    """
    pinned = pinned_package_version()
    assert pinned in SHIPPED_TOOLS, (
        f"record the MCP tools shipped in {pinned} in SHIPPED_TOOLS and mcpb/manifest.json"
    )
    manifest_tools = [tool["name"] for tool in load_manifest()["tools"]]
    assert manifest_tools == SHIPPED_TOOLS[pinned]
    assert all(tool["description"].strip() for tool in load_manifest()["tools"])


def test_checkout_tools_cover_the_manifest_with_only_unreleased_extras():
    from dead_letter.backend.mcp_server import mcp

    manager = getattr(mcp, "_tool_manager", None)
    registered = getattr(manager, "_tools", None)
    if registered is None:
        pytest.skip(
            "MCP server does not expose a synchronous tool registry to introspect"
        )
    declared = {tool["name"] for tool in load_manifest()["tools"]}
    assert declared <= set(registered)
    assert set(registered) - declared <= UNRELEASED_TOOLS


def test_bundle_smoke_compares_runtime_with_manifest():
    import smoke_mcpb

    published = set(SHIPPED_TOOLS[pinned_package_version()])
    # With no unreleased tools, a placeholder keeps the ahead/behind cases real.
    ahead = UNRELEASED_TOOLS or {"unreleased_placeholder_tool"}
    checkout = published | ahead
    assert smoke_mcpb.compare_tools(published, published, local_source=False) == set()
    # A published bundle whose runtime and manifest disagree fails either way.
    with pytest.raises(smoke_mcpb.SmokeFailure):
        smoke_mcpb.compare_tools(checkout, published, local_source=False)
    with pytest.raises(smoke_mcpb.SmokeFailure):
        smoke_mcpb.compare_tools(published, checkout, local_source=False)
    # A local-source bundle may run ahead of the manifest, never behind it.
    assert smoke_mcpb.compare_tools(checkout, published, local_source=True) == ahead
    with pytest.raises(smoke_mcpb.SmokeFailure):
        smoke_mcpb.compare_tools(published - {"get_diagnostics"}, published, local_source=True)


@pytest.mark.parametrize("local", [False, True])
def test_bundle_smoke_detects_local_source_bundles(tmp_path, local):
    import smoke_mcpb

    pyproject = (MCPB_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if local:
        pyproject += '\n[tool.uv.sources]\ndead-letter = { path = "/checkout", editable = true }\n'
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    assert smoke_mcpb.is_local_source(tmp_path) is local


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
