---
title: dead-letter v4 Frontend State Model
doc_type: reference
status: canonical
last_updated: 2026-03-24
audience:
  - maintainers
  - frontend contributors
scope:
  - src/dead_letter/frontend
  - src/dead_letter/backend
---

# dead-letter v4 Frontend State Model

This document describes runtime UI state management in `src/dead_letter/frontend/static/app.js` for the Brume Command Center interface.

## Workspace State (Computed)

`workspaceState` is computed and never stored directly:

- `settings` when `settingsOpen` is `true`
- `converting` when `isSubmitting` is `true`, or `jobId` exists and `jobStatus` is non-terminal
- `done` when `jobId` exists and `jobStatus` is terminal
- `idle` otherwise

Priority order is fixed:

`settings > converting > done > idle`

## Primary State Shape

- settings state:
  - `settingsConfigured`
  - `settingsLoading`
  - `settingsSaving`
  - `settingsForm`: `{inbox_path, cabinet_path}`
  - `settingsErrors`
  - `settingsOpen`
- input state:
  - `mode`: `file | directory`
  - `inputPath`
  - `options`: conversion option booleans, organized in the UI into four domain sections (Content, Images, Output, Behavior) with two meta toggle cards ("Strip junk", "Verbose output") that bulk-control related options:
    - Content (Strip junk): `strip_signatures`, `strip_disclaimers`, `strip_quoted_headers`
    - Images (Strip junk): `strip_signature_images`, `strip_tracking_pixels`
    - Images (Verbose output): `embed_inline_images`
    - Output (Verbose output): `include_all_headers`, `include_raw_html`
    - Output: `no_calendar_summary`
    - Behavior: `allow_fallback_on_html_error`, `delete_eml`, `dry_run`, `report`
- workspace interaction state:
  - `dragActive`
  - `dragDepth`
  - `dragItemCount`
  - `batchConfirm`: `{show, emlFiles, skipped, totalBytes}`
  - `errorsExpanded`
  - `liveAnnouncement`
- watch state:
  - `watchPathOverride`
  - `watchActive`
  - `watchPath`
  - `watchStats`
  - `watchLastError`
  - `watchLatestJobId`
  - `watchLatestJobStatus`
  - `watchPollHandle`
  - `watchSessionId`
  - `watchPollInFlight`
  - `watchActionInFlight`
  - `activeWatchPollController`
  - `activeWatchActionController`
- job state:
  - `jobId`
  - `jobOrigin`: `manual | import | watch | ""`
  - `jobStatus`
  - `outputLocation`: `{strategy:"cabinet", cabinet_path, bundle_path|null} | null`
  - `recoveryActions`: `[{kind, label, message}]`
  - `jobDiagnostics`: `{state, selected_body, segmentation_path, client_hint, confidence, fallback_used, warnings, stripped_images} | null`
  - `diagnosticsOpen`
  - `reportPath`: `string | null`
  - `cancelRequested`
  - `progress`: `{total, completed, failed, current}`
  - `summary`: `{written, skipped, errors}`
  - `errors`
  - `timestamps`: `{created_at, started_at, finished_at}`
- operation messaging:
  - `isSubmitting`
  - `formErrors`
  - `opErrors`
  - `opInfo`
- polling control:
  - `pollHandle`
  - `pollSessionId`
  - `pollInFlight`
  - `pollErrorCount`
  - `activePollController`
- history state:
  - `historyJobs`: array of terminal job snapshots (newest first)
  - `historyTotals`: `{jobs_completed, total_written, total_skipped, total_errors}`
  - `historyLoading`
  - `historyOpen`
- watch handoff state:
  - `pendingWatchJobId`
  - `pendingWatchJobStatus`
- cancellation control:
  - `cancelInFlight`
  - `activeCancelController`

## Computed Getters

- `conversionGrade`: computed via `computeGrade(diagnostics, jobStatus)` returning `"pass"` | `"review"` | `"fail"` | `null`
  - `"fail"` when `jobStatus === "failed"`
  - `null` when diagnostics unavailable (directory jobs, cancelled jobs)
  - `"pass"` when `state === "normal"`
  - `"review"` when `state === "degraded"` or `state === "review_recommended"`
- `gradeLabel`: display text (`"Pass"`, `"Review"`, `"Fail"`, `""`)
- `gradeClass`: CSS class (`"grade-pass"`, `"grade-review"`, `"grade-fail"`, `""`)
- `strippedImagesSummary`: e.g. `"2 signature images, 1 tracking pixel stripped"` or `""`
- `stripJunkState`: `"all"` | `"some"` | `"none"` — derived from the 5 strip options (`strip_signatures`, `strip_disclaimers`, `strip_quoted_headers`, `strip_signature_images`, `strip_tracking_pixels`)
- `verboseOutputState`: `"all"` | `"some"` | `"none"` — derived from the 3 verbose options (`include_all_headers`, `include_raw_html`, `embed_inline_images`)
- Auto-expand: diagnostics disclosure opens automatically when `conversionGrade === "review"`

## Meta Toggle Methods

- `toggleStripJunk()`: if `stripJunkState === "all"`, unchecks all 5 strip options; otherwise checks all 5
- `toggleVerboseOutput()`: if `verboseOutputState === "all"`, unchecks all 3 verbose options; otherwise checks all 3

Removed browser state:

- `browserPath`, `browserEntries`, `browserLoading`, `browseSessionId`, `browserError`
- `pickerOpen`, `activeBrowseController`, `breadcrumbs`
- `selectedBrowserName`, `selectedBrowserPath`, `selectedBrowserType`, `selectedBrowserLabel`

## Request Lifecycle

### First-Run Setup

- `init()` calls `loadSettings()`, `pollWatch()`, and `history.load()`.
- `GET /api/settings` with `configured=false` keeps workflow actions disabled and resets suggested defaults.
- Suggested defaults are `~/Documents/dead-letter/Inbox` and `~/Documents/dead-letter/Cabinet` until saved settings replace them.
- `PUT /api/settings` persists folders, creates missing directories, and enables manual/watch workflows.
- Saving folders while default Inbox watch is active shows a restart-watch notice only when the saved Inbox path differs from the currently watched path.

### Start Manual Job

1. Guard duplicate submissions (`isSubmitting`).
2. Require configured settings.
3. Validate manual form (`inputPath` required, mode must be valid).
4. Reset run state and close settings takeover (`settingsOpen=false`).
5. `POST /api/jobs`.
6. Validate required create response fields.
7. Start poll loop.

### Import Mail (File Input or Drop)

- Accepts one or more selected or dropped files.
- Uses `POST /api/import` for single `.eml` uploads and `POST /api/import-batch` for multi-file `.eml` uploads.
- Requires configured settings before upload starts.
- Sends the current `options` object as a JSON `options` multipart field.
- Partitions `.eml` files from non-`.eml` files before submitting anything.
- If zero `.eml` files remain after filtering, the frontend surfaces an actionable error and rejects the drop.
- If skipped non-`.eml` files are present, or the total `.eml` payload exceeds 100 MB, the frontend shows a confirmation overlay before submitting.
- Confirmed single-file imports:
  - sets `inputPath` from `imported_path`
  - sets `mode="file"`
  - applies started job payload with `jobOrigin="import"` and begins polling
- Confirmed batch imports:
  - leave manual `inputPath` untouched
  - apply started job payload with `jobOrigin="import"` and begins polling
- Workspace drop handling is ignored while in `converting` or `settings` states.
- Escape dismisses the batch confirmation overlay before it closes Settings.

### Watch Inbox

- The status-strip Watch card is a button wired to `toggleWatch()` and reflects `watchActive` / `watchActionInFlight`.
- `watchPathOverride=""` means "watch saved Inbox path".
- Non-empty override is sent as provided to `POST /api/watch`; backend resolves absolute or root-relative path.
- Cabinet paths are rejected by backend.
- Settings remain the place for editing watch path override and conversion options; the status-strip card only starts or stops watch.
- `GET /api/watch` updates active state, resolved path, counters, last error, and latest watch-created job metadata.
- When a latest watch job appears and no foreground job is running, the frontend adopts it into the main workspace polling flow.
- When a foreground job is still running, the latest watch job is queued and adopted after the foreground job reaches a terminal state.
- Session guards prevent stale start/stop/poll responses from overwriting current watch state.
- Failed stop requests preserve the current watch state, surface an operation error, and resume polling until the backend confirms the watcher has stopped.
- `destroy()` aborts active poll/watch/cancel controllers and clears timers to avoid stale async writes after teardown.

### Poll Job

- Poll interval: 800ms.
- `GET /api/jobs/{id}`.
- Required keys: `status`, `cancel_requested`, `output_location`, `progress`, `summary`, `errors`.
- Optional keys: `diagnostics`, `recovery_actions`.
- Session/job-id guards reject stale responses.
- Overlapping poll calls are prevented with `pollInFlight`.
- Polling stops on terminal status; reaching terminal triggers `history.load()` to refresh aggregate counters and the job list.
- File-job failures can populate `recoveryActions`; the current UI consumes `retry_with_html_repair` first and may later consume `retry_with_html_fallback` on a subsequent eligible failure.

Terminal statuses:

- `succeeded`
- `completed_with_errors`
- `failed`
- `cancelled`

### Poll Failure and Retry

- Network and 5xx failures use bounded retry (3 attempts).
- Non-retryable errors (missing keys, 404, 409) stop polling immediately.
- Manual `resumePolling()` re-enables polling for non-terminal jobs.

### Cancel Job

1. Guard overlapping requests (`cancelInFlight`).
2. `POST /api/jobs/{id}/cancel`.
3. Ignore stale responses for changed job ids.
4. If accepted and non-terminal, set `cancelRequested=true` with cooperative cancellation notice.
5. Continue polling until terminal.

### Retry Failed Job

1. Guard duplicate requests with `isSubmitting`.
2. Only allow retry when the current terminal snapshot exposes a matching `recoveryActions` entry.
3. `POST /api/jobs/{id}/retry` with `{action}`.
4. Preserve the current `jobOrigin` when the replacement job starts so watched failures stay labeled as watch-origin.
5. Replacement job polling uses the normal create-job flow and clears the prior snapshot state.

## UI Messaging Rules by Workspace State

- Persistent readout below workspace:
  - always show current `inboxPathLabel` and `cabinetPathLabel`
  - read-only display; editing remains inside settings takeover
- `idle`:
  - show drop prompt ("Drop .eml files to convert")
  - allow click-to-browse file input
  - show a batch confirmation overlay when mixed files or large uploads need confirmation
- `converting`:
  - show progress bar, current file label, and running result counts
  - keep cancel action visible
- `done`:
  - show aggregate result counts from history (written/skipped/errors across all retained jobs), falling back to current job counts when history is empty
  - show status-colored Last Job filename and stable Cabinet output path
  - allow inline expansion for backend file errors and operation messages
  - show grade badge (Pass/Review/Fail) inline in header for file jobs with diagnostics
  - show grade message for non-Pass grades
  - show stripped images summary (clickable to expand diagnostics) when images were stripped
  - show report path row when `reportPath` is set, using the actual per-job backend report path returned by the API
  - show diagnostics panel toggle when diagnostics are available (auto-expanded for Review grade)
  - show history disclosure bar (collapsed by default) with scrollable job list and per-row drill-down when history has entries
  - provide drop hint for immediate next run
  - reuse the same batch confirmation overlay before starting the next job from a mixed or large drop
- `settings`:
  - settings takeover replaces other workspace content
  - two-column layout for paths/watch and options/manual job controls
  - Escape closes takeover
- Status strip:
  - Watch card is interactive and remains visible outside Settings
  - active Watch state uses an accent edge plus animated perimeter trace

`liveAnnouncement` is updated on workspace transitions and in-progress polling updates to support aria-live feedback.

## Safety Rules

- `dry_run=true` forces `delete_eml=false`.
- Missing required poll fields stops polling and surfaces operation error.
- Drop imports reject when no `.eml` files are present.
- Batch confirmation is required before submitting mixed drops or `.eml` payloads larger than 100 MB.

## Contract Dependencies

Frontend expects:

- `GET /api/settings` response with `configured`, `inbox_path`, and `cabinet_path`.
- `PUT /api/settings` response with configured settings shape.
- `POST /api/jobs` success response with `id`, `status`, and `output_location`.
- `GET /api/jobs/{id}` response with `status`, `origin`, `cancel_requested`, `output_location`, `progress`, `summary`, `errors`, optional `diagnostics`, and optional `report_path`.
  - when present, `report_path` is a job-specific backend artifact path rather than a shared latest-report alias
- `GET /api/jobs/history` response with `jobs` array and `totals` aggregate.
- Error envelopes with top-level `errors`.
- `POST /api/import` success response with `imported_path`, `id`, `status`, and `output_location`.
- `POST /api/import-batch` success response with `imported_paths`, `id`, `status`, and `output_location`.
- `GET /api/watch` aggregate response with `active`, absolute `path`, `files_detected`, `jobs_created`, `failed_events`, `last_error`, `latest_job_id`, and `latest_job_status`. Those counters and latest-job fields may reflect startup backlog processing as well as live watch events.

`GET /api/fs/list` is no longer a frontend dependency in Brume.
