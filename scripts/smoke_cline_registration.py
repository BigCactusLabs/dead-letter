"""Test Cline CLI registration/removal in a disposable, credential-free profile.

This is not the Cline GUI, an autonomous README install, or a marketplace
submission. The --yes flag is used only inside this synthetic test profile.
Requires npx; downloads a pinned public CLI. Makes no model request.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLINE_VERSION = "3.0.62"


class RegistrationFailure(RuntimeError):
    pass


def isolated_environment(directory: Path, inherited: dict[str, str]) -> dict[str, str]:
    # Keep platform/runtime plumbing, not credentials, provider settings,
    # NODE_OPTIONS, npm hooks, custom registries, or the developer's profile.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL"}
    env = {key: value for key, value in inherited.items() if key.upper() in allowed}
    home = directory / "home"
    home.mkdir()
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
        "CLINE_DIR": str(home / ".cline"),
        "CLINE_MCP_SETTINGS_PATH": str(directory / "settings.json"),
        "NPM_CONFIG_USERCONFIG": str(directory / "empty.npmrc"),
        "NPM_CONFIG_GLOBALCONFIG": str(directory / "empty-global.npmrc"),
        "NPM_CONFIG_CACHE": str(directory / "npm-cache"),
        "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org", "NO_COLOR": "1",
    })
    (directory / "empty.npmrc").write_text("", encoding="utf-8")
    (directory / "empty-global.npmrc").write_text("", encoding="utf-8")
    return env


def validate_registration(before: dict, after: dict, launcher: dict) -> list[str]:
    if set(after) != set(before):
        raise RegistrationFailure("Cline changed top-level settings")
    if any(after[key] != value for key, value in before.items() if key != "mcpServers"):
        raise RegistrationFailure("Cline changed unrelated settings")
    servers = after.get("mcpServers")
    if not isinstance(servers, dict) or set(servers) != {*before["mcpServers"], "dead-letter"}:
        raise RegistrationFailure("Cline changed unrelated server inventory")
    if any(servers[name] != entry for name, entry in before["mcpServers"].items()):
        raise RegistrationFailure("Cline changed existing server configuration or approval settings")
    # Deliberately version-specific: fail on drift, do not silently accept a
    # second unreviewed settings format or a new automatic approval field.
    expected = {"transport": {"type": "stdio", **launcher}}
    if servers["dead-letter"] != expected:
        raise RegistrationFailure("registered Cline transport differs from the reviewed launcher")
    transport = servers["dead-letter"]["transport"]
    return [transport["command"], *transport["args"]]


def run(command: list[str], directory: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, encoding="utf-8", timeout=180)
    if completed.returncode:
        # Public CLI diagnostics only; env contains no auth/provider credentials.
        raise RegistrationFailure(f"Cline command failed ({completed.returncode}): {completed.stderr[-3000:]}")
    return completed.stdout.strip()


def check() -> dict:
    from generate_client_installs import public_launcher
    from smoke_client_install import check as check_launcher

    npx = shutil.which("npx")
    if not npx:
        raise RegistrationFailure("npx is required")
    launcher = public_launcher(json.loads((ROOT / "server.json").read_text(encoding="utf-8")))
    candidate = json.loads((ROOT / "docs/project/submissions/cline/entry.json").read_text(encoding="utf-8"))
    install_args = candidate["install"]["args"]
    if install_args != ["dead-letter", "--", launcher["command"], *launcher["args"]]:
        raise RegistrationFailure("Cline submission argv differs from the canonical launcher")
    with tempfile.TemporaryDirectory(prefix="dead-letter-cline-") as temporary:
        directory = Path(temporary)
        env = isolated_environment(directory, dict(os.environ))
        settings = Path(env["CLINE_MCP_SETTINGS_PATH"])
        before = {"mcpServers": {"preserve-me": {"transport": {"type": "stdio", "command": "do-not-execute-this-sentinel"}, "disabled": True, "autoApprove": []}}, "auditSentinel": "preserve"}
        settings.write_text(json.dumps(before), encoding="utf-8")
        cli = [npx, "--yes", f"--package=@cline/cli@{CLINE_VERSION}", "cline"]
        version = run([*cli, "--version"], directory, env)
        if not re.search(r"(?<![\d.])" + re.escape(CLINE_VERSION) + r"(?![\d.])", version):
            raise RegistrationFailure("unexpected published Cline CLI version")
        try:
            run([*cli, "mcp", "install", install_args[0], "--yes", *install_args[1:]], directory, env)
            saved = json.loads(settings.read_text(encoding="utf-8"))
            command = validate_registration(before, saved, launcher)
            # Execute the exact saved transport, not an independently guessed
            # launcher. This remains a protocol test, not a Cline agent session.
            check_launcher(command=command, environment=env)
        finally:
            if settings.exists() and "dead-letter" in json.loads(settings.read_text(encoding="utf-8")).get("mcpServers", {}):
                run([*cli, "mcp", "uninstall", "dead-letter"], directory, env)
        if json.loads(settings.read_text(encoding="utf-8")) != before:
            raise RegistrationFailure("uninstall failed to preserve original settings")
    return {"client": "@cline/cli", "version": CLINE_VERSION, "registration": "passed", "saved_transport_smoke": "passed", "uninstall_preserves_settings": "passed", "scope": "disposable CLI profile; no GUI, model request, or marketplace submission"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        print(json.dumps(check(), indent=2))
    except (RegistrationFailure, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"FAIL: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
