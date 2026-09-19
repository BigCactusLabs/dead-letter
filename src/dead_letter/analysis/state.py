"""Deterministic state assembly from already-normalized, caller-supplied evidence.

This module is not an EML parser and does not read files. The future core snapshot
adapter must supply these records from the existing normalization pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATE_BUILDER_VERSION = "normalized-evidence-v1"
CONTEXT_POLICY = "first-n-context-segments-v1"
_AUTHORED = {"authored", "signature_candidate", "disclaimer_candidate"}
_CONTEXT = {"quoted", "forwarded"}


def _text(value: object, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("invalid_normalized_evidence")


@dataclass(frozen=True, slots=True)
class Segment:
    """A stable normalized segment, never an offset into raw MIME bytes."""

    id: str
    kind: str
    text: str = field(repr=False)
    author: str | None = field(default=None, repr=False)
    sent_at: str | None = None

    def __post_init__(self) -> None:
        for value in (self.id, self.kind, self.text):
            _text(value)
        _text(self.author, optional=True)
        _text(self.sent_at, optional=True)
        if not self.id or len(self.id) > 128 or self.kind not in _AUTHORED | _CONTEXT:
            raise ValueError("invalid_normalized_segment")

    def as_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "text": self.text,
            "author": self.author, "sent_at": self.sent_at,
        }


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    """Text-only snapshot boundary. Attachment payloads/full headers have no field."""

    subject: str = field(repr=False)
    sender: str = field(repr=False)
    sent_at: str | None
    segments: tuple[Segment, ...] = field(repr=False)
    to: tuple[str, ...] = field(default=(), repr=False)
    cc: tuple[str, ...] = field(default=(), repr=False)
    attachment_count: int = 0
    reply_headers_present: bool = False
    conversion_degraded: bool = False
    # This identifies the caller's normalization implementation, not this builder.
    normalization_version: str = "unspecified"

    def __post_init__(self) -> None:
        for value in (self.subject, self.sender, self.normalization_version):
            _text(value)
        _text(self.sent_at, optional=True)
        # Freeze caller-owned lists as well as tuples.
        for name in ("segments", "to", "cc"):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)):
                raise ValueError("invalid_normalized_evidence")
            object.__setattr__(self, name, tuple(value))
        for value in (*self.to, *self.cc):
            _text(value)
        if any(not isinstance(segment, Segment) for segment in self.segments):
            raise ValueError("invalid_normalized_segment")
        if len({segment.id for segment in self.segments}) != len(self.segments):
            raise ValueError("duplicate_segment_id")
        if type(self.attachment_count) is not int or self.attachment_count < 0:
            raise ValueError("invalid_attachment_count")
        if any(type(value) is not bool for value in (
            self.reply_headers_present, self.conversion_degraded,
        )):
            raise ValueError("invalid_coverage")
        if not self.normalization_version.strip():
            raise ValueError("invalid_normalization_version")


def build_state(
    message: NormalizedMessage,
    *,
    focus_identity: tuple[str, ...] = (),
    max_context_segments: int = 3,
) -> dict:
    """Keep authored text intact; report bounded-context exclusions explicitly.

    Supplied context order is preserved, not claimed to be chronological. A
    caller should place the most relevant parent first. No date arithmetic,
    ownership inference, signature removal or semantic abstention occurs here.
    """
    if type(max_context_segments) is not int or not 0 <= max_context_segments <= 100:
        raise ValueError("invalid_context_limit")
    if not isinstance(focus_identity, (tuple, list)):
        raise ValueError("invalid_focus_identity")
    for alias in focus_identity:
        _text(alias)
        if not alias.strip():
            raise ValueError("invalid_focus_identity")
    aliases = sorted({alias.strip() for alias in focus_identity})
    authored = [segment for segment in message.segments if segment.kind in _AUTHORED]
    context = [segment for segment in message.segments if segment.kind in _CONTEXT]
    included = context[:max_context_segments]
    return {
        "state_builder_version": STATE_BUILDER_VERSION,
        "normalization_version": message.normalization_version,
        "reference_time_policy": "message_time_not_processing_time",
        "request_scope": "focus_identity" if aliases else "recipient_group",
        "focus_identity": {"aliases": aliases} if aliases else None,
        "message": {
            "subject": message.subject, "sender": message.sender,
            "to": list(message.to), "cc": list(message.cc), "sent_at": message.sent_at,
            "authored_segments": [segment.as_dict() for segment in authored],
        },
        "context": {"segments": [segment.as_dict() for segment in included]},
        "coverage": {
            "authored_text_available": any(segment.text.strip() for segment in authored),
            "thread_context_available": any(segment.text.strip() for segment in included),
            "external_thread_context_available": False,
            "reply_headers_present": message.reply_headers_present,
            "context_selection_policy": CONTEXT_POLICY,
            "context_segment_limit": max_context_segments,
            "context_segments_available": len(context),
            "context_segments_excluded": len(context) - len(included),
            "content_truncated": False,
            "attachment_count": message.attachment_count,
            "attachment_text_available": False,
            "conversion_degraded": message.conversion_degraded,
            "cleanup_policy": "preserve_normalized_text_and_signature_candidates-v1",
        },
    }
