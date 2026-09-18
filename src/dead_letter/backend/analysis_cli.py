"""Experimental local preview command; no provider transport is enabled."""

from __future__ import annotations

import argparse
import json
import os
import sys


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # Argument strings can contain credentials or private paths. Unlike
        # argparse's default, never print them on an invalid invocation.
        raise _ArgumentError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input_path", help="One .eml file; directory analysis is not enabled yet")
    parser.add_argument("--provider", choices=("typesafe",), required=True)
    parser.add_argument("--profile", choices=("triage-v1", "triage-choice-v1"), default="triage-v1")
    parser.add_argument("--identity", action="append", default=[],
                        help="Focus identity/alias; repeat for aliases of the same person")
    parser.add_argument("--model", help="Requested model identifier for the prepared request")
    parser.add_argument("--max-context-segments", type=int, default=3,
                        help="Maximum quoted/forwarded segments; excluded context is reported")
    parser.add_argument("--dry-run", action="store_true",
                        help="Required in this experimental slice; makes no remote request")
    parser.add_argument("--show-state", action="store_true",
                        help="Explicitly include private normalized text/metadata in local JSON")


def _error(code: str, *, hint: str | None = None) -> None:
    result = {"execution_status": "failed", "stage": "preparation", "error_code": code}
    if hint:
        result["hint"] = hint
    print(json.dumps(result, sort_keys=True), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="dead-letter analyze", allow_abbrev=False,
        description="Prepare experimental analysis locally. This build supports --dry-run only.",
    )
    add_arguments(parser)
    try:
        args = parser.parse_args(argv)
    except _ArgumentError:
        _error("invalid_analysis_arguments", hint="Use dead-letter analyze --help.")
        return 2
    if not args.dry_run:
        _error("remote_analysis_not_implemented", hint="Use --dry-run for local preview.")
        return 2

    # Neither help, rejected arguments nor conversion dispatch imports analysis.
    from dead_letter.analysis import AnalysisError, prepare_eml
    from dead_letter.analysis.contracts import DEFAULT_BASE_URL, DEFAULT_MODEL

    try:
        prepared = prepare_eml(
            args.input_path, profile_name=args.profile, focus_identity=tuple(args.identity),
            max_context_segments=args.max_context_segments,
            model=DEFAULT_MODEL if args.model is None else args.model,
            base_url=os.environ.get("TYPESAFE_BASE_URL", DEFAULT_BASE_URL),
        )
        print(json.dumps(prepared.preview(include_state=args.show_state),
                         ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True))
    except AnalysisError as exc:
        _error(exc.code)
        return 1
    except KeyboardInterrupt:
        _error("analysis_interrupted")
        return 130
    return 0
