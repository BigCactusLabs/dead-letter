"""CLI entrypoint for dead-letter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import monotonic

from dead_letter.core import ConvertOptions
from dead_letter.core import convert as core_convert
from dead_letter.core import convert_dir as core_convert_dir
from dead_letter.core.report import ReportEntry, build_report, write_report
from dead_letter.core.types import ConvertResult

SUBCOMMANDS = frozenset({"convert", "doctor"})


def _add_convert_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input_path", help="Path to .eml file or directory")
    parser.add_argument("--output", help="Output file or directory")
    parser.add_argument("--strip-signatures", action="store_true")
    parser.add_argument("--strip-disclaimers", action="store_true")
    parser.add_argument("--strip-quoted-headers", action="store_true")
    parser.add_argument("--strip-signature-images", action="store_true")
    parser.add_argument("--strip-tracking-pixels", action="store_true")
    parser.add_argument("--embed-inline-images", action="store_true")
    parser.add_argument("--include-all-headers", action="store_true")
    parser.add_argument("--include-raw-html", action="store_true")
    parser.add_argument("--no-calendar-summary", action="store_true")
    parser.add_argument("--allow-fallback-on-html-error", action="store_true")
    parser.add_argument("--allow-html-repair-on-panic", action="store_true")
    parser.add_argument("--delete-eml", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", action="store_true", help="Write conversion report to output directory")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dead-letter",
        description="Convert .eml files to Markdown.",
    )
    subs = parser.add_subparsers(dest="command")

    convert_parser = subs.add_parser("convert", help="Convert .eml files to Markdown")
    _add_convert_flags(convert_parser)

    doctor_parser = subs.add_parser("doctor", help="Check runtime environment")
    doctor_parser.add_argument("--json", action="store_true", help="Output as JSON")
    return parser


def _to_core_options(args: argparse.Namespace) -> ConvertOptions:
    return ConvertOptions(
        strip_signatures=args.strip_signatures,
        strip_disclaimers=args.strip_disclaimers,
        strip_quoted_headers=args.strip_quoted_headers,
        strip_signature_images=args.strip_signature_images,
        strip_tracking_pixels=args.strip_tracking_pixels,
        embed_inline_images=args.embed_inline_images,
        include_all_headers=args.include_all_headers,
        include_raw_html=args.include_raw_html,
        no_calendar_summary=args.no_calendar_summary,
        allow_fallback_on_html_error=args.allow_fallback_on_html_error,
        allow_html_repair_on_panic=args.allow_html_repair_on_panic,
        delete_eml=args.delete_eml and not args.dry_run,
        dry_run=args.dry_run,
        report=args.report,
    )


def _report_directory(input_path: Path, output: str | Path | None) -> Path:
    if output is None:
        return input_path if input_path.is_dir() else input_path.parent

    output_path = Path(output).expanduser()
    if output_path.suffix.lower() == ".md":
        return output_path.parent
    return output_path


def _report_status(results: list[ConvertResult]) -> str:
    failures = sum(1 for item in results if not item.success)
    successes = len(results) - failures
    if failures and successes:
        return "completed_with_errors"
    if failures:
        return "failed"
    return "succeeded"


def _report_source(result: ConvertResult, *, input_path: Path, mode: str) -> str:
    if mode == "directory":
        try:
            return result.source.relative_to(input_path).as_posix()
        except ValueError:
            pass
    return result.source.name


def _report_output(result: ConvertResult, *, report_dir: Path) -> str | None:
    if result.output is None:
        return None
    try:
        return result.output.relative_to(report_dir).as_posix()
    except ValueError:
        return str(result.output)


def _write_cli_report(
    *,
    input_path: Path,
    mode: str,
    output: str | Path | None,
    options: ConvertOptions,
    results: list[ConvertResult],
    duration_ms: int,
) -> None:
    report_dir = _report_directory(input_path, output).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    entries = [
        ReportEntry(
            source=_report_source(result, input_path=input_path, mode=mode),
            output=_report_output(result, report_dir=report_dir),
            success=result.success,
            error=None
            if result.success
            else {
                "code": result.error_code or "conversion_error",
                "message": result.error or "unknown conversion error",
                "stage": "core",
            },
        )
        for result in results
    ]
    report = build_report(
        entries=entries,
        options=options,
        job_id="cli",
        job_status=_report_status(results),
        duration_ms=duration_ms,
        input_path=str(input_path),
        input_mode=mode,
        total=len(results),
    )
    write_report(report, report_dir)


def _run_convert(args: argparse.Namespace) -> int:
    input_path = Path(args.input_path).expanduser().resolve()
    options = _to_core_options(args)
    started_at = monotonic()

    if input_path.is_dir():
        results = core_convert_dir(input_path, output=args.output, options=options)
        if options.report:
            _write_cli_report(
                input_path=input_path,
                mode="directory",
                output=args.output,
                options=options,
                results=results,
                duration_ms=int((monotonic() - started_at) * 1000),
            )
        failures = sum(1 for item in results if not item.success)
        return 1 if failures else 0

    result = core_convert(input_path, output=args.output, options=options)
    if options.report:
        _write_cli_report(
            input_path=input_path,
            mode="file",
            output=args.output,
            options=options,
            results=[result],
            duration_ms=int((monotonic() - started_at) * 1000),
        )
    return 0 if result.success else 1


def _run_doctor(args: argparse.Namespace) -> int:
    from dead_letter.backend.doctor import run_doctor
    return run_doctor(json_output=args.json)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    # Backward compat: bare path → implicit convert
    if argv and argv[0] not in SUBCOMMANDS and not argv[0].startswith("-"):
        argv = ["convert", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "convert":
        return _run_convert(args)
    if args.command == "doctor":
        return _run_doctor(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
