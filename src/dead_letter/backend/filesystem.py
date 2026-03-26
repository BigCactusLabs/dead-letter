"""Shared filesystem helpers for browse and watch features."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal


@dataclass(slots=True)
class FsEntry:
    name: str
    path: str
    input_path: str
    type: Literal["file", "directory"]
    size: int
    modified: str


class FilesystemBrowser:
    """Browse the local filesystem within a configured root directory."""

    def __init__(self, *, root: str | Path | None = None) -> None:
        self._root = Path(root or Path.home()).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def normalize_relative(self, relative_path: str | None) -> str:
        raw = (relative_path or "").strip()
        if raw in {"", "."}:
            return ""

        candidate_path = Path(raw)
        if candidate_path.is_absolute():
            raise PermissionError(f"absolute paths are not allowed: {relative_path}")

        normalized = posixpath.normpath(candidate_path.as_posix())
        if normalized in {"", "."}:
            return ""
        if normalized == ".." or normalized.startswith("../"):
            raise PermissionError(f"path escapes configured root: {relative_path}")
        return normalized

    def resolve_relative(self, relative_path: str | None) -> Path:
        normalized = self.normalize_relative(relative_path)
        if normalized == "":
            return self._root

        resolved = (self._root / Path(normalized)).resolve()
        if not resolved.is_relative_to(self._root):
            raise PermissionError(f"path escapes configured root: {relative_path}")
        return resolved

    def to_relative(self, path: str | Path) -> str:
        resolved = Path(path).expanduser().resolve()
        if resolved == self._root:
            return ""
        if not resolved.is_relative_to(self._root):
            raise PermissionError(f"path escapes configured root: {path}")
        return resolved.relative_to(self._root).as_posix()

    def list_dir(self, relative_path: str | None, *, filter_ext: str | None = None) -> list[FsEntry]:
        normalized_path = self.normalize_relative(relative_path)
        target = self.resolve_relative(normalized_path)
        if not target.exists():
            raise FileNotFoundError(f"path does not exist: {relative_path}")
        if not target.is_dir():
            raise ValueError(f"path is not a directory: {relative_path}")

        entries: list[FsEntry] = []
        for item in sorted(target.iterdir(), key=lambda child: child.name.lower()):
            if item.name.startswith("."):
                continue

            try:
                is_dir = item.is_dir()
                if not is_dir and filter_ext and item.suffix.lower() != filter_ext.lower():
                    continue

                stat = item.stat()
                input_path = str(item.resolve())
                if not Path(input_path).is_relative_to(self._root):
                    raise PermissionError(f"path escapes configured root: {item}")
                entry_path = item.name if normalized_path == "" else f"{normalized_path}/{item.name}"
            except (OSError, PermissionError, ValueError):
                continue

            entries.append(
                FsEntry(
                    name=item.name,
                    path=entry_path,
                    input_path=input_path,
                    type="directory" if is_dir else "file",
                    size=0 if is_dir else stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                )
            )

        entries.sort(key=lambda entry: (0 if entry.type == "directory" else 1, entry.name.lower()))
        return entries
