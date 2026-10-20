"""Real filesystem/process tests of the stdlib-only resume journal."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# Deliberately test the persistence layer without loading the MIME stack.
MODULE = Path(__file__).resolve().parents[2] / "src/dead_letter/core/mbox_resume.py"
spec = importlib.util.spec_from_file_location("resume_journal_test", MODULE)
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)
IDENTITY = {"index": 1, "sha256": "a" * 64, "message_offset": 50, "end_offset": 99}
CONTRACT = {"schema": 1, "source": "synthetic"}
PAYLOAD = b"Synthetic complete markdown\n"


def prepared(journal):
    attempt = journal.start(IDENTITY)
    output = journal.stage(IDENTITY) / journal.name(IDENTITY)
    output.write_bytes(PAYLOAD)
    journal.prepare(IDENTITY, {"state": "normal"})
    return attempt


def test_prepared_recovery_and_reuse(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        output, receipt, status, attempt = journal.recover(IDENTITY)
        assert output.read_bytes() == PAYLOAD
        assert (status, attempt) == ("recovered", 1)
        assert receipt == {"diagnostics": {"state": "normal"}}
        assert not journal.stage(IDENTITY).exists()
        assert journal.recover(IDENTITY)[2:] == ("reused", 1)
    assert len(list(tmp_path.glob("*.md"))) == 1


def test_changed_source_or_options_rejected(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
    with pytest.raises(resume.MboxResumeError, match="mbox_resume_mismatch"):
        with resume.ResumeJournal(tmp_path, {**CONTRACT, "options": "different"}):
            pytest.fail("must reject mismatched contract")
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        assert journal.load(IDENTITY)["phase"] == "prepared"


def test_foreign_output_never_overwritten(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        output = tmp_path / journal.name(IDENTITY)
        output.write_bytes(b"user file")
        with pytest.raises(resume.MboxResumeError, match="without a reusable receipt"):
            journal.start(IDENTITY)
        assert output.read_bytes() == b"user file"
        assert journal.load(IDENTITY) is None


@pytest.mark.parametrize("phase", ["prepared", "complete"])
def test_modified_output_is_a_conflict(tmp_path, phase):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
        if phase == "complete":
            journal.recover(IDENTITY)
        output = tmp_path / journal.name(IDENTITY)
        output.write_bytes(b"user changes")
        with pytest.raises(resume.MboxResumeError, match="was modified"):
            journal.recover(IDENTITY)
        assert output.read_bytes() == b"user changes"


def test_missing_completed_output_retries(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
        output = journal.recover(IDENTITY)[0]
        output.unlink()
        assert journal.recover(IDENTITY) is None
        assert prepared(journal) == 2
        assert journal.recover(IDENTITY)[0].read_bytes() == PAYLOAD


def test_failed_attempt_retries_and_keeps_count(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        journal.start(IDENTITY)
        journal.failed(IDENTITY, {"code": "conversion_error"})
        assert journal.load(IDENTITY)["phase"] == "failed"
        assert prepared(journal) == 2


def test_owned_partial_staging_is_replaced_not_published(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        journal.start(IDENTITY)
        pending = journal.stage(IDENTITY) / journal.name(IDENTITY)
        pending.write_bytes(b"partial")
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        assert journal.recover(IDENTITY) is None
        assert prepared(journal) == 2
        assert journal.recover(IDENTITY)[0].read_bytes() == PAYLOAD


def test_unknown_staging_file_is_not_deleted(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        journal.start(IDENTITY)
        unexpected = journal.stage(IDENTITY) / "user-file.txt"
        unexpected.write_text("keep")
        with pytest.raises(resume.MboxResumeError, match="Unexpected files"):
            journal.start(IDENTITY)
        assert unexpected.read_text() == "keep"


def test_modified_prepared_artifact_not_published(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
        pending = journal.stage(IDENTITY) / journal.name(IDENTITY)
        pending.write_bytes(b"changed")
        with pytest.raises(resume.MboxResumeError, match="artifact was modified"):
            journal.recover(IDENTITY)
        assert not list(tmp_path.glob("*.md"))


@pytest.mark.parametrize("bad_identity", [
    {**IDENTITY, "sha256": "../outside"}, {**IDENTITY, "index": True},
    {**IDENTITY, "index": -1}, {**IDENTITY, "index": 2**70},
])
def test_invalid_record_cannot_choose_paths(tmp_path, bad_identity):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        with pytest.raises(resume.MboxResumeError, match="Invalid resume record"):
            journal.start(bad_identity)


def test_changed_offsets_cannot_reuse(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
        with pytest.raises(resume.MboxResumeError, match="identity or phase"):
            journal.recover({**IDENTITY, "end_offset": 100})


@pytest.mark.parametrize("column,value", [
    ("phase", "unknown"), ("attempt", -1), ("digest", "../outside"),
    ("size", -1), ("receipt", "{}"), ("receipt", "null"),
    ("receipt", '{"diagnostics": [], "output": "../outside"}'),
])
def test_corrupt_receipts_fail_closed(tmp_path, column, value):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
        with journal.db:
            journal.db.execute(f"UPDATE records SET {column}=?", (value,))
        with pytest.raises(resume.MboxResumeError):
            journal.recover(IDENTITY)
        assert not list(tmp_path.glob("*.md"))


def test_oversized_receipt_withholds_output(tmp_path):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        journal.start(IDENTITY)
        (journal.stage(IDENTITY) / journal.name(IDENTITY)).write_bytes(PAYLOAD)
        with pytest.raises(resume.MboxResumeError, match="receipt exceeds"):
            journal.prepare(IDENTITY, {"huge": "x" * resume.MAX_RECEIPT_BYTES})
        assert journal.load(IDENTITY)["phase"] == "started"
        assert not list(tmp_path.glob("*.md"))


def test_no_link_support_never_falls_back_to_copy(tmp_path, monkeypatch):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
        def no_link(*args, **kwargs):
            raise OSError("unsupported")
        monkeypatch.setattr(resume.os, "link", no_link)
        with pytest.raises(OSError, match="unsupported"):
            journal.recover(IDENTITY)
        assert journal.load(IDENTITY)["phase"] == "prepared"
        assert not list(tmp_path.glob("*.md"))


def test_publication_race_preserves_competing_file(tmp_path, monkeypatch):
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
        def racing_link(src, dst):
            Path(dst).write_bytes(b"someone else's file")
            raise FileExistsError()
        monkeypatch.setattr(resume.os, "link", racing_link)
        with pytest.raises(resume.MboxResumeError, match="changed during publication"):
            journal.recover(IDENTITY)
        assert (tmp_path / journal.name(IDENTITY)).read_bytes() == b"someone else's file"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires separate Windows privilege")
@pytest.mark.parametrize("target", ["output", "database", "lock", "stage"])
def test_links_are_rejected_without_touching_target(tmp_path, target):
    root = tmp_path / "out"
    outside = tmp_path / "outside"
    outside.write_bytes(PAYLOAD)
    with resume.ResumeJournal(root, CONTRACT) as journal:
        prepared(journal)
        if target == "output":
            (root / journal.name(IDENTITY)).symlink_to(outside)
            with pytest.raises(resume.MboxResumeError):
                journal.recover(IDENTITY)
        elif target == "stage":
            journal._cleanup(IDENTITY)
            journal.stage(IDENTITY).symlink_to(tmp_path, target_is_directory=True)
            with pytest.raises(resume.MboxResumeError):
                journal.recover(IDENTITY)
    if target in {"database", "lock"}:
        linked = root / ".dead-letter-resume" / ("journal.sqlite3" if target == "database" else "lock")
        linked.unlink()
        linked.symlink_to(outside)
        with pytest.raises(resume.MboxResumeError):
            with resume.ResumeJournal(root, CONTRACT):
                pytest.fail("must reject linked state")
    assert outside.read_bytes() == PAYLOAD


BOOTSTRAP = """
import importlib.util, json, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('resume_child', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
root = Path(sys.argv[2]); point = sys.argv[3]
i = json.loads(sys.argv[4]); contract = json.loads(sys.argv[5])
with m.ResumeJournal(root, contract) as j:
    if point == 'lock':
        (root / 'ready').touch()
        time.sleep(60)
    j.start(i)
    p = j.stage(i) / j.name(i)
    if point == 'started': os._exit(77)
    p.write_bytes(b'Synthetic complete markdown\\n')
    if point == 'staged': os._exit(77)
    j.prepare(i, {'state': 'normal'})
    if point == 'prepared': os._exit(77)
    original = m.os.link
    def crash_after_link(src, dst):
        original(src, dst)
        os._exit(77)
    if point == 'published': m.os.link = crash_after_link
    if point == 'committed': j._cleanup = lambda identity: os._exit(77)
    j.recover(i)
    os._exit(77)
"""


def command(root, point):
    return [sys.executable, "-c", BOOTSTRAP, str(MODULE), str(root), point,
            json.dumps(IDENTITY), json.dumps(CONTRACT)]


@pytest.mark.parametrize("point", ["started", "staged", "prepared", "published", "committed"])
def test_process_death_reconciles_every_publication_boundary(tmp_path, point):
    result = subprocess.run(command(tmp_path, point), timeout=15, capture_output=True)
    assert result.returncode == 77, result.stderr.decode()
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        recovered = journal.recover(IDENTITY)
        if point in {"started", "staged"}:
            assert recovered is None
            assert prepared(journal) == 2
            recovered = journal.recover(IDENTITY)
        else:
            assert recovered is not None
            assert recovered[3] == 1  # No second conversion attempt.
        assert recovered[0].read_bytes() == PAYLOAD
        assert journal.load(IDENTITY)["phase"] == "complete"
        assert not journal.stage(IDENTITY).exists()
        assert len(list(tmp_path.glob("*.md"))) == 1


def test_lock_rejects_second_process_and_releases_after_kill(tmp_path):
    import time
    child = subprocess.Popen(command(tmp_path, "lock"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists():
            assert child.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        with pytest.raises(resume.MboxResumeError, match="mbox_resume_busy"):
            with resume.ResumeJournal(tmp_path, CONTRACT):
                pytest.fail("must not admit a second writer")
    finally:
        child.kill()
        child.communicate(timeout=10)
    with resume.ResumeJournal(tmp_path, CONTRACT) as journal:
        prepared(journal)
        assert journal.recover(IDENTITY)[0].read_bytes() == PAYLOAD
