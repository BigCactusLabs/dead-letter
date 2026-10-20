"""Plain-text conversation segmentation fallback."""

from __future__ import annotations

import re
import warnings

from mailparser_reply import EmailReply, EmailReplyParser

from dead_letter.core.conversation import ConversationResult
from dead_letter.core.forwarding import (
    FORWARD_MARKER_RE,
    followed_by_header_line,
    normalize_marker_text,
)
from dead_letter.core.types import ConversationZone, ZoneKind

# Outlook-style reply separator line, which opens a From/Sent header block.
_OUTLOOK_SEPARATOR_RE = re.compile(
    r"^[ \t]*(?:_{20,}|-{3,}[ \t]*Original Message[ \t]*-{3,})[ \t\r]*$", re.IGNORECASE
)
_OUTLOOK_HEADER_RE = re.compile(
    r"^[ \t]*\*{0,2}(?P<label>From|Sent|Date|To|Cc|Bcc|Subject|Reply-To):\*{0,2}",
    re.IGNORECASE,
)
# One ``>`` quote level, removed from the body of a quoted forward.
_QUOTE_LEVEL_RE = re.compile(r"(?m)^[ \t]*>[ \t]?")
# Upper bound on plain-text forward sections, matching the HTML segmenter.
_MAX_FORWARD_SECTIONS = 64
_INDENT_SENTINEL_PREFIX = "\ue000dead-letter-indent-"
_INDENT_SENTINEL_SUFFIX = "\ue001"


def _indentation_sentinel(text: str) -> str:
    index = 0
    while True:
        sentinel = f"{_INDENT_SENTINEL_PREFIX}{index}{_INDENT_SENTINEL_SUFFIX}"
        if sentinel not in text:
            return sentinel
        index += 1


def _protect_leading_indentation(text: str, *, sentinel: str) -> str:
    """Keep Markdown indentation intact across reply-parser normalization."""
    return "\n".join(
        f"{sentinel}{line}" if line.startswith((" ", "\t")) else line
        for line in text.split("\n")
    )


def parse_email_replies(text: str) -> list[EmailReply]:
    """Run ``mailparser_reply.EmailReplyParser`` with the upstream deprecation
    filter applied. Returns the parsed ``replies`` list.

    Shared by Path A (``segment_text_conversation``) and Path B
    (``_pipeline._threaded_content_from_conversation``).
    """
    parser = EmailReplyParser()
    sentinel = _indentation_sentinel(text)
    protected_text = _protect_leading_indentation(text, sentinel=sentinel)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="'count' is passed as positional argument",
            category=DeprecationWarning,
        )
        message = parser.read(protected_text)

    replies = list(message.replies)
    for reply in replies:
        reply.content = str(reply.content or "").replace(sentinel, "")
    return replies


def _outlook_block_ends_at(lines: list[str], last: int) -> bool:
    """True when ``lines[last]`` closes a separator + From/Sent header block."""
    first = last
    labels: set[str] = set()
    while first >= 0:
        match = _OUTLOOK_HEADER_RE.match(lines[first])
        if match is None:
            break
        labels.add(match.group("label").lower())
        first -= 1
    if "from" not in labels or not labels & {"sent", "date"}:
        return False
    while first >= 0 and not lines[first].strip():
        first -= 1
    return first >= 0 and _OUTLOOK_SEPARATOR_RE.match(lines[first]) is not None


def _separator_in_reply_history(prefix: str) -> bool:
    """True when a plain-text separator sits inside reply history.

    ``prefix`` is the text before the separator line. Walking back over blank
    and ``>``-quoted lines, the separator is reply history only when the
    line reached is a reply attribution mail-parser-reply recognizes
    ("On ... wrote:") or closes an Outlook ``____`` /
    ``-----Original Message-----`` + From/Sent header block. A ``>`` line or
    attribution-like line in the forwarder's own note, followed by ordinary
    text, does not count.
    """
    lines = prefix.split("\n")
    last = len(lines) - 1
    while last >= 0 and (not lines[last].strip() or lines[last].lstrip().startswith(">")):
        last -= 1
    if last < 0:
        return False
    # Outlook history is unquoted, so everything after its separator +
    # From/Sent block is reply history.
    for index, line in enumerate(lines):
        if _OUTLOOK_SEPARATOR_RE.match(line):
            block_end = index + 1
            while block_end < len(lines) and not lines[block_end].strip():
                block_end += 1
            while block_end < len(lines) and _OUTLOOK_HEADER_RE.match(lines[block_end]):
                block_end += 1
            if block_end > index + 1 and _outlook_block_ends_at(lines, block_end - 1):
                return True
    if _OUTLOOK_HEADER_RE.match(lines[last]):
        return _outlook_block_ends_at(lines, last)
    tail = lines[last].strip()
    if not tail.endswith((":", "：")):
        return False
    for reply in parse_email_replies(prefix)[1:]:
        header_lines = [line.strip() for line in str(reply.headers or "").splitlines() if line.strip()]
        if (
            header_lines
            and header_lines[-1] == tail
            and not _OUTLOOK_HEADER_RE.match(header_lines[0])
        ):
            return True
    return False


def _opens_quoted_forward(source: str, start: int) -> bool:
    """Accept a ``>``-quoted marker only where a quote block opens without an
    attribution line, as in Apple Mail's plain-text forwards.

    A quoted marker under ``On ... wrote:`` (or any line ending in a colon) or
    deeper inside a quote is reply history containing a forward, which keeps
    its reply handling.
    """
    end = start
    while end > 0:
        line_start = source.rfind("\n", 0, end - 1) + 1
        stripped = source[line_start:end].strip()
        end = line_start
        if stripped:
            return not stripped.startswith(">") and not stripped.endswith(":")
    return True


def _forward_matches(source: str, *, quoted_forwards: bool) -> list[re.Match[str]]:
    """Forward separator lines in ``source`` that are followed by a header line.

    A ``>``-quoted (Apple Mail) marker counts only with ``quoted_forwards``
    and where it opens a quote block (see ``_opens_quoted_forward``).
    """
    normalized = normalize_marker_text(source)
    return [
        match
        for match in FORWARD_MARKER_RE.finditer(normalized)
        if (
            not match.group("quote")
            or (quoted_forwards and _opens_quoted_forward(source, match.start()))
        )
        and followed_by_header_line(normalized, match.end(), quoted=bool(match.group("quote")))
    ]


def _forward_sections(source: str, matches: list[re.Match[str]]) -> list[tuple[str, str, bool]]:
    """``(marker, body, quoted)`` per match; a quoted body loses one ``>`` level."""
    sections: list[tuple[str, str, bool]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        forwarded = source[match.end() : end]
        marker = match.group(0).strip()
        quote = match.group("quote")
        if quote:
            forwarded = _QUOTE_LEVEL_RE.sub("", forwarded)
            marker = marker[len(quote) :].strip()
        sections.append((marker, forwarded.strip(), bool(quote)))
    return sections


def _split_nested_quoted_forwards(
    sections: list[tuple[str, str, bool]],
) -> list[tuple[str, str, bool]]:
    """Split forwards nested inside ``>``-quoted forwards into their own sections.

    Apple Mail quotes each nested plain-text forward one level deeper, so a
    nested marker only opens a quote block once the outer level is removed.
    Each quoted body is searched again until no nested forward remains or
    ``_MAX_FORWARD_SECTIONS`` is reached; anything not split stays in the
    enclosing section's body.
    """
    result: list[tuple[str, str, bool]] = []
    pending = list(reversed(sections))
    while pending:
        marker, body, quoted = pending.pop()
        if quoted and len(result) + len(pending) + 1 < _MAX_FORWARD_SECTIONS:
            nested = _forward_matches(body, quoted_forwards=True)
            if nested and not _separator_in_reply_history(body[: nested[0].start()]):
                result.append((marker, body[: nested[0].start()].strip(), quoted))
                pending.extend(reversed(_forward_sections(body, nested)))
                continue
        result.append((marker, body, quoted))
    return result


def _segment_forwarded_message(
    source: str, *, split_forwards: bool = False, quoted_forwards: bool = False
) -> ConversationResult | None:
    # Quoted (Apple Mail) markers are always split out for structured output;
    # latest mode takes them only when the caller asks (text/plain sources).
    matches = _forward_matches(source, quoted_forwards=quoted_forwards or split_forwards)
    if not matches or _separator_in_reply_history(source[: matches[0].start()]):
        return None
    if not split_forwards:
        # One header/body pair holding everything after the first marker keeps
        # latest-mode output byte-identical to the pre-split renderer.
        matches = matches[:1]

    before = source[: matches[0].start()].strip()

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

    sections = _forward_sections(source, matches)
    if split_forwards:
        sections = _split_nested_quoted_forwards(sections)
    for marker, forwarded, _quoted in sections:
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


def segment_text_conversation(
    text: str, *, split_forwards: bool = False, quoted_forwards: bool = False
) -> ConversationResult:
    """Split plain text into body and quoted conversation zones.

    With ``split_forwards`` each forward marker starts its own header/body
    zone pair; otherwise everything after the first marker is one pair.
    ``quoted_forwards`` also keeps a ``>``-quoted (Apple Mail) forward that
    opens a quote block when ``split_forwards`` is off.
    """
    source = (text or "").strip()
    if not source:
        return ConversationResult(zones=[])

    forwarded = _segment_forwarded_message(
        source, split_forwards=split_forwards, quoted_forwards=quoted_forwards
    )
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
