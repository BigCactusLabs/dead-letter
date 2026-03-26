"""Health check runner for dead-letter doctor command."""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CheckStatus = Literal["ok", "err", "skip"]

CORE_IMPORTS = [
    "mailparser",
    "nh3",
    "html_to_markdown",
    "selectolax",
    "icalendar",
    "yaml",
    "mailparser_reply",
]

CLI_EXTRAS = ["watchfiles"]
UI_EXTRAS = ["fastapi", "uvicorn", "httpx"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    fix: str | None = None


def check_python_version() -> CheckResult:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 12):
        return CheckResult("python_version", "ok", f"Python {version} (>= 3.12 required)")
    return CheckResult(
        "python_version",
        "err",
        f"Python {version} — requires >= 3.12",
        fix="install Python 3.12 or later from https://python.org",
    )


def check_core_dependencies() -> CheckResult:
    missing = []
    for mod in CORE_IMPORTS:
        try:
            importlib.import_module(mod)
        except (ImportError, ModuleNotFoundError):
            missing.append(mod)
    if not missing:
        return CheckResult("core_dependencies", "ok", f"Core dependencies: all {len(CORE_IMPORTS)} importable")
    first = missing[0]
    return CheckResult(
        "core_dependencies",
        "err",
        f"Core dependency missing: {first}",
        fix="pip install dead-letter",
    )


def _check_extras(name: str, modules: list[str], install_hint: str) -> CheckResult:
    available = []
    missing = []
    for mod in modules:
        try:
            importlib.import_module(mod)
            available.append(mod)
        except (ImportError, ModuleNotFoundError):
            missing.append(mod)
    label = name.replace("_", " ").title()
    if available and not missing:
        return CheckResult(name, "ok", f"{label}: {', '.join(available)} available")
    if not available:
        return CheckResult(name, "skip", f"{label}: not installed")
    first = missing[0]
    return CheckResult(name, "err", f"{label}: {first} missing", fix=f"pip install {install_hint}")


def check_cli_extras() -> CheckResult:
    return _check_extras("cli_extras", CLI_EXTRAS, "dead-letter[cli]")


def check_ui_extras() -> CheckResult:
    return _check_extras("ui_extras", UI_EXTRAS, "dead-letter[ui]")


def check_inbox_path(path: Path | None) -> CheckResult:
    if path is None:
        return CheckResult("inbox_path", "skip", "Inbox path: not configured")
    if not path.exists():
        return CheckResult("inbox_path", "err", f"Inbox path: {path} (does not exist)", fix=f"mkdir -p {path}")
    if not path.is_dir():
        return CheckResult("inbox_path", "err", f"Inbox path: {path} (not a directory)", fix="provide a directory path")
    import os
    if not os.access(path, os.R_OK):
        return CheckResult("inbox_path", "err", f"Inbox path: {path} (not readable)", fix=f"check permissions with `ls -la {path}`")
    return CheckResult("inbox_path", "ok", f"Inbox path: {path} (readable)")


def check_cabinet_path(path: Path | None) -> CheckResult:
    if path is None:
        return CheckResult("cabinet_path", "skip", "Cabinet path: not configured")
    if not path.exists():
        return CheckResult("cabinet_path", "err", f"Cabinet path: {path} (does not exist)", fix=f"mkdir -p {path}")
    if not path.is_dir():
        return CheckResult("cabinet_path", "err", f"Cabinet path: {path} (not a directory)", fix="provide a directory path")
    import os
    if not os.access(path, os.W_OK):
        return CheckResult("cabinet_path", "err", f"Cabinet path: {path} (not writable)", fix=f"check permissions with `ls -la {path}`")
    return CheckResult("cabinet_path", "ok", f"Cabinet path: {path} (writable)")


def _load_settings_paths() -> tuple[Path | None, Path | None]:
    try:
        from dead_letter.backend.settings import SettingsStore
        settings = SettingsStore().load()
        if settings is None:
            return None, None
        return settings.inbox_path, settings.cabinet_path
    except Exception:
        return None, None


def run_doctor(*, json_output: bool = False) -> int:
    from dead_letter import __version__

    inbox_path, cabinet_path = _load_settings_paths()

    checks = [
        check_python_version(),
        check_core_dependencies(),
        check_cli_extras(),
        check_ui_extras(),
        check_inbox_path(inbox_path),
        check_cabinet_path(cabinet_path),
    ]

    if json_output:
        data = {
            "version": __version__,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": sys.platform,
            "checks": [
                {"name": c.name, "status": c.status, "message": c.message}
                | ({"fix": c.fix} if c.fix else {})
                for c in checks
            ],
        }
        print(json.dumps(data, indent=2))
    else:
        print(f"\ndead-letter doctor\n")
        for c in checks:
            tag = {"ok": "[ok]  ", "err": "[err] ", "skip": "[skip]"}[c.status]
            print(f"  {tag} {c.message}")
            if c.fix:
                print(f"         -> Fix: {c.fix}")
        print()

    errors = sum(1 for c in checks if c.status == "err")
    if not json_output:
        if errors:
            print(f"{errors} issue{'s' if errors != 1 else ''} found.")
        else:
            print("All checks passed.")
    return 1 if errors else 0
