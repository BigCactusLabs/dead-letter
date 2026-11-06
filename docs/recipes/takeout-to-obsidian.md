# Move a Gmail Takeout export into an Obsidian vault

**Problem.** Google Takeout delivers your Gmail as one large MBOX file inside a
ZIP. You want each message as a note in Obsidian, with its labels, date, and
attachments, without uploading your mail anywhere or signing in to Google
from a tool.

dead-letter reads the Takeout ZIP on your own machine and writes one folder
per message: a Markdown note with YAML properties, a copy of the original
message, and the decoded attachments. Obsidian opens the result as an
ordinary folder of notes; no plugin is involved. This page was tested with
the **released 0.4.10** CLI. Which releases and surfaces (CLI, Python, web,
MCP) handle MBOX, ZIP input, and resume is kept in the
[Gmail Takeout guide](../reference/gmail-takeout.md); check it before relying
on a different version.

## Try it on the synthetic sample first

The [sample](takeout-sample/) is a small Takeout-shaped download with five
synthetic messages from the Cedar Works story used by the other recipes. It
has Gmail labels, two pairs of messages that share a Gmail thread, a CSV and
a PDF attachment, and an HTML newsletter. It contains no real mail.

| File | What it is |
| --- | --- |
| [`takeout-20260921T120000Z-001.zip`](takeout-sample/takeout-20260921T120000Z-001.zip) | The input, shaped like a Takeout download |
| [`Takeout/Mail/All mail Including Spam and Trash.mbox`](<takeout-sample/Takeout/Mail/All mail Including Spam and Trash.mbox>) | The same MBOX, extracted so you can read it |
| [`vault/`](takeout-sample/vault/) | The expected vault, produced by released 0.4.10 |
| [`capture.json`](takeout-sample/capture.json) | Version, resolved packages, exact commands, and their output |

With the [CLI installed](../reference/distribution.md), convert a copy of the
ZIP in an empty working folder so the committed sample stays untouched:

```bash
mkdir takeout-trial
cp docs/recipes/takeout-sample/takeout-20260921T120000Z-001.zip takeout-trial/
cd takeout-trial
dead-letter convert takeout-20260921T120000Z-001.zip --output vault/Email/Gmail --mbox-bundles --report
```

It prints `MBOX: 5 succeeded, 0 errors` and writes:

```text
vault/Email/Gmail/
├── .dead-letter-report.json
├── 00000001-bb82fa241bd345d2/
│   ├── message.md
│   ├── source.eml
│   └── attachments/order-4821.csv
├── 00000002-2363d420ed4d4957/
├── 00000003-a8d4f44e6c9f4392/
├── 00000004-743a237f650af168/
│   └── attachments/dock-3-map.pdf
└── 00000005-a53ee1f0d3803c19/
```

`diff -r vault ../docs/recipes/takeout-sample/vault` should show differences
only in the report's timestamps, duration, and the archive's inode and file
times. The notes, source copies, and attachments match byte for byte. The ZIP
itself is left unchanged; its SHA-256 before and after conversion is recorded
in [`capture.json`](takeout-sample/capture.json).

In Obsidian, choose **Open folder as vault** and select `vault`.

## What Obsidian shows

Checked on 2026-11-05 with Obsidian 1.12.4 on macOS, using a copy of the
sample vault.

**Note names.** Every note is called `message`, because each bundle stores
its note as `message.md`. The folder name (`00000001-bb82fa241bd345d2`) is the
one-based position in the archive plus a hash prefix, not the subject. The
subject is in the `subject` property. Search results and tabs therefore all
read `message`.

**Properties.** The first sample note has these properties:

```yaml
subject: Cedar Works order 4821 - revised delivery
sender: mara@cedarworks.example
date: '2026-09-15T13:20:00+00:00'
attachments:
- order-4821.csv
gmail_labels: Inbox,Important,Opened,Clients/Cedar Works
message_id: <order-4821@cedarworks.example>
gmail_thread_id: '1843002176537310123'
attachment_files:
- attachments/order-4821.csv
```

Obsidian shows `subject`, `gmail_labels`, `message_id`, and `gmail_thread_id`
as text; `date` is also text, not an Obsidian date property. `sender` is shown
as a mail link. `attachments` and `attachment_files` are lists of plain text.
The `source_mbox` provenance block (archive name, index, byte offsets,
SHA-256, and ZIP member details) is a nested object. Obsidian's Properties
panel has no type for that and shows it as one line of raw JSON with an
unknown-type icon. It is still valid YAML for scripts and other tools.

**Attachments.** The PDF appears in the file explorer and opens in Obsidian's
PDF viewer. The CSV and every `source.eml` are on disk next to the note, but
the explorer does not list them, because Obsidian hides file types it does
not open. The note body has no link to its attachments, and
`attachment_files` is plain text, so Obsidian draws no clickable link or
backlink to the file.

## Labels are a single string, not tags

`gmail_labels` is the `X-Gmail-Labels` header value, kept as one string. It is
not split at commas, not mapped to Obsidian tags, and not turned into
folders. Label names can contain spaces and slashes (`Clients/Cedar Works`),
and rather than guess how a value should split, dead-letter keeps it whole.
In 0.4.10 a
folded header (one continued onto a second line) keeps its line break inside
the string.

Obsidian's property search finds labels anyway. Searching
`[gmail_labels:"Cedar Works"]` in the sample vault returns the two notes
carrying `Clients/Cedar Works`. If you want tags, add them in your own
post-processing step, where you decide how to split label names.

## Conversations are not assembled

Each archive record becomes its own note. Notes 1 and 4 share
`gmail_thread_id: '1843002176537310123'`, and notes 2 and 3 share another
thread ID, but they stay separate notes; nothing merges them into a
conversation or links one to the next. The default `--thread-mode latest`
keeps only the text each sender wrote, so note 4 shows the confirmation
without Mara's quoted lines. `--thread-mode structured` keeps detected quoted
history inside each note; it still does not merge records. To gather a
conversation, search on the shared `gmail_thread_id` value.

## Attachment names, bytes, and searchable text are different

| What you get | Flat Markdown (no `--mbox-bundles`) | Bundles (`--mbox-bundles`) |
| --- | --- | --- |
| Attachment filename in the `attachments` property | Yes | Yes |
| Decoded attachment file on disk | No | Yes, in `attachments/`, listed in `attachment_files` |
| Attachment content in the note text | No | No |

dead-letter does not extract text from attachments. In the sample vault,
Obsidian search found nothing for `unloading` (text inside the PDF) or
`notebook` (a value in the CSV). The filenames are searchable because they
appear in the properties. Conversion options still apply to bundles, so an
attachment you choose to strip is not retained.

## Running the command again does not deduplicate

Running the same command into the same output folder converts every message
again. The sample's [capture](takeout-sample/capture.json) records a second
run: the folder then held ten bundles, the second set named with a `-2`
suffix (`00000001-bb82fa241bd345d2-2`), and `.dead-letter-report.json` was
replaced by a report listing only the second run. Existing files are never
overwritten, but duplicates are not detected. Use a new output folder for each
import, or the resume mode below for an import that stopped part-way.

## Convert your own export

1. In [Google Takeout](https://takeout.google.com/), export **Mail** and
   download the result. Keep the download as it is; work from it, never from
   a mailbox something is still writing to.
2. Convert the ZIP into a folder inside your vault, using your file's real
   name:

   ```bash
   dead-letter convert takeout-20261105T120000Z-001.zip --output ~/Vault/Email/Gmail --mbox-bundles --report
   ```

   If the ZIP holds more than one `.mbox`, name the one you want with
   `--mbox-member`. See the [Takeout guide](../reference/gmail-takeout.md#compressed-input)
   for supported containers and staging space.
3. For a large export, extract the `.mbox` first and turn on the resume
   journal from the first run. If the import stops, run the identical
   command again; finished bundles are verified and reused rather than
   duplicated:

   ```bash
   dead-letter convert "Takeout/Mail/All mail Including Spam and Trash.mbox" \
     --output ~/Vault/Email/Gmail --mbox-resume --mbox-bundles --report
   ```

   The CLI resumes a flat `.mbox` only. The
   [resume contract](../reference/mbox-resume.md) lists its requirements.
4. Open `.dead-letter-report.json` and review failures and degraded entries.
   The [report recipe](report-and-diagnostics.md) and
   [quality diagnostics](../reference/quality-diagnostics.md) explain the
   fields.

The archive is never moved or deleted: MBOX conversion only reads it, and
`--delete-eml` is rejected for MBOX input. Bundles store a copy of every
message plus decoded attachments, so allow more free space than the size of
the `.mbox`.

This page demonstrates a five-message sample. It does not show how dead-letter
or Obsidian behave with a full multi-gigabyte account; real-export acceptance
is tracked in [#138](https://github.com/BigCactusLabs/dead-letter/issues/138).
The [Takeout guide](../reference/gmail-takeout.md#large-archives-and-failures)
describes the per-message limits.

## How the sample was made

[`scripts/render_takeout_sample.py`](../../scripts/render_takeout_sample.py)
builds the MBOX and ZIP, installs dead-letter 0.4.10 from PyPI into a
temporary virtual environment (dependencies resolved as of 2026-10-30),
converts the ZIP there, runs the second conversion described above in a
throwaway copy, and writes this folder. It is a maintainer tool and is never
run in CI or tests.

Two messages come from the recipes'
[synthetic inbox](fixtures/inbox/) with Takeout headers added; three are new.
The file follows Takeout's layout and envelope and uses the `X-GM-THRID` and
`X-Gmail-Labels` headers, but it was written by hand: it is not a byte-for-byte
Google export (for example, it uses LF line endings). The sample's
[README](takeout-sample/README.md) explains why it ships as a committed folder.
