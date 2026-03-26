from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from dead_letter.backend.doctor import (
    CheckResult,
    check_cabinet_path,
    check_cli_extras,
    check_core_dependencies,
    check_inbox_path,
    check_python_version,
    check_ui_extras,
    run_doctor,
)


def test_check_python_version_ok() -> None:
    result = check_python_version()
    assert result.status == "ok"
    assert ">= 3.12 required" in result.message


def test_check_core_dependencies_ok() -> None:
    result = check_core_dependencies()
    assert result.status == "ok"
    assert "importable" in result.message


def test_check_core_dependencies_missing(monkeypatch) -> None:
    original_import = importlib.import_module

    def fail_nh3(name):
        if name == "nh3":
            raise ImportError("no module named nh3")
        return original_import(name)

    monkeypatch.setattr(importlib, "import_module", fail_nh3)
    result = check_core_dependencies()
    assert result.status == "err"
    assert "nh3" in result.message


def test_check_inbox_path_ok(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    result = check_inbox_path(inbox)
    assert result.status == "ok"
    assert "readable" in result.message


def test_check_inbox_path_not_configured() -> None:
    result = check_inbox_path(None)
    assert result.status == "skip"
    assert "not configured" in result.message


def test_check_inbox_path_not_readable(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = check_inbox_path(missing)
    assert result.status == "err"
    assert result.fix is not None


def test_check_cabinet_path_ok(tmp_path: Path) -> None:
    cabinet = tmp_path / "cabinet"
    cabinet.mkdir()
    result = check_cabinet_path(cabinet)
    assert result.status == "ok"
    assert "writable" in result.message


def test_check_cabinet_path_not_writable(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = check_cabinet_path(missing)
    assert result.status == "err"
    assert result.fix is not None


def test_run_doctor_exit_zero() -> None:
    rc = run_doctor()
    assert rc == 0


def test_run_doctor_json_output(capsys) -> None:
    rc = run_doctor(json_output=True)
    assert rc == 0
    output = capsys.readouterr().out
    data = json.loads(output)
    assert "checks" in data
    assert "version" in data
    assert "python" in data
    assert "platform" in data
    assert all(c["status"] in ("ok", "err", "skip") for c in data["checks"])
