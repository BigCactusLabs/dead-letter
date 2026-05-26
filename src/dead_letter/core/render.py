"""Markdown rendering helpers for the final pipeline stage."""

from __future__ import annotations

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
        "source": str(parsed.source),
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

    head_lines = [
        zone.content.strip()
        for zone in threaded.zones
        if zone.kind is not ZoneKind.QUOTED and zone.content.strip()
    ]
    used_quoted_fallback = False
    if not head_lines:
        used_quoted_fallback = True
        # In STRUCTURED mode, restore the original (un-stripped) content from
        # the _quoted_original snapshot so output stays byte-identical to LATEST.
        head_lines = [
            (zone.metadata.get("_quoted_original") or zone.content).strip()
            for zone in threaded.zones
            if zone.kind is ZoneKind.QUOTED
            and (zone.metadata.get("_quoted_original") or zone.content).strip()
        ]

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
    body = zone.content.strip()
    if body:
        return f"{header}\n\n{body}"
    return header


def _section_header(zone: Zone) -> str:
    if zone.metadata.get("thread_render") == "degenerate":
        return "## Earlier in thread"
    from_ = zone.metadata.get("attribution_from")
    if not from_:
        return "## Earlier message"
    date = zone.metadata.get("attribution_date")
    subject = zone.metadata.get("attribution_subject")
    if date and subject:
        return f"## From {from_} ({date}) — {subject}"
    if date:
        return f"## From {from_} ({date})"
    if subject:
        return f"## From {from_} — {subject}"
    return f"## From {from_}"


def serialize_markdown(rendered: RenderedMarkdown) -> str:
    """Serialize a rendered markdown object to final document text."""
    yaml_block = yaml.safe_dump(rendered.front_matter, sort_keys=False, allow_unicode=True).strip()
    body = rendered.body.strip()

    if body:
        return f"---\n{yaml_block}\n---\n\n{body}\n"
    return f"---\n{yaml_block}\n---\n"
