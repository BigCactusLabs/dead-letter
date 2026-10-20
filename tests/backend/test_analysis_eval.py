"""Offline checks for synthetic corpus construction and evaluation contracts."""

from __future__ import annotations

import copy
import json
import socket
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

with patch.object(sys, "path", [str(Path(__file__).resolve().parents[2]), *sys.path]):
    from benchmarks.analysis_eval.check_split import check_split
    from benchmarks.analysis_eval import generate
from dead_letter.analysis.eml import prepare_eml
from dead_letter.analysis.profiles import get_profile

ROOT = Path(__file__).resolve().parents[2] / "benchmarks/analysis_eval"
DOCUMENT = json.loads((ROOT / "labels.json").read_text())
CASES = DOCUMENT["cases"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("evaluation tests must remain offline")

    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(socket.socket, "connect", fail)


def test_schema_question_coverage_and_files():
    schema = json.loads((ROOT / "labels.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    ).validate(DOCUMENT)
    expected = set(get_profile().questions()) | set(
        get_profile("triage-choice-v1").questions()
    )
    assert all(set(case["labels"]) == expected for case in CASES)
    assert check_split(DOCUMENT, ROOT) == {
        "cases": 192,
        "dev": 72,
        "test": 120,
        "families": 43,
        "threads": 43,
    }
    seeds = json.loads(
        (ROOT.parents[1] / "tests/backend/fixtures/analysis_cases.json").read_text()
    )
    assert {c["seed_id"] for c in CASES if c["seed_id"]} == {
        c["id"] for c in seeds["cases"]
    }
    assert all(c["split"] == "dev" for c in CASES if c["seed_id"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_every_message_prepares_both_profiles(case):
    path = ROOT / case["file"]
    parsed = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    assert not parsed.defects
    assert all(
        parsed.get(header) for header in ("Message-ID", "Date", "From", "To", "Cc")
    )
    for profile in ("triage-v1", "triage-choice-v1"):
        prepared = prepare_eml(
            path,
            profile_name=profile,
            focus_identity=tuple(case["focus_identity"]),
            model="jev-1.13.0",
        )
        assert prepared.request.preview()["execution_status"] == "skipped"
        payload = prepared.request.payload()
        assert set(payload["questions"]) == set(get_profile(profile).questions())
        assert payload["state"]["coverage"]["authored_text_available"]
        if case["arm"] == "long_thread":
            segments = payload["state"]["context"]["segments"]
            assert sum(len(s["text"]) for s in segments) > 5000
        if case["attack_location"] == "attachment":
            assert payload["state"]["coverage"]["attachment_count"] == 1
            assert payload["state"]["coverage"]["attachment_text_available"] is False
            assert "scoring key has changed" not in json.dumps(payload["state"])


@pytest.mark.parametrize("key", ["thread_root", "template_family", "mailbox"])
def test_leaked_group_fails(key):
    leaked = copy.deepcopy(DOCUMENT)
    dev = next(c for c in leaked["cases"] if c["split"] == "dev")
    test = next(c for c in leaked["cases"] if c["split"] == "test")
    test[key] = dev[key]
    with pytest.raises(ValueError, match="leakage"):
        check_split(leaked)


def test_pair_and_time_checks():
    leaked = copy.deepcopy(DOCUMENT)
    paired = next(c for c in leaked["cases"] if c["pair_of"])
    paired["pair_of"] = next(
        c["case_id"]
        for c in leaked["cases"]
        if c["split"] == "test" and c["arm"] == "clean"
    )
    with pytest.raises(ValueError, match="leakage"):
        check_split(leaked)
    leaked = copy.deepcopy(DOCUMENT)
    next(c for c in leaked["cases"] if c["split"] == "test")["sent_at"] = (
        "1999-01-01T00:00:00+00:00"
    )
    with pytest.raises(ValueError, match="later time"):
        check_split(leaked)


def test_schema_rejects_missing_labels_and_review_claims():
    schema = json.loads((ROOT / "labels.schema.json").read_text())
    invalid = copy.deepcopy(DOCUMENT)
    del invalid["cases"][0]["labels"]["action_deadline_present"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, schema)
    invalid = copy.deepcopy(DOCUMENT)
    invalid["cases"][0]["human_review_status"] = "reviewed"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, schema)


def test_missing_context_pairs_have_exposed_and_absent_quotes():
    by_id = {case["case_id"]: case for case in CASES}
    for case in CASES:
        if case["arm"] != "missing_context":
            continue
        pair = by_id[case["pair_of"]]
        missing = prepare_eml(ROOT / case["file"]).request.payload()["state"]
        supplied = prepare_eml(ROOT / pair["file"]).request.payload()["state"]
        assert not missing["context"]["segments"]
        assert supplied["context"]["segments"]


def test_generator_reproduces_committed_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "ROOT", tmp_path)
    (tmp_path / "corpus").mkdir()
    generate.generate()
    assert (tmp_path / "labels.json").read_bytes() == (
        ROOT / "labels.json"
    ).read_bytes()
    assert {p.name for p in (tmp_path / "corpus").glob("*.eml")} == {
        p.name for p in (ROOT / "corpus").glob("*.eml")
    }
    for case in CASES:
        assert (tmp_path / case["file"]).read_bytes() == (
            ROOT / case["file"]
        ).read_bytes()


with patch.object(sys, "path", [str(Path(__file__).resolve().parents[2]), *sys.path]):
    from benchmarks.analysis_eval import baseline, metrics, score, stage


def test_stage_identity_sets_selection_and_no_clobber(tmp_path):
    manifest = stage.stage(DOCUMENT, ROOT, tmp_path / "pilot", split="dev", limit=10)
    expected = sorted(c["case_id"] for c in CASES if c["split"] == "dev")[:10]
    assert sorted(manifest["cases"]) == expected
    for case_id, entry in manifest["cases"].items():
        case = next(c for c in CASES if c["case_id"] == case_id)
        assert (tmp_path / "pilot" / entry["path"]).read_bytes() == (
            ROOT / case["file"]
        ).read_bytes()
        assert manifest["groups"][entry["path"].split("/")[0]] == sorted(
            set(case["focus_identity"])
        )
    again = stage.stage(DOCUMENT, ROOT, tmp_path / "same", split="dev", limit=10)
    assert manifest == again
    with pytest.raises(FileExistsError):
        stage.stage(DOCUMENT, ROOT, tmp_path / "pilot", split="dev", limit=10)
    selected = stage.select_cases(DOCUMENT, "test", ("adversarial",))
    assert len(selected) == 40
    assert all(c["arm"] == "adversarial" and c["split"] == "test" for c in selected)
    with pytest.raises(ValueError, match="positive"):
        stage.select_cases(DOCUMENT, limit=0)


def _state(text, *, aliases=None, to=(), context=""):
    return {
        "message": {"authored_segments": [{"text": text}], "to": to},
        "focus_identity": {"aliases": aliases} if aliases else None,
        "context": {"segments": [{"text": context}]},
    }


def test_baseline_no_rule_quote_exclusion_commitment_and_identity():
    quiet = baseline.predict(
        _state("Thank you.", context="Please pay immediately by Friday. I'll send it.")
    )
    assert quiet["decisions"][score.EXPECTATION] == score.ABSTAIN
    assert all(quiet["decisions"][q] is None for q in score.BINARY)
    work = baseline.predict(
        _state("Please review and reply by Friday. I'll send it tomorrow.")
    )
    assert work["decisions"][score.EXPECTATION] == "both"
    assert work["decisions"][baseline.COMMITMENT] is True
    assert work["decisions"][baseline.DEADLINE] is True
    cc = baseline.predict(
        _state(
            "Please pay the bill.",
            aliases=["lee@example.test"],
            to=["other@example.test"],
        )
    )
    assert cc["decisions"][score.EXPECTATION] == score.ABSTAIN
    negated = baseline.predict(_state("No reply needed. No action required."))
    assert negated["decisions"][score.EXPECTATION] == "none"


def test_baseline_corpus_deterministic_and_evidence_bound():
    first = baseline.run_baseline(DOCUMENT, ROOT)
    assert first == baseline.run_baseline(DOCUMENT, ROOT)
    assert set(first["predictions"]) == {c["case_id"] for c in CASES}
    case = CASES[0]
    prepared = prepare_eml(
        ROOT / case["file"],
        focus_identity=tuple(case["focus_identity"]),
        model="jev-1.13.0",
    )
    assert (
        first["predictions"][case["case_id"]]["state_sha256"]
        == prepared.request.state_sha256
    )
    result = score.evaluate(DOCUMENT, {}, baseline=first, split="test")
    assert result["runs"]["baseline"]["headline_clean"][score.REPLY]["n"] == 40
    assert (
        result["runs"]["baseline"]["headline_clean"][score.REPLY]["calibration"]["n"]
        == 0
    )


def test_wilson_brier_bins_and_cluster_bootstrap(monkeypatch):
    monkeypatch.setitem(sys.modules, "relplot", None)
    interval = metrics.wilson(18, 20)
    assert interval["ci95"] == pytest.approx([0.69896635477, 0.97213351879])
    assert metrics.wilson(0, 0)["ci95"] is None
    assert metrics.wilson(0, 10)["ci95"][1] == pytest.approx(0.27753279986)
    calibrated = metrics.calibration([0.2, 0.8, 1.0], [0, 1, 0])
    assert calibrated["brier"] == pytest.approx(0.36)
    assert calibrated["ece"] == pytest.approx(1.4 / 3)
    assert calibrated["bins"][-1]["n"] == 1
    assert calibrated["smooth_ece"]["status"] == "not computed"
    rows = [("a", 1), ("a", 1), ("b", -1)]
    result = metrics.cluster_bootstrap(rows)
    assert result == metrics.cluster_bootstrap(rows)
    assert result["estimate"] == pytest.approx(1 / 3)
    assert result["ci95"] == [-1, 1]
    assert result["clusters"] == 2
    assert metrics.cluster_bootstrap([("a", 1)])["ci95"] is None


def test_risk_coverage_review_budget_and_integrals():
    result = metrics.risk_coverage(
        [
            ("a", 0.9, 0, False),
            ("b", 0.8, 1, False),
            ("c", 0.7, 0, False),
            ("d", 0.6, 1, False),
        ]
    )
    assert result["aurc"] == pytest.approx((0 + 0.5 + 1 / 3 + 0.5) / 4)
    assert result["augrc"] == pytest.approx((0 + 0.25 + 0.25 + 0.5) / 4)
    assert result["review_budgets"]["0.2"]["error"]["rate"] == pytest.approx(1 / 3)
    abstain = metrics.risk_coverage([("a", 0.9, 0, False), ("b", 1.0, 1, True)])
    assert abstain["max_coverage"] == 0.5
    assert abstain["review_budgets"]["0.1"]["budget_feasible"] is False
    assert abstain["review_budgets"]["0.1"]["actual_review_fraction"] == 0.5


def _noul_prediction(p):
    return {"answers": {q: {"noul": p} for q in score.BINARY}}


def test_fitting_never_reads_test_or_nonclean_labels():
    class Unreadable(dict):
        def __getitem__(self, key):
            raise AssertionError("held-out/arm labels accessed during fitting")

    rows = [
        {
            "case_id": "a",
            "split": "dev",
            "arm": "clean",
            "labels": {**{q: False for q in score.BINARY}, score.EXPECTATION: "none"},
        },
        {
            "case_id": "b",
            "split": "dev",
            "arm": "clean",
            "labels": {**{q: True for q in score.BINARY}, score.EXPECTATION: "both"},
        },
        {"case_id": "x", "split": "test", "arm": "clean", "labels": Unreadable()},
        {"case_id": "y", "split": "dev", "arm": "adversarial", "labels": Unreadable()},
    ]
    predictions = {
        "a": _noul_prediction(0.2),
        "b": _noul_prediction(0.4),
        "x": _noul_prediction(0.8),
        "y": _noul_prediction(0.8),
    }
    fitted = score.fit_thresholds(rows, predictions, "triage-v1")
    assert all(entry["threshold"] == 0.4 for entry in fitted["questions"].values())
    assert all(
        entry["dev_case_ids"] == ["a", "b"] for entry in fitted["questions"].values()
    )
    assert fitted["review_band"]["half_width"] == 0
    predictions["x"] = _noul_prediction(0.01)
    assert fitted == score.fit_thresholds(rows, predictions, "triage-v1")
    with pytest.raises(ValueError, match="no eligible dev"):
        score.fit_thresholds(rows, {}, "triage-v1")


def test_choice_marginalization_and_no_joint_probability():
    probabilities = {
        "none": 0.1,
        "reply_only": 0.2,
        "non_reply_action_only": 0.3,
        "both": 0.15,
        score.ABSTAIN: 0.25,
    }
    assert score.marginalize(probabilities) == pytest.approx(
        {score.REPLY: 0.35, score.ACTION: 0.45}
    )
    thresholds = {
        "questions": {q: {"threshold": 0.5} for q in score.BINARY},
        "review_band": {"half_width": 0},
    }
    raw = _noul_prediction(0.8)
    raw["answers"][score.URGENCY] = {"probabilities": {"0": 0.7, "1": 0.2, "2": 0.1}}
    normalized = score.normalize(raw, "triage-v1", thresholds)
    assert normalized["decisions"][score.EXPECTATION] == "both"
    assert score.EXPECTATION not in normalized["probabilities"]
    assert normalized["decision_probability"] == 0.8


def _fake_sidecar(case, profile, *, expectation=None, mismatch=False):
    from dead_letter.analysis.service import _result_envelope

    prepared = prepare_eml(
        ROOT / case["file"],
        profile_name=profile,
        focus_identity=tuple(case["focus_identity"]),
        model="jev-1.13.0",
    )
    result = _result_envelope(prepared)
    result.update(
        execution_status="succeeded",
        returned_model="jev-1.13.0",
        usage={"input_tokens": 100},
        attempts=[{"duration_ms": 10}, {"duration_ms": 30}],
        sdk_version="0.7.1",
    )
    for q, spec in get_profile(profile).questions().items():
        if spec["type"] == "noul":
            value = case["labels"][q]
            result["answers"][q] = {"type": "noul", "noul": 0.8 if value else 0.2}
        elif spec["type"] == "score":
            result["answers"][q] = {
                "type": "score",
                "score": 0.0,
                "confidence": 1.0,
                "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0},
                "legend": {str(i): value for i, value in enumerate(spec["criteria"])},
            }
        else:
            chosen = expectation or case["labels"][q]
            distribution = {key: float(key == chosen) for key in spec["criteria"]}
            result["answers"][q] = {
                "type": "choice",
                "choice": ("none" if chosen == "both" else "both")
                if mismatch
                else chosen,
                "probabilities": distribution,
                "confidence": 0.123,
            }
    return result


def _write_sidecar(root, case, value):
    path = root / "identity-group" / (case["case_id"] + ".analysis.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def test_scorer_fake_sidecars_counts_pairs_calibration_and_report(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(sys.modules, "relplot", None)
    dev = [c for c in CASES if c["split"] == "dev" and c["arm"] == "clean"][:4]
    test = [c for c in CASES if c["split"] == "test" and c["arm"] == "clean"][:2]
    cases = dev + test
    paths = {
        profile: tmp_path / profile for profile in ("triage-v1", "triage-choice-v1")
    }
    for profile, directory in paths.items():
        for case in cases:
            _write_sidecar(
                directory,
                case,
                _fake_sidecar(
                    case,
                    profile,
                    mismatch=profile == "triage-choice-v1" and case == test[0],
                ),
            )
    document = {"cases": cases}
    result = score.evaluate(document, paths)
    run = result["runs"]["triage-v1"]
    assert run["headline_clean"][score.REPLY]["calibration"]["brier"] == pytest.approx(
        0.04
    )
    assert run["headline_clean"][score.EXPECTATION]["error"]["rate"] == 0
    assert run["operations"]["input_tokens"]["p50"] == 100
    assert run["operations"]["attempt_latency_ms"]["p50"] == 20
    assert run["operations"]["summed_attempt_latency_ms"]["p50"] == 40
    assert run["operations"]["observed_returned_models"] == {"jev-1.13.0": 2}
    assert (
        result["runs"]["triage-choice-v1"]["diagnostics"][
            "argmax_choice_mismatch_count"
        ]
        == 1
    )
    assert (
        result["runs"]["triage-choice-v1"]["headline_clean"][score.EXPECTATION][
            "error"
        ]["rate"]
        == 0.5
    )
    assert (
        result["paired_profile_difference"]["clean"][score.EXPECTATION]["estimate"]
        == -0.5
    )
    score.write_report(result, tmp_path / "report")
    assert json.loads((tmp_path / "report/metrics.json").read_text()) == result
    assert "model pre-labels" in (tmp_path / "report/report.md").read_text()
    changed = copy.deepcopy(document)
    for case in changed["cases"]:
        if case["split"] == "test":
            case["labels"][score.REPLY] = not case["labels"][score.REPLY]
    assert score.evaluate(changed, paths)["thresholds"] == result["thresholds"]


def test_loader_failed_attempt_malformed_missing_duplicate_and_identity(tmp_path):
    cases = [c for c in CASES if c["arm"] == "clean"][:5]
    profile = "triage-v1"
    success = _fake_sidecar(cases[0], profile)
    path = _write_sidecar(tmp_path, cases[0], success)
    failed = _fake_sidecar(cases[1], profile)
    failed.update(execution_status="failed", answers={})
    attempt_dir = tmp_path / (cases[1]["case_id"] + ".analysis.json.attempts")
    attempt_dir.mkdir()
    (attempt_dir / "attempt.json").write_text(json.dumps(failed))
    bad_path = _write_sidecar(tmp_path, cases[2], success)
    bad_path.write_text("{")
    skipped = _fake_sidecar(cases[3], profile)
    skipped.update(execution_status="skipped", answers={})
    _write_sidecar(tmp_path, cases[3], skipped)
    raw, diag, records = score.load_run(tmp_path, profile, cases, ROOT)
    assert set(raw) == {cases[0]["case_id"]}
    assert list(diag["item_status"].values()) == [
        "succeeded",
        "failed",
        "malformed",
        "skipped",
        "missing",
    ]
    assert diag["envelope_status_counts"] == {"succeeded": 1, "failed": 1, "skipped": 1}
    assert len(records) == 3
    invalid = copy.deepcopy(success)
    invalid["state_sha256"] = "wrong-scope"
    path.write_text(json.dumps(invalid))
    assert (
        score.load_run(tmp_path, profile, cases, ROOT)[1]["item_status"][
            cases[0]["case_id"]
        ]
        == "malformed"
    )
    (tmp_path / path.name).write_text(json.dumps(success))
    with pytest.raises(ValueError, match="duplicate"):
        score.load_run(tmp_path, profile, cases, ROOT)


def test_adversarial_unexposed_control_and_missing_pairs():
    cases = [
        {
            "case_id": "attack",
            "pair_of": "clean",
            "arm": "adversarial",
            "attack_location": "authored",
            "thread_root": "a",
        },
        {
            "case_id": "control",
            "pair_of": "clean",
            "arm": "adversarial",
            "attack_location": "attachment",
            "thread_root": "a",
        },
        {
            "case_id": "missing",
            "pair_of": "clean",
            "arm": "missing_context",
            "ambiguity": True,
            "thread_root": "a",
        },
        {
            "case_id": "lost",
            "pair_of": "absent",
            "arm": "missing_context",
            "ambiguity": False,
            "thread_root": "b",
        },
    ]
    predictions = {
        key: {
            "decisions": {score.EXPECTATION: label},
            "abstain": label == score.ABSTAIN,
        }
        for key, label in [
            ("clean", "none"),
            ("attack", "both"),
            ("control", "none"),
            ("missing", score.ABSTAIN),
        ]
    }
    result = score.pair_metrics(cases, predictions)
    assert result["adversarial_exposed"]["flip_rate"]["rate"] == 1
    assert result["unexposed_control"]["flip_rate"]["rate"] == 0
    assert result["missing_context"]["abstention_change"]["estimate"] == 1
    assert result["missing_context"]["dropped_pairs"] == 1


def test_abstention_precision_cc_false_positive_and_unknown_labels():
    cases = [
        {
            "case_id": "missing",
            "arm": "missing_context",
            "pair_of": "clean",
            "ambiguity": True,
            "attribution": "to",
            "thread_root": "a",
            "labels": {
                **{q: None for q in score.BINARY},
                score.URGENCY: 0,
                score.EXPECTATION: score.ABSTAIN,
            },
        },
        {
            "case_id": "cc",
            "arm": "clean",
            "pair_of": None,
            "ambiguity": False,
            "attribution": "cc_not_asked",
            "thread_root": "b",
            "labels": {
                **{q: False for q in score.BINARY},
                score.URGENCY: 0,
                score.EXPECTATION: "none",
            },
        },
    ]
    predictions = {
        "missing": {
            "decisions": {
                **{q: None for q in score.BINARY},
                score.URGENCY: 0,
                score.EXPECTATION: score.ABSTAIN,
            },
            "probabilities": {},
            "abstain": True,
            "decision_probability": 0.9,
        },
        "cc": {
            "decisions": {
                **{q: True for q in score.BINARY},
                score.URGENCY: 0,
                score.EXPECTATION: "both",
            },
            "probabilities": {},
            "abstain": False,
            "decision_probability": 0.9,
        },
    }
    result = score.summarize(cases, predictions)
    assert result["insufficient_context"]["precision_relevant_missing"]["rate"] == 1
    assert result["cc_not_asked"]["false_positive"]["rate"] == 1
    assert result["by_arm"]["missing_context"][score.REPLY]["excluded_unknown"] == 1


def test_multiclass_brier_and_top_label_calibration(monkeypatch):
    monkeypatch.setitem(sys.modules, "relplot", None)
    cases = [
        {
            "case_id": "a",
            "labels": {score.EXPECTATION: "non_reply_action_only", score.URGENCY: 1},
        }
    ]
    predictions = {
        "a": {
            "decisions": {score.EXPECTATION: "non_reply_action_only", score.URGENCY: 1},
            "probabilities": {
                score.EXPECTATION: {
                    "none": 0.1,
                    "reply_only": 0.2,
                    "non_reply_action_only": 0.3,
                    "both": 0.15,
                    score.ABSTAIN: 0.25,
                },
                score.URGENCY: {"0": 0.2, "1": 0.5, "2": 0.3},
            },
        }
    }
    choice = score.question_metrics(cases, predictions, score.EXPECTATION)
    assert choice["multiclass_brier"] == pytest.approx(0.625)
    assert choice["top_label_calibration"]["ece"] == pytest.approx(0.7)
    urgency = score.question_metrics(cases, predictions, score.URGENCY)
    assert urgency["multiclass_brier"] == pytest.approx(0.38)
    assert urgency["top_label_calibration"]["brier"] == 0.25


def test_profile_mismatch_and_malformed_profile_counted(tmp_path):
    case = CASES[0]
    result = _fake_sidecar(case, "triage-v1")
    path = _write_sidecar(tmp_path, case, result)
    with pytest.raises(ValueError, match="profile mismatch"):
        score.load_run(tmp_path, "triage-choice-v1", [case], ROOT)
    del result["profile"]
    path.write_text(json.dumps(result))
    raw, diag, _ = score.load_run(tmp_path, "triage-v1", [case], ROOT)
    assert not raw
    assert diag["item_status"][case["case_id"]] == "malformed"
