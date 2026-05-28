"""Markdown rendering helpers for the final pipeline stage."""

from __future__ import annotations

import html
import re

import yaml

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
        sections = _build_thread_sections(threaded, opts)
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
        if not zone.content.strip():
            continue
        sections.append(_render_thread_section(zone))
    if opts.thread_order is ThreadOrder.OLDEST_FIRST:
        sections = list(reversed(sections))
    return sections


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
    # while neutralizing raw HTML in normal prose.
    rendered: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    for line in value.splitlines(keepends=True):
        if in_fence:
            rendered.append(line)
            if _is_closing_fence(line, fence_char=fence_char, fence_len=fence_len):
                in_fence = False
            continue

        line_body = line.rstrip("\r\n")
        opener = _FENCE_OPEN_RE.match(line_body)
        if opener:
            fence = opener.group(2)
            fence_char = fence[0]
            fence_len = len(fence)
            in_fence = True
            newline = line[len(line_body):]
            rendered.append(
                f"{opener.group(1)}{fence}{html.escape(opener.group(3), quote=False)}{newline}"
            )
            continue

        if line.startswith(("    ", "\t")):
            rendered.append(line)
            continue

        rendered.append(_escape_html_outside_code_spans(line))

    return "".join(rendered)


def _is_closing_fence(line: str, *, fence_char: str, fence_len: int) -> bool:
    stripped = line.strip()
    return (
        len(stripped) >= fence_len
        and set(stripped) == {fence_char}
    )


def _escape_html_outside_code_spans(line: str) -> str:
    rendered: list[str] = []
    text_start = 0
    index = 0

    while index < len(line):
        if line[index] != "`":
            index += 1
            continue

        tick_end = index + 1
        while tick_end < len(line) and line[tick_end] == "`":
            tick_end += 1
        tick_run = line[index:tick_end]
        closing = line.find(tick_run, tick_end)
        if closing == -1:
            index = tick_end
            continue

        rendered.append(html.escape(line[text_start:index], quote=False))
        rendered.append(line[index:closing + len(tick_run)])
        index = closing + len(tick_run)
        text_start = index

    rendered.append(html.escape(line[text_start:], quote=False))
    return "".join(rendered)


def _section_header(zone: Zone) -> str:
    if zone.metadata.get("thread_render") == "degenerate":
        return "## Earlier in thread"
    from_ = _escaped_metadata(zone, "attribution_from")
    if not from_:
        return "## Earlier message"
    date = _escaped_metadata(zone, "attribution_date")
    subject = _escaped_metadata(zone, "attribution_subject")
    if date and subject:
        return f"## From {from_} ({date}) — {subject}"
    if date:
        return f"## From {from_} ({date})"
    if subject:
        return f"## From {from_} — {subject}"
    return f"## From {from_}"


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
