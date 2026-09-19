"""Describe the checkout and pre-test import resolution without reading mail.

Run with the same interpreter/environment as the tests. This is diagnostic
provenance, not a signed attestation or proof of a child process's imports.
Only allowlisted CI metadata and source-file hashes are printed.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Mapping

ENV_KEYS = (
    "GITHUB_SHA", "GITHUB_REF", "GITHUB_HEAD_REF", "GITHUB_BASE_REF",
    "GITHUB_EVENT_NAME", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
    "RUNNER_OS",
)
PATTERNS = (
    ".github/workflows/*.yml", ".github/workflows/*.yaml",
    "pyproject.toml", "uv.lock", "scripts/ci_provenance.py",
    "scripts/*mbox*.py", "src/dead_letter/*mbox*.py",
    "tests/*mbox*.py", "tests/*stream_report*.py",
    "tests/core/test_ci_provenance.py",
)
# Absence is evidence, not an error: do not invent a test from an old traceback.
TRACEBACK_PATH = "tests/core/test_mbox_stream.py"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        encoding="utf-8", errors="strict", timeout=30,
    )
    return result.stdout


def index_entries(root: Path) -> dict[str, tuple[str, str]]:
    entries = {}
    for entry in git(root, "ls-files", "--stage", "-z").split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode, blob, stage = metadata.split()
        if stage != "0":
            raise ValueError("Checkout has an unresolved index conflict")
        entries[name] = (mode, blob)
    return entries


def fingerprint(root: Path, name: str, mode: str, blob: str) -> dict:
    path = root / name
    result = {"path": name, "index_blob": blob, "mode": mode}
    # Never follow a monitored symlink to user data outside the checkout.
    if mode not in ("100644", "100755") or path.is_symlink():
        return {**result, "status": "not_regular"}
    if not path.resolve().is_relative_to(root.resolve()):
        return {**result, "status": "outside_checkout"}
    if not path.is_file():
        return {**result, "status": "missing"}
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
            size += len(chunk)
    return {**result, "status": "ok", "bytes": size, "sha256": digest.hexdigest()}


def import_origin() -> str | None:
    """Resolve the parser without executing dead_letter package initializers."""
    spec = importlib.util.find_spec("dead_letter")
    for fullname in ("dead_letter.core", "dead_letter.core.mbox"):
        if spec is None or spec.submodule_search_locations is None:
            return None
        spec = importlib.machinery.PathFinder.find_spec(
            fullname, spec.submodule_search_locations,
        )
    return None if spec is None else spec.origin


def event_refs(env: Mapping[str, str]) -> dict:
    """Extract only identifiers; never dump the event, title, body, or env."""
    event_path = env.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    with Path(event_path).open(encoding="utf-8") as stream:
        event = json.load(stream)
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        return {}
    return {
        "number": pr.get("number"),
        "head_sha": pr.get("head", {}).get("sha"),
        "base_sha": pr.get("base", {}).get("sha"),
    }


def snapshot(root: Path, env: Mapping[str, str]) -> dict:
    root = root.resolve()
    if Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve() != root:
        raise ValueError("--root must be the checkout root")
    head = git(root, "rev-parse", "HEAD").strip()
    entries = index_entries(root)
    names = sorted(name for name in entries if any(
        fnmatch.fnmatchcase(name, pattern) for pattern in PATTERNS
    ))
    files = [fingerprint(root, name, *entries[name]) for name in names]
    errors = []
    expected_sha = env.get("GITHUB_SHA")
    if expected_sha and expected_sha != head:
        errors.append("Checked-out HEAD differs from GITHUB_SHA")
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=no").strip())
    if dirty:
        errors.append("Tracked checkout files are modified")
    for item in files:
        if item["status"] != "ok":
            errors.append(f"Monitored file is not a regular checkout file: {item['path']}")
    origin = import_origin()
    parser_path = "src/dead_letter/core/mbox.py"
    expected_origin = root / parser_path
    if parser_path not in entries:
        errors.append("Parser source is not tracked by this checkout")
    if origin is None or Path(origin).resolve() != expected_origin.resolve():
        errors.append("dead_letter.core.mbox does not resolve to this checkout")
    return {
        "schema_version": 1,
        "checkout": {
            "head_sha": head,
            "parents": git(root, "show", "-s", "--format=%P", "HEAD").strip().split(),
            "tracked_dirty": dirty,
        },
        "ci": {key: env[key] for key in ENV_KEYS if key in env},
        "pull_request": event_refs(env),
        "python": {"version": platform.python_version(), "platform": sys.platform},
        "parser_origin": origin,
        "traceback_test": {
            "path": TRACEBACK_PATH,
            "tracked": TRACEBACK_PATH in entries,
            "exists": (root / TRACEBACK_PATH).is_file(),
        },
        "files": files,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        report = snapshot(args.root, os.environ)
    except (OSError, ValueError, ImportError, subprocess.SubprocessError):
        # Do not include command stderr or arbitrary event data in failure logs.
        report = {"schema_version": 1, "errors": ["Unable to collect checkout provenance"]}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
