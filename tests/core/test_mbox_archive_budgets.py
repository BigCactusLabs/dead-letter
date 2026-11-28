"""Compressed Takeout staging (#144) combined with worker resource budgets (#140)."""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from dead_letter.core import convert_mbox_archive
from dead_letter.core import mbox_isolation as isolation
from dead_letter.core.mbox_isolation import MboxBudgetError

FIXTURE = Path(__file__).parent / "fixtures" / "takeout-synthetic.mbox"
POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Same\n\nHello\n\n"
# Rendered Markdown over 1 MiB exceeds a 1 MiB per-file output budget.
LARGE = b"From: bob@example.test\nSubject: Large\n\n" + (b"x" * 99 + b"\n") * 21000 + b"\n"
CONTAINER_KEYS = {"container_basename", "format", "member_name", "member_compressed_bytes",
                  "member_uncompressed_bytes", "crc32", "member_sha256", "staged_bytes"}

posix_budgets = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="Per-file output budgets require Linux or macOS",
)
worker_budgets = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin", "win32"), reason="Unsupported worker-budget platform",
)


def make_zip(path: Path, data: bytes, member: str = "Takeout/Mail/All mail.mbox") -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as out:
        out.writestr(member, data)
        out.writestr("Takeout/archive_browser.html", b"<html></html>")
    return path


def spy_worker_command(monkeypatch):
    calls = []
    real = isolation._worker_command

    def command(request, *budget_args):
        calls.append(budget_args)
        return real(request, *budget_args)

    monkeypatch.setattr(isolation, "_worker_command", command)
    return calls


def front_matter(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


@worker_budgets
@pytest.mark.parametrize("bundles", [False, True])
def test_zip_staged_mbox_converts_in_budgeted_workers_with_container_provenance(tmp_path, monkeypatch, bundles):
    calls = spy_worker_command(monkeypatch)
    source = make_zip(tmp_path / "takeout.zip", FIXTURE.read_bytes())
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    stage = tmp_path / "stage"
    stage.mkdir()
    extra = {"memory_limit_mib": 4096} if sys.platform == "win32" else {"max_output_mib": 64}
    rows = list(convert_mbox_archive(
        source, output=tmp_path / "out", staging_dir=stage, bundles=bundles,
        timeout_seconds=30, cpu_seconds=30, **extra,
    ))
    assert len(rows) == 3 and all(r.success for r in rows)
    # Every record ran in a budgeted worker that received both limits and a nonce.
    assert len(calls) == 3
    expected = (("memory_mib=4096", "cpu_seconds=30") if sys.platform == "win32"
                else ("cpu_seconds=30", "max_output_mib=64"))
    for args in calls:
        assert args[:2] == expected
        assert args[2].startswith("nonce=") and len(args) == 3
    for row in rows:
        assert row.mbox["archive"] == source.name
        assert set(row.mbox["container"]) == CONTAINER_KEYS
        assert row.mbox["container"]["member_name"] == "Takeout/Mail/All mail.mbox"
        # The worker, not the parent, rendered this Markdown.
        written = front_matter(row.output)["source_mbox"]
        assert written["archive"] == source.name
        assert set(written["container"]) == CONTAINER_KEYS
        assert written["container"] == row.mbox["container"]
        assert str(stage) not in row.output.read_text(encoding="utf-8")
    assert list(stage.iterdir()) == []
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@posix_budgets
def test_exceeded_output_budget_on_archive_withholds_record_and_continues(tmp_path):
    source = make_zip(tmp_path / "takeout.zip", b"".join(POSTMARK + m for m in (MESSAGE, LARGE, MESSAGE)))
    root = tmp_path / "out"
    rows = list(convert_mbox_archive(source, output=root, staging_dir=tmp_path,
                                     timeout_seconds=30, max_output_mib=1))
    assert [r.success for r in rows] == [True, False, True]
    assert [r.mbox["index"] for r in rows] == [1, 2, 3]
    assert rows[1].error == {"code": "mbox_message_resource_limit", "stage": "worker",
                             "message": isolation._RESOURCE_MESSAGES["output"]}
    assert rows[1].output is None
    # The withheld record still carries its container provenance.
    assert rows[1].mbox["archive"] == source.name
    assert set(rows[1].mbox["container"]) == CONTAINER_KEYS
    published = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert not [name for name in published if name.startswith("00000002-")]
    assert len([name for name in published if name.endswith(".md")]) == 2


def test_unsupported_archive_budget_fails_before_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("no archive bytes may be read or staged")

    from dead_letter.core import mbox_archive
    monkeypatch.setattr(mbox_archive, "_staging_file", unexpected)
    monkeypatch.setattr(isolation.subprocess, "Popen", unexpected)
    source = make_zip(tmp_path / "takeout.zip", POSTMARK + MESSAGE)
    root = tmp_path / "out"
    with pytest.raises(MboxBudgetError) as caught:
        list(convert_mbox_archive(source, output=root, timeout_seconds=30, cpu_seconds=5, max_output_mib=8))
    assert caught.value.code == "mbox_budget_unsupported"
    assert "--mbox-max-output-mib" in caught.value.message
    assert not root.exists()


def test_archive_budget_without_worker_mode_is_rejected_before_staging(tmp_path, monkeypatch):
    from dead_letter.core import mbox_archive

    def unexpected(*_args, **_kwargs):
        raise AssertionError("no archive bytes may be read or staged")

    monkeypatch.setattr(mbox_archive, "_staging_file", unexpected)
    source = make_zip(tmp_path / "takeout.zip", POSTMARK + MESSAGE)
    with pytest.raises(ValueError, match="require worker mode"):
        list(convert_mbox_archive(source, output=tmp_path / "out", max_output_mib=8))
