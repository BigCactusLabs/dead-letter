"""Opt-in flat-MBOX recovery: durable receipts and no-clobber publication.

The journal is local, private application state, not a portable/trusted input
format. It contains no executable paths. MIME conversion stays in mbox_import.
"""

from __future__ import annotations

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


class ResumeJournal:
    """One record in memory, SQLite receipts on disk, one active writer per root."""

    def __init__(self, root: Path, contract: dict[str, Any]) -> None:
        self.root = root
        self.state = root / ".dead-letter-resume"
        self.contract = _json(contract)
        self._stack = ExitStack()

    def __enter__(self) -> ResumeJournal:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            _directory(self.root)
            self.state.mkdir(mode=0o700, exist_ok=True)
            _directory(self.state)
            self._stack.enter_context(_lock(self.state / "lock"))
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
        return f"{index:08d}-{digest[:16]}.md"

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
                receipt = json.loads(receipt)
            except (ValueError, RecursionError) as exc:
                raise _conflict("Invalid resume receipt") from exc
            if not isinstance(receipt, dict) or set(receipt) != {"diagnostics"}:
                raise _conflict("Invalid resume receipt fields")
            if receipt["diagnostics"] is not None and not isinstance(receipt["diagnostics"], dict):
                raise _conflict("Invalid resume diagnostics")
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
            (stage / name).unlink()
        stage.rmdir()
        _sync_directory(self.state)

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
        stage = self.stage(identity)
        _directory(stage)
        size, digest = _digest(stage / self.name(identity), sync=True)
        _sync_directory(stage)
        receipt = _json({"diagnostics": diagnostics})
        with self.db:
            self.db.execute(
                "UPDATE records SET phase='prepared', receipt=?, size=?, digest=? WHERE id=?",
                (receipt, size, digest, identity["index"]),
            )

    def recover(self, identity: dict[str, Any]) -> tuple[Path, dict[str, Any], str, int] | None:
        row = self.load(identity)
        if row is None or row["phase"] not in {"prepared", "complete"}:
            return None
        target = self.root / self.name(identity)
        expected = (row["size"], row["digest"])
        if os.path.lexists(target):
            if _digest(target) != expected:
                raise _conflict(f"Output for record {identity['index']} was modified; nothing was overwritten")
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
        _sync_directory(self.root)
        with self.db:
            self.db.execute("UPDATE records SET phase='complete' WHERE id=?", (identity["index"],))
        self._cleanup(identity)
        outcome = "reused" if row["phase"] == "complete" else "recovered"
        return target, row["receipt"], outcome, row["attempt"]


def _engine_fingerprint() -> dict[str, Any]:
    """Invalidate editable-checkout changes as well as published versions."""
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py")) + [root.parent / "_mbox_worker.py", root.parent / "__init__.py"]:
        digest.update(path.name.encode())
        digest.update(bytes.fromhex(_digest(path)[1]))
    dependencies = ("mail-parser", "nh3", "html-to-markdown", "selectolax", "icalendar", "pyyaml", "mail-parser-reply")
    return {"source_sha256": digest.hexdigest(), "python": list(sys.version_info[:3]),
            "dependencies": {name: version(name) for name in dependencies}}


def convert_mbox_resumable(
    source: Path, root: Path, options: Any, limits: Any, *, unescape: str,
    timeout_seconds: float | None, budgets: Any,
) -> Iterator[Any]:
    """Implementation behind convert_mbox(resume=True); flat, immutable MBOX only."""
    from dead_letter.core.mbox import MboxFormatError, iter_mbox
    from dead_letter.core.mbox_import import MboxConversion, _convert_record
    from dead_letter.core.mbox_isolation import MboxBudgetError, convert_record_isolated

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
    try:
        with ResumeJournal(root, contract) as journal:
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
                        attempt = journal.start(identity)
                        outcome = "new" if attempt == 1 else "retried"
                        if timeout_seconds is not None and record.path is not None:
                            result = convert_record_isolated(
                                record, source, journal.stage(identity), options, bundles=False,
                                unescape=unescape, timeout=timeout_seconds, budgets=budgets,
                            )
                        else:
                            result = _convert_record(
                                record, source, journal.stage(identity), options,
                                bundles=False, unescape=unescape,
                            )
                        if result.success:
                            expected = journal.stage(identity) / journal.name(identity)
                            if result.output != expected:
                                raise _conflict("Converter returned an unexpected resume artifact")
                            journal.prepare(identity, result.diagnostics)
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
    except (OSError, sqlite3.Error, MboxFormatError) as exc:
        # Do not include private paths/parser text in the detached fatal receipt.
        yield MboxConversion(source.name, None, False, error={
            "code": "mbox_resume_io_error", "message": "Resume import failed; journal and outputs retained",
            "stage": "resume",
        })
