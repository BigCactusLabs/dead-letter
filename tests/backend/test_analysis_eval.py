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
