"""Offline corpus identity, grouping and chronological holdout checks."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def check_split(document: dict, root: Path | None = None) -> dict:
    cases = document["cases"]
    by_id = {case["case_id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("duplicate case_id")
    files = [case["file"] for case in cases]
    if len(set(files)) != len(files):
        raise ValueError("duplicate file")
    groups = {key: {} for key in ("thread_root", "template_family", "mailbox")}
    times = {"dev": [], "test": []}
    for case in cases:
        split = case["split"]
        if split not in times:
            raise ValueError("invalid split")
        stamp = datetime.fromisoformat(case["sent_at"])
        if stamp.tzinfo is None:
            raise ValueError("sent_at must include timezone")
        times[split].append(stamp)
        for key, assignments in groups.items():
            value = case[key]
            if assignments.setdefault(value, split) != split:
                raise ValueError(f"split leakage: {key}={value}")
        pair_id = case["pair_of"]
        if pair_id is not None:
            if pair_id not in by_id or pair_id == case["case_id"]:
                raise ValueError("invalid pair_of")
            pair = by_id[pair_id]
            if pair["arm"] != "clean":
                raise ValueError("pair must reference a clean case")
            if any(case[key] != pair[key] for key in (*groups, "split")):
                raise ValueError("split or group leakage in pair")
        elif case["arm"] != "clean":
            raise ValueError("non-clean case missing pair")
        expected = case["labels"]["response_expectation"]
        reply = case["labels"]["reply_request_present"]
        action = case["labels"]["non_reply_action_request_present"]
        if expected == "insufficient_context":
            if reply is not None or action is not None or not case["ambiguity"]:
                raise ValueError("inconsistent ambiguous labels")
        elif (reply, action) != {
            "none": (False, False),
            "reply_only": (True, False),
            "non_reply_action_only": (False, True),
            "both": (True, True),
        }[expected]:
            raise ValueError("inconsistent response expectation")
    if not all(times.values()) or max(times["dev"]) >= min(times["test"]):
        raise ValueError("test must be a strictly later time slice")
    if root is not None:
        expected = {root / item for item in files}
        actual = set((root / "corpus").glob("*.eml"))
        if expected != actual:
            raise ValueError("corpus file coverage differs from labels")
    return {
        "cases": len(cases),
        "dev": len(times["dev"]),
        "test": len(times["test"]),
        "families": len(groups["template_family"]),
        "threads": len(groups["thread_root"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels", type=Path, default=Path(__file__).with_name("labels.json")
    )
    args = parser.parse_args()
    print(
        json.dumps(check_split(json.loads(args.labels.read_text()), args.labels.parent))
    )


if __name__ == "__main__":
    main()
