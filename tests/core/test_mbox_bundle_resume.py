"""Bundle recovery through the real MIME converter, timed workers and CLI."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

from dead_letter.core.mbox_import import convert_mbox

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"


def archive(tmp_path, *, empty=False):
    path = tmp_path / "source.mbox"
    with path.open("wb") as stream:
        for index in range(1, 4):
            stream.write(POSTMARK)
            if empty and index == 2:
                continue
            message = EmailMessage()
            message["From"] = "sender@example.test"
            message["Subject"] = f"Synthetic bundle {index}"
            message["X-Gmail-Labels"] = "Inbox,Receipts"
            message["Message-ID"] = f"<bundle-{index}@example.test>"
            message.set_content(f"Body {index}\n>From quoted text\n")
            message.add_attachment(bytes(range(256)), maintype="application", subtype="octet-stream", filename="data.bin")
            message.add_attachment(b"second", maintype="application", subtype="octet-stream", filename="data.bin")
            message.add_attachment(b"", maintype="application", subtype="octet-stream", filename="empty.bin")
            message.set_boundary(f"synthetic-bundle-{index}")
            stream.write(message.as_bytes() + b"\n")
    return path


def tree(bundle):
    return {p.relative_to(bundle).as_posix(): p.read_bytes() for p in bundle.rglob("*") if p.is_file()}


def run(source, root, timeout=None, **kwargs):
    return list(convert_mbox(source, output=root, bundles=True, resume=True, timeout_seconds=timeout, **kwargs))


@pytest.mark.parametrize("timeout", [None, 30])
def test_bundle_bytes_provenance_diagnostics_and_reuse(tmp_path, monkeypatch, timeout):
    from dead_letter.core import mbox_import, mbox_isolation
    source = archive(tmp_path)
    before = source.read_bytes()
    root = tmp_path / "out"
    baseline = list(convert_mbox(source, output=tmp_path / "baseline", bundles=True, timeout_seconds=timeout))
    first = run(source, root, timeout)
    assert len(first) == 3 and all(row.success for row in first), first
    assert [tree(row.output.parent) for row in first] == [tree(row.output.parent) for row in baseline]
    assert [row.mbox for row in first] == [row.mbox for row in baseline]
    assert [row.diagnostics for row in first] == [row.diagnostics for row in baseline]
    assert (first[0].output.parent / "attachments/empty.bin").read_bytes() == b""
    assert len(list((first[0].output.parent / "attachments").iterdir())) == 3
    def forbidden(*args, **kwargs):
        pytest.fail("reused bundles must not invoke conversion")
    monkeypatch.setattr(mbox_import, "_convert_record", forbidden)
    monkeypatch.setattr(mbox_isolation, "convert_record_isolated", forbidden)
    second = run(source, root, timeout)
    assert all(row.success for row in second), second
    assert [row.recovery for row in second] == [{"status": "reused", "attempt": 1}] * 3
    assert [row.output for row in second] == [row.output for row in first]
    assert source.read_bytes() == before
    assert len(list(root.glob("0000000*"))) == 3


@pytest.mark.parametrize("timeout", [None, 30])
def test_failed_record_is_retried_without_duplicate_bundles(tmp_path, timeout):
    source = archive(tmp_path, empty=True)
    root = tmp_path / "out"
    first = run(source, root, timeout)
    assert [row.success for row in first] == [True, False, True]
    second = run(source, root, timeout)
    assert [row.recovery["status"] for row in second] == ["reused", "retried", "reused"]
    assert second[1].error["code"] == "mbox_empty_message"
    assert second[1].recovery["attempt"] == 2
    assert len(list(root.glob("0000000*"))) == 2


@pytest.mark.parametrize("member", ["message.md", "source.eml", "attachments/data.bin"])
def test_user_changes_stop_import_and_are_not_repaired(tmp_path, member):
    source = archive(tmp_path)
    root = tmp_path / "out"
    first = run(source, root)
    changed = first[1].output.parent / member
    changed.write_bytes(b"user changes")
    before = [tree(row.output.parent) for row in first]
    rows = run(source, root)
    assert len(rows) == 2 and rows[-1].mbox is None
    assert rows[-1].error["code"] == "mbox_resume_conflict"
    assert [tree(row.output.parent) for row in first] == before


def test_whole_deleted_bundle_retries_without_changing_name(tmp_path):
    source = archive(tmp_path)
    root = tmp_path / "out"
    first = run(source, root)
    bundle = first[1].output.parent
    before = tree(bundle)
    shutil.rmtree(bundle)
    second = run(source, root)
    assert [row.recovery["status"] for row in second] == ["reused", "retried", "reused"]
    assert second[1].output == first[1].output
    assert tree(bundle) == before


def limit_archive(tmp_path, kind):
    path = tmp_path / "limits.mbox"
    with path.open("wb") as stream:
        for index in range(1, 4):
            message = EmailMessage()
            message["From"] = "sender@example.test"
            message["Subject"] = f"Synthetic limit {index}"
            message["Message-ID"] = f"<limit-{index}@example.test>"
            message.set_content(f"Body {index}\n")
            # Only message 2 exceeds the (test-lowered) bundle limits.
            for part in range(80 if index == 2 else 1):
                name = f"{part:03d}-" + "\u6587" * 60 + ".txt" if kind == "receipt" else f"{part:03d}.txt"
                message.add_attachment(b"x", maintype="text", subtype="plain", filename=name)
            message.set_boundary(f"synthetic-limit-{index}")
            stream.write(POSTMARK + message.as_bytes() + b"\n")
    return path


def footprint(root):
    paths = sorted(root.rglob("*"))
    return len(paths), sum(p.stat().st_size for p in paths if p.is_file() and p.suffix != ".json")


@pytest.mark.parametrize("timeout", [None, 30])
@pytest.mark.parametrize("kind", ["members", "receipt"])
def test_over_limit_message_fails_only_its_record_on_every_run(tmp_path, monkeypatch, kind, timeout):
    from dead_letter.core import mbox_resume
    if kind == "members":
        monkeypatch.setattr(mbox_resume, "MAX_BUNDLE_FILES", 40)
    else:
        monkeypatch.setattr(mbox_resume, "MAX_RECEIPT_BYTES", 16 * 1024)
    source = limit_archive(tmp_path, kind)
    root = tmp_path / "out"
    first = run(source, root, timeout)
    assert [row.success for row in first] == [True, False, True], first
    assert first[1].mbox["index"] == 2 and first[1].output is None
    assert first[1].error["code"] == "mbox_resume_receipt_limit"
    assert not list(root.glob("00000002-*"))
    sizes = []
    for attempt in (2, 3):
        rows = run(source, root, timeout)
        assert [row.success for row in rows] == [True, False, True], rows
        assert [row.recovery["status"] for row in rows] == ["reused", "retried", "reused"]
        assert rows[1].recovery["attempt"] == attempt
        assert rows[1].error["code"] == "mbox_resume_receipt_limit"
        assert rows[1].mbox == first[1].mbox
        assert [row.output for row in rows] == [row.output for row in first]
        sizes.append(footprint(root))
    assert sizes[0] == sizes[1]  # Reruns of the same record do not accumulate copies.
    assert len(list((root / ".dead-letter-resume").glob(".abandoned-*"))) == 1
    assert not list(root.glob("00000002-*"))


BOOTSTRAP = r'''
import os, sys, shutil
from pathlib import Path
from dead_letter.core import mbox_resume as m
from dead_letter.core.mbox_import import convert_mbox
source, root, point, mode = sys.argv[1:]
if point == 'started':
    real = m.ResumeJournal.start
    def start(self, identity):
        real(self, identity); os._exit(77)
    m.ResumeJournal.start = start
elif point in ('staged', 'prepared'):
    real = m.ResumeJournal.prepare
    def prepare(self, identity, diagnostics):
        if point == 'staged': os._exit(77)
        real(self, identity, diagnostics); os._exit(77)
    m.ResumeJournal.prepare = prepare
elif point == 'published':
    real = m.ResumeJournal._publish_bundle
    def publish(self, pending, target):
        real(self, pending, target); os._exit(77)
    m.ResumeJournal._publish_bundle = publish
elif point == 'committed':
    real = m.ResumeJournal._cleanup
    def cleanup(self, identity):
        if self.load(identity)['phase'] == 'complete': os._exit(77)
        real(self, identity)
    m.ResumeJournal._cleanup = cleanup
elif point == 'partial':
    if mode == 'direct':
        real = Path.write_bytes
        def write(path, data):
            result = real(path, data)
            if '.dead-letter-resume' in path.parts and 'attachments' in path.parts: os._exit(77)
            return result
        Path.write_bytes = write
    else:
        real = shutil.copyfile
        def copy(source, target, *args, **kwargs):
            result = real(source, target, *args, **kwargs)
            if '.dead-letter-resume' in Path(target).parts and 'attachments' in Path(target).parts: os._exit(77)
            return result
        shutil.copyfile = copy
for result in convert_mbox(source, output=root, bundles=True, resume=True,
                          timeout_seconds=30 if mode == 'worker' else None):
    assert result.success, result.error
raise RuntimeError('injection not reached')
'''


@pytest.mark.parametrize("mode", ["direct", "worker"])
@pytest.mark.parametrize("point", ["started", "partial", "staged", "prepared", "published", "committed"])
def test_real_converter_crash_recovery(tmp_path, mode, point):
    source = archive(tmp_path)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    root = tmp_path / "out"
    child = subprocess.run([sys.executable, "-c", BOOTSTRAP, str(source), str(root), point, mode],
                           capture_output=True, timeout=60)
    assert child.returncode == 77, child.stderr.decode()
    assert len(list(root.glob("0000000*"))) == (1 if point in {"published", "committed"} else 0)
    rows = run(source, root, 30 if mode == "worker" else None)
    assert len(rows) == 3 and all(row.success for row in rows), rows
    status = "retried" if point in {"started", "partial", "staged"} else "reused" if point == "committed" else "recovered"
    assert rows[0].recovery["status"] == status
    assert rows[0].recovery["attempt"] == (2 if status == "retried" else 1)
    baseline = list(convert_mbox(source, output=tmp_path / "baseline", bundles=True))
    assert [tree(row.output.parent) for row in rows] == [tree(row.output.parent) for row in baseline]
    assert [row.mbox for row in rows] == [row.mbox for row in baseline]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    assert len(list(root.glob("0000000*"))) == 3


@pytest.mark.parametrize("mode", ["direct", "worker"])
def test_cli_rebuilds_report_after_bundle_commit_crash(tmp_path, mode):
    from dead_letter.backend.cli import main
    source = archive(tmp_path)
    root = tmp_path / "out"
    args = [str(source), "--output", str(root), "--mbox-resume", "--mbox-bundles", "--report"]
    if mode == "worker":
        args += ["--mbox-timeout", "30"]
    script = '''
import os, sys
from dead_letter.backend import mbox_cli
from dead_letter.backend.cli import main
real = mbox_cli.StreamingReport
class Crash(real):
    def append(self, entry):
        if self.total == 1: os._exit(77)
        return super().append(entry)
mbox_cli.StreamingReport = Crash
raise SystemExit(main(sys.argv[1:]))
'''
    child = subprocess.run([sys.executable, "-c", script, *args], capture_output=True, timeout=60)
    assert child.returncode == 77, child.stderr.decode()
    assert len(list(root.glob("0000000*"))) == 2
    assert main(args) == 0
    report_path = root / ".dead-letter-report.json"
    report = json.loads(report_path.read_text())
    assert [r["mbox"]["index"] for r in report["results"]] == [1, 2, 3]
    assert [r["recovery"]["status"] for r in report["results"]] == ["reused", "reused", "new"]
    assert all((root / r["output"]).is_file() for r in report["results"])
    assert report["mbox_options"]["bundles"] is True
    assert report["mbox_options"]["resume"] is True
    report_path.write_text("user notes")
    assert main(args) == 0
    assert report_path.read_text() == "user notes"
    second = json.loads((root / ".dead-letter-report-2.json").read_text())
    assert second["mbox_options"]["recovery_counts"]["reused"] == 3
    assert len(list(root.glob("0000000*"))) == 3
