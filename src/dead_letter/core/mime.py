"""MIME parsing stage for .eml inputs."""

from __future__ import annotations

import base64
import binascii
import quopri
from email import policy
from email.message import Message
from email.parser import BytesParser
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
from dead_letter.core.slugs import slugify_subject
from dead_letter.core.types import ParsedEmail, PartDefect

_MAX_EMBEDDED_MESSAGE_SLUG_LENGTH = 100


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


def _attachment_parser_disagreement_defect(
    *,
    mailparser_count: int,
    stdlib_count: int,
) -> PartDefect:
    return PartDefect(
        part_id="root",
        code="attachment_parser_disagreement",
        message=(
            "mailparser extracted fewer named attachments than stdlib parser "
            f"(mailparser={mailparser_count}, stdlib={stdlib_count}); using stdlib attachments"
        ),
        severity="warning",
    )


def _extract_raw_attachments_from_stdlib(raw: bytes) -> list[dict[str, Any]]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    extracted: list[dict[str, Any]] = []

    for part in message.walk():
        if part.is_multipart():
            continue

        filename = str(part.get_filename() or "").strip()
        content_id = str(part.get("Content-ID") or "").strip().strip("<>")
        disposition = str(part.get_content_disposition() or "").strip().lower()

        if disposition != "attachment" and not filename and not content_id:
            continue

        charset = str(part.get_content_charset() or "utf-8").strip() or "utf-8"
        payload = part.get_payload(decode=True)
        if payload is None:
            raw_payload = part.get_payload()
            if isinstance(raw_payload, bytes):
                payload = raw_payload
            elif isinstance(raw_payload, str):
                try:
                    payload = raw_payload.encode(charset, errors="replace")
                except LookupError:
                    payload = raw_payload.encode("utf-8", errors="replace")
            else:
                payload = b""

        if not isinstance(payload, bytes):
            payload = b""

        encoded_payload = base64.b64encode(payload).decode("ascii") if payload else ""
        if not encoded_payload:
            continue

        extracted.append(
            {
                "filename": filename,
                "content-id": content_id,
                "mail_content_type": str(part.get_content_type() or "").strip().lower(),
                "content_transfer_encoding": "base64",
                "payload": encoded_payload,
                "content-disposition": str(part.get("Content-Disposition") or disposition).strip(),
                "charset": charset,
            }
        )

    return extracted


def _decode_text_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""

    charset = str(part.get_content_charset() or "utf-8").strip() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _is_body_text_part(part: Message) -> bool:
    if str(part.get_content_disposition() or "").strip().lower() == "attachment":
        return False
    if str(part.get_filename() or "").strip():
        return False
    return part.get_content_maintype() == "text" and part.get_content_subtype() in {
        "plain",
        "html",
    }


def _embedded_message_content(part: Message) -> tuple[bytes, Message | None]:
    """Recover an embedded message's original bytes and its parsed form.

    The stdlib parser nests message/rfc822 payloads without honoring the
    part's Content-Transfer-Encoding, so an encoded part surfaces a bogus
    inner message whose body is the still-encoded text. Decode that text to
    recover the original bytes instead of reserializing the bogus message.
    """
    payload = part.get_payload()
    inner = payload[0] if isinstance(payload, list) and payload else None
    if not isinstance(inner, Message):
        return b"", None

    cte = str(part.get("Content-Transfer-Encoding") or "").strip().lower()
    if cte in {"base64", "quoted-printable"} and not inner.is_multipart():
        encoded = inner.get_payload()
        if isinstance(encoded, str):
            raw_inner = b""
            try:
                if cte == "base64":
                    raw_inner = base64.b64decode(encoded, validate=False)
                else:
                    raw_inner = quopri.decodestring(encoded.encode("ascii", errors="replace"))
            except (ValueError, binascii.Error):
                raw_inner = b""
            if raw_inner:
                decoded_inner = BytesParser(policy=policy.default).parsebytes(raw_inner)
                return raw_inner, decoded_inner

    try:
        return inner.as_bytes(), inner
    except Exception:
        return b"", None


def _embedded_message_attachment(part: Message) -> dict[str, Any] | None:
    """Represent a message/rfc822 part as a raw attachment mapping."""
    raw_inner, inner = _embedded_message_content(part)
    if not raw_inner or inner is None:
        return None

    filename = str(part.get_filename() or "").strip()
    if not filename:
        subject = parse_subject(_normalize_header_value(inner.get("Subject", "")))
        slug = slugify_subject(subject, fallback="forwarded-message")
        slug = slug[:_MAX_EMBEDDED_MESSAGE_SLUG_LENGTH].rstrip("-") or "forwarded-message"
        filename = f"{slug}.eml"

    return {
        "filename": filename,
        "content-id": "",
        "mail_content_type": "message/rfc822",
        "content_transfer_encoding": "base64",
        "payload": base64.b64encode(raw_inner).decode("ascii"),
        "content-disposition": "attachment",
        "charset": "utf-8",
    }


def _walk_top_level_entity(
    part: Message,
    *,
    embedded: bool,
    plain_bodies: list[str],
    html_bodies: list[str],
    embedded_messages: list[Message],
) -> None:
    if part.get_content_type() == "message/rfc822":
        embedded_messages.append(part)
        return

    if part.is_multipart():
        nested = embedded or part.get_content_type() == "multipart/digest"
        for child in part.get_payload():
            if isinstance(child, Message):
                _walk_top_level_entity(
                    child,
                    embedded=nested,
                    plain_bodies=plain_bodies,
                    html_bodies=html_bodies,
                    embedded_messages=embedded_messages,
                )
        return

    if embedded or not _is_body_text_part(part):
        return

    content = _decode_text_part(part)
    if part.get_content_subtype() == "html":
        html_bodies.append(content)
    else:
        plain_bodies.append(content)


def _collect_top_level_entity(
    raw: bytes,
) -> tuple[bool, list[str], list[str], list[dict[str, Any]]]:
    """Collect body text scoped to the outer message plus its embedded messages."""
    message = BytesParser(policy=policy.default).parsebytes(raw)

    plain_bodies: list[str] = []
    html_bodies: list[str] = []
    embedded_messages: list[Message] = []
    _walk_top_level_entity(
        message,
        embedded=False,
        plain_bodies=plain_bodies,
        html_bodies=html_bodies,
        embedded_messages=embedded_messages,
    )

    embedded_attachments = [
        attachment
        for attachment in (_embedded_message_attachment(part) for part in embedded_messages)
        if attachment is not None
    ]
    return bool(embedded_messages), plain_bodies, html_bodies, embedded_attachments


def _merge_embedded_message_attachments(
    raw_attachments: list[dict[str, Any]],
    embedded_attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Substitute derived embedded-message entries in place, keeping MIME order.

    Parsers do surface message/rfc822 parts, but mailparser invents a random
    filename for them, so each is replaced by the derived entry at its original
    position; entries the parser missed are appended.
    """
    pending = list(embedded_attachments)
    merged: list[dict[str, Any]] = []

    for attachment in raw_attachments:
        content_type = str(attachment.get("mail_content_type") or "").strip().lower()
        if content_type != "message/rfc822":
            merged.append(attachment)
            continue
        if pending:
            merged.append(pending.pop(0))

    merged.extend(pending)
    return merged


def parse_eml(
    path: str | Path,
    *,
    include_attachment_payloads: bool = True,
    include_inline_data_uris: bool = True,
) -> ParsedEmail:
    """Parse a single .eml file into the pipeline ParsedEmail contract."""
    source = Path(path).resolve()
    raw = source.read_bytes()

    parsed = mailparser.parse_from_bytes(raw)

    subject = parse_subject(_normalize_header_value(parsed.subject))
    sender = _resolve_sender(parsed)

    date_value: str | None
    if parsed.date is not None:
        date_value = parsed.date.isoformat()
    else:
        headers = parsed.headers or {}
        date_value = parse_date(_normalize_header_value(headers.get("Date", "")))

    (
        has_embedded_messages,
        top_level_plain,
        top_level_html,
        embedded_attachments,
    ) = _collect_top_level_entity(raw)

    if has_embedded_messages:
        # mailparser flattens text parts across the whole tree, so an embedded
        # message/rfc822 would otherwise leak into (or replace) the outer body.
        text_body = "\n".join(top_level_plain)
        html_bodies = [body for body in top_level_html if body]
    else:
        text_body = "\n".join(parsed.text_plain or []) or parsed.body or ""
        html_bodies = [body for body in (parsed.text_html or []) if body]
    defects = _extract_part_defects(parsed)
    mime_model = build_mime_model(text_body=text_body, html_bodies=html_bodies, defects=defects)
    selected_candidate = select_body_candidate(mime_model) if mime_model.body_candidates else None
    html_body = selected_candidate.content if selected_candidate is not None and selected_candidate.kind == "html" else None
    selected_body_kind = selected_candidate.kind if selected_candidate is not None else None

    mailparser_attachments = list(parsed.attachments or [])
    stdlib_attachments = _extract_raw_attachments_from_stdlib(raw)

    mailparser_attachment_names = collect_attachment_names(mailparser_attachments)
    stdlib_attachment_names = collect_attachment_names(stdlib_attachments)
    mailparser_attachment_count = len(mailparser_attachment_names)
    stdlib_attachment_count = len(stdlib_attachment_names)

    if stdlib_attachment_count > mailparser_attachment_count:
        raw_attachments = stdlib_attachments
        defects.append(
            _attachment_parser_disagreement_defect(
                mailparser_count=mailparser_attachment_count,
                stdlib_count=stdlib_attachment_count,
            )
        )
    else:
        raw_attachments = mailparser_attachments

    if has_embedded_messages:
        raw_attachments = _merge_embedded_message_attachments(
            raw_attachments, embedded_attachments
        )

    return ParsedEmail(
        source=source,
        subject=subject,
        sender=sender,
        date=date_value,
        text_body=text_body or "",
        html_body=html_body,
        headers=_normalize_headers(parsed.headers or {}),
        attachments=collect_attachment_names(raw_attachments),
        attachment_parts=collect_attachment_parts(raw_attachments)
        if include_attachment_payloads
        else [],
        inline_cid_to_filename=collect_inline_cid_map(raw_attachments),
        inline_cid_to_data_uri=collect_inline_cid_data_uris(raw_attachments)
        if include_inline_data_uris
        else {},
        calendar_parts=extract_calendar_parts(raw_attachments),
        body_candidates=mime_model.body_candidates,
        selected_body_kind=selected_body_kind,
        defects=defects,
    )
