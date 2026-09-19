"""Synthetic, byte-exact MBOX fixtures; no private mail or network access."""

from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path

import pytest

from dead_letter.core.mbox import MboxFormatError, MboxLimits, iter_mbox

POSTMARK = b"From 1669160939957813740@example.test Thu Jun 11 00:38:38 +0000 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Repeated\n\nHello\n\n"


def snapshots(path: Path, **kwargs):
    with closing(iter_mbox(path, **kwargs)) as records:
        return [(r, r.path.read_bytes() if r.path else None) for r in records]


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
def test_order_offsets_hash_and_staged_bytes(tmp_path, ending):
    envelope = POSTMARK.replace(b"\n", ending)
    message = MESSAGE.replace(b"\n", ending)
    path = tmp_path / "takeout.mbox"
    path.write_bytes((envelope + message) * 3)
    rows = snapshots(path)
    assert len(rows) == 3
    for n, (record, data) in enumerate(rows):
        assert record.index == n + 1
        assert record.envelope_offset == n * (len(envelope) + len(message))
        assert record.message_offset == record.envelope_offset + len(envelope)
        assert record.end_offset == record.message_offset + len(message)
        assert record.sha256 == hashlib.sha256(message).hexdigest()
        assert data == message
        assert not record.path.exists()


@pytest.mark.parametrize("mode, expected", [
    ("preserve", b">From prose\n>>From quoted\n"),
    ("mboxo", b"From prose\n>>From quoted\n"),
    ("mboxrd", b"From prose\n>From quoted\n"),
])
def test_quoting_is_explicit_and_body_only(tmp_path, mode, expected):
    path = tmp_path / "quotes.mbox"
    header = b"Subject: >From headers must remain unchanged\n\n"
    body = b">From prose\n>>From quoted\n"
    path.write_bytes(POSTMARK + header + body)
    [(record, data)] = snapshots(path, unescape=mode)
    assert data == header + expected
    assert record.sha256 == hashlib.sha256(header + body).hexdigest()


def test_ordinary_from_prose_does_not_split_and_final_newline_optional(tmp_path):
    path = tmp_path / "mail.mbox"
    body = b"Subject: Greetings\n\nFrom the engineering team\n\nFrom Paris with love"
    path.write_bytes(POSTMARK + body + b"\n" + POSTMARK + MESSAGE.rstrip(b"\n"))
    rows = snapshots(path)
    assert len(rows) == 2
    assert rows[0][1] == body + b"\n"
    assert rows[1][1] == MESSAGE.rstrip(b"\n")


def test_oversized_record_is_drained_and_next_record_survives(tmp_path):
    path = tmp_path / "mail.mbox"
    too_big = b"Subject: Oversized\n\n" + b"x\n" * 1000
    path.write_bytes(POSTMARK + MESSAGE + POSTMARK + too_big + POSTMARK + MESSAGE)
    rows = snapshots(path, limits=MboxLimits(max_message_bytes=100, max_line_bytes=128))
    assert [r.error_code for r, _ in rows] == [None, "mbox_message_too_large", None]
    assert rows[1][0].sha256 == hashlib.sha256(too_big).hexdigest()
    assert rows[1][1] is None
    assert rows[2][1] == MESSAGE


def test_long_line_fragment_cannot_be_mistaken_for_postmark(tmp_path):
    path = tmp_path / "mail.mbox"
    # A valid-looking postmark starts exactly at a read-fragment boundary,
    # but is NOT at the beginning of a physical line.
    bad = b"Subject: Long line\n\n" + b"x" * 129 + POSTMARK + b"tail\n"
    path.write_bytes(POSTMARK + bad + POSTMARK + MESSAGE)
    rows = snapshots(path, limits=MboxLimits(max_line_bytes=128))
    assert len(rows) == 2
    assert rows[0][0].error_code == "mbox_line_too_long"
    assert rows[0][0].sha256 == hashlib.sha256(bad).hexdigest()
    assert rows[1][1] == MESSAGE


def test_empty_archive_and_empty_record(tmp_path):
    path = tmp_path / "empty.mbox"
    path.touch()
    assert snapshots(path) == []
    path.write_bytes(POSTMARK + POSTMARK + MESSAGE)
    rows = snapshots(path)
    assert rows[0][0].error_code == "mbox_empty_message"
    assert rows[1][1] == MESSAGE


@pytest.mark.parametrize("data", [b"Not an archive\n", b"\n" + POSTMARK + MESSAGE])
def test_no_silent_preamble_loss(tmp_path, data):
    path = tmp_path / "bad.mbox"
    path.write_bytes(data)
    with pytest.raises(MboxFormatError, match="must begin"):
        snapshots(path)


def test_length_framing_refused_before_ambiguous_body(tmp_path):
    path = tmp_path / "cl2.mbox"
    path.write_bytes(POSTMARK + MESSAGE + POSTMARK + b"cOnTeNt-LeNgTh: 900\n\n" + POSTMARK)
    with closing(iter_mbox(path)) as records:
        assert next(records).path.read_bytes() == MESSAGE
        with pytest.raises(MboxFormatError, match="Content-Length"):
            next(records)


def test_closing_early_cleans_staging(tmp_path):
    path = tmp_path / "many.mbox"
    path.write_bytes((POSTMARK + MESSAGE) * 10)
    with closing(iter_mbox(path)) as records:
        record = next(records)
        staged = record.path
        assert staged.exists()
    assert not staged.exists()
    assert path.read_bytes() == (POSTMARK + MESSAGE) * 10


def test_all_reads_are_bounded_and_first_result_does_not_index_archive(tmp_path, monkeypatch):
    path = tmp_path / "many.mbox"
    path.write_bytes((POSTMARK + MESSAGE) * 10000)
    original = Path.open
    bytes_read = 0

    class Guard:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def readline(self, size=-1):
            nonlocal bytes_read
            assert 0 < size <= 129
            value = self.stream.readline(size)
            bytes_read += len(value)
            return value

    def guarded_open(self, mode="r", *args, **kwargs):
        stream = original(self, mode, *args, **kwargs)
        return Guard(stream) if self == path and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    with closing(iter_mbox(path, limits=MboxLimits(max_line_bytes=128))) as records:
        assert next(records).index == 1
        assert bytes_read == len(POSTMARK + MESSAGE + POSTMARK)
        assert sum(1 for _ in records) == 9999


def test_modified_source_is_reported(tmp_path):
    path = tmp_path / "mail.mbox"
    path.write_bytes(POSTMARK + MESSAGE)
    with closing(iter_mbox(path)) as records:
        next(records)
        path.write_bytes(POSTMARK + MESSAGE + b"changed")
        with pytest.raises(MboxFormatError, match="changed during import"):
            next(records)


@pytest.mark.parametrize("kwargs", [{"max_message_bytes": 0}, {"max_line_bytes": 127}])
def test_limits_validated(kwargs):
    with pytest.raises(ValueError):
        MboxLimits(**kwargs)
