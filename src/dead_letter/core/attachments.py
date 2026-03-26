"""Attachment helpers for MIME parsing stage."""

from __future__ import annotations

import base64
import re
from typing import Any

from dead_letter.core.types import AttachmentPart


def _sanitize_attachment_filename(raw_name: object, *, fallback: str = "attachment") -> str:
    name = str(raw_name or "").strip().replace("\x00", "")
    if not name:
        return ""

    parts = [part for part in re.split(r"[\\/]+", name) if part not in {"", ".", ".."}]
    if not parts:
        return fallback
    return parts[-1]


def collect_attachment_names(raw_attachments: list[dict[str, Any]]) -> list[str]:
    """Extract non-empty attachment filenames in source order."""
    names: list[str] = []
    for part in raw_attachments:
        name = _sanitize_attachment_filename(part.get("filename"))
        if name:
            names.append(name)
    return names


def collect_inline_cid_map(raw_attachments: list[dict[str, Any]]) -> dict[str, str]:
    """Build CID -> filename mapping for inline attachments."""
    mapping: dict[str, str] = {}
    for part in raw_attachments:
        filename = _sanitize_attachment_filename(part.get("filename"))
        content_id = str(part.get("content-id") or "").strip().strip("<>")
        if filename and content_id:
            mapping[content_id] = filename
    return mapping


def collect_inline_cid_data_uris(raw_attachments: list[dict[str, Any]]) -> dict[str, str]:
    """Build CID -> data URI mapping for inline attachments with base64 payloads."""
    mapping: dict[str, str] = {}
    for part in raw_attachments:
        content_id = str(part.get("content-id") or "").strip().strip("<>")
        content_type = str(part.get("mail_content_type") or "").strip().lower()
        payload = str(part.get("payload") or "").strip()
        transfer_encoding = str(part.get("content_transfer_encoding") or "").strip().lower()

        if not content_id or not content_type or not payload:
            continue
        if transfer_encoding != "base64":
            continue

        mapping[content_id] = f"data:{content_type};base64,{payload}"

    return mapping


def collect_attachment_parts(raw_attachments: list[dict[str, Any]]) -> list[AttachmentPart]:
    """Decode raw attachment payloads for bundle-writing workflows."""
    parts: list[AttachmentPart] = []

    for part in raw_attachments:
        filename = _sanitize_attachment_filename(part.get("filename"))
        payload = str(part.get("payload") or "")
        if not filename or not payload:
            continue

        transfer_encoding = str(part.get("content_transfer_encoding") or "").strip().lower()
        charset = str(part.get("charset") or "utf-8").strip() or "utf-8"

        if transfer_encoding == "base64":
            try:
                decoded = base64.b64decode(payload)
            except Exception:
                continue
        else:
            decoded = payload.encode(charset, errors="replace")

        parts.append(
            AttachmentPart(
                filename=filename,
                content_type=str(part.get("mail_content_type") or "").strip().lower(),
                payload=decoded,
                content_id=str(part.get("content-id") or "").strip().strip("<>") or None,
                disposition=str(part.get("content-disposition") or "").strip().lower() or "attachment",
            )
        )

    return parts


def extract_calendar_parts(raw_attachments: list[dict[str, Any]]) -> list[str]:
    """Decode text/calendar attachment payloads as UTF-8 strings."""
    parts: list[str] = []
    for part in raw_attachments:
        content_type = str(part.get("mail_content_type") or "").lower()
        if "text/calendar" not in content_type:
            continue

        payload = str(part.get("payload") or "")
        transfer_encoding = str(part.get("content_transfer_encoding") or "").lower()

        if transfer_encoding == "base64":
            try:
                decoded = base64.b64decode(payload).decode("utf-8", errors="replace")
            except Exception:
                continue
            parts.append(decoded)
            continue

        if payload:
            parts.append(payload)

    return parts
