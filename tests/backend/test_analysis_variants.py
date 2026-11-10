"""Evaluation-only variants and dev-only fitting stay offline and harness-local."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

with patch.object(sys, "path", [str(Path(__file__).resolve().parents[2]), *sys.path]):
    from benchmarks.analysis_eval import (
        fitting,
        reviewed_labels,
        score,
        stage,
        variants,
    )

from dead_letter.analysis.contracts import AnalysisError
from dead_letter.analysis.eml import prepare_eml
from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.providers.typesafe import TypeSafeConfig, _validate_request
from dead_letter.analysis.service import _result_envelope

ROOT = Path(__file__).resolve().parents[2] / "benchmarks/analysis_eval"
DOCUMENT = json.loads((ROOT / "labels.json").read_text())
BY_ID = {case["case_id"]: case for case in DOCUMENT["cases"]}
REPLY = "reply_request_present"
ACTION = "non_reply_action_request_present"
EXPECTATION = "response_expectation"
URGENCY = "expressed_urgency"
DEV_CLEAN = sorted(
    c["case_id"]
    for c in DOCUMENT["cases"]
    if c["split"] == "dev" and c["arm"] == "clean"
)
TEST_CLEAN = sorted(
    c["case_id"]
    for c in DOCUMENT["cases"]
    if c["split"] == "test" and c["arm"] == "clean"
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("variant evaluation must remain offline")

    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setitem(sys.modules, "relplot", None)


def _noise(case_id, salt):
    return int(hashlib.sha256(f"{case_id}:{salt}".encode()).hexdigest()[:4], 16) / 65535


def _canonical_answers(case_id, *, zero=False):
    """Deterministic two-decimal answers loosely tracking the pre-labels."""
    labels = BY_ID[case_id]["labels"]
    answers = {}
    for question, spec in variants.CANONICAL.items():
        kind = spec["type"]
        if kind == "noul":
            truth = labels[question]
            p = 0.5 if truth is None else (0.75 if truth else 0.25)
            p += (_noise(case_id, question) - 0.5) * 0.6
            answers[question] = {"type": "noul", "noul": round(min(max(p, 0), 1), 2)}
        elif kind == "score":
            level = str(labels[URGENCY] if labels[URGENCY] is not None else 1)
            probabilities = {k: (0.8 if k == level else 0.1) for k in ("0", "1", "2")}
            answers[question] = {
                "type": "score",
                "score": round(sum(int(k) * v for k, v in probabilities.items()), 2),
                "confidence": 0.5,
                "probabilities": probabilities,
                "legend": {str(i): t for i, t in enumerate(spec["criteria"])},
            }
        else:
            winner = labels[EXPECTATION]
            if _noise(case_id, "choice") < 0.2:
                winner = "none" if winner != "none" else "reply_only"
            high, low = (1.0, 0.0) if zero else (0.6, 0.1)
            answers[question] = {
                "type": "choice",
                "choice": winner,
                "confidence": 0.5,
                "probabilities": {
                    k: (high if k == winner else low) for k in spec["criteria"]
                },
            }
    return answers


def _write_variant_run(directory, variant, case_ids, *, answers=_canonical_answers):
    for case_id in case_ids:
        case = BY_ID[case_id]
        request = variants.prepare_variant(
            ROOT / case["file"],
            variant,
            focus_identity=tuple(case["focus_identity"]),
            model="jev-1.13.0",
        )
        canonical = answers(case_id)
        response = {
            "answers": variants.from_canonical(variant, canonical),
            "model": "jev-1.13.0",
            "usage": {"input_tokens": 1000 + len(variant.questions()) * 100},
            "request_id": f"req-{case_id}",
        }
        result = variants.execute(
            request,
            lambda payload, response=response: response,
            adapter_version="test-fake-transport-v1",
            sdk_version="0.7.5",
        )
        target = directory / f"{case_id}.analysis.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result))
    return directory


def _write_production_run(directory, profile, case_ids):
    for case_id in case_ids:
        case = BY_ID[case_id]
        prepared = prepare_eml(
            ROOT / case["file"],
            profile_name=profile,
            focus_identity=tuple(case["focus_identity"]),
            model="jev-1.13.0",
        )
        result = _result_envelope(prepared)
        keys = get_profile(profile).questions()
        result.update(
            execution_status="succeeded",
            returned_model="jev-1.13.0",
            sdk_version="0.7.5",
            usage={"input_tokens": 1000},
            answers={k: v for k, v in _canonical_answers(case_id).items() if k in keys},
        )
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{case_id}.analysis.json").write_text(json.dumps(result))
    return directory


def _reviewed(split="test"):
    artifact = reviewed_labels.create_template(DOCUMENT, ROOT, revision="variant-test")
    for entry in artifact["cases"]:
        entry["review"] = {
            "status": "reviewed",
            "reviewers": ["synthetic-test-reviewer"],
            "reviewed_at": "2026-11-10T12:00:00Z",
            "adjudicator": None,
            "note": "Fabricated provenance for an offline contract test.",
        }
    return artifact


def _predictions(case_ids, **kwargs):
    return {key: {"answers": _canonical_answers(key, **kwargs)} for key in case_ids}


# Registry identity and digests


def test_registry_identities_are_evaluation_only_and_pinned():
    assert set(variants.VARIANTS) == {
        "eval-reply-reworded-v1",
        "eval-choice-reordered-v1",
        "eval-combined-v1",
    }
    rebuilt = variants._registry()
    for name in variants.VARIANTS:
        variant = variants.get_variant(name)
        assert variant.digest == variants.PINNED_DIGESTS[name]
        assert rebuilt[name].digest == variant.digest
        assert variant.profile.sha256 not in {
            get_profile(p).sha256 for p in variants.PRODUCTION
        }
        with pytest.raises(ValueError, match="unknown_analysis_profile"):
            get_profile(name)
    with pytest.raises(ValueError, match="never production"):
        dataclasses.replace(variants.VARIANTS["eval-combined-v1"], name="triage-v1")


def _replace_spec(variant, change):
    spec = variant.spec()
    change(spec)
    return dataclasses.replace(variant, spec_json=json.dumps(spec))


def test_digest_detects_wording_order_mapping_and_production_drift(monkeypatch):
    reordered = variants.VARIANTS["eval-choice-reordered-v1"]

    def reword(spec):
        spec[0]["instructions"] += " "

    def swap(spec):
        q = next(q for q in spec if q["type"] == "choice")
        q["criteria"][0], q["criteria"][1] = q["criteria"][1], q["criteria"][0]

    def remap(spec):
        q = next(q for q in spec if q["type"] == "choice")
        q["options"]["none"], q["options"]["both"] = "both", "none"

    digests = {reordered.digest}
    for change in (reword, swap, remap):
        digests.add(_replace_spec(reordered, change).digest)
    assert len(digests) == 4
    # Option order is outside Profile.sha256 (sorted JSON) but inside the digest.
    assert _replace_spec(reordered, swap).profile.sha256 == reordered.profile.sha256
    monkeypatch.setitem(
        variants.VARIANTS, reordered.name, _replace_spec(reordered, reword)
    )
    with pytest.raises(ValueError, match="variant drift"):
        variants.get_variant(reordered.name)


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda s: s[0].update(canonical="unknown_question"), "canonical mapping"),
        (lambda s: s[0].update(type="choice"), "canonical mapping"),
        (lambda s: s.pop(), "does not cover"),
        (
            lambda s: next(q for q in s if q["type"] == "choice")["options"].update(
                none="both"
            ),
            "one-to-one",
        ),
    ],
)
def test_invalid_variant_mappings_are_rejected(change, match):
    with pytest.raises(ValueError, match=match):
        _replace_spec(variants.VARIANTS["eval-choice-reordered-v1"], change)


def test_reworded_variant_changes_only_reply_wording():
    production = get_profile("triage-v1").questions()
    questions = variants.get_variant("eval-reply-reworded-v1").questions()
    assert set(questions) == set(production)
    for key, spec in production.items():
        if key == REPLY:
            assert spec["instructions"] != questions[key]["instructions"]
        else:
            assert spec == questions[key]


def test_reordered_choice_wire_order_and_option_mapping_round_trip():
    variant = variants.get_variant("eval-choice-reordered-v1")
    production = get_profile("triage-choice-v1").questions()[EXPECTATION]["criteria"]
    order = list(variant.questions()[EXPECTATION]["criteria"])
    assert order == list(variants._REORDERED)
    assert order == list(reversed(list(production)))
    canonical = _canonical_answers("seed-reply-positive")
    keys = set(get_profile("triage-choice-v1").questions())
    canonical = {k: v for k, v in canonical.items() if k in keys}
    assert (
        variants.to_canonical(variant, variants.from_canonical(variant, canonical))
        == canonical
    )

    # A renamed-option variant proves answers are mapped, not passed through.
    def rename(spec):
        q = next(q for q in spec if q["type"] == "choice")
        letters = dict(zip(variants._REORDERED, "abcde"))
        q["criteria"] = [[letters[k], text] for k, text in q["criteria"]]
        q["options"] = {letters[k]: k for k in letters}

    renamed = _replace_spec(variant, rename)
    native = variants.from_canonical(renamed, canonical)
    assert set(native[EXPECTATION]["probabilities"]) == set("abcde")
    assert native[EXPECTATION]["choice"] == "a"  # reply_only is the first option
    back = variants.to_canonical(renamed, native)
    assert back == canonical and back[EXPECTATION]["choice"] == "reply_only"


def test_combined_arm_asks_nouls_and_choice_within_request_limits():
    variant = variants.get_variant("eval-combined-v1")
    union = set(get_profile("triage-v1").questions()) | set(
        get_profile("triage-choice-v1").questions()
    )
    assert set(variant.questions()) == union
    case = BY_ID["seed-reply-positive"]
    request = variants.prepare_variant(ROOT / case["file"], variant)
    production = prepare_eml(ROOT / case["file"])
    assert request.state_sha256 == production.request.state_sha256
    assert request.fingerprint != production.request.fingerprint
    preview = request.preview()
    assert "state" not in preview and preview["remote_enabled"] is False
    assert preview["limits"]["request_bytes"] <= preview["limits"]["max_request_bytes"]


# Run artifacts and loaders


def test_variant_runs_record_identity_and_production_loaders_reject_them(tmp_path):
    variant = variants.get_variant("eval-choice-reordered-v1")
    run = _write_variant_run(tmp_path / "variant", variant, DEV_CLEAN[:3])
    artifact = json.loads(next(run.glob("*.json")).read_text())
    assert artifact["profile"]["name"] == variant.name
    assert artifact["evaluation_variant"]["digest"] == variant.digest
    assert artifact["evaluation_variant"]["evaluation_only"] is True
    assert artifact["adapter_version"] == "test-fake-transport-v1"
    cases = DOCUMENT["cases"]
    loaded, diagnostics, _ = variants.load_variant_run(run, variant, cases, ROOT)
    assert sorted(loaded) == DEV_CLEAN[:3]
    first = loaded[DEV_CLEAN[0]]["answers"][EXPECTATION]
    assert list(first["probabilities"]) == sorted(first["probabilities"])

    for profile in variants.PRODUCTION:
        with pytest.raises(ValueError, match="profile mismatch"):
            score.load_run(run, profile, cases, ROOT)
    with pytest.raises(AnalysisError):
        _validate_request(
            variants.prepare_variant(
                ROOT / BY_ID[DEV_CLEAN[0]]["file"], variant
            ).validation_request(),
            TypeSafeConfig(),
        )
    with pytest.raises(ValueError, match="unknown_analysis_profile"):
        score.evaluate(DOCUMENT, {variant.name: run}, split="dev")

    production = _write_production_run(
        tmp_path / "production", "triage-choice-v1", DEV_CLEAN[:1]
    )
    with pytest.raises(ValueError, match="production profile triage-choice-v1"):
        variants.load_variant_run(production, variant, cases, ROOT)
    other = variants.get_variant("eval-combined-v1")
    with pytest.raises(ValueError, match="profile mismatch"):
        variants.load_variant_run(run, other, cases, ROOT)
    path = next(run.glob("*.json"))
    artifact["evaluation_variant"]["digest"] = "0" * 64
    path.write_text(json.dumps(artifact))
    with pytest.raises(ValueError, match="digest mismatch"):
        variants.load_variant_run(run, variant, cases, ROOT)


def test_tampered_answers_and_failed_transport_are_not_successes(tmp_path):
    variant = variants.get_variant("eval-reply-reworded-v1")
    run = _write_variant_run(tmp_path / "run", variant, DEV_CLEAN[:2])
    path = run / f"{DEV_CLEAN[0]}.analysis.json"
    artifact = json.loads(path.read_text())
    del artifact["answers"][REPLY]
    path.write_text(json.dumps(artifact))
    case = BY_ID[DEV_CLEAN[1]]
    request = variants.prepare_variant(ROOT / case["file"], variant)

    def broken(payload):
        raise RuntimeError("synthetic transport failure")

    failed = variants.execute(request, broken, adapter_version="test-fake-transport-v1")
    assert failed["execution_status"] == "failed"
    assert failed["error_code"] == "variant_transport_failed"
    (run / f"{DEV_CLEAN[1]}.analysis.json").write_text(json.dumps(failed))
    loaded, diagnostics, _ = variants.load_variant_run(
        run, variant, DOCUMENT["cases"], ROOT
    )
    assert loaded == {}
    assert diagnostics["item_status"][DEV_CLEAN[0]] == "malformed"
    assert diagnostics["item_status"][DEV_CLEAN[1]] == "failed"


def test_run_staged_writes_identity_groups_without_clobbering(tmp_path):
    variant = variants.get_variant("eval-reply-reworded-v1")
    staged = tmp_path / "stage"
    stage.stage(DOCUMENT, ROOT, staged, split="dev", arms=("clean",), limit=2)
    answers = variants.from_canonical(variant, _canonical_answers(DEV_CLEAN[0]))

    def call(payload):
        assert list(payload["questions"]) == list(variant.questions())
        return {"answers": answers, "model": "jev-1.13.0", "usage": None}

    written = variants.run_staged(
        staged, variant, tmp_path / "out", call, adapter_version="test-fake-v1"
    )
    assert len(written) == 2 and all("identity-" in path for path in written)
    loaded, _, _ = variants.load_variant_run(
        tmp_path / "out", variant, DOCUMENT["cases"], ROOT
    )
    assert sorted(loaded) == DEV_CLEAN[:2]
    with pytest.raises(FileExistsError):
        variants.run_staged(
            staged, variant, tmp_path / "out", call, adapter_version="test-fake-v1"
        )


def test_compare_variant_against_production_flips_and_measured_usage(tmp_path):
    variant = variants.get_variant("eval-choice-reordered-v1")
    ids = DEV_CLEAN[:4]
    base = _write_production_run(tmp_path / "base", "triage-choice-v1", ids)

    def shifted(case_id):
        answers = _canonical_answers(case_id)
        if case_id == ids[0]:
            choice = answers[EXPECTATION]
            other = "both" if choice["choice"] != "both" else "none"
            choice["probabilities"] = {
                k: (0.6 if k == other else 0.1) for k in choice["probabilities"]
            }
            choice["choice"] = other
        return answers

    run = _write_variant_run(tmp_path / "variant", variant, ids, answers=shifted)
    report = variants.compare_runs(
        DOCUMENT,
        {"base": ("triage-choice-v1", base), "reordered": (variant.name, run)},
        split="dev",
        arms=("clean",),
        limit=4,
    )
    comparison = report["comparisons"]["reordered"]
    assert comparison["cross_identity"] is True
    assert comparison["matched"] == 4
    assert comparison["questions"][EXPECTATION]["flipped_case_ids"] == [ids[0]]
    assert comparison["questions"][EXPECTATION]["probability_drift"][
        "max"
    ] == pytest.approx(0.5)
    assert comparison["questions"][URGENCY]["label_flips"] == 0
    assert comparison["paired_usage_delta"]["input_tokens"]["mean_delta"] == 400
    assert comparison["paired_usage_delta"]["output_tokens"]["n"] == 0
    assert report["runs"]["reordered"]["adapter_versions"] == {
        "test-fake-transport-v1": 4
    }
    variants.write_comparison(report, tmp_path / "report")
    assert "eval-choice-reordered-v1" in (tmp_path / "report/report.md").read_text()
    with pytest.raises(ValueError, match="outside input run trees"):
        variants.write_comparison(report, run / "nested")


def test_variant_scoring_reuses_scorer_and_reports_usage(tmp_path):
    variant = variants.get_variant("eval-combined-v1")
    run = _write_variant_run(tmp_path / "run", variant, DEV_CLEAN)
    report = variants.evaluate_variant(DOCUMENT, variant, run, split="dev")
    assert set(report["runs"]) == {
        "eval-combined-v1[triage-v1]",
        "eval-combined-v1[triage-choice-v1]",
    }
    assert "paired_profile_difference" in report
    assert report["usage"]["input_tokens"]["n"] == len(DEV_CLEAN)
    assert report["usage"]["output_tokens"]["unknown_records"] == len(DEV_CLEAN)
    assert "model pre-labels" in report["status"]
    variants.write_variant_report(report, tmp_path / "report")
    text = (tmp_path / "report/report.md").read_text()
    assert variant.digest in text and "input_tokens" in text


# Fitting


def test_fitting_refuses_model_prelabels_and_absent_review():
    predictions = _predictions(DEV_CLEAN)
    with pytest.raises(ValueError, match="reviewed label revision"):
        fitting.fit_remap(DOCUMENT["cases"], predictions)
    with pytest.raises(ValueError, match="reviewed label revision"):
        fitting.fit_combiner(DOCUMENT["cases"], predictions)
    with pytest.raises(ValueError, match="reviewed label revision"):
        fitting.reviewed_cases(DOCUMENT, None, ROOT, "test")


def test_fit_uses_dev_clean_only_and_test_labels_cannot_change_parameters():
    review = _reviewed()
    imported, _ = fitting.reviewed_cases(DOCUMENT, review, ROOT, "test")
    ids = [c["case_id"] for c in DOCUMENT["cases"]]
    predictions = _predictions(ids)
    remap = json.dumps(
        fitting.fit_remap(imported["cases"], predictions), sort_keys=True
    )
    combiner = json.dumps(
        fitting.fit_combiner(imported["cases"], predictions), sort_keys=True
    )
    params = json.loads(combiner)
    assert set(params["targets"][REPLY]["training_case_ids"]) <= set(DEV_CLEAN)
    assert json.loads(remap)["types"]["noul"]["training_case_ids"] == DEV_CLEAN

    mutated = copy.deepcopy(review)
    for entry in mutated["cases"]:
        if (
            BY_ID[entry["case_id"]]["split"] == "test"
            or BY_ID[entry["case_id"]]["arm"] != "clean"
        ):
            entry["labels"].update(
                {
                    REPLY: True,
                    ACTION: True,
                    "sender_commitment_present": True,
                    "action_deadline_present": True,
                    URGENCY: 2,
                    EXPECTATION: "both",
                }
            )
            entry["ambiguity_notes"] = {}
    changed, _ = fitting.reviewed_cases(DOCUMENT, mutated, ROOT, "test")
    assert (
        json.dumps(fitting.fit_remap(changed["cases"], predictions), sort_keys=True)
        == remap
    )
    assert (
        json.dumps(fitting.fit_combiner(changed["cases"], predictions), sort_keys=True)
        == combiner
    )
    _, before = fitting.remap_report(imported["cases"], predictions, "test")
    _, after = fitting.remap_report(changed["cases"], predictions, "test")
    assert before["before"] != after["before"]  # test labels still drive assessment

    # Sensitivity check: a dev clean label change does move the fit.
    dev_change = copy.deepcopy(review)
    entry = next(e for e in dev_change["cases"] if e["case_id"] == DEV_CLEAN[0])
    entry["labels"].update({REPLY: not entry["labels"][REPLY]})
    entry["labels"][EXPECTATION] = next(
        k
        for k, bits in score.BITS.items()
        if bits == (entry["labels"][REPLY], entry["labels"][ACTION])
    )
    moved, _ = fitting.reviewed_cases(DOCUMENT, dev_change, ROOT, "test")
    assert (
        json.dumps(fitting.fit_combiner(moved["cases"], predictions), sort_keys=True)
        != combiner
    )


def test_degenerate_and_absent_evidence_is_rejected():
    rows = [(f"c{i}", (float(i),), True) for i in range(5)]
    with pytest.raises(ValueError, match="only one class"):
        fitting.fit_logistic(rows)
    with pytest.raises(ValueError, match="absent fitting evidence"):
        fitting.fit_logistic([])
    with pytest.raises(ValueError, match="constant features"):
        fitting.fit_logistic([("a", (1.0,), True), ("b", (1.0,), False)])
    with pytest.raises(ValueError, match="only one class"):
        fitting.fit_temperature([("a", "q", {"x": 0.9, "y": 0.1}, "x")])
    with pytest.raises(ValueError, match="no probability variation"):
        fitting.fit_temperature(
            [
                ("a", "q", {"x": 0.5, "y": 0.5}, "x"),
                ("b", "q", {"x": 0.5, "y": 0.5}, "y"),
            ]
        )
    imported, _ = fitting.reviewed_cases(DOCUMENT, _reviewed(), ROOT, "test")
    with pytest.raises(ValueError, match="absent fitting evidence"):
        fitting.fit_combiner(imported["cases"], _predictions(TEST_CLEAN))


def test_separable_evidence_has_finite_platt_parameters():
    rows = [(f"n{i}", (-2.0 - i,), False) for i in range(5)] + [
        (f"p{i}", (2.0 + i,), True) for i in range(5)
    ]
    fitted = fitting.fit_logistic(rows)
    assert fitted["converged"]
    assert all(math.isfinite(v) for v in (fitted["intercept"], *fitted["coefficients"]))
    separable = [
        ("a", "q", {"x": 0.9, "y": 0.1}, "x"),
        ("b", "q", {"x": 0.2, "y": 0.8}, "y"),
    ]
    tempered = fitting.fit_temperature(separable)
    assert tempered["at_bound"] is True
    assert tempered["temperature"] == pytest.approx(fitting.TEMPERATURE_BOUNDS[0])


def test_exact_zero_probabilities_are_floored_before_log_loss():
    assert fitting.binary_log_loss(0.0, True) == pytest.approx(-math.log(fitting.EPS))
    assert fitting.binary_log_loss(1.0, False) == pytest.approx(-math.log(fitting.EPS))
    assert math.isfinite(fitting.logit(0.0)) and math.isfinite(fitting.logit(1.0))
    loss = fitting.multiclass_log_loss({"a": 1.0, "b": 0.0}, "b")
    assert math.isfinite(loss) and loss > 10
    imported, _ = fitting.reviewed_cases(DOCUMENT, _reviewed(), ROOT, "test")
    ids = [c["case_id"] for c in DOCUMENT["cases"]]
    params, assessment = fitting.remap_report(
        imported["cases"], _predictions(ids, zero=True), "test"
    )
    assert math.isfinite(params["types"]["choice"]["objective_value"])
    for phase in ("before", "after"):
        assert math.isfinite(assessment[phase]["choice"]["log_loss"])


def test_combined_report_insufficient_context_and_disagreement(tmp_path):
    variant = variants.get_variant("eval-combined-v1")
    ids = DEV_CLEAN + TEST_CLEAN[:10]
    abstained = TEST_CLEAN[0]

    def answers(case_id):
        result = _canonical_answers(case_id)
        if case_id == abstained:
            choice = result[EXPECTATION]
            choice["choice"] = "insufficient_context"
            choice["probabilities"] = {
                k: (0.6 if k == "insufficient_context" else 0.1)
                for k in choice["probabilities"]
            }
        return result

    run = _write_variant_run(tmp_path / "run", variant, ids, answers=answers)
    imported, provenance = fitting.reviewed_cases(DOCUMENT, _reviewed(), ROOT, "test")
    cases = imported["cases"]
    predictions, _, records = variants.load_variant_run(run, variant, cases, ROOT)
    params = fitting.fit_combiner(cases, predictions)
    assert params["insufficient_context"] == fitting.INSUFFICIENT_CONTEXT_POLICY
    report = fitting.combined_report(cases, predictions, records, "test", params)
    assert set(report["runs"]) == {
        "eval-combined-v1[noul]",
        "eval-combined-v1[choice]",
        "eval-combined-v1[combiner]",
    }
    clean = report["disagreement"]["clean"]["choice_vs_combiner"]
    assert clean["available"] == 10 and clean["expected"] == len(TEST_CLEAN)
    assert abstained in clean["flagged_case_ids"]
    assert clean["coverage"]["n"] == 10
    assert clean["error_among_agreeing"]["n"] + len(clean["flagged_case_ids"]) <= 10
    combiner_run = report["runs"]["eval-combined-v1[combiner]"]
    assert combiner_run["headline_clean"][EXPECTATION]["abstentions"] >= 1
    assert report["usage"]["input_tokens"]["n"] == 10


def test_fitting_cli_requires_review_and_writes_artifacts(
    tmp_path, monkeypatch, capsys
):
    variant = variants.get_variant("eval-combined-v1")
    run = _write_variant_run(tmp_path / "run", variant, DEV_CLEAN + TEST_CLEAN[:5])
    review = tmp_path / "review.json"
    review.write_text(json.dumps(_reviewed()))
    monkeypatch.setattr(
        sys,
        "argv",
        ["fitting", "combine", "--run", str(run), "--out", str(tmp_path / "missing")],
    )
    with pytest.raises(SystemExit):
        fitting.main()
    assert "--reviewed-labels" in capsys.readouterr().err
    for command, run_arg, artifact in (
        ("combine", str(run), "combiner.json"),
        ("remap", f"{variant.name}={run}", "remap.json"),
    ):
        out = tmp_path / command
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "fitting",
                command,
                "--run",
                run_arg,
                "--reviewed-labels",
                str(review),
                "--out",
                str(out),
            ],
        )
        fitting.main()
        params = json.loads((out / artifact).read_text())
        assert params["fit_split"] == "dev" and params["label_source"] == "human_review"
        assert (out / "metrics.json").is_file() and (out / "report.md").is_file()


def test_compare_questions_are_per_pair_not_shared_across_all_runs(tmp_path):
    combined = variants.get_variant("eval-combined-v1")
    ids = DEV_CLEAN[:3]
    runs = {
        "combined": (combined.name, _write_variant_run(tmp_path / "c", combined, ids)),
        "nouls": ("triage-v1", _write_production_run(tmp_path / "n", "triage-v1", ids)),
        "choice": (
            "triage-choice-v1",
            _write_production_run(tmp_path / "ch", "triage-choice-v1", ids),
        ),
    }
    together = variants.compare_runs(
        DOCUMENT, runs, split="dev", arms=("clean",), limit=3
    )
    for target, profile in (("nouls", "triage-v1"), ("choice", "triage-choice-v1")):
        alone = variants.compare_runs(
            DOCUMENT,
            {"combined": runs["combined"], target: runs[target]},
            split="dev",
            arms=("clean",),
            limit=3,
        )
        questions = together["comparisons"][target]["questions"]
        assert set(questions) == set(get_profile(profile).questions())
        assert questions == alone["comparisons"][target]["questions"]
    assert REPLY in together["comparisons"]["nouls"]["questions"]
    assert EXPECTATION in together["comparisons"]["choice"]["questions"]


def _empty_and_failed_runs(tmp_path, variant):
    empty = tmp_path / "empty"
    empty.mkdir()
    failed = tmp_path / "failed"
    failed.mkdir()
    for case_id in DEV_CLEAN[:2]:
        case = BY_ID[case_id]
        request = variants.prepare_variant(ROOT / case["file"], variant)

        def broken(payload):
            raise RuntimeError("synthetic transport failure")

        result = variants.execute(request, broken, adapter_version="test-fake-v1")
        (failed / f"{case_id}.analysis.json").write_text(json.dumps(result))
    return {"empty": empty, "failed": failed}


@pytest.mark.parametrize("kind", ["empty", "failed"])
def test_remap_rejects_runs_without_valid_predictions(tmp_path, monkeypatch, kind):
    variant = variants.get_variant("eval-combined-v1")
    run = _empty_and_failed_runs(tmp_path, variant)[kind]
    imported, _ = fitting.reviewed_cases(DOCUMENT, _reviewed(), ROOT, "test")
    predictions, _, _ = variants.load_variant_run(run, variant, imported["cases"], ROOT)
    assert predictions == {}
    with pytest.raises(ValueError, match="absent fitting evidence"):
        fitting.fit_remap(imported["cases"], predictions)
    with pytest.raises(ValueError, match="absent fitting evidence"):
        fitting.fit_combiner(imported["cases"], predictions)
    review = tmp_path / "review.json"
    review.write_text(json.dumps(_reviewed()))
    for command, run_arg in (("remap", f"{variant.name}={run}"), ("combine", str(run))):
        out = tmp_path / f"out-{command}"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "fitting",
                command,
                "--run",
                run_arg,
                "--reviewed-labels",
                str(review),
                "--out",
                str(out),
            ],
        )
        with pytest.raises(SystemExit) as exit_info:
            fitting.main()
        assert exit_info.value.code != 0
        assert not out.exists()
