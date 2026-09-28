"""Read-only compressed MBOX staging. Archive names never become disk paths."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO

from dead_letter.core.mbox import MboxFormatError, MboxLimits, UnescapeMode, _check_source, _source_signature
from dead_letter.core.mbox_import import MboxConversion, _convert_mbox
from dead_letter.core.types import ConvertOptions

_CHUNK = 64 * 1024


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Container entry and decompression budgets; no compression-ratio cap."""

    max_decompressed_bytes: int = 256 * 1024**3
    max_members: int = 100_000

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in (
            self.max_decompressed_bytes, self.max_members,
        )):
            raise ValueError("Archive limits must be positive integers")


class _ArchiveError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = f"mbox_archive_{code}"
        super().__init__(message)


def _safe_name(name: str) -> str:
    # Escape control/bidi characters in terminal errors; retain exact names for
    # selection and structured provenance. Cap diagnostics, never the identity.
    return json.dumps(name[:512], ensure_ascii=True)


def _validate_name(name: str) -> None:
    if ("\x00" in name or name.startswith(("/", "\\"))
            or PureWindowsPath(name).drive or ".." in name.replace("\\", "/").split("/")):
        raise _ArchiveError("unsupported", "Unsafe archive member name: " + _safe_name(name))


class _Selection:
    def __init__(self, member: str | None, limits: ArchiveLimits):
        self.member = member
        self.limits = limits
        self.names: set[str] = set()
        self.candidates: list[str] = []
        self.count = 0

    def consider(self, name: str, regular: bool) -> bool:
        self.count += 1
        if self.count > self.limits.max_members:
            raise _ArchiveError("limit_exceeded", "Archive member count exceeds configured limit")
        _validate_name(name)
        if not name.lower().endswith(".mbox"):
            if name == self.member:
                raise _ArchiveError("unsupported", "Selected member must be a regular .mbox file")
            return False
        if name in self.names:
            raise _ArchiveError("duplicate_member", "Duplicate MBOX member: " + _safe_name(name))
        self.names.add(name)
        if not regular:
            # Do not silently downgrade a mailbox link to an ignored attachment.
            raise _ArchiveError("unsupported", "MBOX member is not a regular file: " + _safe_name(name))
        self.candidates.append(name)
        return name == self.member if self.member is not None else len(self.candidates) == 1

    def finish(self) -> None:
        if not self.candidates or (self.member is not None and self.member not in self.candidates):
            raise _ArchiveError("no_mbox", "No matching regular MBOX member found")
        if self.member is None and len(self.candidates) > 1:
            names = ", ".join(_safe_name(name) for name in self.candidates[:20])
            raise _ArchiveError("multiple_mbox", "Select an exact MBOX member name: " + names)


def _copy_member(stream: BinaryIO, target: Path, limits: ArchiveLimits) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as out:
        while chunk := stream.read(min(_CHUNK, limits.max_decompressed_bytes - size + 1)):
            size += len(chunk)
            if size > limits.max_decompressed_bytes:
                raise _ArchiveError("limit_exceeded", "Decompressed bytes exceed configured limit")
            digest.update(chunk)
            out.write(chunk)
    return size, digest.hexdigest()


def _stage_zip(raw: BinaryIO, target: Path, selection: _Selection) -> dict[str, Any]:
    with zipfile.ZipFile(raw) as container:
        chosen = None
        for info in container.infolist():
            # orig_filename preserves NULs that ZipInfo.filename truncates.
            mode = info.external_attr >> 16
            regular = not info.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG)
            selected = selection.consider(info.orig_filename, regular)
            if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise _ArchiveError("unsupported", "Encrypted or unsupported ZIP compression")
            if selected:
                chosen = info
        selection.finish()
        assert chosen is not None
        if chosen.file_size > selection.limits.max_decompressed_bytes:
            raise _ArchiveError("limit_exceeded", "Declared MBOX size exceeds configured limit")
        if shutil.disk_usage(target.parent).free < chosen.file_size:
            raise _ArchiveError("insufficient_space", "Insufficient staging space for declared MBOX size")
        with container.open(chosen) as stream:
            size, digest = _copy_member(stream, target, selection.limits)
        if size != chosen.file_size:
            raise _ArchiveError("corrupt", "MBOX member size does not match its declaration")
        return {
            "format": "zip", "member_name": chosen.orig_filename,
            "member_compressed_bytes": chosen.compress_size,
            "member_uncompressed_bytes": chosen.file_size, "crc32": f"{chosen.CRC:08x}",
            "staged_bytes": size, "member_sha256": digest,
            "ignored_member_count": selection.count - 1,
        }


def _format(source: Path, raw: BinaryIO) -> str:
    magic = raw.read(4)
    raw.seek(0)
    name = source.name.lower()
    if name.endswith(".zip") and magic in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x06\x06"):
        if sys.version_info < (3, 12, 3):
            raise _ArchiveError("python_too_old", "ZIP input requires Python 3.12.3 or newer")
        return "zip"
    if name.endswith((".tgz", ".tar.gz")) and magic.startswith(b"\x1f\x8b"):
        return "tgz"
    raise _ArchiveError("unsupported", "Expected ZIP or TGZ with matching filename extension and magic bytes")


def convert_mbox_archive(
    path: str | Path,
    *,
    member: str | None = None,
    staging_dir: str | Path | None = None,
    archive_limits: ArchiveLimits | None = None,
    output: str | Path | None = None,
    options: ConvertOptions | None = None,
    limits: MboxLimits | None = None,
    unescape: UnescapeMode = "preserve",
    bundles: bool = False,
    timeout_seconds: float | None = None,
) -> Iterator[MboxConversion]:
    """Fully stage one ZIP/TGZ mailbox, then use the ordinary MBOX pipeline.

    The original is only opened read-only. Close the iterator on early exit.
    Fatal staging failures produce one result with ``mbox is None`` and no
    message conversion. Offsets/hashes locate bytes in the decompressed member.
    """
    yield from _convert_mbox_archive(
        path, member=member, staging_dir=staging_dir, archive_limits=archive_limits,
        output=output, options=options, limits=limits, unescape=unescape,
        bundles=bundles, timeout_seconds=timeout_seconds,
    )


def _convert_mbox_archive(
    path: str | Path, *, member: str | None = None,
    staging_dir: str | Path | None = None, archive_limits: ArchiveLimits | None = None,
    archive_summary: dict[str, Any] | None = None, **conversion_options: Any,
) -> Iterator[MboxConversion]:
    source = Path(path).expanduser().resolve()
    opts = conversion_options.get("options") or ConvertOptions()
    if opts.delete_eml:
        raise ValueError("--delete-eml is not supported for MBOX; the archive is always preserved")
    output = conversion_options.pop("output", None)
    root = Path(output).expanduser().resolve() if output is not None else source.with_suffix(".markdown")
    if root == source or root.suffix.lower() == ".md" or (root.exists() and not root.is_dir()):
        raise ValueError("MBOX output must be a directory distinct from the source")
    selection = _Selection(member, archive_limits or ArchiveLimits())
    try:
        if not source.is_file():
            raise _ArchiveError("unsupported", "Archive input must be an existing regular file")
        with source.open("rb") as raw:
            initial, initial_path = os.fstat(raw.fileno()), source.stat()
            if not stat.S_ISREG(initial.st_mode):
                raise _ArchiveError("unsupported", "Archive input must be a regular file")
            _check_container(raw, source, initial, initial_path)
            format_name = _format(source, raw)
            staging_parent = Path(staging_dir).expanduser() if staging_dir is not None else None
            with TemporaryDirectory(prefix="dead-letter-archive-", dir=staging_parent) as temporary:
                staged = Path(temporary) / "member.mbox"
                try:
                    metadata = (_stage_zip if format_name == "zip" else _stage_tgz)(raw, staged, selection)
                except Exception:
                    # Mutation may first appear as a decoder/CRC error. Preserve
                    # the more useful immutable-source failure in that case.
                    _check_container(raw, source, initial, initial_path)
                    raise
                _check_container(raw, source, initial, initial_path)
                metadata.update({
                    "container_path": str(path), "container_size": initial.st_size,
                    "container_stat_signature": dict(zip(
                        ("device", "inode", "size", "mtime_ns", "ctime_ns"),
                        _source_signature(initial), strict=True,
                    )),
                })
                if archive_summary is not None:
                    archive_summary.update(metadata)
                with closing(_convert_mbox(staged, output=root, archive=metadata, **conversion_options)) as results:
                    yield from results
    except _ArchiveError as exc:
        yield MboxConversion(source.name, None, False, error={
            "code": exc.code, "message": str(exc), "stage": "mbox",
        })
    except (OSError, EOFError, zipfile.BadZipFile, tarfile.TarError, zlib.error, RecursionError) as exc:
        code = "insufficient_space" if isinstance(exc, OSError) and exc.errno == errno.ENOSPC else "corrupt"
        yield MboxConversion(source.name, None, False, error={
            "code": f"mbox_archive_{code}", "message": f"Archive staging failed ({type(exc).__name__})",
            "stage": "mbox",
        })


def _check_container(raw: BinaryIO, source: Path, initial: os.stat_result, initial_path: os.stat_result) -> None:
    try:
        _check_source(raw.fileno(), source, initial, initial_path)
    except MboxFormatError:
        raise _ArchiveError("changed", "Container changed during staging; use an immutable export") from None


class _CheckedGzipInput:
    """Verify gzip framing/CRC while tarfile's r|gz reader consumes the same bytes.

    tarfile's streaming gzip adapter does not check the trailer and inflates
    each compressed read without a max_length. Small compressed reads bound its
    inflation buffer; our separate validator counts all expanded TAR bytes.
    This costs a second inflation, but no second source pass or archive copy.
    """

    def __init__(self, raw: BinaryIO, limit: int):
        self.raw = raw
        self.validator = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self.limit = limit
        self.size = 0

    def read(self, size: int) -> bytes:
        data = self.raw.read(min(size, 4096))
        if not data:
            if not self.validator.eof:
                raise _ArchiveError("corrupt", "Truncated gzip stream")
            return b""
        pending = data
        while pending:
            expanded = self.validator.decompress(pending, min(_CHUNK, self.limit - self.size + 1))
            self.size += len(expanded)
            if self.size > self.limit:
                raise _ArchiveError("limit_exceeded", "Expanded TAR bytes exceed configured limit")
            pending = self.validator.unconsumed_tail
            if self.validator.unused_data:
                # tarfile r|gz reads one gzip stream; do not ignore a second one.
                raise _ArchiveError("corrupt", "Unexpected bytes after gzip stream")
        return data

    def finish(self) -> None:
        while self.read(_CHUNK):
            pass


def _stage_tgz(raw: BinaryIO, target: Path, selection: _Selection) -> dict[str, Any]:
    checked = _CheckedGzipInput(raw, selection.limits.max_decompressed_bytes)
    scanned = depth = 0

    class BoundedTarInfo(tarfile.TarInfo):
        def _proc_member(self, tar):
            # This hook runs before extension processing on both 3.12 and newer
            # readers (newer readers bypass the public frombuf/fromtarfile).
            nonlocal scanned, depth
            scanned += 1
            if scanned > selection.limits.max_members:
                raise _ArchiveError("limit_exceeded", "Archive member count exceeds configured limit")
            if self.size < 0:
                raise _ArchiveError("corrupt", "Negative TAR member size")
            if self.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                             tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
                if self.size > 1024 * 1024:
                    raise _ArchiveError("limit_exceeded", "TAR extended metadata exceeds 1 MiB")
            if self.type == tarfile.GNUTYPE_SPARSE:
                raise _ArchiveError("unsupported", "Sparse TAR members are not supported")
            depth += 1
            try:
                if depth > 64:
                    raise _ArchiveError("limit_exceeded", "TAR metadata nesting exceeds 64 headers")
                return super()._proc_member(tar)
            finally:
                depth -= 1

        def _reject_sparse(self, *args):
            raise _ArchiveError("unsupported", "Sparse TAR members are not supported")

        _proc_gnusparse_00 = _reject_sparse
        _proc_gnusparse_01 = _reject_sparse
        _proc_gnusparse_10 = _reject_sparse

    metadata = None
    with tarfile.open(fileobj=checked, mode="r|gz", tarinfo=BoundedTarInfo) as container:
        for info in container:
            if info.size < 0:
                raise _ArchiveError("corrupt", "Negative TAR member size")
            regular = info.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and info.sparse is None
            if selection.consider(info.name, regular):
                if info.size > selection.limits.max_decompressed_bytes:
                    raise _ArchiveError("limit_exceeded", "Declared MBOX size exceeds configured limit")
                with container.extractfile(info) as stream:
                    size, digest = _copy_member(stream, target, selection.limits)
                if size != info.size:
                    raise _ArchiveError("corrupt", "MBOX member size does not match its declaration")
                metadata = {
                    "format": "tgz", "member_name": info.name,
                    "member_compressed_bytes": None, "member_uncompressed_bytes": info.size,
                    "crc32": None, "staged_bytes": size, "member_sha256": digest,
                }
        # Consume through the gzip trailer, including TAR padding after its EOF
        # blocks. Even damage after a fully staged MBOX must prevent conversion.
        checked.finish()
    selection.finish()
    assert metadata is not None
    metadata["ignored_member_count"] = selection.count - 1
    return metadata
