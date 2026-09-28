"""CLI batch loop for MBOX. Keep results and report entries off the heap."""

from __future__ import annotations

import json
import sys
from contextlib import ExitStack, closing
from pathlib import Path
from time import monotonic

from dead_letter.core.mbox import MboxLimits, UnescapeMode
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.mbox_archive import _convert_mbox_archive
from dead_letter.core.stream_report import StreamingReport
from dead_letter.core.types import ConvertOptions


def run_mbox(
    source: str | Path,
    *,
    output: str | None,
    options: ConvertOptions,
    max_message_mib: int = 64,
    unescape: UnescapeMode = "preserve",
    bundles: bool = False,
    timeout_seconds: float | None = None,
    archive_input: bool = False,
    member: str | None = None,
    staging_dir: str | None = None,
) -> int:
    root = Path(output).expanduser().resolve() if output is not None else Path(source).expanduser().resolve().with_suffix(".markdown")
    started = monotonic()
    total = failures = 0
    interrupted = False
    fatal = False
    archive_summary = {} if archive_input else None
    with ExitStack() as stack:
        report = stack.enter_context(StreamingReport()) if options.report else None
        try:
            limits = MboxLimits(max_message_bytes=max_message_mib * 1024 * 1024)
            convert = _convert_mbox_archive if archive_input else convert_mbox
            archive_options = dict(member=member, staging_dir=staging_dir, archive_summary=archive_summary) if archive_input else {}
            results = stack.enter_context(closing(convert(
                source, output=root, options=options, limits=limits,
                unescape=unescape, bundles=bundles, timeout_seconds=timeout_seconds, **archive_options,
            )))
            for item in results:
                total += 1
                failures += not item.success
                fatal = fatal or item.mbox is None
                entry = {"source": item.source, "output": None, "success": item.success}
                if item.output is not None:
                    entry["output"] = item.output.relative_to(root).as_posix()
                if item.mbox is not None:
                    entry["mbox"] = item.mbox
                if item.diagnostics is not None:
                    entry["diagnostics"] = item.diagnostics
                if item.error is not None:
                    entry["error"] = item.error
                    label = json.dumps(item.source, ensure_ascii=True) if archive_input else item.source
                    print(f"{label}: {item.error['code']}: {item.error['message']}", file=sys.stderr)
                if report is not None:
                    report.append(entry)
        except KeyboardInterrupt:
            interrupted = True
        except (OSError, ValueError) as exc:
            message = json.dumps(str(exc), ensure_ascii=True) if archive_input else str(exc)
            print(f"MBOX import failed: {message}", file=sys.stderr)
            return 1
        if report is not None:
            try:
                report.finish(
                    root, options=options, input_path=str(source),
                    duration_ms=int((monotonic() - started) * 1000),
                    status="interrupted" if interrupted else "failed" if fatal else None,
                    archive_summary=archive_summary,
                    import_options={"unescape": unescape, "bundles": bundles,
                                    "max_message_bytes": limits.max_message_bytes,
                                    "max_line_bytes": limits.max_line_bytes,
                                    "timeout_seconds": timeout_seconds},
                )
            except KeyboardInterrupt:
                print("MBOX report publication interrupted; previous report retained if present", file=sys.stderr)
                return 130
            except OSError as exc:
                message = json.dumps(str(exc), ensure_ascii=True) if archive_input else str(exc)
                print(f"MBOX report could not be written: {message}", file=sys.stderr)
                return 1
    print(f"MBOX: {total - failures} succeeded, {failures} errors" +
          (" (interrupted)" if interrupted else ""), file=sys.stderr)
    return 130 if interrupted else 1 if failures else 0
