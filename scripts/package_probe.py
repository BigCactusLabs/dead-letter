"""Run with the isolated installed interpreter, never with the source environment."""
from __future__ import annotations

import argparse
import asyncio
import builtins
from contextlib import redirect_stderr, redirect_stdout
import importlib
import importlib.metadata
import importlib.resources
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch

BODY = "Packaged installation preserves this synthetic message."
TOOLS = {"convert_eml", "convert_eml_to_bundle", "convert_directory", "get_diagnostics"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def executable(name: str) -> str:
    return str(Path(sys.executable).with_name(name + (".exe" if sys.platform == "win32" else "")))


def offline_preview_probe(fixture: Path) -> None:
    """Prove a present key or installed extra cannot trigger provider access."""
    original_import = builtins.__import__
    original_import_module = importlib.import_module
    original_get = type(os.environ).get
    original_getitem = type(os.environ).__getitem__

    def guarded_import(name, *args, **kwargs):
        require(not name.startswith("typesafe_sdk"), "preview imported the TypeSafe SDK")
        return original_import(name, *args, **kwargs)

    def guarded_import_module(name, *args, **kwargs):
        require(not name.startswith("typesafe_sdk"), "preview imported the TypeSafe SDK")
        return original_import_module(name, *args, **kwargs)

    def guarded_get(environ, key, *args):
        require(key != "TYPESAFE_API_KEY", "preview read the TypeSafe API key")
        return original_get(environ, key, *args)

    def guarded_getitem(environ, key):
        require(key != "TYPESAFE_API_KEY", "preview read the TypeSafe API key")
        return original_getitem(environ, key)

    def forbidden_network(*args, **kwargs):
        raise RuntimeError("preview attempted network access")

    os.environ["TYPESAFE_API_KEY"] = "SYNTHETIC_PACKAGE_PROBE_KEY"
    try:
        with (patch.object(builtins, "__import__", guarded_import),
              patch.object(importlib, "import_module", guarded_import_module),
              patch.object(type(os.environ), "get", guarded_get),
              patch.object(type(os.environ), "__getitem__", guarded_getitem),
              patch.object(socket, "socket", forbidden_network),
              patch.object(socket, "create_connection", forbidden_network)):
            from dead_letter.analysis import prepare_eml
            preview = prepare_eml(fixture).preview()
        require(preview["execution_status"] == "skipped", "preview did not skip execution")
        require(preview["remote_enabled"] is False, "preview enabled remote access")
        require("typesafe_sdk" not in sys.modules, "preview loaded the TypeSafe SDK")
    finally:
        os.environ.pop("TYPESAFE_API_KEY", None)


def missing_typesafe_probe(fixture: Path) -> None:
    """Exercise installed CLI preflight with a real absent SDK and a synthetic key."""
    from dead_letter.analysis import service
    from dead_letter.backend import analysis_cli

    original_import = builtins.__import__
    original_import_module = importlib.import_module

    def guarded_import(name, *args, **kwargs):
        require(not name.startswith("typesafe_sdk"), "missing-SDK preflight imported the SDK")
        return original_import(name, *args, **kwargs)

    def guarded_import_module(name, *args, **kwargs):
        require(not name.startswith("typesafe_sdk"), "missing-SDK preflight imported the SDK")
        return original_import_module(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise RuntimeError("missing-SDK preflight read source or attempted network access")

    stdout, stderr = io.StringIO(), io.StringIO()
    with (patch.dict(os.environ, {"TYPESAFE_API_KEY": "SYNTHETIC_PACKAGE_PROBE_KEY"}),
          patch.object(builtins, "__import__", guarded_import),
          patch.object(importlib, "import_module", guarded_import_module),
          patch.object(service, "prepare_eml", forbidden),
          patch.object(socket.socket, "connect", forbidden),
          patch.object(socket.socket, "connect_ex", forbidden),
          patch.object(socket.socket, "sendto", forbidden),
          patch.object(socket, "create_connection", forbidden),
          patch.object(socket, "getaddrinfo", forbidden),
          redirect_stdout(stdout), redirect_stderr(stderr)):
        code = analysis_cli.main([str(fixture), "--provider", "typesafe"])
    require(code == 1, "missing SDK did not fail closed")
    require(not stdout.getvalue(), "missing SDK produced an analysis result")
    require(json.loads(stderr.getvalue()) == {
        "execution_status": "failed", "stage": "analysis",
        "error_code": "typesafe_sdk_not_installed",
    }, "missing SDK returned the wrong error")
    require("typesafe_sdk" not in sys.modules, "missing-SDK preflight loaded the SDK")


def typesafe_install_probe() -> None:
    require(importlib.metadata.version("typesafe-sdk") == "0.7.0", "wrong TypeSafe SDK version")
    import typesafe_sdk
    import httpx2
    for module in (typesafe_sdk, httpx2):
        location = Path(module.__file__).resolve()
        require(location.is_relative_to(Path(sys.prefix).resolve()), f"not importing the venv installation: {location}")


def cli_probe(fixture: Path) -> None:
    help_result = subprocess.run([executable("dead-letter"), "--help"], capture_output=True, text=True, timeout=30, check=True)
    require("convert" in help_result.stdout, "CLI help is missing convert")
    # Default output naming may use the message subject. Choose the destination
    # explicitly rather than assuming the source stem is the output contract.
    output = fixture.with_suffix(".md")
    require(not output.exists(), "CLI probe destination must be fresh")
    subprocess.run([executable("dead-letter"), "convert", str(fixture), "--output", str(output)], check=True, timeout=60)
    require(BODY in output.read_text(encoding="utf-8"), "CLI conversion lost the body")


async def mcp_probe(fixture: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        executable("dead-letter-mcp"), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=None,
    )
    try:
        async def send(message: dict) -> None:
            assert process.stdin is not None
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()

        async def request(identifier: int, method: str, params: dict) -> dict:
            await send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
            assert process.stdout is not None
            for _ in range(30):
                raw = await process.stdout.readline()
                require(bool(raw), "MCP closed stdout before replying")
                message = json.loads(raw)
                require(message.get("jsonrpc") == "2.0", "non-JSON-RPC stdout")
                if message.get("id") == identifier:
                    require("error" not in message, f"MCP {method} failed")
                    return message["result"]
            raise RuntimeError("MCP response exceeded notification budget")

        async with asyncio.timeout(90):
            initialized = await request(1, "initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "dead-letter-package-smoke", "version": "1"},
            })
            require(bool(initialized.get("protocolVersion")), "missing MCP protocol version")
            await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            listing = await request(2, "tools/list", {})
            require({tool["name"] for tool in listing["tools"]} == TOOLS, "unexpected MCP tools")
            result = await request(3, "tools/call", {"name": "convert_eml", "arguments": {"eml_path": str(fixture)}})
            require(not result.get("isError"), "MCP conversion failed")
            text = "".join(block.get("text", "") for block in result.get("content", []))
            require(BODY in text, "MCP conversion lost the message body")
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    parser.add_argument("--extra", choices=("core", "cli", "mcp", "ui", "benchmark", "typesafe"))
    parser.add_argument("--artifact-url")
    parser.add_argument("--check-typesafe-only", action="store_true")
    args = parser.parse_args()
    if args.check_typesafe_only:
        typesafe_install_probe()
        return 0
    if not all((args.version, args.extra, args.artifact_url)):
        parser.error("--version, --extra and --artifact-url are required for an artifact probe")
    import dead_letter

    location = Path(dead_letter.__file__).resolve()
    require(location.is_relative_to(Path(sys.prefix).resolve()), f"not importing the venv installation: {location}")
    require(importlib.metadata.version("dead-letter") == args.version, "wrong installed version")
    distribution = importlib.metadata.distribution("dead-letter")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    require(direct.get("url") == args.artifact_url, "installation did not use the exact local artifact")
    require(not direct.get("dir_info", {}).get("editable"), "editable installation is not a packaging test")
    for point in ("dead-letter", "dead-letter-mcp", "dead-letter-ui"):
        require(any(ep.name == point for ep in distribution.entry_points), f"missing entry point: {point}")

    fixture = Path.cwd() / "synthetic.eml"
    content = ("From: sender@example.invalid\nTo: recipient@example.invalid\n"
               "Subject: Packaged smoke test\nMIME-Version: 1.0\n"
               "Content-Type: text/plain; charset=utf-8\n\n" + BODY + "\n").encode()
    fixture.write_bytes(content)
    cli_probe(fixture)

    if args.extra in {"core", "typesafe"}:
        offline_preview_probe(fixture)

    if args.extra == "core":
        for module in ("watchfiles", "mcp", "fastapi", "tiktoken", "typesafe_sdk", "httpx2"):
            require(importlib.util.find_spec(module) is None, f"core unexpectedly includes {module}")
        missing_typesafe_probe(fixture)
    elif args.extra == "cli":
        import watchfiles
        require(callable(watchfiles.watch), "watchfiles surface missing")
    elif args.extra == "mcp":
        asyncio.run(mcp_probe(fixture))
    elif args.extra == "ui":
        import dead_letter.backend.ui_server
        assets = importlib.resources.files("dead_letter").joinpath("frontend")
        require(assets.joinpath("static", "app.js").is_file(), "wheel is missing frontend/static/app.js")
        require(assets.joinpath("index.html").is_file(), "wheel is missing frontend/index.html")
        subprocess.run([executable("dead-letter-ui"), "--help"], check=True, timeout=30)
    elif args.extra == "benchmark":
        import tiktoken
        # No downloaded tokenizer data or network access is needed for the probe.
        encoding = tiktoken.Encoding(name="package-smoke", pat_str=r".", mergeable_ranks={bytes([i]): i for i in range(256)}, special_tokens={})
        require(len(encoding.encode("smoke")) == 5, "tokenizer surface failed")
    elif args.extra == "typesafe":
        typesafe_install_probe()
    require(fixture.read_bytes() == content, "packaged conversion modified its source")
    print(json.dumps({"extra": args.extra, "version": args.version, "import_path": str(location), "artifact_url": args.artifact_url}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
