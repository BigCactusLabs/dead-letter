# Gmail Takeout / MBOX to Markdown and Cabinet

**Availability:** plain MBOX shipped in the CLI and Python API in the 0.4.0
release (#103). Compressed ZIP/TGZ input is **unreleased** (#144).
Install any CLI/Python route from the
[installation and distribution map](distribution.md); once dead-letter is
installed, run the commands below without a `uv run` prefix. Keep `uv run` only
when working from a development checkout. The web UI remains EML-only (tracked:
web/API import [#146](https://github.com/BigCactusLabs/dead-letter/issues/146)).
Released MCP servers are EML-only; `main` adds a bounded, unreleased
[`convert_mbox` MCP tool](#mcp-bounded-convert_mbox-unreleased) (#145). Watch
mode and recursive EML directory conversion also remain EML-only. No Google
login, API key, or hosted email processing is needed.

## Convert an export

Export Mail from [Google Takeout](https://takeout.google.com/) and download it.
The unreleased CLI/Python importer can read the ZIP/TGZ directly; see
[compressed input](#compressed-input-unreleased). As an alternative, extract
the download locally and select an actual **flat `.mbox` file**, not an Apple
Mail `.mbox` directory. Work on an
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

## Compressed input (unreleased)

This first #144 slice adds CLI and Python input only. MCP and web/API ingestion
are unchanged and do not accept compressed containers. Use a development checkout
for these examples until a release includes this feature:

```bash
uv run dead-letter convert takeout.zip --output markdown/ --report
uv run dead-letter convert takeout.tgz --output Cabinet/ --mbox-bundles --report
uv run dead-letter convert takeout.tar.gz --mbox-member "Takeout/Mail/All mail.mbox" \
  --mbox-staging-dir /path/to/existing/staging --output markdown/ --report
```

- **Formats:** ZIP (including ZIP64; stored or deflate) and gzip-compressed TAR
  (`.tgz` or `.tar.gz`). Magic bytes must agree with the extension. Plain `.tar`,
  single-file `.gz`, `.bz2`, `.xz`, `.7z` and RAR are unsupported. ZIP requires
  **Python 3.12.3 or newer**, the floor for the overlapped-entry fix
  (CVE-2024-0450); older patch levels refuse ZIP. TGZ has no extra patch floor.
- **Selection:** regular-file names ending in `.mbox`, case-insensitively, are
  candidates. One candidate is automatic; none fails. Multiple candidates fail
  with escaped names (up to 20, each limited to 512 characters in diagnostics).
  `--mbox-member NAME` / Python `member=` selects the exact, case-sensitive archive
  name. Other files, including nested ZIPs, are ignored and never opened as
  members. macOS metadata is never a candidate: AppleDouble entries whose
  basename starts with `._` and anything under a top-level `__MACOSX/` folder
  are ignored even when named `*.mbox`, and cannot be selected with `--mbox-member`. Reports count all non-selected entries as ignored. Each split Takeout
  part is an independent archive: run once per part, preferably into separate
  output directories. Do not concatenate parts. In single-pass TGZ auto-selection,
  the first mailbox may be fully staged before a second candidate is found; that
  staged copy is discarded on the multiple-member error. Select a known member
  explicitly to avoid this ambiguity.
- **Safety:** no member path is extracted. Absolute paths, `..` path segments,
  Windows drive/UNC paths and NUL names are rejected, including in ignored
  entries. Duplicate MBOX names, non-regular MBOX entries (links, directories,
  devices), or encryption/unsupported compression on the selected ZIP member
  fail the whole import before conversion. Ignored ZIP members are never opened;
  their encryption or compression method does not reject the archive. Sparse TAR
  entries are unsupported. Email and archive names are untrusted data and never authorize actions.
- **Staging:** the selected member is streamed to a fixed file in a private
  `TemporaryDirectory`, under `--mbox-staging-dir` / Python `staging_dir=`, or the
  system temporary directory. Budget approximately the uncompressed member size
  **in addition to** output, per-message staging and report space. Both ZIP and TGZ
  check free space against the selected member's declared size before opening it;
  this is a preflight, not a reservation. A missing, non-directory or unwritable
  staging location fails before the archive is read. The original download is
  only opened read-only.
- **Limits:** Python `ArchiveLimits(max_decompressed_bytes=256 * 1024**3,
  max_members=100_000, max_metadata_bytes=16 * 1024**2)` sets the defaults
  (256 GiB expanded bytes, 100,000 members, 16 MiB metadata). Before constructing
  the ZIP index, a bounded tail read checks EOCD/ZIP64 counts and central-directory
  size against the member and metadata limits. The declared count is not trusted:
  the size-bounded central directory is then walked entry by entry, the real count
  is checked against the member limit, and a count that disagrees with the
  EOCD/ZIP64 record is corrupt. The central directory must sit at its recorded
  offset and the file must end with the EOCD record and its comment, so
  self-extracting ZIPs with prepended data and files with trailing bytes after
  the EOCD are rejected as `mbox_archive_corrupt`, even though some unzip tools
  accept them. ZIP also checks selected-member
  declared size and actual bytes read. TGZ counts the entire expanded TAR stream,
  including ignored data, headers and padding, because ignored-member bombs still
  cost decompression CPU. PAX/GNU extension payload bytes count toward the total
  metadata budget, not the member count. Each extension header is limited to
  64 KiB, with at most 64 nested headers. These TAR limits use a private CPython
  `tarfile` hook; if the running Python lacks it or does not call it for every
  header, TGZ input is refused as `mbox_archive_unsupported` rather than parsed
  without limits. Processed TAR member records are
  discarded instead of accumulated. No ratio limit is used.
  The CLI uses the defaults; existing MBOX flags still apply to the staged member,
  including `--mbox-timeout` and the worker resource budgets below, which are
  validated before the archive is read.
- **Integrity and cleanup:** all staging and integrity checks finish before any
  message conversion. ZIP CRC failures and damaged/truncated containers are fatal.
  TAR must reach a real zero-block end marker: a missing marker, short header or
  invalid checksum is corrupt even if the gzip stream itself is valid.
  TGZ uses single-pass `r|gz` with bounded compressed reads plus a parallel gzip
  validator, because the tar reader alone does not verify the gzip trailer. This
  adds a second inflation, but no second source pass; concatenated gzip streams
  and trailing compressed-stream bytes are refused. Temporary staging is removed
  on failure, normal completion, Ctrl-C or explicit iterator close. Hard kills
  cannot guarantee cleanup. `--dry-run` still stages and validates the whole member.

Python exposes the same conversion keywords as `convert_mbox`:

```python
from contextlib import closing
from dead_letter.core import ArchiveLimits, convert_mbox_archive

with closing(convert_mbox_archive(
    "takeout.zip", member="Takeout/Mail/All mail.mbox", output="markdown",
    archive_limits=ArchiveLimits(max_decompressed_bytes=512 * 1024**3),
)) as results:
    for result in results:
        print(result.source, result.success, result.error)
```

For compressed input, `source_mbox.archive` (and each result's `mbox.archive`)
stays a **string**: the container basename, preserving the plain-MBOX field type.
New sibling `source_mbox.container` / `mbox.container` objects contain only
`container_basename`, `format`, `member_name`, `member_compressed_bytes`,
`member_uncompressed_bytes`, ZIP `crc32` (eight hex digits), `member_sha256` and
`staged_bytes`. TAR has no per-member compressed size or CRC32, so those fields
are `null`. Result metadata is copied per record. Plain-MBOX provenance is
unchanged and has no `container` object.

Only the report's top-level `archive` summary adds `container_path` exactly as
supplied, `container_size`, `container_stat_signature` (device, inode, size,
mtime/ctime in nanoseconds) and `ignored_member_count`. Absolute container paths
and filesystem signatures are not copied into Markdown or record provenance.
The report itself still contains local filesystem details; review it before
sharing. No container SHA-256 is computed. The member SHA-256 is computed during
staging. Container handle/path stat signatures are checked before and after
staging; they detect ordinary changes, not adversarial metadata restoration.

Existing record offsets and hashes refer to **decompressed member bytes**, before
MBOX unquoting. Flat `source` uses the member basename; no temporary path appears
in provenance. Reports retain schema 1 and `job.input_mode: mbox`, and add a
top-level `archive` summary even for an empty mailbox. A failed/interrupted
staging operation has an empty summary object. These limits bound admitted
metadata and expanded bytes; they are not process-memory or wall-time guarantees.
The ZIP index has Python-object overhead, and gzip/TAR validation adds CPU work.
Real multi-GB compressed Takeout throughput and cross-platform staging costs
remain unmeasured.

Staging errors use the existing fatal MBOX result/exit path (`mbox is None`,
CLI exit 1; Ctrl-C exits 130). Codes are `mbox_archive_unsupported`,
`mbox_archive_no_mbox`, `mbox_archive_multiple_mbox`,
`mbox_archive_duplicate_member`, `mbox_archive_insufficient_space`,
`mbox_archive_limit_exceeded`, `mbox_archive_corrupt`,
`mbox_archive_python_too_old`, `mbox_archive_changed` and
`mbox_archive_staging_unavailable`. Missing/unreadable archives use the existing
plain-MBOX `mbox_archive_error`; they are not classified as corrupt. Unsafe paths
and non-regular members use `mbox_archive_unsupported`. Archive error messages
escape control characters in member names and OS errors. Existing framing and
conversion errors retain their existing codes after staging.

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

On `main` (unreleased), worker mode also accepts opt-in resource budgets:
`--mbox-cpu-seconds` and `--mbox-max-output-mib` (Linux and macOS) and
`--mbox-memory-mib` (Linux only). An unsupported control is refused before
conversion. Budgets apply to ZIP/TGZ input as well (Python `convert_mbox_archive`
accepts the same `memory_limit_mib`, `cpu_seconds` and `max_output_mib`).
These are resource limits, not filesystem or network isolation; see
[optional resource budgets](mbox-workers.md#optional-resource-budgets).

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

Unsupported inputs fail explicitly when detectable: non-empty preambles are
refused, and in `preserve`, `mboxrd` and `mboxo` modes a top-level
`Content-Length` field is refused rather than silently ignored; select
[`mboxcl` or `mboxcl2`](#content-length-framing-mboxcl-and-mboxcl2) for
length-framed archives. Folded continuation text containing `Content-Length:`
is not a new storage header. That refusal is archive-fatal because continuing
could misidentify body text as additional messages. Unknown postmark
syntaxes, unsupported compressed formats, live mail spools, PST/MSG and Apple
Mail bundle directories are outside this slice. ZIP/TGZ support is described
[above](#compressed-input-unreleased). See
the [implementation history](../project/2026-09-18-mbox-ingestion.md).

### Content-Length framing: mboxcl and mboxcl2

**Availability:** on `main` only, not in 0.4.0 or any published release yet
(see the `Unreleased` section of the [changelog](../../CHANGELOG.md)).

Some local mail tools write a `Content-Length` header that states the body
size. dead-letter reads these archives only when you select the dialect; it
never infers one from the archive.

```bash
# mboxo-style ">From " quoting plus Content-Length.
dead-letter convert archive.mbox --output markdown/ --mbox-unescape mboxcl

# No From quoting; the length alone separates messages.
dead-letter convert archive.mbox --output markdown/ --mbox-unescape mboxcl2
```

Python callers pass `unescape="mboxcl"` or `unescape="mboxcl2"` to
`convert_mbox` or `iter_mbox`.

- **Length.** `N` is the exact number of stored bytes from just after the
  blank line that ends the top-level headers up to, but not including, the
  one line ending before the next postmark. CR bytes count. Only top-level
  header fields named `Content-Length` (any case, with the RFC 5322
  obsolete-syntax spaces or tabs before the colon allowed, as the other
  dialects' refusal check also allows) are read; a
  `message/rfc822` part's own header is body text.
- **Header value.** The unfolded value, trimmed of spaces, tabs and CR, must
  be 1–20 ASCII digits: no sign, no inner whitespace, not empty. Repeated
  fields with the same number are accepted; different numbers are invalid.
- **Validation.** The headers must end before the next postmark or EOF, and
  `body_start + N` must not exceed the file size (an integer check made before
  any seek). At that offset there must be `\n` or `\r\n` followed by a valid
  postmark line within the line limit, or exactly one final `\n` or `\r\n` at
  EOF. `N = 0` is valid when this holds. The check reads at most two bytes and
  one postmark line; the body itself is streamed in the usual bounded reads.
- **Body lines.** Once `N` is valid, lines in the body that look like
  postmarks never start a new message. `mboxcl` removes exactly one `>` from
  body lines that begin with `>From `; `N` counts the stored, quoted bytes.
  `mboxcl2` output equals the stored bytes. Headers are never unquoted.
- **Byte range.** `[message_offset, end_offset)` includes the one separator
  line ending, like the trailing blank line of other dialects, and the SHA-256
  covers those stored bytes.
- **Limits.** Oversized messages and long lines fail that one record as usual
  (`mbox_message_too_large`, `mbox_line_too_long`), and import resumes at the
  validated boundary. Memory never grows with `N`.

A missing or invalid length is handled per dialect:

| Dialect | Missing or invalid `Content-Length` |
|---|---|
| `mboxcl` | That message is split by postmark scanning, as in `mboxo`. Its `source_mbox` provenance and report entry carry `"framing_diagnostic": "mbox_content_length_fallback"` beside its `envelope_offset`. |
| `mboxcl2` | Import stops with an archive error naming the envelope byte offset. Results already emitted remain valid. Scanning would split unquoted body `From ` lines. |

Evidence limits: support is tested with synthetic fixtures, an independent
test-only reference reader, and CPython's `mailbox.mbox` where the two
framings must agree. No archives generated by mutt, Dovecot or other writers
were run. The rules follow Dovecot's [endpoint check](https://github.com/dovecot/core/blob/3103735f1c5ba1ad31dd4357cb975b955a188c03/src/lib-storage/index/mbox/istream-raw-mbox.c#L470-L520)
and are stricter than [mutt's](https://github.com/muttmua/mutt/blob/b74263bb9381b11dc0c08bc1fafa206ed1e53fda/mbox.c#L359-L425),
which compares only five bytes to `From ` at the endpoint. Dialect names
follow [de Boyne Pollard's MBOX survey](https://jdebp.uk/FGA/mail-mbox-formats.html).

## MCP: bounded `convert_mbox` (unreleased)

On `main` only, not in any release yet. The MCP server's `convert_mbox` tool
runs the same importer for small exports, with fixed bounds:

- one flat `.mbox` file (suffix checked case-insensitively); compressed
  archives and Apple Mail `.mbox` directories are rejected;
- at most 256 MiB of source, checked before conversion and again on the bytes
  read. An archive that grows past the cap or changes during the call fails
  with a partial report marked `failed`;
- at most 1000 messages per call. When an archive holds more, the call stops
  cleanly and returns `truncated: true`; the remaining messages are not
  converted and there is no resume. Use the CLI for the whole archive;
- default `preserve` quoting and default per-message limits. Unescape modes,
  `--max-message-mib` and timed workers are CLI/Python-only;
- `output_directory` is required. Output names and the
  `.dead-letter-report.json` report are collision-safe: a second call into the
  same folder writes `.dead-letter-report-2.json` and suffixed messages;
- the response is a bounded summary with counts, the report path and at most
  20 failure entries (index, error code, generic message). It never contains
  message content;
- cancelling the MCP call does not stop the conversion; the bounds limit how
  long it runs.

See the [runtime contract](v4-runtime-contracts.md#mcp-server-dead_letterbackendmcp_server)
for the full inputs, outputs and error text.

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
