# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Claude plugin 0.4.10 pins package 0.4.8 (`dead-letter==0.4.8`) and
  relocks `plugin/uv.lock`, which moves selectolax to 1.0.0. Plugin users get
  the 0.4.8 fixes, including `strip_signatures` on HTML emails with
  html-to-markdown 3.17, and the `convert_directory` `error_code` field that
  `/dead-letter:triage` reports.

## [0.4.8] - 2026-10-09

### Changed

- **MCP success-JSON contract change:** `convert_directory` replaces
  `errors[].error` with `errors[].error_code`; callers must use the new key.
  Each failed file retains its `file` path and returns a stable code, with
  `conversion_error` for missing or empty codes. Raw exception details can
  contain email-derived text and are now logged on the server rather than
  returned to agents. Batch counters, output paths, and successful MCP-call
  status for per-file failures are unchanged, including dry runs (#186).
- HTML parsing now uses selectolax's Lexbor backend, and the minimum
  dependency is `selectolax>=1.0.0,<2` (replacing the `<1` cap from 0.4.6).
  selectolax 1.0 removed the Modest backend. Conversion output is unchanged
  on the test fixtures, benchmark corpus, and MBOX fixture. MBOX resume
  journals record the selectolax version, so a `--mbox-resume` run started
  on an earlier dead-letter release is rejected with `mbox_resume_mismatch`
  after upgrading; rerun into a new output directory. The experimental
  analysis snapshot `normalization_version` changes for the same reason.
- Claude plugin 0.4.9 pins package 0.4.7 as `dead-letter==0.4.7` (no `[mcp]`
  extra) and ships `plugin/pyproject.toml` and `plugin/uv.lock`, so hosts that
  launch from a lock get hashed dependency versions (#212). `plugin.json` now
  declares the listing icon, keywords, and repository, documentation,
  privacy-policy, and support links. The plugin README adds a one-step
  `--marketplace` install command and explains why the server runs as a
  pinned PyPI package.
- The plugin schema check now runs `claude-code@2.1.295`, which accepts the
  listing fields above.
- `release.py prepare` no longer adopts the plugin pin. The new
  `release.py prepare-plugin` does so after the package is on PyPI, and
  `release.py check` requires the plugin project and lock to match the
  launcher pin.

### Fixed

- `strip_signatures` removes signatures from HTML emails again when
  html-to-markdown 3.17 or later is installed. Those releases write the `-- `
  delimiter as `\--`, which the delimiter pattern did not match, so fresh
  installs of 0.4.7 and plugin 0.4.9 kept HTML signatures. The pattern also
  accepts a delimiter followed by a `<br>` line break. The lock moves to
  html-to-markdown 3.17.2. A comparison of 2,353 conversion cases against
  3.15.1 found two other upstream output changes, both corrections: a blank
  line now separates a table from the text after it, and a `<br>` inside a
  table cell becomes a space instead of joining the words.
- A signature image whose filename matches more than one pattern (such as
  `facebook-icon.png`) now always reports the same `filename_pattern:` reason,
  the first match in the documented pattern order. It previously varied
  between runs.

## [0.4.7] - 2026-10-08

### Changed

- The MCP SDK (`mcp>=2.1,<3`) is now a core dependency, so a plain
  `uvx --from dead-letter==X dead-letter-mcp` or `pip install dead-letter`
  starts the MCP server without an extra. This lets the Claude plugin launch
  from a locked `uv.lock` (#212). A bare install now pulls the SDK and its
  dependencies (about 40 packages instead of 11). `dead-letter[mcp]` remains
  a valid, empty compatibility extra; existing commands keep working.

- Claude plugin 0.4.7 (still pinned to package 0.4.6) adds Troubleshooting and
  Support sections to the plugin README: connection checks, first-launch
  downloads, the selectolax 1.0 failure in older plugin pins, Cowork path
  access, and where to report bugs and vulnerabilities.
- Claude plugin 0.4.8 (still pinned to package 0.4.6) adds a listing icon at
  `plugin/.claude-plugin/icon.png` and replaces the README's piped uv install
  command with a link to uv's installation guide.

## [0.4.6] - 2026-10-06

### Added

- Opt-in `--mbox-resume` / Python `convert_mbox(..., resume=True)` for flat
  Markdown and Cabinet-style bundle imports, including timed workers: durable
  source/options/converter-bound receipts, hash-verified reuse, reconciliation
  after output/report interruptions, missing/failed-record retries, and no-clobber
  publication. Bundle receipts verify Markdown, source bytes, every retained
  attachment and directory identity before reuse. Complete bundles publish through
  a probed no-replace directory rename on Linux, macOS and Windows; flat files use
  hard links. Edited, partially missing or conflicting output stops the import
  rather than being overwritten or silently repaired; a message whose receipt
  exceeds 1 MiB, or whose bundle exceeds 4096 files, fails only that record.
  Unprepared partial bundles and one copy of an over-limit bundle are retained
  privately as abandoned attempts, not recursively deleted; reruns fail an
  over-limit record again without reconverting it. Bundle identity survives a
  remount or reboot.
  Resume reports retain source order, add recovery status/attempt counts, and
  never overwrite earlier reports. Requires a trusted local filesystem with the
  appropriate publication support. Compressed input, dry-run, MCP and web/UI resume
  remain unsupported; this is not a universal power-loss guarantee. See
  [MBOX resume](docs/reference/mbox-resume.md) (#139).
- MCP tools now declare `readOnlyHint`, `destructiveHint`, `idempotentHint`
  and `openWorldHint` annotations: `get_diagnostics` is read-only, the four
  conversion tools create new files without modifying sources, and no tool
  makes network requests.

### Fixed

- Fresh installs no longer fail on import with selectolax 1.0, which removed
  the `selectolax.parser` (Modest) backend that dead-letter uses. The
  dependency is now capped at `selectolax<1`. Fresh installs of 0.4.5 resolve
  selectolax 1.0 and fail until a release carries this fix.
- MBOX MCP failures now use stable codes and errno-derived OS reasons instead
  of raw exception text or filenames. Failure-summary codes and messages are
  restricted to reviewed fixed strings; detailed errors remain in local logs
  and reports rather than entering the MCP response (#187).
- MBOX MCP imports now close their iterator before publishing the report, so
  an early-stop cleanup failure produces a failed partial report rather than
  leaving a report marked successful. Byte/message caps are not a wall-clock
  deadline; MCP cancellation remains unsupported (#145).
- Gmail forwarded messages are no longer dropped as quoted reply history.
  In the default `latest` thread mode, Gmail HTML forwards are kept inline,
  including sequential and nested forwards and any reply quoted inside a
  forward; body text after or between forwards is emitted before them. In
  `structured` mode each Gmail forward and each unquoted plain-text forward
  separator gets its own `## Forwarded from …` (or `## Forwarded message`)
  section in document order, with no leftover separator lines, and counts
  toward `thread_messages`. Forward detection also recognizes localized
  Thunderbird, Yahoo and Apple Mail separators. A text/plain forward inside
  `>` quoting is still treated as quoted history in `latest` mode, and
  signature stripping can still remove text after a `-- ` line inside a
  forward. Output for mail without a forward is unchanged.
- A plain-text Outlook reply (`____` or `-----Original Message-----` plus a
  From/Sent block) whose history contains a forward separator no longer
  leaks that reply history into `latest` output.
- `release.py homebrew-prepare` works with Homebrew 7. It skips Homebrew's
  audit of the intermediate sdist formula (`brew style` still checks the final
  formula), adds `depends_on "libyaml"` beside a `pyyaml` resource as
  Homebrew's style rules now require, and restores the tap formula when a
  Homebrew command writes it and then fails.

### Changed

- The `convert_eml` MCP tool description now says attachments are listed in
  front matter but not written to disk (`convert_eml_to_bundle` saves them),
  and how `structured` mode and forwarded content interact.

## [0.4.5] - 2026-09-28

### Added

- The MCP server now reports a title, description, website URL, package
  version and embedded icon in `serverInfo`, and each tool has a display
  `title` (for example "Convert email"), so MCP clients can show dead-letter
  with its logo and readable tool names.
- Opt-in resource budgets for MBOX message workers: `--mbox-cpu-seconds` and
  `--mbox-max-output-mib` (Linux and macOS) and `--mbox-memory-mib` (Linux),
  with matching `convert_mbox` parameters. Budgets require `--mbox-timeout`;
  an unsupported control fails before conversion (`mbox_budget_unsupported`),
  a limit the worker cannot apply aborts the import (`mbox_budget_apply_failed`),
  and an exceeded budget fails only that record (`mbox_message_resource_limit`)
  with its partial output withheld. Windows is not yet supported. These are
  resource limits, not a sandbox. See [MBOX workers](docs/reference/mbox-workers.md#optional-resource-budgets) (#140).
- A bounded `convert_mbox` MCP tool converts one flat `.mbox` file (at most
  256 MiB, first 1000 messages) into `output_directory` with the CLI's streaming
  report under a collision-safe name. It returns counts, `truncated`, the report
  path and at most 20 failure entries, never message content. Compressed archives
  are rejected, the source is never modified, and MCP call cancellation is not
  supported. See the [runtime contract](docs/reference/v4-runtime-contracts.md#mcp-server-dead_letterbackendmcp_server) (#145).
- Claude plugin: a user-typed `/dead-letter:mbox <path> [output-dir]` command
  calls `convert_mbox` with an explicit output folder (`outputs/mbox/<run-id>`
  in Cowork, the given folder or a fresh temp directory in Claude Code), never
  beside the source. The context skill redirects natural-language MBOX requests
  to it instead of calling the tool directly; compressed or oversized archives
  are pointed at the CLI.
- CLI/Python ZIP and TGZ Takeout ingestion with exact MBOX member selection,
  private staging, byte/member budgets, integrity and source-change checks,
  and archive provenance in Markdown and reports. ZIP requires Python 3.12.3+.
  MCP/web ingestion is unchanged. See the [compressed-input guide](docs/reference/gmail-takeout.md#compressed-input)
  (Refs #144).
- Experimental single-message analysis sidecars with `--output` and async
  `analyze_to_sidecar`: no-clobber writes, separate failure/skip attempts, strict
  source/effective-input binding, validated offline reuse, alias-age limits and
  concurrent-winner checks. Sources remain unchanged; saved results contain no
  body/attachment text. See the [contract](docs/reference/experimental-analysis.md)
  for filesystem durability limits (#164).
- Five [conversion recipes](docs/recipes/README.md) for Markdown/Obsidian,
  RAG preprocessing, local MCP conversion, Cabinet archiving, and report/quality
  auditing, with shared synthetic mail and a released-package smoke check (#162).
- A repeated synthetic MBOX worker benchmark separates measured worker lifetime
  and parent publication, verifies output/provenance/diagnostic parity, and records
  timing variability and bounded disk/RSS observations. See the
  [measurement guide](docs/reference/mbox-validation.md) (#141).
- Experimental semantic analysis: two candidate TypeSafe/JEV triage profiles,
  normalized-evidence state assembly, redacted-by-default previews, effective-input
  fingerprints, native-answer validation and a synthetic development seed. Real
  `.eml` files use `prepare_eml` or `dead-letter analyze --provider typesafe --dry-run`
  through a shared read-only core snapshot. Hashes bind to the exact parsed bytes;
  pre-render quotes, signature text, unknown attribution and missing timezones
  remain distinct. `--show-state` explicitly exposes private local evidence (#110).
- Explicit single-message BYOK execution through the optional, exact TypeSafe SDK
  0.7.1. CLI provider opt-in and async Python permission gates disclose
  the validated host before sending normalized evidence. Request-local SDK logging
  filters, redirect/response-size guards, SDK-owned bounded retries, safe attempt
  records and versioned result envelopes keep failures distinct from predictions.
  `doctor` reports SDK/key presence without contacting the provider. Dry-run and
  ordinary conversion remain local; batches remain pending. See
  [experimental analysis](docs/reference/experimental-analysis.md).
- Optional `typesafe` extra with the exact SDK pin in `uv.lock`, plus isolated
  wheel/sdist checks for offline previews and real-SDK fake-HTTP contracts.
  Base installs and the UI launcher remain SDK-free; installing the extra does
  not enable remote analysis (#163).
- A dedicated SDK contract workflow runs the real pinned SDK with synthetic EML
  and fake HTTP, including import-time DEBUG logging, timeout/cancellation,
  redirect/auth, response-validation and retry boundaries. No live inference or
  empirical email-triage quality claim is part of these tests (#110).
- Maintainer release helpers: `release.py status --version X.Y.Z` reads PyPI,
  GitHub release assets, GHCR, the MCP Registry, the plugin marketplace, and
  the Homebrew tap and reports each channel as verified, missing, deferred,
  conflicting, or unable-to-verify without repairing anything (#125).
  `release.py homebrew-prepare` prints a tap formula plan by default, edits
  only the formula with `--write`, and opens a draft tap PR with `--open-pr`
  after the reviewed patch hash matches; it never merges (#129). See the
  [release operations guide](docs/reference/release-operations.md).
- Opt-in `Content-Length`-framed MBOX dialects: `--mbox-unescape mboxcl` /
  `mboxcl2` and Python `unescape="mboxcl"` / `"mboxcl2"`. A length frames a
  body only when it lands on one line ending followed by a valid postmark, or
  on a single final line ending at EOF; the check uses bounded reads and body
  postmark-like lines never split a validated message. `mboxcl` removes one
  `>` from body `>From ` lines and falls back to postmark scanning for a
  message with a missing or invalid length, recording
  `framing_diagnostic: mbox_content_length_fallback` in its provenance;
  `mboxcl2` stops with an archive error instead. Other modes still refuse
  `Content-Length`. Parity is tested against an independent test reference
  reader, not archives written by mutt or Dovecot. See the
  [Takeout recipe](docs/reference/gmail-takeout.md#content-length-framing-mboxcl-and-mboxcl2) (#143).

### Fixed

- Directory conversion preserves output-directory intent for names ending in
  `.md`, including nested input folders. Existing `.md` directories and explicit
  trailing separators are no longer treated as output filenames; dry runs
  still create nothing.
- Bundles retain inline image files and attachment metadata when image embedding
  is combined with signature/tracking-image filtering. The retention pass
  recognizes rendered data URIs as references.
- Removing a signature image no longer deletes a separate body image sharing its
  Content-ID. CIDs still used by images in the filtered HTML are retained;
  stripped-image diagnostics still report the removed occurrence.
- Named zero-byte attachments are retained in MIME fallback extraction and
  bundle output instead of silently disappearing. Metadata-only entries with
  no payload still do not create attachment files. Text attachments whose
  declared charset cannot encode (unknown, non-text, `idna`, `undefined`) fall
  back to UTF-8 instead of aborting conversion.
- Inline-image data URIs remove MIME base64 line wrapping so embedded Markdown
  image destinations stay on one line, without changing attachment bytes.
- Subject decoding falls back to UTF-8 replacement for unsupported charset
  decoders and preserves malformed RFC 2047 encoded words instead of raising.
- Compressed Takeout hardening: bounded ZIP/ZIP64 directory admission and TAR
  metadata, strict TAR end-marker validation, staging access/space checks, and
  report-only container paths/stat signatures. Record provenance keeps `archive`
  as a basename string and adds `container` details. ZIP entry counts are taken
  from the central directory itself rather than the EOCD record, TGZ input is
  refused if the tarfile hooks behind its metadata limits are unavailable, and
  macOS `._*` / `__MACOSX/` metadata is never an MBOX candidate (Refs #144).
- Experimental analysis sidecars now reject unusable destinations before inference,
  preserve full results on write failure, and run provider preflight before new
  source reads. Reuse binds adapter/SDK versions, reports the current source
  basename, tolerates bounded clock skew, and retries a vanished target once.
  Failure/skip records use `attempt_recorded`; macOS file writes request
  `F_FULLFSYNC` where supported (#164).
- The MCP `convert_directory` inputSchema now lists `output_directory` as
  required, matching the existing runtime requirement, and an empty
  `output_directory` is rejected instead of writing into the server's working
  directory. The tool description now states the 50-file limit per call.
- MCP bundle conversion keeps the copy-only rejection message visible with
  MCP SDK 2.2 while preserving the source and rejecting move/delete requests.
- Homebrew preparation reports when a released sdist clears Homebrew's 24-hour
  PyPI upload delay and refuses early `--write` attempts before changing the tap.
  Failed public Homebrew commands include a bounded, sanitized stderr detail
  to help diagnose resolver failures (#133).
- MCP tool errors for a missing file or directory, invalid arguments, and
  conversion failures again reach clients with their documented message under
  MCP SDK 2.1 and later, as `Error executing tool <name>: <message>`. Single-file
  conversion failures now report a stable error code (`html_markdown_failed` or
  `conversion_error`) instead of raw parser text, which is logged on the server;
  `convert_directory` per-file `errors[]` entries are unchanged. Unexpected
  errors stay generic, and the `mcp` extra now requires SDK 2.1 or later, the
  first release that withholds their text.

## [0.4.0] - 2026-09-19

### Added

- Generated VS Code and Cursor MCP install links, client-specific VS Code,
  Cursor, and Cline configurations, and a Cline marketplace submission
  candidate derived from the canonical `server.json` launch contract. Public
  convenience links do not advance to unpublished release-prep pins (#104).
- Cross-platform public-PyPI launcher smoke checks and pinned upstream Cline
  catalog validation, plus client installation guidance and a dated
  [MCP distribution ledger](docs/reference/mcp-distribution.md). Catalog
  submissions, ownership claims, and named-client GUI checks remain separate
  acceptance steps; generated metadata does not claim admission (#104).
- Optional timed MBOX message workers via `--mbox-timeout SECONDS` and Python
  `timeout_seconds`. A fresh subprocess runs the existing conversion pipeline;
  timeouts and abnormal exits are isolated to one message, and the parent only
  publishes validated completed artifacts. Default conversion is unchanged.
  This is not a memory/security sandbox or durable resume. See the
  [worker contract](docs/reference/mbox-workers.md) (#103, follow-up to #115).
- Streaming `.mbox` / Gmail Takeout conversion via the CLI and lazy Python API,
  reusing the EML pipeline with source-order byte/hash provenance, preserved
  Gmail labels, collision-safe names, per-message resource limits, partial
  failures, and disk-backed JSON reports. `--mbox-bundles` writes Cabinet-style
  source/attachment bundles. See the [Takeout recipe](docs/reference/gmail-takeout.md).
  Explicit quoting policies avoid guessing; Content-Length-framed dialects,
  compressed/live mailboxes and MCP/web ingestion are outside this slice (#103).

### Fixed

- MBOX import no longer mistakes folded header continuations for a top-level
  `Content-Length` field. Source-change checks bind the opened file to its path,
  respect Windows metadata semantics, and stop following appended data. Malformed
  postmark tails no longer trigger quadratic regex backtracking. Interrupted
  report appends publish only complete entries, and Ctrl-C during report
  publication exits cleanly. Import/report tests now run on all three CI platforms (#103).
- Conversion reports now replace lone surrogates outside surrogateescape's byte
  range instead of aborting JSON generation, while preserving existing decoded-byte behavior.

## [0.3.1] - 2026-09-19

### Fixed

- The container release job now resolves each platform's child manifest digest
  from the pushed index and tests and anonymously re-pulls that per-platform
  digest. One image store cannot hold two platform variants of the same index
  reference, which failed the `0.3.0` container publish on arm64. The promoted
  release tag still points at the tested index digest (#108).
- The release workflow's PyPI wait now also requires the new version in the
  PyPI simple index, not only the JSON API, and waits up to ten minutes. The
  JSON API went live first for `0.3.0`, so the MCPB bundle's `uv lock` could
  not resolve the pin it had just published (#107).

## [0.3.0] - 2026-09-19

### Added

- Optional OCI container distribution: locked multi-stage builds, non-root
  stdio runtime, MCP ownership/OCI labels, native amd64/arm64 CI smoke tests,
  and release-only GHCR publication with provenance and SBOMs. Version tags
  are promoted only after published-digest tests and anonymous access checks;
  release metadata then adds a digest-pinned OCI package while retaining PyPI
  and MCPB. No `latest` tag or existing-version backfill is introduced (#108).
- A generated Docker MCP Catalog submission candidate and
  [container launch/release runbook](docs/reference/containers.md), including
  selected read-only input/writable output mounts, host-user mapping,
  license-review requirements, and explicit publication/admission gates.
  Docker Catalog acceptance remains a separate pending step (#108).
- A one-click MCP Bundle (`.mcpb`) for Claude Desktop and other MCPB-aware
  clients. The release workflow builds and smoke-tests the bundle against the
  published PyPI package, then attaches `dead-letter-mcp-X.Y.Z.mcpb` and its
  `.sha256` checksum to the GitHub release. The registry `server.json` also
  gains an `mcpb` package entry alongside the existing PyPI entry, stamped
  with the release asset URL and its SHA-256. CI builds and smoke-tests the
  bundle on every push, on ubuntu, macOS, and Windows (#107).
- A portable Agent Skill under `skills/dead-letter/`, written to the
  agentskills.io spec so any skill-aware host can use it: Claude Code, Codex,
  GitHub Copilot, Cursor, Amp, and Gemini CLI. It covers the MCP tools and the
  `uvx` CLI path, states the `.eml`-only input boundary, and carries the
  untrusted-email-content rule. Install it with
  `gh skill install BigCactusLabs/dead-letter dead-letter --agent <agent>`.
  The Claude-specific skill under `plugin/skills/` is unchanged (#109).
- An Agentic Resource Discovery catalog at `.well-known/ard.json` advertising
  the MCP server and the portable skill to ARD-aware crawlers. Both entries'
  `version` fields are a release sync point with `server.json` (#109).
- CI now validates the portable skill with `gh skill publish --dry-run`, and
  `tests/plugin/` gates the skill content and the ARD catalog. New reference
  doc: `docs/reference/agent-discovery.md` (#109).

### Fixed

- Experimental analysis rejects Score values inconsistent with their probability
  maps and Choice values that do not select a maximum-probability alternative,
  while preserving native values and allowing numerical tolerance/ties (#110).
- Quoted-message attribution debug logs no longer include private email prefixes.
- Claude plugin releases now publish an explicit version, release tag, and
  commit SHA to the Big Cactus Labs marketplace before advancing the legacy
  `release` branch. This lets Cowork detect the marketplace commit and keeps
  Cowork and Claude Code on the same immutable plugin assets.
- Forwarded-as-attachment messages (`message/rfc822` or `multipart/digest`
  parts) no longer leak the embedded message's body into, or replace, the
  outer message body. The embedded message is now recorded as an attachment
  instead. Parts carrying `Content-Transfer-Encoding: base64` or
  `quoted-printable` are decoded first, so the recorded `.eml` attachment
  holds the original embedded message bytes rather than a double-encoded
  copy (#92).
- Subjects that slugify to empty, including non-Latin-script subjects with no
  ASCII decomposition, now fall back to the slugified source filename stem
  instead of the generic `email` filename (#95).
- `JobManager` now retains references to background job tasks so a running
  job can no longer be garbage-collected mid-run; exceptions escaping the job
  runner are now logged (#96).
- Nested HTML lists now preserve indentation and use `-` bullets at every
  level instead of cycling markers by depth, so converted Markdown nests
  correctly under CommonMark instead of splitting into sibling lists (#89).
- `write_report` no longer reads or mutates the process-wide umask; the
  report temp file is created with default (0o666) permissions so the kernel
  applies the umask itself (#97).
- `Content-Disposition: inline` attachments that are not images (PDFs,
  `.ics`, spreadsheets) are no longer dropped as unreferenced inline assets;
  only unreferenced inline images are removed. A new diagnostics warning,
  `attachment_discarded_with_source_deleted`, now fires when a non-dry-run
  `source_handling="delete"` conversion discards attachment bytes (#93).
- Signature-image detection no longer strips full-size inline images on a
  bare substring match: it now requires a small or absent rendered
  dimension and matches against filename tokens rather than the whole URL.
  `diagnostics.attachments.referenced` now counts attachments before
  `filter_images` exclusions, so images removed by any filtering layer are
  reflected in the referenced/retained counts (#94).

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
