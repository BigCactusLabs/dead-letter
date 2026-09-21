"""Single-message analysis orchestration and versioned local result envelopes.

Source references and identity are local provenance. Raw state, full diagnostics,
credentials and SDK/HTTP objects are never included in a result envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dead_letter.analysis.contracts import DEFAULT_MODEL, AnalysisError
from dead_letter.analysis.eml import PreparedEmail, prepare_eml
from dead_letter.analysis.providers.typesafe import (
    ADAPTER_VERSION, TypeSafeConfig, TypeSafeProvider, emit_disclosure, preflight,
)

RESULT_SCHEMA_VERSION = 1
ASSESSMENT_POLICY = "experimental_review-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def analyze_prepared(
    prepared: PreparedEmail, *, allow_remote: bool = False,
    config: TypeSafeConfig | None = None,
    on_disclosure: Callable[[dict], None] = emit_disclosure,
    _provider: TypeSafeProvider | None = None,
) -> dict:
    """Execute one explicitly authorized prepared email; never mutate source mail.

    All successful candidate results suggest review because profile quality has
    not yet been evaluated. This policy is not a confidence threshold. It never
    translates missing context or failed execution into an assessed negative.
    """
    if allow_remote is not True:
        raise AnalysisError("remote_analysis_not_authorized")
    if not isinstance(prepared, PreparedEmail):
        raise AnalysisError("invalid_prepared_email")
    request = prepared.request
    state = request.payload()["state"]
    result = {
        "schema_version": RESULT_SCHEMA_VERSION, "artifact_type": "message_analysis",
        "experimental": True, "provider": "typesafe", "adapter_version": ADAPTER_VERSION,
        "source": {"reference": prepared.snapshot.source.name,
                   "sha256": prepared.snapshot.source_sha256,
                   "size_bytes": prepared.snapshot.source_size_bytes},
        "profile": {"name": request.profile.name, "revision": request.profile.revision,
                    "sha256": request.profile.sha256},
        "requested_model": request.model, "returned_model": None,
        "state_sha256": request.state_sha256, "request_fingerprint": request.fingerprint,
        "endpoint": request.base_url,
        "normalization": prepared.snapshot.normalization,
        "normalization_version": state["normalization_version"],
        "state_builder_version": state["state_builder_version"],
        "request_scope": state["request_scope"], "focus_identity": state["focus_identity"],
        "reference_time_policy": state["reference_time_policy"],
        "coverage": state["coverage"], "started_at": _now(), "evaluated_at": None,
        "execution_status": "skipped", "assessment_status": "insufficient_context",
        "assessment_policy": ASSESSMENT_POLICY, "answers": {}, "usage": {},
        "request_id": None, "attempts": [], "retry_count": 0,
        "billing_status": "not_attempted", "error_code": None,
    }
    if not state["coverage"]["authored_text_available"]:
        result["reason"] = "no_authored_text"
        return result
    provider = _provider or TypeSafeProvider(config)
    outcome = await provider.evaluate(request, allow_remote=True, on_disclosure=on_disclosure)
    for key in ("execution_status", "error_code", "attempts", "retry_count", "billing_status", "sdk_version"):
        result[key] = outcome[key]
    if outcome["execution_status"] == "succeeded":
        response = outcome["response"]
        result.update(answers=response["answers"], usage=response["usage"],
                      returned_model=response["model"], request_id=response["request_id"],
                      evaluated_at=_now(), assessment_status="review_suggested")
        # This is the explicit provider-native abstention category, not a
        # heuristic derived from a low Noul or missing attachment metadata.
        expectation = response["answers"].get("response_expectation", {})
        if expectation.get("choice") == "insufficient_context":
            result["assessment_status"] = "insufficient_context"
    else:
        result["assessment_status"] = None
    return result


async def analyze_eml(
    path: str | Path, *, provider: str, allow_remote: bool = False,
    profile_name: str = "triage-v1", focus_identity: tuple[str, ...] = (),
    max_context_segments: int = 3, model: str = DEFAULT_MODEL,
    config: TypeSafeConfig | None = None,
    on_disclosure: Callable[[dict], None] = emit_disclosure,
) -> dict:
    """Explicit async Python entry point. API keys come only from the environment.

    CLI provider selection supplies allow_remote=True; Python callers must do so
    themselves. Dry-run users should continue calling prepare_eml instead.
    """
    if provider != "typesafe":
        raise AnalysisError("unsupported_analysis_provider")
    preflight(allow_remote=allow_remote)
    effective = config or TypeSafeConfig.from_environment()
    prepared = prepare_eml(path, profile_name=profile_name, focus_identity=focus_identity,
                           max_context_segments=max_context_segments, model=model,
                           base_url=effective.base_url)
    return await analyze_prepared(prepared, allow_remote=True, config=effective,
                                  on_disclosure=on_disclosure)
