# MBOX validation and full-import measurements

Availability: development-checkout helper on PR #118, built on the streaming
importer in #115. Run `uv sync --extra dev --locked` first. This does not change
CLI defaults, release versions, or the worker isolation contract.

The earlier `scripts/benchmark_mbox_stream.py` checks framing, oversized-record
recovery and offsets beyond 2 GiB. It deliberately does not parse MIME, render
Markdown, write bundles or exercise streamed reports. This companion helper
measures those operations through the real importer, then audits their output.

## Reproduce a synthetic comparison

Each invocation is a fresh Python process with a fresh temporary destination.
Run from the repository root:

```bash
uv run python scripts/benchmark_mbox_import.py \
  --mode direct --messages 40 --attachment-kib 256 --bundles > direct.json
uv run python scripts/benchmark_mbox_import.py \
  --mode worker --messages 40 --attachment-kib 256 --bundles > worker.json
uv run python scripts/benchmark_mbox_import.py --compare direct.json worker.json
```

`--messages` defaults to 40. Four deterministic record shapes rotate: plain text,
HTML with quoted content, a binary attachment, and a long plain-text reply.
Dates, message IDs, multipart boundaries and Gmail labels are fixed. The file is
not sparse; every byte is generated. One in four messages carries an attachment
of `--attachment-kib` KiB. No inbox, network provider or private fixture is used.

Omit `--bundles` to compare flat Markdown. Reports are enabled by default;
`--no-report` measures the path without report entry writes/publication. Both
modes use structured thread rendering. The worker budget defaults to 30 seconds
per message and can be set with `--worker-timeout`; it is not a whole-run timeout.

A comparison is successful only when the corpus, conversion settings, package
versions, Python version and platform match, both runs are clean, and ordered
result/artifact digests plus all audit counters agree. Identical failures are
not accepted as successful validation. Faster runs that drop messages or change
output do not receive a performance ratio. `--compare` returns 1 for those cases.
Changing an archive basename also changes source provenance; compare the same
source path, not renamed copies.

## What is independently checked

For each source-ordered result, the harness re-reads the declared byte range
through a separate file handle using reads capped at 64 KiB. It checks ordinal,
contiguous envelope offsets, stored-byte counts and SHA-256 against the importer's
provenance. At successful EOF the range sequence must account for the entire
archive. A full-archive digest before and after the run detects changed content
across the validation interval. The importer also retains its normal metadata
mutation checks. This is still not a snapshot or an adversarial concurrency
protocol: use an immutable exported file.

Every returned output file is hashed, including all extracted attachments in
bundle mode. In default `preserve` mode, each bundle's `source.eml` hash must
match the original stored range. With explicit unquoting, source copies are
intentionally different; this equality check is not claimed, and
`source_copies_verified` stays zero. Ordered result metadata/diagnostics and
artifact names, sizes and hashes feed length-delimited aggregate digests.
Paths, message text, labels and individual attachment names are not printed.

The harness compares direct and worker output, not two independent MIME
implementations. Range checks validate the ranges that the framer reports; they
do not independently prove every ambiguous MBOX separator was chosen correctly.
The generated corpus has a known record count and a separate stdlib-reader test.
For real mail, compare the count with an independently trusted export count too.
Rendered-email correctness still needs human review of representative messages;
matching two implementations of the same pipeline is not proof of semantic
correctness. Successful-but-degraded diagnostics are included in the result
digest, but `clean` means no failed results, not that every message received a
perfect quality grade.

The normal report is written by `StreamingReport`, including atomic publication.
The report file itself is not hashed for parity: timestamps, durations and the
selected worker budget differ legitimately. Per-result diagnostics/provenance
are covered by the ordered-results digest. The harness does not load the full
report or retain all message results in memory. It enumerates files for only one
message bundle at a time, so audit memory does not scale with archive count.

## Interpret the measurements

| Field | Scope |
| --- | --- |
| `dependency_import_seconds` | Loading importer/MIME modules in this invocation; separate from conversion. |
| `conversion_seconds` | Time advancing the actual lazy importer, including framing, parsing, rendering and output; workers include per-message startup and parent publication. |
| `report_seconds` | Report-entry append/flush and final report publication. |
| `audit_seconds` | Per-record independent range reads, artifact checks and digest updates. |
| `measured_wall_seconds` | The conversion/report/audit loop plus its bookkeeping, not total command runtime. |
| `first_result_seconds` | Latency from loop setup to the first yielded result; null for an empty archive. |
| `records_per_conversion_second` | Record count divided by conversion time; failed-result counts remain visible. |
| `report_bytes` | Actual final JSON report size, zero with `--no-report`. |

Corpus generation, initial/final full-archive hashing and temporary-directory
cleanup are outside the measured loop. Initial hashing warms filesystem caches;
this is **not a cold-storage I/O benchmark**. Audit reads are excluded from the
conversion timer but can still affect cache state. There is intentional per-record
clock/counter overhead. CI's eight-message run is a correctness smoke with
observational timing, not a statistically controlled throughput benchmark.

Linux exposes two separate process-lifetime high-water marks:
`importer_peak_rss_bytes` and `largest_reaped_worker_peak_rss_bytes`. They are
reported separately and never added into a fictitious measured process-tree
peak. The importer high-water mark includes imports, corpus generation and audit
work, not just conversion. Worker high-water is the largest reaped child, not a
sum across workers. macOS/Windows RSS is **null**, not zero, because this helper
does not implement those platforms' distinct measurement contracts.

`--trace-allocations` optionally adds `traced_importer_peak_bytes` for allocations
traced during the loop. It excludes pre-existing allocations and worker heaps,
is not process RSS, and changes timing. Compare traced with traced and untraced
with untraced; the comparison suppresses the ratio when instrumentation differs.
Use OS-level process-tree profiling separately when simultaneous host memory
pressure matters.

For performance decisions, run several fresh-process pairs on the target machine,
alternating order (direct/worker, then worker/direct), and retain every sample.
Inspect dispersion and outliers instead of selecting the fastest run. Record the
checkout commit, hardware and storage alongside the summaries; package versions
alone do not uniquely identify unreleased code. Do not enable worker reuse or
change defaults based solely on small synthetic CI results. Python startup cost
and real MIME/attachment work are different workloads.

## Validate an authorized real export locally

This is explicit opt-in. No real archive is accessed merely by running the
helper without `--archive`.

```bash
uv run python scripts/benchmark_mbox_import.py \
  --archive "/local/path/All mail.mbox" --mode worker --bundles \
  --worker-timeout 30 --work-dir /local/disk/with-space > local-validation.json
```

`--work-dir` must be an existing directory. The helper creates and owns a fresh
private subdirectory there. It never writes beside, deletes or modifies the
original archive. **All generated Markdown, bundles and normal reports are
temporary and removed after the run. Only the summary redirected above remains.**
Use the [normal conversion recipe](gmail-takeout.md) to retain converted files.
The source archive and per-message worker staging can use the system temporary
directory independently of this option; ensure enough space in both locations.

Reserve space for the complete converted output plus worker staging and report
copies. Auditing performs additional reads of original and generated data, so a
multi-GB run takes materially more I/O than ordinary conversion. There is no
sampling shortcut or hidden message cap. `--messages` and `--archive` are mutually
exclusive. `--attachment-kib` affects synthetic generation only.

Summary JSON contains counts, timings, versions and **content-derived hashes**.
These hashes can link runs to the same archive; the output is not anonymized.
Keep private summaries local and out of public CI/issues. Parser stdout/stderr
are discarded at the standalone process's file descriptors, including native
writes; exception values and tracebacks are not copied into the summary.
Normal report content and converted email still exist temporarily on local disk.
Filesystem/crash collectors remain host policy, not a security guarantee.

A failed conversion yields exit 1 and a summary with visible error counts. An
audit/setup exception yields exit 1 with only its exception class; no misleading
partial success summary is emitted. Ctrl-C returns 130. The direct mode has no
parser deadline and can hang on hostile input; use worker mode for that case.
Workers are not a memory cap or OS sandbox. Hard kills, native crashes in direct
mode, or repeated interruption may leave temporary files; this helper is not a
recovery journal or durable resume implementation. See [worker limits](mbox-workers.md).

## Practitioner sources applied

Reviewed September 19, 2026.

- [Victor Stinner / pyperf's measurement guidance](https://pyperf.readthedocs.io/en/latest/run_benchmark.html)
  motivates fresh processes, multiple samples and explicit noise caveats. This
  helper is an integration/audit probe, not a reimplementation of pyperf's
  calibrated statistical runner, and adds no pyperf dependency.
- [Linux man-pages getrusage contract](https://man7.org/linux/man-pages/man2/getrusage.2.html)
  specifies KiB for `ru_maxrss` and explicitly distinguishes the largest child's
  high-water mark from the process-tree peak. This is why the metrics are
  separate and why unsupported platform measurements are null.
- [Python tracemalloc documentation](https://docs.python.org/3/library/tracemalloc.html)
  defines traced Python allocations and the consequences of starting tracing
  after imports. It is not an all-process/native-memory measurement.

## Next decisions

Complete an authorized real-corpus run and inspect sample quality before claiming
broad Takeout compatibility. Measure startup/copy overhead before evaluating
worker reuse. Durable resume must separately reconcile completed-but-unreceipted
output and preserve user-edited files; matching hashes in this audit does not
implement that protocol. Neither experiment blocks independent review of #115.
