"""Small-sample descriptive metrics; stdlib except optional lazy SmoothECE."""

from __future__ import annotations

import math
import random
from collections import defaultdict

SEED = 166
REPLICATES = 2000


def mean(values):
    return sum(values) / len(values) if values else None


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def wilson(events, total):
    if not total:
        return {"events": events, "n": total, "rate": None, "ci95": None}
    z = 1.959963984540054
    p = events / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = (
        z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    )
    return {
        "events": events,
        "n": total,
        "rate": p,
        "ci95": [max(0.0, center - half), min(1.0, center + half)],
    }


def cluster_bootstrap(rows, *, seed=SEED, replicates=REPLICATES):
    """Rows are (thread_root, paired value); resample roots, not observations."""
    groups = defaultdict(list)
    for group, value in rows:
        groups[group].append(value)
    keys = sorted(groups)
    result = {
        "n": len(rows),
        "clusters": len(keys),
        "estimate": mean([v for _, v in rows]),
        "ci95": None,
        "seed": seed,
        "replicates": replicates,
    }
    if len(keys) < 2:
        return {**result, "status": "not estimable: fewer than two clusters"}
    rng = random.Random(seed)
    values = []
    for _ in range(replicates):
        sample = [v for key in rng.choices(keys, k=len(keys)) for v in groups[key]]
        values.append(mean(sample))
    bounds = [percentile(values, 0.025), percentile(values, 0.975)]
    return {
        **result,
        "ci95": bounds,
        "status": "degenerate empirical interval"
        if bounds[0] == bounds[1]
        else "computed",
    }


def calibration(probabilities, outcomes, *, bins=10):
    buckets = [[] for _ in range(bins)]
    for p, y in zip(probabilities, outcomes, strict=True):
        buckets[min(int(p * bins), bins - 1)].append((p, y))
    rows = [
        {
            "lower": i / bins,
            "upper": (i + 1) / bins,
            "n": len(bucket),
            "mean_probability": mean([p for p, _ in bucket]),
            "observed_rate": mean([y for _, y in bucket]),
        }
        for i, bucket in enumerate(buckets)
    ]
    n = len(probabilities)
    ece = (
        sum(
            row["n"] * abs(row["mean_probability"] - row["observed_rate"])
            for row in rows
            if row["n"]
        )
        / n
        if n
        else None
    )
    smooth = {"status": "not computed", "reason": "no observations"}
    if n:
        try:
            import relplot
            import numpy as np
        except ImportError:
            smooth = {
                "status": "not computed",
                "reason": "optional relplot unavailable",
            }
        else:
            try:
                value = float(
                    relplot.smECE(np.asarray(probabilities), np.asarray(outcomes))
                )
                if not math.isfinite(value):
                    raise ValueError("nonfinite SmoothECE")
                smooth = {"status": "computed", "value": value}
            except (ValueError, TypeError, RuntimeError, AttributeError) as exc:
                smooth = {"status": "not computed", "reason": type(exc).__name__}
    return {
        "n": n,
        "brier": mean([(p - y) ** 2 for p, y in zip(probabilities, outcomes)]),
        "ece": ece,
        "bins": rows,
        "bin_policy": f"{bins} equal-width; last includes 1",
        "smooth_ece": smooth,
    }


def categorical(truths, predictions, classes):
    columns = [*classes, "abstain"]
    matrix = {str(label): {str(p): 0 for p in columns} for label in classes}
    for truth, prediction in zip(truths, predictions, strict=True):
        matrix[str(truth)][str(prediction) if prediction in classes else "abstain"] += 1
    errors = sum(t != p for t, p in zip(truths, predictions))
    return {
        "n": len(truths),
        "confusion": matrix,
        "error": wilson(errors, len(truths)),
        "per_label_error": {
            str(label): wilson(
                sum(t == label and p != t for t, p in zip(truths, predictions)),
                truths.count(label),
            )
            for label in classes
        },
        "abstentions": sum(p not in classes for p in predictions),
    }


def risk_coverage(rows):
    """Rows: (case_id, probability of decision, error, explicit_abstention).

    Right-endpoint discrete integrals over attainable coverage only. Abstained
    cases remain in the denominator and cannot be auto-accepted to fill a budget.
    """
    n = len(rows)
    accepted = sorted((r for r in rows if not r[3]), key=lambda r: (-r[1], r[0]))
    points, errors = [], 0
    for index, (_, _, error, _) in enumerate(accepted, 1):
        errors += error
        points.append(
            {
                "accepted": index,
                "coverage": index / n,
                "risk": errors / index,
                "generalized_risk": errors / n,
            }
        )
    budgets = {}
    for budget in (0.1, 0.2):
        count = min(math.floor(n * (1 - budget) + 1e-9), len(accepted))
        budgets[str(budget)] = {
            "requested_review_fraction": budget,
            "accepted": count,
            "actual_review_fraction": 1 - count / n if n else None,
            "budget_feasible": n > 0
            and n - len(accepted) <= math.floor(n * budget + 1e-9),
            "error": wilson(sum(r[2] for r in accepted[:count]), count),
        }
    return {
        "n": n,
        "explicit_abstentions": n - len(accepted),
        "points": points,
        "max_coverage": len(accepted) / n if n else None,
        "aurc": sum(p["risk"] for p in points) / n if points else None,
        "augrc": sum(p["generalized_risk"] for p in points) / n if points else None,
        "integration": "right-endpoint sum / n; attainable coverage only; compare max_coverage",
        "review_budgets": budgets,
    }
