---
title: dead-letter v4 Runtime Contracts
doc_type: reference
status: canonical
last_updated: 2026-03-24
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

- `path` must exist and have `.eml` suffix.
- Writes markdown output unless `dry_run=True`.
- If `output` is omitted, writes next to source using a slugified subject filename.
- If output path collides, appends incrementing suffix (`-2`, `-3`, ...).
- If `delete_eml=True`, source deletion occurs only after successful write.
- If source deletion fails after writing markdown, the written markdown file is removed and conversion returns failure.
- `delete_eml` is disabled when `dry_run=True`.

### `convert_dir(directory, *, output=None, options=None) -> list[ConvertResult]`

Converts all `.eml` files under a directory (recursive).

Rules:

- Processes files in sorted order.
- Matches `.eml` suffixes case-insensitively.
- Skips symlinked files whose resolved paths escape the requested directory tree.
- Returns one `ConvertResult` per file.
- In directory mode with `output` set, source-relative subdirectories are mirrored under output root.

### `convert_to_bundle(path, *, bundle_root, options=None, source_handling="move") -> BundleResult`

Converts one `.eml` file into a self-contained bundle directory.

Rules:

- `path` must exist and have `.eml` suffix.
- Bundle directories are created under `<bundle_root>/<source-stem>` with collision-safe numeric suffixes when needed.
- `message.md` is always the bundle markdown filename.
- Extracted attachments, inline assets, and calendar files are written under `attachments/` when present.
- Attachment filenames are normalized to safe basenames before writing; directory segments from MIME-provided names are stripped.
- When attachments are written, markdown front matter includes relative `attachment_files` entries such as `attachments/logo.png`.
- `source_handling="move"` moves the original `.eml` into the bundle root.
- `source_handling="copy"` copies the original `.eml` into the bundle root and leaves the source in place.
- `source_handling="delete"` removes the source after successful bundle creation and leaves no `.eml` artifact in the bundle.
- `source_handling` is the only retained-source control for this API; `ConvertOptions.delete_eml` does not change bundle behavior.
- In `dry_run=True`, planned bundle paths are returned but no bundle directory, attachments, markdown, or source moves/copies/deletes are performed.
- If bundle creation fails after filesystem work has started, any partial bundle directory is removed.

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
- `report`

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
    "report": "bool"
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
    "client_hint": "gmail|outlook|generic|null",
    "confidence": "high|medium|low",
    "fallback_used": "plain_text_reply_parser|html_failure_plain_text_fallback|html_markdown_panic_repaired|null",
    "warnings": [{"code": "string", "message": "string", "severity": "warning"}],
    "stripped_images": [{"category": "signature_image|tracking_pixel", "reason": "string", "reference": "string"}]
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
- When `report=true` and report generation succeeds, `report_path` points to a per-job JSON artifact under Cabinet named `.dead-letter-report-<job_id>.json`.
- Report generation still occurs for successful zero-file jobs; those reports contain `total=0` and an empty `results` array.
- `recovery_actions` is empty by default.
- Eligible strict HTML panic failures expose `retry_with_html_repair` first when the backend detects the targeted repair path.
- If a repaired retry later fails and plain text exists, the replacement failed job can expose `retry_with_html_fallback`.
- `state="normal"` means conversion completed without low-confidence or warning flags.
- `state="degraded"` means conversion succeeded with recoverable quality warnings.
- `state="review_recommended"` means conversion succeeded, but the operator should inspect the Markdown before relying on it.
- `diagnostics` is an operator-safe summary, not the raw internal conversion trace.

`404` means unknown job id or a previously terminal job that has been evicted from retention.

Cabinet bundle layout for successful conversions:

- `message.md`
- `<original filename>.eml` when the source was retained
- `attachments/` when extracted files were written

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
- Every uploaded filename must end with `.eml`; any non-`.eml` filename is rejected with `422`.
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

## Error Taxonomy

Common error codes:

- `validation_error`: request schema validation failure.
- `invalid_request`: semantic input validation failure.
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

Flags (all `store_true`, default `false`):

- `--output PATH` — output file or directory
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
5. Inbox path readable (from saved settings)
6. Cabinet path writable (from saved settings)

Exit codes:

- `0` — all checks pass or skip
- `1` — one or more checks failed

`--json` emits structured output:

```json
{
  "version": "0.1.0",
  "python": "3.14.0",
  "platform": "darwin",
  "checks": [
    {"name": "python_version", "status": "ok", "message": "..."},
    {"name": "cabinet_path", "status": "err", "message": "...", "fix": "..."}
  ]
}
```

## HTTP Status Mapping

- `200`: settings get/put, filesystem list, watch get/start/stop, job snapshot
- `202`: job create accepted, import accepted, cancel accepted
- `400`: request rejected (schema or semantic validation)
- `403`: filesystem/watch path escapes configured browser root
- `404`: unknown or evicted job id, or missing browse/watch target
- `409`: workflow settings missing, or invalid lifecycle/watch conflict
- `500`: unexpected backend failure
