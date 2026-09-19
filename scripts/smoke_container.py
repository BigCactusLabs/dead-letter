"""Exercise a built container over real stdio MCP, without an MCP SDK dependency.

Checks every tool with synthetic mail under a read-only input mount, verifies
output persistence, rejects destructive requests, and uses --network=none.
Optionally compares complete advertised tool schemas against the released
PyPI package. This script never reads user mail.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

EXPECTED_TOOLS = {"convert_eml", "convert_eml_to_bundle", "convert_directory", "get_diagnostics"}
SERVER_NAME = "io.github.BigCactusLabs/dead-letter"
MESSAGE = (
    "From: sender@example.invalid\nTo: recipient@example.invalid\n"
    "Subject: Container smoke fixture\nDate: Thu, 17 Sep 2026 12:00:00 +0000\n"
    "Message-ID: <container-smoke@example.invalid>\nMIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n\n"
    "The container smoke fixture was converted locally.\n"
)
MARKER = "The container smoke fixture was converted locally."


class SmokeFailure(RuntimeError):
    """The executable image did not satisfy the distribution contract."""


class StdioClient:
    def __init__(self, process: subprocess.Popen[str], timeout: float = 60) -> None:
        self.process = process
        self.timeout = timeout
        self.next_id = 0
        self.lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        except (OSError, ValueError):
            pass  # The subprocess may be closed while the reader is unwinding.
        finally:
            self.lines.put(None)

    def send(self, message: dict) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise SmokeFailure("server closed stdin before the request") from error

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        request_id = self.next_id
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SmokeFailure(f"{method} exceeded its {self.timeout:g}s deadline")
            try:
                raw = self.lines.get(timeout=remaining)
            except queue.Empty:
                raise SmokeFailure(f"{method} exceeded its {self.timeout:g}s deadline") from None
            if raw is None:
                raise SmokeFailure(f"server closed stdout during {method}")
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as error:
                # Logging on stdout violates stdio MCP; do not hide it.
                raise SmokeFailure("server emitted non-JSON data on stdout") from error
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise SmokeFailure("server emitted an invalid JSON-RPC envelope")
            if "id" not in message and "method" in message:
                continue  # A notification cannot extend the request deadline.
            if message.get("id") != request_id or "method" in message:
                raise SmokeFailure(f"unexpected JSON-RPC message during {method}")
            if "error" in message:
                raise SmokeFailure(f"{method} returned a JSON-RPC error: {message['error']!r}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise SmokeFailure(f"{method} returned no result object")
            return result

    def initialize(self) -> dict[str, dict]:
        result = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "dead-letter-container-smoke", "version": "1"},
        })
        if not result.get("protocolVersion") or "serverInfo" not in result:
            raise SmokeFailure("initialize did not return protocol/server metadata")
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: dict[str, dict] = {}
        cursor = None
        seen = set()
        for _ in range(20):
            page = self.request("tools/list", {"cursor": cursor} if cursor else {})
            entries = page.get("tools")
            if not isinstance(entries, list):
                raise SmokeFailure("tools/list returned no tools array")
            for tool in entries:
                if not isinstance(tool, dict):
                    raise SmokeFailure("tools/list returned a non-object tool")
                name = tool.get("name")
                if not isinstance(name, str) or name in tools:
                    raise SmokeFailure("tools/list returned an invalid or duplicate tool")
                if not isinstance(tool.get("inputSchema"), dict):
                    raise SmokeFailure(f"{name} has no input schema")
                tools[name] = tool
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise SmokeFailure("tools/list repeated or invalid pagination cursor")
            seen.add(cursor)
        else:
            raise SmokeFailure("tools/list exceeded the page limit")
        if set(tools) != EXPECTED_TOOLS:
            raise SmokeFailure(f"unexpected tools: {sorted(tools)}")
        return tools

    def call(self, name: str, arguments: dict, *, error: bool = False) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if bool(result.get("isError")) != error:
            raise SmokeFailure(f"{name}: expected isError={error}, got {result!r}")
        return result


def result_text(result: dict) -> str:
    return "".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")


@contextlib.contextmanager
def session(command: list[str], timeout: float):
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=None, text=True, encoding="utf-8",
    )
    try:
        yield StdioClient(process, timeout)
    finally:
        if process.stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def mount(source: Path, target: str, *, readonly: bool = False) -> str:
    # --mount parses CSV; quoting a path with commas as one argv item alone
    # is insufficient. csv.writer quotes the entire source= field correctly.
    stream = io.StringIO()
    fields = ["type=bind", f"source={source}", f"target={target}"]
    if readonly:
        fields.append("readonly")
    csv.writer(stream, lineterminator="").writerow(fields)
    return stream.getvalue()


def docker_command(image: str, platform: str, name: str, source: Path, output: Path) -> list[str]:
    if not hasattr(os, "getuid") or os.getuid() == 0 or os.getgid() == 0:
        raise SmokeFailure("run the bind-mount smoke test as an unprivileged POSIX user")
    return [
        "docker", "run", "--rm", "-i", "--name", name, "--platform", platform,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "128",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        # Match host ownership without granting root or making user data world-writable.
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--mount", mount(source, "/input", readonly=True),
        "--mount", mount(output, "/output"), image,
    ]


def check_config(image: str, platform: str, version: str, revision: str) -> None:
    config = json.loads(subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{json .Config}}"],
        text=True, timeout=30,
    ))
    if config.get("User") != "10001:10001" or config.get("Entrypoint") != ["dead-letter-mcp"]:
        raise SmokeFailure("image must default to non-root stdio MCP")
    if config.get("ExposedPorts"):
        raise SmokeFailure("stdio image must not expose ports")
    expected = {
        "io.modelcontextprotocol.server.name": SERVER_NAME,
        "org.opencontainers.image.source": "https://github.com/BigCactusLabs/dead-letter",
        "org.opencontainers.image.version": version,
        "org.opencontainers.image.revision": revision,
        "org.opencontainers.image.licenses": "PolyForm-Noncommercial-1.0.0",
    }
    labels = config.get("Labels") or {}
    if any(labels.get(key) != value for key, value in expected.items()):
        raise SmokeFailure("image OCI labels do not match the tested source/version")
    # Verify actual default execution, not just declarative image metadata.
    subprocess.run([
        "docker", "run", "--rm", "--platform", platform, "--network", "none",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--entrypoint", "python", image, "-c",
        "import os; assert os.getuid() == 10001 and os.getgid() == 10001",
    ], check=True, timeout=60)


def tool_contract(tools: dict[str, dict]) -> list[dict]:
    """Project the tool inventory onto the fields clients actually consume.

    The image freezes the MCP SDK through uv.lock while the PyPI reference runs
    an unpinned `mcp` 2.x, so an optional field a newer SDK adds must not abort
    a release after the irreversible upload. EXPECTED_TOOLS stays the hard gate.
    """
    return [
        {field: tool.get(field) for field in ("name", "description", "inputSchema")}
        for _, tool in sorted(tools.items())
    ]


def persisted_path(value: str, output: Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise SmokeFailure(f"invalid output path: {value!r}")
    try:
        relative = path.relative_to("/output")
    except ValueError as error:
        raise SmokeFailure(f"output is outside the selected mount: {value!r}") from error
    result = output / relative
    if not result.resolve().is_relative_to(output.resolve()):
        raise SmokeFailure(f"output symlink escapes the selected mount: {value!r}")
    if not result.is_file():
        raise SmokeFailure(f"output was not persisted: {value!r}")
    return result


def exercise(client: StdioClient, source: Path, output: Path) -> dict[str, dict]:
    tools = client.initialize()
    text = result_text(client.call("convert_eml", {"eml_path": "/input/message.eml"}))
    if not text.lstrip().startswith("---") or MARKER not in text:
        raise SmokeFailure("convert_eml did not return the expected Markdown")
    client.call("convert_eml", {"eml_path": "/input/message.eml", "output_path": "/output/message.md"})
    if MARKER not in persisted_path("/output/message.md", output).read_text(encoding="utf-8"):
        raise SmokeFailure("converted file has unexpected contents")
    diagnostics = json.loads(result_text(client.call("get_diagnostics", {"eml_path": "/input/message.eml"})))
    if diagnostics.get("state") not in {"normal", "degraded", "review_recommended"}:
        raise SmokeFailure("diagnostics have no recognized state")
    bundle = json.loads(result_text(client.call("convert_eml_to_bundle", {
        "eml_path": "/input/message.eml", "bundle_root": "/output/bundles",
    })))
    persisted_path(bundle["markdown_path"], output)
    batch = json.loads(result_text(client.call("convert_directory", {
        "directory": "/input", "output_directory": "/output/batch",
    })))
    if (batch.get("total"), batch.get("successes"), batch.get("failures")) != (1, 1, 0):
        raise SmokeFailure(f"directory conversion failed: {batch!r}")
    for path in batch["output_paths"]:
        persisted_path(path, output)
    for operation in ("move", "delete"):
        client.call("convert_eml_to_bundle", {
            "eml_path": "/input/message.eml", "bundle_root": "/output/rejected",
            "source_handling": operation,
        }, error=True)
    client.call("convert_eml", {"eml_path": "/not-mounted/private.eml"}, error=True)
    client.call("convert_eml", {
        "eml_path": "/input/message.eml", "output_path": "/input/forbidden.md",
    }, error=True)
    if (source / "message.eml").read_text(encoding="utf-8") != MESSAGE:
        raise SmokeFailure("source mail changed")
    if list(source.iterdir()) != [source / "message.eml"]:
        raise SmokeFailure("the input mount was modified")
    return tools


def check(image: str, platform: str, timeout: float, compare_pypi: str | None) -> None:
    name = f"dead-letter-smoke-{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="dead-letter-container-") as temporary:
        root = Path(temporary)
        source, output = root / "input", root / "output"
        source.mkdir(mode=0o700)
        output.mkdir(mode=0o700)
        (source / "message.eml").write_text(MESSAGE, encoding="utf-8")
        (source / "message.eml").chmod(0o444)
        try:
            with session(docker_command(image, platform, name, source, output), timeout) as client:
                tools = exercise(client, source, output)
            if compare_pypi:
                with session([
                    "uvx", "--python", "3.12", "--from",
                    f"dead-letter[mcp]=={compare_pypi}", "dead-letter-mcp",
                ], max(timeout, 120)) as reference:
                    if tool_contract(reference.initialize()) != tool_contract(tools):
                        raise SmokeFailure("container tool schemas differ from the released PyPI package")
                    text = result_text(reference.call("convert_eml", {"eml_path": str(source / "message.eml")}))
                    if MARKER not in text:
                        raise SmokeFailure("PyPI reference conversion failed")
        finally:
            # Closing/killing the Docker CLI alone can leave its server alive.
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["docker", "rm", "--force", name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--platform", choices=["linux/amd64", "linux/arm64"], required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--compare-pypi")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        check_config(args.image, args.platform, args.version, args.revision)
        check(args.image, args.platform, args.timeout, args.compare_pypi)
    except (SmokeFailure, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {args.image} ({args.platform}); four tools, offline mounts, destructive-operation rejection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
