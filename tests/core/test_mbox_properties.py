"""Shrinking, synthetic tests for delimiter-framed MBOX dialects."""

from __future__ import annotations

import hashlib
import mailbox
import os
from contextlib import closing

from hypothesis import given, strategies as st

from dead_letter.core.mbox import MboxLimits, iter_mbox

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020"
HEADERS = (
    (),
    (b"Subject: plain", b"Message-ID: <same@example.test>"),
    (b"Broken header", b"Subject: folded", b" continuation", b"\tsecond fold"),
    (b"Message-ID: <same@example.test>", b"Message-ID: <same@example.test>"),
    (b"Subject: >From stays a header", b"X-Test: binary-\xff"),
)
BODY_LINES = (
    b"ordinary", b"", b"From ordinary prose", POSTMARK,
    b">From quoted prose", b">>From deeper quote", b"Content-Length: body text",
    b"x" * 126, b"x" * 127, b"x" * 128, b"\x00\xff",
)


def _quote(line: bytes, dialect: str) -> bytes:
    """Independent writer rule; add mboxcl dialects here when supported."""
    if dialect == "mboxrd" and line.lstrip(b">").startswith(b"From "):
        return b">" + line
    if dialect == "mboxo" and line.startswith(b"From "):
        return b">" + line
    return line


@st.composite
def mailboxes(draw, dialect=st.sampled_from(("preserve", "mboxrd", "mboxo"))):
    dialect = draw(dialect)
    ending = draw(st.sampled_from((b"\n", b"\r\n")))
    count = draw(st.integers(0, 5))
    missing_final_newline = draw(st.booleans())
    archive = bytearray()
    expected = []
    for index in range(count):
        header_lines = draw(st.sampled_from(HEADERS))
        body_lines = draw(st.lists(st.sampled_from(BODY_LINES), max_size=5))
        if dialect == "mboxo":
            # mboxo cannot encode an original >From line unambiguously.
            body_lines = [line for line in body_lines if not line.startswith(b">From ")]
        if dialect == "preserve":
            # An unescaped full postmark is an ambiguous record delimiter.
            body_lines = [line for line in body_lines if line != POSTMARK]
        raw = (ending.join(header_lines) + ending * 2) if header_lines else (ending if body_lines else b"")
        stored = raw
        for line in body_lines:
            stored += _quote(line, dialect) + ending
            raw += line + ending
        if index == count - 1 and missing_final_newline and stored.endswith(ending):
            stored = stored[:-len(ending)]
            raw = raw[:-len(ending)]
        envelope_offset = len(archive)
        envelope = POSTMARK + ending
        archive.extend(envelope)
        archive.extend(stored)
        expected.append((envelope_offset, envelope_offset + len(envelope), bytes(stored), raw))
    return dialect, bytes(archive), expected


@given(mailboxes())
def test_generated_dialects_roundtrip_and_provenance(tmp_path_factory, case):
    dialect, archive, expected = case
    source = tmp_path_factory.mktemp("mbox-properties") / "generated.mbox"
    source.write_bytes(archive)
    cursor = 0
    with closing(iter_mbox(source, limits=MboxLimits(max_line_bytes=128), unescape=dialect)) as rows:
        for index, (envelope_offset, message_offset, stored, raw) in enumerate(expected, 1):
            record = next(rows)
            assert (record.index, record.envelope_offset, record.message_offset, record.end_offset) == (
                index, envelope_offset, message_offset, message_offset + len(stored)
            )
            assert record.envelope_offset == cursor
            assert record.sha256 == hashlib.sha256(stored).hexdigest()
            assert archive[record.message_offset:record.end_offset] == stored
            overlong = any(len(line) > 128 for line in stored.splitlines(keepends=True))
            assert record.error_code == (
                "mbox_line_too_long" if overlong else "mbox_empty_message" if not stored else None
            )
            assert (record.path.read_bytes() if record.path else None) == (
                raw if stored and not overlong else None
            )
            cursor = record.end_offset
        assert next(rows, None) is None
    assert cursor == len(archive)


@given(st.lists(st.sampled_from((b"plain", b"From ordinary prose", b">From quote", b"")), max_size=5))
def test_stdlib_writer_dialect_only(tmp_path_factory, lines):
    """CPython is an oracle only for mailboxes emitted by its own writer."""
    source = tmp_path_factory.mktemp("mbox-stdlib") / "stdlib.mbox"
    with closing(mailbox.mbox(source, create=True)) as writer:
        writer.add(POSTMARK + b"\nSubject: generated\n\n" + b"\n".join(lines) + b"\n")
    with closing(mailbox.mbox(source, create=False)) as reader:
        with reader.get_file(next(reader.iterkeys())) as message:
            expected = message.read() + os.linesep.encode()
    with closing(iter_mbox(source)) as records:
        record = next(records)
        assert record.path.read_bytes() == expected
        assert record.sha256 == hashlib.sha256(expected).hexdigest()
        assert next(records, None) is None


@given(st.integers(257, 500), st.sampled_from((b"\n", b"\r\n")))
def test_oversized_record_recovery_preserves_following_range(tmp_path_factory, count, ending):
    source = tmp_path_factory.mktemp("mbox-oversized") / "oversized.mbox"
    envelope = POSTMARK + ending
    first = b"Subject: oversized" + ending * 2 + (b"x" + ending) * count
    last = b"Subject: survivor" + ending * 2 + b"ok" + ending
    source.write_bytes(envelope + first + envelope + last)
    with closing(iter_mbox(source, limits=MboxLimits(max_message_bytes=256, max_line_bytes=128))) as rows:
        oversize = next(rows)
        assert oversize.error_code == "mbox_message_too_large"
        assert oversize.path is None
        assert oversize.sha256 == hashlib.sha256(first).hexdigest()
        survivor = next(rows)
        assert survivor.envelope_offset == oversize.end_offset
        assert survivor.path.read_bytes() == last
        assert survivor.sha256 == hashlib.sha256(last).hexdigest()
        assert survivor.end_offset == source.stat().st_size
        assert next(rows, None) is None
