# Open-issue development handoff

Reviewed October 27, 2026 against
[`7d68c5d`](https://github.com/BigCactusLabs/dead-letter/tree/7d68c5de9e48341e07d74852df550e07c9bb978c),
all 25 open issues, their relevant follow-up records, and the existing PRs.
This is a dated preparation checkpoint. The linked issues own execution;
current runtime contracts and evidence stay in their existing reference guides.
Historical client/catalog observations retain their original dates: this pass
did not repeat third-party submissions, desktop installs or live inference.

## What this preparation adds

- An [offline repeat-run comparator](../../benchmarks/analysis_eval/README.md#compare-already-captured-repeat-runs-196)
  for #196, using the existing validated sidecar loader and fixed selections.
  It reports per-question flips, probability drift, exclusions and denominators
  without fitting thresholds or making provider calls.
- Real-stdio #145 acceptance tests for the actual 1,000-message and 256 MiB
  MCP limits, Gmail metadata, source preservation and bounded responses.
  The [MCP contract](../reference/v4-runtime-contracts.md) now also states
  the existing OS-user path boundary.
- Corrected generated Cline submission copy: the current interface has five
  tools, including bounded `convert_mbox`. The candidate remains a candidate.
- Reconciled the [reach plan](reach.md) and the issue handoffs so completed
  recipes, importer features, analysis batching and parser fixes are not
  assigned again.

## Work that can start independently

| First slice | Existing foundation | First completion boundary |
| --- | --- | --- |
| #241: bound attribution parsing | `core/attribution.py`, existing fallback and locale tests | Malformed long attribution candidates finish within bounded work; short valid headers before large bodies still parse; body content is preserved. |
| #146: local-path MBOX jobs | `core/mbox_import.py`, `StreamingReport`, job IDs/status/polling | One flat archive streams into a job-owned destination with bounded retained state and accurate partial/cancelled outcomes. Upload and UI follow. |
| #166: reviewed annotation import | 192-case corpus, frozen split, baseline and scorer | A separate versioned review artifact validates source IDs/hashes, ambiguity and adjudication without rewriting model pre-labels or changing splits. |
| #196: compare independent repeats | New offline comparator | Read already captured, separately executed runs and publish matched-denominator results. New provider calls are separate from this offline slice. |
| #161: visual demo | Five tested recipes and their two-message synthetic inbox | A reproducible 20–30 second silent-readable capture plus still, showing actual released outputs. |

These slices do not depend on marketplace admission or on finishing the whole
MBOX or semantic-analysis parent issue.

## MBOX: reuse the importer and isolate the new surface

### #145: finish acceptance of the existing MCP tool

`backend/mcp_server.py::convert_mbox` already shipped in 0.4.5. It streams
through `core.mbox_import.convert_mbox`, writes `StreamingReport` output,
preserves source bytes, and caps admission at 256 MiB, conversion at 1,000
records and returned failures at 20. The new tests exercise those real limits
through stdio, including truncation and Gmail labels/thread ID in bundle output.

The tool has no Inbox/Cabinet or client-root allowlist; paths are constrained
by the server's OS user and, when used, container mounts. It also has no
per-message deadline or effective call cancellation. Byte/count caps do not
make execution time bounded. Keep those facts explicit when reconciling #145;
an explicit cancellation extension would need a new contract. The existing
unreadable-file case requires a non-root POSIX host for its permission check.

### #146: split local ingestion, UI and upload

1. **Local-path job and runner.** Add a dedicated MBOX request/options model
   in `backend/schemas.py` and an explicit route in `backend/api.py`. Reuse
   `JobManager` IDs, polling, terminal history and cancellation state, with a
   streaming execution branch. Use one immutable flat archive, unconditional
   source preservation, and a job-owned Cabinet destination. Resolve selected
   paths against the configured browser root; existing `_resolve_files` does
   not automatically provide that confinement.
2. **Bounded progress and UI.** Consume the core iterator under
   `contextlib.closing` and append detached results directly to `StreamingReport`.
   Keep counters and a capped error sample plus omitted count, not every result
   in `_JobRecord.report_entries` or every error in `_JobRecord.errors`. Report
   byte progress and processed-record counts without inventing a message total
   or pre-scanning the archive. Then expose selection/progress in the frontend.
3. **Upload after the runner.** `_stream_upload_to_file` already uses bounded
   chunks, but multipart parsing precedes the handler. Increasing its 100 MiB
   constant alone does not bound a multi-GB request. Select a streaming request
   path or verified multipart limits, enforce actual bytes, and define staging
   cleanup for disconnect, oversize and disk-full failures.

Do not feed archive entries through `run_bundle_conversion`, whose EML path
can move/delete sources. Keep archive-fatal events (`item.mbox is None`) separate
from per-message failures. Set completed-with-errors only for recoverable
message failures; archive/report failures must remain failures.

Decide cancellation at the first slice: a flag between yielded records only
cancels between records. Prompt cancellation needs checks during framing and
worker supervision plus worker reaping. Cancelling `asyncio.to_thread` alone
does not stop conversion. Also define concurrent-job admission before staging;
the current eight-worker setting is per EML job, not a global archive limit.

Acceptance should cover source/attachment/provenance parity, mixed success,
fatal suffixes, bounded status memory, path escape/source mutation, job limits,
and cancellation during framing, parsing and report publication. Upload tests
must exercise chunked/misdeclared request bodies, not only the copy helper.
Reuse `tests/backend/test_jobs.py`, `test_api.py`, core MBOX suites and
`tests/frontend/job.test.js`. Existing EML/watch behavior remains its own
contract. Compressed archives and web-job resume are later explicit slices.

### #138 / #140 / #188 / #220: evidence and existing work

Use the existing [real-export validation procedure](../reference/mbox-validation.md)
for #138 with an authorized local archive and independent export/attachment
counts. #140 already has Linux memory and Linux/macOS CPU/per-file output
controls. Fill the narrower child-CPU, expansion and temporary/output-disk
measurement gaps; largest-reaped-worker RSS is not simultaneous process-tree
peak, and worker lifetime is not child CPU time.

[#204](https://github.com/BigCactusLabs/dead-letter/pull/204) owns Windows
budget implementation for #188. The observed open head was `ec1e8c2`; validate
that head and its native Windows evidence rather than repeating older PR-body
results. #220 remains deliberately deferred until #138 or a user report shows
a macOS memory problem. A future soft watchdog has different semantics from
a hard memory limit.

## Analysis: separate label review, stability and profile experiments

### #166: reviewed labels are the next missing artifact

`benchmarks/analysis_eval/` already contains 192 synthetic cases: 72 dev and
120 held out, with 43 template/thread families. The old 22-case issue description
is stale. `check_split.py`, `stage.py`, `baseline.py` and `score.py` already
provide split checks, identity grouping, deterministic baseline, provenance
validation and dev-only threshold/review-band fitting.

Every current annotation is an unreviewed model pre-label. In fact,
`labels.schema.json` fixes `human_review_status` to `unreviewed`, and
`generate.py` rewrites labels. Start with a separate versioned reviewed-label
artifact/schema and an explicit loader. Preserve case/source hashes, split,
per-question ambiguity, review/adjudication status and revision identity.
Test that the loader cannot silently fall back to pre-labels or move cases
between splits. This engineering slice needs no inference.

The September 28 run described in #196/#197 is historical reported agreement
with pre-labels; its sidecar trees are not bundled in the checkout. Recover
those artifacts or capture a separately authorized new run before publishing
new metrics. Reviewed quality, coverage and calibration claims require the
selected reviewed revision, with test labels excluded from all tuning.

### #196 / #197: use one evaluation-only profile extension

The new `benchmarks.analysis_eval.stability` command implements only the
offline repeat comparison. Separate run trees, exact effective inputs and known
returned-model identity must match; failures and incompatible records are
excluded with visible denominators. Identical artifacts can be legitimate,
but files alone cannot prove independent API calls. Runtime cache reuse is
not a repeat-run experiment.

The next shared engineering slice is an **evaluation-only** profile registry
and request-preparation hook for reworded, reordered and combined-question arms.
`get_profile` and `score.load_run` currently enforce built-in profiles and exact
hashes; do not weaken those checks to accept a foreign experiment. Give each
variant a digest and an explicit question mapping. Keep production profiles
and thresholds unchanged.

After reviewed labels exist, fit remapping (#196) or a small Noul combiner
(#197) using clean dev cases only. Save parameters, objective and training IDs
as report artifacts; reject absent/degenerate fitting evidence and floor exact
zeros before log loss. Test-label changes must not affect fitted parameters.
For the combined Noul/Choice arm, define insufficient-context treatment and
report disagreement coverage and errors among agreeing cases. Measure actual
usage instead of assuming extra questions have no cost.

The implemented CLI/Python analysis runtime is already released, including
directory analysis in 0.4.9. Optional task reconciliation, MCP/UI analysis and the separate draft
[DocETL research PR #224](https://github.com/BigCactusLabs/dead-letter/pull/224)
are not prerequisites for these evaluator slices.

## Parsing: a concrete performance fix before new heuristics

#193 largely describes fixes now listed under 0.4.9 in the
[changelog](../../CHANGELOG.md): quoted/nested Apple forwards, per-forward
cleanup, legacy Gmail, no-signal wrappers, localized headers, Proton/Yahoo,
wrapped Gmail attributions and separator-only Outlook sections. The current
forwarding/wrapper/attribution suites passed 809 tests in this preparation.

#241 still has a reproducible attribution cost problem. HTML classification
caps attribution candidates at 1,000 characters, but structured
`annotate_quoted_zones` passes the whole normalized zone to
`parse_attribution_line`. A separate-process probe of
`"Am X" + " " * n + ":"` took approximately 0.002, 0.017 and 0.380 seconds
at n = 1,000, 4,000 and 16,000; n = 128,000 exceeded a three-second outer
timeout and was killed. These are diagnostic observations on one machine.

First fix boundary: bound the **leading attribution candidate** centrally in
`core/attribution.py`, preserving complete candidate lines, wrapped Gmail and
multi-line Outlook headers. Do not truncate a physical line and let its new
end become a false regex match. A short valid header before a large body must
still parse. On over-budget/unrecognized input, preserve all text through
the existing fallback and keep `consumed_end` relative to original text.

Add killable subprocess regressions for long whitespace/repeated-token cases,
limit-boundary cases, short headers with large bodies, and end-to-end sentinel
preservation. Keep wrapper classification unchanged in that first PR.

The other #193/#241 changes need paired representative fixtures: Outlook
reply/forward ambiguity, localized Outlook history headers, Gmail Japanese and
Apple Chinese evidence, unusual quoted-forward layouts, and wrapper/sanitizer
ownership. The English Outlook history guard is distinct from the now-localized
forward-header parser. Re-time #193's external `mail-parser-reply` colon-gated
slow path separately; the old 46-second observation is not a current benchmark.

## Distribution and demo: reuse one synthetic inbox

The [five recipes](../recipes/README.md) and their two-message
[`fixtures/inbox`](../recipes/fixtures/inbox) are complete under #162. For #161,
use a 25-second storyboard: input (0–5s), actual released conversion (5–10s),
Markdown/YAML (10–17s), copy-only Cabinet/decoded CSV (17–22s), diagnostics
(22–25s). Retain the capture command, package revision, output and a still;
check legibility at README width. No new corpus is necessary.

The same inbox supports #156 client installs and #157 skill selection. In a
named client/OS/version, record the link handoff, consent, saved configuration,
five tools, task-triggered skill selection, source hashes and outputs. A single
Cursor session can supply both receipts, with separate outcomes. Codex CLI/MCP
runtime already passed in the [September 24 record](2026-09-24-issue-109-discovery-evidence.md);
Copilot/Cursor remain. A subprocess smoke is not a desktop installation result.

Use the [distribution ledger](../reference/mcp-distribution.md) for catalog
state. Recheck upstream contracts only when resuming a submission; preserve
the recorded owner holds on #153 and #160. #159 already has a selected host,
`bigcactuslabs.xyz`: prepare its site-repository well-known JSON and content
type using resolved release metadata, including strict schema validation.
Deployment and endpoint retrieval remain separate outstanding work.

## Open-issue map

| Issue | Current remaining work / first action |
| --- | --- |
| [#103](https://github.com/BigCactusLabs/dead-letter/issues/103) | Parent: mark completed resume #139 accurately; track real-export evidence, Windows PR and optional web jobs. |
| [#104](https://github.com/BigCactusLabs/dead-letter/issues/104) | Parent: #148/#149 completed; keep remaining client/catalog outcomes in #150–#153/#156 and shared #160. |
| [#105](https://github.com/BigCactusLabs/dead-letter/issues/105) | Parent: #162 recipes complete; #161 visual deliverable remains. |
| [#109](https://github.com/BigCactusLabs/dead-letter/issues/109) | Parent: skill publication/install and Codex evidence exist; remaining hosts, owned-domain ARD and Finder outcome remain. |
| [#110](https://github.com/BigCactusLabs/dead-letter/issues/110) | Parent: directory execution #165 shipped; reviewed evaluation remains, with optional ideas kept separate. |
| [#138](https://github.com/BigCactusLabs/dead-letter/issues/138) | Run existing full-import audit on an authorized real export with an independent baseline. |
| [#140](https://github.com/BigCactusLabs/dead-letter/issues/140) | Measure remaining child CPU/expansion/disk behavior; resource controls already exist. |
| [#145](https://github.com/BigCactusLabs/dead-letter/issues/145) | Existing bounded MCP implementation; finish transport/permission evidence with explicit cancellation limits. |
| [#146](https://github.com/BigCactusLabs/dead-letter/issues/146) | Implement local-path streaming jobs, then UI, then bounded upload. |
| [#150](https://github.com/BigCactusLabs/dead-letter/issues/150) | Refresh complete curated-catalog/duplicate-request lookup; direct install is separate #156 evidence. |
| [#151](https://github.com/BigCactusLabs/dead-letter/issues/151) | Corrected generated candidate; README-only autonomous install and current submission-contract validation remain. |
| [#152](https://github.com/BigCactusLabs/dead-letter/issues/152) | Reproduce Smithery's recorded `server.type: uv` rejection against current released manifest before new packaging. |
| [#153](https://github.com/BigCactusLabs/dead-letter/issues/153) | Free listing copy exists; preserve owner-held submission and review current terms/contact when resumed. |
| [#156](https://github.com/BigCactusLabs/dead-letter/issues/156) | Observe VS Code/Cursor link-to-app installation with the current five-tool inventory. |
| [#157](https://github.com/BigCactusLabs/dead-letter/issues/157) | Preserve Codex pass; observe remaining Copilot/Cursor task-triggered skill use. |
| [#159](https://github.com/BigCactusLabs/dead-letter/issues/159) | Selected domain; prepare site-repo ARD file/headers and strict conformance, then verify deployment. |
| [#160](https://github.com/BigCactusLabs/dead-letter/issues/160) | Prepared skill entry; preserve owner hold, share MCP-entry tracking with #150, then test named retrieval intents. |
| [#161](https://github.com/BigCactusLabs/dead-letter/issues/161) | Produce capture/still from existing recipe fixtures and tested commands. |
| [#166](https://github.com/BigCactusLabs/dead-letter/issues/166) | Separate reviewed-annotation artifact/loader before quality claims; reuse corpus/split/scorer. |
| [#188](https://github.com/BigCactusLabs/dead-letter/issues/188) | Review existing Windows PR #204 and exact-head native evidence. |
| [#193](https://github.com/BigCactusLabs/dead-letter/issues/193) | Narrow to residual Outlook/locale/ambiguity evidence and separate reply-parser retiming. |
| [#196](https://github.com/BigCactusLabs/dead-letter/issues/196) | Offline comparator prepared; independent repeat capture, variants and dev-only remapping remain. |
| [#197](https://github.com/BigCactusLabs/dead-letter/issues/197) | Reuse evaluation-only variant support to add the combined arm; fit and assess the combiner against reviewed labels. |
| [#220](https://github.com/BigCactusLabs/dead-letter/issues/220) | Keep deferred until observed macOS memory pressure justifies a soft watchdog. |
| [#241](https://github.com/BigCactusLabs/dead-letter/issues/241) | Start with bounded attribution parsing; keep remaining wrapper semantics fixture-led. |

## Focused verification

After the normal locked development setup:

```bash
uv run --locked --no-sync pytest -q tests/backend/test_analysis_eval.py tests/backend/test_analysis_stability.py
uv run --locked --no-sync pytest -q tests/backend/test_mcp_mbox.py
uv run --locked --no-sync pytest -q tests/plugin/test_client_installs.py tests/plugin/test_cline_registration.py
python scripts/generate_client_installs.py --check
python scripts/release.py check
```

Use the existing [verification runner](../reference/verification.md) for broader
source checks. These checks do not supply human labels, real-mailbox acceptance,
native Windows/macOS evidence or GUI-client observations.
