"""Copy selected synthetic cases into identity-specific, no-clobber run inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def select_cases(document, split=None, arms=(), limit=None):
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    cases = sorted(
        (
            c
            for c in document["cases"]
            if (split is None or c["split"] == split) and (not arms or c["arm"] in arms)
        ),
        key=lambda c: c["case_id"],
    )
    return cases if limit is None else cases[:limit]


def stage(document, source_root, destination, *, split=None, arms=(), limit=None):
    cases = select_cases(document, split, arms, limit)
    if not cases:
        raise ValueError("selection is empty")
    groups, entries = {}, {}
    for case in cases:
        aliases = sorted(set(a.strip() for a in case["focus_identity"]))
        name = (
            "identity-"
            + hashlib.sha256(
                json.dumps(aliases, separators=(",", ":")).encode()
            ).hexdigest()[:16]
        )
        if name in groups and groups[name] != aliases:
            raise ValueError("identity hash collision")
        groups[name] = aliases
        source = (source_root / case["file"]).resolve()
        if not source.is_relative_to(source_root.resolve()) or not source.is_file():
            raise ValueError("invalid corpus source")
        if Path(case["case_id"]).name != case["case_id"]:
            raise ValueError("invalid case id")
        entries[case["case_id"]] = {
            "path": f"{name}/{case['case_id']}.eml",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "split": case["split"],
            "arm": case["arm"],
        }
    destination.mkdir(parents=True, exist_ok=False)
    for name in groups:
        (destination / name).mkdir()
    for case in cases:
        shutil.copyfile(
            source_root / case["file"], destination / entries[case["case_id"]]["path"]
        )
    manifest = {"schema_version": 1, "groups": groups, "cases": entries}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=ROOT / "labels.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--split", choices=("dev", "test"))
    parser.add_argument(
        "--arm",
        action="append",
        choices=("clean", "missing_context", "adversarial", "long_thread"),
        default=[],
    )
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    manifest = stage(
        json.loads(args.labels.read_text()),
        args.labels.parent,
        args.out,
        split=args.split,
        arms=args.arm,
        limit=args.limit,
    )
    print(json.dumps({"cases": len(manifest["cases"]), "groups": manifest["groups"]}))


if __name__ == "__main__":
    main()
