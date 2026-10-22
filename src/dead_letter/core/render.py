"""Markdown rendering helpers for the final pipeline stage."""

from __future__ import annotations

import html
import re

import yaml

from dead_letter.core.forwarding import parse_forward_headers
from dead_letter.core.types import (
    ConvertOptions,
    ParsedEmail,
    RenderedMarkdown,
    ThreadedContent,
    ThreadMode,
    ThreadOrder,
    Zone,
    ZoneKind,
)

_FENCE_OPEN_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
# Email autolinks pass through unescaped. The ASCII-only local part leaves no
# room for `/`, `=`, quotes, `%`, `?` or `#`, so even a renderer that read one
# as a tag would get an attribute-less element, and the mailto target matches
# the displayed address. URI autolinks stay escaped: outside autolink parsing
# they tokenize as a tag with attributes.
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_SAFE_AUTOLINK_RE = re.compile(rf"<[A-Za-z0-9._+-]+@{_LABEL}(?:\.{_LABEL})*>")
_BACKTICK_RUN_RE = re.compile(r"`+")
_FORWARD_KINDS = frozenset({ZoneKind.FORWARD_HEADER, ZoneKind.FORWARDED_BODY})
_SECTION_KINDS = _FORWARD_KINDS | {ZoneKind.QUOTED}
# A reply zone holding only Outlook's separator line, which mail-parser-reply
# splits off ahead of the From/Sent block it introduces.
_SEPARATOR_ONLY_RE = re.compile(r"\\?-{3,}[ \t]*Original Message[ \t]*-{3,}", re.IGNORECASE)


def render_markdown(
    parsed: ParsedEmail,
    threaded: ThreadedContent,
    *,
    attachment_files: list[str] | None = None,
    calendar_summaries: list[str] | None = None,
    include_all_headers: bool = False,
    include_raw_html: bool = False,
    raw_html: str | None = None,
    options: ConvertOptions | None = None,
) -> RenderedMarkdown:
    """Build front matter and markdown body from normalized pipeline outputs."""
    opts = options or ConvertOptions()
    front_matter: dict[str, object] = {
        # Basename only: the source .eml lives alongside the .md (sibling convert
        # or bundle/cabinet), so "which file" is all the provenance needed. The
        # absolute path was machine-specific dead weight (~28-31 tokens/email).
        "source": parsed.source.name,
        "subject": parsed.subject,
        "sender": parsed.sender,
        "date": parsed.date,
        "attachments": list(parsed.attachments),
    }

    if attachment_files:
        front_matter["attachment_files"] = list(attachment_files)

    if calendar_summaries:
        front_matter["calendar"] = list(calendar_summaries)

    if include_all_headers:
        front_matter["headers"] = dict(parsed.headers)

    if include_raw_html and raw_html is not None:
        front_matter["raw_html"] = raw_html

    head_lines = []
    for zone in threaded.zones:
        if zone.kind is ZoneKind.QUOTED or not zone.content.strip():
            continue
        head_lines.append(_render_zone_content(zone, zone.content))
    used_quoted_fallback = False
    if not head_lines:
        used_quoted_fallback = True
        # In STRUCTURED mode, restore the original (un-stripped) content from
        # the _quoted_original snapshot so output stays byte-identical to LATEST.
        head_lines = []
        for zone in threaded.zones:
            if zone.kind is not ZoneKind.QUOTED:
                continue
            content = zone.metadata.get("_quoted_original") or zone.content
            if content.strip():
                head_lines.append(_render_zone_content(zone, content))

    body = "\n\n".join(head_lines).strip()

    if opts.thread_mode is ThreadMode.STRUCTURED and not used_quoted_fallback:
        forward_sections = _build_forward_sections(threaded)
        if forward_sections:
            body = "\n\n".join(
                _render_zone_content(zone, zone.content)
                for zone in threaded.zones
                if zone.kind not in _SECTION_KINDS and zone.content.strip()
            ).strip()
        # thread_order reorders reply sections only; forwards keep document order.
        sections = [*forward_sections, *_build_thread_sections(threaded, opts)]
        if sections:
            front_matter["thread_messages"] = len(sections)
            body = "\n\n".join([body, *sections]).strip()

    return RenderedMarkdown(front_matter=front_matter, body=body)


def _build_thread_sections(threaded: ThreadedContent, opts: ConvertOptions) -> list[str]:
    quoted = [z for z in threaded.zones if z.kind is ZoneKind.QUOTED]
    if not quoted:
        return []
    sections: list[str] = []
    for zone in quoted:
        content = zone.content.strip()
        if not content or _SEPARATOR_ONLY_RE.fullmatch(content):
            continue
        sections.append(_render_thread_section(zone))
    if opts.thread_order is ThreadOrder.OLDEST_FIRST:
        sections = list(reversed(sections))
    return sections


def _build_forward_sections(threaded: ThreadedContent) -> list[str]:
    """One section per forwarded message, in document order.

    The marker (FORWARD_HEADER) is replaced by the section heading; a marker
    with no following body still yields a heading-only section.
    """
    sections: list[str] = []
    pending_marker = False
    for zone in threaded.zones:
        if zone.kind is ZoneKind.FORWARDED_BODY:
            sections.append(_render_forward_section(zone))
            pending_marker = False
            continue
        if pending_marker:
            sections.append(_FORWARD_FALLBACK_HEADER)
        pending_marker = zone.kind is ZoneKind.FORWARD_HEADER
    if pending_marker:
        sections.append(_FORWARD_FALLBACK_HEADER)
    return sections


def _render_forward_section(zone: Zone) -> str:
    header = _attribution_header(zone, lead="Forwarded from", key="forward")
    content = zone.content
    if header is None:
        header = _FORWARD_FALLBACK_HEADER
    else:
        # The heading carries From/Date/Subject; drop those lines only here so
        # zone content (and snapshot text) keeps the full header block.
        parsed = parse_forward_headers(content)
        if parsed is not None:
            content = parsed[1]
    body = _render_zone_content(zone, content)
    if body:
        return f"{header}\n\n{body}"
    return header


def _render_thread_section(zone: Zone) -> str:
    header = _section_header(zone)
    body = _render_zone_content(zone, zone.content)
    if body:
        return f"{header}\n\n{body}"
    return header


def _render_zone_content(zone: Zone, content: str) -> str:
    body = content.strip()
    if getattr(zone, "source_kind", "plain") == "plain":
        return _escape_plain_text_markdown_html(body)
    return body


def _escape_plain_text_markdown_html(value: str) -> str:
    # Markdown renderers already escape code contents; preserve those regions
    # while neutralizing raw HTML in normal prose. A region stays unescaped only
    # when every renderer must treat it as code; any ambiguity escapes it.
    rendered: list[str] = []
    prose: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    def flush_prose() -> None:
        rendered.extend(_escape_prose_block(prose))
        prose.clear()

    for line in value.splitlines(keepends=True):
        if in_fence:
            rendered.append(line)
            if _is_closing_fence(line, fence_char=fence_char, fence_len=fence_len):
                in_fence = False
            continue

        line_body = line.rstrip("\r\n")
        opener = _FENCE_OPEN_RE.match(line_body)
        if opener and not (opener.group(2)[0] == "`" and "`" in opener.group(3)):
            flush_prose()
            fence = opener.group(2)
            fence_char = fence[0]
            fence_len = len(fence)
            in_fence = True
            newline = line[len(line_body):]
            rendered.append(
                f"{opener.group(1)}{fence}{html.escape(opener.group(3), quote=False)}{newline}"
            )
            continue

        if not line_body.strip():
            flush_prose()
            rendered.append(line)
            continue

        prose.append(line)

    flush_prose()
    return "".join(rendered)


def _is_closing_fence(line: str, *, fence_char: str, fence_len: int) -> bool:
    stripped = line.strip()
    return (
        len(stripped) >= fence_len
        and set(stripped) == {fence_char}
    )


def _escape_prose_block(lines: list[str]) -> list[str]:
    # Code spans can cross lines inside a paragraph, and indentation means
    # code only in some block contexts. Keep spans only when every backtick
    # run in the block pairs, by exact length, within its own line.
    spans = [line_spans for line in lines if (line_spans := _line_code_spans(line)) is not None]
    if len(spans) != len(lines):
        return [_escape_html_preserving_autolinks(line) for line in lines]
    return [
        _escape_html_outside_spans(line, line_spans)
        for line, line_spans in zip(lines, spans, strict=True)
    ]


def _line_code_spans(line: str) -> list[tuple[int, int]] | None:
    runs = list(_BACKTICK_RUN_RE.finditer(line))
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(runs):
        opener = runs[index]
        if opener.start() > 0 and line[opener.start() - 1] == "\\":
            return None
        width = len(opener.group(0))
        closer = next(
            (later for later in range(index + 1, len(runs)) if len(runs[later].group(0)) == width),
            None,
        )
        if closer is None:
            return None
        spans.append((opener.start(), runs[closer].end()))
        index = closer + 1
    return spans


def _escape_html_outside_spans(line: str, spans: list[tuple[int, int]]) -> str:
    rendered: list[str] = []
    text_start = 0
    for start, end in spans:
        rendered.append(_escape_html_preserving_autolinks(line[text_start:start]))
        rendered.append(line[start:end])
        text_start = end
    rendered.append(_escape_html_preserving_autolinks(line[text_start:]))
    return "".join(rendered)


def _escape_html_preserving_autolinks(text: str) -> str:
    rendered: list[str] = []
    start = 0
    for match in _SAFE_AUTOLINK_RE.finditer(text):
        rendered.append(html.escape(text[start:match.start()], quote=False))
        rendered.append(match.group(0))
        start = match.end()
    rendered.append(html.escape(text[start:], quote=False))
    return "".join(rendered)


def _section_header(zone: Zone) -> str:
    if zone.metadata.get("thread_render") == "degenerate":
        return "## Earlier in thread"
    return _attribution_header(zone, lead="From", key="attribution") or "## Earlier message"


_FORWARD_FALLBACK_HEADER = "## Forwarded message"


def _attribution_header(zone: Zone, *, lead: str, key: str) -> str | None:
    from_ = _escaped_metadata(zone, f"{key}_from")
    if not from_:
        return None
    date = _escaped_metadata(zone, f"{key}_date")
    subject = _escaped_metadata(zone, f"{key}_subject")
    if date and subject:
        return f"## {lead} {from_} ({date}) — {subject}"
    if date:
        return f"## {lead} {from_} ({date})"
    if subject:
        return f"## {lead} {from_} — {subject}"
    return f"## {lead} {from_}"


def _escaped_metadata(zone: Zone, key: str) -> str | None:
    value = zone.metadata.get(key)
    if value is None:
        return None
    return _escape_plain_text_markdown_html(value)


def serialize_markdown(rendered: RenderedMarkdown) -> str:
    """Serialize a rendered markdown object to final document text."""
    yaml_block = yaml.safe_dump(rendered.front_matter, sort_keys=False, allow_unicode=True).strip()
    body = rendered.body.strip()

    if body:
        return f"---\n{yaml_block}\n---\n\n{body}\n"
    return f"---\n{yaml_block}\n---\n"
