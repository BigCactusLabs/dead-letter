"""Measure and audit a full MBOX import; run each mode in a fresh invocation.

Examples (from a checkout installed with uv sync --extra dev):
  uv run python scripts/benchmark_mbox_import.py --mode direct --bundles > direct.json
  uv run python scripts/benchmark_mbox_import.py --mode worker --bundles > worker.json
  uv run python scripts/benchmark_mbox_import.py --compare direct.json worker.json

All conversion output is temporary. --archive explicitly opts into local private
mail processing; summaries contain content-derived fingerprints, never mail text
or paths. Keep those summaries private too. See docs/reference/mbox-validation.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import stat
import sys
import time
import tracemalloc
from collections import Counter
from contextlib import closing, contextmanager
from email.message import EmailMessage
from email.policy import SMTP
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

CHUNK = 64 * 1024
SUMMARY_LIMIT = 256 * 1024
POSTMARK = b"From benchmark@example.test Thu Jun 11 00:38:38 +0000 2020\n"


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be a positive integer")
    return number


def positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Must be finite and positive")
    return number


def write_corpus(path: Path, count: int, attachment_kib: int) -> None:
    """Deterministic, non-sparse corpus; memory scales with one message, not count."""
    with path.open("xb") as out:
        for index in range(count):
            message = EmailMessage(policy=SMTP)
            message["From"] = "Alice <alice@example.test>"
            message["To"] = "Bob <bob@example.test>"
            message["Date"] = "Thu, 11 Jun 2020 00:38:38 +0000"
            message["Subject"] = "Repeated subject / Caf\u00e9"
            message["Message-ID"] = f"<benchmark-{index}@example.test>"
            message["X-Gmail-Labels"] = "Inbox,Projects/Benchmark,Important"
            message["X-GM-THRID"] = "1669160939957813740"
            kind = index % 4
            if kind == 0:
                message.set_content("Hello from the benchmark.\nFrom the engineering team.\n")
            elif kind == 1:
                message.set_content("Please review the HTML version.")
                quoted = "<p>Earlier review and measurements.</p>" * 32
                message.add_alternative(
                    '<p>Latest reply.</p><div class="gmail_quote"><blockquote>'
                    + quoted + "</blockquote></div>", subtype="html",
                )
                message.set_boundary(f"benchmark-alternative-{index}")
            elif kind == 2:
                message.set_content("Attached are the synthetic binary measurements.")
                message.add_attachment(
                    bytes(range(256)) * (attachment_kib * 4),
                    maintype="application", subtype="octet-stream", filename="measurements.bin",
                )
                message.set_boundary(f"benchmark-mixed-{index}")
            else:
                message.set_content(
                    "Approved. Use the final version.\n\n"
                    "On Thu, Jun 11, 2020 Alice <alice@example.test> wrote:\n"
                    + "> Earlier discussion and measurements.\n" * 128
                )
            out.write(POSTMARK)
            # Mboxo-style escaping makes this corpus readable by the stdlib
            # oracle as well as our stricter postmark grammar.
            out.write(message.as_bytes().replace(b"\nFrom ", b"\n>From "))
            out.write(b"\n")


def hash_stream(stream: Any, size: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while size is None or total < size:
        chunk = stream.read(CHUNK if size is None else min(CHUNK, size - total))
        if not chunk:
            if size is not None and total != size:
                raise ValueError("Short source range")
            break
        total += len(chunk)
        digest.update(chunk)
    return digest.hexdigest(), total


def hash_file(path: Path) -> tuple[str, int]:
    with path.open("rb") as stream:
        return hash_stream(stream)


def add_digest(digest: Any, value: Any) -> None:
    """Length-delimited canonical JSON prevents ambiguous digest concatenation."""
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def verify_range(stream: Any, provenance: dict[str, Any], index: int, previous_end: int) -> str:
    fields = ("index", "envelope_offset", "message_offset", "end_offset", "stored_bytes")
    if any(type(provenance.get(key)) is not int for key in fields):
        raise ValueError("Invalid range metadata")
    begin, end = provenance["message_offset"], provenance["end_offset"]
    if (provenance["index"] != index or provenance["envelope_offset"] != previous_end
            or not previous_end < begin <= end or provenance["stored_bytes"] != end - begin):
        raise ValueError("Inconsistent source ranges")
    stream.seek(begin)
    digest, _ = hash_stream(stream, end - begin)
    if digest != provenance.get("sha256"):
        raise ValueError("Source checksum mismatch")
    return digest


def artifact_digest(output: Path, root: Path, bundles: bool, digest: Any, index: int,
                    stored_hash: str | None) -> tuple[int, int, int]:
    """Audit one record's files. Never enumerate the whole output directory."""
    output.relative_to(root)
    base = output.parent if bundles else root
    files = [output]
    if bundles:
        files = []
        for directory, dirs, names in os.walk(base, followlinks=False):
            dirs.sort()
            for name in dirs:
                if (Path(directory) / name).is_symlink():
                    raise ValueError("Linked artifact directory")
            files.extend(Path(directory) / name for name in sorted(names))
    total = source_copies = 0
    for path in files:
        if not stat.S_ISREG(path.lstat().st_mode) or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Non-regular or escaping artifact")
        checksum, size = hash_file(path)
        name = path.relative_to(base).as_posix()
        add_digest(digest, [index, name, size, checksum])
        total += size
        if bundles and name == "source.eml" and stored_hash is not None:
            if checksum != stored_hash:
                raise ValueError("Extracted source differs from original stored bytes")
            source_copies += 1
    if bundles and stored_hash is not None and source_copies != 1:
        raise ValueError("Missing source copy")
    return len(files), total, source_copies


def rss_metrics() -> dict[str, int | str | None]:
    """Native platform units; child high-water mark is NOT a process-tree total."""
    metrics: dict[str, int | str | None] = {
        "importer_peak_rss_bytes": None, "largest_reaped_worker_peak_rss_bytes": None,
        "rss_method": "unavailable_on_this_platform",
    }
    if sys.platform.startswith("linux"):
        import resource
        metrics.update(
            importer_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            largest_reaped_worker_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024,
            rss_method="linux_getrusage_separate_high_water_marks_not_tree_total",
        )
    elif sys.platform == "darwin":
        import resource
        metrics.update(
            importer_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            rss_method="darwin_getrusage_self_bytes_children_unavailable",
        )
    return metrics


def run_trial(source: Path, root: Path, args: argparse.Namespace) -> dict[str, Any]:
    imports = time.perf_counter()
    from dead_letter.core.mbox import MboxLimits
    from dead_letter.core.mbox_import import convert_mbox
    from dead_letter.core.stream_report import StreamingReport
    from dead_letter.core.types import ConvertOptions, ThreadMode
    import_seconds = time.perf_counter() - imports
    before_hash, source_bytes = hash_file(source)  # Intentionally outside timing; warms file cache.
    semantic, artifacts = hashlib.sha256(), hashlib.sha256()
    options = ConvertOptions(report=not args.no_report, thread_mode=ThreadMode.STRUCTURED)
    counts: Counter[str] = Counter(records=0, written=0, errors=0, archive_errors=0,
                                  artifact_files=0, artifact_bytes=0, ranges_verified=0,
                                  source_copies_verified=0)
    conversion_seconds = report_seconds = audit_seconds = 0.0
    first_result_seconds = None
    previous_end = 0
    if args.trace_allocations:
        tracemalloc.start()
    started = time.perf_counter()
    try:
        with source.open("rb") as reference, closing(convert_mbox(
            source, output=root, options=options,
            limits=MboxLimits(max_message_bytes=args.max_message_mib * 1024**2),
            unescape=args.unescape, bundles=args.bundles,
            timeout_seconds=args.worker_timeout if args.mode == "worker" else None,
        )) as rows, StreamingReport() as report:
            while True:
                tick = time.perf_counter()
                try:
                    item = next(rows)
                except StopIteration:
                    conversion_seconds += time.perf_counter() - tick
                    break
                conversion_seconds += time.perf_counter() - tick
                if first_result_seconds is None:
                    first_result_seconds = time.perf_counter() - started
                entry = {"source": item.source, "output": None, "success": item.success}
                if item.output is not None:
                    entry["output"] = item.output.relative_to(root).as_posix()
                for key in ("mbox", "diagnostics", "error"):
                    value = getattr(item, key)
                    if value is not None:
                        entry[key] = value
                tick = time.perf_counter()
                if options.report:
                    report.append(entry)
                report_seconds += time.perf_counter() - tick
                tick = time.perf_counter()
                counts["errors"] += not item.success
                stored_hash = None
                if item.mbox is None:
                    counts["archive_errors"] += 1
                else:
                    counts["records"] += 1
                    stored_hash = verify_range(reference, item.mbox, counts["records"], previous_end)
                    previous_end = item.mbox["end_offset"]
                    counts["ranges_verified"] += 1
                add_digest(semantic, {
                    "mbox": item.mbox, "success": item.success, "diagnostics": item.diagnostics,
                    "error_code": item.error.get("code") if item.error else None,
                })
                if item.output is not None:
                    if not item.success:
                        raise ValueError("Failed result published an output")
                    files, size, copies = artifact_digest(
                        item.output, root, args.bundles, artifacts, counts["records"],
                        stored_hash if args.unescape == "preserve" else None,
                    )
                    counts["written"] += 1
                    counts["artifact_files"] += files
                    counts["artifact_bytes"] += size
                    counts["source_copies_verified"] += copies
                audit_seconds += time.perf_counter() - tick
            tick = time.perf_counter()
            report_bytes = 0
            if options.report:
                target = report.finish(
                    root, options=options, input_path=str(source), duration_ms=int(
                        (conversion_seconds + report_seconds) * 1000),
                    status="failed" if counts["archive_errors"] else None,
                    import_options={"unescape": args.unescape, "bundles": args.bundles,
                                    "timeout_seconds": args.worker_timeout if args.mode == "worker" else None},
                )
                report_bytes = target.stat().st_size
            report_seconds += time.perf_counter() - tick
        wall_seconds = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1] if args.trace_allocations else None
    finally:
        if args.trace_allocations:
            tracemalloc.stop()
    if hash_file(source) != (before_hash, source_bytes):
        raise ValueError("Archive changed across validation")
    if not counts["archive_errors"] and previous_end != source_bytes:
        raise ValueError("Unaccounted source bytes")
    if args.archive is None and counts["records"] != (args.messages or 40):
        raise ValueError("Synthetic corpus record count mismatch")
    return {
        "schema_version": 1, "kind": "mbox_import_measurement", "mode": args.mode,
        "python": platform.python_version(), "platform": sys.platform,
        "versions": {name: version(name) for name in ("dead-letter", "html-to-markdown", "mail-parser")},
        "corpus": {"kind": "local_archive" if args.archive else "synthetic_mixed_v1",
                   "bytes": source_bytes, "sha256": before_hash},
        "config": {"bundles": args.bundles, "report": not args.no_report,
                   "unescape": args.unescape, "thread_mode": "structured",
                   "max_message_mib": args.max_message_mib},
        "timeout_seconds": args.worker_timeout if args.mode == "worker" else None,
        "counts": dict(counts), "clean": counts["errors"] == 0,
        "checksums": {"ordered_results": semantic.hexdigest(), "ordered_artifacts": artifacts.hexdigest()},
        "timing": {"dependency_import_seconds": import_seconds,
                   "conversion_seconds": conversion_seconds, "report_seconds": report_seconds,
                   "audit_seconds": audit_seconds, "measured_wall_seconds": wall_seconds,
                   "first_result_seconds": first_result_seconds,
                   "records_per_conversion_second": counts["records"] / conversion_seconds if conversion_seconds else None},
        "memory": {**rss_metrics(), "traced_importer_peak_bytes": peak,
                   "tracemalloc_enabled": args.trace_allocations},
        "report_bytes": report_bytes,
    }


def read_summary(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(SUMMARY_LIMIT + 1)
    if len(raw) > SUMMARY_LIMIT:
        raise ValueError("Summary too large")
    def unique_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate summary field")
            result[key] = item
        return result

    def reject_constant(_value):
        raise ValueError("Non-finite summary value")

    value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("kind") != "mbox_import_measurement":
        raise ValueError("Not a supported measurement")
    for key in ("corpus", "config", "counts", "checksums", "timing", "memory", "versions"):
        if not isinstance(value.get(key), dict):
            raise ValueError("Incomplete measurement")
    for key in ("python", "platform", "mode"):
        if not isinstance(value.get(key), str):
            raise ValueError("Incomplete measurement")
    if value["mode"] not in {"direct", "worker"}:
        raise ValueError("Unknown measurement mode")
    for key in ("records", "written", "errors", "archive_errors", "artifact_files", "artifact_bytes",
                "ranges_verified", "source_copies_verified"):
        number = value["counts"].get(key)
        if type(number) is not int or number < 0:
            raise ValueError("Invalid measurement count")
    if type(value.get("clean")) is not bool or value["clean"] != (value["counts"]["errors"] == 0):
        raise ValueError("Inconsistent success flag")
    for key in ("conversion_seconds", "report_seconds", "audit_seconds", "measured_wall_seconds"):
        number = value["timing"].get(key)
        if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
            raise ValueError("Invalid measurement duration")
    if type(value["memory"].get("tracemalloc_enabled")) is not bool:
        raise ValueError("Missing tracing status")
    for checksum in (value["corpus"].get("sha256"), value["checksums"].get("ordered_results"),
                     value["checksums"].get("ordered_artifacts")):
        if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise ValueError("Invalid measurement checksum")
    return value


def compare(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    compatible = all(left[key] == right[key] for key in ("corpus", "config", "versions", "python", "platform"))
    parity = compatible and all(left[key] == right[key] for key in ("counts", "checksums"))
    clean = left.get("clean") is True and right.get("clean") is True
    # Timing is descriptive only. Never reward skipping failed messages with a speedup.
    timing_comparable = (parity and clean and
                         left["memory"]["tracemalloc_enabled"] == right["memory"]["tracemalloc_enabled"])
    return {"schema_version": 1, "kind": "mbox_import_comparison", "compatible": compatible,
            "parity": parity, "clean": clean, "timing_comparable": timing_comparable,
            "right_over_left_conversion_ratio": (
                right["timing"]["conversion_seconds"] / left["timing"]["conversion_seconds"]
                if timing_comparable and left["timing"]["conversion_seconds"] > 0 else None),
            "note": "Single-run ratio, not a statistical performance claim; report hashes exclude run timestamps."}


@contextmanager
def quiet_stdio():
    """Discard Python/native parser output in this standalone process, not just logs."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    try:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            try:
                yield
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
    finally:
        for fd, original in zip((1, 2), saved):
            os.dup2(original, fd)
            os.close(original)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("LEFT", "RIGHT"))
    parser.add_argument("--mode", choices=("direct", "worker"), default="direct")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--archive", type=Path, help="Explicit local mail opt-in; no source modification")
    inputs.add_argument("--messages", type=positive_int, help="Synthetic records (default: 40)")
    parser.add_argument("--attachment-kib", type=positive_int, default=256, help="Synthetic attachment size")
    parser.add_argument("--bundles", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--unescape", choices=("preserve", "mboxrd", "mboxo"), default="preserve")
    parser.add_argument("--max-message-mib", type=positive_int, default=64)
    parser.add_argument("--worker-timeout", type=positive_seconds, default=30.0)
    parser.add_argument("--trace-allocations", action="store_true", help="Importer only; changes timing")
    parser.add_argument("--work-dir", type=Path, help="Existing disk directory for temporary outputs")
    args = parser.parse_args(argv)
    try:
        if args.compare:
            result = compare(*(read_summary(path) for path in args.compare))
            code = 0 if result["parity"] and result["clean"] else 1
        else:
            with TemporaryDirectory(prefix="dead-letter-validate-", dir=args.work_dir) as temporary:
                workspace = Path(temporary).resolve()
                source = args.archive.expanduser().resolve() if args.archive else workspace / "synthetic.mbox"
                if args.archive is None:
                    write_corpus(source, args.messages or 40, args.attachment_kib)
                with quiet_stdio():
                    result = run_trial(source, workspace / "output", args)
                code = 0 if result["clean"] else 1
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return code
    except KeyboardInterrupt:
        print('{"kind": "mbox_import_measurement_error", "error": "interrupted"}')
        return 130
    except Exception as exc:
        # Never print exception values: parsers and filesystem errors can expose private mail/paths.
        print(json.dumps({"kind": "mbox_import_measurement_error", "error": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
