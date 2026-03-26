from __future__ import annotations

from dead_letter.core.attachments import (
    collect_attachment_parts,
    collect_attachment_names,
    collect_inline_cid_data_uris,
    collect_inline_cid_map,
)


def test_collect_attachment_names_filters_missing_values() -> None:
    raw = [
        {"filename": "agenda.pdf"},
        {"filename": ""},
        {},
        {"filename": "notes.txt"},
    ]

    names = collect_attachment_names(raw)

    assert names == ["agenda.pdf", "notes.txt"]


def test_collect_inline_cid_map_normalizes_content_id() -> None:
    raw = [
        {"filename": "logo.png", "content-id": "<image1>"},
        {"filename": "chart.png", "content-id": "image2"},
        {"filename": "x.txt", "content-id": ""},
    ]

    cid_map = collect_inline_cid_map(raw)

    assert cid_map == {"image1": "logo.png", "image2": "chart.png"}


def test_collect_inline_cid_data_uris_builds_data_uri_map() -> None:
    raw = [
        {
            "filename": "logo.png",
            "content-id": "<image1>",
            "mail_content_type": "image/png",
            "payload": "AAAA",
            "content_transfer_encoding": "base64",
        },
        {
            "filename": "skip.txt",
            "content-id": "",
            "mail_content_type": "text/plain",
            "payload": "ZGF0YQ==",
            "content_transfer_encoding": "base64",
        },
    ]

    data_uris = collect_inline_cid_data_uris(raw)

    assert data_uris == {"image1": "data:image/png;base64,AAAA"}


def test_attachment_filename_helpers_strip_directory_segments() -> None:
    raw = [
        {"filename": "../../agenda.pdf"},
        {"filename": r"..\..\logo.png", "content-id": "<image1>"},
        {
            "filename": "nested/report.txt",
            "payload": "hello",
            "content_transfer_encoding": "7bit",
            "mail_content_type": "text/plain",
        },
    ]

    names = collect_attachment_names(raw)
    cid_map = collect_inline_cid_map(raw)
    parts = collect_attachment_parts(raw)

    assert names == ["agenda.pdf", "logo.png", "report.txt"]
    assert cid_map == {"image1": "logo.png"}
    assert [part.filename for part in parts] == ["report.txt"]
