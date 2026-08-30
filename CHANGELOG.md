# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Claude plugin releases now publish an explicit version, release tag, and
  commit SHA to the Big Cactus Labs marketplace before advancing the legacy
  `release` branch. This lets Cowork detect the marketplace commit and keeps
  Cowork and Claude Code on the same immutable plugin assets.
- Forwarded-as-attachment messages (`message/rfc822` or `multipart/digest`
  parts) no longer leak the embedded message's body into, or replace, the
  outer message body. The embedded message is now recorded as an attachment
  instead (#92).
- Subjects that slugify to empty, including non-Latin-script subjects with no
  ASCII decomposition, now fall back to the slugified source filename stem
  instead of the generic `email` filename (#95).
- `JobManager` now retains references to background job tasks so a running
  job can no longer be garbage-collected mid-run; exceptions escaping the job
  runner are now logged (#96).
- `write_report` no longer mutates the process-wide umask; the effective
  umask is read once at import instead (#97).

## [0.2.5] - 2026-08-20

### Fixed

- Prevented catastrophic Gmail-attribution backtracking on malformed reply text (#79).
- Prevented quadratic generic quote detection on large prose-only HTML bodies (#81).
- Subject-derived output slugs now cap at a safe filename length, and failed
  conversion cleanup absorbs filesystem errors (#80).
- Failed conversions now clean up only outputs they created, preserving
  pre-existing collision targets (#82).
- Clean `dead-letter[mcp]` installs now start with the current dependency
  resolution by migrating to MCP Python SDK 2.x (`mcp>=2,<3`) and its public
  `MCPServer` API. MCP tool failures now arrive as error results carrying only
  the message text — the exception class name is no longer transmitted — so
  clients must match on the text (for example `File not found: <path>`).

## [0.2.4] - 2026-07-06

### Added

- Official MCP Registry publishing. A `server.json` describes the
  `dead-letter-mcp` server, and a `publish-mcp` job in the release workflow
  publishes it to `registry.modelcontextprotocol.io` after each PyPI release
  using GitHub OIDC (no stored secret). The listing propagates automatically
  to the GitHub MCP Registry, PulseMCP, and other aggregators. Ownership is
  verified by an `mcp-name` marker in the package README; the first successful
  publish lands on the next release (`0.2.3` on PyPI predates the marker). See
  the [publishing runbook](docs/reference/publishing.md).
- `AGENTS.md` — operational guide for AI coding agents contributing to the
  repo: verification commands, hard invariants (untrusted email content,
  version sync points, release-pointer ordering), and conventions.

### Changed

- Plugin distribution: the
  [`BigCactusLabs/bigcactuslabs-plugins`](https://github.com/BigCactusLabs/bigcactuslabs-plugins)
  marketplace now tracks a fast-forward-only `release` branch in this repo
  instead of a per-version tag pin, so shipping a plugin release no longer
  requires a hand-edited marketplace `ref` bump. Runtime versioning is
  unchanged — the plugin's `.mcp.json` still pins an exact PyPI version.
  `plugin-vX.Y.Z` tags continue to mark each plugin release. See the updated
  [release runbook](docs/reference/publishing.md). No action needed for
  installed plugins.

### Fixed

- Backend jobs now attach `report_path` before exposing a terminal job status
  when reports are enabled, so polling cannot observe `succeeded` or `failed`
  with a still-pending report write.
- Front-originated HTML replies now report `client_hint="front"`, prefer the
  outer `blockquote.front-blockquote` boundary over nested quote markers, and
  preserve arbitrary siblings after that Front quote as authored body content.
- Local UI API requests now reject untrusted `Host` headers before issuing CSRF
  tokens, closing a DNS-rebinding-style bypass against the local-only browser
  workflow.

## [0.2.3] - 2026-06-09

### Fixed

- Bundle/attachment retention no longer drops real attachments that carry a
  `Content-ID`. Outlook/Exchange stamps a `Content-ID` on `disposition=attachment`
  parts, and the unreferenced-inline-asset pass was treating any cid-bearing part
  as an inline image — silently discarding the attachment (`attachment_paths: []`)
  for a common `multipart/mixed` shape. The pass now skips `disposition=attachment`
  parts regardless of `Content-ID`, so `convert_eml_to_bundle` retains them.
- Claude plugin command and skill guidance now treats converted email content
  as untrusted data, not instructions. Summarize, triage, convert, and cabinet
  flows explicitly reject tool-use, credential, prompt-disclosure, and
  exfiltration instructions embedded in email bodies or attachments.

### Added

- `diagnostics.attachments` `{referenced, retained}` counts, present when a message
  has attachments eligible for retention. A `retained < referenced` gap makes dropped
  attachments machine-detectable. See
  [`docs/reference/quality-diagnostics.md`](docs/reference/quality-diagnostics.md).
- Claude plugin content tests now assert the untrusted-email-content contract
  across the auto-trigger skill and all slash commands.

### Changed

- Claude plugin metadata is bumped to `plugin-v0.2.3`, and its MCP launcher now
  pins `dead-letter[mcp]==0.2.3`.

## [0.2.2] - 2026-06-01

### Changed

- Front matter `source` is now the input filename (basename) instead of the
  absolute filesystem path. The source `.eml` sits alongside the rendered `.md`
  in the common workflows (sibling convert and bundle/cabinet output), so the
  basename carries the needed provenance while dropping ~25–30 machine-specific
  tokens per email. Token-cost benchmark numbers refreshed accordingly.
- Claude plugin metadata is bumped to `plugin-v0.2.2`, and its MCP launcher now
  pins `dead-letter[mcp]==0.2.2`.

## [0.2.1] - 2026-05-28

### Added

- Claude plugin distribution under [`plugin/`](plugin/), released as
  `plugin-v0.2.1` and surfaced through the new
  [`BigCactusLabs/bigcactuslabs-plugins`](https://github.com/BigCactusLabs/bigcactuslabs-plugins)
  marketplace. Install in Claude Code or Cowork with
  `/plugin marketplace add BigCactusLabs/bigcactuslabs-plugins` followed by
  `/plugin install dead-letter`. The plugin bundles the existing
  `dead-letter-mcp` server (via `uvx --python 3.12 --from dead-letter[mcp]==0.2.1`)
  with four slash commands (`/dead-letter:convert`, `/dead-letter:summarize`,
  `/dead-letter:triage`, `/dead-letter:cabinet`) and one auto-trigger skill
  (`dead-letter-context`). Plugin
  release versioning is independent of the package version — see
  [`docs/reference/publishing.md`](docs/reference/publishing.md#plugin-release).
- `tests/plugin/` structural and content tests covering the manifest, MCP
  launcher pin, skill frontmatter, slash command surfaces, and CI wiring.
  CI now runs `pytest tests/plugin` and the pinned Claude Code plugin validator
  (`npx --yes @anthropic-ai/claude-code@2.1.145 plugin validate plugin/`) on
  every PR.

### Changed

- Locked the resolved `html-to-markdown` dependency to 3.5.3.
- MCP directory conversion now requires an explicit `output_directory` and
  rejects batches above 50 `.eml` files before writing output.

### Fixed

- State-changing API routes now require a CSRF token, and the frontend sends
  the token for import, settings, job, and watch requests.
- Rendering and MCP conversion paths are more defensive around thread metadata,
  attachment references, and command-side batch safety.
- Plain-text conversion now preserves Markdown code regions while still
  escaping HTML-like payloads outside code.
- The macOS launcher now handles the ready-exit race where `dead-letter-ui`
  exits after the local UI is already reachable.

## [0.2.0] - 2026-05-26

### Added

- MCP server for Claude Desktop and Claude Code integration. Install with
  `dead-letter[mcp]`, launch with `dead-letter-mcp`. Provides 4 tools:
  `convert_eml`, `convert_eml_to_bundle`, `convert_directory`, `get_diagnostics`.
- Conversion options (strip signatures, dry run, etc.) now persist to
  localStorage and restore on page reload.
- Added a canonical migration guide for upgrading from `html-to-markdown` 2.x
  to 3.x in `docs/reference/html-to-markdown-v3-migration.md`.

### Changed

- `html-to-markdown` runtime support now targets v3 (`>=3.1.0,<4.0`).
- Dependency floors raised for backend/dev runtime packages:
  `fastapi>=0.136.0`, `mcp>=1.27.0`, `python-multipart>=0.0.26`,
  `uvicorn[standard]>=0.45.0`, and `pytest>=9.0.3`.

### Fixed

- Core conversion no longer aborts batch runs when a discovered source file
  disappears before processing; missing sources now return per-file failure
  results.
- Directory `.eml` scans now deduplicate in-tree symlink aliases that resolve
  to the same source file, preventing duplicate conversions and move/delete
  collisions.
- Import endpoints now enforce a backend 100 MB per-file upload limit and
  return `413` for oversized files.
- GitHub Actions workflows are now pinned to immutable action commit SHAs
  instead of mutable version tags.
- Attachment extraction now falls back to stdlib MIME parsing when
  `mail-parser` yields fewer named attachments, and records
  `attachment_parser_disagreement` diagnostics warnings.
- Conversion now emits `attachment_reference_without_attachments` when the
  rendered message body references attached files but none were retained.
- `strip_signature_images` now recognizes Front signature wrappers, including
  generated `...Signature` containers in quoted content.
- Stripped or otherwise unreferenced inline signature/tracking assets are no
  longer surfaced in bundle attachment output or attachment front matter.
- Settings Cancel and Escape now revert unsaved conversion option changes
  instead of preserving them in memory.
- Form labels in setup modal and settings panel are now properly associated
  with their inputs via `for`/`id` attributes for screen reader support.
  Manual Job input field now has a visible label.
- Setup modal traps keyboard focus and marks background content as `inert`,
  preventing tab navigation to elements behind the overlay.
- Batch confirmation overlay now marks the idle drop zone as `inert`,
  preventing keyboard interaction with the file input behind the dialog.
- History row expansion no longer collapses when clicking on expanded
  detail content (output paths, error messages, diagnostics).
- `relativeTime` helper now tolerates up to 30 seconds of server-ahead
  clock skew instead of showing blank timestamps.
- Quote-pattern detection no longer imports the removed
  `html-to-markdown` v2 `convert_with_visitor` API.
- Runtime version reporting now matches package metadata.

## [0.1.2] - 2026-04-28

### Fixed

- Pinned `html-to-markdown` to `>=2.9.1,<3.0` to prevent import-time crashes
  caused by upstream v3 removal of `convert_with_visitor`.
- Long-term v3 migration is tracked in issue [#11](https://github.com/BigCactusLabs/dead-letter/issues/11).

## [0.1.1] - 2026-03-26

### Added

- First-run setup modal prompts users to configure Inbox and Cabinet folders
  on first launch, with `~/letters/Inbox` and `~/letters/Cabinet` as defaults.
- Degraded UI state when unconfigured: watch card disabled with tooltip,
  persistent "Workspace not configured" banner with setup link.
- localStorage-backed modal dismissal — modal shows once per install, banner
  handles re-engagement.
- Save button in Settings highlights when paths or conversion options have
  unsaved changes.

### Changed

- Default suggested paths changed from `~/Documents/dead-letter/` to
  `~/letters/`.
- License changed from MIT to PolyForm Noncommercial 1.0.0 — free for
  personal, educational, and nonprofit use; commercial use requires a
  separate license.

## [0.1.0] - 2026-03-25

### Added

- Core `.eml`-to-Markdown conversion pipeline with YAML front matter output.
- HTML sanitization via nh3 with allowlist-based tag filtering.
- Thread detection and quoted-content handling using html-to-markdown visitor
  callbacks and mail-parser-reply for text-based splitting.
- Attachment extraction with configurable output directories.
- Calendar (`.ics`) event parsing and inline rendering.
- CLI interface with file/directory input (`dead-letter convert`).
- Web UI with drag-and-drop file input, real-time conversion progress,
  expandable diagnostics, settings panel, and an Inbox watch mode for
  continuous folder monitoring.
- macOS launcher for one-click startup.
- CLI restructured to subcommands (`dead-letter convert`, `dead-letter doctor`)
  with backward-compatible bare path invocation.
- `dead-letter doctor` health check command with text and `--json` output modes.
  Validates Python version, core dependencies, optional extras, and configured
  workflow paths.
- Conversion grade badges (Pass / Review / Fail) in done workspace header,
  computed from diagnostics state with inline SVG icons.
- Stripped images surfacing: count summary below done counts (clickable to
  expand diagnostics) and per-image detail in diagnostics disclosure.
- Optional JSON conversion report (`--report` CLI flag, UI toggle) writing
  `.dead-letter-report.json` to Cabinet with per-file diagnostics.
- `--allow-fallback-on-html-error` and `--allow-html-repair-on-panic` CLI flags
  for the `convert` subcommand.

### Fixed

- Flatten `ExceptionGroup` sub-exceptions into individual `ErrorItem` entries
  in the job runner, instead of producing a single opaque message.
- Cap the import file collision loop at 10,000 iterations and return a
  structured 500 error when exceeded.
- Make `convert_dir()` skip symlinked `.eml` files whose resolved targets
  escape the requested input tree, while consistently picking up mixed-case
  `.EML` files.
- Sanitize bundle attachment filenames to safe basenames before writing them
  and surfacing them in bundle metadata and front matter.
- Crash on boolean/empty HTML attributes (e.g. `disabled`, `class=""`) during
  conversation segmentation.
- Signature stripping now recognizes the RFC 3676 standard delimiter (`-- \n`
  with trailing space), matching Thunderbird, Apple Mail, and Gmail.
- HTML quote patterns and image-ref rewriting no longer applied to plain text
  body when the HTML part is empty.
- Tracking pixel detection no longer false-positives on `max-width`,
  `min-height`, and similar compound CSS properties. Also handles
  `!important` declarations.
- Signature boundary extension stops at block-level elements containing text
  content instead of stripping all subsequent sibling images.
- Conversion report (`.dead-letter-report.json`) is now written even when the
  worker TaskGroup raises an exception.
- Cancel button disables immediately on click, preventing double-cancel 409
  errors.
- Operational info messages (`opInfo`) now visible in the done workspace even
  when there are zero errors.
- Screen reader live-region announcements deduplicated during conversion
  polling.
- Expanding a history row no longer collapses on background reload.
- Poll session race condition where the old poll's `finally` block could
  reset `pollInFlight` after a new poll had already started, allowing
  concurrent polls on the next interval tick (job and watch stores).
- "Open Cabinet" button now surfaces backend errors instead of silently
  swallowing failures.
- Settings save no longer shows "Restart watch to switch to the new Inbox
  path" when only the Cabinet path changed.
- Report build/write failures are now logged instead of silently swallowed.
