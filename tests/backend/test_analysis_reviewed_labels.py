"""Offline review revisions bind source evidence and never substitute model truth."""

from __future__ import annotations

import copy
import json
import shutil
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

with patch.object(sys, "path", [str(Path(__file__).resolve().parents[2]), *sys.path]):
    from benchmarks.analysis_eval import baseline, reviewed_labels, score

from dead_letter.analysis.eml import prepare_eml
from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.service import _result_envelope

ROOT = Path(__file__).resolve().parents[2] / "benchmarks/analysis_eval"
DOCUMENT = json.loads((ROOT / "labels.json").read_text())
BY_ID = {case["case_id"]: case for case in DOCUMENT["cases"]}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("reviewed-label tests must remain offline")

    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setitem(sys.modules, "relplot", None)


@pytest.fixture
def template():
    return reviewed_labels.create_template(DOCUMENT, ROOT, revision="test-review-v1")


def _review_required(artifact, split="test"):
    for entry in artifact["cases"]:
        case = BY_ID[entry["case_id"]]
        if case["split"] == split or (
            case["split"] == "dev" and case["arm"] == "clean"
        ):
            entry["review"] = {
                "status": "reviewed",
                "reviewers": ["synthetic-test-reviewer"],
                "reviewed_at": "2026-10-27T12:00:00Z",
                "adjudicator": None,
                "note": "Fabricated provenance for an offline contract test.",
            }
    return artifact


def _entry(artifact, case_id="seed-reply-positive"):
    return next(entry for entry in artifact["cases"] if entry["case_id"] == case_id)


def test_template_is_pending_and_separate_from_unchanged_prelabels(template):
    before = (ROOT / "labels.json").read_bytes()
    assert template["prelabels_sha256"] == score.digest(DOCUMENT)
    assert len(template["cases"]) == 192
    for entry in template["cases"]:
        assert entry["review"] == {
            "status": "pending",
            "reviewers": [],
            "reviewed_at": None,
            "adjudicator": None,
            "note": "",
        }
        assert entry["labels"] == BY_ID[entry["case_id"]]["labels"]
    summary = reviewed_labels.validate_review(DOCUMENT, template, ROOT)
    assert summary["review_status_counts"] == {"pending": 192}
    assert summary["required_case_ids"] == []
    assert (ROOT / "labels.json").read_bytes() == before


@pytest.mark.parametrize("split", ["dev", "test"])
def test_pending_template_cannot_be_imported_or_scored(template, split):
    with pytest.raises(ValueError, match="review incomplete"):
        reviewed_labels.apply_review(DOCUMENT, template, ROOT, split=split)
    with pytest.raises(ValueError, match="review incomplete"):
        score.evaluate(DOCUMENT, {}, split=split, reviewed_labels=template)


def test_partial_review_has_exact_coverage_and_no_pending_truth(template):
    _review_required(template)
    imported, provenance = reviewed_labels.apply_review(
        DOCUMENT, template, ROOT, split="test"
    )
    assert provenance["review_status_counts"] == {"reviewed": 157, "pending": 35}
    assert len(provenance["clean_dev_case_ids"]) == 37
    assert len(provenance["scored_case_ids"]) == 120
    assert len(provenance["required_case_ids"]) == 157
    pending = [
        case for case in imported["cases"] if case["human_review_status"] == "pending"
    ]
    assert len(pending) == 35
    assert all(
        case["labels"] is None and case["label_source"] is None for case in pending
    )
    assert all(case["labels"] is not None for case in DOCUMENT["cases"])
    with pytest.raises(ValueError, match="original pre-labels"):
        score.evaluate(imported, {})
    # Coverage is independent of which cases happen to have predictions.
    _entry(template)["review"] = {
        "status": "pending",
        "reviewers": [],
        "reviewed_at": None,
        "adjudicator": None,
        "note": "",
    }
    with pytest.raises(ValueError, match="seed-reply-positive"):
        score.evaluate(DOCUMENT, {}, reviewed_labels=template)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda review: review.update(prelabels_sha256="0" * 64), "revision mismatch"),
        (
            lambda review: review["cases"].append(copy.deepcopy(review["cases"][0])),
            "duplicate reviewed case",
        ),
        (lambda review: review["cases"].pop(), "retain every original case"),
        (
            lambda review: review["cases"][0].update(case_id="unknown-case"),
            "unknown reviewed case",
        ),
        (
            lambda review: review["cases"][0].update(source_sha256="0" * 64),
            "source_sha256 mismatch",
        ),
        (
            lambda review: review["cases"][0]["metadata"].update(split="dev"),
            "metadata mismatch",
        ),
        (
            lambda review: review["cases"][0]["metadata"].update(
                focus_identity=["other@example.test"]
            ),
            "metadata mismatch",
        ),
        (
            lambda review: review["cases"][0]["metadata"].update(
                template_family="different"
            ),
            "metadata mismatch",
        ),
    ],
)
def test_stale_unknown_duplicate_or_reassigned_cases_fail(template, mutation, match):
    mutation(template)
    with pytest.raises(ValueError, match=match):
        reviewed_labels.validate_review(DOCUMENT, template, ROOT)


def test_current_source_bytes_and_prelabel_revision_are_bound(tmp_path, template):
    source_root = tmp_path / "corpus-root"
    shutil.copytree(ROOT / "corpus", source_root / "corpus")
    target = source_root / BY_ID["seed-reply-positive"]["file"]
    target.write_bytes(target.read_bytes() + b"\nChanged evidence.\n")
    with pytest.raises(ValueError, match="source_sha256 mismatch: seed-reply-positive"):
        reviewed_labels.validate_review(DOCUMENT, template, source_root)
    changed = copy.deepcopy(DOCUMENT)
    changed["status"] = "A different annotation revision."
    with pytest.raises(ValueError, match="revision mismatch"):
        reviewed_labels.validate_review(changed, template, ROOT)


@pytest.mark.parametrize(
    "change",
    [
        {"reviewers": []},
        {"reviewers": ["   "]},
        {"reviewed_at": None},
        {"reviewed_at": "2026-10-27T12:00:00"},
        {"reviewed_at": "not-a-time"},
        {"reviewed_at": "2026-10-27X12:00:00Z"},
        {"reviewed_at": "2026-W44-2T12:00:00Z"},
        {"reviewed_at": "2026-10-27T12:00Z"},
        {"status": "adjudicated", "adjudicator": "judge"},
    ],
)
def test_review_claims_require_entered_provenance(template, change):
    _review_required(template)
    _entry(template)["review"].update(change)
    with pytest.raises(ValueError, match="review schema violation|invalid reviewed_at"):
        reviewed_labels.validate_review(DOCUMENT, template, ROOT)


def test_adjudication_and_question_ambiguity_survive_import(template):
    _review_required(template)
    entry = _entry(template)
    entry["labels"][score.REPLY] = entry["labels"][score.ACTION] = None
    entry["labels"][score.EXPECTATION] = score.ABSTAIN
    entry["ambiguity_notes"] = {
        question: "Synthetic reviewer disagreement cannot be resolved from available context."
        for question in (score.REPLY, score.ACTION, score.EXPECTATION)
    }
    entry["review"].update(
        status="adjudicated",
        reviewers=["reviewer-a", "reviewer-b"],
        adjudicator="adjudicator-c",
        note="Keep this case ambiguous.",
    )
    imported, provenance = reviewed_labels.apply_review(
        DOCUMENT, template, ROOT, split="test"
    )
    case = next(
        case for case in imported["cases"] if case["case_id"] == entry["case_id"]
    )
    assert case["labels"][score.REPLY] is None
    assert case["ambiguity"] is True
    assert score.REPLY in case["ambiguity_note"]
    assert provenance["reviews"][entry["case_id"]] == entry["review"]
    del entry["ambiguity_notes"][score.REPLY]
    with pytest.raises(ValueError, match="per-question notes"):
        reviewed_labels.apply_review(DOCUMENT, template, ROOT, split="test")


@pytest.mark.parametrize("value", [True, 1.0, 0.5])
def test_ordinal_labels_do_not_accept_booleans_or_floats(template, value):
    _entry(template)["labels"][score.URGENCY] = value
    with pytest.raises(ValueError):
        reviewed_labels.validate_review(DOCUMENT, template, ROOT)


def test_inconsistent_reply_action_truth_fails(template):
    _entry(template)["labels"][score.REPLY] = None
    with pytest.raises(ValueError, match="inconsistent response expectation"):
        reviewed_labels.validate_review(DOCUMENT, template, ROOT)


def _fake_success(case):
    prepared = prepare_eml(
        ROOT / case["file"],
        focus_identity=tuple(case["focus_identity"]),
        model="jev-1.13.0",
    )
    result = _result_envelope(prepared)
    result.update(
        execution_status="succeeded", returned_model="jev-1.13.0", sdk_version="0.7.5"
    )
    for question, spec in get_profile().questions().items():
        if spec["type"] == "noul":
            result["answers"][question] = {"type": "noul", "noul": 0.2}
        else:
            result["answers"][question] = {
                "type": "score",
                "score": 0.0,
                "confidence": 1.0,
                "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0},
                "legend": {str(i): text for i, text in enumerate(spec["criteria"])},
            }
    return result


def test_reviewed_truth_changes_test_metrics_but_never_dev_fit(tmp_path, template):
    _review_required(template)
    test_cases = [
        case
        for case in DOCUMENT["cases"]
        if case["split"] == "test" and case["arm"] == "clean"
    ][:2]
    run = tmp_path / "runs"
    run.mkdir()
    for case in [
        BY_ID["seed-reply-positive"],
        BY_ID["seed-reply-negative"],
        *test_cases,
    ]:
        (run / f"{case['case_id']}.analysis.json").write_text(
            json.dumps(_fake_success(case))
        )
    first = score.evaluate(DOCUMENT, {"triage-v1": run}, reviewed_labels=template)
    changed = copy.deepcopy(template)
    entry = _entry(changed, test_cases[0]["case_id"])
    labels = entry["labels"]
    labels[score.REPLY] = not labels[score.REPLY]
    labels[score.EXPECTATION] = next(
        label
        for label, bits in score.BITS.items()
        if bits == (labels[score.REPLY], labels[score.ACTION])
    )
    second = score.evaluate(DOCUMENT, {"triage-v1": run}, reviewed_labels=changed)
    assert first["thresholds"] == second["thresholds"]
    assert first["labels_sha256"] != second["labels_sha256"]
    assert (
        first["runs"]["triage-v1"]["headline_clean"][score.REPLY]["error"]
        != second["runs"]["triage-v1"]["headline_clean"][score.REPLY]["error"]
    )
    assert first["label_provenance"]["revision"] == "test-review-v1"
    assert "reviewed annotations" in first["status"]
    score.write_report(first, tmp_path / "report")
    text = (tmp_path / "report/report.md").read_text()
    assert "test-review-v1" in text
    assert "Labels remain model pre-labels" not in text


def test_cli_template_and_validation_are_no_clobber(tmp_path, monkeypatch, capsys):
    path = tmp_path / "review.json"
    args = ["reviewed_labels", "template", "--revision", "draft-v1", "--out", str(path)]
    monkeypatch.setattr(sys, "argv", args)
    reviewed_labels.main()
    assert json.loads(capsys.readouterr().out)["pending_cases"] == 192
    before = path.read_bytes()
    with pytest.raises(SystemExit) as exc:
        reviewed_labels.main()
    assert exc.value.code == 2
    assert path.read_bytes() == before
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["reviewed_labels", "validate", str(path)])
    reviewed_labels.main()
    assert json.loads(capsys.readouterr().out)["review_status_counts"] == {
        "pending": 192
    }


def test_scorer_cli_selects_review_file_outside_corpus(
    tmp_path, template, monkeypatch, capsys
):
    _review_required(template, split="dev")
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(template))
    baseline_path = tmp_path / "baseline.json"
    small_input = {
        "cases": [BY_ID["seed-reply-positive"], BY_ID["seed-reply-negative"]]
    }
    baseline_path.write_text(json.dumps(baseline.run_baseline(small_input, ROOT)))
    out = tmp_path / "report"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score",
            "--reviewed-labels",
            str(review_path),
            "--baseline",
            str(baseline_path),
            "--split",
            "dev",
            "--out",
            str(out),
        ],
    )
    score.main()
    result = json.loads(capsys.readouterr().out)
    assert "reviewed annotations" in result["status"]
    report = json.loads((out / "metrics.json").read_text())
    assert report["label_provenance"]["review_status_counts"] == {
        "reviewed": 72,
        "pending": 120,
    }
    assert report["runs"]["baseline"]["headline_clean"][score.REPLY]["n"] == 2
    default = score.evaluate(
        DOCUMENT, {}, baseline=json.loads(baseline_path.read_text()), split="dev"
    )
    assert "model pre-labels" in default["status"]
    assert "label_provenance" not in default


def test_cc_false_positives_use_reviewed_negative_truth_not_original_stratum():
    cases, predictions = [], {}
    for name, reply, action, expected in (
        ("positive", True, False, "reply_only"),
        ("unknown", None, None, score.ABSTAIN),
        ("negative", False, False, "none"),
    ):
        case = copy.deepcopy(BY_ID["radio-cabinet-direct"])
        case.update(
            case_id=name, label_source="human_review", human_review_status="reviewed"
        )
        case["labels"].update(
            {score.REPLY: reply, score.ACTION: action, score.EXPECTATION: expected}
        )
        cases.append(case)
        predictions[name] = {
            "decisions": {
                **case["labels"],
                score.REPLY: True,
                score.ACTION: False,
                score.EXPECTATION: "reply_only",
            },
            "probabilities": {},
            "abstain": False,
            "decision_probability": 1.0,
        }
    result = score.summarize(cases, predictions)
    cc = result["cc_not_asked"]
    assert cc["expected"] == cc["successful"] == 3
    assert cc["false_positive"]["n"] == 1
    assert cc["false_positive"]["rate"] == 1
    assert cc["false_positive_excluded_nonnegative_or_unknown"] == 2
    assert cc["per_question_false_positive"][score.REPLY]["n"] == 1
    assert cc["per_question_false_positive"][score.ACTION]["n"] == 2
    assert cc["per_question_false_positive"][score.ACTION]["rate"] == 0
    assert cc["per_question_excluded_nonnegative_or_unknown"] == {
        score.REPLY: 2,
        score.ACTION: 1,
    }
