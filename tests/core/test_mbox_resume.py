"""Resumable importer integration, including real timed workers and process death."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.types import ConvertOptions

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"


def archive(tmp_path, *, empty=False):
    path = tmp_path / "source.mbox"
    path.write_bytes(b"".join(POSTMARK + (
        b"" if empty and n == 2 else
        f"From: sender@example.test\nSubject: Synthetic {n}\nX-Gmail-Labels: Inbox,Receipts\n\nBody {n}\n\n".encode()
    ) for n in range(1, 4)))
    return path


@pytest.mark.parametrize("timeout", [None, 30])
def test_resume_reuses_without_calling_converter(tmp_path, monkeypatch, timeout):
    import dead_letter.core.mbox_import as importer
    import dead_letter.core.mbox_isolation as isolation

    source = archive(tmp_path)
    before = source.read_bytes()
    root = tmp_path / "out"
    ordinary = list(convert_mbox(source, output=tmp_path / "baseline", timeout_seconds=timeout))
    first = list(convert_mbox(source, output=root, resume=True, timeout_seconds=timeout))
    assert len(first) == 3 and all(row.success for row in first)
    assert [row.recovery for row in first] == [{"status": "new", "attempt": 1}] * 3
    assert [row.output.read_bytes() for row in first] == [row.output.read_bytes() for row in ordinary]
    assert [row.mbox for row in first] == [row.mbox for row in ordinary]

    def forbidden(*args, **kwargs):
        pytest.fail("completed records must not launch conversion again")
    monkeypatch.setattr(importer, "_convert_record", forbidden)
    monkeypatch.setattr(isolation, "convert_record_isolated", forbidden)
    second = list(convert_mbox(source, output=root, resume=True, timeout_seconds=timeout))
    assert all(row.success for row in second)
    assert [row.recovery for row in second] == [{"status": "reused", "attempt": 1}] * 3
    assert [row.output for row in first] == [row.output for row in second]
    assert len(list(root.glob("*.md"))) == 3
    assert source.read_bytes() == before


def test_mixed_failure_retries_only_failed_record(tmp_path):
    source = archive(tmp_path, empty=True)
    root = tmp_path / "out"
    first = list(convert_mbox(source, output=root, resume=True))
    assert [row.success for row in first] == [True, False, True]
    second = list(convert_mbox(source, output=root, resume=True))
    assert [row.recovery["status"] for row in second] == ["reused", "retried", "reused"]
    assert second[1].recovery["attempt"] == 2
    assert second[1].error["code"] == "mbox_empty_message"
    assert len(list(root.glob("*.md"))) == 2


def test_modified_output_conflict_keeps_user_edits(tmp_path):
    source = archive(tmp_path)
    root = tmp_path / "out"
    rows = list(convert_mbox(source, output=root, resume=True))
    rows[1].output.write_bytes(b"user edit")
    resumed = list(convert_mbox(source, output=root, resume=True))
    assert resumed[0].recovery["status"] == "reused"
    assert len(resumed) == 2
    assert resumed[-1].mbox is None
    assert resumed[-1].error["code"] == "mbox_resume_conflict"
    assert rows[1].output.read_bytes() == b"user edit"
    assert len(list(root.glob("*.md"))) == 3


def test_missing_output_is_recreated_at_same_name(tmp_path):
    source = archive(tmp_path)
    root = tmp_path / "out"
    rows = list(convert_mbox(source, output=root, resume=True))
    missing, original = rows[1].output, rows[1].output.read_bytes()
    missing.unlink()
    resumed = list(convert_mbox(source, output=root, resume=True))
    assert [row.recovery["status"] for row in resumed] == ["reused", "retried", "reused"]
    assert resumed[1].output == missing
    assert missing.read_bytes() == original
    assert resumed[1].recovery["attempt"] == 2


@pytest.mark.parametrize("change", ["source", "options", "limits", "worker", "engine"])
def test_mismatched_import_rejected_before_message_conversion(tmp_path, monkeypatch, change):
    from dead_letter.core.mbox import MboxLimits
    from dead_letter.core import mbox_resume

    source = archive(tmp_path)
    root = tmp_path / "out"
    first = list(convert_mbox(source, output=root, resume=True))
    originals = [row.output.read_bytes() for row in first]
    kwargs = {}
    if change == "source":
        # Same-size mutation with the mtime restored must still fail the full hash.
        saved = source.stat()
        source.write_bytes(source.read_bytes().replace(b"Body 2", b"Body X"))
        os.utime(source, ns=(saved.st_atime_ns, saved.st_mtime_ns))
    elif change == "options":
        kwargs["options"] = ConvertOptions(strip_signatures=True)
    elif change == "limits":
        kwargs["limits"] = MboxLimits(max_message_bytes=128)
    elif change == "worker":
        kwargs["timeout_seconds"] = 30
    else:
        monkeypatch.setattr(mbox_resume, "_engine_fingerprint", lambda: {"changed": True})
    rows = list(convert_mbox(source, output=root, resume=True, **kwargs))
    assert len(rows) == 1 and rows[0].error["code"] == "mbox_resume_mismatch"
    assert [row.output.read_bytes() for row in first] == originals


@pytest.mark.parametrize("kwargs", [
    {"bundles": True}, {"options": ConvertOptions(dry_run=True)},
    {"options": ConvertOptions(delete_eml=True)}, {"resume": "yes"},
])
def test_unsupported_resume_modes_fail_before_output(tmp_path, kwargs):
    source = archive(tmp_path)
    root = tmp_path / "out"
    with pytest.raises(ValueError):
        list(convert_mbox(source, output=root, **{"resume": True, **kwargs}))
    assert not root.exists()


def test_non_resume_reruns_remain_collision_safe_not_deduplicated(tmp_path):
    source = archive(tmp_path)
    root = tmp_path / "out"
    first = list(convert_mbox(source, output=root))
    second = list(convert_mbox(source, output=root))
    assert all(row.recovery is None for row in first + second)
    assert len(list(root.glob("*.md"))) == 6
    assert not (root / ".dead-letter-resume").exists()


BOOTSTRAP = """
import os, sys
from pathlib import Path
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core import mbox_resume as m
source, root, point, mode = sys.argv[1:]
if point == 'started':
    original = m.ResumeJournal.start
    def start(self, identity):
        original(self, identity)
        os._exit(77)
    m.ResumeJournal.start = start
elif point in ('staged', 'prepared'):
    original = m.ResumeJournal.prepare
    def prepare(self, identity, diagnostics):
        if point == 'staged': os._exit(77)
        original(self, identity, diagnostics)
        os._exit(77)
    m.ResumeJournal.prepare = prepare
elif point == 'published':
    original = m.os.link
    def link(src, dst):
        original(src, dst)
        os._exit(77)
    m.os.link = link
elif point == 'committed':
    original = m.ResumeJournal._cleanup
    def cleanup(self, identity):
        if self.load(identity)['phase'] == 'complete': os._exit(77)
        original(self, identity)
    m.ResumeJournal._cleanup = cleanup
for result in convert_mbox(source, output=root, resume=True,
                           timeout_seconds=30 if mode == 'worker' else None):
    assert result.success, result.error
raise RuntimeError('crash injection was not reached')
"""


@pytest.mark.parametrize("mode", ["direct", "worker"])
@pytest.mark.parametrize("point", ["started", "staged", "prepared", "published", "committed"])
def test_actual_import_recovers_from_process_death(tmp_path, mode, point):
    source = archive(tmp_path)
    before = hashlib.sha256(source.read_bytes()).digest()
    root = tmp_path / "out"
    process = subprocess.run(
        [sys.executable, "-c", BOOTSTRAP, str(source), str(root), point, mode],
        capture_output=True, timeout=60,
    )
    assert process.returncode == 77, process.stderr.decode()
    rows = list(convert_mbox(source, output=root, resume=True, timeout_seconds=30 if mode == "worker" else None))
    assert len(rows) == 3 and all(row.success for row in rows)
    expected = "retried" if point in {"started", "staged"} else "reused" if point == "committed" else "recovered"
    assert rows[0].recovery["status"] == expected
    assert [row.mbox["index"] for row in rows] == [1, 2, 3]
    assert len(list(root.glob("*.md"))) == 3
    assert hashlib.sha256(source.read_bytes()).digest() == before
