# MBOX message workers: timeouts and crash containment

**Availability:** shipped as an optional CLI/Python follow-up (#118) to the
streaming MBOX importer (#103/#115), released together in 0.4.0. Install any
CLI/Python route from the
[installation and distribution map](distribution.md). The examples below use
`uv run` for a development checkout; drop that prefix once dead-letter is
installed.

## Use

```bash
uv run dead-letter convert archive.mbox --output markdown/ --report --mbox-timeout 30

uv run dead-letter convert archive.mbox --output Cabinet/ --report \
  --mbox-bundles --thread-mode structured --mbox-timeout 30
```

`--mbox-timeout` takes a finite, positive number of seconds. It opts into a fresh
Python process for each message admitted by the existing source-byte/line limits.
There is no default timeout: without this flag, conversion remains in-process.
The illustrative 30-second budget is not a universal performance recommendation;
allow for interpreter startup, imports and the complexity of each message.

```python
from contextlib import closing
from dead_letter.core.mbox_import import convert_mbox

with closing(convert_mbox("archive.mbox", output="markdown", timeout_seconds=30)) as results:
    for result in results:
        print(result.source, result.success, result.error)
```

Python callers can pass `None` to disable the option. Zero, negative values,
booleans, NaN, and infinities are rejected. The flag is MBOX-specific; it cannot
silently change standalone EML, MCP or web conversion. `--dry-run` also uses the
worker when a timeout is selected, but does not publish message files.
`--dry-run --report` still explicitly writes the normal conversion report.

## What happens to one message

The parent frames and stages one EML exactly as before. It creates a private
worker workspace and a parent-generated JSON request containing the record
identity, staged EML path and conversion options. Email content cannot choose
an executable, shell command, provider, destination, or timeout.

A fresh interpreter invokes the **same `_convert_record` implementation** used
by ordinary MBOX conversion. Only its destination differs: it writes into its
private workspace, never the user's final output directory. This preserves
Gmail metadata, quoting policy, MIME selection, thread rendering and attachments
without a second conversion implementation.

After the worker exits successfully, the parent validates a JSON receipt capped
at 1 MiB. It checks the schema, message index/hash, status types and allowed error
codes; duplicate JSON fields and non-finite numeric values are rejected. The
receipt contains status and diagnostics, not a serialized MIME object or a path
for the parent to execute/follow. Actual publication paths come from the
parent's record identity. Expected artifact files must be regular files; bundle
members are restricted to the existing flat layout, with links and special
files rejected before publication.

The parent then copies completed artifacts with the existing collision-safe
writers. Temporary parser output is discarded on timeout or abnormal exit.
The worker is killed and reaped before its input EML is reused or its workspace
is removed. There is only one worker active for this import, with no parallel
fan-out, shared result queue or worker pool.

## Errors and reports

Each failed worker still has its original `source` and `mbox` provenance, so it
can be located without leaking a private parser traceback. The following worker
errors use fixed messages and `stage: worker`:

| Code | Meaning |
| --- | --- |
| `mbox_message_timeout` | The worker exceeded its time budget. |
| `mbox_worker_crashed` | The interpreter exited abnormally, including without a normal receipt. This is not a diagnosis of a particular native-library bug. |
| `mbox_worker_invalid_result` | The receipt is missing, malformed, oversized, or inconsistent with the record. No output is published even if some temporary artifacts exist. |
| `conversion_error` / `html_markdown_failed` | The existing conversion pipeline returned a recoverable message error. |
| `mbox_publish_failed` | The worker completed but artifact validation or final copying failed. Newly created incomplete final output is cleaned up. |

Those failures allow later records to continue. Empty/oversized records already
rejected by the scanner do not launch a worker and retain their existing error
codes. An OS-level failure to launch a process is archive-fatal instead of
repeatedly attempting the unavailable executable for every remaining message.

The report stores the effective setting in `mbox_options.timeout_seconds`, with
`null` meaning the default in-process path. Existing source-order entries,
partial-success status, exit code 1 for errors, and Ctrl-C exit code 130 remain.
No new report schema version or inference/remote-analysis behavior is introduced.

## Optional resource budgets

**Availability:** POSIX budgets shipped in 0.4.5 (#140); 0.4.0 and earlier do
not accept these flags. **Windows CPU and committed-memory budgets are
unreleased (#188); no published version includes them.** Budgets are opt-in
and apply only in worker mode. Without them, worker conversion is unchanged.

```bash
# Linux and macOS: CPU time and per-file output size.
uv run dead-letter convert archive.mbox --output markdown/ --report \
  --mbox-timeout 60 --mbox-cpu-seconds 30 --mbox-max-output-mib 256

# Linux: address space. Windows (unreleased): committed memory, not RSS.
uv run dead-letter convert archive.mbox --output markdown/ --report \
  --mbox-timeout 60 --mbox-cpu-seconds 30 --mbox-memory-mib 2048
```

```python
convert_mbox("archive.mbox", output="markdown", timeout_seconds=60,
             cpu_seconds=30, memory_limit_mib=2048)  # Linux / unreleased Windows
```

Each value is a positive integer. A budget without `--mbox-timeout`
(`timeout_seconds`) is a usage error, reported before any conversion or output.
The wall-clock timeout remains the universal backstop on every platform.

| Control | CLI / Python | Linux | macOS | Windows (unreleased) | Mechanism and limitation |
| --- | --- | --- | --- | --- | --- |
| Memory | `--mbox-memory-mib` / `memory_limit_mib` | Supported | **Unavailable** | Supported | MiB of virtual address space with Linux `RLIMIT_AS`; MiB of committed memory with Windows `JOB_OBJECT_LIMIT_PROCESS_MEMORY`. Neither is an RSS ceiling. Linux counts interpreter/native-library mappings and reservations; Windows denies commits over its limit. The same numeric value does not mean the same accounting on both platforms. |
| CPU time | `--mbox-cpu-seconds` / `cpu_seconds` | Supported | Supported | Supported | POSIX `RLIMIT_CPU`: `SIGXCPU` at the limit, with the hard limit one second later. Windows `JOB_OBJECT_LIMIT_JOB_TIME`: user-mode CPU seconds, checked periodically by the OS, so termination may overshoot. Neither bounds blocked time; the wall timeout does. |
| Output size | `--mbox-max-output-mib` / `max_output_mib` | Supported | Supported | **Unavailable** | MiB **per file**, not total output or free disk. `RLIMIT_FSIZE` / `SIGXFSZ` bounds each Markdown file, attachment and bundle `source.eml` copy. A bundle whose source message exceeds the limit fails. |

The parent decides this matrix before the first message and never silently
drops a requested control. An unsupported control fails with
`mbox_budget_unsupported`, including Windows output size combined with otherwise
supported CPU/memory flags. Other platforms remain unsupported.

A budgeted worker is launched differently from an unbudgeted one. Instead of
`python -I -m dead_letter._mbox_worker`, the parent runs the installed worker
file as a script: `python -I -B <path>/_mbox_worker.py`. Importing a module
with `-m` would first import the `dead_letter` package and its MIME and HTML
libraries. As a script, the worker imports only the standard library, applies
its limits, and only then imports `dead_letter`. `-I` keeps the script
directory off `sys.path`, and `-B` disables `.pyc` writes, so imports never
write files against the output limit. Unbudgeted launches keep the `-m`
command unchanged. No `preexec_fn` is used; CPython documents it as unsafe in
threaded parents. The limits reach the worker as parent-generated arguments;
email content cannot set them. The interpreter/stdlib bootstrap before limit
application is not itself a guaranteed bounded allocation phase.

### POSIX limits

After a best-effort attempt to disable core dumps, the worker lowers its own
soft and hard limits before package imports or reading the staged EML. Each
is the smaller of the requested value and the inherited one, so stricter host
limits are never raised. For example, an inherited CPU limit of (20 s,
unlimited) with `--mbox-cpu-seconds 30` becomes (20 s, 31 s). The receipt is
capped at 1 MiB and the smallest output budget is 1 MiB, so a valid receipt fits.

### Windows Job Objects

The parent creates a fresh job named with the per-launch random nonce, sets
its limits, then queries them back before starting a worker. It refuses an
already-existing name instead of adopting or modifying another job. The worker
opens this job with assignment rights only and joins before importing the
package. Its temporary handle is closed immediately; the parent owns the job
handle until the worker has been reaped. No extra runtime dependency is added.

The job permits one active process and has kill-on-close enabled, with no
breakaway flags. This also rejects ordinary child-process creation by the
budgeted parser. Existing host/CI job policies remain in force: incompatible
nested-job assignment aborts instead of retrying without enforcement. The
parent closes the handle on launch failure, timeout, cancellation, query
failure and normal exit. This is containment for the current single-worker
pipeline, not a general hostile-process-tree or security-sandbox promise.

CPU exhaustion is established from the **signaled job object** after the worker
exits. Windows exit code 1816 alone is ambiguous and remains an ordinary crash
without that job signal. The committed-memory limit uses the same Python
`MemoryError` monitoring and nonce-authenticated outcome path as Linux.

### macOS memory decision

Memory remains explicitly unsupported in this slice. The existing XNU
`RLIMIT_AS` accounting includes a very large virtual-address-space baseline,
so substituting a typical RSS-sized number is not a usable memory ceiling.
Apple's archived `setrlimit(2)` description treats `RLIMIT_RSS` as a reclaim
preference under memory pressure, not a dependable hard allocation cap.

Parent-side RSS polling would be a **soft watchdog**, not an equivalent budget:
allocations can spike between samples and continue until the parent runs and
termination completes. It cannot promise prevention of host memory exhaustion.
A future watchdog would need a separately named control/report contract,
process-identity-safe sampling, a defined sampling interval, fail-closed handling
when counters are unavailable, and tests for spikes, blocked workers and
cancellation on supported macOS versions. None of that monitoring is enabled
here. #188 stays open for this decision and native validation; no macOS hard-cap
or polling performance result is claimed.

### Budget failures

| Code | Scope | Meaning |
| --- | --- | --- |
| `mbox_budget_unsupported` | Usage error, before conversion | The control is unavailable on this platform (message names the control, flag and platform). CLI exit code 1, no output or report. |
| `mbox_budget_apply_failed` | Archive-fatal | A requested limit could not be configured or verified, or Windows job setup/join/state/cleanup failed. No unenforced retry; the import stops with an archive error entry. |
| `mbox_message_resource_limit` | One record | The worker exceeded a budget. The message names the limit when determined: CPU time (`SIGXCPU` or Windows job signal), per-file output (`SIGXFSZ`), or memory (a `MemoryError` observed under a memory budget). |

An exceeded budget is handled like a timeout: the worker is reaped, its private
workspace and any partial artifacts are discarded unpublished, and later
records continue. Outcomes that cannot be attributed to a budget stay
`mbox_worker_crashed`. That includes a native library aborting on allocation
failure under a memory budget, and the POSIX kernel `SIGKILL` backstop one
second after the CPU soft limit.

The worker reports apply-failed and memory-limit outcomes with a distinct exit
status. That status only counts if the worker also wrote a small control file,
`budget-status.json`, carrying a random nonce the parent issued for that launch.
The worker opens the control file and encodes its contents before any limit is
applied, so writing it later needs no allocation. Any other exit with the same
number, including a `SystemExit` raised during conversion, is
`mbox_worker_crashed`.

**Memory detection.** Parts of the EML pipeline recover from errors on purpose;
for example, an attachment that fails to decode is skipped. Under a memory
budget, such a recovery could publish a message with an attachment silently
missing. To prevent that, a memory-budgeted worker uses a `sys.monitoring`
`RAISE` callback (Python 3.12+) during conversion. The callback records every
`MemoryError` that reaches a Python frame, including one raised by a C function
such as a base64 decoder and later caught. If any was recorded, the whole
record is withheld as a memory-limit outcome. A worker that cannot get a free
monitoring tool ID treats this as a failure to apply the budget. Known gap: a
native library that hits and handles an allocation failure internally, without
raising into Python, is not observed.

The report records the requested values in `mbox_options.memory_limit_mib`,
`mbox_options.cpu_seconds` and `mbox_options.max_output_mib`, with `null` when a
control is not set. The same options reach workers for ZIP/TGZ input; archive
staging and parent framing are not covered by a per-worker resource budget.

**These are resource limits, not a sandbox.** They do not restrict filesystem
or network access, user privileges, or what native code can read. Existing
synthetic amplification measurements (parent RSS, staging and final disk use)
are in the [worker benchmark](../project/2026-09-24-mbox-worker-benchmark.md);
they are not a recommendation for budget values.

## Precise limits

**This is process-lifetime containment, not a security sandbox.** Without the
opt-in [resource budgets](#optional-resource-budgets) it is not a memory cap.
The child runs with the user's normal privileges and inherited environment.
Python isolated mode (`-I`) excludes current-directory/PYTHONPATH import
shadowing; it does not deny filesystem or network access. The normal conversion
pipeline remains local and introduces no network calls. Host-level restrictions
are still needed for adversarial native code, memory ceilings where no budget
is available, disk quotas, or guaranteed isolation from other user data.

The wall-clock budget starts after process creation and covers worker
startup/imports, parsing, rendering and temporary output. It does **not**
time-limit archive framing, creating the process, parent-side receipt
validation/final copying, or report I/O. OS scheduling, process creation and
uninterruptible I/O can delay termination. Cleanup waits for the direct worker
to exit, rather than knowingly reusing an input while it is still running.
The current parser path has no child process tree; unbudgeted workers and the
POSIX supervisor do not promise to terminate hypothetical future grandchildren.

Child stdout/stderr go to the OS null device rather than memory buffers or
normal logs. On POSIX, the worker makes a best-effort attempt to disable core
dumps before importing native parsers. External crash collectors and Windows
dump services remain host policy; this is not a universal no-dump guarantee.

One fresh interpreter per message adds startup overhead and an extra output-copy
step. In bundle mode, reserve disk space for the worker's temporary source and
attachment copies as well as final output. Worker-local allocations disappear
with the process, but without a supported memory budget a single message can
still exhaust host memory before its time budget expires. Do not interpret the
existing 64 MiB source limit as a 64 MiB resident-memory bound.

Parent publication still uses the existing writers: a file can be visible while
being copied, and ordinary exceptions trigger cleanup. Output plus report receipt
are **not one transaction**. Parent SIGKILL/power loss, repeated interruption
during cleanup, and a machine-wide resource failure remain outside recovery
guarantees. Reruns remain collision-safe, not deduplicated or resumable, unless
you opt into the `--mbox-resume` journal (available from 0.4.6;
see [resumable MBOX imports](mbox-resume.md)). See the
[base Takeout contract](gmail-takeout.md) for immutable-source and dialect limits.

## Practitioner sources and design decisions

Original worker precedents reviewed September 18, 2026; Windows/macOS budget
contracts reviewed October 2, 2026. These are architectural precedents, not
claims of novel parser research or universal format conformance.

**Apache Tika's ForkParser** exposes a parse timeout that shuts down the server
on overrun and a files-per-server limit for parser memory leaks. This supports
separating parser lifetime from a long-running importer. This first slice uses
one file per fresh Python process for simple cleanup; it does not add Tika/Java
or claim the throughput of a recycled process pool.
[First-party ForkParser reference](https://tika.apache.org/3.0.0/api/org/apache/tika/fork/ForkParser.html).

**Python's process/queue contract** warns that terminating processes using pipes,
queues or locks can corrupt shared state or deadlock other processes, and that
`Connection.recv()` automatically unpickles. The decision here is a bounded JSON
receipt on disk after exit, with no shared queue or pickle deserialization.
[Multiprocessing reference](https://docs.python.org/3.12/library/multiprocessing.html).

**Python's subprocess contract** recommends `sys.executable` plus `-m` for the
current interpreter, documents process-creation timeout limits, and requires
care when killing/waiting or capturing output. The supervisor explicitly kills
and reaps in `finally`, discards noisy logs, uses no shell or `preexec_fn`, and
splits long waits into platform-safe intervals without changing the overall
budget. Merely using a `Popen` context manager would wait, not establish the
needed kill-on-timeout behavior.
[Subprocess reference](https://docs.python.org/3.12/library/subprocess.html).

**Microsoft's Job Object contracts** define job-time signaling, nesting,
kill-on-close, committed-memory accounting and the native structure fields.
The implementation queries back limits and uses the job signal rather than
assuming that an exit code or an ordinary completion-port message proves a CPU
limit event. See [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[basic limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information),
and [extended limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information).

**Apple's archived limit contract** is background for rejecting `RLIMIT_RSS`
as a hard ceiling, not a substitute for testing current macOS kernels.
[Archived setrlimit reference](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/setrlimit.2.html).

## Validation and follow-through

The tests are included automatically by the existing Linux/macOS/Windows MBOX
matrix. They exercise real child processes for hangs, abrupt `os._exit`, noisy
output, invalid receipts and cancellation. Faults are injected by a **test-only
command builder**, not a production environment flag or an email instruction.
Abrupt exit is a controlled crash surrogate, not an observed native MIME bug.

Separate integration cases launch the actual installed module and compare all
produced bytes, metadata/provenance and diagnostics against direct conversion,
for flat/bundle output and dry/non-dry runs. Additional tests cover copy failure,
collision safety, receipt identity/types, non-finite/duplicate JSON fields, large
time budgets, archive continuation and unchanged default dispatch.

Resource-budget tests (`tests/core/test_mbox_budgets.py`) exercise the capability
matrix everywhere. Real workers test CPU exhaustion on Linux/macOS/Windows,
memory denial on Linux/Windows, and per-file output limits on Linux/macOS.
They verify withheld artifacts, later-record continuation, output parity,
pre-package limit application, nonce checks and swallowed decoder MemoryErrors.
POSIX inherited-limit cases keep their explicit platform restriction.

Windows-specific cases distinguish an ordinary native exit 1816 from a genuine
CPU-limit signal and request a 768 MiB allocation under a 512 MiB committed-memory
limit, rather than relying only on a 64 GiB request that could exhaust the host.
CLI/report integration covers both a flat MBOX and zipped Takeout input.
`tests/core/test_mbox_windows_job.py` adds platform-independent fake-API contracts
for ABI widths, units, verification and cleanup; these are not native Windows
enforcement evidence. PR check results and commit SHAs are the execution record.

No private email was used or uploaded, and a real authorized multi-GB Takeout
archive has not been tested; that remains
[#138](https://github.com/BigCactusLabs/dead-letter/issues/138). The
[synthetic worker benchmark](../project/2026-09-24-mbox-worker-benchmark.md)
measures startup/copy overhead and defers reuse. Remaining budget work is the
macOS memory decision and native validation in #188. Durable resume (#139) is
implemented separately and is available from 0.4.6; see
[resumable MBOX imports](mbox-resume.md). Neither is implied by the timeout option.
