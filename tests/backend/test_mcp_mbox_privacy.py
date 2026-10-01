"""MBOX failure privacy and report finalization (#187, #145). Synthetic mail only."""

from __future__ import annotations

import errno
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

SENTINEL = "MAIL_FILENAME_SENTINEL_187"
REPORT_SENTINEL = "REPORT_FILENAME_SENTINEL_187"
POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
CASES = [
    ("setup", "os"), ("early", "os"), ("early", "value"),
    ("mid", "os"), ("mid", "value"), ("mid", "runtime"),
    ("append", "os"), ("report", "os"), ("dual", "runtime"),
    ("close", "os"), ("relative", "value"),
    ("entry", "os"), ("unknown_entry", "os"),
]


def make_error(kind: str, sentinel: str = SENTINEL) -> Exception:
    if kind == "os":
        # Both filenames AND strerror are private. Only the numeric errno is safe.
        return OSError(errno.EIO, sentinel, f"/{sentinel}.txt", None, f"/{sentinel}-2.txt")
    if kind == "value":
        return ValueError(sentinel)
    # Exception class names are not necessarily safe either.
    return type(sentinel, (RuntimeError,), {})(sentinel)


def install_failure(server, point: str, kind: str) -> None:
    """Test-only injection, also loaded by the actual stdio child below."""
    real = server._convert_mbox_records

    def records(*args, **kwargs):
        if point == "early":
            raise make_error(kind)
        iterator = real(*args, **kwargs)
        try:
            for item in iterator:
                if point in {"entry", "unknown_entry"}:
                    item = replace(item, success=False, output=None, error={
                        "code": "mbox_archive_error" if point == "entry" else SENTINEL * 10000,
                        "message": SENTINEL * 10000, "stage": "mbox",
                    })
                elif point == "relative":
                    item = replace(item, output=Path(kwargs["output"]).parent / SENTINEL)
                yield item
                if point in {"mid", "dual"}:
                    raise make_error(kind)
        finally:
            iterator.close()
            if point == "close":
                raise make_error(kind)

    server._convert_mbox_records = records
    if point == "close":
        server.MCP_MAX_MBOX_MESSAGES = 1
    if point == "setup":
        def fail_setup(*args, **kwargs):
            raise make_error(kind)
        server._run_mcp_mbox = fail_setup
    if point in {"append", "report", "dual"}:
        original_report = server.StreamingReport

        class FailingReport(original_report):
            def append(self, entry):
                if point == "append":
                    raise make_error(kind)
                return super().append(entry)

            def finish(self, *args, **kwargs):
                if point in {"report", "dual"}:
                    raise make_error("os", REPORT_SENTINEL)
                return super().finish(*args, **kwargs)

        server.StreamingReport = FailingReport


def write_archive(tmp_path: Path) -> Path:
    path = tmp_path / "input.mbox"
    path.write_bytes(b"".join(
        POSTMARK + f"From: sender@example.test\nSubject: {SENTINEL}\n\nSynthetic {n}\n\n".encode()
        for n in range(2)
    ))
    return path


def assert_outcome(text: str, is_error: bool, point: str, root: Path) -> None:
    assert SENTINEL not in text
    assert REPORT_SENTINEL not in text
    assert is_error is (point not in {"entry", "unknown_entry"})
    if point in {"entry", "unknown_entry"}:
        response = json.loads(text)
        assert response["failed"] == 2
        expected = "mbox_archive_error" if point == "entry" else "conversion_error"
        assert {row["code"] for row in response["failures"]} == {expected}
        assert len(text) < 8192
        report = json.loads(Path(response["report_path"]).read_text())
        assert SENTINEL in report["results"][0]["error"]["message"]
    elif point in {"setup", "early"}:
        assert not root.exists()
    elif point in {"report", "dual"}:
        assert "report could not be written" in text
        assert not list(root.glob(".dead-letter-report*"))
    else:
        report_path = root / ".dead-letter-report.json"
        assert f"partial report: {report_path}" in text
        report = json.loads(report_path.read_text())
        assert report["job"]["status"] == "failed"
        expected_total = 0 if point in {"append", "relative"} else 1
        assert report["summary"]["total"] == expected_total
        if point == "close":
            assert "failed after 1 messages" in text
            assert "mbox_io_error" in text
            # Early-stop cleanup failed before success/report publication.
            assert len(list(root.glob("*.md"))) == 1


@pytest.mark.parametrize(("point", "kind"), CASES)
@pytest.mark.anyio
async def test_protocol_failure_privacy(tmp_path, monkeypatch, point, kind):
    from mcp import Client
    from dead_letter.backend import mcp_server

    # install_failure assigns several attributes; restore all of them per test.
    for name in ("_convert_mbox_records", "_run_mcp_mbox", "StreamingReport", "MCP_MAX_MBOX_MESSAGES"):
        monkeypatch.setattr(mcp_server, name, getattr(mcp_server, name))
    install_failure(mcp_server, point, kind)
    source = write_archive(tmp_path)
    before = source.read_bytes()
    root = tmp_path / "out"
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("convert_mbox", {
            "path": str(source), "output_directory": str(root),
        })
    assert_outcome(result.content[0].text, result.is_error, point, root)
    assert source.read_bytes() == before


@pytest.mark.parametrize("point", ["early", "mid", "append", "report", "dual", "close", "relative", "entry"])
@pytest.mark.anyio
async def test_real_stdio_failure_privacy(tmp_path, point):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    source = write_archive(tmp_path)
    before = source.read_bytes()
    root = tmp_path / "out"
    # No production fault flags or private corpus: only this synthetic test child
    # imports the injector. The actual server and MCP stdio transport are used.
    bootstrap = (
        "from runpy import run_path; "
        "from dead_letter.backend import mcp_server; "
        f"run_path({str(Path(__file__).resolve())!r})['install_failure'](mcp_server, {point!r}, 'os'); "
        "mcp_server.main()"
    )
    params = StdioServerParameters(command=sys.executable, args=["-c", bootstrap])
    async with Client(params) as client:
        result = await client.call_tool("convert_mbox", {
            "path": str(source), "output_directory": str(root),
        })
    assert_outcome(result.content[0].text, result.is_error, point, root)
    assert source.read_bytes() == before


@pytest.mark.parametrize("error, expected", [
    (make_error("os"), "mbox_io_error"),
    (OSError(SENTINEL), "mbox_io_error"),
    (OSError(2**100, SENTINEL), "mbox_io_error"),
    (make_error("value"), "mbox_invalid_input"),
    (make_error("runtime"), "mbox_conversion_error"),
])
def test_exception_formatter_never_echoes_private_fields(error, expected, caplog):
    from dead_letter.backend.mcp_server import _mcp_mbox_error

    text = _mcp_mbox_error(error)
    assert text.startswith(expected)
    assert SENTINEL not in text
    assert SENTINEL in caplog.text  # Details remain available to the local operator.
