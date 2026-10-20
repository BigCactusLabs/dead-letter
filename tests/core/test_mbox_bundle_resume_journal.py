"""Real-filesystem bundle journal tests; no MIME dependencies or private mail."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "src/dead_letter/core/mbox_resume.py"
spec = importlib.util.spec_from_file_location("bundle_resume_journal_test", MODULE)
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)
IDENTITY = {"index": 1, "sha256": "a" * 64, "message_offset": 50, "end_offset": 99}
CONTRACT = {"schema": 1, "source": "synthetic"}
FILES = {"message.md": b"Synthetic markdown\n", "source.eml": b"Subject: Synthetic\n\nBody\n",
         "attachments/data.bin": bytes(range(256)), "attachments/empty.txt": b"",
         "attachments/caf\u00e9.txt": b"unicode name"}


def journal(root):
    return resume.ResumeJournal(root, CONTRACT, bundles=True)


def tree(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def stage(j):
    attempt = j.start(IDENTITY)
    bundle = j.stage(IDENTITY) / j.name(IDENTITY)
    for name, data in FILES.items():
        p = bundle / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return attempt, bundle


def prepared(j):
    attempt, bundle = stage(j)
    j.prepare(IDENTITY, {"state": "normal"})
    return attempt, bundle


def test_publish_entire_bundle_and_reuse(tmp_path):
    with journal(tmp_path) as j:
        prepared(j)
        output, receipt, status, attempt = j.recover(IDENTITY)
        assert output.name == "message.md"
        assert tree(output.parent) == FILES
        assert (status, attempt) == ("recovered", 1)
        assert receipt["diagnostics"] == {"state": "normal"}
        assert not j.stage(IDENTITY).exists()
    with journal(tmp_path) as j:
        assert j.recover(IDENTITY)[2:] == ("reused", 1)
        assert tree(output.parent) == FILES


@pytest.mark.parametrize("attachments", [False, True])
def test_no_or_empty_attachment_directory(tmp_path, attachments):
    with journal(tmp_path) as j:
        _, bundle = stage(j)
        shutil.rmtree(bundle / "attachments")
        if attachments:
            (bundle / "attachments").mkdir()
        j.prepare(IDENTITY, None)
        output = j.recover(IDENTITY)[0]
        assert (output.parent / "attachments").exists() is attachments
        assert j.recover(IDENTITY)[2] == "reused"


@pytest.mark.parametrize("phase", ["prepared", "complete"])
@pytest.mark.parametrize("change", ["markdown", "source", "attachment", "missing", "extra", "directory", "empty-dir"])
def test_any_changed_member_is_conflict_without_writes(tmp_path, phase, change):
    with journal(tmp_path) as j:
        _, bundle = prepared(j)
        if phase == "complete":
            bundle = j.recover(IDENTITY)[0].parent
        if change in {"markdown", "source", "attachment"}:
            name = {"markdown": "message.md", "source": "source.eml", "attachment": "attachments/data.bin"}[change]
            (bundle / name).write_bytes(b"user edit")
        elif change == "missing":
            (bundle / "attachments/data.bin").unlink()
        elif change == "extra":
            (bundle / "attachments/user.txt").write_bytes(b"keep")
        elif change == "directory":
            (bundle / "attachments/nested").mkdir()
        else:
            shutil.rmtree(bundle / "attachments")
        before = tree(bundle)
        with pytest.raises(resume.MboxResumeError):
            j.recover(IDENTITY)
        assert tree(bundle) == before
        assert j.load(IDENTITY)["phase"] == phase


@pytest.mark.parametrize("occupied", ["empty-directory", "directory", "file", "identical-bundle"])
def test_foreign_destinations_never_replaced_or_adopted(tmp_path, occupied):
    with journal(tmp_path) as j:
        _, bundle = prepared(j)
        target = tmp_path / j.name(IDENTITY)
        if occupied == "file":
            target.write_bytes(b"keep")
        elif occupied == "identical-bundle":
            shutil.copytree(bundle, target)
        else:
            target.mkdir()
            if occupied == "directory":
                (target / "user.txt").write_bytes(b"keep")
        before = target.lstat()
        with pytest.raises(resume.MboxResumeError):
            j.recover(IDENTITY)
        assert os.path.samestat(before, target.lstat())
        assert tree(bundle) == FILES
        assert j.load(IDENTITY)["phase"] == "prepared"


def test_publication_race_retains_staged_and_foreign_directory(tmp_path, monkeypatch):
    with journal(tmp_path) as j:
        _, bundle = prepared(j)
        real = j._publish_bundle
        def competing(pending, target):
            target.mkdir()
            (target / "user.txt").write_bytes(b"keep")
            real(pending, target)
        monkeypatch.setattr(j, "_publish_bundle", competing)
        with pytest.raises(resume.MboxResumeError, match="appeared during"):
            j.recover(IDENTITY)
        assert tree(bundle) == FILES
        assert (tmp_path / j.name(IDENTITY) / "user.txt").read_bytes() == b"keep"


def test_partial_staging_is_retained_privately_before_retry(tmp_path):
    with journal(tmp_path) as j:
        _, bundle = stage(j)
        (bundle / "message.md").write_bytes(b"partial")
        (bundle / "source.eml").unlink()
    with journal(tmp_path) as j:
        assert j.recover(IDENTITY) is None
        assert prepared(j)[0] == 2
        abandoned = j.state / f".abandoned-{j.name(IDENTITY)}-1"
        assert (abandoned / j.name(IDENTITY) / "message.md").read_bytes() == b"partial"
        assert tree(j.recover(IDENTITY)[0].parent) == FILES


def test_unknown_staging_is_not_removed(tmp_path):
    with journal(tmp_path) as j:
        stage(j)
        other = j.stage(IDENTITY) / "user-file"
        other.write_bytes(b"keep")
        with pytest.raises(resume.MboxResumeError, match="Unexpected files"):
            j.start(IDENTITY)
        assert other.read_bytes() == b"keep"


def test_wholly_missing_bundle_retries_at_original_name(tmp_path):
    with journal(tmp_path) as j:
        prepared(j)
        bundle = j.recover(IDENTITY)[0].parent
        shutil.rmtree(bundle)
        assert j.recover(IDENTITY) is None
        assert prepared(j)[0] == 2
        assert j.recover(IDENTITY)[0].parent == bundle
        assert tree(bundle) == FILES


@pytest.mark.parametrize("field,value", [
    ("manifest", {}), ("manifest", {"attachments": False, "files": {}}),
    ("directory_id", True), ("directory_id", "../outside"),
    ("directory_id", -1), ("directory_id", [0, 1]), ("diagnostics", []),
])
def test_corrupt_bundle_receipts_fail_closed(tmp_path, field, value):
    with journal(tmp_path) as j:
        _, bundle = prepared(j)
        receipt = j.load(IDENTITY)["receipt"]
        receipt[field] = value
        with j.db:
            j.db.execute("UPDATE records SET receipt=?", (json.dumps(receipt),))
        with pytest.raises(resume.MboxResumeError):
            j.recover(IDENTITY)
        assert tree(bundle) == FILES
        assert not (tmp_path / j.name(IDENTITY)).exists()


@pytest.mark.parametrize("name", ["../outside", "attachments/../x", "attachments/x/y", "attachments/\\outside", "attachments/", "/x"])
def test_saved_manifest_cannot_choose_paths(tmp_path, name):
    with journal(tmp_path) as j:
        prepared(j)
        receipt = j.load(IDENTITY)["receipt"]
        receipt["manifest"]["files"][name] = [0, "a" * 64]
        with j.db:
            j.db.execute("UPDATE records SET receipt=?", (json.dumps(receipt),))
        with pytest.raises(resume.MboxResumeError):
            j.recover(IDENTITY)


@pytest.mark.parametrize("kind", ["members", "receipt"])
def test_manifest_limits_fail_record_and_retain_one_private_copy(tmp_path, monkeypatch, kind):
    with journal(tmp_path) as j:
        _, bundle = stage(j)
        if kind == "members":
            monkeypatch.setattr(resume, "MAX_BUNDLE_FILES", 3)
        def no_hashing(*args, **kwargs):
            pytest.fail("limits are checked before members are hashed")
        monkeypatch.setattr(resume, "_digest", no_hashing)
        with pytest.raises(resume.MboxResumeError, match="mbox_resume_receipt_limit") as caught:
            j.prepare(IDENTITY, {"huge": "x" * resume.MAX_RECEIPT_BYTES} if kind == "receipt" else None)
        assert j.load(IDENTITY)["phase"] == "started"
        # The importer then records a per-record failure; cleanup must not raise.
        j.failed(IDENTITY, {"code": caught.value.code, "message": caught.value.message, "stage": "resume"})
        assert j.load(IDENTITY)["phase"] == "failed"
        assert not j.stage(IDENTITY).exists()
        assert not (tmp_path / j.name(IDENTITY)).exists()
        retained = [p.name for p in j.state.glob(".abandoned-*")]
        assert retained == [f".abandoned-{j.name(IDENTITY)}-limit"]
        assert tree(j.state / retained[0] / j.name(IDENTITY)) == FILES
        assert j.retained_limit(IDENTITY) == {
            "code": "mbox_resume_receipt_limit", "message": caught.value.message, "stage": "resume",
        }
    with journal(tmp_path) as j:
        assert j.recover(IDENTITY) is None
        assert j.start(IDENTITY) == 2  # The retained copy never blocks a retry.


def test_over_limit_unprepared_staging_does_not_block_retry(tmp_path, monkeypatch):
    with journal(tmp_path) as j:
        stage(j)
    monkeypatch.setattr(resume, "MAX_BUNDLE_FILES", 3)
    with journal(tmp_path) as j:
        assert j.start(IDENTITY) == 2
        abandoned = j.state / f".abandoned-{j.name(IDENTITY)}-1"
        assert tree(abandoned / j.name(IDENTITY)) == FILES


def test_remount_with_new_device_number_reuses_bundle(tmp_path, monkeypatch):
    with journal(tmp_path) as j:
        prepared(j)
        output = j.recover(IDENTITY)[0].parent
    real = Path.lstat

    class Remounted:
        def __init__(self, value):
            self._value = value

        def __getattr__(self, name):
            return getattr(self._value, name)

        @property
        def st_dev(self):
            return self._value.st_dev + 1

    def lstat(path):
        value = real(path)
        return Remounted(value) if path in (output, output / "attachments") else value

    monkeypatch.setattr(Path, "lstat", lstat)
    with journal(tmp_path) as j:
        assert j.recover(IDENTITY)[2:] == ("reused", 1)
        assert tree(output) == FILES


def test_failed_probe_leaves_layout_unbound(tmp_path, monkeypatch):
    def unavailable(*args):
        raise OSError("unsupported")
    monkeypatch.setattr(resume, "_rename_directory_noreplace", unavailable)
    with pytest.raises(resume.MboxResumeError, match="mbox_resume_unsupported"):
        with journal(tmp_path):
            pytest.fail("must not enter without publication support")
    monkeypatch.undo()
    with resume.ResumeJournal(tmp_path, CONTRACT):  # A flat rerun is not a mismatch.
        pass


def test_bundle_and_flat_layouts_cannot_share_journal(tmp_path):
    with journal(tmp_path):
        pass
    with pytest.raises(resume.MboxResumeError, match="mbox_resume_mismatch"):
        with resume.ResumeJournal(tmp_path, CONTRACT):
            pass


def test_no_replace_probe_rejects_unsafe_rename(tmp_path, monkeypatch):
    # Force the unsafe Unix operation even on Windows through an explicit mock.
    def replace_empty(source, target):
        if target.exists():
            target.rmdir()
        os.rename(source, target)
    monkeypatch.setattr(resume, "_rename_directory_noreplace", replace_empty)
    with pytest.raises(resume.MboxResumeError, match="mbox_resume_unsupported"):
        with journal(tmp_path):
            pytest.fail("must not enter with unsafe publication")


def test_no_replace_probe_fails_closed_without_filesystem_support(tmp_path, monkeypatch):
    def unavailable(*args):
        raise OSError("unsupported")
    monkeypatch.setattr(resume, "_rename_directory_noreplace", unavailable)
    with pytest.raises(resume.MboxResumeError, match="mbox_resume_unsupported"):
        with journal(tmp_path):
            pytest.fail("must not enter without publication support")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires separate Windows privilege")
@pytest.mark.parametrize("location", ["bundle", "attachments", "member"])
def test_links_are_never_followed(tmp_path, location):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private").write_bytes(b"keep")
    with journal(tmp_path / "out") as j:
        _, bundle = stage(j)
        path = bundle if location == "bundle" else bundle / "attachments" if location == "attachments" else bundle / "source.eml"
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        path.symlink_to(outside if location != "member" else outside / "private")
        with pytest.raises(resume.MboxResumeError):
            j.prepare(IDENTITY, None)
        assert (outside / "private").read_bytes() == b"keep"


BOOTSTRAP = r'''
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("r", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
root, point = Path(sys.argv[2]), sys.argv[3]
identity = {"index": 1, "sha256": "a" * 64, "message_offset": 50, "end_offset": 99}
with m.ResumeJournal(root, {"schema": 1, "source": "synthetic"}, bundles=True) as j:
    j.start(identity)
    if point == "started": os._exit(77)
    bundle = j.stage(identity) / j.name(identity)
    (bundle / "attachments").mkdir(parents=True)
    (bundle / "message.md").write_bytes(b"markdown")
    (bundle / "source.eml").write_bytes(b"source")
    (bundle / "attachments/data").write_bytes(b"attachment")
    if point == "staged": os._exit(77)
    j.prepare(identity, None)
    if point == "prepared": os._exit(77)
    if point == "published":
        real = j._publish_bundle
        def publish(pending, target):
            real(pending, target); os._exit(77)
        j._publish_bundle = publish
    if point == "committed":
        j._cleanup = lambda identity: os._exit(77)
    j.recover(identity)
    raise RuntimeError("injection not reached")
'''


@pytest.mark.parametrize("point", ["started", "staged", "prepared", "published", "committed"])
def test_recover_after_actual_process_death(tmp_path, point):
    result = subprocess.run([sys.executable, "-c", BOOTSTRAP, str(MODULE), str(tmp_path), point],
                            capture_output=True, timeout=30)
    assert result.returncode == 77, result.stderr.decode()
    with journal(tmp_path) as j:
        recovered = j.recover(IDENTITY)
        if point in {"started", "staged"}:
            assert recovered is None
            assert prepared(j)[0] == 2
            assert tree(j.recover(IDENTITY)[0].parent) == FILES
        else:
            assert recovered[2] == ("reused" if point == "committed" else "recovered")
            assert tree(recovered[0].parent) == {"message.md": b"markdown", "source.eml": b"source", "attachments/data": b"attachment"}
        assert len(list(tmp_path.glob("00000001-*"))) == 1
