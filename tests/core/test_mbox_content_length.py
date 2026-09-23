"""Opt-in mboxcl/mboxcl2 Content-Length framing; synthetic fixtures only."""
from __future__ import annotations

import hashlib
import json
import mailbox
import os
import re
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from dead_letter.core import convert
from dead_letter.core.mbox import MboxFormatError, MboxLimits, iter_mbox
from dead_letter.core.mbox_import import convert_mbox

POSTMARK = b"From sender@example.test Thu Nov 29 22:33:52 2001\n"
BODY_POSTMARK = b"From x Thu Nov 29 22:33:52 2001"
FALLBACK = "mbox_content_length_fallback"


def snapshots(path: Path, **kwargs):
    with closing(iter_mbox(path, **kwargs)) as records:
        return [(r, r.path.read_bytes() if r.path else None) for r in records]


def message(body: bytes, *, eol: bytes = b"\n", subject: bytes = b"hello", fields: bytes | None = None) -> bytes:
    """Headers, blank line and body; ``fields`` replaces the Content-Length line(s)."""
    body = body.replace(b"\n", eol)
    if fields is None:
        fields = b"Content-Length: %d\n" % len(body)
    head = b"From: alice@example.test\nSubject: " + subject + b"\n" + fields + b"\n"
    return head.replace(b"\n", eol) + body


def archive(*messages: bytes, eol: bytes = b"\n") -> bytes:
    # Writers store one separator EOL after each declared body.
    return b"".join(POSTMARK.replace(b"\n", eol) + raw + eol for raw in messages)


def reference_records(data: bytes, dialect: str) -> list[tuple[int, int, int, str, bytes]]:
    """Independent whole-file reader for tiny *valid* fixtures only.

    Deliberately shares no code with the production framer: it splits in
    memory, unfolds header text directly and checks the next postmark by the
    fixtures' literal prefix. It asserts instead of recovering.
    """
    rows = []
    pos = 0
    while pos < len(data):
        start = data.index(b"\n", pos) + 1
        cursor = start
        fields: list[bytes] = []
        while True:
            line_end = data.index(b"\n", cursor) + 1
            line = data[cursor:line_end]
            cursor = line_end
            if line in (b"\n", b"\r\n"):
                break
            if line[:1] in (b" ", b"\t"):
                fields[-1] += line.rstrip(b"\r\n")
            else:
                fields.append(line.rstrip(b"\r\n"))
        values = {
            int(value.strip(b" \t\r"))
            for name, _, value in (field.partition(b":") for field in fields)
            if name.strip().lower() == b"content-length"
        }
        assert len(values) == 1
        body_end = cursor + values.pop()
        separator = 2 if data[body_end:body_end + 2] == b"\r\n" else 1
        assert data[body_end:body_end + separator] in (b"\n", b"\r\n")
        end = body_end + separator
        assert end == len(data) or data[end:].startswith(b"From sender@example.test ")
        body = data[cursor:end]
        if dialect == "mboxcl":
            lines = re.split(rb"(?<=\n)", body)
            body = b"".join(line[1:] if line.startswith(b">From ") else line for line in lines)
        rows.append((pos, start, end, hashlib.sha256(data[start:end]).hexdigest(), data[start:cursor] + body))
        pos = end
    return rows


NESTED_FIELDS = b"MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=\"b1\"\nContent-Length: %d\n"
NESTED = (
    b"--b1\nContent-Type: text/plain\n\nOuter part\n"
    b"--b1\nContent-Type: message/rfc822\n\n"
    b"Content-Length: 999\nSubject: inner\n\nInner body\n--b1--\n"
)


def nested(eol: bytes = b"\n") -> bytes:
    # Only the top-level Content-Length frames; the rfc822 part's is body text.
    body = NESTED + BODY_POSTMARK + b"\n"
    return message(body, eol=eol, subject=b"nested", fields=NESTED_FIELDS % len(body.replace(b"\n", eol)))


def valid_fixtures(dialect: str, eol: bytes) -> bytes:
    quoted = b">" if dialect == "mboxcl" else b""
    return archive(
        message(b"Hello\n\n", eol=eol, subject=b"plain"),
        message(b"Intro\n" + quoted + BODY_POSTMARK + b"\n>From kept once\n>>From twice\n", eol=eol, subject=b"postmark"),
        message(b"", eol=eol, subject=b"zero"),
        message("Café 東京\n".encode(), eol=eol, subject=b"utf8"),
        message(b"no final newline", eol=eol, subject=b"unterminated"),
        message(b"body\n", eol=eol, subject=b"folded",
                fields=b"Content-Length:\n %d \n" % len(b"body\n".replace(b"\n", eol))),
        nested(eol),
        eol=eol,
    )


@pytest.mark.parametrize("dialect", ["mboxcl", "mboxcl2"])
@pytest.mark.parametrize("eol", [b"\n", b"\r\n"])
def test_valid_fixtures_match_independent_reference(tmp_path, dialect, eol):
    data = valid_fixtures(dialect, eol)
    path = tmp_path / "cl.mbox"
    path.write_bytes(data)
    rows = snapshots(path, unescape=dialect)
    expected = reference_records(data, dialect)
    assert len(rows) == len(expected) == 7
    for (record, staged), (envelope, start, end, sha, output) in zip(rows, expected, strict=True):
        assert (record.envelope_offset, record.message_offset, record.end_offset) == (envelope, start, end)
        assert record.sha256 == sha == hashlib.sha256(data[start:end]).hexdigest()
        assert record.error_code is None and record.framing_diagnostic is None
        assert staged == output
    # The body postmark never became a boundary; quoting follows the dialect.
    body = rows[1][1].split(eol + eol, 1)[1]
    if dialect == "mboxcl":
        # mboxo rule: exactly one ">" is removed only from ">From " lines.
        assert body == (b"Intro\n" + BODY_POSTMARK + b"\nFrom kept once\n>>From twice\n\n").replace(b"\n", eol)
    else:
        assert body == (b"Intro\n" + BODY_POSTMARK + b"\n>From kept once\n>>From twice\n\n").replace(b"\n", eol)
    assert path.read_bytes() == data


@pytest.mark.parametrize("dialect", ["mboxcl", "mboxcl2"])
def test_markdown_and_source_eml_match_standalone_conversion(tmp_path, dialect):
    data = valid_fixtures(dialect, b"\n")
    path = tmp_path / "cl.mbox"
    path.write_bytes(data)
    expected = reference_records(data, dialect)
    flat = list(convert_mbox(path, output=tmp_path / "flat", unescape=dialect))
    bundles = list(convert_mbox(path, output=tmp_path / "bundles", unescape=dialect, bundles=True))
    assert len(flat) == len(bundles) == len(expected)
    for n, (row, bundle, (_, start, end, sha, output)) in enumerate(zip(flat, bundles, expected, strict=True)):
        assert row.success and bundle.success
        assert row.mbox["unescape"] == dialect and "framing_diagnostic" not in row.mbox
        assert (row.mbox["message_offset"], row.mbox["end_offset"], row.mbox["sha256"]) == (start, end, sha)
        assert (bundle.output.parent / "source.eml").read_bytes() == output
        eml = tmp_path / f"single-{n}.eml"
        eml.write_bytes(output)
        single = convert(eml, output=tmp_path / f"single-{n}.md")
        assert single.success
        assert (single.output.read_text(encoding="utf-8").split("---", 2)[2]
                == row.output.read_text(encoding="utf-8").split("---", 2)[2])
    assert path.read_bytes() == data


@pytest.mark.skipif(os.linesep != "\n", reason="CPython's mbox strips only its platform separator")
def test_mboxcl2_without_body_postmarks_matches_cpython_mbox(tmp_path):
    # CPython ignores Content-Length; use it only where both framings agree.
    data = archive(*(message(b"Body %d\n\n" % n + b"Content-Length: 1\n", subject=b"m%d" % n) for n in range(5)),
                   message(b"x" * 300 + b"\n"))
    path = tmp_path / "cl2.mbox"
    path.write_bytes(data)
    with closing(mailbox.mbox(path, create=False)) as independent:
        expected = []
        for key in independent.iterkeys():
            with independent.get_file(key) as stored:
                expected.append(stored.read() + b"\n")
    rows = snapshots(path, unescape="mboxcl2")
    assert [staged for _, staged in rows] == expected
    assert [r.sha256 for r, _ in rows] == [hashlib.sha256(raw).hexdigest() for raw in expected]


@pytest.mark.parametrize("eol", [b"\n", b"\r\n"])
def test_final_message_needs_exactly_one_trailing_eol(tmp_path, eol):
    path = tmp_path / "final.mbox"
    good = archive(message(b"one\n", eol=eol), eol=eol)
    path.write_bytes(good)
    [(record, staged)] = snapshots(path, unescape="mboxcl2")
    assert record.end_offset == len(good) and staged == good[len(POSTMARK) + len(eol) - 1:]
    path.write_bytes(good[: -len(eol)])
    with pytest.raises(MboxFormatError, match="byte 0 has a missing or invalid Content-Length"):
        snapshots(path, unescape="mboxcl2")
    [(record, staged)] = snapshots(path, unescape="mboxcl")
    assert record.framing_diagnostic == FALLBACK
    assert staged == good[len(POSTMARK) + len(eol) - 1: -len(eol)]


def _body(n: int) -> bytes:
    return b"Body %d\n" % n


BAD = b"Line one\nxFrom tail\ncaf\xc3\xa9\n"


@pytest.mark.parametrize("fields, body", [
    pytest.param(b"", BAD, id="missing"),
    pytest.param(b"Content-Length:\n", BAD, id="empty"),
    pytest.param(b"Content-Length: \t\n", BAD, id="whitespace-only"),
    pytest.param(b"Content-Length: -5\n", BAD, id="negative"),
    pytest.param(b"Content-Length: +%d\n" % len(BAD), BAD, id="plus-sign"),
    pytest.param(b"Content-Length: %dx\n" % len(BAD), BAD, id="trailing-junk"),
    # Split the correct digits so that joining them would wrongly frame BAD.
    pytest.param(b"Content-Length: 2 6\n", BAD, id="inner-space"),
    pytest.param(b"Content-Length: 2\n 6\n", BAD, id="folded-digits"),
    pytest.param(b"Content-Length: 2 \n 6\n", BAD, id="folded-digits-after-trailing-space"),
    pytest.param(b"Content-Length: %d\nContent-Length: %d\n" % (len(BAD), len(BAD) + 1), BAD, id="duplicate-differing"),
    pytest.param(b"Content-Length: 0000000000000000000%d\n" % len(BAD), BAD, id="more-than-20-digits"),
    pytest.param(b"Content-Length: 99999999999999999999\n", BAD, id="beyond-eof-20-digits"),
    pytest.param(b"Content-Length: %d\n" % (len(BAD) - 1), BAD, id="off-by-one-short"),
    pytest.param(b"Content-Length: %d\n" % (len(BAD) + 1), BAD, id="off-by-one-long"),
    pytest.param(b"Content-Length: %d\n" % (len(BAD) - 2), BAD, id="mid-utf8"),
    pytest.param(b"Content-Length: %d\n" % BAD.index(b"From"), BAD, id="endpoint-on-xFrom-mid-line"),
    pytest.param(b"Content-Length: 5\n", b"intro\nFrom nobody\nmore\n", id="malformed-postmark-at-endpoint"),
    pytest.param(b"Content-Length: \xd9\xa3\n", BAD, id="non-ascii-digit"),
])
def test_invalid_lengths_follow_dialect_recovery(tmp_path, fields, body):
    bad = message(body, fields=fields, subject=b"bad")
    data = archive(message(_body(1)), bad, message(_body(3)))
    path = tmp_path / "bad.mbox"
    path.write_bytes(data)
    second_envelope = len(POSTMARK) + len(message(_body(1))) + 1
    third_envelope = second_envelope + len(POSTMARK) + len(bad) + 1

    # mboxcl: scan that one message by postmark, with a diagnostic.
    rows = snapshots(path, unescape="mboxcl")
    assert [r.framing_diagnostic for r, _ in rows] == [None, FALLBACK, None]
    assert rows[1][0].envelope_offset == second_envelope
    assert rows[1][0].end_offset == third_envelope
    assert rows[1][1] == bad + b"\n"
    assert rows[2][1] == message(_body(3)) + b"\n"

    # mboxcl2: fatal at that envelope; the earlier record remains yielded.
    with closing(iter_mbox(path, unescape="mboxcl2")) as records:
        assert next(records).path.read_bytes() == message(_body(1)) + b"\n"
        with pytest.raises(MboxFormatError, match=f"Message 2 at byte {second_envelope} .*Content-Length"):
            next(records)
    assert path.read_bytes() == data


@pytest.mark.parametrize("fields", [
    pytest.param(b"Content-Length:\n %d\n", id="folded-value"),
    pytest.param(b"Content-Length: %d \n", id="trailing-space"),
    pytest.param(b"Content-Length:\t%d\r\n", id="tab-and-stray-cr"),
    pytest.param(b"cOnTeNt-LeNgTh: %d\n", id="case-insensitive"),
    pytest.param(b"Content-Length: %d\nX-Other: 1\ncontent-length: %d\n", id="duplicate-equal"),
    pytest.param(b"Content-Length: 000%d\n", id="leading-zeros"),
])
def test_accepted_length_spellings(tmp_path, fields):
    body = b"Hello\n" + BODY_POSTMARK + b"\n"
    n = len(body)
    raw = message(body, fields=fields % ((n,) * fields.count(b"%d")))
    path = tmp_path / "ok.mbox"
    path.write_bytes(archive(raw, message(_body(2))))
    rows = snapshots(path, unescape="mboxcl2")
    assert [r.framing_diagnostic for r, _ in rows] == [None, None]
    assert [staged for _, staged in rows] == [raw + b"\n", message(_body(2)) + b"\n"]


@pytest.mark.parametrize("first, second", [(1, 0), (0, 1)])
def test_differing_duplicates_are_invalid_even_when_each_frames(tmp_path, first, second):
    # Both declared endpoints are followed by a valid postmark; neither wins.
    body = b"a\n" + BODY_POSTMARK + b"\ntail\n"
    lengths = (1, len(body))
    raw = message(body, fields=b"Content-Length: %d\nContent-Length: %d\n" % (lengths[first], lengths[second]))
    path = tmp_path / "dup.mbox"
    path.write_bytes(archive(raw))
    with pytest.raises(MboxFormatError, match="Content-Length"):
        snapshots(path, unescape="mboxcl2")
    rows = snapshots(path, unescape="mboxcl")
    # Fallback scanning splits at the unquoted body postmark, as documented;
    # the split-off tail has no Content-Length of its own, so it falls back too.
    assert [r.framing_diagnostic for r, _ in rows] == [FALLBACK, FALLBACK]
    assert rows[1][1] == b"tail\n\n"


def test_padded_length_line_is_parsed_in_bounded_fragments(tmp_path):
    # Framing validity is independent of the line limit (ruling: size limits
    # fail the record, then resume at the validated boundary).
    body = b"Hello\n" + BODY_POSTMARK + b"\n"
    raw = message(body, fields=b"Content-Length:" + b" " * 300 + b"%d\t\n" % len(body))
    path = tmp_path / "padded.mbox"
    path.write_bytes(archive(raw, message(_body(2))))
    rows = snapshots(path, unescape="mboxcl2", limits=MboxLimits(max_line_bytes=128))
    assert [r.error_code for r, _ in rows] == ["mbox_line_too_long", None]
    assert [r.framing_diagnostic for r, _ in rows] == [None, None]
    assert rows[0][0].sha256 == hashlib.sha256(raw + b"\n").hexdigest()
    assert rows[1][1] == message(_body(2)) + b"\n"


def test_zero_length_and_nested_message_length_are_handled(tmp_path):
    empty = message(b"")
    path = tmp_path / "zero.mbox"
    path.write_bytes(archive(empty, nested(), empty))
    rows = snapshots(path, unescape="mboxcl2")
    assert [staged for _, staged in rows] == [empty + b"\n", nested() + b"\n", empty + b"\n"]
    assert all(r.framing_diagnostic is None for r, _ in rows)


def test_headers_must_end_before_the_next_postmark_or_eof(tmp_path):
    headers_only = b"Subject: no blank line\nContent-Length: 0\n"
    path = tmp_path / "headers.mbox"
    path.write_bytes(archive(message(_body(1))) + POSTMARK + headers_only + archive(message(_body(3))))
    rows = snapshots(path, unescape="mboxcl")
    assert [r.framing_diagnostic for r, _ in rows] == [None, FALLBACK, None]
    assert rows[1][1] == headers_only
    with closing(iter_mbox(path, unescape="mboxcl2")) as records:
        next(records)
        with pytest.raises(MboxFormatError, match="Content-Length"):
            next(records)
    path.write_bytes(POSTMARK + headers_only)
    [(record, _)] = snapshots(path, unescape="mboxcl")
    assert record.framing_diagnostic == FALLBACK


def test_mboxcl_quoting_is_body_only(tmp_path):
    raw = message(b">From body\n", fields=b">From: header stays\nContent-Length: 11\n")
    path = tmp_path / "quote.mbox"
    path.write_bytes(archive(raw))
    [(_, cl)] = snapshots(path, unescape="mboxcl")
    [(_, cl2)] = snapshots(path, unescape="mboxcl2")
    assert cl == raw.replace(b"\n>From body", b"\nFrom body") + b"\n"
    assert cl2 == raw + b"\n"
    assert b">From: header stays" in cl


@pytest.mark.parametrize("mode", ["preserve", "mboxrd", "mboxo"])
def test_delimiter_modes_still_refuse_content_length(tmp_path, mode):
    path = tmp_path / "cl2.mbox"
    path.write_bytes(archive(message(_body(1))))
    with pytest.raises(MboxFormatError, match="uses Content-Length"):
        snapshots(path, unescape=mode)


def test_unknown_dialect_is_rejected(tmp_path):
    path = tmp_path / "x.mbox"
    path.write_bytes(archive(message(_body(1))))
    with pytest.raises(ValueError, match="Unsupported MBOX unescape mode"):
        snapshots(path, unescape="mboxcl3")
    with pytest.raises(ValueError, match="Unsupported MBOX unescape mode"):
        list(convert_mbox(path, output=tmp_path / "out", unescape="auto"))


def test_oversized_valid_length_is_drained_with_bounded_reads(tmp_path, monkeypatch):
    big_body = (b"x" * 100 + b"\n" + BODY_POSTMARK + b"\n") * 40
    big = message(big_body)
    path = tmp_path / "big.mbox"
    data = archive(message(_body(1)), big, message(_body(3)))
    path.write_bytes(data)
    original = Path.open
    sizes: list[tuple[str, int]] = []

    class Guard:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def seek(self, *args):
            return self.stream.seek(*args)

        def read(self, size=-1):
            sizes.append(("read", size))
            return self.stream.read(size)

        def readline(self, size=-1):
            sizes.append(("readline", size))
            return self.stream.readline(size)

    def guarded_open(self, mode="r", *args, **kwargs):
        stream = original(self, mode, *args, **kwargs)
        return Guard(stream) if self == path and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    rows = snapshots(path, unescape="mboxcl2", limits=MboxLimits(max_message_bytes=1000, max_line_bytes=128))
    assert [r.error_code for r, _ in rows] == [None, "mbox_message_too_large", None]
    assert rows[1][1] is None
    assert rows[1][0].sha256 == hashlib.sha256(big + b"\n").hexdigest()
    assert rows[1][0].end_offset - rows[1][0].message_offset == len(big) + 1
    assert rows[2][1] == message(_body(3)) + b"\n"
    assert all(0 < size <= 129 for kind, size in sizes if kind == "readline")
    assert any(kind == "read" for kind, _ in sizes)
    assert all(0 < size <= 2 for kind, size in sizes if kind == "read")


def test_long_body_line_is_isolated_and_boundary_kept(tmp_path):
    raw = message(b"y" * 1000 + b"\n")
    path = tmp_path / "long.mbox"
    path.write_bytes(archive(raw, message(_body(2))))
    rows = snapshots(path, unescape="mboxcl", limits=MboxLimits(max_line_bytes=128))
    assert [r.error_code for r, _ in rows] == ["mbox_line_too_long", None]
    assert rows[0][0].sha256 == hashlib.sha256(raw + b"\n").hexdigest()
    assert rows[1][1] == message(_body(2)) + b"\n"


def test_source_change_is_detected_between_framed_records(tmp_path):
    path = tmp_path / "cl.mbox"
    path.write_bytes(archive(message(_body(1)), message(_body(2))))
    with closing(iter_mbox(path, unescape="mboxcl2")) as records:
        next(records)
        with path.open("ab") as out:
            out.write(archive(message(_body(3))))
        with pytest.raises(MboxFormatError, match="changed during import"):
            next(records)


def frontmatter(path):
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


@pytest.mark.parametrize("timeout", [None, 30.0])
def test_fallback_diagnostic_reaches_import_provenance(tmp_path, timeout):
    path = tmp_path / "cl.mbox"
    data = archive(message(_body(1)), message(BAD, fields=b""), message(_body(3)))
    path.write_bytes(data)
    rows = list(convert_mbox(path, output=tmp_path / "out", unescape="mboxcl", timeout_seconds=timeout))
    assert [r.success for r in rows] == [True, True, True]
    assert [r.mbox.get("framing_diagnostic") for r in rows] == [None, FALLBACK, None]
    assert frontmatter(rows[1].output)["source_mbox"]["framing_diagnostic"] == FALLBACK
    assert "framing_diagnostic" not in frontmatter(rows[0].output)["source_mbox"]
    assert path.read_bytes() == data


def test_mboxcl2_invalid_length_is_an_archive_error_after_earlier_results(tmp_path):
    path = tmp_path / "cl2.mbox"
    path.write_bytes(archive(message(_body(1)), message(BAD, fields=b"Content-Length: -1\n")))
    rows = list(convert_mbox(path, output=tmp_path / "out", unescape="mboxcl2"))
    assert rows[0].success and rows[0].mbox["index"] == 1
    assert rows[1].mbox is None and rows[1].error["code"] == "mbox_archive_error"
    assert "Content-Length" in rows[1].error["message"]


def test_cli_report_records_dialect_and_fallback(tmp_path):
    from dead_letter.backend import cli

    source = tmp_path / "cl.mbox"
    source.write_bytes(archive(message(_body(1)), message(BAD, fields=b"Content-Length: 5x\n")))
    root = tmp_path / "out"
    assert cli.main([str(source), "--output", str(root), "--mbox-unescape", "mboxcl", "--report"]) == 0
    report = json.loads((root / ".dead-letter-report.json").read_text(encoding="utf-8"))
    assert report["mbox_options"]["unescape"] == "mboxcl"
    assert [r["mbox"].get("framing_diagnostic") for r in report["results"]] == [None, FALLBACK]
    assert report["results"][1]["success"] is True
