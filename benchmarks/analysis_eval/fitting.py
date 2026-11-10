"""Offline dev-only probability remapping (#196) and Noul combiner (#197).

Pure standard library. Fitting reads labels only from clean dev cases of an
explicitly imported reviewed revision (#166); model pre-labels are refused.
Test cases are filtered out by metadata before any label is touched, so test
labels cannot change fitted parameters. Exact zeros are floored before any
logarithm. Remapping and combiners are evaluation outputs only; no profile,
threshold or runtime behavior changes.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from . import score, variants
from .metrics import calibration, mean, wilson
from .score import ABSTAIN, ACTION, EXPECTATION, FOUR, REPLY, ROOT

EPS = 1e-6
RIDGE = 1e-3
TEMPERATURE_BOUNDS = (0.05, 20.0)
COMBINER_FEATURES = (REPLY, ACTION)
INSUFFICIENT_CONTEXT_POLICY = {
    "fitting": "A clean dev case whose reviewed reply/action label is null is excluded from that target's fit. The Choice answer is not a combiner feature.",
    "prediction": "When the same combined response's Choice returns insufficient_context, the combiner decision is insufficient_context (routed to review, reply/action decisions null) instead of a four-way label; the scorer counts it as an abstention.",
    "disagreement": "A native Choice insufficient_context counts as flagged for review in the disagreement signal.",
}


def floor(p):
    """Clamp to [EPS, 1 - EPS]; exact 0/1 would make log loss infinite."""
    return min(max(float(p), EPS), 1.0 - EPS)


def floor_distribution(distribution):
    floored = {key: max(float(value), EPS) for key, value in distribution.items()}
    total = math.fsum(floored.values())
    return {key: value / total for key, value in floored.items()}


def logit(p):
    p = floor(p)
    return math.log(p / (1.0 - p))


def sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def binary_log_loss(p, y):
    p = floor(p)
    return -math.log(p if y else 1.0 - p)


def multiclass_log_loss(distribution, y):
    return -math.log(floor_distribution(distribution)[str(y)])


def _solve(matrix, vector):
    n = len(vector)
    rows = [list(matrix[i]) + [vector[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-300:
            raise ValueError("degenerate fitting evidence: singular system")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(n):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def _check_binary(rows, what):
    if not rows:
        raise ValueError(
            f"absent fitting evidence: no eligible clean dev rows for {what}"
        )
    labels = {bool(y) for *_, y in rows}
    if len(labels) < 2:
        raise ValueError(
            f"degenerate fitting evidence for {what}: only one class in clean dev rows"
        )
    if len({tuple(x) for _, x, _ in rows}) < 2:
        raise ValueError(f"degenerate fitting evidence for {what}: constant features")


def fit_logistic(rows, *, what="logistic", ridge=RIDGE, max_iter=100):
    """Newton fit of sigmoid(b + w.x) with Platt-smoothed targets and L2 on w.

    rows: (case_id, features, bool label). Platt targets (N+ + 1)/(N+ + 2) and
    1/(N- + 2) keep the optimum finite on separable data; the ridge keeps
    collinear features solvable. Deterministic: rows are sorted by case_id.
    """
    _check_binary(rows, what)
    rows = sorted(rows, key=lambda r: r[0])
    positives = sum(bool(y) for *_, y in rows)
    negatives = len(rows) - positives
    high, low = (positives + 1) / (positives + 2), 1 / (negatives + 2)
    data = [([1.0, *map(float, x)], high if y else low) for _, x, y in rows]
    k = len(data[0][0])
    n = len(data)

    def objective(w):
        total = 0.0
        for x, t in data:
            p = min(max(sigmoid(sum(a * b for a, b in zip(w, x))), 1e-300), 1 - 1e-16)
            total -= t * math.log(p) + (1 - t) * math.log(1 - p)
        return total / n + ridge / 2 * sum(v * v for v in w[1:])

    w = [0.0] * k
    current = objective(w)
    converged, iterations = False, 0
    for iterations in range(1, max_iter + 1):
        gradient = [ridge * w[j] if j else 0.0 for j in range(k)]
        hessian = [[ridge if i == j and i else 0.0 for j in range(k)] for i in range(k)]
        for x, t in data:
            p = sigmoid(sum(a * b for a, b in zip(w, x)))
            for i in range(k):
                gradient[i] += (p - t) * x[i] / n
                for j in range(k):
                    hessian[i][j] += p * (1 - p) * x[i] * x[j] / n
        step = _solve(hessian, gradient)
        scale = 1.0
        while True:
            candidate = [a - scale * b for a, b in zip(w, step)]
            value = objective(candidate)
            if value <= current or scale < 1e-8:
                break
            scale /= 2
        delta = current - value
        w, current = candidate, value
        if max(abs(g) for g in gradient) < 1e-10 or 0 <= delta < 1e-14:
            converged = True
            break
    return {
        "intercept": w[0],
        "coefficients": w[1:],
        "objective_value": current,
        "iterations": iterations,
        "converged": converged,
        "n": n,
        "positives": positives,
        "training_case_ids": sorted({r[0] for r in rows}),
        "data_sha256": score.digest([[r[0], list(r[1]), bool(r[2])] for r in rows]),
    }


def temper(distribution, temperature):
    floored = floor_distribution(distribution)
    logs = {k: math.log(v) / temperature for k, v in floored.items()}
    top = max(logs.values())
    exp = {k: math.exp(v - top) for k, v in logs.items()}
    total = math.fsum(exp.values())
    return {k: v / total for k, v in exp.items()}


def fit_temperature(rows, *, what="temperature"):
    """Golden-section search of log T minimizing mean NLL over floored distributions."""
    if not rows:
        raise ValueError(
            f"absent fitting evidence: no eligible clean dev rows for {what}"
        )
    rows = sorted(rows, key=lambda r: (r[0], r[1]))
    if len({str(r[3]) for r in rows}) < 2:
        raise ValueError(
            f"degenerate fitting evidence for {what}: only one class in clean dev rows"
        )
    if all(len(set(floor_distribution(r[2]).values())) == 1 for r in rows):
        raise ValueError(
            f"degenerate fitting evidence for {what}: no probability variation"
        )

    def objective(log_t):
        t = math.exp(log_t)
        return mean([-math.log(temper(d, t)[str(y)]) for _, _, d, y in rows])

    lo, hi = (math.log(v) for v in TEMPERATURE_BOUNDS)
    ratio = (math.sqrt(5) - 1) / 2
    a, b = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
    fa, fb = objective(a), objective(b)
    for _ in range(80):
        if fa <= fb:
            hi, b, fb = b, a, fa
            a = hi - ratio * (hi - lo)
            fa = objective(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + ratio * (hi - lo)
            fb = objective(b)
    log_t = (lo + hi) / 2
    return {
        "temperature": math.exp(log_t),
        "objective_value": objective(log_t),
        "identity_objective_value": objective(0.0),
        "bounds": list(TEMPERATURE_BOUNDS),
        # Separable dev evidence drives T to the lower bound; report, do not hide.
        "at_bound": any(abs(log_t - math.log(v)) < 1e-6 for v in TEMPERATURE_BOUNDS),
        "n": len(rows),
        "training_case_ids": sorted({r[0] for r in rows}),
        "data_sha256": score.digest([[r[0], r[1], r[2], str(r[3])] for r in rows]),
    }


def _dev_clean(cases):
    """Metadata filter first; then refuse any training case without human review."""
    dev = sorted(
        (c for c in cases if c["split"] == "dev" and c["arm"] == "clean"),
        key=lambda c: c["case_id"],
    )
    for c in dev:
        if c.get("label_source") != "human_review":
            raise ValueError(
                "fitting requires an imported reviewed label revision (#166); "
                f"{c['case_id']} has label_source {c.get('label_source')!r}"
            )
    return dev


def _remap_rows(cases, predictions):
    rows = {"noul": [], "choice": [], "score": []}
    for c in cases:
        answers = predictions.get(c["case_id"], {}).get("answers")
        if not answers:
            continue
        labels = c["labels"]
        for q, answer in sorted(answers.items()):
            if labels.get(q) is None:
                continue
            if answer["type"] == "noul":
                rows["noul"].append((c["case_id"], q, answer["noul"], labels[q]))
            elif answer["type"] == "choice" and labels[q] in (*FOUR, ABSTAIN):
                rows["choice"].append(
                    (c["case_id"], q, answer["probabilities"], labels[q])
                )
            elif answer["type"] == "score":
                rows["score"].append(
                    (c["case_id"], q, answer["probabilities"], labels[q])
                )
    return rows


def _present_types(predictions):
    return {a["type"] for p in predictions.values() for a in p["answers"].values()}


def fit_remap(cases, predictions):
    """Per question type: Platt map for Nouls, temperature for Choice and Score.

    Types are pooled across questions (the #196 wording "per question type").
    Absent evidence for a type that the run contains is an error.
    """
    dev = _dev_clean(cases)
    rows = _remap_rows(dev, predictions)
    present = _present_types(predictions)
    types = {}
    if "noul" in present:
        fitted = fit_logistic(
            [(f"{cid}#{q}", (logit(p),), y) for cid, q, p, y in rows["noul"]],
            what="noul Platt map",
        )
        fitted["training_case_ids"] = sorted({r[0] for r in rows["noul"]})
        types["noul"] = {
            "method": "platt: sigmoid(a * logit(floor(p)) + b)",
            "questions": sorted({r[1] for r in rows["noul"]}),
            **fitted,
        }
    for kind in ("choice", "score"):
        if kind in present:
            types[kind] = {
                "method": "temperature: softmax(log(floor(p)) / T)",
                "questions": sorted({r[1] for r in rows[kind]}),
                **fit_temperature(rows[kind], what=f"{kind} temperature"),
            }
    return {
        "artifact_type": "analysis_eval_probability_remap",
        "schema_version": 1,
        "fit_split": "dev",
        "fit_arm": "clean",
        "label_source": "human_review",
        "floor": EPS,
        "ridge": RIDGE,
        "objective": "mean cross-entropy on dev clean (Platt-smoothed targets plus L2 on slope for Nouls; plain NLL for temperature)",
        "types": types,
    }


def apply_remap(params, answers):
    result = {}
    for q, answer in answers.items():
        answer = dict(answer)
        fitted = params["types"].get(answer["type"])
        if answer["type"] == "noul" and fitted:
            answer["noul"] = sigmoid(
                fitted["intercept"] + fitted["coefficients"][0] * logit(answer["noul"])
            )
        elif answer["type"] in ("choice", "score") and fitted:
            answer["probabilities"] = temper(
                answer["probabilities"], fitted["temperature"]
            )
            if answer["type"] == "score":
                answer["score"] = math.fsum(
                    int(k) * v for k, v in answer["probabilities"].items()
                )
        result[q] = answer
    return result


def _assess(selected, predictions):
    rows = _remap_rows(selected, predictions)
    result = {}
    if rows["noul"]:
        result["noul"] = {
            "calibration": calibration(
                [p for *_, p, _ in rows["noul"]], [int(y) for *_, y in rows["noul"]]
            ),
            "log_loss": mean([binary_log_loss(p, y) for *_, p, y in rows["noul"]]),
        }
    for kind in ("choice", "score"):
        if rows[kind]:
            items = [(d, str(y)) for _, _, d, y in rows[kind]]
            result[kind] = {
                "n": len(items),
                "multiclass_brier": mean(
                    [
                        sum((p - int(k == y)) ** 2 for k, p in d.items())
                        for d, y in items
                    ]
                ),
                "log_loss": mean([multiclass_log_loss(d, y) for d, y in items]),
                "top_label_calibration": calibration(
                    [max(d.values()) for d, _ in items],
                    [int(max(sorted(d), key=d.get) == y) for d, y in items],
                ),
            }
    return result


def remap_report(cases, predictions, split):
    params = fit_remap(cases, predictions)
    selected = [c for c in cases if c["split"] == split]
    remapped = {
        key: {**value, "answers": apply_remap(params, value["answers"])}
        for key, value in predictions.items()
    }
    return params, {
        "split": split,
        "before": _assess(selected, predictions),
        "after": _assess(selected, remapped),
        "log_loss_floor": EPS,
    }


def fit_combiner(cases, predictions):
    """Per target, logistic over floored-logit reply/action Noul probabilities."""
    dev = _dev_clean(cases)
    targets = {}
    for target in (REPLY, ACTION):
        rows = [
            (
                c["case_id"],
                tuple(
                    logit(predictions[c["case_id"]]["answers"][q]["noul"])
                    for q in COMBINER_FEATURES
                ),
                c["labels"][target],
            )
            for c in dev
            if c["case_id"] in predictions
            and c["labels"][target] is not None
            and all(
                q in predictions[c["case_id"]]["answers"] for q in COMBINER_FEATURES
            )
        ]
        targets[target] = fit_logistic(rows, what=f"combiner {target}")
    return {
        "artifact_type": "analysis_eval_noul_combiner",
        "schema_version": 1,
        "fit_split": "dev",
        "fit_arm": "clean",
        "label_source": "human_review",
        "features": [f"logit(floor({q} noul))" for q in COMBINER_FEATURES],
        "floor": EPS,
        "ridge": RIDGE,
        "objective": "mean cross-entropy with Platt-smoothed targets plus L2 on coefficients",
        "insufficient_context": INSUFFICIENT_CONTEXT_POLICY,
        "targets": targets,
    }


def apply_combiner(params, answers):
    features = [logit(answers[q]["noul"]) for q in COMBINER_FEATURES]
    result = dict(answers)
    for target, fitted in params["targets"].items():
        p = sigmoid(
            fitted["intercept"]
            + sum(w * x for w, x in zip(fitted["coefficients"], features))
        )
        result[target] = {"type": "noul", "noul": p}
    return result


def disagreement(selected, first, second):
    """Coverage of first/second four-way disagreement and error where they agree."""
    available = [
        c for c in selected if c["case_id"] in first and c["case_id"] in second
    ]
    flagged, agreeing = [], []
    for c in available:
        a = first[c["case_id"]]["decisions"][EXPECTATION]
        b = second[c["case_id"]]["decisions"][EXPECTATION]
        if a != b or ABSTAIN in (a, b):
            flagged.append(c["case_id"])
        elif c["labels"][EXPECTATION] in FOUR:
            agreeing.append(a != c["labels"][EXPECTATION])
    return {
        "expected": len(selected),
        "available": len(available),
        "coverage": wilson(len(flagged), len(available)),
        "flagged_case_ids": flagged,
        "error_among_agreeing": wilson(sum(agreeing), len(agreeing)),
        "agreeing_excluded_unknown_truth": len(available)
        - len(flagged)
        - len(agreeing),
    }


def combined_report(cases, predictions, records, split, params):
    """Score Noul, Choice and combiner views of one combined run on `split`."""
    selected = [c for c in cases if c["split"] == split]
    ids = {c["case_id"] for c in selected}
    noul_view = variants.view(predictions, "triage-v1")
    combined = {
        key: {**value, "answers": apply_combiner(params, value["answers"])}
        for key, value in noul_view.items()
    }
    choice_of = {
        key: value["answers"][EXPECTATION]["choice"]
        for key, value in predictions.items()
    }

    def insufficient(key, normalized):
        if choice_of.get(key) != ABSTAIN:
            return normalized
        decisions = {**normalized["decisions"], EXPECTATION: ABSTAIN}
        decisions[REPLY] = decisions[ACTION] = None
        return {**normalized, "decisions": decisions, "abstain": True}

    report = {"thresholds": {}, "runs": {}}
    normalized = {}
    for name, profile, view, adjust in (
        ("noul", "triage-v1", noul_view, None),
        (
            "choice",
            "triage-choice-v1",
            variants.view(predictions, "triage-choice-v1"),
            None,
        ),
        ("combiner", "triage-v1", combined, insufficient),
    ):
        thresholds, normalized[name], summary = variants.score_view(
            cases, selected, view, profile, adjust=adjust
        )
        report["thresholds"][f"eval-combined-v1[{name}]"] = thresholds
        report["runs"][f"eval-combined-v1[{name}]"] = {
            **summary,
            "operations": score.operational(records, ids),
        }
    report["disagreement"] = {
        arm: {
            pair: disagreement(
                [c for c in selected if arm == "all" or c["arm"] == arm],
                normalized["choice"],
                normalized[other],
            )
            for pair, other in (
                ("choice_vs_noul", "noul"),
                ("choice_vs_combiner", "combiner"),
            )
        }
        for arm in ("clean", "all")
    }
    report["paired_profile_difference"] = score.compare(
        selected, normalized["noul"], normalized["choice"]
    )
    report["combiner_vs_choice_difference"] = score.compare(
        selected, normalized["combiner"], normalized["choice"]
    )
    report["usage"] = variants.usage_summary(records, ids)
    return report


def reviewed_cases(document, review, source_root, split):
    if review is None:
        raise ValueError(
            "fitting requires an explicit reviewed label revision (#166); model pre-labels are never used"
        )
    if "label_review" in document:
        raise ValueError("provide original pre-labels and explicit reviewed_labels")
    from .reviewed_labels import apply_review

    imported, provenance = apply_review(document, review, source_root, split=split)
    return imported, provenance


def _write(out, artifacts, lines):
    out.mkdir(parents=True, exist_ok=False)
    for name, value in artifacts.items():
        (out / name).write_text(
            json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
    (out / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    remap = commands.add_parser(
        "remap", help="fit #196 remapping on reviewed dev clean"
    )
    remap.add_argument("--run", required=True, metavar="IDENTITY=DIR")
    combine = commands.add_parser(
        "combine", help="fit #197 combiner on eval-combined-v1"
    )
    combine.add_argument("--run", required=True, type=Path)
    for command in (remap, combine):
        command.add_argument("--labels", type=Path, default=ROOT / "labels.json")
        command.add_argument("--reviewed-labels", type=Path, required=True)
        command.add_argument("--split", choices=("dev", "test"), default="test")
        command.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        document = score._read_json(args.labels)
        imported, provenance = reviewed_cases(
            document,
            score._read_json(args.reviewed_labels),
            args.labels.parent,
            args.split,
        )
        cases = imported["cases"]
        if args.command == "remap":
            identity, directory = args.run.split("=", 1)
            predictions, _, _ = variants.load_any(
                identity, Path(directory), cases, args.labels.parent
            )
            params, assessment = remap_report(cases, predictions, args.split)
            metrics = {
                "status": "evaluation-only probability remapping; agreement with the recorded review revision",
                "run_identity": identity,
                "label_provenance": provenance,
                "assessment": assessment,
            }
            _write(
                args.out,
                {"remap.json": params, "metrics.json": metrics},
                [
                    "# Probability remapping (evaluation only)",
                    "",
                    f"Run `{identity}`; review revision `{provenance['revision']}`. "
                    "Fitted on reviewed clean dev cases only; assessed on "
                    f"`{args.split}`. Parameters, objective and training IDs: `remap.json`. "
                    "Before/after calibration and floored log loss: `metrics.json`.",
                ],
            )
        else:
            variant = variants.get_variant("eval-combined-v1")
            predictions, _, records = variants.load_variant_run(
                args.run, variant, cases, args.labels.parent
            )
            params = fit_combiner(cases, predictions)
            metrics = {
                "status": "evaluation-only combiner; agreement with the recorded review revision",
                "evaluation_variant": variant.identity(),
                "label_provenance": provenance,
                **combined_report(cases, predictions, records, args.split, params),
            }
            _write(
                args.out,
                {"combiner.json": params, "metrics.json": metrics},
                [
                    "# Noul combiner (evaluation only)",
                    "",
                    f"Variant `{variant.name}`; review revision `{provenance['revision']}`. "
                    "Coefficients, objective, training IDs and insufficient-context policy: "
                    "`combiner.json`. Noul, Choice and combiner views, disagreement "
                    "coverage, error among agreeing cases and measured usage: `metrics.json`.",
                ],
            )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"out": str(args.out)}))


if __name__ == "__main__":
    main()
