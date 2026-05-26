"""Attribution-line parsing for quoted reply blocks."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

from dead_letter.core.types import ConversationZone, ConvertOptions, ThreadMode, ZoneKind

_LOGGER = logging.getLogger("dead_letter.core.attribution")


@dataclass(slots=True)
class AttributionMetadata:
    from_: str | None = None
    date: str | None = None
    subject: str | None = None


@dataclass(slots=True)
class AttributionMatch:
    metadata: AttributionMetadata
    consumed_end: int
    matched_text: str


_WHITESPACE_RE = re.compile(r"\s+")


# Apple Mail: "On Mar 5, 2026, at 10:23 AM, Alice <alice@example.com> wrote:"
_APPLE_MAIL = re.compile(
    r"^On (?P<date>[A-Za-z]{3}\s+\d{1,2},?\s+\d{4},?\s+at\s+[\d:]+\s*(?:AM|PM)?),\s+"
    r"(?P<from_>[^\n]+?(?:<[^>]+>)?)\s+wrote:\s*$",
    re.MULTILINE,
)

# Gmail short + line-wrapped:
# "On Thu, Mar 5, 2026 at 10:23 AM Alice <alice@example.com> wrote:"
# Allows the sender to span a newline (Gmail line-wraps long names).
_GMAIL_SHORT = re.compile(
    r"^On (?P<date>[A-Za-z]{3},?\s+[A-Za-z]{3}\s+\d{1,2},?\s+\d{4}(?:\s+at\s+[\d:]+\s*(?:AM|PM)?)?)\s+"
    r"(?P<from_>[^\n<]*(?:\n?[^\n<]*)*(?:<[^>]+>)?)\s+wrote:\s*$",
    re.MULTILINE,
)

# Outlook block — supports plain and markdown-bold projections. The label part
# of each line is wrapped in \*{0,2} so html_to_markdown's bold variant matches
# too. Allows up to 8 intermediate header lines (Cc:, Bcc:, Attachments:, etc.)
# between To: and Subject:.
_OUTLOOK_BLOCK = re.compile(
    r"^\*{0,2}From:\*{0,2}\s*(?P<from_>[^\n]+?)\s*\n"
    r"\*{0,2}Sent:\*{0,2}\s*(?P<date>[^\n]+?)\s*\n"
    r"\*{0,2}To:\*{0,2}\s*[^\n]+\n"
    r"(?:\*{0,2}[A-Z][A-Za-z-]+:\*{0,2}\s*[^\n]*\n){0,8}?"
    r"\*{0,2}Subject:\*{0,2}\s*(?P<subject>[^\n]+?)\s*\n",
    re.MULTILINE,
)

# German: "Am <date> schrieb <sender>:"
_GERMAN = re.compile(
    r"^Am\s+(?P<date>[^\n]+?)\s+schrieb\s+(?P<from_>[^\n]+?):\s*$",
    re.MULTILINE,
)

# French: "Le <date>, <sender> a écrit :"
# Date can contain commas (e.g., "jeu., 5 mars 2026 à 10:23"); the sender group
# disallows commas to anchor the split unambiguously.
_FRENCH = re.compile(
    r"^Le\s+(?P<date>.+?),\s+(?P<from_>[^,\n]+?(?:<[^>]+>)?)\s+a\s+écrit\s*:\s*$",
    re.MULTILINE,
)

# Spanish: "El <date>, <sender> escribió:"
_SPANISH = re.compile(
    r"^El\s+(?P<date>.+?),\s+(?P<from_>[^,\n]+?(?:<[^>]+>)?)\s+escribió:\s*$",
    re.MULTILINE,
)


_PATTERNS: list[re.Pattern[str]] = [
    _OUTLOOK_BLOCK,
    _APPLE_MAIL,
    _GMAIL_SHORT,
    _GERMAN,
    _FRENCH,
    _SPANISH,
]


def _normalize_field(value: str | None) -> str | None:
    """Collapse internal whitespace and strip — used for parsed metadata fields.

    Multi-line patterns can capture newlines inside named groups, which would
    render as broken markdown headings downstream.
    """
    if value is None:
        return None
    collapsed = _WHITESPACE_RE.sub(" ", value).strip()
    return collapsed or None


def parse_attribution_line(text: str) -> AttributionMatch | None:
    """Parse the leading attribution line(s) of a quoted reply."""
    if not text:
        return None
    for pattern in _PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        groups = match.groupdict()
        metadata = AttributionMetadata(
            from_=_normalize_field(groups.get("from_")),
            date=_normalize_field(groups.get("date")),
            subject=_normalize_field(groups.get("subject")),
        )
        end = match.end()
        while end < len(text) and text[end] == "\n":
            end += 1
        return AttributionMatch(metadata=metadata, consumed_end=end, matched_text=text[:end])
    _LOGGER.debug("attribution: no pattern matched for input prefix %r", text[:80])
    return None


_QUOTE_PREFIX_RE = re.compile(r"^(?:>\s?)+", re.MULTILINE)


def _strip_leading_quote_markers(text: str) -> str:
    """Remove all consecutive leading ``>`` markers from each line.

    mailparser_reply dedents one level per reply, but deeply nested chains
    still come back with multiple ``> `` prefixes. The ``(?:>\\s?)+`` group
    consumes every consecutive marker in one pass, so a single ``sub`` handles
    any nesting depth without a fixpoint loop.
    """
    return _QUOTE_PREFIX_RE.sub("", text)


def annotate_quoted_zones(
    zones: list[ConversationZone],
    options: ConvertOptions,
) -> list[ConversationZone]:
    """Parse attribution lines on QUOTED zones in STRUCTURED mode.

    Two-part fallback safety:
    1. If no non-QUOTED content exists, skip annotation entirely so the
       render layer's QUOTED-fallback path returns byte-identical output.
    2. Where annotation does run, ``_quoted_original`` snapshots the input
       content so the render fallback can restore it later.
    """
    if options.thread_mode is not ThreadMode.STRUCTURED:
        return list(zones)

    has_non_quoted_content = any(
        zone.kind is not ZoneKind.QUOTED and zone.content.strip()
        for zone in zones
    )
    if not has_non_quoted_content:
        return list(zones)

    out: list[ConversationZone] = []
    for zone in zones:
        if zone.kind is not ZoneKind.QUOTED:
            out.append(zone)
            continue
        normalized = _strip_leading_quote_markers(zone.content)
        match = parse_attribution_line(normalized)
        if match is None:
            if normalized != zone.content:
                new_meta = dict(zone.metadata)
                new_meta["_quoted_original"] = zone.content
                out.append(replace(zone, content=normalized, metadata=new_meta))
            else:
                out.append(zone)
            continue
        new_meta = dict(zone.metadata)
        new_meta["_quoted_original"] = zone.content
        if match.metadata.from_:
            new_meta["attribution_from"] = match.metadata.from_
        if match.metadata.date:
            new_meta["attribution_date"] = match.metadata.date
        if match.metadata.subject:
            new_meta["attribution_subject"] = match.metadata.subject
        new_content = normalized[match.consumed_end:].lstrip("\n")
        out.append(replace(zone, content=new_content, metadata=new_meta))
    return out
