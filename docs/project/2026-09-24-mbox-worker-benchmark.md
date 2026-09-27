# Fresh MBOX worker measurement and reuse decision

Date: September 24, 2026. Local delivery evidence for [issue #141](https://github.com/BigCactusLabs/dead-letter/issues/141).
No runtime behavior, pool, concurrency, version or release change is included.

## Decision

Retain the optional fresh interpreter per message. Defer worker reuse. The
synthetic results show a substantial relative penalty on short mail and a
smaller penalty on large and attachment-heavy mail. They do not establish a
real-archive service target, sustained memory behavior, or that weakening the
one-message process lifetime is justified. The direct path remains available
when the user does not select a timeout. No pool implementation task is warranted
from these data alone; first obtain authorized representative-corpus evidence
and a concrete throughput target.

If that evidence justifies reuse, create a separate design/implementation task.
It must preserve bounded receipts and parent-controlled publication, kill and
reap a timed-out worker before input reuse, recover from crashes, prevent one
message's state from affecting later messages, and impose a bounded worker
lifetime. Reusing one process cannot retain literal one-process-per-message
isolation; any replacement isolation guarantee needs explicit review and tests.
This measurement does not implement or approve that change.

## Reproduction and run conditions

```bash
uv sync --extra dev --locked
uv run python scripts/benchmark_mbox_workers.py --messages 4 --repeats 3 \
  --work-dir /private/tmp > worker-matrix.json
```

The recorded run used `.venv/bin/python` from that locked environment. It
started at 2026-09-24 20:20:28 UTC. The [complete compact JSON](benchmarks/2026-09-24-mbox-workers.json)
contains all 48 fresh importer trials, three import probes, counters, digests,
versions, individual timings, medians, ranges and sample standard deviations.
The per-trial `corpus.kind` is `local_archive` because the matrix passes each
generated file through the audit helper's explicit-archive path. All inputs
are the named synthetic profiles, identified by their SHA-256; no private mail
was used.
There were 96 real timed worker launches, four messages per trial, and three
repeats of each direct/worker pair. Order alternated direct/worker,
worker/direct, direct/worker. A smaller smoke and an initial full run preceded
this final run. The final helper added source identity and repeat/count checks;
the earlier run is not evidence for that final helper. Only the complete final
run is reported here. No sample within
the final run was discarded. This is warm-cache, short-run descriptive evidence,
not a calibrated pyperf result or statistical significance claim.

Host: MacBook Pro, Apple M3 Max, 14 logical CPUs, 36 GB memory, arm64,
macOS 27.0. CPU and memory came from the selected `chip_type`,
`number_processors` and `physical_memory` fields of local
`system_profiler SPHardwareDataType -json`. The helper's `sysctl` probes were
denied by the execution sandbox, so those two raw JSON fields remain null.
No machine serial or other device identifier is retained. Source, staging and
output were on the same APFS Data volume (`/private/tmp`); `df -T apfs` reported
about 336 GB available. Host load, CPU frequency and thermal state were not
controlled. Power mode and storage-device performance were not measured.

Python 3.12.13; installed package metadata: dead-letter 0.4.0,
html-to-markdown 3.14.3, mail-parser 4.6.5. The checkout base was
`863983ebb42f631d3dacde6794ee1f9aac969d21`; helper changes were uncommitted.
The JSON records both helper SHA-256 values, so the package version alone is
not used to identify the measured code. No runtime source changed for this task.
Reports and structured threads were enabled, quoting was `preserve`, message
limit was 32 MiB, worker timeout was 30 seconds, and tracemalloc was off.
The generated attachment is 8 MiB, not a private export. Initial source hashing
and per-record artifact auditing warm caches. Audit time is separately timed,
but cache effects remain. Every trial used a new output directory and importer
process; temporary data was removed after measurement.

## Conversion results

Times are seconds for four records. Ranges show all three repeated runs.
Throughput is records per conversion second. Ratios are medians of paired
worker/direct ratios, not ratios of rounded values. Conversion includes archive
framing and writing; worker conversion also includes startup, exit observation,
receipt handling and publication. It is not child parser CPU time.

| Workload / output | Direct median [range] | Worker median [range] | Direct / worker records/s | Paired ratio |
| --- | --- | --- | --- | --- |
| small / flat | 0.0112 [0.0104–0.0113] | 0.6922 [0.6419–0.6965] | 357.15 / 5.78 | 61.80× |
| small / bundle | 0.0128 [0.0128–0.0148] | 0.6842 [0.5350–0.7136] | 312.27 / 5.85 | 48.31× |
| large / flat | 0.9363 [0.9212–0.9419] | 1.5361 [1.4817–1.6524] | 4.27 / 2.60 | 1.63× |
| large / bundle | 0.9341 [0.9322–0.9567] | 1.5894 [1.5874–1.6104] | 4.28 / 2.52 | 1.70× |
| attachment-heavy / flat | 1.8023 [1.7315–1.8120] | 2.4247 [2.4144–2.4566] | 2.22 / 1.65 | 1.34× |
| attachment-heavy / bundle | 1.8502 [1.8138–1.8629] | 2.6362 [2.6007–2.8659] | 2.16 / 1.52 | 1.43× |
| html-thread-heavy / flat | 0.1841 [0.1811–0.1852] | 0.9164 [0.9163–0.9932] | 21.73 / 4.36 | 5.06× |
| html-thread-heavy / bundle | 0.1870 [0.1858–0.1948] | 0.9350 [0.8214–0.9865] | 21.39 / 4.28 | 5.00× |

The separate fresh-interpreter/import probes took 0.1161–0.1222 seconds end to
end; dependency imports accounted for 0.0856–0.0919 seconds. These are startup
proxies, not measurements inside actual workers. Do not subtract them from the
worker lifetime to claim isolated child conversion cost. The real launch/wait
interval is recorded separately in every worker trial. Direct per-record
conversion excludes framing but still includes writing. Actual child startup,
child-only conversion and direct publication splits are unavailable.

Median parent publication time across four messages ranged from 0.0033 seconds
(small flat) to 0.0389 seconds (attachment bundles). It was a small portion of
worker conversion in this run. Publication includes validation and copying,
not just an isolated storage transfer. The measurements therefore suggest that
fresh-process work and supervisor waiting deserve more attention than copy
optimization for these workloads; they do not prove exactly how much time was
spent importing inside workers. Disk-observation overhead was at most 0.0125
seconds per trial and remains included in conversion time.

## Memory and disk

RSS entries below are maxima across the three importer runs, in MiB, for direct
and worker modes. Worker-mode RSS is the **parent importer only**, not the worker
or process tree. macOS child RSS is unavailable; a smaller parent number does
not establish lower total host-memory pressure. `RUSAGE_SELF.ru_maxrss` is a
process-lifetime high-water mark and includes imports and auditing.

Disk values are logical MiB, with maxima across repeats. Temporary columns are
observed staging checkpoints, not true peaks. Final output includes the report;
legitimate timestamp/duration/budget differences can change report byte length.
Allocated block counts are also retained in JSON. These do not prove exclusive
physical use on APFS. Corpus storage is separate: 1,480 bytes small, 4,369,368
bytes large, 45,919,348 bytes attachment-heavy, and 421,940 bytes HTML/thread.

| Workload / output | Importer RSS direct / worker MiB | Staging direct / worker MiB | Final direct / worker MiB |
| --- | --- | --- | --- |
| small / flat | 42.8 / 42.5 | 0.0003 / 0.0019 | 0.0049 / 0.0049 |
| small / bundle | 42.7 / 42.6 | 0.0003 / 0.0022 | 0.0060 / 0.0060 |
| large / flat | 62.8 / 42.4 | 1.0417 / 2.0580 | 4.0637 / 4.0637 |
| large / bundle | 60.8 / 44.1 | 1.0417 / 3.0997 | 8.2304 / 8.2304 |
| attachment-heavy / flat | 213.1 / 42.5 | 10.9480 / 10.9497 | 0.0056 / 0.0056 |
| attachment-heavy / bundle | 205.2 / 44.2 | 10.9480 / 29.8977 | 75.7975 / 75.7975 |
| html-thread-heavy / flat | 58.7 / 42.6 | 0.1005 / 0.1732 | 0.2890 / 0.2890 |
| html-thread-heavy / bundle | 58.6 / 42.5 | 0.1005 / 0.2737 | 0.6911 / 0.6911 |

Direct staging was inspected after each conversion, while the staged EML still
existed. Worker staging was inspected after child exit and before parent
publication: staged EML plus worker request, receipt and artifacts. Report
spools, parser transient files and filesystem metadata are excluded. During
publication, temporary staging and already-published output overlap. Do not
add separately observed maxima into a claimed measured disk peak. The
attachment-heavy bundle case alone demonstrates roughly 29.9 MiB staging plus
growing output, with about 75.8 MiB final output and 43.8 MiB corpus storage.

## Fidelity and validation

All eight workload/layout cells passed for all three pairs and across repeats.
All 192 record conversions succeeded, each trial wrote the known four records,
and every source range was checked against the original archive. All ordered
artifact-byte, provenance and diagnostic digests matched direct to worker and
between repeats. Bundle source copies matched their stored source ranges. Input
archive hashes were unchanged. The normal report bytes are not compared because
run times, durations and worker settings legitimately differ; report result
provenance and diagnostics are covered by the result digest.

The helper suppresses aggregate comparisons on changed bytes, failed records,
changed repeat results or incorrect record counts. Regression tests exercise
those rejection cases and launch actual production workers for flat and bundle
parity. The targeted suite passed: **56 tests** in
`test_mbox_benchmark.py` and `test_mbox_worker_benchmark.py`. Advisory Ruff checks
passed for the new script and tests. Wider source verification belongs to the
combined change's validation report. No real/private corpus, multi-GB archive,
Windows/Linux performance run or long-lived worker was tested.

## Measurement references

- [Current Apple XNU getrusage manual](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/man/man2/getrusage.2)
  defines `ru_maxrss` in bytes. The older archived Apple HTML page has different
  unit wording and was not used. Darwin child aggregation was not assumed.
- [Linux getrusage manual](https://man7.org/linux/man-pages/man2/getrusage.2.html)
  defines KiB and says child RSS is the largest reaped child's high-water mark,
  not a process-tree peak. The existing Linux helper keeps those semantics.
- [pyperf measurement guidance](https://pyperf.readthedocs.io/en/latest/run_benchmark.html)
  and [Victor Stinner's noise guidance](https://vstinner.readthedocs.io/benchmark.html)
  motivate fresh processes, repeated samples and explicit host-noise limits.
  They do not make these three-run synthetic results statistically conclusive.

See [measurement field definitions](../reference/mbox-validation.md#reproduce-the-bounded-worker-cost-matrix)
and [worker guarantees](../reference/mbox-workers.md) before interpreting the
results as a performance or isolation contract.
