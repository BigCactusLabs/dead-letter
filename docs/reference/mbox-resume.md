# Resumable MBOX imports

**Availability: unreleased (#139).** Not available in 0.4.5. Opt-in CLI/Python
resume supports one immutable, flat `.mbox` export with either Markdown files
or Cabinet-style bundles. Compressed input, dry runs, MCP and web/UI resume are
not supported. Ordinary conversion keeps its existing behavior.

## Use

Choose a fresh output directory and enable resume on the **first** run, then
rerun the same command after interruption:

```bash
# Flat Markdown output.
dead-letter convert archive.mbox --output markdown/ --mbox-resume --report

# Markdown, source messages and retained attachments together.
dead-letter convert archive.mbox --output Cabinet/ --mbox-resume --mbox-bundles --report
```

```python
from contextlib import closing
from dead_letter.core.mbox_import import convert_mbox

with closing(convert_mbox(
    "archive.mbox", output="Cabinet", resume=True, bundles=True,
)) as results:
    for result in results:
        print(result.success, result.output, result.recovery, result.error)
```

Source files are never modified, moved or deleted. This mode does not adopt
outputs from earlier non-resume imports, deduplicate different exports, or
silently overwrite a file that happens to have the expected name. Conflicts
stop the import. Keep edited Markdown and attachments separately from managed
output while an import is still resumable.

Timed workers and their supported budgets work with both layouts. Select them
on the first run and retain the same settings on reruns:

```bash
dead-letter convert archive.mbox --output Cabinet/ --mbox-resume --mbox-bundles \
  --report --mbox-timeout 30
```

See [worker limits](mbox-workers.md) before choosing a timeout or resource budget.
The example value is not a performance recommendation. Worker crashes/timeouts
remain per-record failures; a later resume retries failed records.

## Source and conversion identity

The journal is `output/.dead-letter-resume/journal.sqlite3`. Its versioned
contract binds the full source SHA-256 and size, resolved source/output paths,
conversion options, flat/bundle layout, quoting/framing policy, message/line
limits, worker settings, Python version, dependency versions and a fingerprint
of the installed core Python sources. Editable-checkout changes invalidate reuse
too. Do not upgrade or edit the converter halfway through an import; use a fresh
destination for a changed contract. There is no force/ignore-mismatch switch.
Journals from the earlier flat-only development revision are incompatible with
this changed converter, not silently migrated.

Each record binds the scanner's ordinal, original stored-byte hash and offsets.
The archive is hashed with bounded reads before each run, then scanned in source
order; every completed output file is also hashed before reuse. Resume saves MIME
conversion work, not the full-source verification/framing pass. It is not a
constant-time seek checkpoint. The source must remain immutable during a run;
metadata checks detect ordinary replacement/mutation, not adversarial restoration
of timestamps or an operating-system snapshot.

`--report` is excluded from the conversion identity. It can be added on a rerun
to rebuild a lost report without reconverting completed messages.

## Publication and recovery

The journal tracks `started`, `prepared`, `complete`, and `failed` records.
Before converting, it commits ownership of one private staging directory. The
existing converter writes there. On success the parent flushes the complete
output and records fingerprints and diagnostics as a prepared receipt. Only
then does it publish, sync directories where supported, and commit completion.

**Flat output** uses a same-filesystem, no-replace hard link. A destination that
appears during publication is accepted only when its bytes match the prepared
size/hash. There is no fallback to an overwriting rename or partially visible
copy when hard links are unavailable. Normal completion removes the private
staged copy, not final Markdown.

**Bundles** retain the usual `<index>-<hash>/message.md`, `source.eml` and optional
`attachments/` layout. A receipt inventories every retained file's relative name,
size and SHA-256, the presence of the attachment directory, and the staged bundle
directory's device/inode identity. Zero-byte attachments and empty attachment
directories remain distinguishable from absent ones. The complete directory is
published in one no-replace rename; final output is never assembled file by file.

| Platform | Bundle publication |
| --- | --- |
| Linux | `renameat2(RENAME_NOREPLACE)` through libc. |
| macOS | `renamex_np(RENAME_EXCL)` through libc. |
| Windows | `os.rename`, which refuses an existing destination. |

The actual filesystem is probed before message conversion, including refusal to
replace an **empty** destination directory. An unavailable primitive, unsupported
filesystem, or different staging/output filesystem fails with
`mbox_resume_unsupported`. No overwriting/copy fallback is used. Python rename
audit events are preserved for the libc calls.

| State found on restart | Action |
| --- | --- |
| Complete receipt and matching output | Return the existing Markdown path without invoking MIME conversion or a worker, writing the journal, or syncing directories. |
| Prepared receipt and matching final output | Commit the missing completion receipt; do not create a second output. |
| Prepared receipt, no final output, matching staged output | Publish the already-converted file or whole bundle and commit completion. |
| Failed/unprepared record, or a wholly missing completed output | Retry at the original stable name. |
| Modified output/staging, missing or added bundle members, unexpected contents, linked artifact or inconsistent receipt | Stop with a conflict; do not overwrite, suffix around, or patch the conflicting output. |

A replaced bundle directory is a conflict even if all its bytes match: it is not
the directory whose identity was recorded before publication. A missing *whole*
bundle can be retried; a missing attachment or `source.eml` inside an existing
bundle is treated as an edit, not permission to repair that directory. Symlinks,
junctions, nested attachment directories and special files are rejected.

Unprepared partial bundle staging is retained inside the private state directory
as `.abandoned-<record>-<attempt>` before retry, rather than recursively deleting
uncertain attachment data. Unexpected layout is a conflict. Successful publication
moves the complete bundle out and removes only its empty staging wrapper. Recovery
does not recursively delete final output. Retained abandoned attempts consume disk
space and can contain private mail; inspect them locally after stopping all imports
before any manual removal. Do not remove the active journal or lock to clear a conflict.

The SQLite database grows with record count; only the current record is loaded
into Python. Receipts are capped at 1 MiB, bundle manifests at 4096 total files,
and the SQLite page cache is configured for approximately 2 MiB. Duplicate JSON
receipt keys are rejected. These bounds are not a cap on the existing MIME parser's
memory use. Reserve space for output, journal, one staged output/bundle, retained
abandoned attempts, existing message/worker staging, and report spooling.

## Results and reports

`MboxConversion.recovery` is `None` for ordinary imports. Resume results add:

```json
{"status": "reused", "attempt": 1}
```

Statuses are `new`, `reused`, `recovered`, or `retried`. `attempt` increments
before a new conversion attempt, including one interrupted before parsing.
`success` and `error` still determine whether that attempt succeeded; a `retried`
record can fail again. Report entries retain source order, output paths, original
`mbox` provenance and diagnostics, with this optional `recovery` object added.
Bundle output paths refer to `message.md`, not the directory or a receipt manifest.

For resume runs, `mbox_options` adds `resume: true` and `recovery_counts` for the
four statuses. The existing `summary.written` counts successful output references,
**including reused outputs**, not just newly written files. Use recovery status
and `success` together to measure fresh work.

Resume reports use collision-safe names: `.dead-letter-report.json`, then
`.dead-letter-report-2.json`, and so on. Earlier reports, including user edits,
are not overwritten. A report interrupted after a message's durable journal
commit may omit that message. Rerunning with `--report` rebuilds source-order
entries from verified receipts and new results. Neither a completed output nor a
report by itself is treated as a completion receipt.

A message whose receipt (mainly its diagnostics) would exceed 1 MiB is a
per-record failure with `mbox_resume_receipt_limit`, not a fatal error. Its
result has `success: false`, its `mbox` provenance and no output or diagnostics.
The staged Markdown is removed, nothing is published for that record, and the
import continues. A rerun retries the record at its stable name; with unchanged
options it fails the same way without blocking or overwriting other records.
With timed workers, the worker's own 1 MiB result cap reports such a message as
`mbox_worker_invalid_result` instead. In bundle mode an oversized bundle
inventory or receipt also uses `mbox_resume_receipt_limit`.

The following stop the import. Conflicts use `mbox_resume_conflict`;
incompatible contracts use `mbox_resume_mismatch`; a competing writer uses
`mbox_resume_busy`. MBOX framing failures use `mbox_archive_error` with the same
fixed message as a non-resume import. Filesystem/database failures use
`mbox_resume_io_error` and retain state for inspection/retry. When hard-link
publication fails with `EPERM`, `ENOTSUP`, `EOPNOTSUPP` or `EXDEV`, the message
is "Output filesystem does not support hard links required for resume". Other
messages name only a known errno or SQLite result code, such as `ENOSPC`, never
paths or OS-provided text. Fatal resume errors, including an unreadable source,
are yielded with `mbox is None`, not raised and not given a fabricated message
identity. CLI success remains 0, errors 1, and Ctrl-C 130. Unsupported input
combinations are rejected before conversion; a failed bundle-publication probe
may leave an empty state store but no converted message output.

## Concurrency, durability and limits

A persistent lock file is held for the iterator's lifetime: POSIX `flock`, or a
Windows nonblocking byte-range lock. A competing resume writer fails rather than
waiting indefinitely. The OS releases the lock when the process dies; **do not
delete the lock file** to clear it. Python callers must close an iterator they
stop consuming. Non-resume runs and external editors do not participate in this
lock, so do not write to the same output directory concurrently.

SQLite uses rollback-journal `DELETE` mode with `synchronous=EXTRA`. Staged file
contents are flushed before the prepared receipt. Each newly converted record
therefore costs several file, directory and database flushes, so a first resume
run is noticeably slower than an ordinary import; reruns only hash and verify
completed outputs. POSIX directory flush failures
stop the import; Windows has no portable directory-flush operation here, so its
power-loss durability is weaker. The database and output still are **not one
atomic filesystem transaction**; reconciliation bridges their process-crash gap.
This is not a universal power-loss guarantee. Hardware/filesystems can violate
flush or lock expectations. Use a trusted local filesystem with the appropriate
publication support, not a network share or synchronized/cloud folder. Hostile
concurrent filesystem mutation and edits to private journal state are outside
the contract. Do not relocate or restore managed bundles while retaining the old
journal: directory identity is part of bundle reuse.

The state directory contains local source paths, hashes, conversion diagnostics
and bundle attachment filenames; leftover staging can contain message text and
attachment bytes. Treat the entire directory as private, not a model-safe summary.
Do not edit its database or copy it while an import is running. Deleting the
journal loses resumability; existing outputs then conflict rather than being
silently adopted. A hard-killed timed-worker parent can still leave the existing
worker's system-temporary files; resume does not change process-tree cleanup guarantees.

The automated tests use synthetic archives and actual process exits, including
partial attachment writes, output publication and the journal/report gap in direct
and worker modes. They do not simulate every hardware power-loss behavior.
Real multi-GB Takeout acceptance remains
[#138](https://github.com/BigCactusLabs/dead-letter/issues/138).

## Design references

- [SQLite atomic commit](https://sqlite.org/atomiccommit.html): journal ordering,
  locks and the limits of filesystem flush assumptions.
- [SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous):
  `EXTRA` adds a directory sync after rollback-journal unlink in `DELETE` mode.
- [Python file operations](https://docs.python.org/3/library/os.html#os.link):
  hard links and Windows rename behavior; ordinary Unix rename can replace destinations.
- [Linux rename](https://man7.org/linux/man-pages/man2/rename.2.html):
  `RENAME_NOREPLACE` requires filesystem support.
- [Apple XNU rename manual](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/man/man2/rename.2):
  `RENAME_EXCL` rejects an existing destination.
- [Windows byte-range locks](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking):
  nonblocking lock acquisition and explicit unlock.

These references explain the primitives, not proof of this implementation's
power-loss behavior. See [the Takeout guide](gmail-takeout.md) for framing and
source-preservation constraints.
