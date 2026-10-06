"""MCP server for dead-letter .eml-to-markdown conversion."""

from __future__ import annotations

import base64
import errno
import json
import logging
import os
import tempfile
from contextlib import ExitStack, closing
from importlib.resources import files
from pathlib import Path
from time import monotonic
from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import Icon, ToolAnnotations

from dead_letter import __version__
from dead_letter.core import convert, convert_dir
from dead_letter.core._pipeline import (
    _iter_source_eml_files,
    _open_collision_safe_output,
    convert_to_bundle_with_diagnostics,
)
from dead_letter.core.mbox import MboxLimits
from dead_letter.core.mbox_import import convert_mbox as _convert_mbox_records
from dead_letter.core.stream_report import StreamingReport
from dead_letter.core.types import ConvertOptions

logger = logging.getLogger(__name__)

# Embedded as a data: URI so clients can show the icon without a network fetch.
_ICON_PNG = files("dead_letter.backend").joinpath("assets/mcp-icon.png").read_bytes()

mcp = MCPServer(
    "dead-letter",
    title="dead-letter",
    description="Convert .eml and .mbox email to Markdown with YAML front matter.",
    website_url="https://github.com/BigCactusLabs/dead-letter",
    icons=[
        Icon(
            src="data:image/png;base64," + base64.b64encode(_ICON_PNG).decode("ascii"),
            mime_type="image/png",
            sizes=["64x64"],
        )
    ],
    version=__version__,
)
# Write tools only create new, collision-safe paths and never modify or remove
# sources, so they are not destructive; repeat calls add numbered copies.
_WRITES_NEW_FILES = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

MCP_MAX_DIRECTORY_FILES = 50
MCP_MAX_MBOX_BYTES = 256 * 1024 * 1024
MCP_MAX_MBOX_MESSAGES = 1000
MCP_MAX_MBOX_FAILURES_RETURNED = 20

PRESETS: dict[str, dict[str, bool]] = {
    "default": {
        "strip_signatures": True,
        "strip_tracking_pixels": True,
        "strip_signature_images": True,
    },
    "clean": {
        "strip_signatures": True,
        "strip_disclaimers": True,
        "strip_quoted_headers": True,
        "strip_tracking_pixels": True,
        "strip_signature_images": True,
    },
    "verbose": {
        "include_all_headers": True,
        "include_raw_html": True,
    },
    "raw": {},
}


_CONVERSION_FLAGS = frozenset({
    "strip_signatures", "strip_disclaimers", "strip_tracking_pixels",
    "strip_signature_images", "strip_quoted_headers", "embed_inline_images",
    "include_all_headers", "include_raw_html", "no_calendar_summary",
    "dry_run", "thread_mode", "thread_order",
})


def _resolve_options(preset: str = "default", **overrides: bool | str | None) -> ConvertOptions:
    """Build ConvertOptions from a preset name with optional flag overrides."""
    base: dict[str, bool | str] = dict(PRESETS.get(preset, PRESETS["default"]))
    for key, value in overrides.items():
        if value is not None:
            base[key] = value
    # MCP operations always enable resilience
    base["allow_fallback_on_html_error"] = True
    base["allow_html_repair_on_panic"] = True
    return ConvertOptions(**base)  # type: ignore[arg-type]


def _build_options(local_vars: dict) -> ConvertOptions:
    """Build ConvertOptions from a tool function's local variables.

    Extracts ``preset`` and any recognized conversion flags from the dict
    (typically ``locals()``), ignoring unrelated tool parameters.
    """
    return _resolve_options(
        local_vars.get("preset", "default"),
        **{k: local_vars[k] for k in _CONVERSION_FLAGS if k in local_vars},
    )


def _check_eml_source(eml_path: str) -> Path:
    """Return the source path, or raise ToolError if it is missing or not .eml."""
    source = Path(eml_path).expanduser()
    if not source.exists():
        raise ToolError(f"File not found: {eml_path}")
    if source.resolve().suffix.lower() != ".eml":
        raise ToolError(f"Expected a .eml file: {eml_path}")
    return source


def _raise_on_failure(result: object) -> None:
    """Raise ToolError if a ConvertResult or BundleResult indicates failure.

    The client sees only the stable error code. The raw error text can quote
    email content, so it is logged on the server instead.
    """
    if getattr(result, "success", True):
        return
    error_code = getattr(result, "error_code", None) or "conversion_error"
    logger.warning(
        "Conversion failed (%s) for %s: %s",
        error_code,
        getattr(result, "source", None),
        getattr(result, "error", None),
    )
    parts = [f"Conversion failed: {error_code}"]
    if getattr(result, "plain_text_fallback_available", None):
        parts.append("Plain text fallback is available.")
    if getattr(result, "html_repair_available", None):
        parts.append("HTML repair is available.")
    raise ToolError(" ".join(parts))


@mcp.tool(title="Convert email", annotations=_WRITES_NEW_FILES)
def convert_eml(
    eml_path: str,
    output_path: str | None = None,
    preset: Literal["default", "clean", "verbose", "raw"] = "default",
    strip_signatures: bool | None = None,
    strip_disclaimers: bool | None = None,
    strip_tracking_pixels: bool | None = None,
    strip_signature_images: bool | None = None,
    strip_quoted_headers: bool | None = None,
    embed_inline_images: bool | None = None,
    include_all_headers: bool | None = None,
    include_raw_html: bool | None = None,
    no_calendar_summary: bool | None = None,
    thread_mode: Literal["latest", "structured"] = "latest",
    thread_order: Literal["oldest-first", "latest-first"] = "oldest-first",
) -> str:
    """Convert a .eml email file to Markdown with YAML front matter.

    Returns the full Markdown content (front matter + body). When output_path
    is provided, also writes the file to disk.

    Attachments are listed in the front matter but not written to disk; use
    convert_eml_to_bundle to save the decoded attachment files.

    thread_mode="structured" adds a section per earlier reply or forwarded
    message. Gmail HTML forwards and unquoted plain-text forwards are kept in
    latest mode too; a plain-text forward inside ">" quoting is not.

    Presets bundle common flag combinations:
    - default: strips signatures, tracking pixels, signature images
    - clean: default + strips disclaimers and quoted headers
    - verbose: includes all headers and raw HTML
    - raw: no stripping, preserves everything

    Individual flags override the preset when provided.
    """
    options = _build_options(locals())
    source = _check_eml_source(eml_path)

    if output_path is not None:
        result = convert(source, output=Path(output_path), options=options)
        _raise_on_failure(result)
        assert result.output is not None
        return result.output.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        result = convert(source, output=Path(tmp), options=options)
        _raise_on_failure(result)
        assert result.output is not None
        return result.output.read_text(encoding="utf-8")


@mcp.tool(title="Convert email to bundle", annotations=_WRITES_NEW_FILES)
def convert_eml_to_bundle(
    eml_path: str,
    bundle_root: str,
    source_handling: Literal["copy", "move", "delete"] = "copy",
    preset: Literal["default", "clean", "verbose", "raw"] = "default",
    strip_signatures: bool | None = None,
    strip_disclaimers: bool | None = None,
    strip_tracking_pixels: bool | None = None,
    strip_signature_images: bool | None = None,
    strip_quoted_headers: bool | None = None,
    embed_inline_images: bool | None = None,
    include_all_headers: bool | None = None,
    include_raw_html: bool | None = None,
    no_calendar_summary: bool | None = None,
    thread_mode: Literal["latest", "structured"] = "latest",
    thread_order: Literal["oldest-first", "latest-first"] = "oldest-first",
) -> str:
    """Convert a .eml file to a self-contained bundle with markdown and attachments.

    Creates a directory containing the converted markdown, extracted attachments,
    and optionally the original .eml source.

    source_handling only accepts 'copy' over MCP: the original .eml is copied
    into the bundle and left untouched. The 'move' and 'delete' modes are
    rejected here — use the CLI or the Python API for those.

    Returns JSON with bundle_path, markdown_path, attachment_paths, and
    optional diagnostics.
    """
    if source_handling != "copy":
        raise ToolError(
            "MCP convert_eml_to_bundle only supports source_handling='copy'; "
            "use the CLI/API for move/delete."
        )

    options = _build_options(locals())
    source = _check_eml_source(eml_path)

    bundle_path = Path(bundle_root)
    try:
        bundle_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolError(
            f"Cannot create bundle_root {bundle_root}: {exc.strerror or type(exc).__name__}"
        ) from exc

    result, diagnostics = convert_to_bundle_with_diagnostics(
        source,
        bundle_root=bundle_path,
        options=options,
        source_handling=source_handling,
    )
    _raise_on_failure(result)

    response: dict[str, object] = {
        "bundle_path": str(result.bundle),
        "markdown_path": str(result.markdown),
        "attachment_paths": [str(p) for p in result.attachments],
    }
    if diagnostics is not None:
        response["diagnostics"] = diagnostics
    return json.dumps(response, indent=2)


@mcp.tool(title="Convert email folder", annotations=_WRITES_NEW_FILES)
def convert_directory(
    directory: str,
    output_directory: str,
    dry_run: bool = False,
    preset: Literal["default", "clean", "verbose", "raw"] = "default",
    strip_signatures: bool | None = None,
    strip_disclaimers: bool | None = None,
    strip_tracking_pixels: bool | None = None,
    strip_signature_images: bool | None = None,
    strip_quoted_headers: bool | None = None,
    embed_inline_images: bool | None = None,
    include_all_headers: bool | None = None,
    include_raw_html: bool | None = None,
    no_calendar_summary: bool | None = None,
    thread_mode: Literal["latest", "structured"] = "latest",
    thread_order: Literal["oldest-first", "latest-first"] = "oldest-first",
) -> str:
    """Batch convert all .eml files in a directory to Markdown.

    Recursively finds .eml files (at most 50 per call; larger directories
    are rejected before any conversion). output_directory is required:
    Markdown is written there, mirroring subfolders, and source .eml files
    are left in place. Returns a JSON summary with total, successes,
    failures, output_paths, and errors.

    Use convert_eml to retrieve individual converted file content.
    """
    options = _build_options(locals())
    dir_path = Path(directory).expanduser().resolve()
    if not dir_path.is_dir():
        raise ToolError(f"Directory not found: {directory}")
    if not output_directory:
        raise ToolError("output_directory is required for MCP directory conversion")

    files = _iter_source_eml_files(dir_path)
    if len(files) > MCP_MAX_DIRECTORY_FILES:
        raise ToolError(
            "MCP directory conversion supports at most "
            f"{MCP_MAX_DIRECTORY_FILES} .eml files; found {len(files)}."
        )

    out = Path(output_directory).expanduser()
    results = convert_dir(dir_path, output=out, options=options)

    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]

    summary = {
        "total": len(results),
        "successes": len(successes),
        "failures": len(failures),
        "output_paths": [str(r.output) for r in successes if r.output],
        "errors": [{"file": str(r.source), "error": r.error} for r in failures],
    }
    return json.dumps(summary, indent=2)


@mcp.tool(title="Convert MBOX archive", annotations=_WRITES_NEW_FILES)
def convert_mbox(
    path: str,
    output_directory: str,
    bundles: bool = False,
    dry_run: bool = False,
    preset: Literal["default", "clean", "verbose", "raw"] = "default",
    strip_signatures: bool | None = None,
    strip_disclaimers: bool | None = None,
    strip_tracking_pixels: bool | None = None,
    strip_signature_images: bool | None = None,
    strip_quoted_headers: bool | None = None,
    embed_inline_images: bool | None = None,
    include_all_headers: bool | None = None,
    include_raw_html: bool | None = None,
    no_calendar_summary: bool | None = None,
    thread_mode: Literal["latest", "structured"] = "latest",
    thread_order: Literal["oldest-first", "latest-first"] = "oldest-first",
) -> str:
    """Convert one flat .mbox archive (e.g. Gmail Takeout) to Markdown files.

    Bounded: the archive must be at most 256 MiB, and conversion stops after
    1000 messages (the response then has truncated=true; use the dead-letter
    CLI for larger archives). Compressed archives and Apple Mail .mbox
    directories are rejected. output_directory is required; one .md per
    message (or one bundle directory when bundles=true) is written there with a
    collision-safe JSON report. The source archive is never modified.

    Returns a JSON summary (processed, converted, skipped, failed, truncated,
    report_path, and at most 20 failure entries), never message content.
    Cancellation is not supported; byte/message caps are not a time limit.
    """
    options = _build_options(locals())
    if not output_directory:
        raise ToolError("output_directory is required for MCP MBOX conversion")
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".mbox":
        raise ToolError(
            "MCP convert_mbox accepts only a flat .mbox file; "
            "extract compressed archives first."
        )
    if not source.exists():
        raise ToolError(f"File not found: {path}")
    if not source.is_file():
        raise ToolError(f"MBOX path is not a regular file: {path}")
    if not os.access(source, os.R_OK):
        raise ToolError(f"File not readable: {path}")
    admitted = source.stat()
    size = admitted.st_size
    if size > MCP_MAX_MBOX_BYTES:
        raise ToolError(
            f"MCP MBOX conversion supports archives up to {MCP_MAX_MBOX_BYTES // (1024 * 1024)} MiB; "
            f"found {size} bytes. Use the dead-letter CLI for larger archives."
        )

    root = Path(output_directory).expanduser().resolve()
    try:
        return _run_mcp_mbox(source, admitted, root, options, bundles=bundles)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"MBOX conversion failed: {_mcp_mbox_error(exc)}") from exc


def _publish_mcp_report(report: StreamingReport, root: Path, **kwargs: object) -> Path:
    """Publish under a reserved collision-safe name; never leave the placeholder."""
    handle, reserved = _open_collision_safe_output(root / ".dead-letter-report.json")
    try:
        handle.close()
        return report.finish(root, filename=reserved.name, job_id="mcp", **kwargs)  # type: ignore[arg-type]
    except BaseException:
        reserved.unlink(missing_ok=True)
        raise


class _McpMboxLimitError(Exception):
    """The archive outgrew or changed after admission; never a message error."""


# Only fixed, reviewed descriptions leave the MBOX summary boundary. Archive
# errors can contain paths, parser text or MIME-derived attachment filenames.
_MCP_MBOX_FAILURE_MESSAGES = {
    "mbox_empty_message": "Stored message is empty or exceeds configured resource limits",
    "mbox_message_too_large": "Stored message is empty or exceeds configured resource limits",
    "mbox_line_too_long": "Stored message is empty or exceeds configured resource limits",
    "mbox_invalid_message": "Stored message is empty or exceeds configured resource limits",
    "mbox_archive_error": "Archive conversion failed; inspect the local report for details",
    "html_markdown_failed": "HTML-to-Markdown conversion failed",
    "conversion_error": "Message conversion failed",
}


def _mcp_mbox_error(error: Exception) -> str:
    """Log private details locally; return only fixed text or an OS errno reason."""
    logger.warning("MBOX conversion exception", exc_info=(type(error), error, error.__traceback__))
    if isinstance(error, _McpMboxLimitError):
        # This private exception is constructed only from MCP-owned limit text.
        return str(error)
    if isinstance(error, OSError):
        # Even strerror may be application-supplied. Derive the reason from a
        # known numeric errno, never filename/filename2, args or exception text.
        if type(error.errno) is int and error.errno in errno.errorcode:
            return f"mbox_io_error ({errno.errorcode[error.errno]}: {os.strerror(error.errno)})"
        return "mbox_io_error"
    if isinstance(error, ValueError):
        # Preserve the one reviewed core validation message reachable from MCP.
        if type(error) is ValueError and error.args == (
            "MBOX output must be a directory distinct from the source",
        ):
            return "MBOX output must be a directory distinct from the source"
        return "mbox_invalid_input"
    return "mbox_conversion_error"


def _mcp_mbox_failure(error: dict[str, str] | None) -> dict[str, str]:
    """Bound both failure strings, leaving full importer details in the report."""
    code = (error or {}).get("code")
    if code not in _MCP_MBOX_FAILURE_MESSAGES:
        code = "conversion_error"
    return {"code": code, "message": _MCP_MBOX_FAILURE_MESSAGES[code]}


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _run_mcp_mbox(
    source: Path, admitted: os.stat_result, root: Path, options: ConvertOptions, *, bundles: bool,
) -> str:
    size = admitted.st_size
    last_end: int | None = None
    limits = MboxLimits()
    started = monotonic()
    processed = converted = skipped = failed = 0
    truncated = fatal = False
    failures: list[dict[str, object]] = []
    report_path: Path | None = None
    error: Exception | None = None
    with ExitStack() as stack:
        report = None if options.dry_run else stack.enter_context(StreamingReport())
        try:
            # Close the importer inside the failure/report boundary. In
            # particular, early-stop cleanup must finish before success is saved.
            with closing(_convert_mbox_records(
                source, output=root, options=options, limits=limits, bundles=bundles,
            )) as results:
                for item in results:
                    processed += 1
                    fatal = fatal or item.mbox is None
                    entry: dict[str, object] = {"source": item.source, "output": None, "success": item.success}
                    if item.output is not None:
                        entry["output"] = item.output.relative_to(root).as_posix()
                    if item.mbox is not None:
                        entry["mbox"] = item.mbox
                    if item.diagnostics is not None:
                        entry["diagnostics"] = item.diagnostics
                    if item.error is not None:
                        entry["error"] = item.error
                    if not item.success:
                        failed += 1
                        if len(failures) < MCP_MAX_MBOX_FAILURES_RETURNED:
                            failures.append({
                                "index": item.mbox["index"] if item.mbox is not None else None,
                                **_mcp_mbox_failure(item.error),
                            })
                    elif item.output is None:
                        skipped += 1
                    else:
                        converted += 1
                    if report is not None:
                        report.append(entry)
                    if item.mbox is not None and int(item.mbox["end_offset"]) > MCP_MAX_MBOX_BYTES:
                        # The admission stat is a fast path only; enforce the cap on
                        # the bytes actually read in case the file grew or was swapped.
                        raise _McpMboxLimitError(
                            "MBOX archive exceeds the MCP limit of "
                            f"{MCP_MAX_MBOX_BYTES // (1024 * 1024)} MiB; use the dead-letter CLI"
                        )
                    if processed >= MCP_MAX_MBOX_MESSAGES:
                        # Stop before framing another record.
                        last_end = int(item.mbox["end_offset"]) if item.mbox is not None else size
                        break
            try:
                current = _stat_identity(source.stat())
            except OSError:
                current = None
            if current != _stat_identity(admitted):
                raise _McpMboxLimitError("MBOX changed during MCP conversion; use an immutable export")
            # The archive holds more messages only if the last record ended
            # before the admitted (and re-verified) end of file.
            truncated = last_end is not None and last_end < size
        except Exception as exc:
            # Like the CLI's interrupted path: outputs already written still get
            # a published report (status "failed") before the error surfaces.
            error = exc
        if report is not None and (error is None or processed):
            try:
                report_path = _publish_mcp_report(
                    report, root, options=options, input_path=str(source),
                    duration_ms=int((monotonic() - started) * 1000),
                    status="failed" if fatal or error is not None else None,
                    import_options={"unescape": "preserve", "bundles": bundles,
                                    "max_message_bytes": limits.max_message_bytes,
                                    "max_line_bytes": limits.max_line_bytes,
                                    "timeout_seconds": None,
                                    "max_messages": MCP_MAX_MBOX_MESSAGES,
                                    "truncated": truncated},
                )
            except Exception as report_exc:
                report_error = _mcp_mbox_error(report_exc)
                if error is None:
                    raise ToolError(
                        f"MBOX report could not be written after {processed} messages: {report_error}"
                    ) from report_exc
                raise ToolError(
                    f"MBOX conversion failed after {processed} messages: {_mcp_mbox_error(error)}; "
                    f"the report could not be written: {report_error}"
                ) from error
    if error is not None:
        safe_error = _mcp_mbox_error(error)
        if not processed:
            raise ToolError(f"MBOX conversion failed: {safe_error}") from error
        partial = f"; partial report: {report_path}" if report_path is not None else ""
        raise ToolError(
            f"MBOX conversion failed after {processed} messages: {safe_error}{partial}"
        ) from error

    response: dict[str, object] = {
        "output_directory": str(root),
        "processed": processed,
        "converted": converted,
        "skipped": skipped,
        "failed": failed,
        "truncated": truncated,
        "report_path": str(report_path) if report_path is not None else None,
        "failures": failures,
        "failures_omitted": failed - len(failures),
    }
    if truncated:
        response["message"] = (
            f"Stopped after the MCP limit of {MCP_MAX_MBOX_MESSAGES} messages; "
            "the archive has more. Use the dead-letter CLI to convert the whole archive."
        )
    return json.dumps(response, indent=2)


@mcp.tool(title="Get conversion diagnostics", annotations=_READ_ONLY)
def get_diagnostics(
    eml_path: str,
    preset: Literal["default", "clean", "verbose", "raw"] = "default",
    strip_signatures: bool | None = None,
    strip_disclaimers: bool | None = None,
    strip_tracking_pixels: bool | None = None,
    strip_signature_images: bool | None = None,
    strip_quoted_headers: bool | None = None,
    embed_inline_images: bool | None = None,
    include_all_headers: bool | None = None,
    include_raw_html: bool | None = None,
    no_calendar_summary: bool | None = None,
    thread_mode: Literal["latest", "structured"] = "latest",
    thread_order: Literal["oldest-first", "latest-first"] = "oldest-first",
) -> str:
    """Inspect email quality and structure without writing permanent files.

    Use this to assess conversion quality before committing, or to
    troubleshoot problematic .eml files.

    Always returns JSON with: state (normal/degraded/review_recommended),
    selected_body, segmentation_path, client_hint, confidence,
    fallback_used, and warnings. Two keys are conditional: stripped_images
    appears only when images were removed, and attachments only when the
    message had attachments eligible for retention.
    """
    source = _check_eml_source(eml_path)

    options = _build_options(locals())

    with tempfile.TemporaryDirectory() as tmp:
        result, diagnostics = convert_to_bundle_with_diagnostics(
            source,
            bundle_root=Path(tmp) / "bundle",
            options=options,
            source_handling="copy",
        )
        _raise_on_failure(result)

    if diagnostics is None:
        raise RuntimeError("Diagnostics unavailable for successful conversion.")

    return json.dumps(diagnostics, indent=2, default=str)


def main() -> None:
    """Entry point for the dead-letter-mcp console script."""
    mcp.run()
