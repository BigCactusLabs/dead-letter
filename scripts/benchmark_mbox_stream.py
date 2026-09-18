"""Synthetic >2 GiB framing/overflow-recovery probe, NOT a MIME throughput benchmark.

Run: uv run python scripts/benchmark_mbox_stream.py --gib 2
Creates a sparse file in the temp directory, reads every byte, and removes it.
No private email, network calls, or permanent output files are involved.
"""
from __future__ import annotations

import argparse
import json
import time
import tracemalloc
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from dead_letter.core.mbox import MboxLimits, iter_mbox


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gib", type=int, default=2)
    args = parser.parse_args()
    if args.gib < 1:
        parser.error("--gib must be >= 1")
    postmark = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
    message = b"Subject: Valid\n\nHello\n\n"
    with TemporaryDirectory(prefix="mbox-probe-") as directory:
        source = Path(directory) / "probe.mbox"
        with source.open("wb") as out:
            out.write(postmark + message + postmark + b"Subject: Oversize\n\n")
            out.seek(args.gib * 1024**3, 1)
            out.write(b"\n" + postmark + message)
        size = source.stat().st_size
        tracemalloc.start()
        started = time.perf_counter()
        with closing(iter_mbox(source, limits=MboxLimits(max_message_bytes=1024**2))) as records:
            observed = [(record.index, record.error_code) for record in records]
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert observed == [(1, None), (2, "mbox_message_too_large"), (3, None)], observed
        print(json.dumps({"bytes_scanned": size, "seconds": round(elapsed, 3),
                          "python_peak_bytes": peak, "records": observed}, indent=2))


if __name__ == "__main__":
    main()
