# Issue #110 — implementation checkpoint

## Implemented in this slice

The new `dead_letter.analysis` package is offline-only and stdlib-only. It contains
immutable normalized-evidence records, deterministic state assembly, two
experimental built-in profiles, redacted-by-default previews, effective-input
fingerprints, endpoint validation and strict native-answer validation. Backend
contract tests run under the existing backend CI suite. A 22-case synthetic
unreviewed development seed supports the next evaluation step.

This is not the issue's complete first usable slice. No production entry point
calls this package. Conversion, existing CLI dispatch, four MCP tools, UI,
dependencies and release versions are unchanged.

## Next implementation order

1. Expose a shared read-only core snapshot from the existing normalization path,
   including zones/attribution and coverage. Do not reconstruct MIME or rerun
   analysis on rendered Markdown with guessed quote boundaries. Ensure parser
   attachment-payload and inline-data-URI loading are disabled. Keep source hash,
   source-change detection and full diagnostics local. Map that snapshot into
   `NormalizedMessage` with a real normalization version; add byte-for-byte
   conversion regressions before wiring an EML analysis entry point.
2. Add the optional TypeSafe adapter and a verified/pinned SDK range. The SDK
   changelog reports a breaking 0.7.0 release on September 18; don't integrate from
   older msgspec examples. Read BYOK only from the process environment, require
   explicit provider consent and disclose the validated host. Protect email bodies
   from SDK debug logs, including import-time `TYPESAFE_LOG_LEVEL` and surrounding
   verbose loggers. Disable cross-origin redirects and prevent auth forwarding.
   Test with the actual pinned SDK and a fake HTTP transport; do not run paid or
   private-email inference as an incidental install/doctor/CI check.
3. Add service execution/assessment envelopes, safe failure codes, provenance,
   no-clobber atomic sidecars and valid-result-only reuse. The current fingerprint
   is necessary but insufficient for resume. Store requested/returned model,
   evaluated-at time, coverage, usage, request ID and attempts without raw SDK
   dumps. Keep missing evidence distinct from failed execution and assessed no.
4. Wire explicit `analyze` CLI/Python entry points, including bare-path dispatch,
   stdout-only single-file output and no-network preview. Directory output follows
   with bounded concurrency, single-layer retry budgets, 401 fail-fast, cancellation
   and partial-success tests. Add environment-presence/installation-only doctor
   checks, privacy/setup guidance and a real EML consumer recipe.
5. Human-review/expand the synthetic seed; add authorized examples and a true
   held-out family-separated set. Compare both candidate profiles and context
   policies. Do not call profile semantics stable from schema tests or fabricated
   answer fixtures. The proposed 250–500-example starting target remains open.

## Important boundaries

- `NormalizedMessage` is an integration contract, not a completed core snapshot.
  The caller still owns normalization; it is not a redactor or an EML parser.
- `validate_response` validates an allowlisted projection. It does not assert that
  an inference call succeeded or that the semantic judgment is correct.
- Context selection is deterministic first-N in caller order, not semantic
  retrieval, conversation reconciliation or a chronology claim.
- There is no profile-defined endpoint, executable custom profile, dynamic
  template, environment expansion, profile file reader or automatic fallback.
- Local byte caps are not a substitute for verifying provider token constraints.
- No source mail was read, uploaded, moved, deleted or modified in this work.

## Verification record

The isolated new-component suite passed locally: 103 tests. The working container cannot
clone GitHub or install the repository's dependency graph, so this local run used
a temporary parent namespace to import the real new package/tests; that harness
is not committed. This does **not** verify package startup, the four full suites,
EML conversion or SDK behavior. GitHub CI must supply repository integration
results. No key, live inference, latency/cost benchmark or classifier-quality
measurement was used or claimed.
