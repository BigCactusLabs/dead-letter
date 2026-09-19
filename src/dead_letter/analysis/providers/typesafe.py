"""Explicit, environment-only BYOK execution with the pinned optional SDK.

No SDK import, credential lookup or network request occurs at module import.
Email/profile content cannot select an endpoint, transport, headers or tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from time import monotonic
from typing import Callable
from urllib.parse import urlsplit

from dead_letter.analysis.contracts import (
    DEFAULT_BASE_URL, MAX_REQUEST_BYTES, MAX_STATE_BYTES,
    MAX_STATE_AND_QUESTION_BYTES, AnalysisError, PreparedRequest,
    canonical_json, validate_base_url,
)
from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.responses import validate_response
from dead_letter.analysis.providers._logging import install_filters, private_provider_logs

SDK_VERSION = "0.7.0"
ADAPTER_VERSION = "typesafe-sdk-0.7.0-v1"
SYSTEM_ONE_PATH = "/v1/systemone"
MAX_RESPONSE_BYTES = 512_000
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")


@dataclass(frozen=True, slots=True)
class TypeSafeConfig:
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 15.0
    budget_seconds: float = 45.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", validate_base_url(self.base_url))
        for name in ("timeout_seconds", "budget_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 < value <= 300 or not math.isfinite(value):
                raise AnalysisError("invalid_provider_budget")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 3:
            raise AnalysisError("invalid_provider_retries")

    @classmethod
    def from_environment(cls, **options) -> TypeSafeConfig:
        """Endpoint configuration only; credentials are read after explicit opt-in."""
        return cls(base_url=os.environ.get("TYPESAFE_BASE_URL", DEFAULT_BASE_URL), **options)


def _api_key() -> str:
    value = os.environ.get("TYPESAFE_API_KEY", "")
    if not value or not value.strip():
        raise AnalysisError("typesafe_api_key_missing")
    if len(value) > 8192 or any(not 33 <= ord(char) <= 126 for char in value):
        raise AnalysisError("invalid_typesafe_api_key")
    return value


def preflight(*, allow_remote: bool) -> None:
    """Fail before reading EML or importing the SDK; a present key is not consent."""
    if allow_remote is not True:
        raise AnalysisError("remote_analysis_not_authorized")
    _api_key()
    try:
        installed = version("typesafe-sdk")
    except PackageNotFoundError:
        raise AnalysisError("typesafe_sdk_not_installed") from None
    if installed != SDK_VERSION:
        raise AnalysisError("unsupported_typesafe_sdk_version")


def disclosure(request: PreparedRequest) -> dict:
    state = request.payload()["state"]
    return {
        "event": "remote_analysis_disclosure", "provider": "typesafe",
        "destination_host": urlsplit(request.base_url).netloc,
        "request_scope": state["request_scope"],
        "profile": request.profile.name, "experimental": True,
        "notice": "Normalized email text and selected metadata will be sent to this host. "
                  "Analysis is not local processing and does not authorize mailbox actions.",
        "privacy_policy": "https://typesafe.ai/legal/privacy-policy",
    }


def emit_disclosure(value: dict) -> None:
    print(json.dumps(value, sort_keys=True), file=sys.stderr, flush=True)


def _validate_request(request: PreparedRequest, config: TypeSafeConfig) -> dict:
    try:
        if type(request) is not PreparedRequest or request.base_url != config.base_url:
            raise AnalysisError("provider_endpoint_mismatch")
        # Only the two versioned built-ins are currently executable. No dynamic
        # profiles, SDK extra_body, environment templates or provider overrides.
        if request.profile.sha256 != get_profile(request.profile.name).sha256:
            raise AnalysisError("unsupported_analysis_profile")
        payload = request.payload()
        state = payload["state"]
        if state["request_scope"] not in {"recipient_group", "focus_identity"}:
            raise AnalysisError("invalid_analysis_input")
        state_bytes = len(canonical_json(state).encode("utf-8"))
        longest = max(len(canonical_json(q).encode("utf-8"))
                      for q in payload["questions"].values())
        if state_bytes > MAX_STATE_BYTES:
            raise AnalysisError("state_byte_limit_exceeded")
        if state_bytes + longest > MAX_STATE_AND_QUESTION_BYTES or len(
            canonical_json(payload).encode("utf-8")
        ) > MAX_REQUEST_BYTES:
            raise AnalysisError("request_byte_limit_exceeded")
        return payload
    except AnalysisError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise AnalysisError("invalid_analysis_input") from None


def _safe_id(value: object, key: str) -> str | None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or key in value:
        return None
    return value


def _strict_json(data: bytes) -> None:
    """Reject duplicate answer keys and non-JSON numbers before SDK decoding."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def constant(value):
        raise ValueError
    try:
        json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise AnalysisError("invalid_provider_response") from None


def _guarded_http_client(httpx, config, request, key, attempts, transport):
    """Use public client injection, streaming reads and a single exact URL.

    Decompressed response bytes are bounded. Cookies and redirect locations are
    not passed back into the SDK. No raw response/request object leaves the adapter.
    """
    expected_url = config.base_url + SYSTEM_ONE_PATH

    class GuardedClient(httpx.AsyncClient):
        async def send(self, wire_request, **kwargs):
            if wire_request.method != "POST" or str(wire_request.url) != expected_url:
                raise AnalysisError("provider_destination_refused")
            wire = await wire_request.aread()
            if len(wire) > MAX_REQUEST_BYTES:
                raise AnalysisError("request_byte_limit_exceeded")
            attempt = {"number": len(attempts) + 1, "http_status": None,
                       "request_id": None, "wire_sha256": hashlib.sha256(wire).hexdigest(),
                       "status": "started", "duration_ms": 0}
            attempts.append(attempt)
            started = monotonic()
            response = None
            try:
                kwargs.update(stream=True, follow_redirects=False)
                response = await super().send(wire_request, **kwargs)
                attempt["http_status"] = response.status_code
                attempt["request_id"] = _safe_id(response.headers.get("x-typesafe-request-id"), key)
                if 300 <= response.status_code < 400:
                    raise AnalysisError("provider_redirect_refused")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise AnalysisError("provider_response_too_large")
                    chunks.append(chunk)
                body = b"".join(chunks)
                if 200 <= response.status_code < 300:
                    _strict_json(body)
                attempt["status"] = "response_received"
                headers = {name: response.headers[name] for name in (
                    "content-type", "retry-after", "retry-after-ms",
                ) if name in response.headers}
                if attempt["request_id"] is not None:
                    headers["x-typesafe-request-id"] = attempt["request_id"]
                # The bytes above are already decompressed; do not copy content-
                # encoding/length or arbitrary provider-controlled headers.
                return httpx.Response(response.status_code, headers=headers,
                                      content=body, request=wire_request)
            except BaseException:
                attempt["status"] = "interrupted_or_failed"
                raise
            finally:
                attempt["duration_ms"] = round((monotonic() - started) * 1000)
                if response is not None:
                    await response.aclose()

    return GuardedClient(timeout=config.timeout_seconds, trust_env=False,
                         follow_redirects=False, transport=transport)


def _failure_code(exc, sdk) -> str:
    if isinstance(exc, AnalysisError):
        return exc.code
    if isinstance(exc, sdk.TypeSafeAPIResponseValidationError):
        return "invalid_provider_response"
    if isinstance(exc, sdk.TypeSafeAPITimeoutError):
        return "provider_timeout"
    if isinstance(exc, sdk.TypeSafeAPIConnectionError):
        return "provider_connection_failed"
    if isinstance(exc, sdk.TypeSafeAPIError):
        return {400: "provider_bad_request", 401: "provider_authentication_failed",
                403: "provider_permission_denied", 404: "provider_not_found",
                422: "provider_validation_failed", 429: "provider_rate_limited"}.get(
                    exc.status, "provider_unavailable" if exc.status >= 500 else "provider_http_error")
    return "provider_failed"


class TypeSafeProvider:
    """One prepared message per call, SDK-owned retries, bounded wall-clock budget."""

    def __init__(self, config: TypeSafeConfig | None = None, *, _transport=None):
        self.config = config or TypeSafeConfig.from_environment()
        self._transport = _transport  # Trusted Python test injection, never CLI/email data.

    async def evaluate(
        self, request: PreparedRequest, *, allow_remote: bool = False,
        on_disclosure: Callable[[dict], None] = emit_disclosure,
    ) -> dict:
        preflight(allow_remote=allow_remote)
        payload = _validate_request(request, self.config)
        # Fail closed if the caller's disclosure sink fails. Read no email-derived
        # URLs and never use a callback supplied by email/profile data.
        try:
            on_disclosure(disclosure(request))
        except Exception:
            raise AnalysisError("remote_disclosure_failed") from None
        key = _api_key()
        attempts = []
        checked = None
        error_code = None
        with private_provider_logs():
            try:
                sdk = importlib.import_module("typesafe_sdk")
                httpx = importlib.import_module("httpx2")
                pydantic = importlib.import_module("pydantic")
                install_filters()
            except Exception:
                raise AnalysisError("typesafe_sdk_import_failed") from None
            # A custom strict model preserves unknown answer kinds for our own
            # completeness validator, instead of the SDK silently skipping them.
            projection = pydantic.create_model(
                "DeadLetterWireProjection",
                __config__=pydantic.ConfigDict(strict=True, extra="ignore"),
                answers=(dict, ...), model=(str | None, None), usage=(dict | None, None),
            )
            try:
                async with asyncio.timeout(self.config.budget_seconds):
                    async with _guarded_http_client(
                        httpx, self.config, request, key, attempts, self._transport,
                    ) as http_client:
                        # The SDK alone owns retries. No outer retry loop, no
                        # fallback model/provider, and no invisible environment proxy.
                        async with sdk.AsyncTypeSafeClient(
                            api_key=key, base_url=self.config.base_url, model=request.model,
                            http_client=http_client,
                            retry=sdk.RetryPolicy(max_retries=self.config.max_retries,
                                                  timeout=self.config.budget_seconds,
                                                  backoff_initial=0.25, backoff_max=2.0),
                        ) as client:
                            response = await client.system_one(
                                payload["state"], payload["questions"], response_model=projection,
                            )
                            raw = response.model_dump(exclude_none=True)
                            # Null usage means unknown, not zero or an invalid count.
                            if isinstance(raw.get("usage"), dict):
                                raw["usage"] = {k: v for k, v in raw["usage"].items() if v is not None}
                            raw["request_id"] = attempts[-1]["request_id"] if attempts else None
                            if isinstance(raw.get("model"), str) and key in raw["model"]:
                                raise AnalysisError("invalid_response_metadata")
                            checked = validate_response(request, raw)
            except TimeoutError:
                error_code = "provider_budget_exceeded"
            except Exception as exc:
                error_code = _failure_code(exc, sdk)
        return {
            "execution_status": "succeeded" if error_code is None else "failed",
            "response": checked if error_code is None else None,
            "error_code": error_code, "attempts": attempts,
            "retry_count": max(0, len(attempts) - 1),
            "billing_status": "unknown" if attempts else "not_attempted",
            "sdk_version": SDK_VERSION, "adapter_version": ADAPTER_VERSION,
        }
