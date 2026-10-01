"""Opt-in MBOX recovery: durable receipts and no-clobber publication.

The journal is local, private application state, not a portable/trusted input
format. It contains no executable paths. MIME conversion stays in mbox_import.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
from contextlib import ExitStack, closing, contextmanager
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any, Iterator

MAX_RECEIPT_BYTES = 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
# os.link errnos meaning the output filesystem cannot provide no-clobber links.
_NO_LINK_ERRNOS = frozenset(
    getattr(errno, name) for name in ("EPERM", "ENOTSUP", "EOPNOTSUPP", "EXDEV") if hasattr(errno, name)
)


class MboxResumeError(ValueError):
    """A recovery conflict; never permission to remove a user's output."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def _conflict(message: str) -> MboxResumeError:
    return MboxResumeError("mbox_resume_conflict", message)


def _json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise MboxResumeError("mbox_resume_receipt_limit", "Resume receipt exceeds 1 MiB")
    return encoded


def _signature(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _directory(path: Path) -> None:
    if not stat.S_ISDIR(path.lstat().st_mode) or path.is_junction():
        raise _conflict("Resume directories must not be links or special files")


def _sync_directory(path: Path) -> None:
    # Python offers no portable Windows directory flush. This is explicitly
    # weaker than a power-loss guarantee; SQLite still flushes its own files.
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _digest(path: Path, *, sync: bool = False) -> tuple[int, str]:
    flags = (os.O_RDWR if sync else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise _conflict("Resume artifacts must be regular files, not links")
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise _conflict("Resume artifact changed while opening")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if sync:
            os.fsync(stream.fileno())
        if _signature(opened) != _signature(os.fstat(stream.fileno())):
            raise _conflict("Resume artifact changed while hashing")
    if _signature(opened) != _signature(path.lstat()):
        raise _conflict("Resume artifact changed while hashing")
    return opened.st_size, digest


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    if path.exists() or path.is_symlink():
        if not stat.S_ISREG(path.lstat().st_mode):
            raise _conflict("Resume lock must be a regular file")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _conflict("Resume lock must be a regular file")
        try:
            if os.name == "nt":
                import msvcrt
                # A persistent byte-range lock; never delete/recreate this file.
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise MboxResumeError("mbox_resume_busy", "Another import holds the resume lock") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# The layout is fixed-depth; neither a receipt nor a MIME filename can choose
# an arbitrary path. The count and JSON caps bound manifest memory per record.
MAX_BUNDLE_FILES = 4096
_LIMIT_MESSAGES = frozenset({"Resume receipt exceeds 1 MiB", "Resume bundle exceeds the file-count limit"})


def _rename_directory_noreplace(source: Path, target: Path) -> None:
    """Publish a complete directory without replacing even an empty destination."""
    if os.name == "nt":
        os.rename(source, target)  # Windows rename never replaces an existing path.
        return
    import ctypes
    import errno

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "linux":
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            args = (-100, os.fsencode(source), -100, os.fsencode(target), 1)  # AT_FDCWD, RENAME_NOREPLACE
        elif sys.platform == "darwin":
            rename = libc.renamex_np
            rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            args = (os.fsencode(source), os.fsencode(target), 4)  # RENAME_EXCL
        else:
            raise AttributeError("No supported exclusive directory rename")
    except (AttributeError, OSError) as exc:
        raise MboxResumeError("mbox_resume_unsupported", "Exclusive bundle publication is unavailable") from exc
    rename.restype = ctypes.c_int
    # ctypes calls must not bypass the Python audit event used by os.rename.
    sys.audit("os.rename", os.fspath(source), os.fspath(target), -1, -1)
    if rename(*args):
        code = ctypes.get_errno() or errno.EIO
        raise OSError(code, os.strerror(code))


def _probe_bundle_publication(state: Path) -> None:
    """Test the actual filesystem's no-replace behavior before converting mail."""
    from tempfile import TemporaryDirectory

    try:
        with TemporaryDirectory(prefix=".publish-probe-", dir=state) as temporary:
            source, target = Path(temporary) / "source", Path(temporary) / "target"
            source.mkdir()
            target.mkdir()
            before = target.stat()
            (source / "probe").write_bytes(b"probe")
            try:
                _rename_directory_noreplace(source, target)
            except FileExistsError:
                pass
            else:
                raise MboxResumeError("mbox_resume_unsupported", "Filesystem did not preserve an existing destination")
            if not os.path.samestat(before, target.stat()) or (source / "probe").read_bytes() != b"probe":
                raise _conflict("Bundle publication probe was modified")
            target.rmdir()
            _rename_directory_noreplace(source, target)
            if source.exists() or (target / "probe").read_bytes() != b"probe":
                raise MboxResumeError("mbox_resume_unsupported", "Filesystem did not publish the probe directory")
    except OSError as exc:
        raise MboxResumeError("mbox_resume_unsupported", "Filesystem cannot provide exclusive bundle publication") from exc


def _bundle_files(bundle: Path, *, complete: bool = True, bounded: bool = True) -> tuple[bool, list[Path]]:
    _directory(bundle)
    with os.scandir(bundle) as entries:
        names = {entry.name for _, entry in zip(range(4), entries)}
    if not names <= {"message.md", "source.eml", "attachments"}:
        raise _conflict("Unexpected files in resume bundle; nothing was removed")
    if complete and not {"message.md", "source.eml"} <= names:
        raise _conflict("Resume bundle is missing a required file")
    paths = [bundle / name for name in sorted(names - {"attachments"})]
    if "attachments" in names:
        _directory(bundle / "attachments")
        with os.scandir(bundle / "attachments") as entries:
            for entry in entries:
                if bounded and len(paths) >= MAX_BUNDLE_FILES:
                    raise MboxResumeError("mbox_resume_receipt_limit", "Resume bundle exceeds the file-count limit")
                paths.append(bundle / "attachments" / entry.name)
    for path in paths:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise _conflict("Resume bundle members must be regular files, not links or directories")
    return "attachments" in names, sorted(paths)


def _manifest_fingerprint(manifest: Any) -> tuple[int, str]:
    if not isinstance(manifest, dict) or set(manifest) != {"attachments", "files"}:
        raise _conflict("Invalid bundle manifest")
    members = manifest["files"]
    if type(manifest["attachments"]) is not bool or not isinstance(members, dict):
        raise _conflict("Invalid bundle manifest types")
    if not {"message.md", "source.eml"} <= members.keys() or len(members) > MAX_BUNDLE_FILES:
        raise _conflict("Invalid bundle manifest membership")
    total = 0
    for name, fingerprint in members.items():
        if name not in {"message.md", "source.eml"}:
            if not isinstance(name, str) or not name.startswith("attachments/") or not manifest["attachments"]:
                raise _conflict("Invalid bundle manifest path")
            leaf = name[len("attachments/"):]
            if leaf in {"", ".", ".."} or any(char in leaf for char in ("/", "\\", "\0")):
                raise _conflict("Invalid bundle manifest basename")
        if (
            not isinstance(fingerprint, list) or len(fingerprint) != 2
            or type(fingerprint[0]) is not int or fingerprint[0] < 0
            or not isinstance(fingerprint[1], str) or not _HEX.fullmatch(fingerprint[1])
        ):
            raise _conflict("Invalid bundle member fingerprint")
        total += fingerprint[0]
    if total >= 2**63:
        raise _conflict("Invalid bundle byte count")
    return total, hashlib.sha256(_json(manifest).encode("ascii")).hexdigest()


def _bundle_snapshot(bundle: Path, *, sync: bool = False) -> tuple[dict[str, Any], int]:
    attachments, paths = _bundle_files(bundle)
    before = bundle.lstat()
    nested = (bundle / "attachments").lstat() if attachments else None
    manifest = {"attachments": attachments, "files": {
        path.relative_to(bundle).as_posix(): list(_digest(path, sync=sync)) for path in paths
    }}
    _manifest_fingerprint(manifest)
    if sync:
        if attachments:
            _sync_directory(bundle / "attachments")
        _sync_directory(bundle)
    if _signature(bundle.lstat()) != _signature(before) or (
        nested is not None and _signature((bundle / "attachments").lstat()) != _signature(nested)
    ):
        raise _conflict("Resume bundle changed while hashing")
    # Only the inode: st_dev can change across a remount or reboot. The
    # same-filesystem requirement is checked live when the journal opens.
    return manifest, before.st_ino


def _unique_json_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate receipt field")
        result[key] = value
    return result


class ResumeJournal:
    """One record in memory, SQLite receipts on disk, one active writer per root."""

    def __init__(self, root: Path, contract: dict[str, Any], *, bundles: bool = False) -> None:
        self.root = root
        self.state = root / ".dead-letter-resume"
        self.bundles = bundles
        self.contract = _json({**contract, "bundles": bundles})
        self._stack = ExitStack()

    def __enter__(self) -> ResumeJournal:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            _directory(self.root)
            self.state.mkdir(mode=0o700, exist_ok=True)
            _directory(self.state)
            self._stack.enter_context(_lock(self.state / "lock"))
            if self.bundles:
                # Probe before binding the layout, so a refusal leaves no contract.
                if self.state.stat().st_dev != self.root.stat().st_dev:
                    raise MboxResumeError("mbox_resume_unsupported", "Bundle staging and output must share a filesystem")
                _probe_bundle_publication(self.state)
            database = self.state / "journal.sqlite3"
            # Do not let SQLite follow linked database/rollback-journal files.
            for path in (database, self.state / "journal.sqlite3-journal"):
                if path.exists() or path.is_symlink():
                    if not stat.S_ISREG(path.lstat().st_mode):
                        raise _conflict("Resume database files must be regular files")
            self.db = self._stack.enter_context(closing(sqlite3.connect(database, timeout=0)))
            self.db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_RECEIPT_BYTES + 65536)
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=EXTRA")
            self.db.execute("PRAGMA cache_size=-2048")
            with self.db:
                self.db.execute("CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY, contract TEXT NOT NULL)")
                self.db.execute("""CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY, identity TEXT NOT NULL, phase TEXT NOT NULL,
                    attempt INTEGER NOT NULL, receipt TEXT, size INTEGER, digest TEXT)""")
                saved = self.db.execute("SELECT contract FROM meta WHERE id=1").fetchone()
                if saved is None:
                    if self.db.execute("SELECT 1 FROM records LIMIT 1").fetchone():
                        raise _conflict("Resume journal has records but no source contract")
                    self.db.execute("INSERT INTO meta VALUES (1, ?)", (self.contract,))
                elif saved[0] != self.contract:
                    raise MboxResumeError(
                        "mbox_resume_mismatch", "Source, output, converter or options differ from the resume journal",
                    )
            _sync_directory(self.state)
            _sync_directory(self.root)
            return self
        except BaseException:
            self._stack.close()
            raise

    def __exit__(self, *args: object) -> None:
        self._stack.close()

    def name(self, identity: dict[str, Any]) -> str:
        index, digest = identity.get("index"), identity.get("sha256")
        if type(index) is not int or not 1 <= index < 2**63 or not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise _conflict("Invalid resume record identity")
        return f"{index:08d}-{digest[:16]}" + ("" if self.bundles else ".md")

    def stage(self, identity: dict[str, Any]) -> Path:
        # This path is derived from the current scanner, never from saved JSON.
        return self.state / self.name(identity).removesuffix(".md")

    def load(self, identity: dict[str, Any]) -> dict[str, Any] | None:
        self.name(identity)
        row = self.db.execute(
            "SELECT identity, phase, attempt, receipt, size, digest FROM records WHERE id=?", (identity["index"],),
        ).fetchone()
        if row is None:
            return None
        original, phase, attempt, receipt, size, digest = row
        if original != _json(identity) or phase not in {"started", "prepared", "complete", "failed"}:
            raise _conflict("Resume record identity or phase is inconsistent")
        if type(attempt) is not int or attempt < 1:
            raise _conflict("Invalid resume attempt count")
        if phase in {"prepared", "complete"}:
            if type(size) is not int or size < 0 or not isinstance(digest, str) or not _HEX.fullmatch(digest):
                raise _conflict("Invalid resume output fingerprint")
            if not isinstance(receipt, str) or len(receipt) > MAX_RECEIPT_BYTES:
                raise _conflict("Invalid resume receipt")
            try:
                receipt = json.loads(receipt, object_pairs_hook=_unique_json_fields)
            except (ValueError, RecursionError) as exc:
                raise _conflict("Invalid resume receipt") from exc
            fields = {"diagnostics", "manifest", "directory_id"} if self.bundles else {"diagnostics"}
            if not isinstance(receipt, dict) or set(receipt) != fields:
                raise _conflict("Invalid resume receipt fields")
            if receipt["diagnostics"] is not None and not isinstance(receipt["diagnostics"], dict):
                raise _conflict("Invalid resume diagnostics")
            if self.bundles:
                if _manifest_fingerprint(receipt["manifest"]) != (size, digest):
                    raise _conflict("Bundle manifest fingerprint is inconsistent")
                if type(receipt["directory_id"]) is not int or receipt["directory_id"] < 0:
                    raise _conflict("Invalid bundle directory identity")
        return dict(phase=phase, attempt=attempt, receipt=receipt, size=size, digest=digest)

    def _cleanup(self, identity: dict[str, Any]) -> None:
        stage = self.stage(identity)
        if not stage.exists() and not stage.is_symlink():
            return
        _directory(stage)
        # No recursive deletion: only the exact, journal-owned staging file.
        with os.scandir(stage) as entries:
            names = [entry.name for _, entry in zip(range(2), entries)]
        name = self.name(identity)
        if names not in ([], [name]):
            raise _conflict("Unexpected files in resume staging; nothing was removed")
        if names:
            if self.bundles:
                row = self.load(identity)
                if row is None or row["phase"] == "complete":
                    raise _conflict("Unexpected staging beside a completed bundle; nothing was removed")
                _bundle_files(stage / name, complete=False, bounded=False)
                # The manifest was not committed. Retain partial files privately
                # rather than recursively deleting unknown attachment contents.
                abandoned = self.state / f".abandoned-{name}-{row['attempt']}"
                if self._limit_failure(identity) and not os.path.lexists(self._limit_copy(identity)):
                    # One copy per over-limit record; retained_limit() then
                    # fails reruns without reconverting into another copy.
                    abandoned = self._limit_copy(identity)
                _rename_directory_noreplace(stage, abandoned)
                _sync_directory(self.state)
                return
            (stage / name).unlink()
        stage.rmdir()
        _sync_directory(self.state)

    def _limit_copy(self, identity: dict[str, Any]) -> Path:
        return self.state / f".abandoned-{self.name(identity)}-limit"

    def _limit_failure(self, identity: dict[str, Any]) -> str | None:
        """The fixed message of a recorded receipt-limit failure, if any."""
        row = self.db.execute("SELECT phase, receipt FROM records WHERE id=?", (identity["index"],)).fetchone()
        if row is None or row[0] != "failed" or not isinstance(row[1], str) or len(row[1]) > MAX_RECEIPT_BYTES:
            return None
        try:
            error = json.loads(row[1], object_pairs_hook=_unique_json_fields)
        except (ValueError, RecursionError):
            return None
        if not isinstance(error, dict) or error.get("code") != "mbox_resume_receipt_limit":
            return None
        return error.get("message") if error.get("message") in _LIMIT_MESSAGES else None

    def retained_limit(self, identity: dict[str, Any]) -> dict[str, str] | None:
        """The failure to repeat for a record whose over-limit bundle is retained.

        Under one contract a record converts deterministically, so a rerun
        records a new failed attempt instead of reconverting into a new copy.
        """
        if not self.bundles or not os.path.lexists(self._limit_copy(identity)):
            return None
        row = self.load(identity)
        if row is None or row["phase"] not in {"started", "failed"}:
            return None
        message = self._limit_failure(identity) or "Resume bundle exceeds a receipt limit"
        return {"code": "mbox_resume_receipt_limit", "message": message, "stage": "resume"}

    def start(self, identity: dict[str, Any]) -> int:
        previous = self.load(identity)
        target = self.root / self.name(identity)
        if os.path.lexists(target):
            raise _conflict(f"Output for record {identity['index']} already exists without a reusable receipt")
        if previous is not None:
            self._cleanup(identity)
        elif os.path.lexists(self.stage(identity)):
            raise _conflict("Unowned resume staging directory already exists")
        attempt = 1 if previous is None else previous["attempt"] + 1
        # Commit intent BEFORE creating staging; a crash leaves owned partial work.
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO records VALUES (?, ?, 'started', ?, NULL, NULL, NULL)",
                (identity["index"], _json(identity), attempt),
            )
        self.stage(identity).mkdir(mode=0o700)
        _sync_directory(self.state)
        return attempt

    def failed(self, identity: dict[str, Any], error: dict[str, str] | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE records SET phase='failed', receipt=? WHERE id=?", (_json(error), identity["index"]),
            )
        self._cleanup(identity)

    def prepare(self, identity: dict[str, Any], diagnostics: dict[str, Any] | None) -> None:
        if self.bundles:
            self._prepare_bundle(identity, diagnostics)
            return
        # Size-check the receipt first; an oversized one leaves the record started.
        receipt = _json({"diagnostics": diagnostics})
        stage = self.stage(identity)
        _directory(stage)
        size, digest = _digest(stage / self.name(identity), sync=True)
        _sync_directory(stage)
        with self.db:
            self.db.execute(
                "UPDATE records SET phase='prepared', receipt=?, size=?, digest=? WHERE id=?",
                (receipt, size, digest, identity["index"]),
            )

    def recover(self, identity: dict[str, Any]) -> tuple[Path, dict[str, Any], str, int] | None:
        row = self.load(identity)
        if row is None or row["phase"] not in {"prepared", "complete"}:
            return None
        if self.bundles:
            return self._recover_bundle(identity, row)
        target = self.root / self.name(identity)
        expected = (row["size"], row["digest"])
        if os.path.lexists(target):
            if _digest(target) != expected:
                raise _conflict(f"Output for record {identity['index']} was modified; nothing was overwritten")
            if row["phase"] == "complete" and not os.path.lexists(self.stage(identity)):
                # Verified and already durable: no directory sync or journal write.
                return target, row["receipt"], "reused", row["attempt"]
        else:
            if row["phase"] == "complete":
                return None  # A removed output must be retried, not called complete.
            stage = self.stage(identity)
            if not os.path.lexists(stage):
                return None
            _directory(stage)
            pending = stage / self.name(identity)
            if not os.path.lexists(pending):
                return None
            if _digest(pending) != expected:
                raise _conflict("Prepared resume artifact was modified")
            try:
                # Atomic no-replace publication on the same filesystem. Never
                # fall back to rename/replace or a partially visible copy.
                os.link(pending, target)
            except FileExistsError:
                if _digest(target) != expected:
                    raise _conflict(f"Output for record {identity['index']} changed during publication") from None
            except OSError as exc:
                if exc.errno in _NO_LINK_ERRNOS:
                    raise MboxResumeError(
                        "mbox_resume_io_error", "Output filesystem does not support hard links required for resume",
                    ) from exc
                raise
        _sync_directory(self.root)
        with self.db:
            self.db.execute("UPDATE records SET phase='complete' WHERE id=?", (identity["index"],))
        self._cleanup(identity)
        outcome = "reused" if row["phase"] == "complete" else "recovered"
        return target, row["receipt"], outcome, row["attempt"]


    def _prepare_bundle(self, identity: dict[str, Any], diagnostics: dict[str, Any] | None) -> None:
        stage = self.stage(identity)
        _directory(stage)
        bundle = stage / self.name(identity)
        # Check both limits before hashing: an equal-length placeholder digest
        # gives the final receipt size, so an over-limit record costs no hashing.
        attachments, paths = _bundle_files(bundle)
        _json({"diagnostics": diagnostics, "directory_id": bundle.lstat().st_ino, "manifest": {
            "attachments": attachments,
            "files": {path.relative_to(bundle).as_posix(): [path.lstat().st_size, "0" * 64] for path in paths},
        }})
        manifest, directory_id = _bundle_snapshot(bundle, sync=True)
        size, digest = _manifest_fingerprint(manifest)
        _sync_directory(stage)
        receipt = _json({"diagnostics": diagnostics, "manifest": manifest, "directory_id": directory_id})
        with self.db:
            self.db.execute(
                "UPDATE records SET phase='prepared', receipt=?, size=?, digest=? WHERE id=?",
                (receipt, size, digest, identity["index"]),
            )

    def _publish_bundle(self, pending: Path, target: Path) -> None:
        _rename_directory_noreplace(pending, target)

    def _recover_bundle(self, identity: dict[str, Any], row: dict[str, Any]) -> tuple[Path, dict[str, Any], str, int] | None:
        target = self.root / self.name(identity)
        stage = self.stage(identity)
        present = os.path.lexists(target)
        if present:
            candidate = target
        else:
            if row["phase"] == "complete" or not os.path.lexists(stage):
                return None  # Retry a wholly missing bundle, never patch an edited one.
            _directory(stage)
            candidate = stage / self.name(identity)
            if not os.path.lexists(candidate):
                return None
        manifest, directory_id = _bundle_snapshot(candidate)
        if manifest != row["receipt"]["manifest"] or directory_id != row["receipt"]["directory_id"]:
            raise _conflict(f"Bundle for record {identity['index']} was modified or replaced; nothing was overwritten")
        if not present:
            try:
                self._publish_bundle(candidate, target)
            except FileExistsError as exc:
                # Even an identical foreign bundle is not the staged directory.
                raise _conflict(f"Output for record {identity['index']} appeared during bundle publication") from exc
        _sync_directory(self.root)
        if os.path.lexists(stage):
            _directory(stage)
            _sync_directory(stage)
        with self.db:
            self.db.execute("UPDATE records SET phase='complete' WHERE id=?", (identity["index"],))
        self._cleanup(identity)
        outcome = "reused" if row["phase"] == "complete" else "recovered"
        return target / "message.md", row["receipt"], outcome, row["attempt"]


def _engine_fingerprint() -> dict[str, Any]:
    """Invalidate editable-checkout changes as well as published versions."""
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py")) + [root.parent / "_mbox_worker.py", root.parent / "__init__.py"]:
        digest.update(path.name.encode())
        # Package sources, not resume artifacts: symlinked installs are fine.
        with path.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
    dependencies = ("mail-parser", "nh3", "html-to-markdown", "selectolax", "icalendar", "pyyaml", "mail-parser-reply")
    return {"source_sha256": digest.hexdigest(), "python": list(sys.version_info[:3]),
            "dependencies": {name: version(name) for name in dependencies}}


def convert_mbox_resumable(
    source: Path, root: Path, options: Any, limits: Any, *, unescape: str,
    timeout_seconds: float | None, budgets: Any, bundles: bool = False,
) -> Iterator[Any]:
    """Implementation behind convert_mbox(resume=True); flat, immutable MBOX only."""
    from dead_letter.core.mbox import MboxFormatError, iter_mbox
    from dead_letter.core.mbox_import import MboxConversion, _convert_record
    from dead_letter.core.mbox_isolation import MboxBudgetError, convert_record_isolated

    try:
        before = _signature(source.stat())
        size, digest = _digest(source)
        if _signature(source.stat()) != before:
            raise _conflict("MBOX changed while fingerprinting")
        conversion = asdict(options)
        conversion.pop("report", None)  # A report can be added/rebuilt without reconversion.
        contract = dict(schema=1, source=str(source), bytes=size, sha256=digest, output=str(root),
                        options=conversion, limits=asdict(limits), unescape=unescape,
                        timeout_seconds=timeout_seconds, budgets=asdict(budgets) if budgets else None,
                        engine=_engine_fingerprint())
        with ResumeJournal(root, contract, bundles=bundles) as journal:
            if _signature(source.stat()) != before:
                raise _conflict("MBOX changed before framing")
            with closing(iter_mbox(source, limits=limits, unescape=unescape)) as records:
                for record in records:
                    identity = record.provenance(source)
                    cached = journal.recover(identity)
                    if cached is not None:
                        output, receipt, outcome, attempt = cached
                        result = MboxConversion(
                            f"{source.name}#message-{record.index:08d}", output, True,
                            {**identity, "unescape": unescape}, receipt["diagnostics"],
                        )
                    else:
                        retained = journal.retained_limit(identity)
                        attempt = journal.start(identity)
                        outcome = "new" if attempt == 1 else "retried"
                        if retained is not None:
                            journal.failed(identity, retained)
                            result = MboxConversion(
                                f"{source.name}#message-{record.index:08d}", None, False,
                                {**identity, "unescape": unescape}, error=retained,
                            )
                        elif timeout_seconds is not None and record.path is not None:
                            result = convert_record_isolated(
                                record, source, journal.stage(identity), options, bundles=bundles,
                                unescape=unescape, timeout=timeout_seconds, budgets=budgets,
                            )
                        else:
                            result = _convert_record(
                                record, source, journal.stage(identity), options,
                                bundles=bundles, unescape=unescape,
                            )
                        if result.success:
                            expected = journal.stage(identity) / journal.name(identity)
                            if bundles:
                                expected /= "message.md"
                            if result.output != expected:
                                raise _conflict("Converter returned an unexpected resume artifact")
                            try:
                                journal.prepare(identity, result.diagnostics)
                            except MboxResumeError as exc:
                                if exc.code != "mbox_resume_receipt_limit":
                                    raise
                                # One untrusted message must not stop the import:
                                # withhold its output and fail only this record.
                                error = {"code": exc.code, "message": exc.message, "stage": "resume"}
                                journal.failed(identity, error)
                                result = MboxConversion(result.source, None, False, result.mbox, error=error)
                            else:
                                published = journal.recover(identity)
                                assert published is not None
                                result.output = published[0]
                        else:
                            journal.failed(identity, result.error)
                    result.recovery = {"status": outcome, "attempt": attempt}
                    yield result
            if _signature(source.stat()) != before:
                raise _conflict("MBOX changed during resumed import")
    except (MboxResumeError, MboxBudgetError) as exc:
        yield MboxConversion(source.name, None, False, error={
            "code": exc.code, "message": exc.message, "stage": "resume",
        })
    except MboxFormatError as exc:
        # Fixed framing text, reported as the non-resume path does.
        yield MboxConversion(source.name, None, False, error={
            "code": "mbox_archive_error", "message": str(exc).replace(str(source), source.name), "stage": "mbox",
        })
    except (OSError, sqlite3.Error) as exc:
        # Name only a known errno/SQLite code; never paths, strerror or parser text.
        reason = None
        if isinstance(exc, OSError) and type(exc.errno) is int:
            reason = errno.errorcode.get(exc.errno)
        elif isinstance(exc, sqlite3.Error):
            name = getattr(exc, "sqlite_errorname", None)
            reason = name if isinstance(name, str) and re.fullmatch(r"SQLITE_[A-Z_]+", name) else None
        detail = f" ({reason})" if reason else ""
        yield MboxConversion(source.name, None, False, error={
            "code": "mbox_resume_io_error", "message": f"Resume import failed{detail}; journal and outputs retained",
            "stage": "resume",
        })
