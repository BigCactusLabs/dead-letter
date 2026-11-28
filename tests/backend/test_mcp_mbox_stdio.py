"""Real-stdio MBOX rejection, recovery and cancellation evidence (#145)."""

from __future__ import annotations

import json
import os
import sys
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.backend.test_mcp_mbox import digest, write_mbox
from tests.backend.test_mcp_mbox_privacy import SENTINEL, write_archive


NON_ROOT_POSIX = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX non-root permissions",
)


def stdio_server():
    return StdioServerParameters(
        command=sys.executable,
        args=["-c", "from dead_letter.backend.mcp_server import main; main()"],
    )


class RecordingWrites(AbstractAsyncContextManager):
    """Observe client wire messages without changing their delivery."""

    def __init__(self, stream, messages):
        self.stream = stream
        self.messages = messages

    async def send(self, item):
        await self.stream.send(item)
        self.messages.append(item.message.model_dump(by_alias=True, exclude_none=True))

    async def aclose(self):
        await self.stream.aclose()

    async def __aexit__(self, *args):
        await self.aclose()


@asynccontextmanager
async def recording_stdio(messages):
    async with stdio_client(stdio_server()) as (read, write):
        yield read, RecordingWrites(write, messages)


async def assert_recovery(client, source, output):
    result = await client.call_tool("convert_mbox", {
        "path": str(source), "output_directory": str(output),
    })
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["converted"] == 2
    report = json.loads(Path(payload["report_path"]).read_text())
    assert report["summary"] == {"total": 2, "written": 2, "skipped": 0, "errors": 0}


@pytest.mark.parametrize("case, expected", [
    ("missing", "File not found"),
    ("directory", "MBOX path is not a regular file"),
    ("suffix", "MCP convert_mbox accepts only a flat .mbox file; extract compressed archives first."),
    pytest.param("unreadable", "File not readable", marks=NON_ROOT_POSIX),
    pytest.param(
        "unwritable", "MBOX report could not be written after 2 messages: mbox_io_error (EACCES: Permission denied)",
        marks=NON_ROOT_POSIX,
    ),
])
@pytest.mark.anyio
async def test_real_stdio_rejection_privacy_and_session_recovery(tmp_path, case, expected):
    source = write_archive(tmp_path)
    before = source.read_bytes()
    bad = tmp_path / "bad.mbox"
    out = tmp_path / "rejected-out"
    if case == "directory":
        bad.mkdir()
    elif case == "suffix":
        bad = tmp_path / "bad.zip"
        bad.write_bytes(before)
    elif case == "unreadable":
        bad.write_bytes(before)
        bad.chmod(0)
    elif case == "unwritable":
        bad = source
        out.mkdir()
        out.chmod(0o500)

    try:
        async with Client(stdio_server()) as client:
            result = await client.call_tool("convert_mbox", {
                "path": str(bad), "output_directory": str(out),
            })
            assert result.is_error is True
            text = result.content[0].text
            assert SENTINEL not in text
            assert "Synthetic" not in text
            # Only caller-provided input paths may appear in validation errors.
            # Output failures expose neither the output path nor exception paths.
            if case in {"missing", "directory", "unreadable"}:
                expected += f": {bad}"
            assert text == f"Error executing tool convert_mbox: {expected}"
            if case == "unwritable":
                # Observed behavior: per-message writes fail, then report
                # publication fails; this is NOT a pre-conversion rejection.
                assert list(out.iterdir()) == []
            else:
                assert not out.exists()
            await assert_recovery(client, source, tmp_path / "recovered")
    finally:
        if case == "unreadable":
            bad.chmod(0o600)
        elif case == "unwritable":
            out.chmod(0o700)
    assert source.read_bytes() == before
    if case == "unreadable":
        assert bad.read_bytes() == before


@pytest.mark.anyio
async def test_real_stdio_cancelled_call_finishes_with_complete_report(tmp_path):
    source = tmp_path / "mail.mbox"
    count = 1000
    write_mbox(source, count)
    before = digest(source)
    out = tmp_path / "out"
    finished = anyio.Event()
    scopes = []
    returned = []
    sent = []

    async with Client(recording_stdio(sent)) as client:
        # Resolve the schema before starting the call we intend to cancel.
        await client.list_tools()

        async def convert():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                returned.append(await client.call_tool("convert_mbox", {
                    "path": str(source), "output_directory": str(out),
                }))
            finished.set()

        with anyio.fail_after(30):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(convert)
                # A real output proves conversion started. Wait only on the
                # client; there are no injected server delays or monkeypatches.
                while not list(out.glob("*.md")):
                    assert not finished.is_set(), "conversion finished before cancellation"
                    await anyio.sleep(0.001)
                assert len(list(out.glob("*.md"))) < count
                assert not list(out.glob(".dead-letter-report*"))
                # MCP 2.3 sends notifications/cancelled with this request's ID
                # and reason='caller cancelled' when its awaiting scope cancels.
                scopes[0].cancel()
                await finished.wait()
                assert scopes[0].cancelled_caught
                assert returned == []
                await assert_recovery(client, _recovery_archive(tmp_path), tmp_path / "recovered")
                # The cancelled conversion keeps running alongside recovery.
                # Closing the session stops the server, so wait while it is open.
                while not list(out.glob(".dead-letter-report*")):
                    await anyio.sleep(0.01)

    calls = [item for item in sent if item["method"] == "tools/call"]
    cancellations = [item for item in sent if item["method"] == "notifications/cancelled"]
    assert len(cancellations) == 1
    assert cancellations[0]["params"] == {
        "requestId": calls[0]["id"], "reason": "caller cancelled",
    }
    reports = list(out.glob(".dead-letter-report*"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["job"]["status"] == "succeeded"
    assert report["summary"] == {"total": count, "written": count, "skipped": 0, "errors": 0}
    assert len(report["results"]) == count
    assert report["mbox_options"]["truncated"] is False
    assert len(list(out.glob("*.md"))) == count
    assert digest(source) == before


def _recovery_archive(tmp_path):
    source = tmp_path / "recovery.mbox"
    write_mbox(source, 2)
    return source
