# MBOX validation and full-import measurements

Availability: development-checkout helpers for the streaming importer and
optional workers released in 0.4.0. Run `uv sync --extra dev --locked` first. This does not change
CLI defaults, release versions, or the worker isolation contract.

The earlier `scripts/benchmark_mbox_stream.py` checks framing, oversized-record
recovery and offsets beyond 2 GiB. It deliberately does not parse MIME, render
Markdown, write bundles or exercise streamed reports. This companion helper
measures those operations through the real importer, then audits their output.

## Property and fuzz testing

The normal `pytest` run includes `tests/core/test_mbox_properties.py` and
`test_mbox_fuzz.py`. The `ci` Hypothesis profile is the default: 30 deterministic,
shrinking examples per property with a 500 ms example deadline. Mutation tests
run 24 numbered seeds by default. Inputs are synthetic and at most 4 KiB per
mutation case. The generators cover delimiter-framed `preserve`, `mboxrd`, and
`mboxo` quoting, LF/CRLF, fake postmarks, malformed and folded headers,
delimiter-adjacent long lines, duplicate IDs, empty records, and EOF without a
final newline. They accept a dialect strategy so another dialect can be added
after its framing contract exists. Content-Length dialects (`mboxcl`,
`mboxcl2`) are not generated here; their framing cases live in
`tests/core/test_mbox_content_length.py`.

Run the longer local check from the repository root:

```bash
HYPOTHESIS_PROFILE=fuzz DEAD_LETTER_FUZZ_ITERATIONS=300 \
  uv run pytest -q --hypothesis-show-statistics \
  tests/core/test_mbox_properties.py tests/core/test_mbox_fuzz.py
```

The `fuzz` profile runs 300 examples per property; the iteration knob accepts
1–2000 numbered mutation seeds. To repeat a mutation failure, use its pytest
case ID, for example `uv run pytest -q tests/core/test_mbox_fuzz.py -k seed-42`.
Hypothesis reports a shrunk failing example and a reproduction blob; retain a
confirmed minimized synthetic `.mbox` under `tests/core/fixtures/` and record
any mutation seed in the regression test. Never use private mail as a public
fixture.

The generated mailbox model checks source-order ranges, byte hashes, stored
bytes and dialect-specific unquoting. The mutation check limits reads and
staged record size, then reconciles contiguous emitted ranges with the input.
The mutation corpus also runs in a child process with a 20-second CI timeout
(60 seconds in the longer profile), so a stuck parser fails the test run.
An invalid preamble or unsupported `Content-Length` can stop the archive;
bytes after the last emitted range are then an unreported fatal suffix. The
stdlib comparison applies only to mailboxes written by CPython's own MBOX
writer, whose separator and storage-newline behavior match that test.

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
sum across workers. On macOS, importer RSS uses `RUSAGE_SELF.ru_maxrss` in
bytes, as specified by the [current Apple XNU contract](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/man/man2/getrusage.2).
The macOS child metric and both Windows metrics are **null**, not zero; no
unverified child aggregation or unit conversion is applied.

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

## Reproduce the bounded worker-cost matrix

```bash
uv run python scripts/benchmark_mbox_workers.py --messages 4 --repeats 3 \
  --work-dir /private/tmp > worker-matrix.json
```

Use an existing local directory appropriate to the host in `--work-dir`
(`/tmp` on Linux). Check the reported filesystem: a RAM-backed temporary
directory measures a different storage path from a persistent disk. The helper
has no private-archive option. It generates four
separate deterministic workloads: small plain mail, approximately 1 MiB plain
mail, mail with an 8 MiB binary attachment, and HTML with 1,200 quoted paragraphs.
It runs flat and bundle output through direct conversion and the actual timed
worker. Each trial uses a fresh importer process and destination. Each admitted
worker message uses the production fresh-process launcher. The helper permits
1–32 records per trial and two or three repeats; defaults are four and three.
It alternates direct/worker order and retains all samples, medians, ranges and
sample standard deviations. It makes no significance or confidence claim from
three repeats. Corpus construction and import-only probes run outside trials.

The existing audit checks all artifact bytes, source ranges, provenance and
diagnostics. Known record counts must match; parity must also hold across
repeats. A mismatch or failed record suppresses aggregate timing comparisons and
causes a failing exit. This checks fidelity between paths, not independent MIME
correctness. Each trial retains raw elapsed, throughput, RSS and artifact data.

The extra metrics have deliberately narrow meanings:

| Metric | Scope |
| --- | --- |
| `import_probes` | Fresh `-I` interpreter with importer/supervisor imports, recording import and process elapsed time. A startup-cost proxy, not the timed worker's measured startup component. Do not subtract it from worker conversion. |
| `direct_record_conversion_seconds` | Direct parsing, rendering and writing, excluding archive framing. Includes final publication; the direct publication split is unavailable. |
| `worker_lifetime_seconds` | Actual supervisor launch/wait interval, including child startup/import, conversion, receipt, exit and parent exit observation. Child conversion and startup cannot be separated without additional instrumentation. |
| `parent_publication_seconds` | Parent artifact validation/copy within `_publish`; a subset of worker-mode `conversion_seconds`, not an additional duration. |
| `disk_observation_seconds` | Checkpoint inspection overhead, also included in conversion time. |
| `temporary_checkpoint_max_*` | Maximum observed per-message staging bytes. Direct: staged EML after conversion. Worker: staged EML plus private workspace after worker exit and before final copy. Excludes the source archive, report spools, parser transient files and filesystem metadata. A lower bound, not a sampled peak. |
| `final_disk` | All final artifacts plus report, measured before cleanup. Logical bytes and allocated `st_blocks × 512` bytes; allocation is null where unavailable. Shared/compressed filesystem storage is not an exclusive physical-usage measurement. |

Corpus bytes are reported separately. Source, staging and final output use the
selected filesystem (`TMPDIR` is set for each trial). Temporary staging overlaps
with growing final output; do not add independently observed maxima and call the
sum a measured whole-run peak. Importer RSS includes imports and auditing;
macOS worker RSS and simultaneous process-tree RSS remain unavailable. File
hashing warms caches, and the host is not tuned or isolated from other work.
Private-function timing hooks exist only in the helper; runtime code, timeout
behavior and receipt validation remain unchanged.

See the [September 24 evidence and reuse decision](../project/2026-09-24-mbox-worker-benchmark.md)
for the measured machine, exact helper hashes, full samples and limits. The
helper exits 0 only when every matrix cell passes parity. A worker timeout or
trial failure stops the matrix with a nonzero exit; incomplete output is not a
valid comparison.

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
broad Takeout compatibility. The bounded synthetic worker-cost matrix supports
retaining fresh-worker isolation; see the
[reuse decision](../project/2026-09-24-mbox-worker-benchmark.md) before reopening it.
Durable resume must separately reconcile completed-but-unreceipted
output and preserve user-edited files; matching hashes in this audit does not
implement that protocol. These experiments do not block the released importer.
