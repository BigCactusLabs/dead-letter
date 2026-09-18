"""Read-only snapshot regressions using synthetic EML, not model predictions."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
from dataclasses import FrozenInstanceError, asdict
from email.message import EmailMessage
from pathlib import Path

import pytest

from dead_letter.core import ConvertOptions
from dead_letter.core import _pipeline
from dead_letter.core.attribution import parse_attribution_line
from dead_letter.core.mime import parse_eml, parse_eml_bytes
from dead_letter.core.snapshot import MAX_SOURCE_BYTES, SnapshotError, read_snapshot
from dead_letter.core.types import ThreadMode

PRIVATE = "PRIVATE_SNAPSHOT_BODY_6d145"
ATTACHMENT = b"PRIVATE_ATTACHMENT_BYTES_a2991"


def write_eml(tmp_path, *, body=PRIVATE, html=False, attachment=False, reply=False):
    message = EmailMessage()
    message["From"] = "Current Author <current@example.com>"
    message["To"] = "Quinn <quinn@example.com>, Alex <alex@example.com>"
    message["Cc"] = "Observer <observer@example.com>"
    message["Subject"] = "Private snapshot subject"
    message["Date"] = "Tue, 02 Jan 2001 10:30:00 -0500"
    message["X-Private-Metadata"] = "NEVER_IN_STATE_f824"
    if reply:
        message["In-Reply-To"] = "<not-available@example.com>"
    message.set_content(body, subtype="html" if html else "plain")
    if attachment:
        message.add_attachment(ATTACHMENT, maintype="application", subtype="octet-stream",
                               filename="private-attachment.bin")
    path = tmp_path / "private-source.eml"
    path.write_bytes(message.as_bytes())
    return path


def test_snapshot_reads_exact_bytes_without_writes_or_network(tmp_path, monkeypatch):
    source = write_eml(tmp_path)
    before = source.read_bytes()
    files_before = sorted(tmp_path.iterdir())
    def forbidden(*args, **kwargs):
        pytest.fail("snapshot attempted network or conversion writer")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(_pipeline, "convert", forbidden)
    monkeypatch.setattr(_pipeline, "convert_to_bundle", forbidden)
    monkeypatch.setattr(_pipeline, "_write_attachment_parts", forbidden)
    snapshot = read_snapshot(source)
    assert snapshot.source_sha256 == hashlib.sha256(before).hexdigest()
    assert snapshot.source_size_bytes == len(before)
    assert source.read_bytes() == before
    assert sorted(tmp_path.iterdir()) == files_before
    assert PRIVATE in "\n".join(zone.text for zone in snapshot.zones)
    assert snapshot.sent_at.endswith("-0500")
    assert "2001" in snapshot.sent_at
    assert "quinn@example.com" in snapshot.to[0]
    assert "observer@example.com" in snapshot.cc[0]
    assert snapshot.diagnostics["state"] == "normal"


@pytest.mark.parametrize("html", [False, True])
@pytest.mark.parametrize("attachment", [False, True])
def test_file_and_bytes_parser_paths_are_equivalent(tmp_path, html, attachment):
    path = write_eml(tmp_path, body=f"<p>{PRIVATE}</p>" if html else PRIVATE,
                     html=html, attachment=attachment)
    from_file = parse_eml(path, include_attachment_payloads=False, include_inline_data_uris=False)
    from_bytes = parse_eml_bytes(path.read_bytes(), source=path,
                                include_attachment_payloads=False, include_inline_data_uris=False)
    assert asdict(from_bytes) == asdict(from_file)
    assert from_bytes.attachment_parts == []
    assert from_bytes.inline_cid_to_data_uri == {}


def test_bytes_parser_does_not_read_source_path(tmp_path, monkeypatch):
    path = write_eml(tmp_path)
    raw = path.read_bytes()
    path.unlink()
    def forbidden(*args, **kwargs):
        pytest.fail("bytes parser reopened the source")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    parsed = parse_eml_bytes(raw, source=path, include_attachment_payloads=False)
    assert parsed.subject == "Private snapshot subject"


@pytest.mark.parametrize("raw", ["Subject: text", bytearray(b"mutable"), None])
def test_bytes_parser_requires_immutable_bytes(tmp_path, raw):
    with pytest.raises(TypeError, match="^expected_eml_bytes$"):
        parse_eml_bytes(raw, source=tmp_path / "unread.eml")


def test_source_changes_cannot_change_the_bytes_bound_to_the_hash(tmp_path, monkeypatch):
    import dead_letter.core.snapshot as snapshots
    path = write_eml(tmp_path)
    original = path.read_bytes()
    calls = []
    original_parser = snapshots.parse_eml_bytes
    def changing_source(raw, **kwargs):
        calls.append(raw)
        path.write_bytes(b"From: other@example.com\nSubject: Changed\n\nChanged body")
        return original_parser(raw, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("pipeline reparsed the mutable file instead of supplied parsed bytes")
    monkeypatch.setattr(snapshots, "parse_eml_bytes", changing_source)
    monkeypatch.setattr(_pipeline, "parse_eml", forbidden)
    snapshot = read_snapshot(path)
    assert calls == [original]
    assert snapshot.source_sha256 == hashlib.sha256(original).hexdigest()
    assert snapshot.subject == "Private snapshot subject"
    assert PRIVATE in "\n".join(zone.text for zone in snapshot.zones)
    assert b"Changed body" in path.read_bytes()  # Only the simulated external writer changed it.


def test_snapshot_is_detached_and_repr_omits_private_data(tmp_path):
    path = write_eml(tmp_path)
    snapshot = read_snapshot(path)
    same = read_snapshot(path)
    assert snapshot == same
    assert [zone.id for zone in snapshot.zones] == [zone.id for zone in same.zones]
    with pytest.raises(FrozenInstanceError):
        snapshot.zones[0].text = "mutation"
    diagnostics = snapshot.diagnostics
    diagnostics["state"] = "mutation"
    normalization = snapshot.normalization
    normalization["runtime_packages"].clear()
    assert snapshot.diagnostics["state"] == "normal"
    assert snapshot.normalization["runtime_packages"]
    assert snapshot.normalization_version == same.normalization_version
    for private in (PRIVATE, "current@example.com", str(path), "Private snapshot subject"):
        assert private not in repr(snapshot)
        assert all(private not in repr(zone) for zone in snapshot.zones)


def test_snapshot_never_exports_attachment_or_calendar_text(tmp_path):
    path = write_eml(tmp_path, body="See attached.", attachment=True)
    snapshot = read_snapshot(path)
    serialized = json.dumps(asdict(snapshot), default=str)
    assert snapshot.attachment_count == 1
    assert ATTACHMENT.decode() not in serialized
    assert "NEVER_IN_STATE_f824" not in serialized
    assert not hasattr(snapshot, "attachment_parts")
    assert not hasattr(snapshot, "html_body")
    assert not hasattr(snapshot, "raw")


def test_latest_and_quoted_zones_keep_distinct_authors(tmp_path):
    path = write_eml(tmp_path, html=True, body='''
        <div>Done, thanks.</div>
        <div class="gmail_quote">
          <div>On Thu, Mar 5, 2026 at 10:23 AM Alice &lt;alice@example.com&gt; wrote:</div>
          <blockquote>Please pay the earlier invoice.</blockquote>
        </div>''')
    snapshot = read_snapshot(path)
    authored = [zone for zone in snapshot.zones if zone.kind == "body"]
    quoted = [zone for zone in snapshot.zones if zone.kind == "quoted"]
    assert any("Done, thanks." in zone.text for zone in authored)
    assert any("earlier invoice" in zone.text for zone in quoted)
    assert all("earlier invoice" not in zone.text for zone in authored)
    assert all("current@example.com" not in (zone.author or "") for zone in quoted)


def test_quote_only_snapshot_does_not_promote_render_fallback_to_authored(tmp_path):
    path = write_eml(tmp_path, html=True, body='''
        <div class="gmail_quote"><blockquote>Earlier author's request only.</blockquote></div>''')
    snapshot = read_snapshot(path)
    assert snapshot.zones
    assert all(zone.kind == "quoted" for zone in snapshot.zones)
    assert all(zone.author is None for zone in snapshot.zones)


def test_ps_request_is_preserved_near_signature(tmp_path):
    path = write_eml(tmp_path, body="A status update.\n\nThanks,\nQuinn\n\nP.S. Please send the revised draft.")
    snapshot = read_snapshot(path)
    assert "Please send the revised draft" in "\n".join(zone.text for zone in snapshot.zones)
    assert snapshot.normalization["options"]["strip_signatures"] is False
    assert snapshot.normalization["options"]["strip_disclaimers"] is False


def test_no_timezone_is_inferred_when_date_header_has_none(tmp_path):
    path = tmp_path / "no-timezone.eml"
    path.write_bytes(b"From: a@example.com\nDate: Tue, 02 Jan 2001 10:30:00\n\nHistorical request")
    snapshot = read_snapshot(path)
    assert snapshot.sent_at == "Tue, 02 Jan 2001 10:30:00"
    path.write_bytes(b"From: a@example.com\n\nNo date")
    assert read_snapshot(path).sent_at is None


@pytest.mark.parametrize("thread_mode", [ThreadMode.LATEST, ThreadMode.STRUCTURED])
def test_conversion_wrapper_preserves_snapshot_results(tmp_path, thread_mode):
    path = write_eml(tmp_path, body="Current message.\n\nOn Thu, Mar 5, 2026 at 10:23 AM Alice <alice@example.com> wrote:\n> Earlier message.")
    options = ConvertOptions(thread_mode=thread_mode)
    before = _pipeline._build_rendered_markdown(path, options, include_attachment_payloads=False)
    snapshot = _pipeline._build_pipeline_snapshot(path, options, include_attachment_payloads=False)
    read_snapshot(path)
    after = _pipeline._build_rendered_markdown(path, options, include_attachment_payloads=False)
    assert before == snapshot[:4] == after
    assert len(snapshot) == 5


def test_small_source_limit_fails_before_parser_without_echo(tmp_path, monkeypatch):
    import dead_letter.core.snapshot as snapshots
    path = write_eml(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("oversized source reached MIME parser")
    monkeypatch.setattr(snapshots, "parse_eml_bytes", forbidden)
    with pytest.raises(SnapshotError, match="^source_byte_limit_exceeded$"):
        read_snapshot(path, max_source_bytes=10)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "100", MAX_SOURCE_BYTES + 1])
def test_invalid_source_limits(tmp_path, limit):
    with pytest.raises(SnapshotError, match="^invalid_source_byte_limit$"):
        read_snapshot(tmp_path / "unread.eml", max_source_bytes=limit)


def test_missing_wrong_extension_and_directory_errors_are_safe(tmp_path):
    for path, code in ((tmp_path / "private-missing.eml", "source_not_found"),
                       (tmp_path / "private-wrong.txt", "expected_eml_file")):
        with pytest.raises(SnapshotError, match=f"^{code}$"):
            read_snapshot(path)
    directory = tmp_path / "directory.eml"
    directory.mkdir()
    with pytest.raises(SnapshotError, match="^(expected_regular_eml_file|source_not_readable)$"):
        read_snapshot(directory)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO test")
def test_fifo_named_eml_is_rejected_without_blocking(tmp_path):
    path = tmp_path / "pipe.eml"
    os.mkfifo(path)
    with pytest.raises(SnapshotError, match="^expected_regular_eml_file$"):
        read_snapshot(path)


def test_parser_exception_does_not_expose_email_or_path(tmp_path, monkeypatch):
    import dead_letter.core.snapshot as snapshots
    path = write_eml(tmp_path)
    def broken(*args, **kwargs):
        raise RuntimeError(f"parser failed on {PRIVATE} at {path}")
    monkeypatch.setattr(snapshots, "parse_eml_bytes", broken)
    with pytest.raises(SnapshotError, match="^normalization_failed$") as error:
        read_snapshot(path)
    assert error.value.__suppress_context__
    assert PRIVATE not in str(error.value)


def test_attribution_debug_logs_do_not_include_email_prefix(caplog):
    with caplog.at_level(logging.DEBUG, logger="dead_letter.core.attribution"):
        assert parse_attribution_line(PRIVATE) is None
    assert PRIVATE not in caplog.text
