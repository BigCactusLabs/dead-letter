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

**Availability:** on `main`, unreleased (#140, first slice). Budgets are opt-in
and apply only in worker mode. Without them, worker conversion is unchanged.

```bash
uv run dead-letter convert archive.mbox --output markdown/ --report \
  --mbox-timeout 60 --mbox-cpu-seconds 30 --mbox-max-output-mib 256

# Linux only: also cap each worker's virtual address space.
uv run dead-letter convert archive.mbox --output markdown/ --report \
  --mbox-timeout 60 --mbox-cpu-seconds 30 --mbox-memory-mib 2048
```

```python
convert_mbox("archive.mbox", output="markdown", timeout_seconds=60,
             cpu_seconds=30, max_output_mib=256)
```

Each value is a positive integer. A budget without `--mbox-timeout`
(`timeout_seconds`) is a usage error, reported before any conversion or output.
The wall-clock timeout remains the universal backstop on every platform.

| Control | CLI / Python | Unit | Linux | macOS | Windows | Mechanism and limitation |
| --- | --- | --- | --- | --- | --- | --- |
| Memory | `--mbox-memory-mib` / `memory_limit_mib` | MiB of virtual address space | Supported | **Unavailable** | **Unavailable** | `RLIMIT_AS`. Counts reserved address space, not resident memory, so it includes interpreter and native-library mappings; set it well above the observed RSS. On macOS, xnu counts it against a baseline virtual size of hundreds of GiB, so an absolute budget cannot be honored. |
| CPU time | `--mbox-cpu-seconds` / `cpu_seconds` | seconds of CPU time | Supported | Supported | **Unavailable** | `RLIMIT_CPU`: `SIGXCPU` at the limit, with the kernel's hard limit one second later. Includes interpreter startup and imports. It does not bound time spent blocked; the wall-clock timeout covers that. |
| Output size | `--mbox-max-output-mib` / `max_output_mib` | MiB **per file** | Supported | Supported | **Unavailable** | `RLIMIT_FSIZE`: `SIGXFSZ` when a single file the worker writes would exceed the limit. It bounds each Markdown file, attachment and bundle `source.eml` copy, not total output or free disk. A bundle whose source message exceeds the limit fails. |

Windows Job Objects (and other platforms) are a follow-up; there, requesting any
budget fails with `mbox_budget_unsupported` rather than running unenforced.
The parent decides this matrix from the platform before the first message and
never silently drops a requested control.

The worker applies its own limits at startup, after disabling core dumps and
before importing the MIME parsers or reading the staged EML. It does not use
`preexec_fn`, which CPython documents as unsafe in threaded parents. The limits
reach the worker as parent-generated arguments; email content cannot set them.
Under an output budget the worker also disables bytecode caching so lazy imports
never write `.pyc` files against the limit. The receipt is capped at 1 MiB and
the smallest output budget is 1 MiB, so a valid receipt always fits.

### Budget failures

| Code | Scope | Meaning |
| --- | --- | --- |
| `mbox_budget_unsupported` | Usage error, before conversion | The control is unavailable on this platform (message names the control, flag and platform). CLI exit code 1, no output or report. |
| `mbox_budget_apply_failed` | Archive-fatal | A worker could not apply a requested limit (for example, a stricter host policy rejected it). No record is converted without the requested guarantee; the import stops with an archive error entry. |
| `mbox_message_resource_limit` | One record | The worker exceeded a budget. The message names the limit when it can be determined: CPU time (`SIGXCPU`), per-file output (`SIGXFSZ`), or memory (`MemoryError` under a memory budget). |

An exceeded budget is handled like a timeout: the worker is reaped, its private
workspace and any partial artifacts are discarded unpublished, and later
records continue. Outcomes that cannot be attributed to a budget stay
`mbox_worker_crashed`. That includes a native library aborting on allocation
failure under a memory budget, and the kernel `SIGKILL` backstop one second
after the CPU soft limit.

The report records the requested values in `mbox_options.memory_limit_mib`,
`mbox_options.cpu_seconds` and `mbox_options.max_output_mib`, with `null` when a
control is not set.

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
are still needed for adversarial native code, memory ceilings where no budget is available, disk
quotas, or guaranteed isolation from other user data.

The budget starts after process creation and covers worker startup/imports,
parsing, rendering and temporary output. It does **not** time-limit archive
framing, creating the process, parent-side receipt validation/final copying, or
report I/O. OS scheduling, process creation and uninterruptible I/O can delay
termination. Cleanup waits for the direct worker to exit, rather than knowingly
reusing an input while it is still running. The current parser path has no child
process tree; this implementation does not promise to terminate hypothetical
future grandchildren.

Child stdout/stderr go to the OS null device rather than memory buffers or
normal logs. On POSIX, the worker makes a best-effort attempt to disable core
dumps before importing native parsers. External crash collectors and Windows
dump services remain host policy; this is not a universal no-dump guarantee.

One fresh interpreter per message adds startup overhead and an extra output-copy
step. In bundle mode, reserve disk space for the worker's temporary source and
attachment copies as well as final output. Worker-local allocations disappear
with the process, but without a Linux memory budget a single message can still exhaust host memory before its
time budget expires. Do not interpret the existing 64 MiB source limit as a
64 MiB resident-memory bound.

Parent publication still uses the existing writers: a file can be visible while
being copied, and ordinary exceptions trigger cleanup. Output plus report receipt
are **not one transaction**. Parent SIGKILL/power loss, repeated interruption
during cleanup, and a machine-wide resource failure remain outside recovery
guarantees. Reruns remain collision-safe, not deduplicated or resumable. See the
[base Takeout contract](gmail-takeout.md) for immutable-source and dialect limits.

## Practitioner sources and design decisions

Reviewed September 18, 2026; these are architectural precedents, not claims of
novel parser research or universal format conformance.

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

## Validation and follow-through

The new tests are included automatically by the existing Linux/macOS/Windows
MBOX matrix. They exercise real child processes for hangs, abrupt `os._exit`,
noisy output, invalid receipts and cancellation. Faults are injected by a
**test-only command builder**, not a production environment flag or an email
instruction. Abrupt exit is a controlled crash surrogate, not an observed crash
in a native MIME library.

Separate integration cases launch the actual installed module and compare all
produced bytes, metadata/provenance and diagnostics against direct conversion,
for flat/bundle output and dry/non-dry runs. Additional tests cover copy failure,
collision safety, receipt identity/types, non-finite/duplicate JSON fields, large
time budgets, archive continuation and unchanged default dispatch.

The local environment could run 42 supervisor/contract checks with lightweight
core stand-ins but lacks the MIME dependencies; full package and actual-worker
validation runs in GitHub CI. PR check results and their commit SHAs are the
current evidence. No private email was used or uploaded, and a real authorized
multi-GB Takeout archive has not been tested; that gap is tracked in
[#138](https://github.com/BigCactusLabs/dead-letter/issues/138).

Resource-budget tests (`tests/core/test_mbox_budgets.py`) run the capability
matrix on every platform by patching the platform name, and use actual worker
processes for exceeded CPU and output budgets, withheld artifacts, later-record
continuation, output parity when budgets are satisfied, and an injected
`setrlimit` failure. The memory case runs only on Linux; CPU/output worker cases
skip on Windows with an explicit reason. CPU and memory overruns are driven by
the same test-only command builder replacing one private pipeline function.

The [synthetic worker benchmark](../project/2026-09-24-mbox-worker-benchmark.md)
measures startup/copy overhead and defers reuse. Next foundation work remains:
obtain representative real-corpus evidence; Windows Job Object budgets
and a macOS memory control (#140 follow-up); and design durable resume around both file publication
and report receipts, tracked in
[#139](https://github.com/BigCactusLabs/dead-letter/issues/139). None is
implied by this timeout option.
