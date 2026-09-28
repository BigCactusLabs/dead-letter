"""Synthetic container contracts: no extraction, no conversion before validation."""
from __future__ import annotations

import hashlib
import io
import stat
import struct
import tarfile
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from dead_letter.core import ArchiveLimits, convert_mbox_archive
from dead_letter.core import mbox_archive as archive
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.types import ConvertOptions

FIXTURE = Path(__file__).parent / "fixtures" / "takeout-synthetic.mbox"
MAIL = b"From sender@example.test Thu Jun 11 00:38:38 2020\nSubject: hello\n\nBody\n"


def make_archive(path, entries=None):
    entries = [("Takeout/Mail/fixture.mbox", MAIL)] if entries is None else entries
    if path.suffix.lower() == ".zip":
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


def fatal(tmp_path, source, code, **kwargs):
    stage = tmp_path / "stage"
    stage.mkdir(exist_ok=True)
    root = tmp_path / "out"
    before = source.read_bytes()
    [row] = convert_mbox_archive(source, staging_dir=stage, output=root, **kwargs)
    assert not row.success and row.mbox is None
    assert row.error["code"] == "mbox_archive_" + code
    assert list(stage.iterdir()) == []
    assert not root.exists()
    assert source.read_bytes() == before
    return row


@pytest.mark.parametrize("extension", ["zip", "tgz", "tar.gz"])
@pytest.mark.parametrize("bundles", [False, True])
@pytest.mark.parametrize("timeout", [None, 10])
def test_parity_provenance_and_source_preservation(tmp_path, extension, bundles, timeout):
    source = make_archive(tmp_path / f"mail.{extension}", [("Takeout/fixture.mbox", FIXTURE.read_bytes()), ("nested.zip", b"ignored")])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    extracted = tmp_path / "fixture.mbox"
    extracted.write_bytes(FIXTURE.read_bytes())
    expected = list(convert_mbox(extracted, output=tmp_path / "plain", bundles=bundles))
    stage = tmp_path / "stage"
    stage.mkdir()
    actual = list(convert_mbox_archive(source, output=tmp_path / "compressed", staging_dir=stage, bundles=bundles, timeout_seconds=timeout))
    assert len(actual) == len(expected) == 3
    for result, baseline in zip(actual, expected, strict=True):
        assert result.success == baseline.success
        assert result.output.name == baseline.output.name
        original_md = baseline.output.read_text()
        actual_md = result.output.read_text()
        # The new archive provenance is the sole Markdown difference.
        original_front = yaml.safe_load(original_md.split("---", 2)[1])
        actual_front = yaml.safe_load(actual_md.split("---", 2)[1])
        assert actual_front["source_mbox"].pop("archive") == source.name
        provenance = actual_front["source_mbox"].pop("container")
        original_front["source_mbox"].pop("archive")
        assert original_front == actual_front
        assert actual_md.split("---", 2)[2].encode() == original_md.split("---", 2)[2].encode()
        assert result.mbox["archive"] == source.name
        assert provenance == result.mbox["container"]
        assert set(provenance) == {"container_basename", "format", "member_name",
                                   "member_compressed_bytes", "member_uncompressed_bytes",
                                   "crc32", "member_sha256", "staged_bytes"}
        assert provenance["container_basename"] == source.name
        assert str(tmp_path) not in actual_md
        assert provenance["member_name"] == "Takeout/fixture.mbox"
        assert provenance["member_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        assert "container_sha256" not in provenance
        assert provenance["staged_bytes"] == len(FIXTURE.read_bytes())
        assert str(stage) not in actual_md
        assert result.mbox["sha256"] == baseline.mbox["sha256"]
        if bundles:
            expected_files = {p.relative_to(baseline.output.parent): p.read_bytes() for p in baseline.output.parent.rglob("*") if p.is_file() and p.name != "message.md"}
            actual_files = {p.relative_to(result.output.parent): p.read_bytes() for p in result.output.parent.rglob("*") if p.is_file() and p.name != "message.md"}
            assert actual_files == expected_files
    assert list(stage.iterdir()) == []
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_selection(tmp_path, extension):
    source = make_archive(tmp_path / f"mail.{extension}", [("nested.zip", b"not opened"), ("notes.json", b"{}")])
    fatal(tmp_path, source, "no_mbox")
    make_archive(source, [("a.mbox", MAIL), ("b.MBOX", MAIL)])
    row = fatal(tmp_path, source, "multiple_mbox")
    assert "a.mbox" in row.error["message"] and "b.MBOX" in row.error["message"]
    fatal(tmp_path, source, "no_mbox", member="B.MBOX")
    [row] = convert_mbox_archive(source, member="b.MBOX", options=ConvertOptions(dry_run=True))
    assert row.success and row.mbox["container"]["member_name"] == "b.MBOX"
    assert "ignored_member_count" not in row.mbox["container"]


@pytest.mark.parametrize("extension", ["zip", "tgz"])
@pytest.mark.parametrize("name", ["../evil.mbox", "/evil.mbox", "a/../evil.mbox", "C:\\evil.mbox", "C:evil.mbox", "\\\\server\\evil.mbox", "..\\evil.mbox", "../ignored.json"])
def test_unsafe_names(tmp_path, extension, name):
    source = make_archive(tmp_path / f"mail.{extension}", [(name, MAIL)])
    fatal(tmp_path, source, "unsupported")


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_duplicate_candidates_even_with_selection(tmp_path, extension):
    if extension == "zip":
        with pytest.warns(UserWarning, match="Duplicate"):
            source = make_archive(tmp_path / "mail.zip", [("a.mbox", MAIL)] * 2)
    else:
        source = make_archive(tmp_path / "mail.tgz", [("a.mbox", MAIL)] * 2)
    fatal(tmp_path, source, "duplicate_member", member="a.mbox")


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_tar_special_members(tmp_path, kind):
    source = tmp_path / "mail.tgz"
    with tarfile.open(source, "w:gz") as out:
        info = tarfile.TarInfo("link.mbox")
        info.type = kind
        info.linkname = "elsewhere"
        out.addfile(info)
    fatal(tmp_path, source, "unsupported", member="link.mbox")


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFDIR, stat.S_IFCHR])
def test_zip_special_members(tmp_path, mode):
    source = tmp_path / "mail.zip"
    with zipfile.ZipFile(source, "w") as out:
        info = zipfile.ZipInfo("special.mbox")
        info.create_system = 3
        info.external_attr = (mode | 0o600) << 16
        out.writestr(info, "target")
    fatal(tmp_path, source, "unsupported", member="special.mbox")


def patch_zip(path, field, value):
    data = bytearray(path.read_bytes())
    central = data.index(b"PK\x01\x02")
    local = data.index(b"PK\x03\x04")
    offsets = {"flags": (6, 8, "H"), "method": (8, 10, "H"), "crc": (14, 16, "I")}
    a, b, fmt = offsets[field]
    struct.pack_into("<" + fmt, data, local + a, value)
    struct.pack_into("<" + fmt, data, central + b, value)
    path.write_bytes(data)


@pytest.mark.parametrize("field,value,code", [("flags", 1, "unsupported"), ("method", 12, "unsupported"), ("crc", 0, "corrupt")])
def test_zip_header_rejections(tmp_path, field, value, code):
    source = make_archive(tmp_path / "mail.zip")
    patch_zip(source, field, value)
    fatal(tmp_path, source, code)


def test_zip_nul_name_not_truncated_into_safe_name(tmp_path):
    source = make_archive(tmp_path / "mail.zip", [("safe.mboxXevil", MAIL)])
    source.write_bytes(source.read_bytes().replace(b"safe.mboxXevil", b"safe.mbox\0evil"))
    fatal(tmp_path, source, "unsupported")


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_member_limit(tmp_path, extension):
    source = make_archive(tmp_path / f"mail.{extension}", [("a.mbox", MAIL), ("ignored.json", b"{}")])
    fatal(tmp_path, source, "limit_exceeded", archive_limits=ArchiveLimits(max_members=1))


def test_zip_declared_limit_and_space_checked_before_open(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "mail.zip")
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda *a, **k: pytest.fail("must reject before opening member"))
    fatal(tmp_path, source, "limit_exceeded", archive_limits=ArchiveLimits(max_decompressed_bytes=1))
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    fatal(tmp_path, source, "insufficient_space")


def test_zip_counted_limit_with_lying_reader(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "mail.zip")
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda *a, **k: io.BytesIO(MAIL * 100))
    fatal(tmp_path, source, "limit_exceeded", archive_limits=ArchiveLimits(max_decompressed_bytes=len(MAIL) + 1))


@pytest.mark.parametrize("extension", ["zip", "tgz"])
@pytest.mark.parametrize("cut", [1, 8, 30])
def test_truncation_never_converts(tmp_path, extension, cut):
    source = make_archive(tmp_path / f"mail.{extension}")
    source.write_bytes(source.read_bytes()[:-cut])
    fatal(tmp_path, source, "corrupt")


@pytest.mark.parametrize("extension", ["gz", "tar", "bz2", "xz", "7z", "rar"])
def test_unsupported_extensions(tmp_path, extension):
    source = make_archive(tmp_path / "mail.zip")
    renamed = source.rename(tmp_path / f"mail.{extension}")
    fatal(tmp_path, renamed, "unsupported")


@pytest.mark.parametrize("original,new", [("zip", "tgz"), ("tgz", "zip")])
def test_extension_magic_mismatch(tmp_path, original, new):
    source = make_archive(tmp_path / f"mail.{original}").rename(tmp_path / f"mail.{new}")
    fatal(tmp_path, source, "unsupported")


def test_zip_python_floor_tgz_unaffected(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "mail.zip")
    monkeypatch.setattr(archive.sys, "version_info", (3, 12, 2))
    fatal(tmp_path, source, "python_too_old")
    source = make_archive(tmp_path / "mail.tgz")
    assert all(row.success for row in convert_mbox_archive(source, options=ConvertOptions(dry_run=True)))


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_container_changes_before_conversion(tmp_path, monkeypatch, extension):
    source = make_archive(tmp_path / f"mail.{extension}")
    stage = tmp_path / "stage"
    stage.mkdir()
    original = archive._copy_member

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        with source.open("ab") as out:
            out.write(b"changed")
        return result

    monkeypatch.setattr(archive, "_copy_member", mutate)
    [row] = convert_mbox_archive(source, staging_dir=stage, output=tmp_path / "out")
    assert row.error["code"] == "mbox_archive_changed"
    assert not (tmp_path / "out").exists()
    assert not list(stage.iterdir())


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_staging_interrupt_cleanup(tmp_path, monkeypatch, extension):
    source = make_archive(tmp_path / f"mail.{extension}")
    before = source.read_bytes()
    stage = tmp_path / "stage"
    stage.mkdir()

    def interrupt(stream, target, limits):
        target.write_bytes(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(archive, "_copy_member", interrupt)
    with pytest.raises(KeyboardInterrupt):
        list(convert_mbox_archive(source, staging_dir=stage, output=tmp_path / "out"))
    assert not list(stage.iterdir())
    assert not (tmp_path / "out").exists()
    assert source.read_bytes() == before


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_close_early_cleans_staged_member(tmp_path, extension):
    source = make_archive(tmp_path / f"mail.{extension}", [("mail.mbox", MAIL * 2)])
    stage = tmp_path / "stage"
    stage.mkdir()
    with closing(convert_mbox_archive(source, staging_dir=stage, options=ConvertOptions(dry_run=True))) as results:
        assert next(results).success
        assert list(stage.iterdir())
    assert not list(stage.iterdir())


@pytest.mark.parametrize("kwargs", [{"max_members": 0}, {"max_decompressed_bytes": -1}, {"max_members": True}, {"max_decompressed_bytes": 1.5}, {"max_metadata_bytes": 0}, {"max_metadata_bytes": True}])
def test_archive_limits_are_positive_integers(kwargs):
    with pytest.raises(ValueError):
        ArchiveLimits(**kwargs)


def test_zip64_and_stored(tmp_path):
    source = tmp_path / "mail.zip"
    with zipfile.ZipFile(source, "w") as out:
        with out.open("mail.mbox", "w", force_zip64=True) as member:
            member.write(MAIL)
    [row] = convert_mbox_archive(source, options=ConvertOptions(dry_run=True))
    assert row.success


def test_diagnostics_escape_archive_names(tmp_path):
    source = make_archive(tmp_path / "mail.zip", [("one\n\x1b[31m.mbox", MAIL), ("two.mbox", MAIL)])
    row = fatal(tmp_path, source, "multiple_mbox")
    assert "\x1b" not in row.error["message"] and "\n" not in row.error["message"]


def test_gzip_crc_checked_after_tar_end_marker(tmp_path):
    source = make_archive(tmp_path / "mail.tgz")
    data = bytearray(source.read_bytes())
    data[-8] ^= 1
    source.write_bytes(data)
    fatal(tmp_path, source, "corrupt")


def test_tgz_budget_includes_ignored_bytes_and_tar_padding(tmp_path):
    source = make_archive(tmp_path / "mail.tgz", [("mail.mbox", MAIL), ("ignored.json", b"x" * 200_000)])
    fatal(tmp_path, source, "limit_exceeded", archive_limits=ArchiveLimits(max_decompressed_bytes=100_000))


def test_tgz_second_gzip_stream_is_rejected(tmp_path):
    source = make_archive(tmp_path / "mail.tgz")
    source.write_bytes(source.read_bytes() * 2)
    fatal(tmp_path, source, "corrupt")


@pytest.mark.parametrize("metadata", ["large", "nested", "negative", "sparse"])
def test_tar_metadata_is_bounded_before_processing(tmp_path, metadata):
    import gzip

    source = tmp_path / "mail.tgz"
    info = tarfile.TarInfo("metadata")
    if metadata == "large":
        info.type = tarfile.XHDTYPE
        info.size = 64 * 1024 + 1
        raw = info.tobuf()
        code = "limit_exceeded"
    elif metadata == "nested":
        info.type = tarfile.GNUTYPE_LONGNAME
        info.size = 0
        raw = info.tobuf() * 65
        code = "limit_exceeded"
    elif metadata == "negative":
        info.size = -1
        raw = info.tobuf(format=tarfile.GNU_FORMAT)
        code = "corrupt"
    else:
        info.type = tarfile.GNUTYPE_SPARSE
        raw = info.tobuf(format=tarfile.GNU_FORMAT)
        code = "unsupported"
    source.write_bytes(gzip.compress(raw))
    fatal(tmp_path, source, code)


def test_pax_long_name_accepted_and_nul_rejected(tmp_path):
    source = make_archive(tmp_path / "mail.tgz", [("directory/" * 20 + "mail.mbox", MAIL)])
    assert all(row.success for row in convert_mbox_archive(source, options=ConvertOptions(dry_run=True)))
    with tarfile.open(source, "w:gz", format=tarfile.PAX_FORMAT) as out:
        info = tarfile.TarInfo("mail.mbox")
        info.pax_headers = {"path": "mail.mbox\x00suffix"}
        info.size = len(MAIL)
        out.addfile(info, io.BytesIO(MAIL))
    fatal(tmp_path, source, "unsupported")


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_conversion_interrupt_also_cleans_stage(tmp_path, monkeypatch, extension):
    from dead_letter.core import mbox_import

    source = make_archive(tmp_path / f"mail.{extension}")
    stage = tmp_path / "stage"
    stage.mkdir()
    root = tmp_path / "out"

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(mbox_import, "serialize_markdown", interrupt)
    with pytest.raises(KeyboardInterrupt):
        list(convert_mbox_archive(source, staging_dir=stage, output=root))
    assert not list(stage.iterdir())
    assert not list(root.iterdir())


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_mbox_message_limits_still_apply(tmp_path, extension):
    from dead_letter.core.mbox import MboxLimits

    source = make_archive(tmp_path / f"mail.{extension}")
    [row] = convert_mbox_archive(source, limits=MboxLimits(max_message_bytes=1))
    assert row.error["code"] == "mbox_message_too_large"
    assert row.mbox["container"]["member_sha256"] == hashlib.sha256(MAIL).hexdigest()


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_python_delete_and_output_source_refused(tmp_path, extension):
    source = make_archive(tmp_path / f"mail.{extension}")
    before = source.read_bytes()
    with pytest.raises(ValueError, match="always preserved"):
        list(convert_mbox_archive(source, options=ConvertOptions(delete_eml=True)))
    with pytest.raises(ValueError, match="distinct"):
        list(convert_mbox_archive(source, output=source))
    assert source.read_bytes() == before


def test_staging_disk_full_removes_partial_file(tmp_path, monkeypatch):
    import errno

    source = make_archive(tmp_path / "mail.zip")

    def full(stream, target, limits):
        target.write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "synthetic disk full")

    monkeypatch.setattr(archive, "_copy_member", full)
    fatal(tmp_path, source, "insufficient_space")


def test_pax_sparse_metadata_is_rejected_before_payload(tmp_path):
    source = tmp_path / "mail.tgz"
    with tarfile.open(source, "w:gz", format=tarfile.PAX_FORMAT) as out:
        info = tarfile.TarInfo("mail.mbox")
        info.pax_headers = {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"}
        info.size = len(MAIL)
        out.addfile(info, io.BytesIO(MAIL))
    fatal(tmp_path, source, "unsupported")


@pytest.mark.parametrize("format", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
def test_extension_headers_do_not_consume_member_budget(tmp_path, format):
    source = tmp_path / "mail.tgz"
    with tarfile.open(source, "w:gz", format=format) as out:
        for index in range(6):
            info = tarfile.TarInfo("x" * 200 + f"{index}.mbox")
            info.size = len(MAIL)
            out.addfile(info, io.BytesIO(MAIL))
    [row] = convert_mbox_archive(source, member="x" * 200 + "0.mbox",
                                 archive_limits=ArchiveLimits(max_members=6), options=ConvertOptions(dry_run=True))
    assert row.success
    fatal(tmp_path, source, "limit_exceeded", member="x" * 200 + "0.mbox", archive_limits=ArchiveLimits(max_members=5))


def test_400_large_pax_members_do_not_accumulate_tarinfo(tmp_path, monkeypatch):
    # Reviewer's probe, with each path below the new 64 KiB header cap and the
    # total below 16 MiB, so this tests retention independently of rejection.
    source = make_archive(tmp_path / "mail.tgz", [("mail.mbox", MAIL)] +
                          [(f"{index:06d}" + "a" * 32768, b"") for index in range(400)])
    original = tarfile.TarFile.next
    retained = []

    def observed(self):
        result = original(self)
        retained.append(len(self.members))
        return result

    monkeypatch.setattr(tarfile.TarFile, "next", observed)
    [row] = convert_mbox_archive(source, options=ConvertOptions(dry_run=True))
    assert row.success
    assert len(retained) >= 401
    assert max(retained) <= 1


@pytest.mark.parametrize("format", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
def test_total_extension_metadata_budget(tmp_path, format):
    source = tmp_path / "mail.tgz"
    with tarfile.open(source, "w:gz", format=format) as out:
        for index in range(2):
            info = tarfile.TarInfo(f"{index}" + "x" * 40000 + ".mbox")
            info.size = len(MAIL)
            out.addfile(info, io.BytesIO(MAIL))
    fatal(tmp_path, source, "limit_exceeded", archive_limits=ArchiveLimits(max_metadata_bytes=65536))


@pytest.mark.parametrize("damage", ["boundary", "partial", "checksum", "valid_multiple"])
def test_tar_inside_valid_gzip_requires_real_end_marker(tmp_path, damage):
    import gzip

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as out:
        for name in ("Inbox.mbox", "All mail.mbox"):
            info = tarfile.TarInfo(name)
            info.size = len(MAIL)
            out.addfile(info, io.BytesIO(MAIL))
    raw = buf.getvalue()
    boundary = 1024
    if damage == "boundary":
        raw = raw[:boundary]
    elif damage == "partial":
        raw = raw[:boundary + 100]
    elif damage == "checksum":
        raw = raw[:boundary + 148] + b"9999999\x00" + raw[boundary + 156:]
    source = tmp_path / "mail.tgz"
    source.write_bytes(gzip.compress(raw))
    fatal(tmp_path, source, "multiple_mbox" if damage == "valid_multiple" else "corrupt")


def test_tgz_free_space_checked_before_opening_selected_member(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "mail.tgz")
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda *args: pytest.fail("member opened before space check"))
    fatal(tmp_path, source, "insufficient_space")


@pytest.mark.parametrize("kind", ["missing", "file", "unwritable"])
def test_staging_unavailable_before_archive_open(tmp_path, monkeypatch, kind):
    source = make_archive(tmp_path / "mail.zip")
    staging = tmp_path / "stage"
    if kind == "file":
        staging.write_text("not a directory")
    elif kind == "unwritable":
        staging.mkdir()
        def denied(*args, **kwargs):
            raise PermissionError("synthetic read-only staging directory")
        monkeypatch.setattr(archive, "TemporaryDirectory", denied)
    original = Path.open
    def unopened(path, mode="r", *args, **kwargs):
        assert path != source, "archive must not be opened before staging check"
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", unopened)
    [row] = convert_mbox_archive(source, staging_dir=staging)
    assert row.error["code"] == "mbox_archive_staging_unavailable"


@pytest.mark.parametrize("kind", ["missing", "unreadable"])
def test_archive_io_errors_match_plain_mbox_code(tmp_path, monkeypatch, kind):
    plain = tmp_path / "mail.mbox"
    source = tmp_path / "mail.zip"
    if kind == "unreadable":
        plain.write_bytes(MAIL)
        make_archive(source)
        original = Path.open
        def denied(path, mode="r", *args, **kwargs):
            if path in (plain, source) and mode == "rb":
                raise PermissionError("unreadable\n\x1b[31m")
            return original(path, mode, *args, **kwargs)
        monkeypatch.setattr(Path, "open", denied)
    [baseline] = convert_mbox(plain)
    [row] = convert_mbox_archive(source)
    assert row.error["code"] == baseline.error["code"] == "mbox_archive_error"
    assert "\n" not in row.error["message"] and "\x1b" not in row.error["message"]


@pytest.mark.parametrize("limit", ["members", "metadata"])
def test_zip_index_rejected_before_zipfile_construction(tmp_path, monkeypatch, limit):
    source = make_archive(tmp_path / "mail.zip", [(f"{i:07d}", b"") for i in range(400)])
    monkeypatch.setattr(zipfile, "ZipFile", lambda *a, **k: pytest.fail("central index was constructed"))
    limits = ArchiveLimits(max_members=10) if limit == "members" else ArchiveLimits(max_metadata_bytes=100)
    fatal(tmp_path, source, "limit_exceeded", archive_limits=limits)


def zip64_end_records(path):
    # Build actual ZIP64 EOCD/locator bytes around an ordinary small ZIP.
    data = path.read_bytes()
    at = data.rindex(b"PK\x05\x06")
    fields = list(struct.unpack("<4s4H2IH", data[at:at + 22]))
    count, size, offset = fields[4:7]
    record = struct.pack("<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, size, offset)
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, at, 1)
    fields[3:7] = [0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF]
    path.write_bytes(data[:at] + record + locator + struct.pack("<4s4H2IH", *fields))


def test_zip64_directory_preflight_and_limits(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "mail.zip")
    zip64_end_records(source)
    [row] = convert_mbox_archive(source, options=ConvertOptions(dry_run=True))
    assert row.success
    monkeypatch.setattr(zipfile, "ZipFile", lambda *a, **k: pytest.fail("central index was constructed"))
    fatal(tmp_path, source, "limit_exceeded", archive_limits=ArchiveLimits(max_metadata_bytes=1))


@pytest.mark.parametrize("damage", ["missing", "comment", "offset", "zip64_locator", "zip64_record"])
def test_zip_malformed_end_records(tmp_path, damage):
    source = make_archive(tmp_path / "mail.zip")
    if damage.startswith("zip64"):
        zip64_end_records(source)
    data = bytearray(source.read_bytes())
    at = data.rindex(b"PK\x05\x06")
    if damage == "missing":
        del data[at:]
    elif damage == "comment":
        struct.pack_into("<H", data, at + 20, 1)
    elif damage == "offset":
        struct.pack_into("<I", data, at + 16, len(data) + 1)
    elif damage == "zip64_locator":
        struct.pack_into("<Q", data, at - 12, len(data) + 1)
    else:
        data[at - 76:at - 72] = b"oops"
    source.write_bytes(data)
    fatal(tmp_path, source, "corrupt")


def test_zip_tail_read_is_bounded_and_full_comment_supported(tmp_path):
    source = make_archive(tmp_path / "mail.zip")
    with zipfile.ZipFile(source, "a") as out:
        out.comment = b"c" * 65535
    class Bounded(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 65535 + 22
            return super().read(size)
    archive._check_zip_directory(Bounded(source.read_bytes()), ArchiveLimits())
    [row] = convert_mbox_archive(source, options=ConvertOptions(dry_run=True))
    assert row.success


@pytest.mark.parametrize("field,value", [("flags", 1), ("method", 12)])
@pytest.mark.parametrize("name", ["ignored.json", "ignored.mbox"])
def test_ignored_zip_encryption_and_compression_not_opened(tmp_path, monkeypatch, field, value, name):
    source = make_archive(tmp_path / "mail.zip", [(name, b"ignored"), ("selected.mbox", MAIL)])
    patch_zip(source, field, value)
    original = zipfile.ZipFile.open
    def selected_only(self, member, *args, **kwargs):
        assert member.filename == "selected.mbox"
        return original(self, member, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", selected_only)
    [row] = convert_mbox_archive(source, member="selected.mbox", options=ConvertOptions(dry_run=True))
    assert row.success


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_record_container_metadata_is_detached(tmp_path, extension):
    source = make_archive(tmp_path / f"mail.{extension}", [("mail.mbox", MAIL * 2)])
    summary = {}
    with closing(archive._convert_mbox_archive(source, archive_summary=summary, options=ConvertOptions(dry_run=True))) as rows:
        first = next(rows)
        first.mbox["container"]["member_name"] = "mutated"
        second = next(rows)
    assert second.mbox["container"]["member_name"] == summary["member_name"] == "mail.mbox"


def set_eocd_count(path, count):
    data = bytearray(path.read_bytes())
    at = data.rindex(b"PK\x05\x06")
    struct.pack_into("<2H", data, at + 8, count, count)
    path.write_bytes(data)


def test_zip_understated_eocd_count_is_bounded_before_zipfile_construction(tmp_path, monkeypatch):
    # zipfile builds every entry inside cd_size and ignores the declared count.
    source = make_archive(tmp_path / "mail.zip", [(f"{i:07d}", b"") for i in range(50)])
    set_eocd_count(source, 1)
    monkeypatch.setattr(zipfile, "ZipFile", lambda *a, **k: pytest.fail("central index was constructed"))
    fatal(tmp_path, source, "limit_exceeded", archive_limits=ArchiveLimits(max_members=10))


@pytest.mark.parametrize("declared", [1, 4, 6])
def test_zip_eocd_count_must_match_central_directory(tmp_path, monkeypatch, declared):
    source = make_archive(tmp_path / "mail.zip", [("a.mbox", MAIL), *((f"{i}.json", b"{}") for i in range(4))])
    set_eocd_count(source, declared)
    monkeypatch.setattr(zipfile, "ZipFile", lambda *a, **k: pytest.fail("central index was constructed"))
    fatal(tmp_path, source, "corrupt")


def test_zip64_count_must_match_central_directory(tmp_path):
    source = make_archive(tmp_path / "mail.zip", [("a.mbox", MAIL), ("b.json", b"{}")])
    zip64_end_records(source)
    data = bytearray(source.read_bytes())
    record = data.rindex(b"PK\x06\x06")
    struct.pack_into("<2Q", data, record + 24, 1, 1)
    source.write_bytes(data)
    fatal(tmp_path, source, "corrupt")


def test_zip_central_directory_walk_rejects_bad_entry_signature(tmp_path):
    source = make_archive(tmp_path / "mail.zip", [("a.mbox", MAIL), ("b.json", b"{}")])
    data = bytearray(source.read_bytes())
    at = data.rindex(b"PK\x01\x02")
    data[at:at + 4] = b"PK\x09\x09"
    source.write_bytes(data)
    fatal(tmp_path, source, "corrupt")


@pytest.mark.parametrize("extension", ["zip", "tgz"])
def test_macos_metadata_members_are_ignored_and_never_opened(tmp_path, monkeypatch, extension):
    apple_double = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        " + b"\x00" * 64
    source = make_archive(tmp_path / f"mail.{extension}", [
        ("Takeout/Mail/All mail.mbox", MAIL),
        ("Takeout/Mail/._All mail.mbox", apple_double),
        ("__MACOSX/Takeout/Mail/._All mail.mbox", apple_double),
        ("__MACOSX/Takeout/Mail/Other.mbox", apple_double),
    ])
    opened = []
    if extension == "zip":
        original = zipfile.ZipFile.open
        def tracked(self, name, *args, **kwargs):
            opened.append(getattr(name, "orig_filename", name))
            return original(self, name, *args, **kwargs)
        monkeypatch.setattr(zipfile.ZipFile, "open", tracked)
    else:
        original = tarfile.TarFile.extractfile
        def tracked(self, member):
            opened.append(member.name)
            return original(self, member)
        monkeypatch.setattr(tarfile.TarFile, "extractfile", tracked)
    summary = {}
    with closing(archive._convert_mbox_archive(source, archive_summary=summary,
                                               options=ConvertOptions(dry_run=True))) as rows:
        [row] = list(rows)
    assert row.success and row.mbox["container"]["member_name"] == "Takeout/Mail/All mail.mbox"
    assert summary["ignored_member_count"] == 3
    assert opened == ["Takeout/Mail/All mail.mbox"]
    # macOS metadata cannot be selected as the mailbox either.
    fatal(tmp_path, source, "unsupported", member="Takeout/Mail/._All mail.mbox")


def test_tgz_refused_when_tarfile_hook_is_missing(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "mail.tgz")
    monkeypatch.delattr(tarfile.TarInfo, "_proc_member")
    row = fatal(tmp_path, source, "unsupported")
    assert "header hooks" in row.error["message"]


def test_tgz_refused_when_reader_bypasses_tarfile_hook(tmp_path, monkeypatch):
    # Model a future reader that parses headers without calling our subclass.
    source = make_archive(tmp_path / "mail.tgz", [("a.mbox", MAIL), ("b.json", b"{}")])
    original = tarfile.TarFile.next
    def bypass(self):
        self.tarinfo = tarfile.TarInfo
        return original(self)
    monkeypatch.setattr(tarfile.TarFile, "next", bypass)
    row = fatal(tmp_path, source, "unsupported")
    assert "header hooks" in row.error["message"]
