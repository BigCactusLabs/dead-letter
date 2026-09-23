# Gmail Takeout / MBOX to Markdown and Cabinet

**Availability:** shipped in the CLI and Python API in the 0.4.0 release (#103).
Install any CLI/Python route from the
[installation and distribution map](distribution.md); once dead-letter is
installed, run the commands below without a `uv run` prefix. Keep `uv run` only
when working from a development checkout. The MCP server and web UI remain
EML-only: MBOX import is not available through either surface (tracked: MCP
ingestion [#145](https://github.com/BigCactusLabs/dead-letter/issues/145), web/API
import [#146](https://github.com/BigCactusLabs/dead-letter/issues/146)). Watch
mode and recursive EML directory conversion also remain EML-only. No Google
login, API key, or hosted email processing is needed.

## Convert an export

Export Mail from [Google Takeout](https://takeout.google.com/), download it, and
extract the downloaded ZIP/TGZ locally. Select an actual **flat `.mbox` file**,
not the compressed download or an Apple Mail `.mbox` directory. Work on an
immutable export, never an actively written system mailbox. Google documents
that exports include messages, headers, attachments and label information in
[its Gmail export guide](https://support.google.com/mail/answer/10016932?hl=en).

```bash
dead-letter convert "Takeout/Mail/All mail.mbox" --output markdown/ --report
```

This writes one `.md` per message plus `.dead-letter-report.json`. Without
`--output`, the destination is `<archive-stem>.markdown/` next to the archive.
Output names look like `00000001-0123456789abcdef.md`: one-based source ordinal
and a short SHA-256 prefix, **not the subject**. Duplicate, blank, non-Latin, and
path-like subjects cannot collide within an import or become filesystem paths.
Rerunning does not overwrite earlier outputs; existing names receive suffixes.
This is collision safety, not deduplication or resumable import.

The Markdown is suitable for an Obsidian vault or local RAG preprocessing.
Attachments are listed as metadata in this mode; they are not extracted.
Conversion does not embed/index messages, group separate archive records into
conversations, interpret historical messages as current tasks, or upload data.

## Preserve source messages and attachment bytes

```bash
dead-letter convert "Takeout/Mail/All mail.mbox" \
  --output Cabinet/ --mbox-bundles --thread-mode structured --report
```

```text
Cabinet/
  .dead-letter-report.json
  00000001-0123456789abcdef/
    message.md
    source.eml
    attachments/
      figures.csv
```

These use the existing Cabinet-style bundle layout and the same MIME parser,
attachment handling, renderer and diagnostics as EML conversion. The selected
conversion options still apply, including intentionally stripped attachments.
`source.eml` contains the extracted record with the selected quoting policy;
the original `.mbox` is always retained. **`--delete-eml` is rejected for MBOX**,
including during a dry run. Preserve the original archive for lossless recovery.

## Metadata and identity

Alongside subject, sender, date and attachments, output contains:

- `gmail_labels`: the entire normalized `X-Gmail-Labels` value, when present.
  It is a string, not a guessed comma-split taxonomy; the original header bytes
  remain in the archive and, with default quoting, the source EML.
- `message_id` and `gmail_thread_id`, when present. Missing or duplicate IDs
  never cause messages to be dropped.
- `source_mbox`: archive basename, one-based index, envelope/message/end byte
  offsets, stored size, full SHA-256 and the selected unescape policy.

The byte range `[message_offset, end_offset)` excludes the outer `From `
envelope but includes any stored trailing blank lines. Its hash covers those
**original stored bytes**, before unquoting. The same unchanged archive produces
the same identities; reordered/modified exports may not. These are source
locators and integrity checks, not cryptographic proof of who sent an email.
Flat Markdown `source` is `<archive>#message-00000001`; bundle `source` is
`source.eml`. Neither points into the temporary working directory.

## Large archives and failures

Reading and reporting are incremental. Only one staged EML and one parsed
message are processed at a time; the JSON entries are spooled to disk rather
than accumulated in a Python list. The default per-message stored-byte limit is
64 MiB, with a 1 MiB physical-line limit. A single message still goes through the
existing in-memory MIME/render pipeline: **64 MiB of source is not a 64 MiB
process-memory guarantee**. Attachments and HTML can amplify memory and CPU.
Increase the source cap deliberately when needed:

```bash
dead-letter convert archive.mbox --output markdown/ --max-message-mib 128 --report
```

Oversized messages and overlong lines are drained with bounded reads, reported
as failures, and scanning resumes at the next supported postmark. Conversion
failures are isolated per record; malformed but recoverable MIME keeps the
existing diagnostics. Review both failures and successful-but-degraded entries.
Message exception text is not copied into the report because it can contain
private content. Use the index, byte range and existing diagnostics to inspect
the original locally.

The reader binds the opened file handle to the named export and checks file
identity, size and modification metadata at message boundaries. Reads never
extend past the size observed when opening the archive. Detected replacement,
append, truncation or rewriting stops the import with an archive error, rather
than processing a moving target; earlier outputs are retained. These checks are
best-effort mutation detection, not a filesystem snapshot or protection against
an attacker restoring metadata. Always work from an immutable export.

Reports retain schema version 1 with `job.input_mode: mbox`, per-result `mbox`
provenance, and `mbox_options`. Source-order result entries include successes and
failures. A fatal archive error has no `mbox` field and is an extra error entry,
not a message; `summary.total` counts result entries. Fatal framing/read errors
mark the job `failed`, even if earlier outputs succeeded. Otherwise a mix of
successes and failures is `completed_with_errors`.

Exit status is 0 for success (including an empty mailbox), 1 for errors, and 130
for Ctrl-C. With `--report`, Ctrl-C during conversion publishes a partial report
with status `interrupted`; completed outputs are retained and incomplete output
is cleaned up. Only complete, committed report entries are published: a signal
midway through an append cannot leave a dangling comma or incomplete JSON token.
File output and report receipts are **not one atomic transaction**. The newest
completed file can be absent from the report if interruption occurs before its
receipt commits. Counts describe committed receipts, not a post-interruption
rescan of the destination. This is not resumability or exactly-once ingestion.
Durable resume is tracked in
[#139](https://github.com/BigCactusLabs/dead-letter/issues/139); a real
multi-GB Takeout corpus has not been validated end-to-end, tracked in
[#138](https://github.com/BigCactusLabs/dead-letter/issues/138).

Report publication is atomic. Ctrl-C during the final report copy returns 130
without a traceback; a failed or interrupted write before replacement leaves a
previous report intact. A previous report may describe an older run, so inspect
its timestamps/status and the files on disk. A hard kill/power loss cannot
guarantee a new report or cleanup. Reserve disk space for outputs, one staged
message, the growing report spool, and its final atomic copy; the spool can
approach the report size.

`--dry-run` parses and validates using temporary storage but creates no message
outputs. `--dry-run --report` explicitly writes a report. Choose separate output
directories for simultaneous imports: report publication is last-writer-wins.

## Optional timed message workers

Byte limits do not stop a stuck parser or a native-library crash. Shipped
alongside the base importer in 0.4.0, `--mbox-timeout` opts into a per-message
budget (#118, follow-up to #103):

```bash
dead-letter convert archive.mbox --output markdown/ --report --mbox-timeout 30
```

Each admitted message runs in a fresh subprocess using the same conversion
pipeline. A timed-out or abnormally exited worker yields a named message failure;
later messages can continue, and that worker's partial files are never published
to the final destination. Default conversion stays in-process. The setting is
recorded in `mbox_options.timeout_seconds`, including `null` when disabled.

This is **not a memory cap or an OS security sandbox**. It adds process startup
and temporary-copy overhead and does not time-limit framing or final publication.
Hard-killed-parent recovery and durable resume remain unimplemented. See the
[worker contract and practitioner sources](mbox-workers.md) for error codes,
Python usage, tests, and precise limits.

## Dialects: do not guess away quoting

By default `--mbox-unescape preserve` retains `>From ` body text. When the export
writer's dialect is known:

```bash
# Undo one level of mboxrd quoting in body lines only.
dead-letter convert archive.mbox --output markdown/ --mbox-unescape mboxrd

# Historical mboxo quoting is intrinsically ambiguous; opt in deliberately.
dead-letter convert archive.mbox --output markdown/ --mbox-unescape mboxo
```

The scanner recognizes ctime-style postmarks, including Gmail-style numeric
timezones before the year, LF/CRLF, and a final line without a newline. It does
not split ordinary prose merely because it starts with `From `. A literal,
unescaped line that exactly resembles a valid postmark is inherently ambiguous
in this format; there is no universal reliable detector.

Unsupported inputs fail explicitly when detectable: non-empty preambles and
`Content-Length`-framed mboxcl/mboxcl2 mailboxes are refused, not silently treated
as another dialect. mboxcl/mboxcl2 are not supported yet; validated
Content-Length framing is tracked in
[#143](https://github.com/BigCactusLabs/dead-letter/issues/143). Folded
continuation text containing `Content-Length:` is not a new storage header.
Content-Length refusal is archive-fatal because continuing could misidentify
body text as additional messages. Unknown postmark syntaxes, compressed files
(compressed Takeout archives are tracked in
[#144](https://github.com/BigCactusLabs/dead-letter/issues/144)), live mail
spools, PST/MSG and Apple Mail bundle directories are outside this slice. See
the [implementation history](../project/2026-09-18-mbox-ingestion.md).

## Python: consume lazily

```python
from contextlib import closing
from dead_letter.core.mbox_import import convert_mbox

with closing(convert_mbox("archive.mbox", output="markdown")) as results:
    for result in results:
        print(result.source, result.success, result.output, result.error)
```

Do not wrap a large import in `list(...)`. The lower-level
`dead_letter.core.mbox.iter_mbox` exposes one temporary `.eml` at a time; read or
copy its path before advancing the iterator, and close it when stopping early.
Python callers can set both resource limits through `MboxLimits`; byte counts
must be integers, not floats or booleans.
