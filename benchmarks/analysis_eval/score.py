"""Offline paired profile scoring. No provider execution or test-label fitting."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from dead_letter.analysis.eml import prepare_eml
from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.responses import validate_response

from .check_split import check_split
from .metrics import (
    calibration,
    categorical,
    cluster_bootstrap,
    mean,
    percentile,
    risk_coverage,
    wilson,
)

ROOT = Path(__file__).resolve().parent
REPLY = "reply_request_present"
ACTION = "non_reply_action_request_present"
EXPECTATION = "response_expectation"
URGENCY = "expressed_urgency"
ABSTAIN = "insufficient_context"
FOUR = ("none", "reply_only", "non_reply_action_only", "both")
BITS = {
    "none": (False, False),
    "reply_only": (True, False),
    "non_reply_action_only": (False, True),
    "both": (True, True),
}
BINARY = tuple(
    q for q, spec in get_profile().questions().items() if spec["type"] == "noul"
)
QUESTIONS = (*BINARY, URGENCY, EXPECTATION)
if set(get_profile("triage-choice-v1").questions()[EXPECTATION]["criteria"]) != {
    *FOUR,
    ABSTAIN,
}:
    raise ValueError("Choice profile drift: review the evaluation mapping")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def marginalize(probabilities):
    return {
        REPLY: probabilities["reply_only"] + probabilities["both"],
        ACTION: probabilities["non_reply_action_only"] + probabilities["both"],
    }


def _read_json(path):
    def invalid(value):
        raise ValueError("nonfinite JSON")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(
        path.read_text(), parse_constant=invalid, object_pairs_hook=unique
    )


def load_run(directory, profile, cases, source_root):
    """Accept arbitrary identity subtrees; basenames are globally unique case IDs.

    Failed/skipped persistence records live in <case>.analysis.json.attempts/.
    Count all records, but never substitute them for a successful prediction.
    """
    if not directory.is_dir():
        raise ValueError(f"run directory missing: {directory}")
    by_id = {c["case_id"]: c for c in cases}
    success, seen, records, statuses, malformed = {}, set(), [], Counter(), []
    item_status = {key: "missing" for key in by_id}
    mismatches = []
    for path in sorted(directory.rglob("*.json")):
        attempt_parent = next(
            (p for p in path.parents if p.name.endswith(".analysis.json.attempts")),
            None,
        )
        if path.name.endswith(".analysis.json"):
            case_id = path.name.removesuffix(".analysis.json")
            # #165 may preserve the .eml extension before the sidecar suffix.
            if case_id.endswith(".eml"):
                case_id = case_id[:-4]
            if case_id in seen:
                raise ValueError(f"duplicate sidecar for {case_id}")
            seen.add(case_id)
        elif attempt_parent is not None:
            case_id = attempt_parent.name.removesuffix(
                ".analysis.json.attempts"
            ).removesuffix(".eml")
        else:
            continue
        if case_id not in by_id:
            raise ValueError(f"unknown sidecar case: {case_id}")
        try:
            result = _read_json(path)
            if not isinstance(result, dict):
                raise ValueError("envelope must be an object")
        except (ValueError, OSError) as exc:
            malformed.append({"path": str(path), "reason": str(exc)})
            if attempt_parent is None:
                item_status[case_id] = "malformed"
            continue
        if not isinstance(result.get("profile"), dict) or not result["profile"].get(
            "name"
        ):
            malformed.append({"path": str(path), "reason": "missing profile identity"})
            if attempt_parent is None:
                item_status[case_id] = "malformed"
            continue
        if result["profile"]["name"] != profile:
            raise ValueError(f"profile mismatch in {path}")
        status = result.get("execution_status")
        if (
            status not in ("succeeded", "failed", "skipped")
            or result.get("schema_version") != 1
            or result.get("artifact_type") != "message_analysis"
        ):
            malformed.append(
                {"path": str(path), "reason": "invalid envelope schema/status"}
            )
            if attempt_parent is None:
                item_status[case_id] = "malformed"
            continue
        statuses[status] += 1
        records.append(
            {
                "case_id": case_id,
                "path": str(path),
                "result": result,
                "attempt_record": attempt_parent is not None,
            }
        )
        if status != "succeeded" or attempt_parent is not None:
            if item_status[case_id] == "missing":
                item_status[case_id] = (
                    status if status in {"failed", "skipped"} else "malformed"
                )
            continue
        try:
            case = by_id[case_id]
            prepared = prepare_eml(
                source_root / case["file"],
                profile_name=profile,
                focus_identity=tuple(case["focus_identity"]),
                model="jev-1.13.0",
            )
            if (
                result.get("source", {}).get("sha256")
                != prepared.snapshot.source_sha256
            ):
                raise ValueError("source hash mismatch")
            if result.get("state_sha256") != prepared.request.state_sha256:
                raise ValueError(
                    "state hash mismatch: inspect identity/context configuration"
                )
            if (
                result.get("profile", {}).get("sha256")
                != prepared.request.profile.sha256
            ):
                raise ValueError("profile hash mismatch")
            if result.get("requested_model") != "jev-1.13.0":
                raise ValueError("requested model is not pinned jev-1.13.0")
            response = {
                "answers": copy.deepcopy(result["answers"]),
                "model": result.get("returned_model"),
                "usage": result.get("usage"),
                "request_id": result.get("request_id"),
            }
            choice = response["answers"].get(EXPECTATION)
            mismatch = False
            if choice:
                probabilities = choice["probabilities"]
                winner = max(sorted(probabilities), key=probabilities.get)
                mismatch = choice["choice"] != winner
                if choice["choice"] not in probabilities:
                    raise ValueError("unknown returned choice")
                # Diagnose foreign inconsistent choices without relaxing runtime validation.
                choice["choice"] = winner
            validate_response(prepared.request, response)
            if mismatch:
                mismatches.append(case_id)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            malformed.append({"path": str(path), "reason": str(exc)})
            item_status[case_id] = "malformed"
            records.pop()  # Invalid successes do not supply trusted usage/model observations.
            continue
        success[case_id] = result
        item_status[case_id] = "succeeded"
    return (
        success,
        {
            "item_status": item_status,
            "envelope_status_counts": dict(statuses),
            "malformed": malformed,
            "argmax_choice_mismatch_ids": mismatches,
        },
        records,
    )


def fit_thresholds(cases, predictions, profile):
    # Filter on metadata before touching labels. Callers may supply test records
    # whose labels are unreadable; this function never inspects those fields.
    dev = sorted(
        (c for c in cases if c["split"] == "dev" and c["arm"] == "clean"),
        key=lambda c: c["case_id"],
    )
    fitted = {}
    for question, spec in get_profile(profile).questions().items():
        if spec["type"] != "noul":
            continue
        rows = [
            (
                c["case_id"],
                predictions[c["case_id"]]["answers"][question]["noul"],
                c["labels"][question],
            )
            for c in dev
            if c["case_id"] in predictions and c["labels"][question] is not None
        ]
        if not rows:
            raise ValueError(
                f"no eligible dev clean observations for {profile}/{question}"
            )
        candidates = [i / 100 for i in range(10, 91, 5)]
        threshold = min(
            candidates,
            key=lambda t: (sum((p >= t) != y for _, p, y in rows), abs(t - 0.5), t),
        )
        fitted[question] = {
            "threshold": threshold,
            "n": len(rows),
            "dev_case_ids": [i for i, _, _ in rows],
            "data_sha256": digest(rows),
            "dev_errors": sum((p >= threshold) != y for _, p, y in rows),
        }
    result = {
        "questions": fitted,
        "fit_split": "dev",
        "fit_arm": "clean",
        "objective": "minimum binary error; tie: nearest .50 then lower",
        "candidates": [i / 100 for i in range(10, 91, 5)],
    }
    if profile == "triage-v1":
        rows = [
            c
            for c in dev
            if c["case_id"] in predictions and c["labels"][EXPECTATION] in FOUR
        ]
        options = []
        for width in (0.0, 0.05, 0.10, 0.15, 0.20, 0.25):
            accepted = []
            for c in rows:
                answers = predictions[c["case_id"]]["answers"]
                if any(
                    abs(answers[q]["noul"] - fitted[q]["threshold"]) < width
                    for q in (REPLY, ACTION)
                ):
                    continue
                bits = tuple(
                    answers[q]["noul"] >= fitted[q]["threshold"]
                    for q in (REPLY, ACTION)
                )
                label = next(k for k, v in BITS.items() if v == bits)
                accepted.append(label != c["labels"][EXPECTATION])
            if accepted and mean(accepted) <= 0.1:
                options.append((len(accepted), width, mean(accepted)))
        chosen = min(options, key=lambda v: (-v[0], v[1])) if options else None
        result["review_band"] = {
            "status": "fitted" if chosen else "no qualifying band",
            "half_width": chosen[1] if chosen else None,
            "accepted_dev": chosen[0] if chosen else 0,
            "eligible_dev": len(rows),
            "dev_error": chosen[2] if chosen else None,
            "dev_case_ids": [c["case_id"] for c in rows],
            "policy": "shared half-width around reply/action thresholds; strict interior; width 0 abstains on none",
        }
    return result


def normalize(result, profile, thresholds):
    if profile == "baseline":
        decisions = result["decisions"]
        return {
            "decisions": decisions,
            "probabilities": {},
            "decision_probability": 1.0,
            "abstain": decisions[EXPECTATION] == ABSTAIN,
        }
    decisions, probabilities = {}, {}
    answers = result["answers"]
    for q in BINARY:
        if q not in answers:
            continue
        p = answers[q]["noul"]
        probabilities[q] = p
        decisions[q] = p >= thresholds["questions"][q]["threshold"]
    urgency = answers[URGENCY]["probabilities"]
    probabilities[URGENCY] = urgency
    decisions[URGENCY] = int(max(sorted(urgency), key=urgency.get))
    if EXPECTATION in answers:
        answer = answers[EXPECTATION]
        label = answer["choice"]
        probabilities.update(marginalize(answer["probabilities"]))
        probabilities[EXPECTATION] = answer["probabilities"]
        decisions[REPLY], decisions[ACTION] = BITS.get(label, (None, None))
        confidence = answer["probabilities"][label]
        abstain = label == ABSTAIN
    else:
        label = next(
            k for k, v in BITS.items() if v == (decisions[REPLY], decisions[ACTION])
        )
        # No joint distribution is invented from two marginal Nouls.
        confidence = min(
            probabilities[q] if decisions[q] else 1 - probabilities[q]
            for q in (REPLY, ACTION)
        )
        width = thresholds["review_band"]["half_width"]
        abstain = width is None or any(
            abs(probabilities[q] - thresholds["questions"][q]["threshold"]) < width
            for q in (REPLY, ACTION)
        )
    decisions[EXPECTATION] = label
    return {
        "decisions": decisions,
        "probabilities": probabilities,
        "decision_probability": confidence,
        "abstain": abstain,
    }


def question_metrics(cases, predictions, question):
    classes = (
        FOUR
        if question == EXPECTATION
        else (0, 1, 2)
        if question == URGENCY
        else (False, True)
    )
    available = [c for c in cases if c["case_id"] in predictions]
    usable = [
        c
        for c in available
        if c["labels"][question] is not None
        and (question != EXPECTATION or c["labels"][question] in FOUR)
    ]
    truths = [c["labels"][question] for c in usable]
    decisions = [predictions[c["case_id"]]["decisions"][question] for c in usable]
    result = categorical(truths, decisions, classes)
    result.update(
        expected=len(cases),
        successful=len(available),
        missing_or_failed=len(cases) - len(available),
        excluded_unknown=len(available) - len(usable),
    )
    if question in BINARY:
        calibrated = [
            c for c in usable if question in predictions[c["case_id"]]["probabilities"]
        ]
        result["calibration"] = calibration(
            [predictions[c["case_id"]]["probabilities"][question] for c in calibrated],
            [int(c["labels"][question]) for c in calibrated],
        )
    else:
        # Choice Brier includes all five classes and ambiguous truth; four-way
        # confusion above excludes ambiguous truth. Urgency includes all levels.
        calibrated = [
            c
            for c in available
            if c["labels"][question] is not None
            and question in predictions[c["case_id"]]["probabilities"]
        ]
        distributions = [
            predictions[c["case_id"]]["probabilities"][question] for c in calibrated
        ]
        targets = [str(c["labels"][question]) for c in calibrated]
        result["multiclass_brier"] = mean(
            [
                sum((p - int(k == y)) ** 2 for k, p in distribution.items())
                for distribution, y in zip(distributions, targets)
            ]
        )
        result["multiclass_calibration_n"] = len(calibrated)
        result["top_label_calibration"] = calibration(
            [max(d.values()) for d in distributions],
            [
                int(max(sorted(d), key=d.get) == y)
                for d, y in zip(distributions, targets)
            ],
        )
    return result


def pair_metrics(cases, predictions):
    def measure(selected):
        usable = [
            c
            for c in selected
            if c["case_id"] in predictions and c["pair_of"] in predictions
        ]
        flips, changes = [], []
        for c in usable:
            current, clean = predictions[c["case_id"]], predictions[c["pair_of"]]
            flips.append(
                (
                    c["thread_root"],
                    int(
                        current["decisions"][EXPECTATION]
                        != clean["decisions"][EXPECTATION]
                    ),
                )
            )
            changes.append(
                (c["thread_root"], int(current["abstain"]) - int(clean["abstain"]))
            )
        return {
            "expected_pairs": len(selected),
            "dropped_pairs": len(selected) - len(usable),
            "flip_rate": wilson(sum(v for _, v in flips), len(flips)),
            "paired_flip_interval": cluster_bootstrap(flips),
            "abstention_change": cluster_bootstrap(changes),
            "clean_abstentions": sum(
                predictions[c["pair_of"]]["abstain"] for c in usable
            ),
            "variant_abstentions": sum(
                predictions[c["case_id"]]["abstain"] for c in usable
            ),
        }

    adversarial = [c for c in cases if c["arm"] == "adversarial"]
    missing = [c for c in cases if c["arm"] == "missing_context"]
    return {
        "adversarial_exposed": measure(
            [c for c in adversarial if c["attack_location"] != "attachment"]
        ),
        "unexposed_control": measure(
            [c for c in adversarial if c["attack_location"] == "attachment"]
        ),
        "adversarial_by_location": {
            location: measure(
                [c for c in adversarial if c["attack_location"] == location]
            )
            for location in sorted({c["attack_location"] for c in adversarial})
        },
        "missing_context": measure(missing),
        "missing_relevant": measure([c for c in missing if c["ambiguity"]]),
        "missing_detail_only": measure([c for c in missing if not c["ambiguity"]]),
        "long_thread": measure([c for c in cases if c["arm"] == "long_thread"]),
    }


def summarize(cases, predictions):
    def questions(selected):
        return {q: question_metrics(selected, predictions, q) for q in QUESTIONS}

    clean = [c for c in cases if c["arm"] == "clean"]
    available = [c for c in cases if c["case_id"] in predictions]
    abstained = [
        c
        for c in available
        if predictions[c["case_id"]]["decisions"][EXPECTATION] == ABSTAIN
    ]
    cc = [c for c in clean if c["attribution"] == "cc_not_asked"]
    cc_available = [c for c in cc if c["case_id"] in predictions]
    result = {
        "headline_clean": questions(clean),
        "by_arm": {
            arm: questions([c for c in cases if c["arm"] == arm])
            for arm in sorted({c["arm"] for c in cases})
        },
        "by_attribution_clean": {
            scope: questions([c for c in clean if c["attribution"] == scope])
            for scope in sorted({c["attribution"] for c in clean})
        },
        "insufficient_context": {
            "precision_ambiguity": wilson(
                sum(c["ambiguity"] for c in abstained), len(abstained)
            ),
            "precision_missing_arm": wilson(
                sum(c["arm"] == "missing_context" for c in abstained), len(abstained)
            ),
            "precision_relevant_missing": wilson(
                sum(
                    c["arm"] == "missing_context" and c["ambiguity"] for c in abstained
                ),
                len(abstained),
            ),
            "successful": len(available),
        },
        "cc_not_asked": {
            "expected": len(cc),
            "successful": len(cc_available),
            "false_positive": wilson(
                sum(
                    any(
                        predictions[c["case_id"]]["decisions"][q] is True
                        for q in (REPLY, ACTION)
                    )
                    for c in cc_available
                ),
                len(cc_available),
            ),
            "abstentions": sum(
                predictions[c["case_id"]]["abstain"] for c in cc_available
            ),
        },
        "pairs": pair_metrics(cases, predictions),
    }
    reviewed = [c for c in available if predictions[c["case_id"]]["abstain"]]
    result["review_abstention_precision"] = {
        "ambiguity": wilson(sum(c["ambiguity"] for c in reviewed), len(reviewed)),
        "relevant_missing": wilson(
            sum(c["arm"] == "missing_context" and c["ambiguity"] for c in reviewed),
            len(reviewed),
        ),
        "note": "Includes fitted Noul review bands; separate from native insufficient_context.",
    }
    result["cc_not_asked"]["per_question_false_positive"] = {
        q: wilson(
            sum(
                predictions[c["case_id"]]["decisions"][q] is True for c in cc_available
            ),
            len(cc_available),
        )
        for q in (REPLY, ACTION)
    }
    result["risk_coverage"] = {}
    for arm in sorted({c["arm"] for c in cases}):
        selected = [c for c in cases if c["arm"] == arm]
        rows = []
        for c in selected:
            if c["case_id"] not in predictions or c["labels"][EXPECTATION] not in FOUR:
                continue
            p = predictions[c["case_id"]]
            rows.append(
                (
                    c["case_id"],
                    p["decision_probability"],
                    p["decisions"][EXPECTATION] != c["labels"][EXPECTATION],
                    p["abstain"],
                )
            )
        result["risk_coverage"][arm] = {
            **risk_coverage(rows),
            "expected": len(selected),
            "excluded": len(selected) - len(rows),
        }
    return result


def compare(cases, first, second):
    result = {}
    for arm in sorted({c["arm"] for c in cases}):
        selected = [c for c in cases if c["arm"] == arm]
        result[arm] = {}
        for q in QUESTIONS:
            eligible = [
                c
                for c in selected
                if c["labels"][q] is not None
                and (q != EXPECTATION or c["labels"][q] in FOUR)
            ]
            paired = [
                c for c in eligible if c["case_id"] in first and c["case_id"] in second
            ]
            rows = [
                (
                    c["thread_root"],
                    int(second[c["case_id"]]["decisions"][q] == c["labels"][q])
                    - int(first[c["case_id"]]["decisions"][q] == c["labels"][q]),
                )
                for c in paired
            ]
            result[arm][q] = {
                **cluster_bootstrap(rows),
                "eligible": len(eligible),
                "dropped_pairs": len(eligible) - len(paired),
                "direction": "triage-choice-v1 correctness minus triage-v1 correctness",
            }
    return result


def operational(records, ids):
    selected = [r for r in records if r["case_id"] in ids]
    items = []
    for record in selected:
        result = record["result"]
        attempts = result.get("attempts")
        durations = (
            [a.get("duration_ms") if isinstance(a, dict) else None for a in attempts]
            if isinstance(attempts, list)
            else []
        )
        valid = [
            v
            for v in durations
            if type(v) in (int, float) and math.isfinite(v) and v >= 0
        ]
        usage = result.get("usage")
        tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        if type(tokens) is not int or tokens < 0:
            tokens = None
        items.append(
            {
                "case_id": record["case_id"],
                "path": record["path"],
                "execution_status": result.get("execution_status"),
                "input_tokens": tokens,
                "attempt_duration_ms": valid,
                "summed_attempt_duration_ms": sum(valid)
                if valid and len(valid) == len(durations)
                else None,
                "returned_model": result.get("returned_model"),
                "sdk_version": result.get("sdk_version"),
                "profile": result.get("profile"),
                "requested_model": result.get("requested_model"),
                "adapter_version": result.get("adapter_version"),
            }
        )

    def percentiles(values):
        return {
            "n": len(values),
            **{f"p{p}": percentile(values, p / 100) for p in (50, 90, 95, 99)},
        }

    return {
        "items": items,
        "input_tokens": percentiles(
            [i["input_tokens"] for i in items if i["input_tokens"] is not None]
        ),
        "unknown_input_tokens_records": sum(i["input_tokens"] is None for i in items),
        "attempt_latency_ms": percentiles(
            [v for i in items for v in i["attempt_duration_ms"]]
        ),
        "summed_attempt_latency_ms": percentiles(
            [
                i["summed_attempt_duration_ms"]
                for i in items
                if i["summed_attempt_duration_ms"] is not None
            ]
        ),
        "observed_returned_models": dict(
            Counter(
                i["returned_model"]
                for i in items
                if isinstance(i["returned_model"], str)
            )
        ),
        "unknown_returned_model_records": sum(
            i["returned_model"] is None for i in items
        ),
        "limits": "Usage is response-reported, not total billed usage. Failed retries may be billed but unreported. Attempt durations exclude retry sleep and local processing.",
    }


def evaluate(document, runs, *, source_root=ROOT, baseline=None, split="test"):
    cases = document["cases"]
    selected = [c for c in cases if c["split"] == split]
    report = {
        "schema_version": 1,
        "status": "descriptive comparison to model pre-labels; not a held-out quality claim",
        "split": split,
        "labels_sha256": digest(document),
        "thresholds": {},
        "runs": {},
        "policy": {
            "headline_arm": "clean",
            "adversarial_attachment": "unexposed control",
            "ece_bins": 10,
            "bootstrap_seed": 166,
            "bootstrap_replicates": 2000,
            "calibration_confidence": "probabilities only; never native confidence",
        },
    }
    normalized = {}
    for profile, directory in runs.items():
        raw, diagnostics, records = load_run(directory, profile, cases, source_root)
        thresholds = fit_thresholds(cases, raw, profile)
        report["thresholds"][profile] = thresholds
        normalized[profile] = {
            key: normalize(value, profile, thresholds) for key, value in raw.items()
        }
        report["runs"][profile] = summarize(selected, normalized[profile])
        statuses = diagnostics["item_status"]
        diagnostics["selected_status_counts"] = dict(
            Counter(statuses[c["case_id"]] for c in selected)
        )
        diagnostics["argmax_choice_mismatch_count"] = sum(
            c["case_id"] in diagnostics["argmax_choice_mismatch_ids"] for c in selected
        )
        report["runs"][profile]["diagnostics"] = diagnostics
        report["runs"][profile]["operations"] = operational(
            records, {c["case_id"] for c in selected}
        )
    if baseline is not None:
        from .baseline import RULE_VERSION

        if (
            baseline.get("schema_version") != 1
            or baseline.get("rule_version") != RULE_VERSION
        ):
            raise ValueError("unknown baseline format/version")
        raw = baseline["predictions"]
        by_id = {c["case_id"]: c for c in cases}
        for case_id, prediction in raw.items():
            if case_id not in by_id or set(prediction["decisions"]) != set(QUESTIONS):
                raise ValueError("baseline case/question mismatch")
            decisions = prediction["decisions"]
            if any(
                decisions[q] is not None and type(decisions[q]) is not bool
                for q in BINARY
            ):
                raise ValueError("invalid baseline binary decision")
            if decisions[EXPECTATION] not in (*FOUR, ABSTAIN):
                raise ValueError("invalid baseline expectation")
            if decisions[URGENCY] is not None and (
                type(decisions[URGENCY]) is not int
                or decisions[URGENCY] not in (0, 1, 2)
            ):
                raise ValueError("invalid baseline urgency")
            c = by_id[case_id]
            prepared = prepare_eml(
                source_root / c["file"],
                focus_identity=tuple(c["focus_identity"]),
                model="jev-1.13.0",
            )
            if (
                prediction.get("source_sha256") != prepared.snapshot.source_sha256
                or prediction.get("state_sha256") != prepared.request.state_sha256
            ):
                raise ValueError("baseline evidence mismatch")
        normalized["baseline"] = {
            key: normalize(value, "baseline", {}) for key, value in raw.items()
        }
        report["runs"]["baseline"] = {
            **summarize(selected, normalized["baseline"]),
            "rule_version": RULE_VERSION,
            "calibration_note": "Rules do not supply probabilities; calibration is not computed. Ranking ties use case ID.",
        }
    if {"triage-v1", "triage-choice-v1"} <= normalized.keys():
        report["paired_profile_difference"] = compare(
            selected, normalized["triage-v1"], normalized["triage-choice-v1"]
        )
    return report


def write_report(report, out):
    out.mkdir(parents=True, exist_ok=False)
    (out / "metrics.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    lines = [
        "# Synthetic profile evaluation",
        "",
        report["status"],
        "",
        f"Split: `{report['split']}`. Headline: clean arm only.",
        "Labels remain model pre-labels. Adversarial and unexposed attachment controls are separate.",
        "",
        "Full confusion matrices, intervals, calibration bins, risk curves, pairs, usage and frozen dev thresholds are in [metrics.json](metrics.json).",
        "",
        "| Run | Clean question | N | Error | Wilson 95% |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for name, run in report["runs"].items():
        for q, metrics in run["headline_clean"].items():
            error = metrics["error"]
            lines.append(
                f"| {name} | {q} | {error['n']} | {error['rate']} | {error['ci95']} |"
            )
    for name, run in report["runs"].items():
        lines += [
            "",
            f"## {name}",
            "",
            "| Question | Brier | ECE | SmoothECE |",
            "| --- | ---: | ---: | --- |",
        ]
        for q, value in run["headline_clean"].items():
            calibrated = value.get(
                "calibration", value.get("top_label_calibration", {})
            )
            brier = value.get("multiclass_brier", calibrated.get("brier"))
            lines.append(
                f"| {q} | {brier} | {calibrated.get('ece')} | {calibrated.get('smooth_ece')} |"
            )
        lines += [
            "",
            "Clean risk/review summary:",
            "",
            "```json",
            json.dumps(
                {
                    key: value
                    for key, value in run["risk_coverage"].get("clean", {}).items()
                    if key != "points"
                },
                indent=2,
            ),
            "```",
            "",
        ]
        lines += [
            "",
            "Diagnostics and strata:",
            "",
            "```json",
            json.dumps(
                {
                    key: run[key]
                    for key in (
                        "diagnostics",
                        "cc_not_asked",
                        "insufficient_context",
                        "review_abstention_precision",
                        "pairs",
                        "operations",
                    )
                    if key in run
                },
                indent=2,
            ),
            "```",
            "",
        ]
    lines += [
        "## Frozen dev fitting",
        "",
        "```json",
        json.dumps(report["thresholds"], indent=2),
        "```",
        "",
        "## Paired correctness differences",
        "",
        "```json",
        json.dumps(report.get("paired_profile_difference", {}), indent=2),
        "```",
        "",
        "AURC/AUGRC integrate only attainable coverage. Compare max coverage as well as area. Abstentions consume review budget.",
        "SmoothECE is not computed when optional relplot is absent. Native confidence is never treated as correctness probability.",
        "Token usage is not verified billing; attempt latency omits retry sleeps. Missing values remain unknown.",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=ROOT / "labels.json")
    parser.add_argument("--run", action="append", default=[], metavar="PROFILE=DIR")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    document = _read_json(args.labels)
    check_split(document, args.labels.parent)
    runs = {}
    for run in args.run:
        profile, directory = run.split("=", 1)
        get_profile(profile)
        if profile in runs:
            parser.error("duplicate profile run")
        runs[profile] = Path(directory)
    if not runs and args.baseline is None:
        parser.error("provide at least one run or a baseline")
    report = evaluate(
        document,
        runs,
        source_root=args.labels.parent,
        baseline=_read_json(args.baseline) if args.baseline else None,
        split=args.split,
    )
    write_report(report, args.out)
    print(
        json.dumps({"report": str(args.out / "report.md"), "status": report["status"]})
    )


if __name__ == "__main__":
    main()
