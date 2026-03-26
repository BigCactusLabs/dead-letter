"""Plain-text conversation segmentation fallback."""

from __future__ import annotations

import re
import warnings

from mailparser_reply import EmailReplyParser

from dead_letter.core.conversation import ConversationResult
from dead_letter.core.types import ConversationZone, ZoneKind

_FORWARD_MARKER_RE = re.compile(
    r"(?im)^(?:-+\s*Forwarded message\s*-+|Begin forwarded message:)\s*$"
)


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

    parser = EmailReplyParser()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="'count' is passed as positional argument",
            category=DeprecationWarning,
        )
        message = parser.read(source)

    zones: list[ConversationZone] = []

    if message.replies:
        body = str(message.replies[0].content or "").strip()
        if body:
            zones.append(
                ConversationZone(
                    kind=ZoneKind.BODY,
                    content=body,
                    source_kind="plain",
                    confidence=0.8,
                )
            )

        for reply in message.replies[1:]:
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
