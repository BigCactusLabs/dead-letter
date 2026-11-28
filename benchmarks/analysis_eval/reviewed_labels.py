"""Create and validate separate human-review artifacts without changing pre-labels."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import jsonschema

from .check_split import check_split
from .score import (
    ABSTAIN,
    ACTION,
    BITS,
    EXPECTATION,
    REPLY,
    ROOT,
    URGENCY,
    _read_json,
    digest,
)

ANNOTATION_FIELDS = {
    "case_id",
    "labels",
    "ambiguity",
    "ambiguity_note",
    "label_source",
    "human_review_status",
}


def _validate_schema(value, filename, name):
    schema = _read_json(ROOT / filename)
    try:
        jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        ).validate(value)
    except jsonschema.ValidationError as exc:
        location = "/".join(map(str, exc.absolute_path)) or "root"
        raise ValueError(
            f"{name} schema violation at {location}: {exc.validator}"
        ) from None


def _bindings(document, source_root):
    _validate_schema(document, "labels.schema.json", "pre-labels")
    source_root = Path(source_root).resolve()
    check_split(document, source_root)
    bindings = {}
    for case in document["cases"]:
        source = (source_root / case["file"]).resolve()
        if not source.is_relative_to(source_root) or not source.is_file():
            raise ValueError(f"invalid corpus source for {case['case_id']}")
        bindings[case["case_id"]] = {
            "case_id": case["case_id"],
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "metadata": {
                key: value
                for key, value in case.items()
                if key not in ANNOTATION_FIELDS
            },
        }
    return bindings


def create_template(document, source_root, *, revision):
    """Copy draft labels with pending review; never fill human provenance fields."""
    bindings = _bindings(document, source_root)
    entries = []
    for case in sorted(document["cases"], key=lambda item: item["case_id"]):
        notes = {
            question: case["ambiguity_note"]
            for question, label in case["labels"].items()
            if label is None or (question == EXPECTATION and label == ABSTAIN)
        }
        if case["ambiguity"] and not notes:
            notes[EXPECTATION] = case["ambiguity_note"]
        entries.append(
            {
                **copy.deepcopy(bindings[case["case_id"]]),
                "labels": copy.deepcopy(case["labels"]),
                "ambiguity_notes": notes,
                "review": {
                    "status": "pending",
                    "reviewers": [],
                    "reviewed_at": None,
                    "adjudicator": None,
                    "note": "",
                },
            }
        )
    result = {
        "schema_version": 1,
        "artifact_type": "analysis_label_review",
        "revision": revision,
        "prelabels_sha256": digest(document),
        "cases": entries,
    }
    _validate_schema(result, "reviewed-labels.schema.json", "review")
    return result


def _validate_labels(entry):
    labels, notes = entry["labels"], entry["ambiguity_notes"]
    if entry["review"]["status"] != "pending":
        # jsonschema's date-time checker is optional and is absent from some
        # locked environments. Validate provenance timestamps without an extra.
        try:
            reviewed_at = datetime.fromisoformat(entry["review"]["reviewed_at"])
            if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(
                f"invalid reviewed_at with timezone: {entry['case_id']}"
            ) from None
    if labels[URGENCY] is not None and type(labels[URGENCY]) is not int:
        raise ValueError(f"urgency must be an integer or null: {entry['case_id']}")
    expected = labels[EXPECTATION]
    bits = (labels[REPLY], labels[ACTION])
    if (expected == ABSTAIN and bits != (None, None)) or (
        expected != ABSTAIN and bits != BITS[expected]
    ):
        raise ValueError(f"inconsistent response expectation: {entry['case_id']}")
    if entry["review"]["status"] != "pending":
        unknown = {
            q
            for q, value in labels.items()
            if value is None or (q == EXPECTATION and value == ABSTAIN)
        }
        if any(not note.strip() for note in notes.values()) or unknown - notes.keys():
            raise ValueError(
                f"reviewed ambiguous labels need per-question notes: {entry['case_id']}"
            )


def validate_review(document, review, source_root, *, split=None):
    """Validate every binding; a requested split also requires complete review.

    Required coverage is every case in that split plus every clean dev case,
    independent of which predictions happened to succeed. No opportunistic fit.
    """
    if split not in (None, "dev", "test"):
        raise ValueError("invalid scored split")
    bindings = _bindings(document, source_root)
    _validate_schema(review, "reviewed-labels.schema.json", "review")
    if review["prelabels_sha256"] != digest(document):
        raise ValueError("pre-label revision mismatch")
    entries = {}
    for entry in review["cases"]:
        key = entry["case_id"]
        if key in entries:
            raise ValueError(f"duplicate reviewed case: {key}")
        if key not in bindings:
            raise ValueError(f"unknown reviewed case: {key}")
        for field in ("metadata", "source_sha256"):
            if entry[field] != bindings[key][field]:
                raise ValueError(f"review {field} mismatch: {key}")
        _validate_labels(entry)
        entries[key] = entry
    if entries.keys() != bindings.keys():
        raise ValueError(
            "review must retain every original case, including pending rows"
        )
    scored = sorted(c["case_id"] for c in document["cases"] if c["split"] == split)
    fitting = sorted(
        c["case_id"]
        for c in document["cases"]
        if c["split"] == "dev" and c["arm"] == "clean"
    )
    required = sorted(set(scored) | set(fitting)) if split is not None else []
    pending = sorted(
        key for key, entry in entries.items() if entry["review"]["status"] == "pending"
    )
    missing = sorted(set(required) & set(pending))
    if missing:
        raise ValueError(
            "review incomplete for scored split or clean-dev fitting cases: "
            + ", ".join(missing)
        )
    return {
        "mode": "reviewed",
        "validated_for_split": split,
        "revision": review["revision"],
        "reviewed_labels_sha256": digest(review),
        "prelabels_sha256": review["prelabels_sha256"],
        "review_status_counts": dict(
            Counter(entry["review"]["status"] for entry in entries.values())
        ),
        "scored_case_ids": scored,
        "clean_dev_case_ids": fitting,
        "required_case_ids": required,
        "pending_case_ids": pending,
        "reviews": {key: copy.deepcopy(entries[key]["review"]) for key in required},
    }


def apply_review(document, review, source_root, *, split):
    """Import an explicit review revision; pending rows have no usable truth."""
    if split not in ("dev", "test"):
        raise ValueError("review import requires an explicit scored split")
    provenance = validate_review(document, review, source_root, split=split)
    imported = copy.deepcopy(document)
    imported["status"] = "reviewed annotation projection; pending rows contain no truth"
    by_id = {entry["case_id"]: entry for entry in review["cases"]}
    for case in imported["cases"]:
        entry = by_id[case["case_id"]]
        pending = entry["review"]["status"] == "pending"
        case["labels"] = None if pending else copy.deepcopy(entry["labels"])
        case["label_source"] = None if pending else "human_review"
        case["human_review_status"] = entry["review"]["status"]
        case["ambiguity"] = bool(entry["ambiguity_notes"])
        case["ambiguity_note"] = "\n".join(
            f"{question}: {note}"
            for question, note in sorted(entry["ambiguity_notes"].items())
        )
    imported["label_review"] = provenance
    return imported, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser(
        "template", help="create a pending editable review revision"
    )
    template.add_argument("--revision", required=True)
    template.add_argument("--out", type=Path, required=True)
    validate = commands.add_parser(
        "validate", help="check bindings and optional scoring coverage"
    )
    validate.add_argument("review", type=Path)
    validate.add_argument("--split", choices=("dev", "test"))
    for command in (template, validate):
        command.add_argument("--labels", type=Path, default=ROOT / "labels.json")
    args = parser.parse_args()
    try:
        document = _read_json(args.labels)
        if args.command == "template":
            artifact = create_template(
                document, args.labels.parent, revision=args.revision
            )
            with args.out.open("x", encoding="utf-8") as target:
                target.write(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
            result = {
                "path": str(args.out),
                "revision": args.revision,
                "pending_cases": len(artifact["cases"]),
            }
        else:
            result = validate_review(
                document, _read_json(args.review), args.labels.parent, split=args.split
            )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
