from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import textwrap


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_SCRIPT = REPO_ROOT / "scripts" / "launch-dead-letter-ui.sh"
LAUNCHER_SCRIPT = REPO_ROOT / "Launch Dead Letter.command"


def _write_stub(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\nset -eu\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _run_helper(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:/usr/bin:/bin:/usr/sbin:/sbin"
    return subprocess.run(
        ["bash", str(HELPER_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_launcher_scripts_exist_and_are_executable() -> None:
    assert LAUNCHER_SCRIPT.exists()
    assert HELPER_SCRIPT.exists()
    assert os.access(LAUNCHER_SCRIPT, os.X_OK)
    assert os.access(HELPER_SCRIPT, os.X_OK)


def test_helper_exits_with_guidance_when_uv_is_missing(tmp_path: Path) -> None:
    _write_stub(tmp_path, "curl", "exit 7\n")

    result = _run_helper(tmp_path)

    assert result.returncode != 0
    assert "uv" in result.stdout.lower()
    assert "install" in result.stdout.lower()


def test_helper_reuses_existing_dead_letter_server_without_running_uv(tmp_path: Path) -> None:
    log_path = tmp_path / "calls.log"
    _write_stub(
        tmp_path,
        "curl",
        textwrap.dedent(
            f"""
            printf '%s\\n' '<!doctype html><title>dead-letter</title>'
            """
        ),
    )
    _write_stub(
        tmp_path,
        "open",
        textwrap.dedent(
            f"""
            printf 'open:%s\\n' "$1" >> "{log_path}"
            """
        ),
    )
    _write_stub(
        tmp_path,
        "uv",
        textwrap.dedent(
            f"""
            printf 'uv:%s\\n' "$*" >> "{log_path}"
            exit 99
            """
        ),
    )

    result = _run_helper(tmp_path)

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "open:http://127.0.0.1:8765"
    ]


def test_helper_refuses_non_dead_letter_port_conflict(tmp_path: Path) -> None:
    log_path = tmp_path / "calls.log"
    _write_stub(
        tmp_path,
        "curl",
        textwrap.dedent(
            """
            printf '%s\\n' '<!doctype html><title>Other App</title>'
            """
        ),
    )
    _write_stub(
        tmp_path,
        "open",
        textwrap.dedent(
            f"""
            printf 'open:%s\\n' "$1" >> "{log_path}"
            """
        ),
    )
    _write_stub(
        tmp_path,
        "uv",
        textwrap.dedent(
            f"""
            printf 'uv:%s\\n' "$*" >> "{log_path}"
            exit 0
            """
        ),
    )

    result = _run_helper(tmp_path)

    assert result.returncode != 0
    assert "8765" in result.stdout
    assert "another service" in result.stdout.lower()
    assert not log_path.exists()


def test_helper_reports_manual_command_when_server_exits_before_ready(tmp_path: Path) -> None:
    _write_stub(tmp_path, "curl", "exit 7\n")
    _write_stub(
        tmp_path,
        "uv",
        textwrap.dedent(
            """
            if [ "$1" = "sync" ] && [ "$2" = "--check" ]; then
              exit 0
            fi

            if [ "$1" = "run" ]; then
              exit 12
            fi

            exit 1
            """
        ),
    )

    result = _run_helper(tmp_path)

    assert result.returncode == 12
    assert "exited before the ui became ready" in result.stdout.lower()
    assert "manual launch command" in result.stdout.lower()
