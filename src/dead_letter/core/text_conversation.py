"""Plain-text conversation segmentation fallback."""

from __future__ import annotations

import re
import warnings

from mailparser_reply import EmailReply, EmailReplyParser

from dead_letter.core.conversation import ConversationResult
from dead_letter.core.types import ConversationZone, ZoneKind

_FORWARD_MARKER_RE = re.compile(
    r"(?im)^(?:-+\s*Forwarded message\s*-+|Begin forwarded message:)\s*$"
)


def parse_email_replies(text: str) -> list[EmailReply]:
    """Run ``mailparser_reply.EmailReplyParser`` with the upstream deprecation
    filter applied. Returns the parsed ``replies`` list.

    Shared by Path A (``segment_text_conversation``) and Path B
    (``_pipeline._threaded_content_from_conversation``).
    """
    parser = EmailReplyParser()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="'count' is passed as positional argument",
            category=DeprecationWarning,
        )
        message = parser.read(text)
    return list(message.replies)


def _segment_forwarded_message(source: str) -> ConversationResult | None:
    match = _FORWARD_MARKER_RE.search(source)
    if match is None:
        return None

    before = source[: match.start()].strip()
    marker = match.group(0).strip()
    forwarded = source[match.end() :].strip()

    zones: list[ConversationZone] = []
    if before:
        zones.append(
            ConversationZone(
                kind=ZoneKind.BODY,
                content=before,
                source_kind="plain",
                confidence=0.8,
            )
        )

    zones.append(
        ConversationZone(
            kind=ZoneKind.FORWARD_HEADER,
            content=marker,
            source_kind="plain",
            confidence=0.9,
        )
    )

    if forwarded:
        zones.append(
            ConversationZone(
                kind=ZoneKind.FORWARDED_BODY,
                content=forwarded,
                source_kind="plain",
                confidence=0.85,
            )
        )

    return ConversationResult(zones=zones, client_hint="generic")


def segment_text_conversation(text: str) -> ConversationResult:
    """Split plain text into body and quoted conversation zones."""
    source = (text or "").strip()
    if not source:
        return ConversationResult(zones=[])

    forwarded = _segment_forwarded_message(source)
    if forwarded is not None:
        return forwarded

    replies = parse_email_replies(source)

    zones: list[ConversationZone] = []

    if replies:
        body = str(replies[0].content or "").strip()
        if body:
            zones.append(
                ConversationZone(
                    kind=ZoneKind.BODY,
                    content=body,
                    source_kind="plain",
                    confidence=0.8,
                )
            )

        for reply in replies[1:]:
            quoted = str(reply.content or "").strip()
            if quoted:
                zones.append(
                    ConversationZone(
                        kind=ZoneKind.QUOTED,
                        content=quoted,
                        source_kind="plain",
                        confidence=0.8,
                    )
                )
    else:
        zones.append(
            ConversationZone(
                kind=ZoneKind.BODY,
                content=source,
                source_kind="plain",
                confidence=0.7,
            )
        )

    return ConversationResult(
        zones=zones,
        client_hint="generic",
        fallback_used="plain_text_reply_parser",
    )
