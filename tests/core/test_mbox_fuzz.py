"""Small, reproducible byte mutations over synthetic MBOX seeds."""

from __future__ import annotations

import hashlib
import json
import os
import random
from contextlib import closing
from pathlib import Path

import pytest

from dead_letter.backend.mbox_cli import run_mbox
from dead_letter.core.mbox import MboxFormatError, MboxLimits, iter_mbox
from dead_letter.core.types import ConvertOptions

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
SEEDS = (
    POSTMARK + b"Subject: one\n\nbody\n" + POSTMARK + b"Subject: two\n\n>From quote\n",
    POSTMARK + POSTMARK + b"Broken header\n folded\n\n" + b"x" * 128 + b"\n",
    (POSTMARK + b"Message-ID: <same@example.test>\n\nFrom fake prose\n") * 2,
    (POSTMARK + b"Subject: CRLF\r\n\r\n>From quote\r\n").replace(b"2020\n", b"2020\r\n"),
)
ITERATIONS = int(os.environ.get("DEAD_LETTER_FUZZ_ITERATIONS", "300" if os.environ.get("HYPOTHESIS_PROFILE") == "fuzz" else "24"))
if not 1 <= ITERATIONS <= 2000:
    raise ValueError("DEAD_LETTER_FUZZ_ITERATIONS must be between 1 and 2000")


def mutate(seed: int) -> bytes:
    rng = random.Random(seed)
    data = SEEDS[seed % len(SEEDS)]
    for _ in range(rng.randint(1, 4)):
        operation = rng.choice(("bit", "insert", "delete", "duplicate", "truncate", "postmark", "cr"))
        position = rng.randrange(len(data) + 1)
        if operation == "bit" and data:
            at = min(position, len(data) - 1)
            data = data[:at] + bytes((data[at] ^ (1 << rng.randrange(8)),)) + data[at + 1:]
        elif operation == "insert":
            data = data[:position] + rng.choice((b"\x00", b"\n", b"From ", b">From ")) + data[position:]
        elif operation == "delete" and data:
            data = data[:position] + data[min(position + rng.randint(1, 32), len(data)):]
        elif operation == "duplicate":
            lines = data.splitlines(keepends=True)
            if lines:
                at = rng.randrange(len(lines))
                lines.insert(at, lines[at])
                data = b"".join(lines)
        elif operation == "truncate":
            data = data[:position]
        elif operation == "postmark":
            lines = data.splitlines(keepends=True)
            lines.insert(rng.randrange(len(lines) + 1), rng.choice((POSTMARK, b">" + POSTMARK)))
            data = b"".join(lines)
        elif operation == "cr":
            data = data[:position] + b"\r" + data[position:]
    assert len(data) <= 4096
    return data


@pytest.mark.parametrize("seed", range(ITERATIONS), ids=lambda seed: f"seed-{seed}")
def test_seeded_mutations_account_for_bytes_and_bound_reads(tmp_path, monkeypatch, seed):
    source = tmp_path / "mutated.mbox"
    data = mutate(seed)
    source.write_bytes(data)
    original_open = Path.open
    bytes_read = 0

    class BoundedSource:
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
            assert 0 < size <= 129, f"seed={seed}: unbounded read {size}"
            chunk = self.stream.readline(size)
            bytes_read += len(chunk)
            return chunk

    def guarded_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return BoundedSource(stream) if path == source and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    cursor = count = 0
    fatal = False
    with closing(iter_mbox(source, limits=MboxLimits(max_message_bytes=256, max_line_bytes=128))) as rows:
        try:
            for record in rows:
                count += 1
                assert record.index == count, f"seed={seed}"
                assert cursor == record.envelope_offset < record.message_offset <= record.end_offset <= len(data), f"seed={seed}"
                assert data[record.envelope_offset:record.message_offset].startswith(b"From "), f"seed={seed}"
                assert record.sha256 == hashlib.sha256(data[record.message_offset:record.end_offset]).hexdigest(), f"seed={seed}"
                if record.path is not None:
                    assert record.path.read_bytes() == data[record.message_offset:record.end_offset], f"seed={seed}"
                    assert record.path.stat().st_size <= 256, f"seed={seed}"
                else:
                    assert record.error_code in {"mbox_empty_message", "mbox_message_too_large", "mbox_line_too_long"}, f"seed={seed}"
                cursor = record.end_offset
        except MboxFormatError:
            # A bad preamble or unsupported Content-Length is archive-fatal;
            # the unreported suffix starts at the last completed record.
            fatal = True
    assert bytes_read <= len(data), f"seed={seed}"
    assert cursor <= len(data), f"seed={seed}"
    if not fatal:
        assert cursor == len(data), f"seed={seed}: unaccounted bytes"


def test_report_matches_streamed_empty_and_valid_records(tmp_path):
    source = tmp_path / "report.mbox"
    first = b"Message-ID: <same@example.test>\nSubject: one\n\nbody\n"
    last = b"Message-ID: <same@example.test>\nSubject: last\n\nbody\n"
    source.write_bytes(POSTMARK + first + POSTMARK + POSTMARK + last)
    root = tmp_path / "out"
    assert run_mbox(source, output=str(root), options=ConvertOptions(dry_run=True, report=True)) == 1
    report = json.loads((root / ".dead-letter-report.json").read_text(encoding="utf-8"))
    assert report["summary"] == {"total": 3, "written": 0, "skipped": 2, "errors": 1}
    rows = report["results"]
    assert [row["mbox"]["index"] for row in rows] == [1, 2, 3]
    assert [row["success"] for row in rows] == [True, False, True]
    assert rows[1]["error"]["code"] == "mbox_empty_message"
    assert [row["mbox"]["sha256"] for row in rows] == [
        hashlib.sha256(raw).hexdigest() for raw in (first, b"", last)
    ]
    assert rows[-1]["mbox"]["end_offset"] == source.stat().st_size
