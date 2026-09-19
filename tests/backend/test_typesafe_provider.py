"""Actual pinned SDK + fake HTTP. No real key, endpoint call or quality claims.

Base installs may skip this optional suite. typesafe-contracts CI installs the
exact SDK and checks its import before running this file, so CI cannot skip it.
"""

from __future__ import annotations

import asyncio
import builtins
import io
import json
import logging
import os
import subprocess
import sys
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx2")
pytest.importorskip("typesafe_sdk")

from dead_letter.analysis import AnalysisError, NormalizedMessage, Segment, prepare_eml, prepare_request
from dead_letter.analysis.providers import typesafe as provider_module
from dead_letter.analysis.providers.typesafe import SDK_VERSION, TypeSafeConfig, TypeSafeProvider, preflight
from dead_letter.analysis.service import analyze_eml, analyze_prepared
from dead_letter.backend import cli

PRIVATE = "PRIVATE_EMAIL_BODY_28c5e"
KEY = "SYNTHETIC_KEY_NOT_REAL_5397a"


@pytest.fixture(autouse=True)
def fake_environment(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    monkeypatch.setenv("TYPESAFE_LOG_LEVEL", "debug")
    monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "must-not-override-pin")
    # A regression that honors implicit proxies will fail rather than reach a
    # live provider. MockTransport handles the explicitly configured endpoint.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")


def prepared_request(profile="triage-v1"):
    return prepare_request(NormalizedMessage(
        subject="Synthetic", sender="sender@example.test", sent_at="2001-01-02T10:30:00-05:00",
        to=("Quinn <quinn@example.test>",), normalization_version="synthetic-test-v1",
        segments=(Segment("body-1", "authored", "Review and reply. " + PRIVATE),),
    ), profile_name=profile)


def body_for(request):
    payload = json.loads(request.content)
    answers = {}
    for key, question in payload["questions"].items():
        if question["type"] == "noul":
            answers[key] = {"type": "noul", "noul": 0.01}
        elif question["type"] == "score":
            criteria = question["criteria"]
            legend = dict(enumerate(criteria)) if isinstance(criteria, list) else criteria
            answers[key] = {"type": "score", "score": 1.0, "confidence": 1.0,
                            "legend": legend, "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0}}
        else:
            answers[key] = {"type": "choice", "choice": "none", "confidence": 1.0,
                            "probabilities": {name: float(name == "none") for name in question["criteria"]}}
    return {"model": payload["model"], "usage": {"input_tokens": 123, "output_tokens": 10},
            "answers": answers}


def success(request):
    return httpx.Response(200, json=body_for(request), headers={"x-typesafe-request-id": "req-test-1"})


def evaluate(handler=success, *, request=None, config=None, on_disclosure=lambda value: None):
    provider = TypeSafeProvider(config or TypeSafeConfig(), _transport=httpx.MockTransport(handler))
    return asyncio.run(provider.evaluate(request or prepared_request(), allow_remote=True,
                                         on_disclosure=on_disclosure))


def test_sdk_version_is_the_real_pinned_distribution():
    assert provider_module.version("typesafe-sdk") == SDK_VERSION


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_present_key_is_not_consent(value):
    with pytest.raises(AnalysisError, match="^remote_analysis_not_authorized$"):
        preflight(allow_remote=value)


@pytest.mark.parametrize("value", ["", " ", "key with spaces", "bad\nkey", "é", "k" * 8193])
def test_invalid_keys_are_never_echoed(value, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", value)
    with pytest.raises(AnalysisError) as error:
        preflight(allow_remote=True)
    assert str(error.value) in {"typesafe_api_key_missing", "invalid_typesafe_api_key"}


def test_unreviewed_sdk_version_fails_closed(monkeypatch):
    monkeypatch.setattr(provider_module, "version", lambda name: "0.8.0")
    with pytest.raises(AnalysisError, match="^unsupported_typesafe_sdk_version$"):
        preflight(allow_remote=True)


@pytest.mark.parametrize("options", [
    {"max_retries": True}, {"max_retries": -1}, {"max_retries": 4},
    {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"budget_seconds": float("inf")}, {"budget_seconds": True},
    {"budget_seconds": 10 ** 1000},
])
def test_budgets_are_finite_and_bounded(options):
    with pytest.raises(AnalysisError):
        TypeSafeConfig(**options)


@pytest.mark.parametrize("profile", ["triage-v1", "triage-choice-v1"])
def test_real_sdk_serialization_and_allowlisted_native_result(profile):
    calls, disclosures = [], []
    def handler(request):
        calls.append(request)
        assert disclosures  # disclosure precedes sending private data
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["authorization"] == "Bearer " + KEY
        payload = json.loads(request.content)
        assert payload["model"] == "jev-1.13.0"
        assert PRIVATE in json.dumps(payload["state"])
        assert "raw_html" not in payload["state"]
        result = body_for(request)
        result["private_debug"] = PRIVATE + KEY
        return httpx.Response(200, json=result, headers={"x-typesafe-request-id": "req-test"})
    result = evaluate(handler, request=prepared_request(profile), on_disclosure=disclosures.append)
    assert result["execution_status"] == "succeeded"
    assert result["response"]["model"] == "jev-1.13.0"
    assert result["response"]["request_id"] == "req-test"
    assert result["response"]["usage"]["input_tokens"] == 123
    assert len(calls) == len(result["attempts"]) == 1
    assert result["billing_status"] == "unknown"
    assert result["retry_count"] == 0
    for item in (result, disclosures):
        assert PRIVATE not in json.dumps(item)
        assert KEY not in json.dumps(item)
    for answer in result["response"]["answers"].values():
        if answer["type"] == "noul":
            assert answer == {"type": "noul", "noul": 0.01}


def test_endpoint_configuration_and_disclosure_cannot_diverge():
    config = TypeSafeConfig(base_url="https://proxy.example.test/gateway")
    request = replace(prepared_request(), base_url=config.base_url)
    hosts = []
    def handler(wire):
        hosts.append(str(wire.url))
        return success(wire)
    notices = []
    assert evaluate(handler, request=request, config=config, on_disclosure=notices.append)["execution_status"] == "succeeded"
    assert hosts == ["https://proxy.example.test/gateway/v1/systemone"]
    assert notices[0]["destination_host"] == "proxy.example.test"
    with pytest.raises(AnalysisError, match="^provider_endpoint_mismatch$"):
        evaluate(handler, config=config)
    assert len(hosts) == 1


def test_disclosure_failure_prevents_http():
    def fail(value):
        raise RuntimeError(PRIVATE + KEY)
    with pytest.raises(AnalysisError, match="^remote_disclosure_failed$"):
        evaluate(lambda req: pytest.fail("HTTP after disclosure failure"), on_disclosure=fail)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", ["https://other.example.test/collect", "https://api.typesafe.ai/moved"])
def test_redirects_never_forward_auth_or_email(status, location):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={"location": location}, text=PRIVATE + KEY)
    result = evaluate(handler)
    assert result["error_code"] == "provider_redirect_refused"
    assert calls == ["https://api.typesafe.ai/v1/systemone"]
    assert KEY not in json.dumps(result) and PRIVATE not in json.dumps(result)


@pytest.mark.parametrize(("status", "code"), [
    (400, "provider_bad_request"), (401, "provider_authentication_failed"),
    (403, "provider_permission_denied"), (404, "provider_not_found"),
    (422, "provider_validation_failed"),
])
def test_nonretryable_errors_fail_fast_without_body(status, code):
    result = evaluate(lambda req: httpx.Response(status, text=PRIVATE + KEY))
    assert result["execution_status"] == "failed"
    assert result["response"] is None and result["error_code"] == code
    assert len(result["attempts"]) == 1
    assert PRIVATE not in json.dumps(result) and KEY not in json.dumps(result)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_sdk_retries_are_bounded_and_not_multiplied(status):
    result = evaluate(lambda req: httpx.Response(status, text=PRIVATE,
                      headers={"retry-after-ms": "0"}))
    assert result["execution_status"] == "failed"
    assert len(result["attempts"]) == 3
    assert result["retry_count"] == 2


def test_rate_limit_then_success_preserves_actual_attempts():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after-ms": "0"}, json={"error": PRIVATE})
        return success(request)
    result = evaluate(handler)
    assert result["execution_status"] == "succeeded"
    assert [attempt["http_status"] for attempt in result["attempts"]] == [429, 200]
    assert result["attempts"][0]["wire_sha256"] == result["attempts"][1]["wire_sha256"]
    assert result["response"]["usage"] == {"input_tokens": 123, "output_tokens": 10}


def test_long_retry_after_does_not_exceed_budget():
    result = evaluate(lambda req: httpx.Response(429, headers={"retry-after": "10000"}),
                      config=TypeSafeConfig(budget_seconds=0.1))
    assert len(result["attempts"]) == 1
    assert result["error_code"] in {"provider_budget_exceeded", "provider_rate_limited"}


@pytest.mark.parametrize("error_type", ["ConnectError", "ReadTimeout"])
def test_transport_errors_are_safe(error_type):
    def handler(request):
        raise getattr(httpx, error_type)(PRIVATE + KEY, request=request)
    result = evaluate(handler, config=TypeSafeConfig(max_retries=0))
    assert result["error_code"] in {"provider_connection_failed", "provider_timeout"}
    assert result["billing_status"] == "unknown"
    assert len(result["attempts"]) == 1
    assert PRIVATE not in json.dumps(result) and KEY not in json.dumps(result)


def test_wall_clock_budget_interrupts_a_hanging_transport():
    async def handler(request):
        await asyncio.sleep(10)
        return success(request)
    result = evaluate(handler, config=TypeSafeConfig(budget_seconds=0.02))
    assert result["error_code"] == "provider_budget_exceeded"
    assert len(result["attempts"]) == 1 and result["billing_status"] == "unknown"


def test_external_cancellation_propagates_and_closes_transport():
    closed = []
    class Transport(httpx.MockTransport):
        async def aclose(self):
            closed.append(True)
            await super().aclose()
    async def run():
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.sleep(10)
            return success(request)
        provider = TypeSafeProvider(_transport=Transport(handler))
        task = asyncio.create_task(provider.evaluate(prepared_request(), allow_remote=True,
                                                    on_disclosure=lambda value: None))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert closed


@pytest.mark.parametrize("mode", ["missing", "unknown_kind", "boolean", "contradictory_score", "extra"])
def test_raw_answer_validation_is_not_hidden_by_sdk(mode):
    def handler(request):
        body = body_for(request)
        if mode == "missing":
            del body["answers"]["reply_request_present"]
        elif mode == "unknown_kind":
            body["answers"]["reply_request_present"]["type"] = "future-answer"
        elif mode == "boolean":
            body["answers"]["reply_request_present"]["noul"] = False
        elif mode == "extra":
            body["answers"]["extra"] = {"type": "noul", "noul": 1}
        else:
            body["answers"]["expressed_urgency"]["score"] = 0
        return httpx.Response(200, json=body)
    result = evaluate(handler)
    assert result["execution_status"] == "failed" and result["response"] is None
    assert len(result["attempts"]) == 1


@pytest.mark.parametrize("content", [b"not json", b'{"answers":{},"answers":{}}', b'{"answers":{"x":NaN}}'])
def test_bad_json_fails_closed(content):
    assert evaluate(lambda req: httpx.Response(200, content=content))["error_code"] == "invalid_provider_response"


def test_response_payload_is_bounded():
    result = evaluate(lambda req: httpx.Response(200, content=b"x" * 512_001))
    assert result["error_code"] == "provider_response_too_large"


def test_missing_metadata_remains_unknown():
    def handler(request):
        return httpx.Response(200, json={"answers": body_for(request)["answers"],
                                      "usage": {"input_tokens": None, "output_tokens": None}})
    response = evaluate(handler)["response"]
    assert response["model"] is None and response["request_id"] is None and response["usage"] == {}


def test_key_echo_in_metadata_is_never_returned():
    def handler(request):
        body = body_for(request)
        body["model"] = KEY
        return httpx.Response(200, json=body, headers={"x-typesafe-request-id": KEY})
    result = evaluate(handler)
    assert result["error_code"] == "invalid_response_metadata"
    assert KEY not in json.dumps(result)


def test_debug_logs_with_direct_sdk_handler_cannot_expose_payload(capsys):
    output = io.StringIO()
    logger = logging.getLogger("typesafe_sdk")
    handler = logging.StreamHandler(output)
    old_level, old_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        assert evaluate()["execution_status"] == "succeeded"
        failed = evaluate(lambda req: httpx.Response(401, text=PRIVATE + KEY))
        assert failed["execution_status"] == "failed"
        assert output.getvalue() == ""
        logger.warning("unrelated-sdk-request-still-visible")
        assert "unrelated-sdk-request-still-visible" in output.getvalue()
        assert PRIVATE not in output.getvalue() and KEY not in output.getvalue()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def make_eml(tmp_path, body=PRIVATE):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["To"] = "quinn@example.test"
    message["Subject"] = "Synthetic integration"
    message["Date"] = "Tue, 02 Jan 2001 10:30:00 -0500"
    message.set_content(body)
    source = tmp_path / "synthetic.eml"
    source.write_bytes(message.as_bytes())
    return source


def test_service_envelope_is_versioned_and_source_remains_untouched(tmp_path):
    source = make_eml(tmp_path)
    before = source.read_bytes()
    prepared = prepare_eml(source, focus_identity=("quinn@example.test",))
    provider = TypeSafeProvider(_transport=httpx.MockTransport(success))
    result = asyncio.run(analyze_prepared(prepared, allow_remote=True, _provider=provider,
                                         on_disclosure=lambda value: None))
    assert result["execution_status"] == "succeeded"
    assert result["assessment_status"] == "review_suggested"
    assert result["assessment_policy"] == "experimental_review-v1"
    assert result["schema_version"] == 1 and result["evaluated_at"]
    assert result["source"]["sha256"] == prepared.snapshot.source_sha256
    assert result["focus_identity"]["aliases"] == ["quinn@example.test"]
    assert result["reference_time_policy"] == "message_time_not_processing_time"
    assert result["returned_model"] == "jev-1.13.0"
    assert PRIVATE not in json.dumps(result) and KEY not in json.dumps(result)
    assert source.read_bytes() == before and list(tmp_path.iterdir()) == [source]


def test_service_failure_is_not_an_assessed_negative(tmp_path):
    prepared = prepare_eml(make_eml(tmp_path))
    provider = TypeSafeProvider(_transport=httpx.MockTransport(lambda req: httpx.Response(401)))
    result = asyncio.run(analyze_prepared(prepared, allow_remote=True, _provider=provider,
                                         on_disclosure=lambda value: None))
    assert result["execution_status"] == "failed" and result["assessment_status"] is None
    assert result["answers"] == {} and result["evaluated_at"] is None


def test_no_authored_text_is_skipped_without_provider_call(tmp_path):
    source = make_eml(tmp_path, "")
    prepared = prepare_eml(source)
    provider = TypeSafeProvider(_transport=httpx.MockTransport(lambda req: pytest.fail("empty message sent")))
    result = asyncio.run(analyze_prepared(prepared, allow_remote=True, _provider=provider))
    assert result["execution_status"] == "skipped" and result["reason"] == "no_authored_text"
    assert result["assessment_status"] == "insufficient_context" and result["answers"] == {}
    assert result["attempts"] == []


def test_python_entry_requires_consent_before_source_read(monkeypatch):
    import dead_letter.analysis.service as service
    monkeypatch.setattr(service, "prepare_eml", lambda *a, **k: pytest.fail("read before consent"))
    with pytest.raises(AnalysisError, match="^remote_analysis_not_authorized$"):
        asyncio.run(analyze_eml("unread.eml", provider="typesafe"))


def test_cli_remote_stdout_is_result_and_stderr_is_disclosure(tmp_path, monkeypatch, capsys):
    original = provider_module._guarded_http_client
    def mocked(httpx, config, request, key, attempts, transport):
        return original(httpx, config, request, key, attempts, httpx.MockTransport(success))
    monkeypatch.setattr(provider_module, "_guarded_http_client", mocked)
    assert cli.main(["analyze", str(make_eml(tmp_path)), "--provider", "typesafe"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["execution_status"] == "succeeded"
    assert json.loads(captured.err)["destination_host"] == "api.typesafe.ai"
    assert PRIVATE not in captured.out + captured.err and KEY not in captured.out + captured.err


def test_convert_with_key_present_does_not_import_sdk(tmp_path, monkeypatch):
    original = builtins.__import__
    def guard(name, *args, **kwargs):
        if name.startswith("typesafe_sdk"):
            pytest.fail("conversion imported provider")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guard)
    assert cli.main(["convert", str(make_eml(tmp_path)), "--dry-run"]) == 0


def test_first_sdk_import_with_debug_environment_is_guarded():
    # A fresh interpreter is necessary: setting the variable after SDK import
    # would not exercise its import-time logging behavior.
    script = r'''
import asyncio, io, json, logging, sys
import httpx2
from dead_letter.analysis import NormalizedMessage, Segment, prepare_request
from dead_letter.analysis.providers.typesafe import TypeSafeProvider
assert "typesafe_sdk" not in sys.modules
stream = io.StringIO()
logging.basicConfig(level=logging.DEBUG, stream=stream)
request = prepare_request(NormalizedMessage(subject="Synthetic", sender="s@example.test",
    sent_at=None, segments=(Segment("s", "authored", "IMPORT_PRIVATE_SENTINEL"),)))
def handle(wire):
    return httpx2.Response(401, text="IMPORT_PRIVATE_SENTINEL SYNTHETIC_KEY_NOT_REAL_5397a")
result = asyncio.run(TypeSafeProvider(_transport=httpx2.MockTransport(handle)).evaluate(
    request, allow_remote=True, on_disclosure=lambda value: None))
assert result["error_code"] == "provider_authentication_failed"
assert "IMPORT_PRIVATE_SENTINEL" not in stream.getvalue()
assert "SYNTHETIC_KEY_NOT_REAL_5397a" not in stream.getvalue()
print("import-time logging guarded")
'''
    run = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         env={**os.environ, "TYPESAFE_API_KEY": KEY, "TYPESAFE_LOG_LEVEL": "debug"}, timeout=30)
    assert run.returncode == 0, run.stderr
    assert "import-time logging guarded" in run.stdout
