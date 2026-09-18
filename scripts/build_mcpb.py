"""Build the dead-letter MCPB bundle from `mcpb/`.

Stages the committed bundle source into `build/mcpb/`, locks its dependencies so a
`uv.lock` ships with the bundle, then validates and packs it with the `mcpb` CLI.

Usage: uv run python scripts/build_mcpb.py [--output-dir dist] [--local-source PATH]
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCPB_SOURCE = REPO_ROOT / "mcpb"
STAGE_DIR = REPO_ROOT / "build" / "mcpb"
ICON_SOURCE = REPO_ROOT / "docs" / "brand" / "production" / "favicon-128x128.png"
MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"


class VersionMismatch(Exception):
    """Raised when the bundle sources disagree with the package version."""


def read_package_version(pyproject_path: Path) -> str:
    """Return the `dead-letter` version declared in the root pyproject."""
    with pyproject_path.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def check_versions(package_version: str, manifest: dict, bundle_project: dict) -> None:
    """Assert the manifest, bundle project, and dependency pin match the package.

    Raises VersionMismatch with every problem found, so one run reports them all.
    """
    problems: list[str] = []

    manifest_version = manifest.get("version")
    if manifest_version != package_version:
        problems.append(
            f"mcpb/manifest.json version {manifest_version!r} != package version {package_version!r}"
        )

    project_version = bundle_project.get("project", {}).get("version")
    if project_version != package_version:
        problems.append(
            f"mcpb/pyproject.toml version {project_version!r} != package version {package_version!r}"
        )

    expected_pin = f"dead-letter[mcp]=={package_version}"
    dependencies = bundle_project.get("project", {}).get("dependencies", [])
    if expected_pin not in dependencies:
        problems.append(
            f"mcpb/pyproject.toml dependencies {dependencies!r} must pin {expected_pin!r}"
        )

    if problems:
        raise VersionMismatch("; ".join(problems))


def run(command: list[str], *, cwd: Path | None = None) -> None:
    """Run a command, echoing it first and failing the build on a non-zero exit."""
    print(f"$ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def stage_bundle(local_source: Path | None) -> None:
    """Copy `mcpb/` into `build/mcpb/`, add the icon, and lock dependencies."""
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(MCPB_SOURCE, STAGE_DIR)

    if not ICON_SOURCE.is_file():
        raise SystemExit(f"error: missing icon source {ICON_SOURCE}")
    shutil.copyfile(ICON_SOURCE, STAGE_DIR / "icon.png")

    if local_source is not None:
        print(
            "NOTE: --local-source overrides the published dependency with a local "
            f"checkout ({local_source}). The resulting bundle is NOT releasable.",
            flush=True,
        )
        staged_pyproject = STAGE_DIR / "pyproject.toml"
        staged_pyproject.write_text(
            staged_pyproject.read_text(encoding="utf-8")
            + "\n[tool.uv.sources]\n"
            + f'dead-letter = {{ path = "{local_source.as_posix()}", editable = true }}\n',
            encoding="utf-8",
        )

    run(["uv", "lock", "--directory", str(STAGE_DIR)])


def write_checksum(archive: Path) -> str:
    """Write `<archive>.sha256` in sha256sum format and return the hex digest."""
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "dist",
        help="directory for the packed .mcpb archive (default: dist/)",
    )
    parser.add_argument(
        "--local-source",
        type=Path,
        default=None,
        help="build against an unreleased dead-letter checkout instead of PyPI",
    )
    args = parser.parse_args()

    package_version = read_package_version(REPO_ROOT / "pyproject.toml")
    manifest = json.loads((MCPB_SOURCE / "manifest.json").read_text(encoding="utf-8"))
    with (MCPB_SOURCE / "pyproject.toml").open("rb") as handle:
        bundle_project = tomllib.load(handle)

    try:
        check_versions(package_version, manifest, bundle_project)
    except VersionMismatch as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    local_source = args.local_source.resolve() if args.local_source else None
    stage_bundle(local_source)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"dead-letter-mcp-{package_version}.mcpb"

    run(["npx", "--yes", MCPB_CLI, "validate", str(STAGE_DIR / "manifest.json")])
    run(["npx", "--yes", MCPB_CLI, "pack", str(STAGE_DIR), str(archive)])

    digest = write_checksum(archive)
    print(f"built {archive}")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
