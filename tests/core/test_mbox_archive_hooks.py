"""Optional web guards preserve default archive bytes and clean up on stop."""

import threading

import pytest

from dead_letter.core import convert_mbox_archive
from dead_letter.core.mbox import MboxCancelled
from dead_letter.core.types import ConvertOptions

from .test_mbox_archive import MAIL, make_archive


@pytest.mark.parametrize("suffix", ["zip", "tgz"])
def test_archive_default_and_none_hooks_have_identical_output(tmp_path, suffix):
    source = make_archive(tmp_path / f"mail.{suffix}")
    original = source.read_bytes()
    a, b = tmp_path / "default", tmp_path / "explicit-none"
    assert all(row.success for row in convert_mbox_archive(source, output=a, bundles=True))
    assert all(row.success for row in convert_mbox_archive(source, output=b, bundles=True, cancel_requested=None, staging_check=None))
    assert {p.relative_to(a): p.read_bytes() for p in a.rglob("*") if p.is_file()} == {
        p.relative_to(b): p.read_bytes() for p in b.rglob("*") if p.is_file()
    }
    assert source.read_bytes() == original


@pytest.mark.parametrize("suffix", ["zip", "tgz"])
def test_archive_staging_check_is_bounded_and_exception_cleans_up(tmp_path, suffix):
    source = make_archive(tmp_path / f"mail.{suffix}", [("mail.mbox", MAIL + b"x" * (256 * 1024))])
    staging = tmp_path / "stage"
    staging.mkdir()
    seen = []
    class Stop(RuntimeError):
        pass
    def check(path, written):
        assert path.name == "member.mbox"
        assert path.is_relative_to(staging)
        seen.append(written)
        if written >= 128 * 1024:
            raise Stop("reserve")
    with pytest.raises(Stop, match="reserve"):
        list(convert_mbox_archive(source, staging_dir=staging, staging_check=check))
    assert seen[0] == 0
    assert max(b - a for a, b in zip(seen, seen[1:])) <= 64 * 1024 * 1024
    assert not list(staging.iterdir())
    assert not source.with_suffix(".markdown").exists()


@pytest.mark.parametrize("suffix", ["zip", "tgz"])
@pytest.mark.parametrize("phase", ["before_staging", "staging", "conversion"])
def test_archive_cancellation_is_forwarded_and_cleans_staging(tmp_path, suffix, phase):
    source = make_archive(tmp_path / f"mail.{suffix}", [("mail.mbox", MAIL * 2)])
    original = source.read_bytes()
    staging = tmp_path / "stage"
    staging.mkdir()
    cancelled = threading.Event()
    if phase == "before_staging":
        cancelled.set()
    def check(path, written):
        if phase == "staging":
            cancelled.set()
    results = convert_mbox_archive(
        source, staging_dir=staging, output=tmp_path / "output", bundles=True,
        timeout_seconds=10, cancel_requested=cancelled.is_set, staging_check=check,
    )
    if phase == "conversion":
        assert next(results).success
        cancelled.set()
    with pytest.raises(MboxCancelled):
        next(results)
    assert not list(staging.iterdir())
    assert source.read_bytes() == original
    assert len(list((tmp_path / "output").glob("*/message.md"))) == (1 if phase == "conversion" else 0)


def test_archive_hooks_validate_before_staging(tmp_path):
    for option in ("cancel_requested", "staging_check"):
        with pytest.raises(ValueError, match="must be callable"):
            list(convert_mbox_archive(tmp_path / "missing.zip", **{option: True}, options=ConvertOptions(dry_run=True)))
