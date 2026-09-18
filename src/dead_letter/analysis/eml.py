"""Offline EML-to-request facade. No remote client, credentials or file writers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from dead_letter.analysis.contracts import (
    DEFAULT_BASE_URL, DEFAULT_MODEL, AnalysisError, PreparedRequest, prepare_request,
)
from dead_letter.analysis.state import NormalizedMessage, Segment

if TYPE_CHECKING:
    from dead_letter.core.snapshot import EmailSnapshot

PROJECTION_VERSION = "core-zones-to-analysis-v1"
_KIND_MAP = {
    "body": "authored",
    "quoted": "quoted",
    "forwarded_body": "forwarded",
    "signature_candidate": "signature_candidate",
    "signature": "signature_candidate",
    "disclaimer_candidate": "disclaimer_candidate",
    "disclaimer": "disclaimer_candidate",
}


def message_from_snapshot(snapshot: EmailSnapshot) -> NormalizedMessage:
    """Project existing zone labels, never infer ownership from rendered Markdown.

    A forward marker is joined to its immediately following forwarded body so
    it cannot consume the only available parent-context slot by itself. Unknown
    authors stay unknown. Decorative zones are not semantic evidence.
    """
    segments = []
    forward_headers = []
    for zone in snapshot.zones:
        if zone.kind == "forward_header":
            forward_headers.append(zone)
            continue
        if forward_headers:
            if zone.kind == "forwarded_body":
                text = "\n\n".join([*(header.text for header in forward_headers), zone.text])
            else:
                segments.extend(Segment(header.id, "forwarded", header.text)
                                for header in forward_headers)
                text = zone.text
            forward_headers = []
        else:
            text = zone.text
        kind = _KIND_MAP.get(zone.kind)
        if kind is not None:
            segments.append(Segment(zone.id, kind, text, author=zone.author, sent_at=zone.sent_at))
        elif zone.kind != "decorative":
            # A new core zone type must not silently become an assessed absence.
            raise AnalysisError("unsupported_snapshot_zone")
    segments.extend(Segment(header.id, "forwarded", header.text) for header in forward_headers)
    return NormalizedMessage(
        subject=snapshot.subject, sender=snapshot.sender, sent_at=snapshot.sent_at,
        segments=tuple(segments), to=snapshot.to, cc=snapshot.cc,
        attachment_count=snapshot.attachment_count,
        reply_headers_present=snapshot.reply_headers_present,
        conversion_degraded=snapshot.diagnostics.get("state") != "normal",
        normalization_version=f"{PROJECTION_VERSION}:{snapshot.normalization_version}",
    )


@dataclass(frozen=True, slots=True)
class PreparedEmail:
    """Local provenance and the separately allowlisted effective request."""

    snapshot: EmailSnapshot = field(repr=False)
    request: PreparedRequest = field(repr=False)

    def preview(self, *, include_state: bool = False) -> dict:
        result = self.request.preview(include_state=include_state)
        result["source_sha256"] = self.snapshot.source_sha256
        result["source_size_bytes"] = self.snapshot.source_size_bytes
        result["normalization"] = self.snapshot.normalization
        result["projection_version"] = PROJECTION_VERSION
        if include_state:
            # Source references are local inspection only, never request fields.
            result["source_reference"] = self.snapshot.source.name
        return result


def prepare_eml(
    path: str | Path,
    *,
    profile_name: str = "triage-v1",
    focus_identity: tuple[str, ...] = (),
    max_context_segments: int = 3,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
) -> PreparedEmail:
    """Prepare, but never send, one EML's message-time analysis request.

    Environment variables are not read by this Python API. Paths/diagnostics
    remain in the local snapshot; only the selected text/metadata enter state.
    Request limits, identity, profile/model and endpoint validation are shared
    with the caller-supplied-evidence API. Source bytes are never modified.
    """
    from dead_letter.core.snapshot import SnapshotError, read_snapshot

    try:
        snapshot = read_snapshot(path)
        request = prepare_request(
            message_from_snapshot(snapshot), profile_name=profile_name,
            focus_identity=focus_identity, max_context_segments=max_context_segments,
            model=model, base_url=base_url,
        )
        return PreparedEmail(snapshot=snapshot, request=request)
    except SnapshotError as exc:
        raise AnalysisError(exc.code) from None
    except AnalysisError:
        raise
    except (TypeError, ValueError):
        raise AnalysisError("invalid_analysis_input") from None
