"""Internal single-message worker. Not a public command or a security sandbox.

A budgeted worker is launched as a script by file path, so this module's top
level must import only the standard library: resource limits are applied before
any ``dead_letter`` package, MIME parser or native extension is imported.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Distinct exit statuses read by the parent (mbox_isolation). The parent trusts
# them only together with a matching nonce in the budget control file, so an
# ordinary exit with the same number cannot impersonate a budget outcome.
EXIT_BUDGET_APPLY_FAILED = 3
EXIT_MEMORY_LIMIT = 4
BUDGET_NAMES = ("memory_mib", "cpu_seconds", "max_output_mib")
BUDGET_STATUS_FILE = "budget-status.json"
NONCE_LENGTH = 32
_OUTCOMES = {EXIT_BUDGET_APPLY_FAILED: "apply_failed", EXIT_MEMORY_LIMIT: "memory_limit"}
_MIB = 1024 * 1024


def _parse_budgets(args: list[str]) -> tuple[dict[str, int], str | None]:
    # Parent-generated ``name=value`` pairs only; never derived from email.
    budgets: dict[str, int] = {}
    nonce: str | None = None
    for arg in args:
        name, sep, value = arg.partition("=")
        if name == "nonce" and sep and nonce is None:
            if len(value) != NONCE_LENGTH or not all(c in "0123456789abcdef" for c in value):
                raise ValueError("Invalid worker nonce")
            nonce = value
            continue
        if not sep or name not in BUDGET_NAMES or name in budgets or not (value.isascii() and value.isdigit()):
            raise ValueError("Invalid worker budget argument")
        budgets[name] = int(value)
        if budgets[name] <= 0:
            raise ValueError("Invalid worker budget argument")
    if bool(budgets) != (nonce is not None):
        raise ValueError("Worker budgets and nonce must be supplied together")
    return budgets, nonce


def _limit_pair(requested: tuple[int, int], inherited: tuple[int, int], infinity: int) -> tuple[int, int]:
    """Lower soft and hard limits to the request; never raise an inherited one."""
    return tuple(  # type: ignore[return-value]
        want if have == infinity else min(want, have) for want, have in zip(requested, inherited, strict=True)
    )


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

    for name, value in budgets.items():
        if name == "memory_mib":
            kind, requested = resource.RLIMIT_AS, (value * _MIB, value * _MIB)
        elif name == "cpu_seconds":
            # SIGXCPU at the soft limit terminates by default; the hard limit
            # one second later is the kernel's SIGKILL backstop.
            kind, requested = resource.RLIMIT_CPU, (value, value + 1)
        else:
            # RLIMIT_FSIZE bounds each file this process writes. Python ignores
            # SIGXFSZ at startup; restore the default so an oversized write ends
            # the worker instead of surfacing as an ordinary conversion error.
            # The launch also passes -B; this covers any later import.
            sys.dont_write_bytecode = True
            signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
            kind, requested = resource.RLIMIT_FSIZE, (value * _MIB, value * _MIB)
        limits = _limit_pair(requested, resource.getrlimit(kind), resource.RLIM_INFINITY)
        resource.setrlimit(kind, limits)
        if resource.getrlimit(kind) != limits:
            raise OSError("Worker budget was not applied")


class _MemoryErrorWatch:
    """Record every MemoryError raised in Python frames, even if later caught.

    The EML pipeline deliberately tolerates some failures (for example, an
    undecodable attachment is skipped). Under a memory budget such a recovery
    would silently publish incomplete output, so any observed MemoryError makes
    the whole record a memory-limit outcome. A MemoryError created and handled
    entirely inside native code never reaches a Python frame and is not seen.
    """

    def __init__(self) -> None:
        self.seen = False
        self._tool: int | None = None

    def __enter__(self) -> _MemoryErrorWatch:
        monitoring = sys.monitoring
        for tool in range(6):
            if monitoring.get_tool(tool) is None:
                monitoring.use_tool_id(tool, "dead-letter-memory-watch")
                self._tool = tool
                break
        else:
            raise RuntimeError("No free sys.monitoring tool id")
        monitoring.register_callback(self._tool, monitoring.events.RAISE, self._on_raise)
        monitoring.set_events(self._tool, monitoring.events.RAISE)
        return self

    def _on_raise(self, _code: object, _offset: int, exception: BaseException) -> None:
        # No allocation beyond a bool store: this can run under memory pressure.
        if isinstance(exception, MemoryError):
            self.seen = True

    def __exit__(self, *_exc: object) -> None:
        monitoring = sys.monitoring
        if self._tool is not None:
            monitoring.set_events(self._tool, monitoring.events.NO_EVENTS)
            monitoring.register_callback(self._tool, monitoring.events.RAISE, None)
            monitoring.free_tool_id(self._tool)
            self._tool = None


def _disable_core_dumps() -> None:
    # Do this before importing the MIME/native libraries. Best effort: OS crash
    # collectors and externally configured dump services remain host policy.
    if os.name == "posix":
        import resource
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (OSError, ValueError):
            pass


class _MemoryLimit(Exception):
    """A MemoryError was observed during a memory-budgeted conversion."""


def run(request_path: Path, budgets: dict[str, int] | None = None,
        watch: _MemoryErrorWatch | None = None) -> int:
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
    memory_budget = bool(budgets and "memory_mib" in budgets)
    if memory_budget and watch is None:
        raise RuntimeError("A memory budget requires a MemoryError watch")
    result = _convert_record(
        record, Path(request["archive_name"]), request_path.parent / "artifacts", options,
        bundles=request["bundles"], unescape=request["unescape"], archive=request.get("archive"),
        # Under a memory budget an allocation failure is a resource-limit
        # outcome for the parent to report, not an ordinary conversion error.
        reraise=(MemoryError,) if memory_budget else (),
    )
    if watch is not None and watch.seen:
        raise _MemoryLimit
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


def _run_budgeted(request_path: Path, budgets: dict[str, int], nonce: str) -> int:
    # Pre-open the tiny control file and pre-encode both outcomes before any
    # limit exists: signalling later needs no allocation, JSON encoding or new
    # file, and a ~70-byte write cannot reach the >= 1 MiB output budget.
    try:
        fd = os.open(request_path.parent / BUDGET_STATUS_FILE,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return 1
    payloads = {
        code: json.dumps({"nonce": nonce, "outcome": outcome}).encode("ascii")
        for code, outcome in _OUTCOMES.items()
    }

    def signal_outcome(code: int) -> int:
        try:
            if os.write(fd, payloads[code]) != len(payloads[code]):
                return 1
        except OSError:
            return 1
        return code

    watch = _MemoryErrorWatch() if "memory_mib" in budgets else None
    try:
        try:
            _apply_budgets(budgets)
            if watch is not None:
                # Detection is part of the memory guarantee: no free monitoring
                # tool id means the budget cannot be honored.
                watch.__enter__()
        except Exception:
            return signal_outcome(EXIT_BUDGET_APPLY_FAILED)
        try:
            return run(request_path, budgets, watch)
        except (MemoryError, _MemoryLimit):
            return signal_outcome(EXIT_MEMORY_LIMIT) if "memory_mib" in budgets else 1
        except KeyboardInterrupt:
            raise
        except BaseException:
            # Includes SystemExit(3)/(4) from conversion code: an ordinary crash,
            # never a budget outcome.
            return 1
    finally:
        if watch is not None:
            watch.__exit__()
        os.close(fd)


def main() -> int:
    try:
        if len(sys.argv) < 2:
            return 2
        budgets, nonce = _parse_budgets(sys.argv[2:])
    except ValueError:
        return 2
    _disable_core_dumps()
    if nonce is not None:
        return _run_budgeted(Path(sys.argv[1]), budgets, nonce)
    try:
        return run(Path(sys.argv[1]))
    except Exception:
        # The parent emits only a fixed safe error code; no private traceback.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
