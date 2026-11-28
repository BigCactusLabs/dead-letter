"""Measure bounded synthetic MIME cases through the real timed MBOX workers.

Run explicitly; the full matrix is not part of the default test suite. JSON goes
to stdout. Requires Linux/macOS wait4; never accepts mail supplied by a user.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

POSTMARK = b"From benchmark@example.test Thu Jun 11 00:38:38 +0000 2020\n"
CASES = ("small-text", "base64-attachment", "many-attachments",
         "nested-multipart", "large-html", "quoted-printable")
ATTACHMENT_BYTES = 4 * 1024**2
ATTACHMENT_COUNT = 64
SMALL_ATTACHMENT_BYTES = 1024
MIME_DEPTH = 64  # No explicit MIME-depth limit exists in core; not a claimed ceiling.
HTML_DEPTH = 32
HTML_ROWS = 512
QP_LINES = 4096
WORKER_TIMEOUT_SECONDS = 30.0


def synthetic_message(case: str) -> bytes:
    """Fixed headers, boundaries and payloads; maximum archive size is < 6 MiB."""
    message = EmailMessage(policy=SMTP)
    for name, value in {
        "From": "alice@example.test", "To": "bob@example.test",
        "Date": "Thu, 11 Jun 2020 00:38:38 +0000",
        "Subject": f"Synthetic {case}", "Message-ID": f"<{case}@example.test>",
    }.items():
        message[name] = value
    if case == "small-text":
        message.set_content("A short synthetic message.\n")
    elif case in {"base64-attachment", "many-attachments"}:
        message.set_content("Bounded synthetic attachments.\n")
        count = 1 if case == "base64-attachment" else ATTACHMENT_COUNT
        size = ATTACHMENT_BYTES if count == 1 else SMALL_ATTACHMENT_BYTES
        for index in range(count):
            message.add_attachment(bytes(range(256)) * (size // 256),
                                   maintype="application", subtype="octet-stream",
                                   filename=f"synthetic-{index:03d}.bin")
        message.set_boundary("synthetic-mixed")
    elif case == "nested-multipart":
        current = message
        for depth in range(MIME_DEPTH):
            current.make_mixed()
            current.set_boundary(f"synthetic-nested-{depth:02d}")
            child = EmailMessage(policy=SMTP)
            current.attach(child)
            current = child
        current.set_content("A bounded deeply nested body.\n")
    elif case == "large-html":
        cell = "<td>" + "&amp;&lt;&gt;&quot;&nbsp;&#x1F642;" * 8 + "</td>"
        html = ("<html><body>" + "<div>" * HTML_DEPTH + "<table>"
                + ("<tr>" + cell * 4 + "</tr>\n") * HTML_ROWS
                + "</table>" + "</div>" * HTML_DEPTH + "</body></html>")
        message.set_content(html, subtype="html", cte="quoted-printable")
    elif case == "quoted-printable":
        # QP inflates the wire representation; decoding does not expand bytes.
        message.set_content(("=é" * 24 + "\n") * QP_LINES, cte="quoted-printable")
    else:
        raise ValueError(f"Unknown synthetic case: {case}")
    return message.as_bytes()


def write_archive(path: Path, case: str) -> None:
    raw = synthetic_message(case)
    path.write_bytes(POSTMARK + raw.replace(b"\nFrom ", b"\n>From ") + b"\n")


def rss_bytes(value: int, system: str) -> int:
    """wait4 ru_maxrss is bytes on Darwin, KiB on Linux; no guessed fallback."""
    if system == "darwin":
        return value
    if system.startswith("linux"):
        return value * 1024
    raise ValueError(f"Unsupported RSS units: {system}")


def usage_metrics(usage, system: str) -> dict:
    return {"child_user_cpu_seconds": usage.ru_utime,
            "child_system_cpu_seconds": usage.ru_stime,
            "child_cpu_seconds": usage.ru_utime + usage.ru_stime,
            "worker_peak_rss_bytes": rss_bytes(usage.ru_maxrss, system)}


def observe_wait4(wait4, samples: list):
    """Keep waitpid's pid/status protocol while collecting the same reap's usage."""
    def waitpid(pid, options):
        waited, status, usage = wait4(pid, options)
        if waited:
            samples.append((waited, status, usage))
        return waited, status
    return waitpid


def output_inventory(root: Path) -> dict:
    totals = dict(markdown_bytes=0, attachment_bytes=0, source_copy_bytes=0, other_bytes=0)
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        size = path.stat().st_size
        if "attachments" in relative.parts:
            kind = "attachment_bytes"
        elif path.name == "source.eml":
            kind = "source_copy_bytes"
        elif path.suffix == ".md":
            kind = "markdown_bytes"
        else:
            kind = "other_bytes"
        totals[kind] += size
        files.append({"path": relative.as_posix(), "bytes": size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {**totals, "total_bytes": sum(totals.values()), "files": files}


def tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def measure(source: Path, output: Path, *, bundles: bool) -> dict:
    """Observe the existing supervisor; never replace its worker or conversion."""
    from dead_letter.core import mbox_isolation as isolation
    from dead_letter.core.mbox_import import convert_mbox

    measurements = []
    run_worker = isolation._run_worker

    def measured_worker(request, timeout, *args, **kwargs):
        samples = []
        tick = time.perf_counter()
        # Popen.wait(timeout) reaps through os.waitpid on supported CPython.
        # Missing/repeated reaps fail below instead of inventing CPU/RSS data.
        with patch.object(os, "waitpid", observe_wait4(os.wait4, samples)):
            returncode = run_worker(request, timeout, *args, **kwargs)
        wall = time.perf_counter() - tick
        if len(samples) != 1:
            raise RuntimeError(f"Expected one observed worker reap, got {len(samples)}")
        pid, status, usage = samples[0]
        if os.waitstatus_to_exitcode(status) != returncode or usage.ru_maxrss <= 0:
            raise RuntimeError("Worker status or peak RSS accounting unavailable")
        staged = Path(json.loads(request.read_text(encoding="utf-8"))["record"]["path"])
        measurements.append({**usage_metrics(usage, sys.platform), "worker_pid": pid,
                             "worker_returncode": returncode, "worker_wall_seconds": wall,
                             "temporary_checkpoint_bytes": tree_bytes(request.parent) + staged.stat().st_size})
        return returncode

    tick = time.perf_counter()
    with patch.object(isolation, "_run_worker", measured_worker), closing(convert_mbox(
        source, output=output, bundles=bundles, timeout_seconds=WORKER_TIMEOUT_SECONDS,
    )) as rows:
        results = list(rows)
    wall = time.perf_counter() - tick
    if len(results) != 1 or len(measurements) != 1 or not results[0].success:
        raise RuntimeError(f"Expected one successful timed conversion: {[row.error for row in results]}")
    if results[0].mbox is None or results[0].mbox["index"] != 1:
        raise RuntimeError("Missing single-record provenance")
    inventory = output_inventory(output)
    input_bytes = source.stat().st_size
    return {"mode": "bundles" if bundles else "flat", "input_bytes": input_bytes,
            "stored_eml_bytes": results[0].mbox["stored_bytes"],
            "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "output": inventory, "output_over_input": inventory["total_bytes"] / input_bytes,
            "temporary_checkpoint_over_input": measurements[0]["temporary_checkpoint_bytes"] / input_bytes,
            "wall_seconds": wall, "worker_launches": len(measurements), **measurements[0],
            "peak_temporary_directory_bytes": None,
            "peak_temporary_directory_method": "not observable without production hook; checkpoint is not peak"}


def machine() -> dict:
    def command(args):
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    return {"platform": platform.platform(), "python": sys.version,
            "machine": platform.machine(), "cpu_count": os.cpu_count(),
            "cpu": command(["sysctl", "-n", "machdep.cpu.brand_string"])
            if sys.platform == "darwin" else platform.processor(),
            "source_revision": command(["git", "rev-parse", "HEAD"]),
            "working_tree_dirty": bool(command(["git", "status", "--porcelain"])),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--case", choices=CASES, action="append", help="Default: all six cases")
    parser.add_argument("--trial", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--bundles", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if sys.platform not in {"darwin", "linux"} or not hasattr(os, "wait4"):
        parser.error("Worker CPU/RSS measurement requires Linux/macOS os.wait4")
    if args.trial:
        # The public command always supplies a generated one-message archive.
        from benchmark_mbox_import import quiet_stdio
        with quiet_stdio():
            result = measure(args.trial, args.output, bundles=args.bundles)
        print(json.dumps(result, allow_nan=False))
        return 0
    result = {"schema_version": 1, "kind": "mbox_worker_amplification",
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "machine": machine(), "command": [sys.executable, *sys.argv],
              "conditions": {
                  "records_per_trial": 1, "repeats": 1,
                  "timeout_seconds": WORKER_TIMEOUT_SECONDS, "resource_budgets": None,
                  "concurrency": "one trial / one worker; host otherwise uncontrolled",
                  "cpu_and_rss": "wait4 at actual timed supervisor reap; whole worker lifetime including imports",
                  "rss": "Darwin bytes / Linux KiB converted to bytes; per child, not simultaneous process-tree peak",
                  "disk": "logical regular-file bytes, not allocated blocks or filesystem I/O; excludes original archive",
                  "temporary_checkpoint": "after worker exit before publication: staged EML + request/receipt + private artifacts",
                  "temporary_peak": "not observable without production hook; checkpoint may miss transient files",
                  "wall": "import framing through publication and checkpoint observation; excludes initial parent imports",
                  "mime_depth": MIME_DEPTH, "core_mime_depth_limit": None,
                  "quoted_printable": "large encoded/decoded ratio; QP decoding shrinks bytes, not an expansion bomb",
                  "html_entities": "ordinary repeated HTML character references; no recursive XML entity definitions",
              }, "measurements": []}
    with TemporaryDirectory(prefix="dead-letter-amplification-", dir=args.work_dir) as temporary:
        work = Path(temporary)
        for case in args.case or CASES:
            source = work / f"{case}.mbox"
            write_archive(source, case)
            for bundles in (False, True):
                with TemporaryDirectory(prefix="trial-", dir=work) as trial:
                    root = Path(trial)
                    command = [sys.executable, str(Path(__file__).resolve()), "--trial", str(source),
                               "--output", str(root / "output")]
                    if bundles:
                        command.append("--bundles")
                    process = subprocess.run(command, check=False, capture_output=True, text=True,
                                             env={**os.environ, "TMPDIR": str(root)})
                    if process.returncode:
                        print(process.stderr, file=sys.stderr)
                        return process.returncode
                    result["measurements"].append({"case": case, **json.loads(process.stdout)})
                print(f"Completed {case} bundles={bundles}", file=sys.stderr, flush=True)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
