"""Internal single-message worker. Not a public command or a security sandbox."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Distinct exit statuses read by the parent (mbox_isolation). A worker that was
# asked for a budget it could not apply must not convert anything.
EXIT_BUDGET_APPLY_FAILED = 3
EXIT_MEMORY_LIMIT = 4
BUDGET_NAMES = ("memory_mib", "cpu_seconds", "max_output_mib")
_MIB = 1024 * 1024


def _parse_budgets(args: list[str]) -> dict[str, int]:
    # Parent-generated ``name=value`` pairs only; never derived from email.
    budgets: dict[str, int] = {}
    for arg in args:
        name, sep, value = arg.partition("=")
        if not sep or name not in BUDGET_NAMES or name in budgets or not (value.isascii() and value.isdigit()):
            raise ValueError("Invalid worker budget argument")
        budgets[name] = int(value)
        if budgets[name] <= 0:
            raise ValueError("Invalid worker budget argument")
    return budgets


def _apply_budgets(budgets: dict[str, int]) -> None:
    """Limit this process before it imports parsers or reads the staged EML.

    Applied by the worker itself rather than a ``preexec_fn``, which CPython
    documents as unsafe in threaded parents. Any failure is raised so the parent
    can abort instead of converting without the requested guarantee.
    """
    if not budgets:
        return
    import resource
    import signal

    def cap(value: int, hard: int) -> int:
        # Never raise an existing, stricter host hard limit.
        return value if hard == resource.RLIM_INFINITY or hard > value else hard

    for name, value in budgets.items():
        if name == "memory_mib":
            kind, amount = resource.RLIMIT_AS, value * _MIB
            limits = (cap(amount, resource.getrlimit(kind)[1]),) * 2
        elif name == "cpu_seconds":
            # SIGXCPU at the soft limit terminates by default; the hard limit
            # one second later is the kernel's SIGKILL backstop.
            kind, hard = resource.RLIMIT_CPU, resource.getrlimit(resource.RLIMIT_CPU)[1]
            limits = (cap(value, hard), cap(value + 1, hard))
        else:
            # RLIMIT_FSIZE bounds each file this process writes. Python ignores
            # SIGXFSZ at startup; restore the default so an oversized write ends
            # the worker instead of surfacing as an ordinary conversion error.
            # Bytecode caching is disabled so lazy imports never hit the limit.
            sys.dont_write_bytecode = True
            signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
            kind, amount = resource.RLIMIT_FSIZE, value * _MIB
            limits = (cap(amount, resource.getrlimit(kind)[1]),) * 2
        resource.setrlimit(kind, limits)
        if resource.getrlimit(kind) != limits:
            raise OSError("Worker budget was not applied")


def _disable_core_dumps() -> None:
    # Do this before importing the MIME/native libraries. Best effort: OS crash
    # collectors and externally configured dump services remain host policy.
    if os.name == "posix":
        import resource
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (OSError, ValueError):
            pass


def run(request_path: Path, budgets: dict[str, int] | None = None) -> int:
    from dead_letter.core.mbox import MboxRecord
    from dead_letter.core.mbox_import import _convert_record
    from dead_letter.core.mbox_isolation import MAX_RECEIPT_BYTES
    from dead_letter.core.report import _sanitize_value
    from dead_letter.core.types import ConvertOptions

    # Only the parent creates requests in a fresh private directory. There are
    # no email-controlled flags, environment expansion, commands, or endpoints.
    request = json.loads(request_path.read_text(encoding="utf-8"))
    fields = request["record"]
    fields["path"] = Path(fields["path"])
    record = MboxRecord(**fields)
    options = ConvertOptions(**request["options"])
    if options.delete_eml:
        raise ValueError("A worker must never delete its input")
    result = _convert_record(
        record, Path(request["archive_name"]), request_path.parent / "artifacts", options,
        bundles=request["bundles"], unescape=request["unescape"],
        # Under a memory budget an allocation failure is a resource-limit
        # outcome for the parent to report, not an ordinary conversion error.
        reraise=(MemoryError,) if budgets and "memory_mib" in budgets else (),
    )
    # A receipt carries only status/diagnostics, never a serialized MIME object
    # or a path for the parent to follow. Exit without a receipt on oversize.
    receipt = {
        "schema_version": 1, "index": record.index, "sha256": record.sha256,
        "success": result.success, "diagnostics": result.diagnostics,
        "error_code": result.error["code"] if result.error else None,
    }
    raw = json.dumps(_sanitize_value(receipt), ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_RECEIPT_BYTES:
        return 0
    (request_path.parent / "receipt.json").write_bytes(raw)
    return 0


def main() -> int:
    try:
        if len(sys.argv) < 2:
            return 2
        budgets = _parse_budgets(sys.argv[2:])
    except ValueError:
        return 2
    _disable_core_dumps()
    try:
        _apply_budgets(budgets)
    except Exception:
        return EXIT_BUDGET_APPLY_FAILED
    try:
        return run(Path(sys.argv[1]), budgets)
    except MemoryError:
        return EXIT_MEMORY_LIMIT if "memory_mib" in budgets else 1
    except Exception:
        # The parent emits only a fixed safe error code; no private traceback.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
