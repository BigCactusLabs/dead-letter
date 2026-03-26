"""Persistent workflow settings for the local UI/backend app."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkflowSettings:
    inbox_path: Path
    cabinet_path: Path


def default_settings_path() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "dead-letter" / "settings.json"
    if sys.platform.startswith("win"):
        return home / "AppData" / "Roaming" / "dead-letter" / "settings.json"
    return home / ".config" / "dead-letter" / "settings.json"


def validate_workflow_paths(*, inbox_path: str | Path, cabinet_path: str | Path) -> WorkflowSettings:
    inbox = Path(inbox_path).expanduser().resolve()
    cabinet = Path(cabinet_path).expanduser().resolve()

    if inbox == cabinet or cabinet.is_relative_to(inbox):
        raise ValueError("Cabinet must be separate from Inbox and cannot be nested inside it.")
    if inbox.is_relative_to(cabinet):
        raise ValueError("Inbox cannot be nested inside Cabinet.")

    return WorkflowSettings(inbox_path=inbox, cabinet_path=cabinet)


class SettingsStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path or default_settings_path()).expanduser().resolve()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> WorkflowSettings | None:
        if not self._path.exists():
            return None

        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

        if not isinstance(payload, dict):
            return None

        try:
            settings = validate_workflow_paths(
                inbox_path=payload["inbox_path"],
                cabinet_path=payload["cabinet_path"],
            )
        except (KeyError, TypeError, ValueError):
            return None
        if settings.inbox_path.exists() and not settings.inbox_path.is_dir():
            return None
        if settings.cabinet_path.exists() and not settings.cabinet_path.is_dir():
            return None
        return settings

    def save(self, *, inbox_path: str | Path, cabinet_path: str | Path) -> WorkflowSettings:
        settings = validate_workflow_paths(inbox_path=inbox_path, cabinet_path=cabinet_path)
        settings.inbox_path.mkdir(parents=True, exist_ok=True)
        settings.cabinet_path.mkdir(parents=True, exist_ok=True)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "inbox_path": str(settings.inbox_path),
                    "cabinet_path": str(settings.cabinet_path),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return settings
