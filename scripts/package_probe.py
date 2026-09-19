"""Run with the isolated installed interpreter, never with the source environment."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.resources
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

BODY = "Packaged installation preserves this synthetic message."
TOOLS = {"convert_eml", "convert_eml_to_bundle", "convert_directory", "get_diagnostics"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def executable(name: str) -> str:
    return str(Path(sys.executable).with_name(name + (".exe" if sys.platform == "win32" else "")))


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
    parser.add_argument("--version", required=True)
    parser.add_argument("--extra", choices=("core", "cli", "mcp", "ui", "benchmark"), required=True)
    parser.add_argument("--artifact-url", required=True)
    args = parser.parse_args()
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
    help_result = subprocess.run([executable("dead-letter"), "--help"], capture_output=True, text=True, timeout=30, check=True)
    require("convert" in help_result.stdout, "CLI help is missing convert")
    subprocess.run([executable("dead-letter"), "convert", str(fixture)], check=True, timeout=60)
    require(BODY in fixture.with_suffix(".md").read_text(encoding="utf-8"), "CLI conversion lost the body")

    if args.extra == "core":
        for module in ("watchfiles", "mcp", "fastapi", "tiktoken"):
            require(importlib.util.find_spec(module) is None, f"core unexpectedly includes {module}")
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
    require(fixture.read_bytes() == content, "packaged conversion modified its source")
    print(json.dumps({"extra": args.extra, "version": args.version, "import_path": str(location), "artifact_url": args.artifact_url}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
