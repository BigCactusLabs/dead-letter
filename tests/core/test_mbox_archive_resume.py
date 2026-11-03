"""Resume with cooperative cancellation and compressed input (#272), synthetic only."""
from __future__ import annotations

import io
import os
import tarfile
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from dead_letter.core import convert_mbox_archive
from dead_letter.core.mbox import MboxCancelled
from dead_letter.core.mbox_import import convert_mbox

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MAIL = b"".join(
    POSTMARK + f"From: a@example.test\nSubject: {n}\n\nBody {n}\n\n".encode() for n in range(3)
)


def _archive(path: Path, entries) -> Path:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for name, data in entries:
                out.writestr(name, data)
    else:
        with tarfile.open(path, "w:gz") as out:
            for name, data in entries:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                out.addfile(info, io.BytesIO(data))
    return path


def _staging(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    return staging


@pytest.mark.parametrize("timeout", [None, 30.0])
def test_cancelled_resume_retries_only_unfinished_records(tmp_path, timeout):
    source = tmp_path / "mail.mbox"
    source.write_bytes(MAIL)
    root = tmp_path / "out"
    stop = {"now": False}
    with closing(convert_mbox(
        source, output=root, bundles=True, resume=True, timeout_seconds=timeout,
        cancel_requested=lambda: stop["now"],
    )) as results:
        first = next(results)
        stop["now"] = True
        with pytest.raises(MboxCancelled):
            next(results)
    assert first.success and first.recovery == {"status": "new", "attempt": 1}
    kept = first.output.stat()
    rerun = list(convert_mbox(source, output=root, bundles=True, resume=True, timeout_seconds=timeout))
    assert [row.recovery["status"] for row in rerun] == ["reused", "new", "new"]
    assert all(row.success for row in rerun)
    assert rerun[0].output.stat().st_ino == kept.st_ino
    assert len(list(root.glob("*/message.md"))) == 3


def test_cancel_before_fingerprinting_leaves_no_output(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(MAIL)
    root = tmp_path / "out"
    with pytest.raises(MboxCancelled):
        next(convert_mbox(source, output=root, resume=True, cancel_requested=lambda: True))
    assert not root.exists()


@pytest.mark.parametrize("suffix", [".zip", ".tgz"])
def test_compressed_resume_restages_and_keeps_archive_provenance(tmp_path, suffix):
    container = _archive(tmp_path / ("takeout" + suffix), [
        ("Takeout/a.mbox", MAIL), ("Takeout/b.mbox", POSTMARK + b"Subject: b\n\nb\n"),
    ])
    root = tmp_path / "out"
    options = dict(member="Takeout/a.mbox", staging_dir=_staging(tmp_path), output=root, bundles=True, resume=True)
    with closing(convert_mbox_archive(container, **options)) as results:
        first = next(results)
    assert first.success and first.recovery["status"] == "new"
    # Each run stages to a new private path; the journal is bound to the archive.
    assert not list((tmp_path / "staging").iterdir())
    rerun = list(convert_mbox_archive(container, **options))
    assert [row.recovery["status"] for row in rerun] == ["reused", "new", "new"]
    assert not list((tmp_path / "staging").iterdir())
    for row in rerun:
        assert row.source.startswith("a.mbox#message-")
        assert row.mbox["archive"] == container.name
        assert row.mbox["container"]["member_name"] == "Takeout/a.mbox"
    front = yaml.safe_load(rerun[2].output.read_text().split("---", 2)[1])
    assert front["source_mbox"]["archive"] == container.name
    assert front["source_mbox"]["container"]["member_name"] == "Takeout/a.mbox"
    # Non-resumed output of the same member is byte-identical.
    plain = tmp_path / "plain"
    assert all(row.success for row in convert_mbox_archive(
        container, member="Takeout/a.mbox", staging_dir=_staging(tmp_path), output=plain, bundles=True,
    ))
    for message in plain.glob("*/message.md"):
        assert (root / message.relative_to(plain)).read_bytes() == message.read_bytes()


def test_compressed_resume_rejects_other_member_or_changed_archive(tmp_path):
    container = _archive(tmp_path / "takeout.zip", [("a.mbox", MAIL), ("b.mbox", MAIL)])
    root = tmp_path / "out"
    common = dict(staging_dir=_staging(tmp_path), output=root, bundles=True, resume=True)
    assert all(row.success for row in convert_mbox_archive(container, member="a.mbox", **common))
    [other] = list(convert_mbox_archive(container, member="b.mbox", **common))
    assert other.mbox is None and other.error["code"] == "mbox_resume_mismatch"
    moved = container.rename(tmp_path / "renamed.zip")
    [renamed] = list(convert_mbox_archive(moved, member="a.mbox", **common))
    assert renamed.error["code"] == "mbox_resume_mismatch"
    assert len(list(root.glob("*/message.md"))) == 3


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_compressed_resume_staging_stays_private(tmp_path):
    container = _archive(tmp_path / "takeout.zip", [("a.mbox", MAIL)])
    seen = []

    def check(path, written):
        seen.append(path.parent.stat().st_mode & 0o077)

    list(convert_mbox_archive(
        container, staging_dir=_staging(tmp_path), output=tmp_path / "out", bundles=True,
        resume=True, staging_check=check,
    ))
    assert seen and set(seen) == {0}
