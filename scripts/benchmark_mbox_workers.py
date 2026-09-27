"""Bounded synthetic fresh-worker matrix; see docs/reference/mbox-validation.md."""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import benchmark_mbox_import as audit

PROFILES = ("small", "large", "attachment-heavy", "html-thread-heavy")


def write_corpus(path: Path, profile: str, messages: int) -> None:
    """Fixed MIME boundaries and content; no private input or random payloads."""
    from email.message import EmailMessage
    from email.policy import SMTP

    with path.open("xb") as out:
        for index in range(messages):
            message = EmailMessage(policy=SMTP)
            for name, value in {
                "From": "alice@example.test", "To": "bob@example.test",
                "Date": "Thu, 11 Jun 2020 00:38:38 +0000",
                "Subject": f"Synthetic {profile}",
                "Message-ID": f"<{profile}-{index}@example.test>",
                "X-Gmail-Labels": "Inbox,Measurements",
            }.items():
                message[name] = value
            if profile == "small":
                message.set_content("A short synthetic message.\n")
            elif profile == "large":
                message.set_content("A bounded large plain text paragraph.\n" * 28000)
            elif profile == "attachment-heavy":
                message.set_content("An eight MiB synthetic attachment.\n")
                message.add_attachment(bytes(range(256)) * 32768, maintype="application",
                                       subtype="octet-stream", filename="synthetic.bin")
                message.set_boundary(f"synthetic-mixed-{index}")
            elif profile == "html-thread-heavy":
                message.set_content("Please read the HTML conversation.")
                message.add_alternative(
                    '<p>Latest response.</p><div class="gmail_quote"><blockquote>'
                    + '<p>Earlier <b>discussion</b> and <a href="https://example.test">reference</a>.</p>' * 1200
                    + '</blockquote></div>', subtype="html")
                message.set_boundary(f"synthetic-alternative-{index}")
            else:
                raise ValueError("Unknown synthetic profile")
            out.write(audit.POSTMARK)
            out.write(message.as_bytes().replace(b"\nFrom ", b"\n>From "))
            out.write(b"\n")


def disk_bytes(root: Path) -> dict[str, int | None]:
    sizes = {"logical_bytes": 0, "allocated_bytes": 0 if hasattr(root.stat(), "st_blocks") else None}
    for path in root.rglob("*"):
        if path.is_file():
            info = path.stat()
            sizes["logical_bytes"] += info.st_size
            if sizes["allocated_bytes"] is not None:
                sizes["allocated_bytes"] += info.st_blocks * 512
    return sizes


def trial(source: Path, root: Path, mode: str, bundles: bool) -> dict:
    # Let the existing harness measure dependency imports before installing hooks.
    tick = time.perf_counter()
    from dead_letter.core import mbox_isolation as isolation
    from dead_letter.core import mbox_import
    imports = time.perf_counter() - tick
    metrics = {"worker_lifetime_seconds": 0.0, "parent_publication_seconds": 0.0,
               "disk_observation_seconds": 0.0, "worker_launches": 0,
               "direct_record_conversion_seconds": None if mode == "worker" else 0.0,
               "temporary_checkpoint_max_logical_bytes": 0,
               "temporary_checkpoint_max_allocated_bytes": 0 if hasattr(source.stat(), "st_blocks") else None}
    run_worker, publish = isolation._run_worker, isolation._publish
    convert_record = mbox_import._convert_record

    def measured_record(record, *args, **kwargs):
        started = time.perf_counter()
        try:
            return convert_record(record, *args, **kwargs)
        finally:
            metrics["direct_record_conversion_seconds"] += time.perf_counter() - started
            started = time.perf_counter()
            if record.path is not None:
                info = record.path.stat()
                for kind, value in (("logical_bytes", info.st_size),
                                    ("allocated_bytes", info.st_blocks * 512 if hasattr(info, "st_blocks") else None)):
                    key = f"temporary_checkpoint_max_{kind}"
                    if value is not None:
                        metrics[key] = max(metrics[key], value)
            metrics["disk_observation_seconds"] += time.perf_counter() - started

    def measured_worker(request, timeout):
        started = time.perf_counter()
        result = run_worker(request, timeout)
        metrics["worker_lifetime_seconds"] += time.perf_counter() - started
        metrics["worker_launches"] += 1
        started = time.perf_counter()
        sizes = disk_bytes(request.parent)
        # The staged EML is outside the worker workspace and remains live here.
        staged = Path(json.loads(request.read_text())["record"]["path"]).stat()
        sizes["logical_bytes"] += staged.st_size
        if sizes["allocated_bytes"] is not None:
            sizes["allocated_bytes"] += staged.st_blocks * 512
        for kind, value in sizes.items():
            key = f"temporary_checkpoint_max_{kind}"
            if value is not None:
                metrics[key] = max(metrics[key], value)
        metrics["disk_observation_seconds"] += time.perf_counter() - started
        return result

    def measured_publish(*args, **kwargs):
        started = time.perf_counter()
        try:
            return publish(*args, **kwargs)
        finally:
            metrics["parent_publication_seconds"] += time.perf_counter() - started

    args = argparse.Namespace(archive=source, messages=None, mode=mode, bundles=bundles,
                              no_report=False, max_message_mib=32, unescape="preserve",
                              worker_timeout=30.0, trace_allocations=False)
    with patch.object(isolation, "_run_worker", measured_worker), patch.object(isolation, "_publish", measured_publish), patch.object(mbox_import, "_convert_record", measured_record):
        result = audit.run_trial(source, root, args)
    result["timing"]["dependency_import_seconds"] += imports
    result["worker_measurement"] = metrics
    result["final_disk"] = disk_bytes(root)
    if mode == "direct":
        metrics["parent_publication_seconds"] = None
    result["memory"]["largest_reaped_worker_peak_rss_bytes"] = (
        result["memory"]["largest_reaped_worker_peak_rss_bytes"] if mode == "worker" else None)
    return result


def distribution(values: list[float]) -> dict:
    return {"samples": values, "median": statistics.median(values), "min": min(values),
            "max": max(values), "stdev": statistics.stdev(values) if len(values) > 1 else None}


def summarize(pairs: list[dict], expected_records: int | None = None) -> dict:
    comparisons = [audit.compare(pair["direct"], pair["worker"]) for pair in pairs]
    repeat_parity = all(audit.compare(pairs[0]["direct"], pair["direct"])["timing_comparable"]
                        for pair in pairs)
    count_matches = expected_records is None or all(
        pair[mode]["counts"]["records"] == pair[mode]["counts"]["written"] == expected_records
        for pair in pairs for mode in ("direct", "worker"))
    valid = all(item["timing_comparable"] for item in comparisons) and repeat_parity and count_matches
    return {"parity": valid, "repeat_parity": repeat_parity, "expected_count_matches": count_matches,
            "comparisons": comparisons,
            "worker_over_direct_conversion": distribution([
                item["right_over_left_conversion_ratio"] for item in comparisons]) if valid else None,
            "conversion_seconds": {mode: distribution([
                pair[mode]["timing"]["conversion_seconds"] for pair in pairs])
                for mode in ("direct", "worker")} if valid else None}


def import_probe() -> dict:
    code = ('import time,json; t=time.perf_counter(); '
            'import dead_letter.core.mbox_import,dead_letter.core.mbox_isolation; '
            'print(json.dumps({"dependency_import_seconds":time.perf_counter()-t}))')
    start = time.perf_counter()
    result = subprocess.run([sys.executable, "-I", "-c", code], check=True,
                            capture_output=True, text=True)
    return {**json.loads(result.stdout), "process_elapsed_seconds": time.perf_counter() - start}


def machine(work: Path) -> dict:
    def command(args):
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    return {"platform": platform.platform(), "machine": platform.machine(),
            "cpu_count": os.cpu_count(), "processor": platform.processor(),
            "cpu_brand": command(["sysctl", "-n", "machdep.cpu.brand_string"]) if sys.platform == "darwin" else None,
            "physical_memory_bytes": command(["sysctl", "-n", "hw.memsize"]) if sys.platform == "darwin" else None,
            "filesystem": command(["df", "-T", str(work)]) if sys.platform.startswith("linux") else
                          command(["df", "-T", "apfs", str(work)]) if sys.platform == "darwin" else None,
            "source_revision": command(["git", "rev-parse", "HEAD"]),
            "helper_sha256": {path.name: audit.hash_file(path)[0] for path in
                              (Path(__file__).resolve(), Path(audit.__file__).resolve())},
            "working_tree_dirty": bool(command(["git", "status", "--porcelain"]))}


def bounded_messages(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 32:
        raise argparse.ArgumentTypeError("Use 1 to 32 messages")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=bounded_messages, default=4)
    parser.add_argument("--repeats", type=int, choices=(2, 3), default=3)
    parser.add_argument("--work-dir", type=Path, default=Path("/private/tmp") if sys.platform == "darwin" else Path("/tmp"))
    parser.add_argument("--trial", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("direct", "worker"), default="direct", help=argparse.SUPPRESS)
    parser.add_argument("--bundles", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.trial:
        with audit.quiet_stdio():
            result = trial(args.trial, args.output, args.mode, args.bundles)
        print(json.dumps(result, allow_nan=False))
        return 0 if result["clean"] else 1
    result = {"schema_version": 1, "kind": "mbox_worker_matrix", "machine": machine(args.work_dir),
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "conditions": {"messages_per_trial": args.messages, "repeats": args.repeats,
                             "order": "direct-worker on even repeat; worker-direct on odd repeat",
                             "cache": "source hash warms cache; no cache flush; no warmup excluded",
                             "worker_timeout_seconds": 30, "tracemalloc": False,
                             "concurrency": "one trial and at most one worker; host otherwise uncontrolled",
                             "temporary_storage": "TMPDIR set to trial workspace on same filesystem as output",
                             "timing": "disk observations overlap conversion timer; worker lifetime includes startup/import/conversion/receipt/exit; publication is a subset of conversion",
                             "unavailable": "child-only conversion and child startup split; direct publication split; true temporary disk peak; process-tree RSS peak"},
              "import_probes": [import_probe() for _ in range(args.repeats)], "matrix": []}
    with TemporaryDirectory(prefix="dead-letter-worker-matrix-", dir=args.work_dir) as temporary:
        work = Path(temporary)
        for profile in PROFILES:
            source = work / f"{profile}.mbox"
            write_corpus(source, profile, args.messages)
            for bundles in (False, True):
                pairs = []
                for repeat in range(args.repeats):
                    pair = {}
                    order = ("direct", "worker") if repeat % 2 == 0 else ("worker", "direct")
                    for mode in order:
                        with TemporaryDirectory(prefix="trial-", dir=work) as trial_root:
                            root = Path(trial_root)
                            command = [sys.executable, str(Path(__file__).resolve()), "--trial", str(source),
                                       "--output", str(root / "output"), "--mode", mode]
                            if bundles:
                                command.append("--bundles")
                            tick = time.perf_counter()
                            process = subprocess.run(command, capture_output=True, text=True,
                                                     env={**os.environ, "TMPDIR": str(root)}, check=True)
                            pair[mode] = json.loads(process.stdout)
                            pair[mode]["process_elapsed_seconds"] = time.perf_counter() - tick
                    pairs.append(pair)
                result["matrix"].append({"profile": profile, "bundles": bundles,
                                         "summary": summarize(pairs, args.messages), "pairs": pairs})
                print(f"Completed {profile} bundles={bundles}", file=sys.stderr, flush=True)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if all(cell["summary"]["parity"] for cell in result["matrix"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
