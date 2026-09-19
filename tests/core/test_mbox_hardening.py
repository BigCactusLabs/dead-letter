"""Framing regressions and deterministic differential tests; no private mail."""
from __future__ import annotations

import hashlib
import mailbox
import os
import random
from contextlib import closing
from pathlib import Path

import pytest

from dead_letter.core.mbox import MboxFormatError, MboxLimits, iter_mbox

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"Subject: hello\n\nBody\n\n"


def snapshots(path: Path, **kwargs):
    with closing(iter_mbox(path, **kwargs)) as records:
        return [(r, r.path.read_bytes() if r.path else None) for r in records]


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("wsp", [b" ", b"\t", b" \t"])
def test_folded_content_length_is_not_a_storage_header(tmp_path, ending, wsp):
    first = b"Subject: hello\n" + wsp + b"Content-Length: not framing\n\nBody\n"
    first = first.replace(b"\n", ending)
    path = tmp_path / "mail.mbox"
    path.write_bytes(POSTMARK.replace(b"\n", ending) + first + POSTMARK + MESSAGE)
    rows = snapshots(path)
    assert [r.index for r, _ in rows] == [1, 2]
    assert rows[0][1] == first
    assert rows[1][1] == MESSAGE


@pytest.mark.parametrize("field", [b"Content-Length:", b"cOnTeNt-LeNgTh:", b"Content-Length \t:"])
def test_real_length_header_after_continuation_is_still_fatal(tmp_path, field):
    path = tmp_path / "mail.mbox"
    path.write_bytes(POSTMARK + MESSAGE + POSTMARK + b"Subject: hello\n continuation\n" + field + b"\n 99\n\n")
    with closing(iter_mbox(path)) as records:
        assert next(records).index == 1
        with pytest.raises(MboxFormatError, match="Content-Length"):
            next(records)


def test_body_content_length_is_ordinary_text(tmp_path):
    path = tmp_path / "mail.mbox"
    body = MESSAGE + b"Content-Length: not a storage header\n"
    path.write_bytes(POSTMARK + body + POSTMARK + MESSAGE)
    assert [data for _, data in snapshots(path)] == [body, MESSAGE]


@pytest.mark.skipif(os.name == "nt", reason="Windows may deny replacing open source files")
def test_replacement_between_open_and_initial_check_is_detected(tmp_path, monkeypatch):
    path = tmp_path / "mail.mbox"
    replacement = tmp_path / "replacement.mbox"
    path.write_bytes(POSTMARK + MESSAGE)
    replacement.write_bytes(POSTMARK + MESSAGE.replace(b"hello", b"other"))
    original_open = Path.open

    def replacing_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == path and mode == "rb":
            os.replace(replacement, path)
        return handle

    monkeypatch.setattr(Path, "open", replacing_open)
    with pytest.raises(MboxFormatError, match="changed during import"):
        snapshots(path)


@pytest.mark.parametrize("mutation", ["append", "truncate", "rewrite"])
def test_source_changes_stop_before_another_record_is_emitted(tmp_path, mutation):
    path = tmp_path / "mail.mbox"
    original = (POSTMARK + MESSAGE) * 3
    path.write_bytes(original)
    with closing(iter_mbox(path)) as records:
        assert next(records).index == 1
        if mutation == "append":
            with path.open("ab") as out:
                out.write(POSTMARK + MESSAGE)
        elif mutation == "truncate":
            path.write_bytes(POSTMARK + MESSAGE)
        else:
            stamp = path.stat()
            path.write_bytes(original.replace(b"hello", b"other"))
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
        with pytest.raises(MboxFormatError, match="changed during import"):
            next(records)


@pytest.mark.parametrize("limits", [{"max_message_bytes": 1.5}, {"max_line_bytes": 128.5}, {"max_message_bytes": True}])
def test_resource_limits_require_integer_byte_counts(limits):
    with pytest.raises(ValueError):
        MboxLimits(**limits)


@pytest.mark.parametrize("seed", [3, 29, 103])
def test_cpython_written_mbox_matches_independent_reader(tmp_path, seed):
    # The stdlib reader is only an oracle for its own writer's delimiter dialect,
    # not for arbitrary unescaped prose or a universal MBOX conformance claim.
    rng = random.Random(seed)
    path = tmp_path / "stdlib.mbox"
    with closing(mailbox.mbox(path, create=True)) as writer:
        for index in range(80):
            lines = [rng.choice([
                b"From ordinary body prose", b">From already quoted", b">>>From deeper quote",
                POSTMARK.rstrip(b"\n"), b"", b"a:b", b"\x00\xffbinary-ish",
                "Caf\u00e9 / \u6771\u4eac".encode(),
            ]) for _ in range(rng.randrange(1, 12))]
            writer.add(POSTMARK + f"Subject: record {index}\n\n".encode() + b"\n".join(lines) + b"\n")
    with closing(mailbox.mbox(path, create=False)) as independent:
        expected = []
        for key in independent.iterkeys():
            with independent.get_file(key) as message:
                # CPython removes its one storage blank line; our provenance
                # explicitly includes that line. Do not strip arbitrary body bytes.
                expected.append(message.read() + os.linesep.encode())
    rows = snapshots(path)
    assert len(rows) == 80
    assert [data for _, data in rows] == expected
    for (record, _), raw in zip(rows, expected, strict=True):
        assert record.sha256 == hashlib.sha256(raw).hexdigest()
        assert record.end_offset - record.message_offset == len(raw)


@pytest.mark.parametrize("seed", [5, 31, 103])
def test_mboxrd_quoting_roundtrips_generated_body_bytes(tmp_path, seed):
    rng = random.Random(seed)
    originals = []
    stored = []
    for _ in range(80):
        lines = [rng.choice([b"From here", POSTMARK.rstrip(b"\n"), b"regular", b"", b"\xff\x00"])]
        lines += [b">" * rng.randrange(8) + b"From quote" for _ in range(8)]
        body = b"\n".join(lines) + b"\n"
        original = b"Subject: generated\n\n" + body
        # Independent writer rule, deliberately not our reader's regex.
        quoted = b"\n".join(b">" + line if line.lstrip(b">").startswith(b"From ") else line for line in body.split(b"\n"))
        originals.append(original)
        stored.append(b"Subject: generated\n\n" + quoted)
    path = tmp_path / "rd.mbox"
    path.write_bytes(b"".join(POSTMARK + raw for raw in stored))
    rows = snapshots(path, unescape="mboxrd")
    assert [data for _, data in rows] == originals
    assert [r.sha256 for r, _ in rows] == [hashlib.sha256(raw).hexdigest() for raw in stored]


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
@pytest.mark.parametrize("host", [b"relay.example.test", b"relay.example.test \t", b" \t"])
def test_remote_postmark_padding_remains_supported(tmp_path, ending, host):
    from dead_letter.core.mbox import _ENVELOPE

    envelope = POSTMARK.rstrip(b"\n") + b" remote from " + host + ending
    assert _ENVELOPE.fullmatch(envelope) is not None


def test_malformed_remote_postmark_cannot_backtrack_quadratically():
    import subprocess
    import sys

    from dead_letter.core import mbox

    # Bound the whole child process so a regex regression fails rather than
    # hanging the test runner. Load the actual production module, not a copy
    # of its pattern; no MIME dependencies are needed for this framing test.
    probe = r"""
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("mbox_probe", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
prefix = b"From sender@example.test Thu Jun 11 00:38:38 2020 remote from "
for padding in (b" " * 100_000, b"\t" * 100_000):
    assert module._ENVELOPE.fullmatch(prefix + padding + b"\r") is None
"""
    subprocess.run(
        [sys.executable, "-c", probe, str(Path(mbox.__file__).resolve())],
        check=True, capture_output=True, timeout=10,
    )


@pytest.mark.parametrize("change", [None, "descriptor", "path", "identity"])
def test_stat_and_fstat_use_separate_timestamp_baselines(tmp_path, monkeypatch, change):
    from types import SimpleNamespace
    from dead_letter.core import mbox

    source = tmp_path / "mail.mbox"
    # Model the platform API difference without changing global os.name or
    # dropping timestamp checks. Each API must still detect its own changes.
    common = dict(st_dev=7, st_ino=11, st_size=200, st_mtime_ns=900)
    initial = SimpleNamespace(**common, st_ctime_ns=800)
    initial_path = SimpleNamespace(**common, st_ctime_ns=500)
    current_fd = SimpleNamespace(**vars(initial))
    current_path = SimpleNamespace(**vars(initial_path))
    if change == "descriptor":
        current_fd.st_ctime_ns += 1
    elif change == "path":
        current_path.st_ctime_ns += 1
    elif change == "identity":
        initial_path.st_ino += 1
        current_path.st_ino += 1
    monkeypatch.setattr(mbox.os, "fstat", lambda fd: current_fd)
    monkeypatch.setattr(Path, "stat", lambda path, **kwargs: current_path)
    if change is None:
        mbox._check_source(123, source, initial, initial_path)
    else:
        with pytest.raises(MboxFormatError, match="changed during import"):
            mbox._check_source(123, source, initial, initial_path)
