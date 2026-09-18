"""Validate an allowlisted JSON projection, never a raw SDK/HTTP object.

Successful structural validation does not establish inference success, evidence
sufficiency, model accuracy or authorization to take action. The future service
owns execution/assessment records; this module never fabricates missing answers.
"""

from __future__ import annotations

import math
import re

from dead_letter.analysis.contracts import AnalysisError, PreparedRequest

PROBABILITY_SUM_TOLERANCE = 1e-6
_METADATA_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")


def _number(value: object, *, maximum: float = 1.0) -> float:
    if type(value) not in (int, float):
        raise AnalysisError("invalid_answer_number")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise AnalysisError("invalid_answer_number") from None
    if not math.isfinite(result) or not 0.0 <= result <= maximum:
        raise AnalysisError("invalid_answer_number")
    return result


def _mapping(value: object) -> dict:
    if type(value) is not dict:
        raise AnalysisError("invalid_response_shape")
    return value


def _level_map(value: object, keys: set[str]) -> dict:
    """Accept JSON string levels or SDK integer levels, rejecting key collisions."""
    raw = _mapping(value)
    if any(type(key) not in (str, int) for key in raw):
        raise AnalysisError("invalid_answer_levels")
    mapped = {str(key): item for key, item in raw.items()}
    if len(mapped) != len(raw) or set(mapped) != keys:
        raise AnalysisError("invalid_answer_levels")
    return mapped


def _probabilities(value: object, keys: set[str], *, levels: bool = False) -> dict:
    probabilities = _level_map(value, keys) if levels else _mapping(value)
    if set(probabilities) != keys:
        raise AnalysisError("invalid_answer_levels")
    checked = {key: _number(probabilities[key]) for key in sorted(keys)}
    if not math.isclose(sum(checked.values()), 1.0, rel_tol=0.0,
                        abs_tol=PROBABILITY_SUM_TOLERANCE):
        raise AnalysisError("invalid_probability_sum")
    return checked


def _metadata(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _METADATA_ID.fullmatch(value):
        raise AnalysisError("invalid_response_metadata")
    return value


def validate_response(request: PreparedRequest, response: object) -> dict:
    """Return only native answers plus optional model/request-id/token metadata.

    Unknown extra fields are discarded rather than persisted. Missing/extra
    question IDs, wrong kinds, incomplete distributions and invalid ranges fail
    as safe codes. Near-zero Noul means a strong negative; it is not confidence.
    No thresholds, combined action probability, ranking or confidence are invented.
    """
    raw = _mapping(response)
    answers = _mapping(raw.get("answers"))
    questions = request.profile.questions()
    if set(answers) != set(questions):
        raise AnalysisError("incomplete_or_unexpected_answers")
    validated = {}
    for question_id, question in questions.items():
        answer = _mapping(answers[question_id])
        kind = question["type"]
        if answer.get("type") != kind:
            raise AnalysisError("unexpected_answer_type")
        if kind == "noul":
            validated[question_id] = {"type": kind, "noul": _number(answer.get("noul"))}
        elif kind == "score":
            expected_legend = {str(i): text for i, text in enumerate(question["criteria"])}
            keys = set(expected_legend)
            legend = _level_map(answer.get("legend"), keys)
            if legend != expected_legend:
                raise AnalysisError("unexpected_score_legend")
            validated[question_id] = {
                "type": kind, "score": _number(answer.get("score"), maximum=len(keys) - 1),
                "confidence": _number(answer.get("confidence")), "legend": legend,
                "probabilities": _probabilities(answer.get("probabilities"), keys, levels=True),
            }
        elif kind == "choice":
            keys = set(question["criteria"])
            choice = answer.get("choice")
            if type(choice) is not str or choice not in keys:
                raise AnalysisError("unexpected_choice")
            validated[question_id] = {
                "type": kind, "choice": choice,
                "confidence": _number(answer.get("confidence")),
                "probabilities": _probabilities(answer.get("probabilities"), keys),
            }
        else:
            raise AnalysisError("unsupported_question_type")
    usage = {}
    if raw.get("usage") is not None:
        raw_usage = _mapping(raw["usage"])
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if key not in raw_usage:
                continue
            value = raw_usage[key]
            if type(value) is not int or value < 0:
                raise AnalysisError("invalid_token_usage")
            usage[key] = value
    return {
        "answers": validated,
        # Missing returned model stays unknown; never resolve an alias ourselves.
        "model": _metadata(raw.get("model")),
        "request_id": _metadata(raw.get("request_id")),
        "usage": usage,
    }
