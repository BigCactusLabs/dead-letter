"""Plain-text conversation segmentation fallback."""

from __future__ import annotations

from collections.abc import Iterator
import re
import warnings

from mailparser_reply import EmailReply
from mailparser_reply.constants import GENERIC_MAIL_SEPARATOR, QUOTED_MATCH_INCLUDE
from mailparser_reply.parser import EmailMessage

from dead_letter.core.conversation import ConversationResult
from dead_letter.core.forwarding import (
    FORWARD_MARKER_RE,
    OUTLOOK_HEADER_LABELS,
    followed_by_header_line,
    normalize_marker_text,
)
from dead_letter.core.types import ConversationZone, ZoneKind

# In 1.36, .+?\s?.+? matches at least two non-newline characters, or two
# nonempty lines joined by one newline. Lazy branches keep the earliest end.
_WROTE_BODY = r"(?:[^\n]{2,}?|[^\n]+\n[^\n]+?)"
_WROTE_HEADER = (
    r"^(?!On[.\s]*On\s(" + _WROTE_BODY + r")\swrote:)("
    + QUOTED_MATCH_INCLUDE + r"On\s" + _WROTE_BODY + r"\s?wrote:)$"
)
# Each header iteration must end at a line end, or at a line start with an
# empty .* branch. Atomic runs keep that order without retrying star suffixes.
_FROM_HEADER = (
    r"((?:(?:^|\n|\n" + QUOTED_MATCH_INCLUDE
    + r")[* ]*(?:From|Sent|To|Subject|Date|Cc|Organization):"
    r"(?>[ *]*)(?:\s{,2})(?:(?>.*)|)){2,}(?:\n.*){,1})"
)
_LINEAR_HEADER_RE = re.compile(
    "|".join([_WROTE_HEADER, _FROM_HEADER, f"({GENERIC_MAIL_SEPARATOR})"]),
    re.MULTILINE | re.IGNORECASE,
)


class _BoundedEmailMessage(EmailMessage):
    def _process_signatures_disclaimers(self, text: str) -> tuple[list[str], str]:
        # Consumers use only content/headers, which do not depend on these
        # fields. Avoid upstream's quadratic signature/disclaimer searches.
        return [], ""


# Outlook-style reply separator line, which opens a From/Sent header block.
_OUTLOOK_SEPARATOR_RE = re.compile(
    r"^[ \t]*(?:_{20,}|-{3,}[ \t]*(?:Original Message|Ursprüngliche Nachricht|"
    r"Message d['’]origine|Mensaje original|Oorspronkelijk bericht)[ \t]*-{3,})[ \t\r]*$",
    re.IGNORECASE,
)
_OUTLOOK_HEADER_FIELDS = {
    label.casefold(): field
    for field, labels in OUTLOOK_HEADER_LABELS.items()
    for label in labels
}
# Locale labels come from the shared table; Outlook headers accept both colon forms.
_OUTLOOK_HEADER_RE = re.compile(
    r"^[ \t]*\*{0,2}(?P<label>"
    + "|".join(re.escape(label) for label in sorted(_OUTLOOK_HEADER_FIELDS, key=len, reverse=True))
    + r")\*{0,2}[ \t\u00a0]?[:：]\*{0,2}",
    re.IGNORECASE,
)
# mail-parser-reply 1.36 searches an unanchored signature alternative with
# ``\s*^``. A long whitespace run takes quadratic time even without a header.
# Its unanchored ``\n{2,} ?[_-]{32,}`` alternative also retries newline runs.
# Work accumulates across runs: 100 newline runs of length 1k took ~4.26 s,
# versus ~0.045 s for one. Budget squared lengths, not just the longest run.
# Runs up to 64 characters are cheap ordinary spacing (300 x 64 LF ~0.07 s).
# Check the indentation-protected input: sentinel-separated blank indented
# lines are fast, as are long prose lines with short runs between words.
_REPLY_PARSER_WHITESPACE_FLOOR = 64
_REPLY_PARSER_WHITESPACE_BUDGET = 1024 * 1024
_WHITESPACE_RUN_RE = re.compile(r"\s+")
# One ``>`` quote level, removed from the body of a quoted forward.
_QUOTE_LEVEL_RE = re.compile(r"(?m)^[ \t]*>[ \t]?")
# Upper bound on plain-text forward sections, matching the HTML segmenter.
_MAX_FORWARD_SECTIONS = 64
# Nested quoted forwards are found by searching each quoted body again; the
# characters searched across those searches stay within this multiple of the
# input length, or the floor when that is larger, so deep quoting costs linear
# time while ordinary chains still split fully.
_NESTED_SCAN_BUDGET_FACTOR = 4
_NESTED_SCAN_BUDGET_FLOOR = 1_000_000
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
    """Read content/headers with the bounded English reply parser.

    Plain-text callers also use ``_parse_text_replies`` with the existing
    whitespace-run guard as defense in depth.
    """
    return _read_email_replies(text, budget=None)


class _WhitespaceBudget:
    """Squared long-run whitespace work left for one message's parser calls."""

    def __init__(self) -> None:
        self.remaining = _REPLY_PARSER_WHITESPACE_BUDGET


def _read_email_replies(
    text: str, *, budget: _WhitespaceBudget | None,
) -> list[EmailReply]:
    sentinel = _indentation_sentinel(text)
    protected_text = _protect_leading_indentation(text, sentinel=sentinel)
    if budget is not None:
        whitespace_work = 0
        for match in _WHITESPACE_RUN_RE.finditer(protected_text):
            length = match.end() - match.start()
            if length > _REPLY_PARSER_WHITESPACE_FLOOR:
                whitespace_work += length * length
                if whitespace_work > budget.remaining:
                    return []
        budget.remaining -= whitespace_work
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="'count' is passed as positional argument",
            category=DeprecationWarning,
        )
        message = _BoundedEmailMessage(
            text=protected_text, languages=["en"], _header_regex=_LINEAR_HEADER_RE,
        ).read()

    replies = list(message.replies)
    for reply in replies:
        reply.content = str(reply.content or "").replace(sentinel, "")
    return replies


def _parse_text_replies(
    text: str, budget: _WhitespaceBudget | None = None,
) -> list[EmailReply]:
    """Bound the plain-text path, whose no-split fallback keeps the full body.

    Keep this policy separate from HTML's shared parser: its caller can try
    other splitters after no split, which need not retain an arbitrary prefix.
    Pass one budget to every call for a message so slices cannot each spend it.
    """
    return _read_email_replies(text, budget=budget or _WhitespaceBudget())


def _outlook_block_ends_at(lines: list[str], last: int) -> bool:
    """True when ``lines[last]`` closes a separator + From/Sent header block."""
    first = last
    labels: set[str] = set()
    while first >= 0:
        match = _OUTLOOK_HEADER_RE.match(lines[first])
        if match is None:
            break
        field = _OUTLOOK_HEADER_FIELDS.get(match.group("label").casefold())
        if field is None:
            return False
        labels.add(field)
        first -= 1
    if not {"from", "date"} <= labels:
        return False
    while first >= 0 and not lines[first].strip():
        first -= 1
    return first >= 0 and _OUTLOOK_SEPARATOR_RE.match(lines[first]) is not None


def _outlook_history_boundaries(
    source: str, *, column_zero: bool = False
) -> Iterator[tuple[int, int]]:
    """Offsets of each separator and qualified From/Date block.

    Structural splitting requires column zero; the older forward-history
    guard retains its existing indentation tolerance.
    """
    lines = source.split("\n")
    offset = 0
    for index, line in enumerate(lines):
        if _OUTLOOK_SEPARATOR_RE.match(line) and not (
            column_zero and line.startswith((" ", "\t"))
        ):
            block_start = index + 1
            while block_start < len(lines) and not lines[block_start].strip():
                block_start += 1
            block_end = block_start
            while block_end < len(lines) and _OUTLOOK_HEADER_RE.match(lines[block_end]):
                block_end += 1
            if (
                block_end > block_start
                and _outlook_block_ends_at(lines, block_end - 1)
                and not (
                    column_zero
                    and any(part.startswith((" ", "\t")) for part in lines[block_start:block_end])
                )
            ):
                header_offset = offset + sum(len(part) + 1 for part in lines[index:block_start])
                yield offset, header_offset
        offset += len(line) + 1


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
    if next(_outlook_history_boundaries(prefix), None) is not None:
        return True
    if _OUTLOOK_HEADER_RE.match(lines[last]):
        return _outlook_block_ends_at(lines, last)
    tail = lines[last].strip()
    if not tail.endswith((":", "：")):
        return False
    for reply in _parse_text_replies(prefix)[1:]:
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
    sections: list[tuple[str, str, bool]], *, scan_budget: int
) -> list[tuple[str, str, bool]]:
    """Split forwards nested inside ``>``-quoted forwards into their own sections.

    Apple Mail quotes each nested plain-text forward one level deeper, so a
    nested marker only opens a quote block once the outer level is removed.
    Each quoted body is searched again until no nested forward remains,
    ``_MAX_FORWARD_SECTIONS`` is reached, or the next search would take the
    characters searched past ``scan_budget``. Once the budget would be
    exceeded no further body is searched; anything not split stays in the
    enclosing section's body.
    """
    result: list[tuple[str, str, bool]] = []
    pending = list(reversed(sections))
    scanned = 0
    exhausted = False
    while pending:
        marker, body, quoted = pending.pop()
        remaining = _MAX_FORWARD_SECTIONS - (len(result) + len(pending) + 1)
        if quoted and remaining > 0 and not exhausted:
            if scanned + len(body) > scan_budget:
                # Budget spent: this and every later section keep their text.
                exhausted = True
            else:
                scanned += len(body)
                # Admit only as many nested forwards as the section cap allows;
                # the rest stay in the last admitted section's body, in order.
                nested = _forward_matches(body, quoted_forwards=True)[:remaining]
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
        sections = _split_nested_quoted_forwards(
            sections,
            scan_budget=max(_NESTED_SCAN_BUDGET_FACTOR * len(source), _NESTED_SCAN_BUDGET_FLOOR),
        )
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
    # Normalize before either forward segmentation or reply parsing sees the body.
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    source = normalized.strip()
    if not source:
        return ConversationResult(zones=[])

    forwarded = _segment_forwarded_message(
        source, split_forwards=split_forwards, quoted_forwards=quoted_forwards
    )
    if forwarded is not None:
        return forwarded

    history: list[EmailReply] = []
    # Check columns before outer whitespace trimming can erase indentation
    # from a separator at the very beginning of the message.
    leading = len(normalized) - len(normalized.lstrip())
    boundaries = [
        (separator - leading, header - leading)
        for separator, header in _outlook_history_boundaries(normalized, column_zero=True)
    ]
    authored = source[:boundaries[0][0]].strip() if boundaries else source
    # The latest message spends the shared budget first; history slices that
    # would exceed what remains stay raw QUOTED zones.
    budget = _WhitespaceBudget()
    replies = _parse_text_replies(authored, budget) if authored else []
    for index, (_separator_start, header_start) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(source)
        # Consume each structural separator, then retain existing reply
        # splitting within its block (On ... wrote: or bare header blocks).
        quoted = source[header_start:end]
        history.extend(_parse_text_replies(quoted, budget) or [EmailReply(content=quoted)])
    source = authored

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
    elif source:
        zones.append(
            ConversationZone(
                kind=ZoneKind.BODY,
                content=source,
                source_kind="plain",
                confidence=0.7,
            )
        )

    for reply in history:
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

    return ConversationResult(
        zones=zones,
        client_hint="generic",
        fallback_used="plain_text_reply_parser",
    )
