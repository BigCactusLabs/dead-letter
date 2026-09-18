"""CLI batch loop for MBOX. Keep results and report entries off the heap."""

from __future__ import annotations

import sys
from contextlib import ExitStack, closing
from pathlib import Path
from time import monotonic

from dead_letter.core.mbox import MboxLimits, UnescapeMode
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.stream_report import StreamingReport
from dead_letter.core.types import ConvertOptions


def run_mbox(
    source: Path,
    *,
    output: str | None,
    options: ConvertOptions,
    max_message_mib: int = 64,
    unescape: UnescapeMode = "preserve",
    bundles: bool = False,
) -> int:
    root = Path(output).expanduser().resolve() if output is not None else source.with_suffix(".markdown")
    started = monotonic()
    total = failures = 0
    interrupted = False
    fatal = False
    with ExitStack() as stack:
        report = stack.enter_context(StreamingReport()) if options.report else None
        try:
            limits = MboxLimits(max_message_bytes=max_message_mib * 1024 * 1024)
            results = stack.enter_context(closing(convert_mbox(
                source, output=root, options=options, limits=limits,
                unescape=unescape, bundles=bundles,
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
                    print(f"{item.source}: {item.error['code']}: {item.error['message']}", file=sys.stderr)
                if report is not None:
                    report.append(entry)
        except KeyboardInterrupt:
            interrupted = True
        except (OSError, ValueError) as exc:
            print(f"MBOX import failed: {exc}", file=sys.stderr)
            return 1
        if report is not None:
            try:
                report.finish(
                    root, options=options, input_path=str(source),
                    duration_ms=int((monotonic() - started) * 1000),
                    status="interrupted" if interrupted else "failed" if fatal else None,
                    import_options={"unescape": unescape, "bundles": bundles,
                                    "max_message_bytes": limits.max_message_bytes,
                                    "max_line_bytes": limits.max_line_bytes},
                )
            except OSError as exc:
                print(f"MBOX report could not be written: {exc}", file=sys.stderr)
                return 1
    print(f"MBOX: {total - failures} succeeded, {failures} errors" +
          (" (interrupted)" if interrupted else ""), file=sys.stderr)
    return 130 if interrupted else 1 if failures else 0
