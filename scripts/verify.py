"""One sequential verification entry point; JSON on stdout, check output on stderr.

Run `uv sync --extra dev --locked` before source checks. No publishing commands
are used. Existing --dist-dir/--checksums artifacts are tested without rebuilding.
Exit 0: all passed; 1: a check failed; 2: checks could not run (no failures).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("core", "cli", "mcp", "ui", "benchmark", "sdist")
SUITES = ("core", "backend", "plugin", "frontend", "metadata")


def run_check(name: str, command: list[str], *, cwd: Path = ROOT, timeout: int = 1200) -> dict:
    result = {"name": name, "command": command}
    if not shutil.which(command[0]):
        return {**result, "status": "could-not-run", "reason": f"missing executable: {command[0]}"}
    print(f"\n== {name} ==", file=sys.stderr, flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=cwd, stdout=sys.stderr, stderr=sys.stderr, timeout=timeout, check=False)
    except OSError as exc:
        result.update(status="could-not-run", reason=str(exc))
    except subprocess.TimeoutExpired:
        result.update(status="failed", reason=f"exceeded {timeout}s timeout")
    else:
        result.update(status="passed" if completed.returncode == 0 else "failed", returncode=completed.returncode)
    result["seconds"] = round(time.monotonic() - started, 3)
    return result


def source_commands(mode: str, suites: list[str] | None = None) -> list[tuple[str, list[str]]]:
    pytest = ["uv", "run", "--locked", "--no-sync", "pytest", "-q"]
    groups = {
        "core": [("core", pytest + ["tests/core"])],
        "backend": [("backend", pytest + ["tests/backend"])],
        "plugin": [
            ("plugin-tests", pytest + ["tests/plugin"]),
            ("plugin-schema", ["npx", "--yes", "@anthropic-ai/claude-code@2.1.145", "plugin", "validate", "plugin/"]),
            ("agent-skills", ["gh", "skill", "publish", "--dry-run"]),
        ],
        "frontend": [
            ("frontend-tests", ["node", "--test", *[str(p.relative_to(ROOT)) for p in sorted((ROOT / "tests/frontend").glob("*.test.js"))]]),
            ("frontend-syntax", ["node", "--check", "src/dead_letter/frontend/static/app.js"]),
        ],
        "metadata": [("release-metadata", [sys.executable, "scripts/release.py", "check"])],
    }
    if suites is not None:
        return [item for suite in dict.fromkeys(suites) for item in groups[suite]]
    if mode == "quick":
        return groups["core"] + groups["backend"] + [groups["frontend"][1]]
    return [item for suite in SUITES for item in groups[suite]]


def source_checks(mode: str, suites: list[str] | None) -> list[dict]:
    results = []
    for name, command in source_commands(mode, suites):
        if name == "frontend-tests" and len(command) == 2:
            # `node --test` with no paths can succeed without testing this suite.
            results.append({"name": name, "status": "failed", "reason": "no frontend test files found"})
            continue
        if name == "agent-skills" and shutil.which("gh"):
            try:
                probe = subprocess.run(["gh", "skill", "publish", "--help"], cwd=ROOT, capture_output=True, timeout=30, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                results.append({"name": name, "status": "could-not-run", "reason": f"gh skill availability probe failed: {type(exc).__name__}"})
                continue
            if probe.returncode:
                results.append({"name": name, "status": "could-not-run", "reason": "gh with skill support (2.90+) is required"})
                continue
        results.append(run_check(name, command))
    return results


def packaging_checks(directory: Path, manifest: Path, *, build: bool, version: str) -> list[dict]:
    results = []
    if build:
        results.append(run_check("build", ["uv", "build", "--out-dir", str(directory)]))
    if not results or results[-1]["status"] == "passed":
        results.append(run_check("package-metadata", [sys.executable, str(ROOT / "scripts/package_artifacts.py"),
                                                     "record" if build else "verify", "--dist-dir", str(directory),
                                                     "--checksums", str(manifest), "--version", version]))
    if results[-1]["status"] != "passed":
        # An invalid/unbuilt artifact is not safe to install; independent source
        # suites still run to completion in their own mode/CI jobs.
        return results + [{"name": name, "status": "could-not-run", "reason": "build/metadata prerequisite did not pass"}
                          for name in ("readme-render", *[f"installed-{p}" for p in PROFILES])]
    files = sorted(str(p) for p in directory.iterdir() if p.suffix == ".whl" or p.name.endswith(".tar.gz"))
    results.append(run_check("readme-render", ["uv", "tool", "run", "--from", "twine==7.0.0", "twine", "check", "--strict", *files]))
    for profile in PROFILES:
        if not shutil.which("uv"):
            results.append({"name": f"installed-{profile}", "status": "could-not-run", "reason": "missing executable: uv"})
            continue
        results.append(run_check(f"installed-{profile}", [sys.executable, str(ROOT / "scripts/smoke_package.py"),
                                                         "--dist-dir", str(directory), "--checksums", str(manifest),
                                                         "--version", version, "--profile", profile]))
    return results


def summarize(mode: str, results: list[dict]) -> int:
    counts = {status: sum(item["status"] == status for item in results) for status in ("passed", "failed", "could-not-run")}
    code = 1 if counts["failed"] else 2 if counts["could-not-run"] else 0
    print(json.dumps({"schema_version": 1, "mode": mode, "results": results, "counts": counts, "exit_code": code}, indent=2))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("quick", "full", "packaging"))
    parser.add_argument("--suite", action="append", choices=SUITES, help="select an independent source suite (repeatable)")
    parser.add_argument("--dist-dir", type=Path, help="test these existing distributions; never rebuild them")
    parser.add_argument("--checksums", type=Path, help="required recorded build checksums with --dist-dir")
    args = parser.parse_args(argv)
    if bool(args.dist_dir) != bool(args.checksums):
        parser.error("--dist-dir and --checksums must be supplied together")
    if args.mode == "packaging" and args.suite:
        parser.error("--suite applies only to source checks")
    if args.mode != "packaging" and args.dist_dir:
        parser.error("--dist-dir applies only to packaging")
    if args.mode != "packaging":
        return summarize(args.mode, source_checks(args.mode, args.suite))
    try:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        if args.dist_dir:
            results = packaging_checks(args.dist_dir.resolve(), args.checksums.resolve(), build=False, version=version)
        else:
            with tempfile.TemporaryDirectory(prefix="dead-letter-package-") as temporary:
                root = Path(temporary)
                results = packaging_checks(root / "dist", root / "SHA256SUMS", build=True, version=version)
    except (OSError, ValueError, KeyError) as exc:
        results = [{"name": "packaging-setup", "status": "could-not-run", "reason": str(exc)}]
    return summarize(args.mode, results)


if __name__ == "__main__":
    raise SystemExit(main())
