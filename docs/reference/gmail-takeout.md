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

Unsupported inputs fail explicitly when detectable: non-empty preambles are
refused, and in `preserve`, `mboxrd` and `mboxo` modes a top-level
`Content-Length` field is refused rather than silently ignored; select
[`mboxcl` or `mboxcl2`](#content-length-framing-mboxcl-and-mboxcl2) for
length-framed archives. Folded continuation text containing `Content-Length:`
is not a new storage header. That refusal is archive-fatal because continuing
could misidentify body text as additional messages. Unknown postmark
syntaxes, compressed files (compressed Takeout archives are tracked in
[#144](https://github.com/BigCactusLabs/dead-letter/issues/144)), live mail
spools, PST/MSG and Apple Mail bundle directories are outside this slice. See
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
  header fields named `Content-Length` (any case) are read; a
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
