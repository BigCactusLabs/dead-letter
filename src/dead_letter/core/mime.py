"""MIME parsing stage for .eml inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mailparser

from dead_letter.core.attachments import (
    collect_attachment_parts,
    collect_attachment_names,
    collect_inline_cid_data_uris,
    collect_inline_cid_map,
    extract_calendar_parts,
)
from dead_letter.core.header_parser import parse_date, parse_subject
from dead_letter.core.mime_selection import build_mime_model, select_body_candidate
from dead_letter.core.types import ParsedEmail, PartDefect


def _normalize_header_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, tuple):
                parts.append(" ".join(str(v) for v in item if v))
            else:
                parts.append(str(item))
        return ", ".join(part for part in parts if part)
    return str(value)


def _normalize_headers(raw_headers: dict[str, Any]) -> dict[str, str]:
    return {str(k): _normalize_header_value(v) for k, v in raw_headers.items()}


def _resolve_sender(parsed: mailparser.MailParser) -> str:
    if parsed.from_:
        display, address = parsed.from_[0]
        if address:
            return address
        if display:
            return display
    return "unknown"


def _extract_part_defects(parsed: mailparser.MailParser) -> list[PartDefect]:
    defects: list[PartDefect] = []
    for defect in getattr(parsed, "defects", []) or []:
        defects.append(
            PartDefect(
                part_id="root",
                code="mime_defect",
                message=str(defect),
                severity="warning",
            )
        )
    return defects


def parse_eml(path: str | Path) -> ParsedEmail:
    """Parse a single .eml file into the pipeline ParsedEmail contract."""
    source = Path(path).resolve()
    raw = source.read_bytes()

    parsed = mailparser.parse_from_bytes(raw)

    subject = parse_subject(parsed.subject)
    sender = _resolve_sender(parsed)

    date_value: str | None
    if parsed.date is not None:
        date_value = parsed.date.isoformat()
    else:
        headers = parsed.headers or {}
        date_value = parse_date(_normalize_header_value(headers.get("Date", "")))

    text_body = "\n".join(parsed.text_plain or []) or parsed.body or ""
    html_bodies = [body for body in (parsed.text_html or []) if body]
    defects = _extract_part_defects(parsed)
    mime_model = build_mime_model(text_body=text_body, html_bodies=html_bodies, defects=defects)
    selected_candidate = select_body_candidate(mime_model) if mime_model.body_candidates else None
    html_body = selected_candidate.content if selected_candidate is not None and selected_candidate.kind == "html" else None
    selected_body_kind = selected_candidate.kind if selected_candidate is not None else None

    raw_attachments = list(parsed.attachments or [])

    return ParsedEmail(
        source=source,
        subject=subject,
        sender=sender,
        date=date_value,
        text_body=text_body or "",
        html_body=html_body,
        headers=_normalize_headers(parsed.headers or {}),
        attachments=collect_attachment_names(raw_attachments),
        attachment_parts=collect_attachment_parts(raw_attachments),
        inline_cid_to_filename=collect_inline_cid_map(raw_attachments),
        inline_cid_to_data_uri=collect_inline_cid_data_uris(raw_attachments),
        calendar_parts=extract_calendar_parts(raw_attachments),
        body_candidates=mime_model.body_candidates,
        selected_body_kind=selected_body_kind,
        defects=defects,
    )
