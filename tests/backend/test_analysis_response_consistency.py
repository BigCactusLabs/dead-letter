"""Native-field consistency checks, not email-classification accuracy tests."""

import pytest

from dead_letter.analysis import AnalysisError, NormalizedMessage, Segment, prepare_request, validate_response
from dead_letter.analysis.responses import (
    ANSWER_CONSISTENCY_TOLERANCE, probability_sum_tolerance, score_tolerance,
)


def example(profile_name="triage-v1"):
    request = prepare_request(NormalizedMessage(
        subject="Synthetic", sender="sender@example.com", sent_at=None,
        segments=(Segment("s1", "authored", "Please review."),),
    ), profile_name=profile_name)
    answers = {}
    for key, question in request.profile.questions().items():
        kind = question["type"]
        if kind == "noul":
            answers[key] = {"type": kind, "noul": 0.1}
        elif kind == "score":
            answers[key] = {
                "type": kind, "score": 1.0, "confidence": 1.0,
                "legend": {str(i): value for i, value in enumerate(question["criteria"])},
                "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0},
            }
        else:
            answers[key] = {
                "type": kind, "choice": "none", "confidence": 1.0,
                "probabilities": {choice: float(choice == "none") for choice in question["criteria"]},
            }
    return request, {"answers": answers}


@pytest.mark.parametrize(("score", "probabilities"), [
    (0, {"0": 0.0, "1": 0.0, "2": 1.0}),
    (2, {"0": 1.0, "1": 0.0, "2": 0.0}),
    (0.9, {"0": 0.5, "1": 0.0, "2": 0.5}),
])
def test_contradictory_score_is_rejected(score, probabilities):
    request, response = example()
    response["answers"]["expressed_urgency"].update(score=score, probabilities=probabilities)
    with pytest.raises(AnalysisError, match="^inconsistent_score_distribution$"):
        validate_response(request, response)


@pytest.mark.parametrize(("score", "probabilities"), [
    (1.0, {"0": 0.5, "1": 0.0, "2": 0.5}),
    (1.0, {"0": 0.0, "1": 1.0, "2": 0.0}),
    (1.629632, {0: 0.123456, 1: 0.123456, 2: 0.753088}),
    (1.0 + ANSWER_CONSISTENCY_TOLERANCE, {"0": 0.0, "1": 1.0, "2": 0.0}),
])
def test_consistent_score_and_distribution_are_preserved_without_recalculation(score, probabilities):
    request, response = example()
    response["answers"]["expressed_urgency"].update(score=score, probabilities=probabilities)
    actual = validate_response(request, response)["answers"]["expressed_urgency"]
    assert actual["score"] == score
    assert actual["probabilities"] == {str(key): value for key, value in probabilities.items()}


def test_two_decimal_wire_rounding_is_accepted():
    # Observed live jev-1.13.0 answer: 0.99/0.01/0.00 averages 0.01, but the
    # independently rounded Score is 0.02.
    request, response = example()
    response["answers"]["expressed_urgency"].update(
        score=0.02, probabilities={"0": 0.99, "1": 0.01, "2": 0.0})
    actual = validate_response(request, response)["answers"]["expressed_urgency"]
    assert actual["score"] == 0.02


def test_score_rounding_allowance_is_bounded():
    assert score_tolerance(3) == pytest.approx(0.02)
    request, response = example()
    urgency = response["answers"]["expressed_urgency"]
    urgency.update(score=1.02, probabilities={"0": 0.0, "1": 1.0, "2": 0.0})
    validate_response(request, response)
    urgency.update(score=1.03)
    with pytest.raises(AnalysisError, match="^inconsistent_score_distribution$"):
        validate_response(request, response)


def test_probability_sum_rounding_allowance_is_bounded():
    assert probability_sum_tolerance(3) == pytest.approx(0.015)
    request, response = example()
    urgency = response["answers"]["expressed_urgency"]
    urgency.update(score=1.0, probabilities={"0": 0.33, "1": 0.33, "2": 0.33})
    validate_response(request, response)
    urgency.update(probabilities={"0": 0.32, "1": 0.33, "2": 0.33})
    with pytest.raises(AnalysisError, match="^invalid_probability_sum$"):
        validate_response(request, response)


def test_choice_must_match_a_maximum_probability():
    request, response = example("triage-choice-v1")
    response["answers"]["response_expectation"]["choice"] = "both"
    with pytest.raises(AnalysisError, match="^inconsistent_choice_distribution$"):
        validate_response(request, response)


@pytest.mark.parametrize("choice", ["none", "both"])
def test_either_tied_choice_is_valid(choice):
    request, response = example("triage-choice-v1")
    answer = response["answers"]["response_expectation"]
    answer["choice"] = choice
    answer["probabilities"] = {key: 0.5 if key in {"none", "both"} else 0.0
                               for key in answer["probabilities"]}
    assert validate_response(request, response)["answers"]["response_expectation"]["choice"] == choice
