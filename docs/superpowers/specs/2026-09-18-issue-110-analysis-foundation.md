# Issue #110 — implementation checkpoint

Updated September 18, 2026. Working PR: #117. This checkpoint supersedes the
initial contracts-only handoff; the issue's complete first usable slice remains
open. Public usage/limits: [experimental analysis](../../reference/experimental-analysis.md).

## Implemented

The first commit (`a341f028`) added immutable normalized-evidence records,
deterministic state assembly, two experimental profiles, default-private previews,
effective-input fingerprints and native-answer validation. Its 22-case synthetic
seed remains unreviewed and development-only.

The continuation (`dd33a2f`) connects that foundation to real EML:

- `core.mime.parse_eml_bytes` is the shared implementation used by both the
  existing file parser and the new read-only path; no second MIME stack.
- `_build_pipeline_snapshot` retains pre-render zones. Existing conversion uses
  the original four-value `_build_rendered_markdown` compatibility wrapper, with
  unchanged normalization/rendering behavior and writer defaults.
- `core.snapshot.read_snapshot` opens a bounded regular file, hashes the exact
  immutable bytes it parses and never rereads it during normalization. It returns
  frozen text-only records and detached local diagnostics/provenance.
- The source Date header remains evidence, without invented timezone/current-time
  arithmetic. Quotes are not promoted to authored content by rendering fallback.
  Signature/P.S. text remains available; attachment/calendar text is not evidence.
- `analysis.prepare_eml` projects core zones to the existing state builder. A
  forward marker shares its following body's context slot. Unknown zone kinds
  fail closed; unknown authors remain unknown. Installed normalizer versions and
  effective options participate in input identity.
- `analyze --provider typesafe --dry-run` emits local JSON; `--show-state` explicitly
  exposes sensitive state. Non-dry-run requests fail before source reading. Keys
  are never read, nor are SDKs imported. The CLI validates/discloses an explicit
  environment endpoint; Python APIs use explicit endpoint arguments instead.
- Score/probability means and Choice/maximum-probability consistency are checked
  without replacing native values or manufacturing confidence. This addresses the
  Codex review finding on PR #117. The existing attribution-prefix DEBUG payload
  was also removed, with a regression test.

This implementation does not write sidecars, run batches, move/delete source mail,
change MCP/UI tools, add dependencies or change releases. A raw-source bound is
not a streaming mailbox implementation or a total process-memory guarantee.

## Next implementation order

1. Add the optional TypeSafe adapter with a verified/pinned SDK range. The SDK
   changelog reports breaking 0.7.0 serialization changes on September 18. Read
   BYOK only from the environment, require explicit consent and disclose the
   effective validated host. Prevent SDK request/response body logging even when
   imported under verbose logging; disable redirects/auth forwarding. Test the
   actual pinned SDK with fake HTTP responses, including 401/422/429/overload,
   timeout, connection errors and missing/unknown answer kinds. No incidental
   paid/private inference in installation, doctor or CI.
2. Add service execution/assessment envelopes, provenance, no-clobber atomic
   sidecars and valid-result-only reuse. Store requested/returned model,
   evaluated-at time, coverage, usage/request ID and attempts, not raw SDK dumps.
   Input fingerprints alone do not make a cache. Do not use source hash alone or
   treat a failed/timed-out attempt as a successful/unbilled result.
3. Enable the explicit remote CLI/Python execution path only through that service.
   Add directory output, bounded concurrency, one-layer retry/time budgets,
   cancellation and partial-success tests. Add installed/configured-only doctor
   checks and current provider privacy/setup guidance. Keep all existing local
   conversion/MCP/UI entrypoints local; a key is not consent.
4. Human-review/expand the seed; add authorized examples and a family-separated
   held-out set. Compare both profiles, relevant context/cleanup policies and a
   deterministic baseline. The proposed 250–500-example starting target remains
   open. Contract tests cannot establish classification quality or calibration.

## Boundaries that still matter

- `NormalizedMessage` also supports caller-supplied normalized exports; it is not
  a sanitizer, secret scrubber or license to build an alternate parser.
- The snapshot records the bytes read, not a filesystem transaction against a
  concurrently writing process. Actual inode changes after reading do not alter
  the immutable buffer/hash. Preserve this same-buffer property in future MBOX work.
- The snapshot currently uses strict existing HTML-conversion behavior, with no
  new fallback/repair override. Degraded conversion is coverage, not an assessed no.
- First-N context follows supplied/pipeline order, not a chronology or semantic
  retrieval guarantee. No external parent messages or attachment text are fetched.
- `validate_response` is structural only. Missing evidence, failed execution and
  an assessed negative must remain separate in service/result envelopes.
- No profile-defined endpoints, executable custom profiles, dynamic templates,
  environment expansion or automatic transfer to another model provider.

## Verification record

Original foundation: 103 component tests passed locally in an isolated package
harness; GitHub CI subsequently passed core/backend/plugin/frontend tests and
plugin/skill validation for `a341f028`.

Continuation: Python compilation was checked locally. The working container
cannot install the repository dependency graph, so real EML execution was verified
in GitHub CI, not by a stubbed parser. Run
[35387757900](https://github.com/BigCactusLabs/dead-letter/actions/runs/35387757900)
tested the merge of `dd33a2f` into base `b28d643` and passed:

- Core: 316 tests; backend: 354 tests; plugin: 92 tests; frontend: 87 tests.
- Plugin schema, Agent Skill validation and frontend syntax.
- MCPB build/smoke tests on Windows, macOS and Ubuntu.
- The separate documentation-link run for the implementation commit also passed.

That is 849 passing suite tests, including 59 new parametrized cases in:

- `tests/core/test_snapshot.py` — 29 cases.
- `tests/backend/test_analysis_eml.py` — 20 cases.
- `tests/backend/test_analysis_response_consistency.py` — 10 cases.

The original `tests/backend/test_analysis_contracts.py` also remains in the
successful backend suite. New cases cover exact-byte provenance under source
replacement, parser equivalence, no-write/no-network behavior, quote-only
fallbacks, adopted forwards, P.S. text, missing timezones/parents, attachment
exclusion, bounds/FIFO rejection, default-private previews, CLI argument/endpoint
redaction, conversion dispatch and native answer consistency. All inputs are
synthetic. No key, live inference, private-email submission, cost/latency benchmark
or classifier-quality measurement is claimed. Later docs-only heads and their
checks are recorded in the PR rather than implying a new model evaluation.
