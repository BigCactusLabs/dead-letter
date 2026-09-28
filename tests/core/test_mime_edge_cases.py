"""Regression coverage for malformed headers and attachment edge cases."""

from __future__ import annotations

import base64

import pytest

from dead_letter.core.attachments import (
    collect_attachment_parts,
    collect_inline_cid_data_uris,
)
from dead_letter.core.header_parser import parse_subject


@pytest.mark.parametrize("encoding", ["base64", "7bit", "8bit", ""])
def test_named_empty_attachment_is_not_missing_payload(encoding: str) -> None:
    parts = collect_attachment_parts([
        {
            "filename": "empty.txt",
            "payload": "",
            "content_transfer_encoding": encoding,
            "mail_content_type": "text/plain",
        },
    ])

    assert len(parts) == 1
    assert parts[0].filename == "empty.txt"
    assert parts[0].payload == b""


@pytest.mark.parametrize("metadata", [{}, {"payload": None}])
def test_missing_attachment_payload_is_not_invented(metadata: dict) -> None:
    assert collect_attachment_parts([{"filename": "metadata-only.txt", **metadata}]) == []


def test_whitespace_in_text_attachment_is_preserved() -> None:
    parts = collect_attachment_parts([
        {"filename": "spaces.txt", "payload": " \r\n\t", "content_transfer_encoding": "7bit"},
    ])
    assert parts[0].payload == b" \r\n\t"


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r\n\t", " "])
def test_folded_base64_becomes_single_line_data_uri(separator: str) -> None:
    payload = bytes(range(256))
    encoded = base64.b64encode(payload).decode("ascii")
    folded = separator.join(encoded[index:index + 76] for index in range(0, len(encoded), 76))
    raw = [{
        "filename": "chart.png",
        "content-id": "<chart>",
        "mail_content_type": "image/png",
        "content_transfer_encoding": "base64",
        "payload": folded,
    }]

    data_uri = collect_inline_cid_data_uris(raw)["chart"]

    assert data_uri == f"data:image/png;base64,{encoded}"
    assert base64.b64decode(data_uri.split(",", 1)[1], validate=True) == payload
    # URI normalization must not alter attachment extraction or mutate parser data.
    assert collect_attachment_parts(raw)[0].payload == payload
    assert raw[0]["payload"] == folded


def test_whitespace_only_inline_payload_is_not_embedded() -> None:
    assert collect_inline_cid_data_uris([{
        "content-id": "<empty>",
        "mail_content_type": "image/png",
        "content_transfer_encoding": "base64",
        "payload": " \r\n\t",
    }]) == {}


@pytest.mark.parametrize("charset", ["x-not-a-charset", "unknown-8bit", "base64_codec", "rot_13", "idna"])
def test_subject_with_unknown_or_nontext_charset_uses_utf8_fallback(charset: str) -> None:
    encoded = base64.b64encode("café".encode()).decode("ascii")
    assert parse_subject(f"=?{charset}?b?{encoded}?=") == "café"


def test_subject_fallback_preserves_adjacent_words() -> None:
    assert parse_subject("Before =?x-not-a-charset?q?caf=C3=A9?= after") == "Before café after"


def test_subject_with_malformed_base64_remains_readable() -> None:
    subject = "=?utf-8?b?A?="
    assert parse_subject(subject) == subject


def test_subject_fallback_replaces_undecodable_bytes() -> None:
    assert parse_subject("=?x-not-a-charset?b?/w==?=") == "\ufffd"


@pytest.mark.parametrize("subject, expected", [
    (None, ""),
    ("", ""),
    ("  Plain subject  ", "Plain subject"),
    ("=?iso-8859-1?q?caf=E9?=", "café"),
    ("=?utf-8?b?SGVsbG8gV29ybGQ=?=", "Hello World"),
])
def test_valid_subjects_keep_existing_behavior(subject: str | None, expected: str) -> None:
    assert parse_subject(subject) == expected
