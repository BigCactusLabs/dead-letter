"""Markdown rendering helpers for the final pipeline stage."""

from __future__ import annotations

from collections.abc import Iterable

import yaml

from dead_letter.core.types import ParsedEmail, RenderedMarkdown, ThreadedContent, ZoneKind


def render_markdown(
    parsed: ParsedEmail,
    threaded: ThreadedContent,
    *,
    attachment_files: list[str] | None = None,
    calendar_summaries: list[str] | None = None,
    include_all_headers: bool = False,
    include_raw_html: bool = False,
    raw_html: str | None = None,
) -> RenderedMarkdown:
    """Build front matter and markdown body from normalized pipeline outputs."""
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

    body_lines = [
        zone.content.strip()
        for zone in threaded.zones
        if zone.kind is not ZoneKind.QUOTED and zone.content.strip()
    ]
    if not body_lines:
        body_lines = [
            zone.content.strip()
            for zone in threaded.zones
            if zone.kind is ZoneKind.QUOTED and zone.content.strip()
        ]
    body = "\n\n".join(body_lines).strip()

    return RenderedMarkdown(front_matter=front_matter, body=body)


def serialize_markdown(rendered: RenderedMarkdown) -> str:
    """Serialize a rendered markdown object to final document text."""
    yaml_block = yaml.safe_dump(rendered.front_matter, sort_keys=False, allow_unicode=True).strip()
    body = rendered.body.strip()

    if body:
        return f"---\n{yaml_block}\n---\n\n{body}\n"
    return f"---\n{yaml_block}\n---\n"
