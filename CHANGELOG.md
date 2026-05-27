# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Claude plugin distribution under [`plugin/`](plugin/), released as
  `plugin-v0.2.0` and surfaced through the new
  [`BigCactusLabs/bigcactuslabs-plugins`](https://github.com/BigCactusLabs/bigcactuslabs-plugins)
  marketplace. Install in Claude Code or Cowork with
  `/plugin marketplace add BigCactusLabs/bigcactuslabs-plugins` followed by
  `/plugin install dead-letter`. The plugin bundles the existing
  `dead-letter-mcp` server (via `uvx --python 3.12 --from dead-letter[mcp]==0.2.0`)
  with four slash commands (`/dead-letter:convert`, `/summarize`, `/triage`,
  `/cabinet`) and one auto-trigger skill (`dead-letter-context`). Plugin
  release versioning is independent of the package version — see
  [`docs/reference/publishing.md`](docs/reference/publishing.md#plugin-release).
- `tests/plugin/` structural and content tests covering the manifest, MCP
  launcher pin, skill frontmatter, slash command surfaces, and CI wiring.
  CI now runs `pytest tests/plugin` and `claude plugin validate plugin/` on
  every PR.

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
