"""Repeat-run metrics use offline sidecars, fixed rules and matched evidence."""

from __future__ import annotations

import copy
import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

with patch.object(sys, "path", [str(Path(__file__).resolve().parents[2]), *sys.path]):
    from benchmarks.analysis_eval import stability

from dead_letter.analysis.eml import prepare_eml
from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.service import _result_envelope

ROOT = Path(__file__).resolve().parents[2] / "benchmarks/analysis_eval"
CASES = json.loads((ROOT / "labels.json").read_text())["cases"][:2]
REPLY = "reply_request_present"
EXPECTATION = "response_expectation"
URGENCY = "expressed_urgency"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("stability comparison must remain offline")

    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(socket.socket, "connect", fail)


def _result(case, profile="triage-v1"):
    prepared = prepare_eml(
        ROOT / case["file"],
        profile_name=profile,
        focus_identity=tuple(case["focus_identity"]),
        model="jev-1.13.0",
    )
    result = _result_envelope(prepared)
    result.update(
        execution_status="succeeded", returned_model="jev-1.13.0", sdk_version="0.7.5"
    )
    for question, spec in get_profile(profile).questions().items():
        kind = spec["type"]
        if kind == "noul":
            answer = {"type": kind, "noul": 0.2}
        elif kind == "score":
            answer = {
                "type": kind,
                "score": 0.0,
                "confidence": 0.5,
                "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0},
                "legend": {str(i): text for i, text in enumerate(spec["criteria"])},
            }
        else:
            answer = {
                "type": kind,
                "choice": "none",
                "confidence": 0.5,
                "probabilities": {
                    key: float(key == "none") for key in spec["criteria"]
                },
            }
        result["answers"][question] = answer
    return result


def _write(directory, case, result):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{case['case_id']}.eml.analysis.json"
    path.write_text(json.dumps(result))
    return path


def _runs(tmp_path, profile="triage-v1", cases=CASES):
    runs = {"first": tmp_path / "first", "repeat": tmp_path / "repeat"}
    for case in cases:
        result = _result(case, profile)
        for directory in runs.values():
            _write(directory, case, result)
    return runs


def test_noul_known_flip_drift_and_no_label_fitting(tmp_path):
    runs = _runs(tmp_path)
    first, repeat = _result(CASES[0]), _result(CASES[0])
    first["answers"][REPLY]["noul"] = 0.49
    repeat["answers"][REPLY]["noul"] = 0.51
    _write(runs["first"], CASES[0], first)
    _write(runs["repeat"], CASES[0], repeat)
    repeat = _result(CASES[1])
    repeat["answers"][REPLY]["noul"] = 0.8
    _write(runs["repeat"], CASES[1], repeat)
    document = {"cases": copy.deepcopy(CASES)}
    report = stability.compare(document, runs)
    result = report["comparisons"]["repeat"]["questions"][REPLY]
    assert result["n"] == result["label_flips"] == 2
    assert result["label_flip_rate"] == 1
    assert result["probability_drift"] == pytest.approx({"mean": 0.31, "max": 0.6})
    # Changing all truth values must not alter stability measurements.
    for case in document["cases"]:
        case["labels"] = {}
    changed = stability.compare(document, runs)
    assert changed["comparisons"] == report["comparisons"]
    assert changed["labels_sha256"] != report["labels_sha256"]


def test_choice_abstention_score_distribution_and_native_confidence(tmp_path):
    profile = "triage-choice-v1"
    runs = _runs(tmp_path, profile, CASES[:1])
    result = _result(CASES[0], profile)
    answer = result["answers"][EXPECTATION]
    answer.update(choice="insufficient_context", confidence=1.0)
    answer["probabilities"].update(none=0.2, insufficient_context=0.8)
    result["answers"][URGENCY].update(
        score=0.8, confidence=0.0, probabilities={"0": 0.2, "1": 0.8, "2": 0.0}
    )
    _write(runs["repeat"], CASES[0], result)
    report = stability.compare({"cases": CASES[:1]}, runs, profile=profile)
    questions = report["comparisons"]["repeat"]["questions"]
    assert questions[EXPECTATION]["label_flips"] == 1
    assert questions[EXPECTATION]["probability_drift"]["mean"] == pytest.approx(0.8)
    assert questions[URGENCY]["label_flips"] == 1
    assert questions[URGENCY]["absolute_score_change"]["mean"] == pytest.approx(0.8)


def test_native_tied_choice_can_flip_without_probability_drift(tmp_path):
    profile = "triage-choice-v1"
    runs = _runs(tmp_path, profile, CASES[:1])
    for name, chosen in (("first", "none"), ("repeat", "both")):
        result = _result(CASES[0], profile)
        result["answers"][EXPECTATION].update(choice=chosen)
        result["answers"][EXPECTATION]["probabilities"].update(none=0.5, both=0.5)
        _write(runs[name], CASES[0], result)
    report = stability.compare({"cases": CASES[:1]}, runs, profile=profile)
    question = report["comparisons"]["repeat"]["questions"][EXPECTATION]
    assert question["label_flip_rate"] == 1
    assert question["probability_drift"]["max"] == 0


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("state_sha256", "wrong-scope", "no_valid_success_in_both_runs"),
        ("requested_model", "different-model", "no_valid_success_in_both_runs"),
        ("returned_model", "different-model", "different_returned_model"),
        ("returned_model", None, "missing_returned_model"),
        ("endpoint", "https://different.example.test", "different_endpoint"),
        ("sdk_version", "other", "different_sdk_version"),
        ("request_fingerprint", "different-input", "different_request_fingerprint"),
    ],
)
def test_provenance_mismatch_is_excluded_not_zero_instability(
    tmp_path, field, value, reason
):
    runs = _runs(tmp_path, cases=CASES[:1])
    result = _result(CASES[0])
    result[field] = value
    _write(runs["repeat"], CASES[0], result)
    report = stability.compare({"cases": CASES[:1]}, runs)
    comparison = report["comparisons"]["repeat"]
    assert comparison["matched"] == 0
    assert comparison["excluded"] == {CASES[0]["case_id"]: reason}
    assert comparison["questions"][REPLY]["label_flip_rate"] is None
    assert comparison["questions"][REPLY]["probability_drift"]["mean"] is None


def test_failed_attempts_and_missing_cases_have_explicit_denominators(tmp_path):
    runs = _runs(tmp_path, cases=CASES[:1])
    result = _result(CASES[0])
    result.update(execution_status="failed", answers={})
    path = _write(runs["repeat"], CASES[0], result)
    attempts = runs["repeat"] / f"{CASES[0]['case_id']}.analysis.json.attempts"
    attempts.mkdir()
    path.rename(attempts / "attempt.json")
    report = stability.compare({"cases": CASES}, runs)
    assert report["runs"]["repeat"]["selected_status_counts"] == {
        "failed": 1,
        "missing": 1,
    }
    comparison = report["comparisons"]["repeat"]
    assert comparison["selected"] == 2
    assert comparison["matched"] == 0
    assert comparison["questions"][REPLY]["n"] == 0


def test_separate_identical_runs_allowed_aliases_and_duplicate_sidecars_rejected(
    tmp_path,
):
    runs = _runs(tmp_path)
    report = stability.compare({"cases": CASES}, runs)
    assert report["comparisons"]["repeat"]["questions"][REPLY]["label_flip_rate"] == 0
    with pytest.raises(ValueError, match="non-overlapping"):
        stability.compare(
            {"cases": CASES}, {"first": runs["first"], "repeat": runs["first"] / "."}
        )
    with pytest.raises(ValueError, match="non-overlapping"):
        stability.compare(
            {"cases": CASES}, {"first": tmp_path, "repeat": runs["repeat"]}
        )
    _write(runs["first"] / "nested", CASES[0], _result(CASES[0]))
    with pytest.raises(ValueError, match="duplicate sidecar"):
        stability.compare({"cases": CASES}, runs)


def test_report_is_deterministic_no_clobber_and_never_changes_inputs(tmp_path):
    runs = _runs(tmp_path)
    before = {
        path: path.read_bytes()
        for root in runs.values()
        for path in root.rglob("*")
        if path.is_file()
    }
    report = stability.compare({"cases": CASES}, runs)
    assert report == stability.compare({"cases": CASES}, runs)
    out = tmp_path / "report"
    stability.write_report(report, out)
    assert json.loads((out / "metrics.json").read_text()) == report
    assert "not correctness" in (out / "report.md").read_text()
    with pytest.raises(FileExistsError):
        stability.write_report(report, out)
    with pytest.raises(ValueError, match="outside input"):
        stability.write_report(report, runs["first"] / "report")
    assert all(path.read_bytes() == value for path, value in before.items())


def test_cli_uses_named_runs_and_frozen_selection(tmp_path, monkeypatch, capsys):
    runs = _runs(tmp_path)
    out = tmp_path / "report"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stability",
            "--profile",
            "triage-v1",
            "--run",
            f"first={runs['first']}",
            "--run",
            f"repeat={runs['repeat']}",
            "--split",
            "dev",
            "--arm",
            "clean",
            "--out",
            str(out),
        ],
    )
    stability.main()
    assert json.loads(capsys.readouterr().out)["report"] == str(out / "report.md")
    report = json.loads((out / "metrics.json").read_text())
    assert report["comparisons"]["repeat"]["matched"] == 2
    assert report["selection"] == {"split": "dev", "arms": ["clean"], "limit": None}


def test_inconsistent_native_choice_is_excluded(tmp_path):
    profile = "triage-choice-v1"
    runs = _runs(tmp_path, profile, CASES[:1])
    result = _result(CASES[0], profile)
    result["answers"][EXPECTATION]["choice"] = "both"
    _write(runs["repeat"], CASES[0], result)
    report = stability.compare({"cases": CASES[:1]}, runs, profile=profile)
    assert report["comparisons"]["repeat"]["excluded"] == {
        CASES[0]["case_id"]: "inconsistent_choice_distribution"
    }
