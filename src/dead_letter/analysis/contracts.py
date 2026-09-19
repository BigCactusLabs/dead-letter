"""Offline request preparation, privacy-preserving previews and input identity."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from dead_letter.analysis.profiles import Profile, get_profile
from dead_letter.analysis.state import NormalizedMessage, build_state

REQUEST_SCHEMA_VERSION = 1
DEFAULT_MODEL = "jev-1.13.0"
DEFAULT_BASE_URL = "https://api.typesafe.ai"
# Local byte guards, NOT a JEV tokenizer or a guarantee about provider token use.
MAX_STATE_BYTES = 24_000
MAX_STATE_AND_QUESTION_BYTES = 28_000
MAX_REQUEST_BYTES = 48_000
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class AnalysisError(ValueError):
    """Safe machine-readable code only; never interpolate evidence or SDK errors."""

    def __init__(self, code: str) -> None:
        if not _IDENTIFIER.fullmatch(code):
            code = "invalid_analysis_error"
        self.code = code
        super().__init__(code)


def canonical_json(value: object) -> str:
    """Serialize strict JSON without key coercion, custom objects or non-finites."""
    def check(item: object, depth: int = 0) -> None:
        if depth > 32:
            raise AnalysisError("invalid_json_data")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise AnalysisError("invalid_json_data")
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise AnalysisError("invalid_json_data")
    try:
        check(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise AnalysisError("invalid_json_data") from None


def digest(value: str) -> str:
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise AnalysisError("invalid_json_data") from None


def validate_base_url(value: str) -> str:
    """Normalize an explicit endpoint; never consult email/profile/environment.

    HTTPS is required except for literal loopback addresses and localhost test
    servers. URL credentials, queries, fragments and ambiguous path forms are
    rejected. This validates configuration only; it opens no connection.
    """
    try:
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError
        if any(ord(char) < 33 or ord(char) == 127 for char in value):
            raise ValueError
        if any(char in value for char in ("\\", "%", "?", "#")):
            raise ValueError
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        hostname = parsed.hostname
        if not hostname or parsed.scheme not in {"https", "http"}:
            raise ValueError
        host = hostname.encode("idna").decode("ascii").lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            ):
                raise ValueError
            loopback = host == "localhost"
        else:
            loopback = address.is_loopback
            host = address.compressed
        if parsed.scheme == "http" and not loopback:
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_/~.\-]*", parsed.path):
            raise ValueError
        if "//" in parsed.path or any(part in {".", ".."} for part in parsed.path.split("/")):
            raise ValueError
        if ":" in host:
            host = f"[{host}]"
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            host += f":{port}"
        return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))
    except (ValueError, UnicodeError):
        raise AnalysisError("invalid_provider_endpoint") from None


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    """Immutable effective inputs. No API key, source path, client or SDK object."""

    profile: Profile
    model: str
    base_url: str
    state_json: str = field(repr=False)

    @property
    def state_sha256(self) -> str:
        return digest(self.state_json)

    @property
    def fingerprint(self) -> str:
        return digest(canonical_json({
            "schema_version": REQUEST_SCHEMA_VERSION,
            "provider": "typesafe", "base_url": self.base_url,
            "model": self.model, "profile_sha256": self.profile.sha256,
            "state_sha256": self.state_sha256,
        }))

    def payload(self) -> dict:
        """Explicit sensitive inspection/export; returned objects are detached."""
        return {
            "model": self.model, "state": json.loads(self.state_json),
            "questions": self.profile.questions(),
        }

    def preview(self, *, include_state: bool = False) -> dict:
        """Default preview includes no subject, body, addresses or source path."""
        state = json.loads(self.state_json)
        result = {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "execution_status": "skipped", "reason": "offline_preparation_only",
            "assessment_status": None, "remote_enabled": False,
            "provider": "typesafe", "destination_host": urlsplit(self.base_url).netloc,
            "requested_model": self.model,
            "profile": {
                "name": self.profile.name, "revision": self.profile.revision,
                "experimental": self.profile.experimental, "sha256": self.profile.sha256,
            },
            "request_scope": state["request_scope"],
            "focus_identity_configured": state["focus_identity"] is not None,
            "state_sha256": self.state_sha256, "request_fingerprint": self.fingerprint,
            "coverage": state["coverage"],
            "included_fields": [
                "subject", "sender", "to", "cc", "sent_at", "authored_segments",
                "selected_context_segments", "focus_identity_when_configured", "coverage",
            ],
            "excluded_fields": [
                "api_key", "source_path", "raw_html", "full_headers", "attachment_payloads",
                "attachment_text", "remote_images", "full_conversion_diagnostics",
            ],
            "limits": {
                "unit": "utf8_bytes_not_provider_tokens",
                "state_bytes": len(self.state_json.encode("utf-8")),
                "max_state_bytes": MAX_STATE_BYTES,
                "max_state_and_question_bytes": MAX_STATE_AND_QUESTION_BYTES,
                "max_request_bytes": MAX_REQUEST_BYTES,
            },
            "disclosure": (
                "This preview makes no network request. A future explicitly enabled "
                "provider invocation would send normalized email text and selected "
                "metadata to the configured host. No model-quality evaluation has run."
            ),
        }
        if include_state:
            result["state"] = state
        return result


def prepare_request(
    message: NormalizedMessage,
    *,
    profile_name: str = "triage-v1",
    focus_identity: tuple[str, ...] = (),
    max_context_segments: int = 3,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
) -> PreparedRequest:
    """Build a candidate request locally, with explicit limits and no truncation.

    NormalizedMessage is caller-supplied evidence, not a claim that EML snapshot
    integration already exists. Environment variables are deliberately not read.
    """
    if not isinstance(message, NormalizedMessage):
        raise AnalysisError("invalid_normalized_message")
    if not isinstance(model, str) or not _IDENTIFIER.fullmatch(model):
        raise AnalysisError("invalid_model_identifier")
    endpoint = validate_base_url(base_url)
    profile = get_profile(profile_name)
    state = build_state(message, focus_identity=focus_identity,
                        max_context_segments=max_context_segments)
    state_json = canonical_json(state)
    request = PreparedRequest(profile=profile, model=model,
                              base_url=endpoint, state_json=state_json)
    try:
        state_bytes = len(state_json.encode("utf-8"))
        longest = max(len(canonical_json(question).encode("utf-8"))
                      for question in profile.questions().values())
        total = len(canonical_json(request.payload()).encode("utf-8"))
    except UnicodeError:
        raise AnalysisError("invalid_json_data") from None
    if state_bytes > MAX_STATE_BYTES:
        raise AnalysisError("state_byte_limit_exceeded")
    if state_bytes + longest > MAX_STATE_AND_QUESTION_BYTES or total > MAX_REQUEST_BYTES:
        raise AnalysisError("request_byte_limit_exceeded")
    return request
