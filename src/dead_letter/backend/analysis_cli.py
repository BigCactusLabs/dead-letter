"""Explicit EML and directory BYOK analysis; dry-run remains completely offline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # Argument strings can contain credentials or private paths. Unlike
        # argparse's default, never print them on an invalid invocation.
        raise _ArgumentError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input_path", help="One .eml file or a directory tree of .eml files")
    parser.add_argument("--provider", choices=("typesafe",), required=True,
                        help="Explicit opt-in to sending normalized email to this provider")
    parser.add_argument("--profile", choices=("triage-v1", "triage-choice-v1"), default="triage-v1")
    parser.add_argument("--identity", action="append", default=[],
                        help="Focus identity/alias; repeat for aliases of the same person")
    parser.add_argument("--model", help="Requested model identifier")
    parser.add_argument("--max-context-segments", type=int, default=3,
                        help="Maximum quoted/forwarded segments; excluded context is reported")
    parser.add_argument("--dry-run", action="store_true",
                        help="Local preview only; no network, SDK import or API key required")
    parser.add_argument("--show-state", action="store_true",
                        help="Include private normalized evidence locally; requires --dry-run")
    parser.add_argument("--output", help="Write or reuse a validated no-clobber analysis sidecar")
    parser.add_argument("--output-dir", help="Required destination for directory analysis")
    parser.add_argument("--jobs", default=None, help="Directory workers (1-16; default: 4)")
    parser.add_argument("--alias-max-age", type=float, default=86400,
                        help="Maximum reused alias age in seconds (default: 86400)")
    parser.add_argument("--timeout-seconds", type=float, default=15.0,
                        help="Per-operation HTTP timeout (default: 15 seconds)")
    parser.add_argument("--budget-seconds", type=float, default=45.0,
                        help="Total provider-call budget including retries (default: 45 seconds)")
    parser.add_argument("--max-retries", type=int, default=2,
                        help="SDK retries after initial request (0-3; default: 2)")


def _error(code: str, *, hint: str | None = None, discarded_fresh_result: bool = False) -> None:
    result = {"execution_status": "failed", "stage": "analysis", "error_code": code}
    if hint:
        result["hint"] = hint
    if discarded_fresh_result:
        result["discarded_fresh_result"] = True
    print(json.dumps(result, sort_keys=True), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="dead-letter analyze", allow_abbrev=False,
        description="Experimental BYOK analysis. Normalized text is sent remotely unless --dry-run is used.",
    )
    add_arguments(parser)
    try:
        args = parser.parse_args(argv)
    except _ArgumentError:
        _error("invalid_analysis_arguments", hint="Use dead-letter analyze --help.")
        return 2
    try:
        is_directory = Path(args.input_path).expanduser().is_dir()
    except (OSError, RuntimeError, ValueError):
        _error("analysis_output_invalid" if args.output is not None else "invalid_analysis_arguments")
        return 2
    if is_directory:
        if args.output_dir is None:
            _error("analysis_output_dir_required")
            return 2
        if args.output is not None or args.dry_run or args.show_state:
            _error("invalid_analysis_arguments")
            return 2
    elif args.output_dir is not None or args.jobs is not None:
        _error("invalid_analysis_arguments")
        return 2
    if args.jobs is not None:
        if not args.jobs.isascii() or not args.jobs.isdecimal():
            _error("invalid_analysis_jobs")
            return 2
        try:
            args.jobs = int(args.jobs)
        except ValueError:
            _error("invalid_analysis_jobs")
            return 2
        if not 1 <= args.jobs <= 16:
            _error("invalid_analysis_jobs")
            return 2
    if args.show_state and not args.dry_run:
        _error("show_state_requires_dry_run")
        return 2
    if args.dry_run and args.output is not None:
        _error("invalid_analysis_arguments", hint="--output cannot be combined with --dry-run.")
        return 2

    from dead_letter.analysis import AnalysisError, prepare_eml
    from dead_letter.analysis.contracts import DEFAULT_BASE_URL, DEFAULT_MODEL
    from dead_letter.analysis.providers.typesafe import TypeSafeConfig

    try:
        config = TypeSafeConfig(
            base_url=os.environ.get("TYPESAFE_BASE_URL", DEFAULT_BASE_URL),
            timeout_seconds=args.timeout_seconds, budget_seconds=args.budget_seconds,
            max_retries=args.max_retries,
        )
        options = dict(profile_name=args.profile, focus_identity=tuple(args.identity),
                       max_context_segments=args.max_context_segments,
                       model=DEFAULT_MODEL if args.model is None else args.model)
        if is_directory:
            from dead_letter.analysis import analyze_directory

            async def run_directory():
                try:
                    return await analyze_directory(
                        args.input_path, args.output_dir, provider=args.provider, allow_remote=True,
                        jobs=4 if args.jobs is None else args.jobs, config=config, alias_max_age=args.alias_max_age, **options,
                    )
                except asyncio.CancelledError as exc:
                    summary = getattr(exc, "dead_letter_summary", None)
                    if summary is None:
                        raise
                    return summary

            result = asyncio.run(run_directory())
        elif args.dry_run:
            prepared = prepare_eml(args.input_path, base_url=config.base_url, **options)
            result = prepared.preview(include_state=args.show_state)
        elif args.output is not None:
            from dead_letter.analysis import analyze_to_sidecar
            result = asyncio.run(analyze_to_sidecar(
                args.input_path, args.output, provider=args.provider, allow_remote=True,
                config=config, alias_max_age=args.alias_max_age, **options,
            ))
        else:
            from dead_letter.analysis.service import analyze_eml
            result = asyncio.run(analyze_eml(args.input_path, provider=args.provider,
                                            allow_remote=True, config=config, **options))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True))
        if is_directory:
            if result["status"] == "interrupted":
                return 130
            return 1 if result["counts"]["failed"] or result["status"] == "stopped" else 0
        return 1 if (result["execution_status"] == "failed"
                     or result.get("sidecar", {}).get("outcome") == "write_failed") else 0
    except AnalysisError as exc:
        hint = None
        if exc.code in {"analysis_output_corrupt", "analysis_output_mismatch",
                        "analysis_output_stale_alias"}:
            hint = "Choose another output path or remove the existing file."
        _error(exc.code, hint=hint,
               discarded_fresh_result=getattr(exc, "discarded_fresh_result", False))
        return 2 if exc.code in {"analysis_output_invalid", "invalid_alias_max_age",
                                  "analysis_output_inside_source", "invalid_analysis_jobs"} else 1
    except KeyboardInterrupt:
        _error("analysis_interrupted")
        return 130
