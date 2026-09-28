from __future__ import annotations

import io
import json
import tarfile
import zipfile

import pytest

from dead_letter.backend import cli
from dead_letter.core import mbox_archive

MAIL = b"From sender@example.test Thu Jun 11 00:38:38 2020\nSubject: hello\n\n>From quoted\n"


def make_archive(path, entries):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path, "w") as out:
            for name, data in entries:
                out.writestr(name, data)
    else:
        with tarfile.open(path, "w:gz") as out:
            for name, data in entries:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                out.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize("extension", ["zip", "tgz", "tar.gz"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_end_to_end_and_report(tmp_path, monkeypatch, extension, dry_run):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / f"mail.{extension}"
    make_archive(source, [("one.mbox", MAIL), ("Takeout/two.MBOX", MAIL), ("nested.zip", b"ignored")])
    before = source.read_bytes()
    stage = tmp_path / "stage"
    stage.mkdir()
    args = ["convert", "./" + source.name, "--output", "out", "--report", "--mbox-member", "Takeout/two.MBOX", "--mbox-staging-dir", str(stage), "--mbox-bundles", "--mbox-unescape", "mboxrd", "--mbox-timeout", "10"]
    if dry_run:
        args.append("--dry-run")
    assert cli.main(args) == 0
    report = json.loads((tmp_path / "out/.dead-letter-report.json").read_text())
    assert report["job"]["input_mode"] == "mbox"
    assert report["job"]["status"] == "succeeded"
    assert report["summary"]["total"] == 1
    assert report["summary"]["written"] == (0 if dry_run else 1)
    assert report["archive"]["container_path"] == "./" + source.name
    assert report["archive"]["ignored_member_count"] == 2
    assert report["archive"]["member_name"] == "Takeout/two.MBOX"
    assert report["archive"]["staged_bytes"] == len(MAIL)
    assert report["archive"]["format"] == ("zip" if extension == "zip" else "tgz")
    assert report["results"][0]["mbox"]["archive"] == report["archive"]
    assert report["mbox_options"]["timeout_seconds"] == 10
    if not dry_run:
        output = tmp_path / "out" / report["results"][0]["output"]
        assert b"\nFrom quoted\n" in output.with_name("source.eml").read_bytes()
    assert source.read_bytes() == before
    assert not list(stage.iterdir())


@pytest.mark.parametrize("extension", ["zip", "tgz"])
@pytest.mark.parametrize("entries,member,code", [
    ([], None, "no_mbox"),
    ([("a.mbox", MAIL), ("b.mbox", MAIL)], None, "multiple_mbox"),
    ([("a.mbox", MAIL)], "missing.mbox", "no_mbox"),
])
def test_cli_selection_fatal(tmp_path, extension, entries, member, code):
    source = tmp_path / f"mail.{extension}"
    make_archive(source, entries)
    args = [str(source), "--report", "--output", str(tmp_path / "out")]
    if member:
        args.extend(["--mbox-member", member])
    assert cli.main(args) == 1
    report = json.loads((tmp_path / "out/.dead-letter-report.json").read_text())
    assert report["job"]["status"] == "failed"
    assert report["results"][0]["error"]["code"] == "mbox_archive_" + code
    assert report["summary"]["written"] == 0


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_empty_mbox_still_reports_archive_summary(tmp_path, extension):
    source = tmp_path / f"mail.{extension}"
    make_archive(source, [("empty.mbox", b"")])
    assert cli.main([str(source), "--report"]) == 0
    report = json.loads((source.with_suffix(".markdown") / ".dead-letter-report.json").read_text())
    assert report["summary"]["total"] == 0
    assert report["archive"]["member_name"] == "empty.mbox"
    assert report["archive"]["staged_bytes"] == 0


@pytest.mark.parametrize("extension", ["zip", "tgz"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_never_deletes_archive(tmp_path, extension, dry_run):
    source = tmp_path / f"mail.{extension}"
    make_archive(source, [("mail.mbox", MAIL)])
    before = source.read_bytes()
    args = [str(source), "--delete-eml"] + (["--dry-run"] if dry_run else [])
    assert cli.main(args) == 1
    assert source.read_bytes() == before


@pytest.mark.parametrize("suffix", [".eml", ".mbox"])
@pytest.mark.parametrize("flag,value", [("--mbox-member", "mail.mbox"), ("--mbox-staging-dir", "stage")])
def test_archive_flags_require_archive(tmp_path, suffix, flag, value):
    path = tmp_path / ("mail" + suffix)
    path.write_bytes(MAIL)
    assert cli.main([str(path), flag, value]) == 1


def test_interrupted_staging_report(tmp_path, monkeypatch):
    source = tmp_path / "mail.zip"
    make_archive(source, [("mail.mbox", MAIL)])
    stage = tmp_path / "stage"
    stage.mkdir()

    def interrupt(stream, target, limits):
        target.write_bytes(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(mbox_archive, "_copy_member", interrupt)
    assert cli.main([str(source), "--report", "--mbox-staging-dir", str(stage)]) == 130
    report = json.loads((source.with_suffix(".markdown") / ".dead-letter-report.json").read_text())
    assert report["job"]["status"] == "interrupted"
    assert report["summary"]["total"] == 0
    assert not list(stage.iterdir())


@pytest.mark.parametrize("extension", ["gz", "tar", "bz2", "xz", "7z", "rar"])
def test_unsupported_container_cli_error_code(tmp_path, extension, capsys):
    source = tmp_path / f"mail.{extension}"
    source.write_bytes(b"unsupported")
    assert cli.main([str(source)]) == 1
    assert "mbox_archive_unsupported" in capsys.readouterr().err
