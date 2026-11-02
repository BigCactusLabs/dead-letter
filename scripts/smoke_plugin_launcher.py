"""Smoke-test the shipped plugin launcher with a read-only copy and empty uv cache.

Speaks initialize / tools/list over stdio, checks the exact package version,
then closes stdin and checks clean exit and temporary-environment cleanup.
Prints a JSON result. No Claude client, private mail, or app dependencies needed.

Usage: uv run python scripts/smoke_plugin_launcher.py [--plugin-dir plugin/]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import tomllib

from release import launcher_pin
from smoke_mcpb import PUBLISHED_TOOLS, SmokeFailure, StdioClient

ROOT = Path(__file__).resolve().parents[1]


def launcher_command(plugin_dir: Path) -> list[str]:
    """Expand Claude's plugin-root token without shell parsing or argument splitting."""
    server = json.loads((plugin_dir / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["dead-letter"]
    command = [server["command"], *server["args"]]
    if not command or any(not isinstance(value, str) or not value for value in command):
        raise SmokeFailure("plugin launcher command and arguments must be nonempty strings")
    return [value.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_dir)) for value in command]


def snapshot(root: Path) -> dict:
    return {
        str(path.relative_to(root)): {
            "mode": path.stat().st_mode,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        }
        for path in [root, *sorted(root.rglob("*"))]
    }


def check(plugin_dir: Path, cache: Path) -> dict:
    project = tomllib.loads((plugin_dir / "pyproject.toml").read_text(encoding="utf-8"))
    pin = launcher_pin(project)
    before = snapshot(plugin_dir)
    if (plugin_dir / ".venv").exists():
        raise SmokeFailure("plugin copy already contains a .venv")
    command = launcher_command(plugin_dir)
    environment = {**os.environ, "UV_CACHE_DIR": str(cache), "CLAUDE_PLUGIN_ROOT": str(plugin_dir)}
    process = subprocess.Popen(
        command, cwd=plugin_dir.parent, env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
        text=True, encoding="utf-8",
    )
    try:
        client = StdioClient(process)
        initialized = client.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "dead-letter-plugin-smoke", "version": "1"},
        })
        version = initialized.get("serverInfo", {}).get("version")
        if version != pin:
            raise SmokeFailure(f"serverInfo version {version!r} does not match plugin pin {pin}")
        client.notify("notifications/initialized")
        tools = {tool["name"] for tool in client.request("tools/list").get("tools", [])}
        if tools != PUBLISHED_TOOLS:
            raise SmokeFailure(f"tools/list returned {sorted(tools)}, expected {sorted(PUBLISHED_TOOLS)}")
        process.stdin.close()
        try:
            code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            raise SmokeFailure("launcher did not exit within 10s after stdin closed") from None
        if code:
            raise SmokeFailure(f"launcher exited with status {code}")
        if snapshot(plugin_dir) != before:
            raise SmokeFailure("launcher changed the read-only plugin copy")
        leftovers = list((cache / "builds-v0").iterdir()) if (cache / "builds-v0").exists() else []
        if leftovers:
            raise SmokeFailure(f"launcher left temporary environments: {[path.name for path in leftovers]}")
        return {"plugin_pin": pin, "server_version": version, "tools": sorted(tools),
                "exit_code": code, "plugin_unchanged": True, "temporary_environments": []}
    finally:
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-dir", type=Path, default=ROOT / "plugin")
    args = parser.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="dead-letter-plugin-smoke-") as temporary:
            root = Path(temporary)
            plugin = root / "plugin"
            shutil.copytree(args.plugin_dir, plugin)
            cache = root / "cache"
            cache.mkdir()
            try:
                for path in [*plugin.rglob("*"), plugin]:
                    path.chmod(path.stat().st_mode & ~0o222)
                result = check(plugin, cache)
            finally:
                # Restore only our disposable copy so TemporaryDirectory can remove it.
                for path in [plugin, *plugin.rglob("*")]:
                    path.chmod(path.stat().st_mode | stat.S_IWUSR)
    except (SmokeFailure, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"schema_version": 1, "status": "failed", "error": str(error)}))
        return 1
    print(json.dumps({"schema_version": 1, "status": "passed", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
