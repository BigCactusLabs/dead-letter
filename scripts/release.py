"""Release metadata checks, dry-run preparation, and immutable asset uploads.

Use Python 3.12+. `check` and `prepare` are offline; preparation prints a patch
for review and `git apply`. `wait-pypi` reads the network; `upload-assets` can
write GitHub release assets. No command tags or publishes a Python package.
See docs/reference/publishing.md.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import re
import sys
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
FILES = (
    "pyproject.toml", "uv.lock", "src/dead_letter/__init__.py", "server.json",
    "mcpb/manifest.json", "mcpb/pyproject.toml", ".well-known/ard.json",
    "plugin/.claude-plugin/plugin.json", "plugin/.mcp.json",
)
SERVER = "io.github.BigCactusLabs/dead-letter"
ASSET_ROOT = "https://github.com/BigCactusLabs/dead-letter/releases/download"


def stable(value: str) -> str:
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError(f"expected stable X.Y.Z version, got {value!r}")
    return value


def newer(value: str, previous: str) -> None:
    if tuple(map(int, stable(value).split("."))) <= tuple(map(int, stable(previous).split("."))):
        raise ValueError(f"new version {value} must be greater than {previous}")


def read_sources(root: Path) -> dict[str, str]:
    return {name: (root / name).read_text(encoding="utf-8") for name in FILES}


def one(items: list, label: str):
    if len(items) != 1:
        raise ValueError(f"expected exactly one {label}, found {len(items)}")
    return items[0]


def launcher_pin(data: dict) -> str:
    args = data["mcpServers"]["dead-letter"]["args"]
    position = one([i for i, value in enumerate(args) if value == "--from"], "--from")
    if position + 1 >= len(args):
        raise ValueError("plugin launcher has no --from value")
    pin = args[position + 1]
    prefix = "dead-letter[mcp]=="
    if not isinstance(pin, str) or not pin.startswith(prefix):
        raise ValueError("plugin launcher must use an exact dead-letter[mcp]==X.Y.Z pin")
    return stable(pin[len(prefix):])


def check(sources: dict[str, str]) -> dict[str, str]:
    """Fail closed on drift; a deliberately older exact plugin pin is allowed."""
    project = tomllib.loads(sources["pyproject.toml"])["project"]
    version = stable(project["version"])
    if project["name"] != "dead-letter":
        raise ValueError("unexpected project name")
    observed: dict[str, str] = {}
    lock = tomllib.loads(sources["uv.lock"])
    local = one([p for p in lock["package"] if p["name"] == "dead-letter"], "dead-letter lock entry")
    if local.get("source") != {"editable": "."}:
        raise ValueError("uv.lock must contain the editable root project")
    observed["uv.lock"] = local["version"]
    assignments = [node.value for node in ast.parse(sources["src/dead_letter/__init__.py"]).body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)]
    observed["__version__"] = ast.literal_eval(one(assignments, "__version__ assignment"))
    server = json.loads(sources["server.json"])
    if server["name"] != SERVER:
        raise ValueError("unexpected MCP Registry identity")
    observed["server.json"] = server["version"]
    for kind in ("pypi", "mcpb"):
        package = one([p for p in server["packages"] if p["registryType"] == kind], kind)
        observed[f"server.json {kind}"] = package["version"]
        if kind == "pypi":
            runtime = one([a for a in package["runtimeArguments"] if a.get("name") == "--from"], "registry --from")
            if runtime["value"] != f"dead-letter[mcp]=={version}":
                raise ValueError("server.json PyPI runtime pin does not match package")
        else:
            expected = f"{ASSET_ROOT}/v{version}/dead-letter-mcp-{version}.mcpb"
            if package["identifier"] != expected:
                raise ValueError("server.json MCPB asset URL does not match package")
            if package["fileSha256"] != "0" * 64:
                raise ValueError("source server.json must use the MCPB build-time hash placeholder")
    if any(p["registryType"] == "oci" for p in server["packages"]):
        raise ValueError("OCI belongs in verified release metadata, not the source template")
    observed["mcpb/manifest.json"] = json.loads(sources["mcpb/manifest.json"])["version"]
    bundle = tomllib.loads(sources["mcpb/pyproject.toml"])["project"]
    observed["mcpb/pyproject.toml"] = bundle["version"]
    if bundle["dependencies"] != [f"dead-letter[mcp]=={version}"]:
        raise ValueError("MCPB dependency must exactly match package")
    entries = json.loads(sources[".well-known/ard.json"])["entries"]
    if len(entries) != 2:
        raise ValueError("expected MCP and portable-skill ARD entries; update release policy for new entries")
    for index, entry in enumerate(entries):
        observed[f"ARD entry {index}"] = entry["version"]
    errors = [f"{key}: {value!r} != {version}" for key, value in observed.items() if value != version]
    if errors:
        raise ValueError("release metadata drift:\n" + "\n".join(errors))
    plugin = stable(json.loads(sources["plugin/.claude-plugin/plugin.json"])["version"])
    pin = launcher_pin(json.loads(sources["plugin/.mcp.json"]))
    return {"package": version, "plugin": plugin, "plugin_pin": pin}


def check_tag(versions: dict[str, str], tag: str, *, plugin: bool = False) -> None:
    prefix, key = ("plugin-v", "plugin") if plugin else ("v", "package")
    if not tag.startswith(prefix) or stable(tag[len(prefix):]) != versions[key]:
        raise ValueError(f"tag {tag!r} does not match {key} version {versions[key]}")


def replace_once(text: str, pattern: str, replacement: str) -> str:
    result, count = re.subn(pattern, lambda _: replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"expected one editable field for {pattern!r}, found {count}")
    return result


def project_version(text: str, version: str) -> str:
    match = re.search(r"(?ms)^\[project\]\n.*?(?=^\[|\Z)", text)
    if match is None:
        raise ValueError("missing [project] section")
    section = replace_once(match[0], r'^version = "[^"\n]+"$', f'version = "{version}"')
    return text[:match.start()] + section + text[match.end():]


def encode(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def prepare(sources: dict[str, str], version: str, *, plugin_version: str | None = None,
            keep_plugin: bool = False) -> dict[str, str]:
    """Return a complete proposed change, without I/O or changelog fabrication."""
    current = check(sources)
    newer(version, current["package"])
    if keep_plugin and plugin_version is not None:
        raise ValueError("--keep-plugin and --plugin-version are mutually exclusive")
    result = dict(sources)
    result["pyproject.toml"] = project_version(sources["pyproject.toml"], version)
    result["mcpb/pyproject.toml"] = project_version(sources["mcpb/pyproject.toml"], version).replace(
        f'dead-letter[mcp]=={current["package"]}', f"dead-letter[mcp]=={version}")
    result["src/dead_letter/__init__.py"] = replace_once(
        sources["src/dead_letter/__init__.py"], r'''^__version__ = ["'][^"'\n]+["']$''', f'__version__ = "{version}"')
    blocks = list(re.finditer(r"(?ms)^\[\[package\]\]\n.*?(?=^\[\[package\]\]|\Z)", sources["uv.lock"]))
    block = one([b for b in blocks if tomllib.loads(b[0])["package"][0]["name"] == "dead-letter"], "editable lock block")
    updated = replace_once(block[0], r'^version = "[^"\n]+"$', f'version = "{version}"')
    result["uv.lock"] = sources["uv.lock"][:block.start()] + updated + sources["uv.lock"][block.end():]
    server = json.loads(sources["server.json"])
    server["version"] = version
    for package in server["packages"]:
        package["version"] = version
        if package["registryType"] == "pypi":
            for argument in package["runtimeArguments"]:
                if argument.get("name") == "--from":
                    argument["value"] = f"dead-letter[mcp]=={version}"
        elif package["registryType"] == "mcpb":
            package["identifier"] = f"{ASSET_ROOT}/v{version}/dead-letter-mcp-{version}.mcpb"
            package["fileSha256"] = "0" * 64
    result["server.json"] = encode(server)
    manifest = json.loads(sources["mcpb/manifest.json"])
    manifest["version"] = version
    result["mcpb/manifest.json"] = encode(manifest)
    ard = json.loads(sources[".well-known/ard.json"])
    for entry in ard["entries"]:
        entry["version"] = version
    result[".well-known/ard.json"] = encode(ard)
    if not keep_plugin:
        asset_version = plugin_version or version
        newer(asset_version, current["plugin"])
        plugin = json.loads(sources["plugin/.claude-plugin/plugin.json"])
        plugin["version"] = asset_version
        result["plugin/.claude-plugin/plugin.json"] = encode(plugin)
        launcher = json.loads(sources["plugin/.mcp.json"])
        args = launcher["mcpServers"]["dead-letter"]["args"]
        args[args.index("--from") + 1] = f"dead-letter[mcp]=={version}"
        result["plugin/.mcp.json"] = encode(launcher)
    check(result)
    return result


def patch(before: dict[str, str], after: dict[str, str]) -> str:
    return "".join("".join(difflib.unified_diff(before[name].splitlines(keepends=True),
                   after[name].splitlines(keepends=True), fromfile=f"a/{name}", tofile=f"b/{name}"))
                   for name in FILES if before[name] != after[name])


def fetch_json(url: str, accept: str = "application/json") -> dict:
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "dead-letter-release-check"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def pypi_ready(version: str) -> bool:
    stable(version)
    release = fetch_json(f"https://pypi.org/pypi/dead-letter/{version}/json")
    index = fetch_json("https://pypi.org/simple/dead-letter/", "application/vnd.pypi.simple.v1+json")
    return (release.get("info", {}).get("version") == version
            and any(not item.get("yanked", False) for item in release.get("urls", []))
            and version in index.get("versions", []))


def wait_pypi(version: str, attempts: int = 60, interval: float = 10) -> None:
    stable(version)
    if not 1 <= attempts <= 60 or not 0 <= interval <= 60:
        raise ValueError("attempts must be 1..60 and interval 0..60 seconds")
    for attempt in range(attempts):
        try:
            if pypi_ready(version):
                print(f"PyPI JSON API and simple index serve dead-letter {version}")
                return
        except (urllib.error.URLError, OSError, ValueError, TypeError, AttributeError):
            pass
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise ValueError(f"PyPI did not serve an unyanked dead-letter {version} on both indexes")


def upload_assets(tag: str, paths: list[Path]) -> None:
    """Upload missing assets, reuse identical bytes, never replace a release asset."""
    if not tag.startswith("v"):
        raise ValueError("assets require a package release tag")
    stable(tag[1:])
    if not paths or len({p.name for p in paths}) != len(paths):
        raise ValueError("provide distinct release asset filenames")
    for path in paths:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", path.name) or not path.is_file():
            raise ValueError(f"invalid asset: {path}")
    repo = "BigCactusLabs/dead-letter"
    response = subprocess.run(["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
                              check=True, capture_output=True, text=True, timeout=60)
    existing = {item["name"] for item in json.loads(response.stdout)["assets"]}
    missing = []
    with tempfile.TemporaryDirectory(prefix="dead-letter-assets-") as temporary:
        for path in paths:
            if path.name not in existing:
                missing.append(path)
                continue
            subprocess.run(["gh", "release", "download", tag, "--repo", repo,
                            "--pattern", path.name, "--dir", temporary], check=True, timeout=120)
            downloaded = Path(temporary) / path.name
            if hashlib.sha256(downloaded.read_bytes()).digest() != hashlib.sha256(path.read_bytes()).digest():
                raise ValueError(f"refusing to replace published asset {path.name}; recover the original or cut a new release")
    # Compare all existing assets before making any remote change. Concurrent
    # creation fails safely: gh upload does not receive --clobber.
    if missing:
        subprocess.run(["gh", "release", "upload", tag, "--repo", repo,
                        *[str(path.resolve()) for path in missing]], check=True, timeout=120)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("check", help="read-only source metadata and optional tag checks")
    tag = verify.add_mutually_exclusive_group()
    tag.add_argument("--tag")
    tag.add_argument("--plugin-tag")
    prep = commands.add_parser("prepare", help="print a proposed version-sync patch; write nothing")
    prep.add_argument("version")
    mode = prep.add_mutually_exclusive_group()
    mode.add_argument("--plugin-version")
    mode.add_argument("--keep-plugin", action="store_true")
    wait = commands.add_parser("wait-pypi", help="bounded, read-only live availability check")
    wait.add_argument("version")
    assets = commands.add_parser("upload-assets", help="publish missing assets; reject replacement with different bytes")
    assets.add_argument("tag")
    assets.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "upload-assets":
            upload_assets(args.tag, args.paths)
            return 0
        if args.command == "wait-pypi":
            wait_pypi(args.version)
            return 0
        sources = read_sources(args.root)
        versions = check(sources)
        if args.command == "prepare":
            proposed = prepare(sources, args.version, plugin_version=args.plugin_version, keep_plugin=args.keep_plugin)
            print(patch(sources, proposed), end="")
            print("Review/apply this patch, finalize CHANGELOG.md, then run uv lock --check and release.py check --tag.", file=sys.stderr)
            return 0
        if args.tag or args.plugin_tag:
            check_tag(versions, args.tag or args.plugin_tag, plugin=bool(args.plugin_tag))
        if args.tag:
            changelog = (args.root / "CHANGELOG.md").read_text(encoding="utf-8")
            if not re.search(rf"^## \[{re.escape(versions['package'])}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE):
                raise ValueError("package release needs a dated CHANGELOG.md entry")
        print(json.dumps(versions, sort_keys=True))
        if versions["plugin_pin"] != versions["package"]:
            print("WARNING: plugin intentionally pins a different package; record adoption or deferral in release checklist.", file=sys.stderr)
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, SyntaxError, subprocess.SubprocessError) as error:
        print(f"release check: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
