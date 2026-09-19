"""Check generated client launchers against public PyPI using synthetic mail.

This is a subprocess/stdio test, not an assertion that a desktop client's GUI
or Cline's autonomous README installer has been exercised. No user config or
mail is read. The first launch downloads the public package into a temp cache.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from generate_client_installs import ROOT, public_launcher
from smoke_mcpb import EXPECTED_TOOLS, SmokeFailure, StdioClient, result_text

MESSAGE = (
    "From: sender@example.invalid\nTo: recipient@example.invalid\n"
    "Subject: Client installation smoke\nMIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n\n"
    "Synthetic mail confirms the published client launcher works.\n"
)
MARKER = "Synthetic mail confirms the published client launcher works."


def command_from_examples(root: Path = ROOT) -> list[str]:
    server = json.loads((root / "server.json").read_text(encoding="utf-8"))
    expected = public_launcher(server)
    for client, wrapper in (("vscode", "servers"), ("cursor", "mcpServers"), ("cline", "mcpServers")):
        config = json.loads((root / "examples" / "mcp" / f"{client}.json").read_text(encoding="utf-8"))
        entry = config[wrapper]["dead-letter"]
        if {key: entry[key] for key in ("command", "args")} != expected:
            raise SmokeFailure(f"{client} launcher differs from the canonical public command")
        if entry.get("env") or entry.get("url") or entry.get("autoApprove"):
            raise SmokeFailure(f"{client} example must not add credentials, remote transport, or auto-approval")
    return [expected["command"], *expected["args"]]


def stop(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None:
        with contextlib.suppress(OSError, ValueError):
            process.stdin.close()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.stdout is not None:
        process.stdout.close()


def check(command: list[str] | None = None, *, environment: dict[str, str] | None = None) -> None:
    # Registration harnesses can test the exact transport persisted by a client.
    # With no overrides, keep exercising the generated examples as before.
    if command is None:
        command = command_from_examples()
    with tempfile.TemporaryDirectory(prefix="dead-letter-client-smoke-") as temporary:
        directory = Path(temporary)
        fixture = directory / "message.eml"
        fixture.write_text(MESSAGE, encoding="utf-8")
        env = dict(os.environ if environment is None else environment)
        env.pop("PYTHONPATH", None)
        env["UV_NO_CONFIG"] = "1"
        env["UV_CACHE_DIR"] = str(directory / "uv-cache")
        # This is deliberately not `uv run` in the source checkout. The exact
        # generated public client command must install the published package.
        process = subprocess.Popen(command, cwd=directory, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=None, text=True, encoding="utf-8")
        try:
            client = StdioClient(process)
            initialized = client.request("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "dead-letter-client-smoke", "version": "1"},
            })
            if not initialized.get("protocolVersion") or not initialized.get("serverInfo"):
                raise SmokeFailure("initialize returned no protocol/server information")
            client.notify("notifications/initialized")
            tools = client.request("tools/list").get("tools", [])
            if len(tools) != len(EXPECTED_TOOLS) or {t["name"] for t in tools} != EXPECTED_TOOLS:
                raise SmokeFailure("published launcher did not expose the four expected tools")
            result = client.request("tools/call", {
                "name": "convert_eml", "arguments": {"eml_path": str(fixture)},
            })
            text = result_text(result)
            if result.get("isError") or not text.lstrip().startswith("---") or MARKER not in text:
                raise SmokeFailure("published launcher failed the synthetic conversion")
            if fixture.read_text(encoding="utf-8") != MESSAGE:
                raise SmokeFailure("synthetic source mail changed")
        finally:
            stop(process)


def main() -> int:
    try:
        check()
    except (SmokeFailure, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: generated client command, public PyPI stdio handshake, four tools, synthetic conversion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
