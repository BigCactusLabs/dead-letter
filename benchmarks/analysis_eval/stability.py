"""Compare separate repeat-run sidecar trees offline; never execute or fit a model."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.responses import ANSWER_CONSISTENCY_TOLERANCE

from .score import ROOT, _read_json, digest, load_run
from .stage import select_cases

PROVENANCE = (
    "provider",
    "endpoint",
    "adapter_version",
    "sdk_version",
    "requested_model",
    "returned_model",
    "normalization_version",
    "state_builder_version",
    "request_fingerprint",
)


def _decision(answer):
    if answer["type"] == "noul":
        return answer["noul"] >= 0.5
    if answer["type"] == "choice":
        return answer["choice"]
    probabilities = answer["probabilities"]
    return max(sorted(probabilities, key=int), key=probabilities.get)


def _drift(first, second):
    if first["type"] == "noul":
        return abs(second["noul"] - first["noul"])
    return (
        sum(
            abs(second["probabilities"][key] - probability)
            for key, probability in first["probabilities"].items()
        )
        / 2
    )


def _summary(values):
    return {
        "mean": sum(values) / len(values) if values else None,
        "max": max(values) if values else None,
    }


def _incomparable(first, second):
    for field in PROVENANCE:
        if any(
            not isinstance(item.get(field), str) or not item[field]
            for item in (first, second)
        ):
            return f"missing_{field}"
        if first[field] != second[field]:
            return f"different_{field}"
    if first["provider"] != "typesafe":
        return "unsupported_provider"
    # The quality scorer diagnoses foreign inconsistent Choices. A repeat-run
    # comparison excludes them; native tied Choices remain legitimate labels.
    for item in (first, second):
        for answer in item["answers"].values():
            if answer["type"] == "choice":
                probabilities = answer["probabilities"]
                if (
                    max(probabilities.values()) - probabilities[answer["choice"]]
                    > ANSWER_CONSISTENCY_TOLERANCE
                ):
                    return "inconsistent_choice_distribution"
    return None


def compare(
    document,
    runs,
    *,
    profile="triage-v1",
    source_root=ROOT,
    split=None,
    arms=(),
    limit=None,
):
    """Compare each named repeat to the first run on the same selected case IDs.

    Label values never enter decisions or metrics. Missing/invalid successes
    and differing execution provenance are excluded with explicit denominators.
    """
    if len(runs) < 2:
        raise ValueError("provide at least two separately named runs")
    directories = {}
    for name, path in runs.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValueError("run names must be simple identifiers")
        path = Path(path).resolve()
        if any(
            path.is_relative_to(other) or other.is_relative_to(path)
            for other in directories.values()
        ):
            raise ValueError("run directories must be separate, non-overlapping trees")
        directories[name] = path
    cases = document["cases"]
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("duplicate case_id")
    selected = select_cases(document, split, arms, limit)
    if not selected:
        raise ValueError("selection is empty")
    ids = [case["case_id"] for case in selected]
    questions = get_profile(profile).questions()
    report = {
        "schema_version": 1,
        "status": "repeat-run stability, not correctness or calibration evidence",
        "profile": profile,
        "labels_sha256": digest(document),
        "label_sources": dict(
            Counter(case.get("label_source", "unknown") for case in selected)
        ),
        "human_review_status": dict(
            Counter(case.get("human_review_status", "unknown") for case in selected)
        ),
        "selected_case_ids": ids,
        "selection": {"split": split, "arms": list(arms), "limit": limit},
        "policy": {
            "reference": next(iter(runs)),
            "noul_label": "probability >= 0.5; fixed diagnostic rule, no fitting",
            "choice_label": "provider-returned choice, including insufficient_context",
            "score_label": "highest-probability level; lowest level wins ties",
            "probability_drift": "absolute Noul change; half L1 distance of reported Choice/Score distributions (no renormalization)",
            "confidence": "native confidence is not used",
            "limits": "Separate artifacts do not prove independent API execution; returned model identity and source-code provenance are provider/operator observations.",
        },
        "runs": {},
        "comparisons": {},
    }
    loaded = {}
    for name, path in directories.items():
        raw, diagnostics, _ = load_run(path, profile, cases, source_root)
        loaded[name] = raw
        report["runs"][name] = {
            "directory": str(path),
            "selected_status_counts": dict(
                Counter(diagnostics["item_status"][key] for key in ids)
            ),
            "selected_artifacts_sha256": digest(
                {key: raw[key] for key in ids if key in raw}
            ),
            "diagnostics": diagnostics,
        }
    reference = report["policy"]["reference"]
    for name in list(runs)[1:]:
        first, second = loaded[reference], loaded[name]
        matched, excluded = [], {}
        for key in ids:
            if key not in first or key not in second:
                excluded[key] = "no_valid_success_in_both_runs"
                continue
            reason = _incomparable(first[key], second[key])
            if reason:
                excluded[key] = reason
            else:
                matched.append(key)
        metrics = {}
        for question, spec in questions.items():
            pairs = [
                (key, first[key]["answers"][question], second[key]["answers"][question])
                for key in matched
            ]
            flips = [key for key, a, b in pairs if _decision(a) != _decision(b)]
            metrics[question] = {
                "type": spec["type"],
                "n": len(pairs),
                "label_flips": len(flips),
                "label_flip_rate": len(flips) / len(pairs) if pairs else None,
                "flipped_case_ids": flips,
                "probability_drift": _summary([_drift(a, b) for _, a, b in pairs]),
            }
            if spec["type"] == "score":
                metrics[question]["absolute_score_change"] = _summary(
                    [abs(b["score"] - a["score"]) for _, a, b in pairs]
                )
        report["comparisons"][name] = {
            "reference": reference,
            "selected": len(ids),
            "matched": len(matched),
            "matched_case_ids": matched,
            "excluded": excluded,
            "questions": metrics,
        }
    return report


def write_report(report, out):
    out = Path(out).resolve()
    if any(
        out.is_relative_to(Path(run["directory"])) for run in report["runs"].values()
    ):
        raise ValueError("report must be outside input run trees")
    out.mkdir(parents=True, exist_ok=False)
    (out / "metrics.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    lines = [
        "# Repeat-run stability",
        "",
        report["status"] + ".",
        "",
        "Compared with the first named run. Label values are not used; no thresholds are fitted.",
        "See `metrics.json` for artifact/label hashes, selected case IDs, exclusions and decision rules.",
        "",
        "| Repeat | Matched / selected | Question | Label flips | Mean probability drift |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, comparison in report["comparisons"].items():
        for question, metrics in comparison["questions"].items():
            drift = metrics["probability_drift"]["mean"]
            lines.append(
                f"| {name} | {comparison['matched']} / {comparison['selected']} | {question} | "
                f"{metrics['label_flips']} / {metrics['n']} | {drift if drift is not None else 'not computed'} |"
            )
    lines.extend(
        [
            "",
            report["policy"]["probability_drift"] + ".",
            "",
            "Zero matched cases means no estimate, not zero instability. "
            "Copies/caches cannot establish independent repeat inference.",
        ]
    )
    (out / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=ROOT / "labels.json")
    parser.add_argument(
        "--profile", choices=("triage-v1", "triage-choice-v1"), required=True
    )
    parser.add_argument("--run", action="append", required=True, metavar="NAME=DIR")
    parser.add_argument("--split", choices=("dev", "test"))
    parser.add_argument(
        "--arm",
        action="append",
        default=[],
        choices=("clean", "missing_context", "adversarial", "long_thread"),
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        runs = {}
        for value in args.run:
            name, directory = value.split("=", 1)
            if name in runs or not directory:
                raise ValueError("each run needs a unique name and a directory")
            runs[name] = Path(directory)
        report = compare(
            _read_json(args.labels),
            runs,
            profile=args.profile,
            source_root=args.labels.parent,
            split=args.split,
            arms=args.arm,
            limit=args.limit,
        )
        write_report(report, args.out)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(
        json.dumps({"report": str(args.out / "report.md"), "status": report["status"]})
    )


if __name__ == "__main__":
    main()
