"""Bounded MBOX ingestion over MCP (issue #145). Synthetic mail only."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from dead_letter.backend import mcp_server
from dead_letter.backend.mcp_server import convert_mbox

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"


def message(n: int) -> bytes:
    return (
        f"From: alice@example.test\nSubject: Synthetic {n}\n"
        f"Message-ID: <m{n}@example.test>\n\nBody number {n}\n\n"
    ).encode()


def write_mbox(path: Path, count: int, *, empty_at: int | None = None) -> bytes:
    parts = []
    for n in range(1, count + 1):
        parts.append(POSTMARK + (b"" if n == empty_at else message(n)))
    data = b"".join(parts)
    path.write_bytes(data)
    return data


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(**kwargs) -> dict:
    return json.loads(convert_mbox(**kwargs))


def test_mixed_success_writes_markdown_and_report_without_touching_source(tmp_path):
    source = tmp_path / "mail.mbox"
    write_mbox(source, 3, empty_at=2)
    before = digest(source)
    out = tmp_path / "out"

    result = run(path=str(source), output_directory=str(out))

    assert result["processed"] == 3
    assert result["converted"] == 2
    assert result["failed"] == 1
    assert result["skipped"] == 0
    assert result["truncated"] is False
    assert result["failures"] == [
        {"index": 2, "code": "mbox_empty_message",
         "message": "Stored message is empty or exceeds configured resource limits"},
    ]
    assert result["failures_omitted"] == 0
    assert "message" not in result
    assert result["output_directory"] == str(out.resolve())
    assert len(list(out.glob("0000000*.md"))) == 2
    # Never returns message content.
    assert "Body number" not in json.dumps(result)

    report_path = Path(result["report_path"])
    assert report_path == out.resolve() / ".dead-letter-report.json"
    report = json.loads(report_path.read_text())
    assert report["summary"] == {"total": 3, "written": 2, "skipped": 0, "errors": 1}
    assert report["job"]["status"] == "completed_with_errors"
    assert report["job"]["id"] == "mcp"
    assert report["mbox_options"]["unescape"] == "preserve"
    assert report["mbox_options"]["max_messages"] == mcp_server.MCP_MAX_MBOX_MESSAGES
    assert report["mbox_options"]["truncated"] is False
    assert [row["mbox"]["index"] for row in report["results"]] == [1, 2, 3]
    assert digest(source) == before


def test_bundles_write_one_directory_per_message(tmp_path):
    source = tmp_path / "mail.mbox"
    write_mbox(source, 2)
    out = tmp_path / "out"
    result = run(path=str(source), output_directory=str(out), bundles=True)
    assert result["converted"] == 2
    bundles = sorted(p for p in out.iterdir() if p.is_dir())
    assert len(bundles) == 2
    assert all((b / "message.md").is_file() and (b / "source.eml").is_file() for b in bundles)


def test_cap_stops_cleanly_and_marks_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_MAX_MBOX_MESSAGES", 2)
    source = tmp_path / "mail.mbox"
    write_mbox(source, 5)
    out = tmp_path / "out"

    result = run(path=str(source), output_directory=str(out))

    assert result["processed"] == 2
    assert result["converted"] == 2
    assert result["truncated"] is True
    assert "CLI" in result["message"]
    # The third message was never converted.
    assert len(list(out.glob("*.md"))) == 2
    report = json.loads(Path(result["report_path"]).read_text())
    assert report["summary"]["total"] == 2
    assert report["mbox_options"]["truncated"] is True


def test_archive_exactly_at_cap_is_not_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_MAX_MBOX_MESSAGES", 2)
    source = tmp_path / "mail.mbox"
    write_mbox(source, 2)
    result = run(path=str(source), output_directory=str(tmp_path / "out"))
    assert result["processed"] == 2
    assert result["truncated"] is False


def test_failure_list_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_MAX_MBOX_FAILURES_RETURNED", 2)
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK * 5 + POSTMARK + message(1))
    result = run(path=str(source), output_directory=str(tmp_path / "out"))
    assert result["failed"] == 5
    assert len(result["failures"]) == 2
    assert result["failures_omitted"] == 3
    assert result["converted"] == 1


def test_oversize_source_is_rejected_before_conversion(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_MAX_MBOX_BYTES", 16)
    source = tmp_path / "mail.mbox"
    write_mbox(source, 1)
    out = tmp_path / "out"
    with pytest.raises(ToolError, match="archives up to .* Use the dead-letter CLI"):
        convert_mbox(path=str(source), output_directory=str(out))
    assert not out.exists()


@pytest.mark.parametrize("name", ["mail.zip", "mail.mbox.gz", "mail.tgz", "mail.eml", "mail"])
def test_non_mbox_suffix_is_rejected(tmp_path, name):
    source = tmp_path / name
    source.write_bytes(POSTMARK + message(1))
    out = tmp_path / "out"
    with pytest.raises(ToolError, match="only a flat .mbox file"):
        convert_mbox(path=str(source), output_directory=str(out))
    assert not out.exists()


def test_missing_path_uses_file_not_found_contract(tmp_path):
    with pytest.raises(ToolError, match="File not found: "):
        convert_mbox(path=str(tmp_path / "absent.mbox"), output_directory=str(tmp_path / "out"))


def test_mbox_directory_is_rejected(tmp_path):
    apple_mail = tmp_path / "Inbox.mbox"
    apple_mail.mkdir()
    with pytest.raises(ToolError, match="not a regular file"):
        convert_mbox(path=str(apple_mail), output_directory=str(tmp_path / "out"))


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX non-root permissions")
def test_unreadable_source_is_rejected(tmp_path):
    source = tmp_path / "mail.mbox"
    write_mbox(source, 1)
    source.chmod(0)
    try:
        with pytest.raises(ToolError, match="File not readable"):
            convert_mbox(path=str(source), output_directory=str(tmp_path / "out"))
    finally:
        source.chmod(0o600)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("output_directory", ["", None])
def test_output_directory_is_required(tmp_path, output_directory):
    source = tmp_path / "mail.mbox"
    write_mbox(source, 1)
    with pytest.raises(ToolError, match="output_directory is required"):
        convert_mbox(path=str(source), output_directory=output_directory)


def test_output_directory_must_not_be_a_file(tmp_path):
    source = tmp_path / "mail.mbox"
    write_mbox(source, 1)
    target = tmp_path / "existing.txt"
    target.write_text("keep")
    with pytest.raises(ToolError, match="directory distinct from the source"):
        convert_mbox(path=str(source), output_directory=str(target))
    assert target.read_text() == "keep"


def test_second_run_is_collision_safe_for_outputs_and_report(tmp_path):
    source = tmp_path / "mail.mbox"
    write_mbox(source, 2)
    out = tmp_path / "out"
    first = run(path=str(source), output_directory=str(out))
    first_report = Path(first["report_path"]).read_bytes()
    second = run(path=str(source), output_directory=str(out))

    assert second["converted"] == 2
    assert Path(second["report_path"]).name == ".dead-letter-report-2.json"
    assert Path(first["report_path"]).read_bytes() == first_report
    json.loads(Path(second["report_path"]).read_text())
    assert len(list(out.glob("0000000*.md"))) == 4


def test_dry_run_writes_nothing(tmp_path):
    source = tmp_path / "mail.mbox"
    write_mbox(source, 2)
    out = tmp_path / "out"
    result = run(path=str(source), output_directory=str(out), dry_run=True)
    assert result["processed"] == 2
    assert result["skipped"] == 2
    assert result["report_path"] is None
    assert not out.exists()


def test_response_size_is_bounded_for_many_failures(tmp_path):
    source = tmp_path / "mail.mbox"
    source.write_bytes(POSTMARK * 200)
    result_text = convert_mbox(path=str(source), output_directory=str(tmp_path / "out"))
    result = json.loads(result_text)
    assert result["failed"] == 200
    assert len(result["failures"]) == mcp_server.MCP_MAX_MBOX_FAILURES_RETURNED
    assert len(result_text) < 8192


def test_conversion_options_reach_the_importer(tmp_path, monkeypatch):
    from dead_letter.core.types import ThreadMode

    captured = {}
    real = mcp_server._convert_mbox_records

    def spy(source, **kwargs):
        captured.update(kwargs)
        return real(source, **kwargs)

    monkeypatch.setattr(mcp_server, "_convert_mbox_records", spy)
    source = tmp_path / "mail.mbox"
    write_mbox(source, 1)
    run(path=str(source), output_directory=str(tmp_path / "out"), preset="clean",
        thread_mode="structured")
    options = captured["options"]
    assert options.strip_disclaimers is True
    assert options.thread_mode is ThreadMode.STRUCTURED
    assert options.allow_fallback_on_html_error is True
    assert "unescape" not in captured and "timeout_seconds" not in captured


@pytest.mark.anyio
async def test_schema_requires_path_and_output_directory():
    from mcp import Client

    async with Client(mcp_server.mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["convert_mbox"]
    assert set(tool.input_schema["required"]) == {"path", "output_directory"}
    properties = tool.input_schema["properties"]
    for absent in ("unescape", "timeout_seconds", "max_message_mib", "source_handling", "delete_eml"):
        assert absent not in properties
    assert "256 MiB" in tool.description and "1000 messages" in tool.description


@pytest.mark.anyio
async def test_real_stdio_round_trip(tmp_path):
    """Spawn the console entry point and call convert_mbox over stdio."""
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    source = tmp_path / "mail.mbox"
    write_mbox(source, 3, empty_at=2)
    before = digest(source)
    out = tmp_path / "out"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from dead_letter.backend.mcp_server import main; main()"],
    )
    async with Client(params) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        result = await client.call_tool(
            "convert_mbox", {"path": str(source), "output_directory": str(out)},
        )
        rejected = await client.call_tool(
            "convert_mbox", {"path": str(tmp_path / "mail.zip"), "output_directory": str(out)},
        )

    assert names == {"convert_eml", "convert_eml_to_bundle", "convert_directory",
                     "get_diagnostics", "convert_mbox"}
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert (payload["converted"], payload["failed"], payload["truncated"]) == (2, 1, False)
    assert json.loads(Path(payload["report_path"]).read_text())["summary"]["total"] == 3
    assert rejected.is_error is True
    assert "only a flat .mbox file" in rejected.content[0].text
    assert digest(source) == before


def test_append_failure_publishes_partial_failed_report(tmp_path, monkeypatch):
    real = mcp_server.StreamingReport

    class FullSpool(real):
        def append(self, entry):
            if self.total == 2:
                raise OSError("No space left on device")
            super().append(entry)

    monkeypatch.setattr(mcp_server, "StreamingReport", FullSpool)
    source = tmp_path / "mail.mbox"
    write_mbox(source, 4)
    out = tmp_path / "out"

    with pytest.raises(ToolError, match="failed after 3 messages: mbox_io_error.*partial report"):
        convert_mbox(path=str(source), output_directory=str(out))

    report = json.loads((out / ".dead-letter-report.json").read_text())
    assert report["job"]["status"] == "failed"
    assert report["summary"]["total"] == 2
    assert [row["mbox"]["index"] for row in report["results"]] == [1, 2]
    # Message 3 was converted before its append failed; message 4 never was.
    assert len(list(out.glob("*.md"))) == 3


def test_importer_error_mid_stream_publishes_partial_failed_report(tmp_path, monkeypatch):
    real = mcp_server._convert_mbox_records

    def failing(source, **kwargs):
        results = real(source, **kwargs)
        try:
            for count, item in enumerate(results, 1):
                yield item
                if count == 2:
                    raise OSError("disk went away")
        finally:
            results.close()

    monkeypatch.setattr(mcp_server, "_convert_mbox_records", failing)
    source = tmp_path / "mail.mbox"
    write_mbox(source, 4)
    out = tmp_path / "out"

    with pytest.raises(ToolError, match="failed after 2 messages: mbox_io_error"):
        convert_mbox(path=str(source), output_directory=str(out))

    report = json.loads((out / ".dead-letter-report.json").read_text())
    assert report["job"]["status"] == "failed"
    assert report["summary"] == {"total": 2, "written": 2, "skipped": 0, "errors": 0}


def test_report_placeholder_is_removed_when_close_fails(tmp_path, monkeypatch):
    real = mcp_server._open_collision_safe_output

    class BadHandle:
        def __init__(self, handle):
            self._handle = handle

        def close(self):
            self._handle.close()
            raise OSError("close failed")

    def reserve(target):
        handle, path = real(target)
        return BadHandle(handle), path

    monkeypatch.setattr(mcp_server, "_open_collision_safe_output", reserve)
    source = tmp_path / "mail.mbox"
    write_mbox(source, 1)
    out = tmp_path / "out"

    with pytest.raises(ToolError, match="report could not be written after 1 messages: mbox_io_error"):
        convert_mbox(path=str(source), output_directory=str(out))

    assert not list(out.glob(".dead-letter-report*"))
    assert len(list(out.glob("*.md"))) == 1


def swap_before_open(monkeypatch, change):
    """Run ``change`` after the admission stat, just before the importer opens."""
    real = mcp_server._convert_mbox_records

    def swapped(source, **kwargs):
        change(Path(source))
        return real(source, **kwargs)

    monkeypatch.setattr(mcp_server, "_convert_mbox_records", swapped)


def test_growth_past_byte_cap_after_admission_stops_with_failed_report(tmp_path, monkeypatch):
    source = tmp_path / "mail.mbox"
    small = write_mbox(source, 2)
    monkeypatch.setattr(mcp_server, "MCP_MAX_MBOX_BYTES", len(small))
    grown = POSTMARK + message(1) + POSTMARK + message(2) + b"".join(
        POSTMARK + message(n) for n in range(3, 11)
    )
    swap_before_open(monkeypatch, lambda path: path.write_bytes(grown))
    out = tmp_path / "out"

    with pytest.raises(ToolError, match="failed after 3 messages: MBOX archive exceeds the MCP limit"):
        convert_mbox(path=str(source), output_directory=str(out))

    report = json.loads((out / ".dead-letter-report.json").read_text())
    assert report["job"]["status"] == "failed"
    # Records 1-2 fit; record 3 crossed the cap and conversion stopped there.
    assert [row["mbox"]["index"] for row in report["results"]] == [1, 2, 3]
    assert len(list(out.glob("*.md"))) == 3


def test_enlarged_within_cap_after_admission_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "mail.mbox"
    write_mbox(source, 2)
    swap_before_open(monkeypatch, lambda path: path.write_bytes(path.read_bytes() + POSTMARK + message(3)))
    out = tmp_path / "out"

    with pytest.raises(ToolError, match="MBOX changed during MCP conversion; use an immutable export"):
        convert_mbox(path=str(source), output_directory=str(out))

    report = json.loads((out / ".dead-letter-report.json").read_text())
    assert report["job"]["status"] == "failed"
    assert report["summary"]["total"] == 3


def test_replaced_file_after_admission_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "mail.mbox"
    data = write_mbox(source, 2)

    def replace(path):
        other = tmp_path / "other.mbox"
        other.write_bytes(data)  # same size and bytes, different file
        os.replace(other, path)

    swap_before_open(monkeypatch, replace)
    out = tmp_path / "out"

    with pytest.raises(ToolError, match="MBOX changed during MCP conversion"):
        convert_mbox(path=str(source), output_directory=str(out))

    report = json.loads((out / ".dead-letter-report.json").read_text())
    assert report["job"]["status"] == "failed"


def test_truncation_at_cap_still_uses_admitted_size(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_MAX_MBOX_MESSAGES", 2)
    source = tmp_path / "mail.mbox"
    write_mbox(source, 3)
    result = run(path=str(source), output_directory=str(tmp_path / "out"))
    assert (result["processed"], result["truncated"]) == (2, True)
    report = json.loads(Path(result["report_path"]).read_text())
    assert report["job"]["status"] == "succeeded"
