"""Validate container release inputs and generate distribution metadata.

Uses only the standard library. No releases, registry writes, or catalog
submissions are performed here. `prepare` reads public image manifests;
`registry` and `catalog` only write local files.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/bigcactuslabs/dead-letter"
SERVER_NAME = "io.github.BigCactusLabs/dead-letter"
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def validate_digest(value: str) -> str:
    if not DIGEST_PATTERN.fullmatch(value) or value == "sha256:" + "0" * 64:
        raise ValueError("expected a non-placeholder sha256 image digest")
    return value


def validate_version(value: str) -> str:
    # Keep the exact Python release version as the OCI tag, without lossy
    # normalization of '+' local versions or other unsupported characters.
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9_.-]*", value):
        raise ValueError("package version is not a supported OCI release tag")
    if len(value) > 128:
        raise ValueError("OCI release tag exceeds 128 characters")
    return value


def release_version(root: Path, ref: str, *, release: bool) -> str:
    with (root / "pyproject.toml").open("rb") as source:
        version = validate_version(tomllib.load(source)["project"]["version"])
    if release and ref != f"refs/tags/v{version}":
        raise ValueError(f"release ref must be refs/tags/v{version}, got {ref!r}")
    return version


def resolve_image(image: str) -> str:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", image],
        check=True, capture_output=True, text=True, timeout=90,
    )
    # The first top-level Digest is the index digest, not an architecture's
    # child manifest. Keep the tag in the reference for human readability.
    match = re.search(r"^Digest:\s+(sha256:[0-9a-f]{64})\s*$", result.stdout, re.M)
    if match is None:
        raise ValueError(f"could not resolve the index digest for {image}")
    return f"{image.split('@', 1)[0]}@{validate_digest(match[1])}"


def prepare(root: Path, ref: str, *, release: bool) -> dict[str, str]:
    version = release_version(root, ref, release=release)
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, timeout=10,
    ).strip()
    if not COMMIT_PATTERN.fullmatch(revision):
        raise ValueError("expected a full source commit SHA")
    epoch = subprocess.check_output(
        ["git", "show", "-s", "--format=%ct", "HEAD"],
        cwd=root, text=True, timeout=10,
    ).strip()
    if not epoch.isdecimal():
        raise ValueError("invalid source commit timestamp")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    defaults = dict(re.findall(r"^ARG (PYTHON_IMAGE|UV_IMAGE)=(\S+)$", dockerfile, re.M))
    return {
        "version": version,
        "revision": revision,
        "source_date_epoch": epoch,
        "python_image": resolve_image(defaults["PYTHON_IMAGE"]),
        "uv_image": resolve_image(defaults["UV_IMAGE"]),
    }


def oci_package(digest: str) -> dict:
    def flag(value: str) -> dict:
        return {"type": "positional", "value": value}

    def named(name: str, value: str) -> dict:
        return {"type": "named", "name": name, "value": value}

    mounts = []
    # Publish the bind-mount form the smoke test exercises. --volume silently
    # creates a missing host source directory; --mount fails, which is what the
    # "existing absolute host directory" contract below promises.
    for variable, target, mode, suffix in (
        ("input_directory", "/input", "read-only", ",readonly"),
        ("output_directory", "/output", "writable", ""),
    ):
        mount = named("--mount", f"type=bind,source={{{variable}}},target={target}{suffix}")
        mount["variables"] = {
            variable: {
                "description": f"Existing absolute host directory bind-mounted at {target} ({mode})",
                "isRequired": True,
            }
        }
        mounts.append(mount)
    user = named("--user", "{container_user}")
    user["variables"] = {
        "container_user": {
            "description": "Unprivileged numeric UID:GID; on Linux use your host IDs for private bind mounts",
            "default": "10001:10001",
            "isRequired": True,
        }
    }
    return {
        "registryType": "oci",
        "identifier": f"{IMAGE}@{validate_digest(digest)}",
        "runtimeHint": "docker",
        "transport": {"type": "stdio"},
        "runtimeArguments": [
            flag("--rm"), flag("-i"),
            named("--network", "none"), flag("--read-only"),
            named("--cap-drop", "ALL"),
            named("--security-opt", "no-new-privileges"),
            named("--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"),
            user, *mounts,
        ],
    }


def with_oci(server: dict, version: str, digest: str) -> dict:
    validate_version(version)
    if server.get("name") != SERVER_NAME or server.get("version") != version:
        raise ValueError("server identity/version does not match the release")
    packages = server.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("server.json must retain its existing packages")
    result = copy.deepcopy(server)
    result["packages"] = [p for p in result["packages"] if p.get("registryType") != "oci"]
    result["packages"].append(oci_package(digest))
    return result


def write_json(path: Path, value: dict) -> None:
    """Replace atomically so a failed write cannot leave partial metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def render_catalog(root: Path, commit: str, output: Path) -> None:
    if not COMMIT_PATTERN.fullmatch(commit) or commit == "0" * 40:
        raise ValueError("catalog source requires a full non-placeholder commit SHA")
    template_dir = root / "docker" / "mcp-registry"
    template = (template_dir / "server.yaml.in").read_text(encoding="utf-8")
    if template.count("__SOURCE_COMMIT__") != 1:
        raise ValueError("catalog template must contain exactly one source commit token")
    output.mkdir(parents=True, exist_ok=True)
    (output / "server.yaml").write_text(
        template.replace("__SOURCE_COMMIT__", commit), encoding="utf-8",
    )
    (output / "readme.md").write_text(
        (template_dir / "readme.md").read_text(encoding="utf-8"), encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--ref", required=True)
    prep.add_argument("--release", action="store_true")
    registry = commands.add_parser("registry")
    registry.add_argument("--server-json", type=Path, default=ROOT / "server.json")
    registry.add_argument("--version", required=True)
    registry.add_argument("--digest", required=True)
    catalog = commands.add_parser("catalog")
    catalog.add_argument("--commit", required=True)
    catalog.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            for key, value in prepare(ROOT, args.ref, release=args.release).items():
                print(f"{key}={value}")
        elif args.command == "registry":
            value = json.loads(args.server_json.read_text(encoding="utf-8"))
            write_json(args.server_json, with_oci(value, args.version, args.digest))
        else:
            render_catalog(ROOT, args.commit, args.output)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"container metadata: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
