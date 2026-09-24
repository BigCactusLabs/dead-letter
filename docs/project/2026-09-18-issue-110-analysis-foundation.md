# Issue #110 — implementation checkpoint

Written September 18, 2026; PR #117 merged to `main` on September 21, 2026
(merge commit `70b8e2f`). The workflow-pin follow-up landed as #134. Nothing
below is released; the full issue remains open.
Public contract: [experimental analysis](../reference/experimental-analysis.md).
This supersedes the earlier offline-only handoff.

## Implemented so far

`a341f028` established immutable normalized evidence, deterministic state, two
experimental profiles, redacted-by-default previews, effective-input fingerprints
and native-answer validation. The 22-case synthetic seed is still unreviewed and
not held-out evaluation data.

`dd33a2f` connected real EML through shared `parse_eml_bytes`, pre-render zones and
`core.snapshot.read_snapshot`. Hashes bind to the exact immutable bytes parsed,
not a later reread. Original dates, unknown attribution, quotes, signature/P.S.
text and coverage are preserved. The original conversion wrapper/defaults remain.
`prepare_eml` and CLI dry-run expose this locally. Score/Choice consistency and
quoted-prefix debug logging were corrected. `7ea9ab4` recorded that checkpoint.

`b3f0c94` adds the remote execution path:

- `analysis/providers/typesafe.py`: exact SDK 0.7.0, imported lazily only after
  explicit opt-in, key/version preflight, endpoint agreement and disclosure.
  Credentials come only from process `TYPESAFE_API_KEY`; no key parameters or flags.
- An injected public `httpx2.AsyncClient` subclass constrains requests to the exact
  configured system-one endpoint, refuses redirects, streams bounded responses,
  strips nonessential response headers and records safe attempt metadata.
- SDK-owned bounded retries plus an async total budget; no retry multiplication,
  implicit environment proxy or automatic fallback provider/model.
- Request-local ContextVar logging filters installed before SDK import contain
  private wire logs even with SDK DEBUG environment/direct handlers. They do not
  change global logger levels and are inert for unrelated contexts.
- Strict custom Pydantic response projection, then existing completeness/native
  validation. Unknown SDK answer kinds cannot silently become absent negatives.
  Duplicate JSON keys/non-finite values fail; missing model/usage stays unknown.
- `service.analyze_eml` / `analyze_prepared` provide versioned result envelopes,
  separate execution/assessment, provenance, usage and attempts without raw state
  or SDK objects. No authored text skips; successful experimental candidates
  suggest review, independently of any confidence cutoff. Failures have no answers.
- CLI live provider selection is explicit opt-in; stderr disclosure, stdout JSON.
  Dry-run remains keyless, offline and SDK-free. `--show-state` requires dry-run.
- The original `typesafe-contracts` CI used an exact SDK overlay and exercised
  it against fake HTTP. The #163 continuation below replaces that overlay.

`f6a6714` distinguishes SDK operation timeouts from total-budget expiry and adds
installed/configured-only doctor reporting with five tests. The SDK timeout also
inherits built-in TimeoutError, so exception-handler ordering is significant.

The original foundation used an exact SDK overlay. The #163 continuation adds
the optional `typesafe = ["typesafe-sdk==0.7.0"]` extra and regenerates `uv.lock`.
The checkout recipe is now `uv run --locked --extra typesafe dead-letter analyze
... --provider typesafe`. Base/development installs do not select the extra.
This remains unreleased; the published 0.4.0 package has no TypeSafe extra.

**Not implemented:** atomic sidecars, resume, directory processing, batch
scheduling or empirical inference evaluation. Core conversion, bundles, existing
four MCP tools, UI and release versions are unchanged by this continuation.

### SDK pin decision — September 23, 2026

[Upstream 0.7.1](https://github.com/typesafe-ai/typesafe-sdk-python/compare/v0.7.0...v0.7.1)
adds API-key validation and exception redaction. Packaging retains the adapter's
tested 0.7.0 pin; dead-letter already validates keys before SDK use and suppresses
SDK/HTTP logs within provider calls. This is a bounded packaging decision, not a
claim that 0.7.0 is the latest SDK. A pin upgrade must rerun the adapter's wire,
logging, retry and response contracts. The
[0.7.0 metadata](https://pypi.org/pypi/typesafe-sdk/0.7.0/json) remains available
and its wheel and sdist are not yanked. No live inference was used for this check.

## Next work, in implementation order

1. Add atomic, no-clobber sidecars and validated-result-only reuse (#164). Bind source,
   effective inputs, schema/profile/normalizer/model/endpoint to the saved artifact.
   Keep successful results separate from attempts. Reject corrupted, incomplete,
   failed, mismatched or stale-alias cached artifacts. Expose age/returned model;
   do not call an alias cache entry fresh inference. No raw SDK/body dumps.
2. Add directory output and bounded worker scheduling (#165), safe collision handling,
   auth fail-fast, cancellation and partial-success persistence. The adapter
   already owns retries: do not introduce a second retry loop in batch code.
   External cancellation currently propagates and cleans up the current client;
   persisting completed work/attempts across interruption is still batch work.
3. Review/expand the synthetic seed with authorized data (#166); create a family-separated
   held-out set. Compare separate Nouls and response-expectation Choice, context
   and cleanup variants and a simple baseline. Optional live runs require separate
   authorization, never an incidental CI/install/doctor request. Measure per-label
   quality, calibration, attribution, abstention, observed usage and timing before
   freezing profile semantics. No invented universal thresholds or priority scores.

## Engineering boundaries

The source snapshot records immutable bytes read, not a filesystem transaction
against a concurrent writer. Pre-render zones, original dates and unknown context
must survive future MBOX integration. No inferred current-task status from an old
request. No body/attachment text in sidecars by default; local result identities
and source basenames are still sensitive.

The transport uses the actual public SDK client and custom response-model hooks,
not a copied SDK or guessed REST request. Pin upgrades deliberately: rerun wire,
logging, retry and schema tests. Request/response body logging is contained at
known source loggers, but a hostile application can still inspect memory or
remove instrumentation. Do not claim a universal sandbox. Custom test transports
and disclosure callbacks are trusted Python seams, never profile/email inputs.

Keep raw provider error bodies out of normal errors/logs. `billing_status: unknown`
means attempts may have been billed even when no validated response was returned.
Usage currently reports only fields supplied by the successful response, not
unknown failed-attempt usage. A future cost report must disclose this coverage.

The named `experimental_review-v1` policy marks successful candidate answers for
review without pretending probabilities are calibrated. The Choice formulation
can explicitly select insufficient_context. Missing coverage alone does not
become a negative result, and no classification authorizes mailbox actions.

## Verification record

### #163 packaging continuation — September 24, 2026

Local checks passed on Python 3.12:

- Full source checks: 594 core, 386 backend, 415 plugin and 87 frontend tests;
  156 subtests. The optional SDK suite was skipped in the base environment.
- The locked-extra source suite and each isolated TypeSafe wheel/sdist install
  passed 192 fake-HTTP contract tests. No tests were skipped in those SDK runs.
- Independent review found that the UI launcher's `--all-extras` would install
  the SDK and that the base install probe lacked an explicit missing-SDK failure
  check. Both were corrected. The launcher now selects `--extra dev`; the core
  probe checks the installed CLI's safe rejection before source reads or network
  access. The repair passed 55 focused tests and 15 subtests.
- Rebuilt final wheel/sdist metadata, all eight isolated install profiles and
  strict README rendering passed. The plugin schema and README validators needed
  scoped retries outside sandbox cache restrictions; their initial runner exits
  were failures, not clean full-run passes. Agent Skill dry run and release
  metadata checks passed. No existing locked package version changed.

The full source results preceded the bounded launcher/probe repair; focused
repair checks and final artifact checks cover that delta. This is local evidence,
not a new CI run, release, live-provider test or profile-quality evaluation.
The SDK-free development environment was restored. Sidecars, resume, batches and
reviewed held-out evaluation remain pending.

### Original foundation evidence

Earlier EML implementation: CI run
[35387757900](https://github.com/BigCactusLabs/dead-letter/actions/runs/35387757900)
passed 316 core, 354 backend, 92 plugin and 87 frontend tests (849 total), plus
plugin/skill/syntax validation, docs links and Windows/macOS/Ubuntu bundle tests.
The subsequent docs-only `7ea9ab4` full rerun also passed.

Initial SDK integration run
[35416008774](https://github.com/BigCactusLabs/dead-letter/actions/runs/35416008774)
installed actual typesafe-sdk 0.7.0 and ran 192 focused tests: 191 passed and one
caught the operation-timeout/total-budget error-labeling bug. `f6a6714` fixes that
production exception ordering without weakening the failing assertion. Final
head-specific CI results are recorded on PR #117; do not infer a pass from this
historical initial result.

The original working container could not install the repository/SDK dependency
graph, so its local checks were Python compilation, not a substituted parser/SDK
test harness. Actual EML and SDK tests ran in GitHub CI. Every request fixture and
credential was synthetic, transport was fake HTTP, and no private mailbox or real
API key was used.
No live quality, calibration, cost, provider-latency or stable-profile claim.
