# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- CLI interface with file/directory input and `--watch` mode for continuous
  inbox monitoring.
- Web UI with drag-and-drop file input, real-time conversion progress,
  expandable diagnostics, and settings panel.
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
