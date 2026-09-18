"""Smoke-test a packed .mcpb bundle by speaking MCP over its stdio server.

Unpacks the archive to a temp directory, launches `uv run --directory <dir>
server/main.py`, then runs initialize / tools/list / tools/call and checks the
results. Prints PASS or FAIL and exits non-zero on failure.

Usage: uv run python scripts/smoke_mcpb.py dist/dead-letter-mcp-<version>.mcpb
"""

import argparse
import json
import queue
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "core" / "fixtures" / "forwarded.eml"
EXPECTED_TOOLS = {
    "convert_eml",
    "convert_eml_to_bundle",
    "convert_directory",
    "get_diagnostics",
}
# First launch resolves and downloads dependencies, so reads need a generous budget.
READ_TIMEOUT_SECONDS = 60.0


class SmokeFailure(Exception):
    """Raised when the bundle does not behave as expected."""


class StdioClient:
    """Minimal newline-delimited JSON-RPC client over a subprocess's stdio."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self._process = process
        self._next_id = 0
        # readline() has no portable timeout, so a reader thread feeds a queue instead.
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        while True:
            message = self._read()
            if message.get("id") == request_id:
                if "error" in message:
                    raise SmokeFailure(f"{method} returned error: {message['error']}")
                return message.get("result", {})

    def _send(self, message: dict) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _read(self) -> dict:
        while True:
            try:
                raw = self._lines.get(timeout=READ_TIMEOUT_SECONDS)
            except queue.Empty:
                raise SmokeFailure(
                    f"no reply from server within {READ_TIMEOUT_SECONDS:.0f}s"
                ) from None
            if raw is None:
                raise SmokeFailure("server closed stdout before replying")
            line = raw.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                # Servers may log to stdout; ignore anything that is not JSON-RPC.
                continue


def unpack(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)


def result_text(result: dict) -> str:
    return "".join(
        block.get("text", "")
        for block in result.get("content", [])
        if block.get("type") == "text"
    )


def check(bundle_dir: Path, fixture: Path) -> None:
    process = subprocess.Popen(
        ["uv", "run", "--directory", str(bundle_dir), "server/main.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
    )
    try:
        client = StdioClient(process)
        client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "dead-letter-mcpb-smoke", "version": "1"},
            },
        )
        client.notify("notifications/initialized")

        tools = {tool["name"] for tool in client.request("tools/list").get("tools", [])}
        if tools != EXPECTED_TOOLS:
            raise SmokeFailure(
                f"tools/list returned {sorted(tools)}, expected {sorted(EXPECTED_TOOLS)}"
            )

        result = client.request(
            "tools/call",
            {"name": "convert_eml", "arguments": {"eml_path": str(fixture)}},
        )
        if result.get("isError"):
            raise SmokeFailure(
                f"convert_eml reported an error: {result_text(result)!r}"
            )
        text = result_text(result)
        if not text.strip():
            raise SmokeFailure("convert_eml returned empty text")
        if "---" not in text:
            raise SmokeFailure("convert_eml output has no YAML front matter delimiter")
    finally:
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="path to the packed .mcpb bundle")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help=f"'.eml' file to convert (default: {DEFAULT_FIXTURE})",
    )
    args = parser.parse_args()

    archive = args.archive.resolve()
    fixture = args.fixture.resolve()
    if not archive.is_file():
        print(f"FAIL: no such bundle {archive}", file=sys.stderr)
        return 1
    if not fixture.is_file():
        print(f"FAIL: no such fixture {fixture}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="dead-letter-mcpb-smoke-") as temp:
        bundle_dir = Path(temp) / "bundle"
        unpack(archive, bundle_dir)
        try:
            check(bundle_dir, fixture)
        except SmokeFailure as error:
            print(f"FAIL: {error}", file=sys.stderr)
            return 1

    print(f"PASS: {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
