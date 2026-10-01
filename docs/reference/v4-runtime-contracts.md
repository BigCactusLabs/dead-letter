---
title: dead-letter v4 Runtime Contracts
doc_type: reference
status: canonical
last_updated: 2026-09-30
audience:
  - maintainers
  - contributors
scope:
  - src/dead_letter/core
  - src/dead_letter/backend
---

# dead-letter v4 Runtime Contracts

This document is the canonical runtime contract reference for v4 core and backend behavior. Other docs (README and phase plans) are summaries and should defer to this file when there is any ambiguity.

## Core Conversion API (`dead_letter.core`)

### `convert(path, *, output=None, options=None) -> ConvertResult`

Converts one `.eml` file.

Rules:

- `path` must have `.eml` suffix.
- Writes markdown output unless `dry_run=True`.
- If `output` is omitted, writes next to source using a slugified subject filename. If the subject slugifies to empty (for example a subject in a non-Latin script with no ASCII decomposition), falls back to the slugified source filename stem, and only then to `email`.
- A `Subject` with an unsupported or non-text charset decodes as UTF-8 with replacement characters; a malformed RFC 2047 encoded word is kept as its original text. Neither aborts conversion.
- `embed_inline_images` data URIs are single-line: MIME base64 line folding is removed from the URI, not from attachment bytes.
- `output` is treated as the Markdown filename when it ends in `.md`, unless it ends with a path separator or names an existing directory; otherwise it is a directory and the slugified filename is written inside it.
- With `strip_signature_images` or `strip_tracking_pixels`, a stripped inline asset is omitted from rendered Markdown and attachment output only when no remaining body image references its Content-ID. With `embed_inline_images`, rendered data URIs count as references, so embedded images keep their attachment files and metadata.
- If output path collides, appends incrementing suffix (`-2`, `-3`, ...).
- If `delete_eml=True`, source deletion occurs only after successful write.
- If source deletion fails after writing markdown, the written markdown file is removed and conversion returns failure.
- `delete_eml` is disabled when `dry_run=True`.
- If the source file is missing at conversion time, conversion returns a failed `ConvertResult` instead of raising.

### `convert_dir(directory, *, output=None, options=None) -> list[ConvertResult]`

Converts all `.eml` files under a directory (recursive).

Rules:

- Processes files in sorted order.
- Matches `.eml` suffixes case-insensitively.
- Skips symlinked files whose resolved paths escape the requested directory tree.
- Deduplicates in-tree alias paths that resolve to the same source file.
- Returns one `ConvertResult` per file.
- In directory mode with `output` set, source-relative subdirectories are mirrored under output root.
- `output` is always a directory, even when a component ends in `.md`; output subdirectories are created only when a file is written, so `dry_run=True` creates nothing.

### `convert_to_bundle(path, *, bundle_root, options=None, source_handling="move") -> BundleResult`

Converts one `.eml` file into a self-contained bundle directory.

Rules:

- `path` must have `.eml` suffix.
- Bundle directories are created under `<bundle_root>/<source-stem>` with collision-safe numeric suffixes when needed.
- `message.md` is always the bundle markdown filename.
- Retained extracted attachments and calendar files are written under `attachments/` when present. Inline signature/tracking assets stripped from the rendered output, or inline CID assets no longer referenced by the retained output, are omitted.
- Attachment filenames are normalized to safe basenames before writing; directory segments from MIME-provided names are stripped.
- A named attachment with an empty payload is written as a zero-byte file. A metadata-only part with no payload creates no file. A text attachment whose declared charset cannot encode it (unknown, non-text, `idna`, `undefined`) is written as UTF-8.
- An embedded `message/rfc822` part (e.g. Outlook/Apple Mail "Forward as Attachment") is recorded as an attachment with content type `message/rfc822`; its filename is the part's own filename if present, else the embedded message's slugified `Subject` plus `.eml`, else `forwarded-message.eml`. Parts with `Content-Transfer-Encoding: base64` or `quoted-printable` are decoded first, so the attachment payload is the original embedded-message bytes rather than a double-encoded copy; otherwise the part is re-serialized as-is. That payload is written under `attachments/` like any other attachment. Body text (plain and HTML) is collected only from the top-level entity, so the embedded message never replaces or leaks into the outer body; this also applies to `multipart/digest` containers.
- When retained attachments are written, markdown front matter includes relative `attachment_files` entries such as `attachments/logo.png`.
- `source_handling="move"` moves the original `.eml` into the bundle root.
- `source_handling="copy"` copies the original `.eml` into the bundle root and leaves the source in place.
- `source_handling="delete"` removes the source after successful bundle creation and leaves no `.eml` artifact in the bundle.
- `source_handling` is the only retained-source control for this API; `ConvertOptions.delete_eml` does not change bundle behavior.
- In `dry_run=True`, planned bundle paths are returned but no bundle directory, attachments, markdown, or source moves/copies/deletes are performed.
- If bundle creation fails after filesystem work has started, any partial bundle directory is removed.
- If the source file is missing at conversion time, conversion returns a failed `BundleResult` instead of raising.

### `ConvertOptions`

`options` uses this fixed field set:

- `strip_signatures`
- `strip_disclaimers`
- `strip_quoted_headers`
- `strip_signature_images`
- `strip_tracking_pixels`
- `embed_inline_images`
- `include_all_headers`
- `include_raw_html`
- `no_calendar_summary`
- `allow_fallback_on_html_error`
- `allow_html_repair_on_panic`
- `delete_eml`
- `dry_run`
- `thread_mode` — `"latest"` (default) or `"structured"`. Structured mode appends per-message sections for prior replies and forwarded messages. Gmail HTML forwards and unquoted plain-text forward separators are kept in latest mode too; see the forwarded-message rules below for the exceptions.
- `thread_order` — `"oldest-first"` (default) or `"latest-first"`. Only meaningful when `thread_mode` is `"structured"`.
- `report`

### Thread history

When `thread_mode="structured"`, the renderer appends per-message sections after the latest message. Each section uses a header ladder rooted in the parsed sender (`## From {sender}` plus optional date / subject). Sections whose attribution line could not be parsed render as `## Earlier message`; threads that the parser could not split at all render as a single `## Earlier in thread` section. Front matter gains a `thread_messages: N` key when N > 0, counting forward and reply sections.

Forwarded messages are content, not reply history:

- **Gmail HTML.** A forward block is a Gmail `div.gmail_quote` that has no direct-child `blockquote.gmail_quote` and whose first element child is `div.gmail_attr`, where the attribution's first line (text before the first `<br>`) is a forward separator or does not end in `:`. An attribution ending in `:` or a fullwidth `：` ("On … wrote:", "Le … a écrit :", "… 写道：") marks a reply. A legacy `div.gmail_quote` with no `gmail_attr` whose own leading text is a forward separator is also a forward. Forward blocks nested inside a forward are split out; any other quote block inside a forward stays in that forward's content. Collection stops at the first reply boundary outside a forward, and after 64 forward blocks. At that limit the 64th block also takes the siblings that follow it in the same parent element, so sibling forwards (and anything else there, including a later reply quote) stay in that block in document order. Forwards left uncollected elsewhere stay in the body, which is emitted before the forward blocks; when each forward sits in its own wrapper element, forwards past the limit therefore appear ahead of the first 64. No content is lost.
- **Plain text.** A forward starts at an unindented separator line: Gmail's `---------- Forwarded message ---------` (any run of 2+ dashes), Thunderbird/Yahoo `-------- Forwarded Message --------` and its localized labels, or Apple Mail `Begin forwarded message:` and its localized forms. The next non-blank line must look like a header (`From:`, `Von:`, `De :`, …), and the first separator must not sit inside reply history; otherwise the text keeps its reply handling. A separator is inside reply history when an Outlook separator line (`____` or `-----Original Message-----`) followed by a From + Sent/Date header block appears anywhere before it (Outlook history is unquoted, so everything after that block is history), or when the last line before it that is neither blank nor `>`-quoted is a reply attribution that mail-parser-reply recognizes (`On … wrote:`, including one wrapped across lines; only checked when that line ends in `:` or `：`). A `>` line, a `… wrote:`-like line, or a bare `From:`/`Sent:` pair in the forwarder's own note, followed by ordinary text, does not make the separator reply history. The separators live in one list in `dead_letter/core/forwarding.py`, drawn from Gmail samples, Thunderbird l10n sources, and the MIT-licensed crisp-oss/email-forward-parser fixtures.
- **`thread_mode="latest"`** keeps Gmail HTML forwards and unquoted plain-text forwards (Gmail, Thunderbird, Yahoo, Apple Mail separators) inline: separator line, forwarded header lines, then the forwarded body. Body text that sits after or between top-level Gmail forwards is emitted before the forwards; no content is lost. Reply quotes are still dropped, including a reply quote that contains a forward. A text/plain forward inside `>` quoting (Apple Mail's plain-text layout) is still treated as quoted history and dropped. Signature stripping (`strip_signatures`) can still remove text after a `-- ` line inside a forward. Output for mail without a forward is unchanged, and so is latest-mode output for Thunderbird and Apple Mail HTML forwards and for plain-text forwards that meet the rule above, which were already kept. An English separator that appears after reply history or without a following header line is now left to reply handling.
- **`thread_mode="structured"`** renders one section per forward, before any reply sections, in document order (outermost first for nested forwards). The separator line is dropped. When a `From:` header parses, the section heading is `## Forwarded from {sender}` with the same optional date / subject ladder, for HTML-derived text, Markdown emphasis pairs that wrap a value (`**Dave**`, `__Budget__`) unwrapped in the sender and subject and doubled angle brackets removed from the sender; plain-text values keep literal `**` and `__`; the first `From`, `Date` (or `Sent`) and `Subject` lines of the top header block (up to the first blank line; in Apple Mail's bold-label layout, up to the first line that is not a bold-label header) move into the heading, while `To`, `Cc`, other header lines and the body stay in the section. Otherwise the heading is `## Forwarded message`. `thread_order` reorders reply sections only. A `>`-quoted separator (Apple Mail plain text) counts as a forward only in structured mode, and only when no attribution line (a line ending in `:`) or quoted line directly precedes it. Zone content, and so the analysis snapshot, keeps the full forwarded header block.
- Outlook `divRplyFwdMsg` blocks and `message/rfc822` attachments are handled as before.

### `ConvertResult`

```text
source: Path
output: Path | None
subject: str
sender: str
date: str | None
attachments: list[str]
success: bool
error: str | None
dry_run: bool
error_code: str | None
plain_text_fallback_available: bool | None
html_repair_available: bool | None
```

### `BundleResult`

```text
source: Path
bundle: Path | None
markdown: Path | None
source_artifact: Path | None
attachments: list[Path]
success: bool
error: str | None
dry_run: bool
error_code: str | None
plain_text_fallback_available: bool | None
html_repair_available: bool | None
```

Notes:

- `ConvertResult.attachments` and `BundleResult.attachments` reflect retained attachment output after conversion-time stripping; stripped or unreferenced inline signature/tracking assets are omitted.
- On success, `bundle` points to the bundle directory and `markdown` points to `bundle/message.md`.
- On success with `source_handling in {"move", "copy"}`, `source_artifact` points to the retained `.eml` inside the bundle.
- On success with `source_handling="delete"`, `source_artifact=None`.
- On failure, `bundle=None`, `markdown=None`, `source_artifact=None`, and `attachments=[]`.
- In dry-run mode, planned bundle paths are still returned, but no files are written.

## Backend API (`dead_letter.backend`)

### Status Enum

`queued | running | succeeded | completed_with_errors | failed | cancelled`

### Error Envelope

Non-2xx API responses use:

```json
{
  "errors": [
    {"path":"string|null","code":"string","message":"string","stage":"validation|backend|core"}
  ]
}
```

### Local API Session and CSRF

#### `GET /api/session`

Returns the per-process CSRF token required by mutating local UI requests.

Response (`200`):

```json
{
  "csrf_token": "string"
}
```

Rules:

- The token is generated once per `create_app()` process and changes after the local server restarts.
- All `/api/*` requests, including `GET /api/session`, require a trusted loopback `Host` (`localhost`, `127.0.0.1`, or `::1`).
- Untrusted `Host` failures use the standard error envelope with `code="host_validation_failed"`.
- `GET`, `HEAD`, and `OPTIONS` `/api/*` requests are not gated by CSRF.
- Every non-safe `/api/*` request (`POST`, `PUT`, `DELETE`, etc.) must include `X-Dead-Letter-CSRF: <csrf_token>`.
- Browser requests with `Sec-Fetch-Site: cross-site` are rejected with `403`.
- Requests with an `Origin` header that does not match the request origin are rejected with `403`.
- CSRF failures use the standard error envelope with `code="csrf_validation_failed"`.
- No CORS support is provided; the same-origin local UI is the only browser client. Script clients must call `/api/session` and include the CSRF header before using mutating endpoints.

### Workflow Settings

#### `GET /api/settings`

Returns the saved Inbox/Cabinet workflow folders.

Response (`200`):

```json
{
  "configured": false,
  "inbox_path": null,
  "cabinet_path": null
}
```

Rules:

- First run returns `configured=false` with null folder paths.
- If the persisted settings file is malformed, the backend treats settings as unconfigured and returns the same `configured=false` shape.
- Once configured, `inbox_path` and `cabinet_path` are resolved absolute paths.
- `POST /api/jobs`, `POST /api/import`, `POST /api/import-batch`, and `POST /api/watch` return `409` until workflow folders have been saved.
- Settings are persisted to a platform-specific local config file:
  - macOS: `~/Library/Application Support/dead-letter/settings.json`
  - Windows: `~/AppData/Roaming/dead-letter/settings.json`
  - Linux/other Unix: `~/.config/dead-letter/settings.json`

#### `PUT /api/settings`

Persists workflow folders.

Request:

```json
{
  "inbox_path": "string",
  "cabinet_path": "string"
}
```

Response (`200`):

```json
{
  "configured": true,
  "inbox_path": "absolute Inbox path string",
  "cabinet_path": "absolute Cabinet path string"
}
```

Rules:

- Inbox and Cabinet must resolve to separate directories.
- Cabinet cannot equal Inbox or be nested inside it.
- Inbox cannot be nested inside Cabinet.
- Missing directories are created on save.
- Settings are persisted to the platform-specific dead-letter settings file.

### `POST /api/jobs`

Creates a conversion job.

Request:

```json
{
  "mode": "file|directory",
  "input_path": "string",
  "options": {
    "strip_signatures": "bool",
    "strip_disclaimers": "bool",
    "strip_quoted_headers": "bool",
    "strip_signature_images": "bool",
    "strip_tracking_pixels": "bool",
    "embed_inline_images": "bool",
    "include_all_headers": "bool",
    "include_raw_html": "bool",
    "no_calendar_summary": "bool",
    "allow_fallback_on_html_error": "bool",
    "allow_html_repair_on_panic": "bool",
    "delete_eml": "bool",
    "dry_run": "bool",
    "report": "bool",
    "thread_mode": "string  // 'latest' (default) or 'structured'",
    "thread_order": "string  // 'oldest-first' (default) or 'latest-first', only meaningful when thread_mode='structured'"
  }
}
```

Success response (`202`):

```json
{
  "id":"string",
  "status":"queued",
  "output_location": {
    "strategy": "cabinet",
    "cabinet_path": "absolute Cabinet path string",
    "bundle_path": "absolute bundle path string|null"
  }
}
```

Error responses:

- `400` for schema validation and semantic validation failures.
- `409` when workflow settings are not configured.
- `500` for unexpected backend failures.

Output placement is server-owned:

- All backend jobs write Cabinet bundles under the configured Cabinet root.
- Single-file create responses include an expected `bundle_path` derived from the source stem.
- Directory jobs report `bundle_path=null` because multiple bundles may be written.
- Terminal status responses update `bundle_path` to the actual resolved bundle directory for successful single-file jobs.
- Cabinet sources are rejected as job input.
- Successful file jobs move the source `.eml` into the Cabinet bundle by default.
- `delete_eml=true` changes successful file handling to delete the source instead of retaining a `.eml` artifact in Cabinet.
- Failed file jobs leave the source at its original path.

### `GET /api/jobs/{id}`

Returns job snapshot.

Response (`200`):

```json
{
  "id": "string",
  "status": "queued|running|succeeded|completed_with_errors|failed|cancelled",
  "origin": "manual|import|watch",
  "cancel_requested": true,
  "output_location": {
    "strategy": "cabinet",
    "cabinet_path": "absolute Cabinet path string",
    "bundle_path": "absolute bundle path string|null"
  },
  "progress": {"total": 0, "completed": 0, "failed": 0, "current": null},
  "summary": {"written": 0, "skipped": 0, "errors": 0},
  "errors": [{"path": "string|null", "code": "string", "message": "string", "stage": "validation|backend|core"}],
  "recovery_actions": [
    {
      "kind": "retry_with_html_repair|retry_with_html_fallback",
      "label": "string",
      "message": "string"
    }
  ],
  "diagnostics": {
    "state": "normal|degraded|review_recommended",
    "selected_body": "html|plain",
    "segmentation_path": "html|plain_fallback",
    "client_hint": "gmail|outlook|front|generic|null",
    "confidence": "high|medium|low",
    "fallback_used": "plain_text_reply_parser|html_failure_plain_text_fallback|html_markdown_panic_repaired|null",
    "warnings": [{"code": "string", "message": "string", "severity": "warning"}],
    "stripped_images": [{"category": "signature_image|tracking_pixel", "reason": "string", "reference": "string"}],
    "attachments": {"referenced": 0, "retained": 0}
  },
  "report_path": "string|null",
  "created_at": "ISO-8601 string",
  "started_at": "ISO-8601 string|null",
  "finished_at": "ISO-8601 string|null"
}
```

Directory-job variant:

```json
{
  "id": "string",
  "status": "queued|running|succeeded|completed_with_errors|failed|cancelled",
  "diagnostics": null
}
```

Diagnostics semantics:

- `diagnostics` is populated for `mode="file"` jobs only.
- Directory jobs return `"diagnostics": null`.
- `diagnostics.attachments` is an object when the message has attachments eligible for retention, and `null` otherwise. `referenced` counts attachments before any filtering pass — `filter_images` exclusions and the unreferenced-inline-asset pass alike; `retained` counts those written to the output. A `retained < referenced` gap signals dropped attachments and is machine-detectable; see [quality-diagnostics.md](quality-diagnostics.md) for the associated warning codes.
- When `report=true` and report generation succeeds, `report_path` points to a per-job JSON artifact under Cabinet named `.dead-letter-report-<job_id>.json`.
- Report generation still occurs for successful zero-file jobs; those reports contain `total=0` and an empty `results` array.
- `recovery_actions` is empty by default.
- Eligible strict HTML panic failures expose `retry_with_html_repair` first when the backend detects the targeted repair path.
- If a repaired retry later fails and plain text exists, the replacement failed job can expose `retry_with_html_fallback`.
- `state="normal"` means conversion completed without low-confidence or warning flags.
- `state="degraded"` means conversion succeeded with recoverable quality warnings.
- `state="review_recommended"` means conversion succeeded, but the operator should inspect the Markdown before relying on it.
- `diagnostics` is an operator-safe summary, not the raw internal conversion trace.
- `diagnostics.stripped_images[].reason` is a detector label such as `gmail_signature_wrapper`, `front_signature_wrapper`, `dimension_heuristic`, or `hidden_image`.

`404` means unknown job id or a previously terminal job that has been evicted from retention.

Cabinet bundle layout for successful conversions:

- `message.md`
- `<original filename>.eml` when the source was retained
- `attachments/` when retained extracted files were written

Failure behavior:

- Failed imported files remain in Inbox.
- Failed manual file jobs leave the source at its original path.

### `POST /api/jobs/{id}/cancel`

Requests cancellation.

Success response (`202` when accepted):

```json
{"id":"string","status":"queued|running|cancelled","accepted":true}
```

Other responses:

- `409` when job is already terminal (standard top-level `errors` envelope).
- `404` for unknown or evicted job id.

### `POST /api/jobs/{id}/retry`

Requests a new file job derived from the original job request plus an explicit recovery action.

Request body:

```json
{"action":"retry_with_html_repair"}
```

Success response (`202`):

```json
{
  "id": "string",
  "status": "queued",
  "output_location": {
    "strategy": "cabinet",
    "cabinet_path": "absolute Cabinet path string",
    "bundle_path": "absolute bundle path string|null"
  }
}
```

Notes:

- Reuses the original `mode="file"` input path and options.
- `retry_with_html_repair` forces `allow_html_repair_on_panic=true` for the new job only.
- `retry_with_html_fallback` forces `allow_fallback_on_html_error=true` for the new job only.
- Does not mutate the original job snapshot or saved UI options.
- Returns `409` when the retry action is not currently available for the referenced job.

### `GET /api/jobs/history`

Returns terminal jobs and aggregate counters.

Query: `limit` (int, default 50, max 200)

Response (`200`):

```json
{
  "jobs": [ "...JobStatusResponse objects, newest first..." ],
  "totals": {
    "jobs_completed": 0,
    "total_written": 0,
    "total_skipped": 0,
    "total_errors": 0
  }
}
```

Rules:

- Returns only terminal jobs (`succeeded`, `completed_with_errors`, `failed`, `cancelled`).
- `jobs` is sorted by `finished_at` descending, truncated to `limit`.
- `totals` aggregates across all retained terminal jobs regardless of `limit`.
- Cancelled jobs contribute partial counts to totals.
- Each item in `jobs` uses the same shape as `GET /api/jobs/{id}` responses.

### `GET /api/fs/list`

Lists a directory under the configured filesystem root.

Query:

- `path`: root-relative directory path, default `""` for root
- `filter`: optional file suffix filter such as `.eml`

Response (`200`):

```json
{
  "path": "string",
  "entries": [
    {
      "name": "string",
      "path": "string",
      "input_path": "absolute path string",
      "type": "file|directory",
      "size": 0,
      "modified": "ISO-8601 string"
    }
  ]
}
```

Rules:

- `path` and entry `path` values are root-relative.
- `path` preserves the logical browser path the user clicked, including in-root symlink paths.
- `input_path` is an absolute resolved local path safe to submit to `POST /api/jobs`.
- Hidden entries whose names start with `.` are excluded.
- Directories are always included even when `filter` is present.
- Entries are sorted with directories first, then files, alphabetically within each group.
- The Brume frontend does not depend on this endpoint; it remains part of the backend contract for API/tooling clients.

Error responses:

- `403` for traversal/escape attempts
- `404` for missing paths
- `400` for non-directory targets

All non-2xx responses use the standard top-level error envelope.

### `POST /api/import`

Copies one uploaded `.eml` into the configured Inbox and immediately starts a file job.

Request:

- multipart form upload
- field name: `file`
- optional field name: `options` containing a JSON-serialized `JobOptions` object

Success response (`202`):

```json
{
  "imported_path":"absolute Inbox path string",
  "id":"string",
  "status":"queued",
  "output_location": {
    "strategy": "cabinet",
    "cabinet_path": "absolute Cabinet path string",
    "bundle_path": "absolute bundle path string"
  }
}
```

Rules:

- Workflow folders must already be configured, otherwise the endpoint returns `409`.
- Only `.eml` filenames are accepted.
- Invalid `options` payloads return `400` with the standard error envelope and `path="options"`.
- Uploaded files larger than 100 MB are rejected with `413`.
- The uploaded file is copied into Inbox using a collision-safe filename (`name.eml`, `name-2.eml`, ...), capped at 10,000 attempts. Exceeding the cap returns `500` with `backend_error`.
- Import immediately creates a file-mode job using the imported Inbox path and the provided options.
- If an active watcher already covers the imported path and supports suppression, that path is suppressed only after the import job has been accepted, to avoid a duplicate watch-created job.
- With the default backend source-handling mode, the imported Inbox copy is moved into the resulting Cabinet bundle after successful conversion.
- If the import job fails, the copied Inbox file remains in Inbox.
- If import setup fails before the job is created, the copied Inbox file is removed and no watch suppression is recorded.

All non-2xx responses use the standard top-level error envelope.

### `POST /api/import-batch`

Copies multiple uploaded `.eml` files into a reserved Inbox batch directory and immediately starts one directory job.

Request:

- multipart form upload
- repeated field name: `files`
- optional field name: `options` containing a JSON-serialized `JobOptions` object

Success response (`202`):

```json
{
  "imported_paths": [
    "absolute Inbox batch file path string"
  ],
  "id":"string",
  "status":"queued",
  "output_location": {
    "strategy": "cabinet",
    "cabinet_path": "absolute Cabinet path string",
    "bundle_path": null
  }
}
```

Rules:

- Workflow folders must already be configured, otherwise the endpoint returns `409`.
- At least one uploaded file is required.
- Batch uploads accept at most 100 files. Larger batches are rejected with `413` before any batch directory is staged.
- Every uploaded filename must end with `.eml`; any non-`.eml` filename is rejected with `422`.
- Uploaded files larger than 100 MB are rejected with `413`.
- The aggregate staged batch payload is capped at 100 MB. If the total exceeds the cap after staging a file, the reserved `_batch-*` directory is removed and the endpoint returns `413`.
- Uploaded files are copied into `Inbox/_batch-<uuid>/` using collision-safe filenames (`name.eml`, `name-2.eml`, ...), capped at 10,000 attempts. Exceeding the cap returns `500` with `backend_error`.
- Import immediately creates one directory-mode job using the reserved batch directory and the provided options.
- Active watch sessions ignore `_batch-*` directories, so batch imports do not create duplicate watch-origin jobs.
- If batch import setup fails before the job is created, the reserved `_batch-*` directory is removed.
- After the batch job reaches a terminal state, the reserved `_batch-*` directory is removed only when it is empty. If retained source `.eml` files remain, the directory is preserved.

All non-2xx responses use the standard top-level error envelope.

### Watch API

#### `POST /api/watch`

Starts watching one directory target.

Request:

```json
{
  "path": "string",
  "options": { "...JobOptions": true }
}
```

Response (`200`):

```json
{
  "active": true,
  "path": "absolute path string",
  "files_detected": 0,
  "jobs_created": 0,
  "failed_events": 0,
  "latest_job_id": "string|null",
  "latest_job_status": "queued|running|succeeded|completed_with_errors|failed|cancelled|null",
  "last_error": {
    "path": "string|null",
    "code": "watch_processing_error",
    "message": "string",
    "stage": "backend"
  }
}
```

Rules:

- Workflow folders must already be configured, otherwise the endpoint returns `409`.
- `path=""` watches the configured Inbox by default.
- Non-empty `path` may be either an absolute path or a browser-root-relative path.
- Only one watch session is active at a time.
- Watch targets inside Cabinet are rejected.
- Stable `.eml` files already present when watch starts are auto-submitted once before live event watching begins.
- New `.eml` files are auto-submitted as file-mode jobs using the provided options.
- Paths inside reserved `_batch-*` directories are ignored.
- Symlinked `.eml` files whose resolved targets escape the active watch directory are ignored.
- Watch processing waits for file stability and suppresses near-duplicate events for a short dedupe window.
- Watch processing failures are counted in `failed_events` and the latest failure is exposed as `last_error`.
- `files_detected`, `jobs_created`, and `latest_job_id` / `latest_job_status` include both the startup backlog sweep and later live watch events.
- `latest_job_id` / `latest_job_status` identify the most recently created watch job so clients can inspect its full snapshot through `GET /api/jobs/{id}`.

Error responses:

- `403` for traversal/escape attempts
- `404` for missing paths
- `400` for non-directory targets
- `409` when workflow settings are missing or a watch session is already active

All non-2xx responses use the standard top-level error envelope.

#### `GET /api/watch`

Returns current aggregate watch status:

```json
{
  "active": false,
  "path": "absolute path string|null",
  "files_detected": 0,
  "jobs_created": 0,
  "failed_events": 0,
  "latest_job_id": "string|null",
  "latest_job_status": "queued|running|succeeded|completed_with_errors|failed|cancelled|null",
  "last_error": null
}
```

#### `DELETE /api/watch`

Stops the active watch session and returns the same aggregate watch-status shape.

### `POST /api/open-folder`

Opens the configured Cabinet folder in the host operating system's file manager.

Request: empty body.

Response (`200`):

```json
{
  "path": "absolute Cabinet path string"
}
```

Rules:

- Workflow folders must already be configured, otherwise the endpoint returns `409`.
- The Cabinet directory is created if it does not already exist.
- This endpoint has a local OS side effect: macOS launches `open`, Windows launches `explorer`, and Linux/other Unix launches `xdg-open`.
- If Cabinet creation or file-manager launch fails, the endpoint returns `500` with `backend_error`.

All non-2xx responses use the standard top-level error envelope.

## Error Taxonomy

Common error codes:

- `validation_error`: request schema validation failure.
- `invalid_request`: semantic input validation failure.
- `host_validation_failed`: `/api/*` request used an untrusted non-loopback `Host`.
- `csrf_validation_failed`: mutating `/api/*` request failed CSRF, cross-site, or cross-origin validation.
- `backend_error`: unhandled API layer failure.
- `backend_exception`: worker-side exception around core conversion call.
- `conversion_error`: mapped core conversion failure (`ConvertResult.success=False`).
- `job_failure`: orchestration-level failure in runner. When an `ExceptionGroup` is raised from `TaskGroup`, each sub-exception produces its own `ErrorItem`.
- `watch_processing_error`: per-file or watcher-loop failure while watch mode is active.

## Cancellation Semantics

- Cancellation is cooperative and flag-based (`cancel_requested`).
- New queue items are not started after workers observe `cancel_requested=True`.
- In multi-worker mode, one or more files already in progress may still complete before terminal `cancelled`.

## Retention Semantics

- Job registry is in-memory.
- Terminal jobs are retained with a bounded cap (`max_retained_terminal_jobs`, default `2000`).
- Oldest terminal jobs are pruned first.
- Active jobs are not pruned.

## CLI (`dead-letter`)

### `dead-letter convert <path> [flags]`

Converts one `.eml` file or a directory of `.eml` files.

Options:

- `--output PATH` — output file or directory
- `--thread-mode {latest,structured}` — thread history rendering; defaults to `latest`
- `--thread-order {oldest-first,latest-first}` — section order in `structured` mode; defaults to `oldest-first` (no effect when `--thread-mode latest`)

Flags (all `store_true`, default `false`):

- `--strip-signatures`
- `--strip-disclaimers`
- `--strip-quoted-headers`
- `--strip-signature-images`
- `--strip-tracking-pixels`
- `--embed-inline-images`
- `--include-all-headers`
- `--include-raw-html`
- `--no-calendar-summary`
- `--allow-fallback-on-html-error`
- `--allow-html-repair-on-panic`
- `--delete-eml`
- `--dry-run`
- `--report` — write `.dead-letter-report.json`
  - when `--output PATH` is provided, the report is written under that output directory
  - without `--output`, file conversions write the report next to the source `.eml`
  - without `--output`, directory conversions write the report to the input directory root
  - report `results[].source` uses the source basename for file mode and source-relative POSIX paths for directory mode

Backward compatibility: bare `dead-letter <path>` (without `convert` subcommand) is treated as `dead-letter convert <path>` when the first argument is not a registered subcommand and does not start with `-`.

### `dead-letter doctor [--json]`

Validates runtime environment. Checks:

1. Python version (>= 3.12 required)
2. Core dependencies importable (mail-parser, nh3, html-to-markdown, selectolax, icalendar, pyyaml, mail-parser-reply)
3. CLI extras (watchfiles)
4. UI extras (FastAPI, uvicorn, httpx)
5. Inbox path readable and traversable (from saved settings)
6. Cabinet path writable and traversable, confirmed by a temp-file write probe (from saved settings)

Exit codes:

- `0` — all checks pass or skip
- `1` — one or more checks failed

`--json` emits structured output:

```json
{
  "version": "0.2.4",
  "python": "3.14.0",
  "platform": "darwin",
  "checks": [
    {"name": "python_version", "status": "ok", "message": "..."},
    {"name": "cabinet_path", "status": "err", "message": "...", "fix": "..."}
  ]
}
```

## MCP Server (`dead_letter.backend.mcp_server`)

Published as the `dead-letter-mcp` console script (`dead-letter[mcp]`, MCP Python
SDK 2.x) and described by `server.json` for the MCP Registry.

From 0.4.5, `serverInfo` also carries a `title`, `description`,
`websiteUrl`, the package `version`, and a 64×64 PNG icon embedded as a
`data:` URI (no network fetch), and each tool has a human-readable `title`.
Clients decide whether and where to display these fields.

Packages from 0.4.5 expose the five tools below. `convert_mbox` (#145) was
added in 0.4.5; 0.4.0 and earlier expose the other four.

| Tool | Required arguments | Returns |
| --- | --- | --- |
| `convert_eml` | `eml_path` | Markdown text (front matter + body). Writes a file too when `output_path` is given. |
| `convert_eml_to_bundle` | `eml_path`, `bundle_root` | JSON: `bundle_path`, `markdown_path`, `attachment_paths`, and `diagnostics` when available. |
| `convert_directory` | `directory`, `output_directory` | JSON: `total`, `successes`, `failures`, `output_paths`, `errors`. |
| `convert_mbox` | `path`, `output_directory` | JSON: `output_directory`, `processed`, `converted`, `skipped`, `failed`, `truncated`, `report_path`, `failures` (at most 20 of `index`, `code`, `message`), `failures_omitted`, and `message` when truncated. Never returns message content. |
| `get_diagnostics` | `eml_path` | Diagnostics JSON — `state`, `selected_body`, `segmentation_path`, `client_hint`, `confidence`, `fallback_used`, `warnings`, plus conditional `stripped_images` and `attachments` (see [quality-diagnostics.md](quality-diagnostics.md)). Writes nothing permanent. |

All five accept `preset` (`default`, `clean`, `verbose`, `raw`) plus per-flag
overrides: `strip_signatures`, `strip_disclaimers`, `strip_tracking_pixels`,
`strip_signature_images`, `strip_quoted_headers`, `embed_inline_images`,
`include_all_headers`, `include_raw_html`, `no_calendar_summary`, `thread_mode`,
and `thread_order`. An explicit flag overrides the preset; `None` leaves the
preset value in place.

Presets:

| Preset | Flags set |
| --- | --- |
| `default` | `strip_signatures`, `strip_tracking_pixels`, `strip_signature_images` |
| `clean` | `default` plus `strip_disclaimers`, `strip_quoted_headers` |
| `verbose` | `include_all_headers`, `include_raw_html` |
| `raw` | none |

### MCP-only constraints

These differ from the CLI and the Python API:

- **Resilience is forced on.** `_resolve_options` sets
  `allow_fallback_on_html_error` and `allow_html_repair_on_panic` to `True` for
  every tool call, whatever the preset. MCP output can therefore differ from an
  equivalent CLI run on a malformed HTML body.
- **`convert_eml_to_bundle` is copy-only.** `source_handling` accepts `"copy"`;
  `"move"` and `"delete"` are rejected with a `ToolError`. The original `.eml`
  is never modified over MCP.
- **`convert_directory` requires `output_directory`.** Unlike `convert_dir`, it
  has no in-place default. The inputSchema lists it as required, and an empty
  string is rejected.
- **Directory batches cap at 50 files.** `MCP_MAX_DIRECTORY_FILES = 50`; a larger
  directory is rejected before any conversion runs.
- **`convert_mbox` is bounded (0.4.5 and later, #145).** It reuses the CLI's MBOX
  importer with the default `preserve` unescape mode and default per-message
  limits. It exposes only `bundles` plus the conversion options above
  (including `dry_run`); unescape modes, worker timeouts and the per-message
  size override are CLI/Python-only.
  - `path` must be one existing, readable regular file whose name ends in
    `.mbox` (case-insensitive). Compressed archives, other suffixes and Apple
    Mail `.mbox` directories are rejected before any output is written.
  - The archive must be at most `MCP_MAX_MBOX_BYTES` (256 MiB); a larger one is
    rejected before conversion. The cap is also enforced on the bytes actually
    read: if a record ends past 256 MiB (the file grew or was swapped after
    the check), conversion stops at that record. After iteration the source is
    re-stated; a changed device, inode, size or modification time fails the
    call. Both cases publish the partial report as `"failed"`.
  - Conversion stops after `MCP_MAX_MBOX_MESSAGES` (1000) records. The
    iterator is closed, nothing further is converted, and `truncated` is `true`
    when the last processed record ended before end of file. The rest of the
    archive is not converted; use the CLI. There is no resume.
  - `output_directory` is required and must not be an existing file. Output
    names are collision-safe, as in the CLI. Unless `dry_run` is set, a
    streaming report with the CLI's MBOX report schema (`job.id` `"mcp"`,
    `mbox_options.max_messages`, `mbox_options.truncated`) is written to
    `.dead-letter-report.json` in `output_directory`, or `-2`, `-3`, ... when
    that name exists; earlier reports are never overwritten. `dry_run` writes
    nothing and returns `report_path: null`.
  - The source archive is opened read-only and never modified, moved or
    deleted.
  - MCP request cancellation is **not supported**: a cancelled call keeps
    running until it finishes or reaches a bound. Byte/message caps are not
    a wall-clock deadline; parsing and filesystem I/O have no MCP time limit.

### Error contract

Tool failures do **not** reach the client as exceptions. The server catches
them and returns a `CallToolResult` with `is_error=True`; the exception class
name is never transmitted. Anticipated failures raise `ToolError`, and the
client receives the text `Error executing tool <name>: <message>`. Clients must
check that the text contains one of these messages:

- `File not found: <path>` — missing `.eml`; `<path>` is the caller's argument
- `Expected a .eml file: <path>` — the source exists but is not a `.eml` file
- `Directory not found: <path>` — missing directory
- `Cannot create bundle_root <path>: <reason>` — `convert_eml_to_bundle` could
  not create `bundle_root`
- `Conversion failed: <error_code>` — pipeline failure in `convert_eml`,
  `convert_eml_to_bundle` or `get_diagnostics`, where `<error_code>` is
  `html_markdown_failed` or `conversion_error` (any other failure the core
  pipeline reports, including an output write error), optionally followed by
  `Plain text fallback is available.` and/or `HTML repair is available.` The
  raw parser or renderer error is logged on the server (stderr for stdio) and
  is not sent to the client, because it can quote email content.
  `convert_directory` does not raise for per-file failures: its JSON `errors[]`
  entries still carry each file's raw error text, which can include
  email-derived text such as subject-based output filenames.
- `MCP directory conversion supports at most 50 .eml files; found <n>.`
- `MCP convert_eml_to_bundle only supports source_handling='copy'; use the CLI/API for move/delete.`
- `output_directory is required for MCP directory conversion`

For tools other than `convert_mbox`, any other exception (one that escapes the
core pipeline, such as a failure reading back the converted Markdown, a broken
internal invariant or an unexpected crash) reaches the client only as
`Error executing tool <name>`, with no further text. MCP SDK 2.1 and later mask
these by design, and dead-letter relies on that so internal and email-derived
text stays on the server.

`convert_mbox` raises `ToolError`, so the MCP Python SDK 2.2 locked in
`uv.lock` delivers its message to the client, prefixed by `Error executing
tool convert_mbox: `:

- `output_directory is required for MCP MBOX conversion`
- `MCP convert_mbox accepts only a flat .mbox file; extract compressed archives first.`
- `File not found: <path>`
- `MBOX path is not a regular file: <path>`
- `File not readable: <path>`
- `MCP MBOX conversion supports archives up to 256 MiB; found <n> bytes. Use the dead-letter CLI for larger archives.`
- `MBOX conversion failed: <reason>` — core output validation (for example,
  `MBOX output must be a directory distinct from the source`) or a failure
  before any message was processed
- `MBOX conversion failed after <n> messages: <reason>; partial report: <path>`
  — an error after conversion started (for example, a full disk while
  spooling the report, `MBOX archive exceeds the MCP limit of 256 MiB; use the
  dead-letter CLI`, or `MBOX changed during MCP conversion; use an immutable
  export`). Outputs already written stay in place and the report
  is still published with `job.status` `"failed"`; it lists the entries
  committed before the error. If the report itself cannot be written, the
  message ends with `the report could not be written: <reason>` instead.
- `MBOX report could not be written after <n> messages: <reason>` — conversion
  completed but report publication failed. Existing message outputs remain;
  a failed report reservation is removed where filesystem cleanup succeeds.

**Unreleased hardening (#187):** MBOX conversion/report failure reasons use
`mbox_io_error` (optionally followed by a known errno name and the OS-generated
reason), `mbox_invalid_input`, or `mbox_conversion_error`. The fixed core output
validation message and MCP-owned archive-limit/change messages above remain
readable. Raw exception text, custom `strerror`, filenames and arbitrary
exception class names are never used as these reasons. Caller-provided input
paths in validation errors and the requested output/report paths remain part
of the response. Exception details are logged locally, not erased.

Per-message failures are not tool errors; they appear in `failures` and the
report. In the unreleased hardening, summary codes are allowlisted:
`mbox_empty_message`, `mbox_message_too_large`, `mbox_line_too_long`,
`mbox_invalid_message`, `mbox_archive_error`, `html_markdown_failed` and
`conversion_error`. Each has a fixed message; an unknown code becomes
`conversion_error`. Both string fields are therefore bounded independently
of the importer's error text. The report retains the original error details
and provenance. Local reports and logs can contain private metadata and must
not be treated as sanitized, model-safe summaries.

**Unreleased cleanup ordering (#145):** the importer is closed before final
source verification and report publication. A failure during early-stop
iterator cleanup is inside the same failure boundary as iteration: the
partial report is marked failed, never successful. This is not durable resume,
transactional output/report publication or a power-loss recovery guarantee.

### Distribution

The same MCP server ships three ways: as `dead-letter[mcp]` from PyPI (run
with `uvx` or `uv run`), inside the Claude Code plugin under `plugin/`, and as
a one-click MCP Bundle (`.mcpb`) for Claude Desktop and other MCPB-aware
clients, built from `mcpb/`. None of these change the tool contract above —
the bundle runs the same `dead-letter-mcp` entry point via `uv run` against a
pinned PyPI version.

## HTTP Status Mapping

- `200`: settings get/put, filesystem list, watch get/start/stop, job snapshot, open Cabinet folder
- `202`: job create accepted, import accepted, cancel accepted
- `400`: request rejected (schema or semantic validation)
- `403`: untrusted API `Host`, CSRF validation failure, hostile browser-origin signal, or filesystem/watch path escaping the configured browser root
- `404`: unknown or evicted job id, or missing browse/watch target
- `409`: workflow settings missing, or invalid lifecycle/watch conflict
- `413`: import payload exceeds the 100 MB per-file limit, the 100-file batch limit, or the 100 MB aggregate batch limit
- `422`: batch import contains non-`.eml` files (`POST /api/import-batch` rejects any upload whose filename does not end in `.eml`; clients are expected to filter or confirm-and-skip before upload)
- `500`: unexpected backend failure
