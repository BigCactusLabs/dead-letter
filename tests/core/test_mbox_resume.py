"""Resumable importer integration, including real timed workers and process death."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import closing
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
    assert "use a new output directory" in rows[0].error["message"]
    assert [row.output.read_bytes() for row in first] == originals


def test_journal_from_other_selectolax_version_is_rejected(tmp_path, monkeypatch):
    from dead_letter.core import mbox_resume

    installed = mbox_resume.version
    assert int(installed("selectolax").split(".")[0]) >= 1
    source = archive(tmp_path)
    root = tmp_path / "out"
    # A journal started on a Modest-era release records its selectolax version.
    monkeypatch.setattr(
        mbox_resume, "version", lambda name: "0.4.12" if name == "selectolax" else installed(name),
    )
    first = list(convert_mbox(source, output=root, resume=True))
    originals = [row.output.read_bytes() for row in first]
    monkeypatch.setattr(mbox_resume, "version", installed)

    rows = list(convert_mbox(source, output=root, resume=True))

    assert len(rows) == 1 and rows[0].error["code"] == "mbox_resume_mismatch"
    assert "use a new output directory" in rows[0].error["message"]
    assert [row.output.read_bytes() for row in first] == originals


@pytest.mark.parametrize("kwargs", [
    {"bundles": "yes"}, {"options": ConvertOptions(dry_run=True)},
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
from contextlib import closing
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


def test_oversized_receipt_fails_only_that_record_and_retries(tmp_path):
    # Untrusted HTML whose stripped-pixel diagnostics exceed the 1 MiB receipt cap.
    pixels = "".join(
        f'<img src="https://t.example.test/px/{i:06d}/{"a" * 300}" width="1" height="1">\n' for i in range(4000)
    )
    hostile = (
        "From: sender@example.test\nSubject: Pixels\nMIME-Version: 1.0\n"
        f"Content-Type: text/html; charset=utf-8\n\n<html><body><p>hi</p>{pixels}</body></html>\n\n"
    ).encode()
    source = tmp_path / "source.mbox"
    source.write_bytes(b"".join(POSTMARK + body for body in (
        b"From: sender@example.test\nSubject: One\n\nBody 1\n\n", hostile,
        b"From: sender@example.test\nSubject: Three\n\nBody 3\n\n",
    )))
    root = tmp_path / "out"
    options = ConvertOptions(strip_tracking_pixels=True)
    first = list(convert_mbox(source, output=root, options=options, resume=True))
    assert [row.success for row in first] == [True, False, True]
    assert [row.mbox["index"] for row in first] == [1, 2, 3]
    assert first[1].output is None and first[1].error["code"] == "mbox_resume_receipt_limit"
    published = sorted(root.glob("*.md"))
    assert published == sorted([first[0].output, first[2].output])
    assert not [path for path in (root / ".dead-letter-resume").iterdir() if path.is_dir()]
    originals = [path.read_bytes() for path in published]

    second = list(convert_mbox(source, output=root, options=options, resume=True))
    assert [row.success for row in second] == [True, False, True]
    assert [row.recovery for row in second] == [
        {"status": "reused", "attempt": 1}, {"status": "retried", "attempt": 2}, {"status": "reused", "attempt": 1},
    ]
    assert second[1].error["code"] == "mbox_resume_receipt_limit"
    assert sorted(root.glob("*.md")) == published
    assert [path.read_bytes() for path in published] == originals


def test_malformed_source_reports_fixed_format_message(tmp_path):
    source = tmp_path / "source.mbox"
    source.write_bytes(b"Subject: not an mbox\n\nBody\n")
    ordinary = list(convert_mbox(source, output=tmp_path / "plain"))
    rows = list(convert_mbox(source, output=tmp_path / "out", resume=True))
    assert len(rows) == 1 and rows[0].mbox is None
    assert rows[0].error["code"] == ordinary[0].error["code"] == "mbox_archive_error"
    assert rows[0].error["message"] == ordinary[0].error["message"]
    assert "postmark" in rows[0].error["message"]


@pytest.mark.parametrize("code", ["EPERM", "ENOTSUP", "EOPNOTSUPP", "EXDEV"])
def test_missing_hard_link_support_has_clear_message(tmp_path, monkeypatch, code):
    import errno
    from dead_letter.core import mbox_resume

    def no_link(src, dst):
        raise OSError(getattr(errno, code), "private strerror", str(src))
    monkeypatch.setattr(mbox_resume.os, "link", no_link)
    rows = list(convert_mbox(archive(tmp_path), output=tmp_path / "out", resume=True))
    assert len(rows) == 1 and rows[0].mbox is None
    assert rows[0].error["code"] == "mbox_resume_io_error"
    assert rows[0].error["message"] == "Output filesystem does not support hard links required for resume"
    assert not list((tmp_path / "out").glob("*.md"))


def test_other_os_errors_name_errno_without_private_text(tmp_path, monkeypatch):
    import errno
    from dead_letter.core import mbox_resume

    def full(src, dst):
        raise OSError(errno.ENOSPC, "private strerror", str(tmp_path / "private-name"))
    monkeypatch.setattr(mbox_resume.os, "link", full)
    rows = list(convert_mbox(archive(tmp_path), output=tmp_path / "out", resume=True))
    assert len(rows) == 1 and rows[0].mbox is None
    assert rows[0].error["code"] == "mbox_resume_io_error"
    assert "ENOSPC" in rows[0].error["message"]
    assert "private" not in rows[0].error["message"] and str(tmp_path) not in rows[0].error["message"]


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permission enforcement")
def test_unreadable_source_yields_fatal_result(tmp_path):
    source = archive(tmp_path)
    source.chmod(0)
    try:
        rows = list(convert_mbox(source, output=tmp_path / "out", resume=True))
    finally:
        source.chmod(0o600)
    assert len(rows) == 1 and rows[0].mbox is None and not rows[0].success
    assert rows[0].error["code"] == "mbox_resume_io_error"
    assert "EACCES" in rows[0].error["message"]


def test_engine_fingerprint_accepts_symlinked_package_sources(tmp_path, monkeypatch):
    from dead_letter.core import mbox_resume

    real = mbox_resume._engine_fingerprint()
    package = Path(mbox_resume.__file__).resolve().parents[1]
    (tmp_path / "core").mkdir()
    for path in (package / "core").glob("*.py"):
        (tmp_path / "core" / path.name).symlink_to(path)
    for name in ("_mbox_worker.py", "__init__.py"):
        (tmp_path / name).symlink_to(package / name)
    monkeypatch.setattr(mbox_resume, "__file__", str(tmp_path / "core" / "mbox_resume.py"))
    assert mbox_resume._engine_fingerprint() == real


def test_complete_rerun_skips_directory_sync_and_journal_write(tmp_path, monkeypatch):
    import sqlite3
    from dead_letter.core import mbox_resume

    source = archive(tmp_path)
    root = tmp_path / "out"
    first = list(convert_mbox(source, output=root, resume=True))
    synced = []
    original = mbox_resume._sync_directory
    monkeypatch.setattr(mbox_resume, "_sync_directory", lambda path: (synced.append(path), original(path)))
    database = root / ".dead-letter-resume" / "journal.sqlite3"
    with closing(sqlite3.connect(database)) as db:
        before = db.execute("SELECT * FROM records ORDER BY id").fetchall()
    stamp = database.stat().st_mtime_ns
    second = list(convert_mbox(source, output=root, resume=True))
    assert [row.recovery["status"] for row in second] == ["reused"] * 3
    assert [row.output for row in second] == [row.output for row in first]
    # Only the journal open syncs its directories; reused records write nothing.
    assert len(synced) == 2
    assert database.stat().st_mtime_ns == stamp
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT * FROM records ORDER BY id").fetchall() == before
