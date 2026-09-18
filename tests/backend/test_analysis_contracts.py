"""Offline boundary tests, not an empirical email-classification benchmark."""

from __future__ import annotations

import copy
import json
import socket
import sys
from dataclasses import replace

import pytest

from dead_letter.analysis import (
    AnalysisError, NormalizedMessage, Segment, build_state, get_profile,
    prepare_request, validate_response,
)
from dead_letter.analysis.contracts import canonical_json, validate_base_url

PRIVATE = "PRIVATE_BODY_SENTINEL_94c12"


def message(**changes):
    values = {
        "subject": "PRIVATE_SUBJECT_SENTINEL",
        "sender": "sender-private@example.com",
        "to": ("quinn-private@example.com", "alex-private@example.com"),
        "cc": ("observer-private@example.com",),
        "sent_at": "2001-01-02T10:30:00-05:00",
        "normalization_version": "test-normalizer-v1",
        "segments": (
            Segment("latest-1", "authored", f"Review and reply with approval. {PRIVATE}"),
            Segment("parent-1", "quoted", "Earlier request", "prior@example.com", "2001-01-01"),
        ),
    }
    values.update(changes)
    return NormalizedMessage(**values)


def response_for(request):
    """Invented values exercise the wire contract; they are not model predictions."""
    answers = {}
    for key, question in request.profile.questions().items():
        kind = question["type"]
        if kind == "noul":
            answers[key] = {"type": kind, "noul": 0.01}
        elif kind == "score":
            answers[key] = {
                "type": kind, "score": 1.0, "confidence": 0.0,
                "legend": {str(i): value for i, value in enumerate(question["criteria"])},
                "probabilities": {"0": 0.5, "1": 0.0, "2": 0.5},
            }
        else:
            answers[key] = {
                "type": kind, "choice": "insufficient_context", "confidence": 1.0,
                "probabilities": {choice: float(choice == "insufficient_context")
                                  for choice in question["criteria"]},
            }
    return {"answers": answers, "model": "jev-1.13.0",
            "usage": {"input_tokens": 321, "output_tokens": 18}, "request_id": "req-123"}


def test_candidate_profiles_are_versioned_detached_and_explicit():
    first, second = get_profile(), get_profile("triage-choice-v1")
    assert first.experimental and second.experimental
    assert first.sha256 != second.sha256
    assert len(first.questions()) == 5
    assert set(second.questions()) == {
        "response_expectation", "sender_commitment_present",
        "action_deadline_present", "expressed_urgency",
    }
    assert set(second.questions()["response_expectation"]["criteria"]) == {
        "none", "reply_only", "non_reply_action_only", "both", "insufficient_context",
    }
    for profile in (first, second):
        assert profile.revision
        for question in profile.questions().values():
            for field in ("message.sent_at", "request_scope", "focus_identity.aliases",
                          "context.segments", "untrusted", "attachment"):
                assert field in question["instructions"]
        assert isinstance(profile.questions()["expressed_urgency"]["criteria"], list)
        exported = profile.questions()
        exported.clear()
        assert profile.questions()
    assert replace(first, revision="another-revision").sha256 != first.sha256
    modified = first.questions()
    modified["reply_request_present"]["instructions"] += " changed"
    assert replace(first, questions_json=json.dumps(modified)).sha256 != first.sha256
    with pytest.raises(ValueError, match="^unknown_analysis_profile$"):
        get_profile(PRIVATE)


def test_state_keeps_authorship_coverage_and_message_time_distinct():
    state = build_state(message(attachment_count=1, reply_headers_present=True))
    assert state["request_scope"] == "recipient_group"
    assert state["focus_identity"] is None
    assert state["message"]["sent_at"].startswith("2001-")
    assert state["reference_time_policy"] == "message_time_not_processing_time"
    assert state["message"]["authored_segments"][0]["id"] == "latest-1"
    assert state["context"]["segments"][0]["author"] == "prior@example.com"
    assert state["coverage"]["attachment_count"] == 1
    assert not state["coverage"]["attachment_text_available"]
    assert "answers" not in state
    assert "requires_action" not in canonical_json(state)


def test_unknown_dates_authors_and_missing_parent_remain_unknown():
    state = build_state(message(sent_at=None, reply_headers_present=True, segments=(
        Segment("s1", "authored", "Yes, please do that."),
    )))
    assert state["message"]["sent_at"] is None
    assert not state["coverage"]["thread_context_available"]
    assert state["coverage"]["reply_headers_present"]
    assert "assessment_status" not in state
    forwarded = build_state(message(segments=(Segment("f1", "forwarded", "Please pay"),)))
    assert forwarded["context"]["segments"][0]["author"] is None
    assert not forwarded["coverage"]["authored_text_available"]
    assert forwarded["message"]["authored_segments"] == []


def test_context_bounding_is_visible_and_preserves_latest_and_ps():
    segments = [Segment("latest", "authored", "Done, thanks."),
                Segment("ps", "signature_candidate", "P.S. Please send the revised draft.")]
    segments.extend(Segment(f"p{i}", "quoted", f"Prior message {i}") for i in range(5))
    normalized = message(segments=segments)
    segments.clear()
    state = build_state(normalized, max_context_segments=2)
    assert len(state["message"]["authored_segments"]) == 2
    assert [part["id"] for part in state["context"]["segments"]] == ["p0", "p1"]
    assert state["coverage"]["context_segments_excluded"] == 3
    assert not state["coverage"]["content_truncated"]
    assert state["coverage"]["context_selection_policy"] == "first-n-context-segments-v1"
    none = build_state(normalized, max_context_segments=0)
    assert not none["coverage"]["thread_context_available"]
    assert none["coverage"]["context_segments_excluded"] == 5


@pytest.mark.parametrize("value", [-1, 101, True, 1.5, "3"])
def test_invalid_context_limits(value):
    with pytest.raises(ValueError, match="^invalid_context_limit$"):
        build_state(message(), max_context_segments=value)


@pytest.mark.parametrize("value", ["me@example.com", ("",), (" ",), (None,)])
def test_invalid_identity(value):
    with pytest.raises(ValueError):
        build_state(message(), focus_identity=value)


@pytest.mark.parametrize("changes", [
    {"attachment_count": True}, {"attachment_count": -1},
    {"reply_headers_present": 1}, {"conversion_degraded": "false"},
    {"to": "person@example.com"}, {"segments": [object()]},
    {"normalization_version": ""},
    {"segments": (Segment("same", "authored", "one"), Segment("same", "quoted", "two"))},
])
def test_invalid_snapshot_fields(changes):
    with pytest.raises(ValueError):
        message(**changes)


def test_fingerprint_covers_effective_inputs_not_only_source():
    original = prepare_request(message())
    variants = [
        prepare_request(message(), focus_identity=("quinn@example.com",)),
        prepare_request(message(), focus_identity=("alex@example.com",)),
        prepare_request(message(), max_context_segments=0),
        prepare_request(message(), model="jev-latest"),
        prepare_request(message(), base_url="https://proxy.example.com/typesafe"),
        prepare_request(message(), profile_name="triage-choice-v1"),
        prepare_request(message(normalization_version="test-normalizer-v2")),
        prepare_request(message(segments=(Segment("latest-1", "authored", "Changed"),))),
        prepare_request(message(conversion_degraded=True)),
    ]
    assert len({original.fingerprint, *(item.fingerprint for item in variants)}) == 10
    assert original.fingerprint == prepare_request(message()).fingerprint
    alias_a = prepare_request(message(), focus_identity=("B", "A", "A"))
    alias_b = prepare_request(message(), focus_identity=("A", "B"))
    assert alias_a.fingerprint == alias_b.fingerprint
    assert alias_a.preview()["request_scope"] == "focus_identity"
    assert alias_a.preview()["focus_identity_configured"] is True
    assert replace(original, profile=replace(original.profile, revision="next")).fingerprint != original.fingerprint
    payload = original.payload()
    payload["state"]["message"]["subject"] = "mutation"
    payload["questions"].clear()
    assert original.fingerprint == prepare_request(message()).fingerprint


def test_preview_and_repr_do_not_disclose_evidence():
    normalized = message()
    request = prepare_request(normalized, focus_identity=("quinn-private@example.com",))
    preview = request.preview()
    for private in (PRIVATE, "PRIVATE_SUBJECT_SENTINEL", "quinn-private@example.com",
                    "sender-private@example.com", "observer-private@example.com"):
        assert private not in json.dumps(preview)
        assert private not in repr(request)
        assert private not in repr(normalized)
        assert private not in repr(normalized.segments[0])
    assert preview["execution_status"] == "skipped"
    assert preview["assessment_status"] is None
    assert preview["remote_enabled"] is False
    assert "state" not in preview
    assert PRIVATE in json.dumps(request.preview(include_state=True))
    assert preview["limits"]["unit"] == "utf8_bytes_not_provider_tokens"


def test_key_or_debug_environment_does_not_enable_remote_processing(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "PRIVATE_KEY_SENTINEL")
    monkeypatch.setenv("TYPESAFE_LOG_LEVEL", "debug")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://unexpected.example.com")
    loaded_before = set(sys.modules)
    def forbidden(*args, **kwargs):
        pytest.fail("offline contracts attempted network or file access")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    request = prepare_request(message())
    assert request.base_url == "https://api.typesafe.ai"
    assert "PRIVATE_KEY_SENTINEL" not in json.dumps(request.preview(include_state=True))
    validate_response(request, response_for(request))
    assert not any(name.startswith("typesafe_sdk") for name in set(sys.modules) - loaded_before)


@pytest.mark.parametrize(("value", "expected"), [
    ("HTTPS://API.TypeSafe.ai:443/", "https://api.typesafe.ai"),
    ("https://proxy.example.com:8443/gateway/", "https://proxy.example.com:8443/gateway"),
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("http://localhost:8000", "http://localhost:8000"),
    ("http://[::1]:8000/", "http://[::1]:8000"),
])
def test_explicit_endpoint_validation(value, expected):
    assert validate_base_url(value) == expected


@pytest.mark.parametrize("value", [
    "http://api.typesafe.ai", "https://secret@api.typesafe.ai", "https://u:p@api.typesafe.ai",
    "https://api.typesafe.ai?key=secret", "https://api.typesafe.ai#secret",
    "https://api.typesafe.ai?", "https://api.typesafe.ai#", "https://api.typesafe.ai/%2fsecret",
    "https://api.typesafe.ai/a/../b", "https://api.typesafe.ai//path", "ftp://example.com",
    "https://api.typesafe.ai\\@other.example", "https://api.typesafe.ai\n",
    "https://api.typesafe.ai:0", "https://api.typesafe.ai:99999", "https://api.typesafe.ai:abc",
    "https://", "//example.com", "http://127.0.0.1.evil.example", "https://bad_host.example",
    "https://-bad.example", "https://good..example", "", None,
])
def test_unsafe_endpoints_rejected_without_echo(value):
    with pytest.raises(AnalysisError, match="^invalid_provider_endpoint$"):
        validate_base_url(value)


def test_canonical_endpoints_produce_same_fingerprint():
    assert prepare_request(message(), base_url="HTTPS://API.TypeSafe.ai:443/").fingerprint == prepare_request(message()).fingerprint


@pytest.mark.parametrize("text", ["a" * 25_000, "界" * 9_000])
def test_oversized_state_is_rejected_not_truncated(text):
    normalized = message(segments=(Segment("s1", "authored", text),))
    with pytest.raises(AnalysisError, match="^state_byte_limit_exceeded$"):
        prepare_request(normalized)
    assert normalized.segments[0].text == text


@pytest.mark.parametrize("value", [{1: "coercion"}, {"bad": float("nan")}, object()])
def test_only_strict_json_data(value):
    with pytest.raises(AnalysisError, match="^invalid_json_data$"):
        canonical_json(value)


def test_cycle_and_unpaired_surrogate_rejected_safely():
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(AnalysisError, match="^invalid_json_data$"):
        canonical_json(cyclic)
    with pytest.raises(AnalysisError, match="^invalid_json_data$"):
        prepare_request(message(subject="\ud800"))


@pytest.mark.parametrize("profile_name", ["triage-v1", "triage-choice-v1"])
def test_native_results_are_preserved_and_extras_discarded(profile_name):
    request = prepare_request(message(), profile_name=profile_name)
    response = response_for(request)
    response["raw_request"] = {"headers": {"authorization": PRIVATE}, "state": PRIVATE}
    response["usage"]["debug"] = PRIVATE
    for answer in response["answers"].values():
        answer["private_extra"] = PRIVATE
        if answer["type"] == "noul":
            answer["confidence"] = 0.99
    actual = validate_response(request, response)
    assert PRIVATE not in json.dumps(actual)
    assert actual["model"] == "jev-1.13.0"
    assert actual["usage"] == {"input_tokens": 321, "output_tokens": 18}
    for answer in actual["answers"].values():
        if answer["type"] == "noul":
            assert answer == {"type": "noul", "noul": 0.01}
    urgency = actual["answers"]["expressed_urgency"]
    assert urgency["probabilities"] == {"0": 0.5, "1": 0.0, "2": 0.5}
    assert urgency["score"] == 1.0
    assert urgency["confidence"] == 0.0
    assert "execution_status" not in actual
    assert "assessment_status" not in actual


def test_same_score_different_distributions_remain_distinguishable():
    request = prepare_request(message())
    split = response_for(request)
    concentrated = copy.deepcopy(split)
    concentrated["answers"]["expressed_urgency"].update(
        probabilities={"0": 0.0, "1": 1.0, "2": 0.0}, confidence=1.0,
    )
    first = validate_response(request, split)["answers"]["expressed_urgency"]
    second = validate_response(request, concentrated)["answers"]["expressed_urgency"]
    assert first["score"] == second["score"]
    assert first["probabilities"] != second["probabilities"]


@pytest.mark.parametrize("value", [True, False, None, "0.2", -0.1, 1.1, float("nan"), float("inf"), 10 ** 1000])
def test_invalid_noul_rejected(value):
    request = prepare_request(message())
    response = response_for(request)
    response["answers"]["reply_request_present"]["noul"] = value
    with pytest.raises(AnalysisError, match="^invalid_answer_number$"):
        validate_response(request, response)


@pytest.mark.parametrize("mode", ["missing", "extra", "unknown_type"])
def test_completeness_and_types_fail_closed(mode):
    request = prepare_request(message())
    response = response_for(request)
    if mode == "missing":
        del response["answers"]["reply_request_present"]
    elif mode == "extra":
        response["answers"][PRIVATE] = {"type": "noul", "noul": 0}
    else:
        response["answers"]["reply_request_present"]["type"] = PRIVATE
    with pytest.raises(AnalysisError) as error:
        validate_response(request, response)
    assert PRIVATE not in str(error.value)


@pytest.mark.parametrize(("field", "value"), [
    ("score", 2.01), ("score", True), ("confidence", None), ("confidence", 1.1),
    ("probabilities", {"0": 0.5, "1": 0.5}),
    ("probabilities", {"0": 0.5, "1": 0.5, "2": 0.5}),
    ("probabilities", {"0": 0.5, "1": -0.5, "2": 1.0}),
    ("probabilities", {0: 0.5, "0": 0.5, "1": 0, "2": 0}),
    ("legend", {"0": PRIVATE, "1": PRIVATE, "2": PRIVATE}),
    ("legend", None),
])
def test_invalid_scores_rejected(field, value):
    request = prepare_request(message())
    response = response_for(request)
    response["answers"]["expressed_urgency"][field] = value
    with pytest.raises(AnalysisError) as error:
        validate_response(request, response)
    assert PRIVATE not in str(error.value)


def test_sdk_integer_score_levels_are_accepted_without_rounding():
    request = prepare_request(message())
    response = response_for(request)
    answer = response["answers"]["expressed_urgency"]
    answer["legend"] = {int(key): value for key, value in answer["legend"].items()}
    answer["probabilities"] = {0: 0.123456, 1: 0.123456, 2: 0.753088}
    answer["score"] = 1.629632
    actual = validate_response(request, response)["answers"]["expressed_urgency"]
    assert actual["probabilities"]["0"] == 0.123456
    assert actual["score"] == 1.629632


@pytest.mark.parametrize("choice", [PRIVATE, None, [], {}, True])
def test_invalid_choice_rejected(choice):
    request = prepare_request(message(), profile_name="triage-choice-v1")
    response = response_for(request)
    response["answers"]["response_expectation"]["choice"] = choice
    with pytest.raises(AnalysisError, match="^unexpected_choice$"):
        validate_response(request, response)


def test_choice_requires_complete_distribution():
    request = prepare_request(message(), profile_name="triage-choice-v1")
    response = response_for(request)
    del response["answers"]["response_expectation"]["probabilities"]["none"]
    with pytest.raises(AnalysisError, match="^invalid_answer_levels$"):
        validate_response(request, response)


@pytest.mark.parametrize("usage", [{"input_tokens": True}, {"output_tokens": -1}, {"total_tokens": 2.5}, {"input_tokens": "32"}])
def test_invalid_usage_rejected(usage):
    request = prepare_request(message())
    response = response_for(request)
    response["usage"] = usage
    with pytest.raises(AnalysisError, match="^invalid_token_usage$"):
        validate_response(request, response)


def test_missing_returned_model_usage_and_request_id_not_fabricated():
    request = prepare_request(message(), model="jev-latest")
    response = response_for(request)
    actual = validate_response(request, {"answers": response["answers"]})
    assert actual["model"] is None
    assert actual["request_id"] is None
    assert actual["usage"] == {}


@pytest.mark.parametrize("response", [None, [], object(), {"answers": []}])
def test_raw_objects_are_not_accepted(response):
    with pytest.raises(AnalysisError, match="^invalid_response_shape$"):
        validate_response(prepare_request(message()), response)


def test_private_invalid_metadata_is_not_echoed():
    request = prepare_request(message())
    response = response_for(request)
    response["request_id"] = f"Server error including {PRIVATE}"
    with pytest.raises(AnalysisError, match="^invalid_response_metadata$"):
        validate_response(request, response)


def test_synthetic_seed_is_explicitly_unreviewed_and_builds_both_candidates():
    from pathlib import Path

    fixture = json.loads((Path(__file__).parent / "fixtures/analysis_cases.json").read_text())
    assert fixture["synthetic"] is True
    assert fixture["human_review_status"] == "pending"
    ids = set()
    family_splits = {}
    for case in fixture["cases"]:
        assert case["id"] not in ids
        ids.add(case["id"])
        assert case["split"] == "development"  # Not a held-out benchmark.
        family_splits.setdefault(case["family"], set()).add(case["split"])
        fields = dict(case["message"])
        fields["segments"] = tuple(Segment(**part) for part in fields["segments"])
        normalized = NormalizedMessage(**fields)
        for name in ("triage-v1", "triage-choice-v1"):
            request = prepare_request(normalized, profile_name=name,
                                      focus_identity=case["focus_identity"])
            assert request.preview()["execution_status"] == "skipped"
        labels = case["proposed_labels"]
        assert labels["reply_request_present"] in (True, False, None)
        assert labels["non_reply_action_request_present"] in (True, False, None)
        # Structural checks only: no model was asked to predict these labels.
    assert len(ids) == 22
    assert all(len(splits) == 1 for splits in family_splits.values())
