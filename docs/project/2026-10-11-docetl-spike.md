# DocETL + dead-letter research spike

Date: 2026-10-11. Status: research and offline compatibility probe complete;
semantic quality, paid inference cost, and production integration untested.

## Decision

**Proceed with a small, separate archive-analysis experiment. Keep DocETL outside
the conversion runtime and retain TypeSafe/JEV for the existing triage path.**

The useful product is a source-linked ledger of decisions and commitments from
an email archive, followed by project/account briefs. dead-letter supplies
normalized email evidence; DocETL can extract variable-length records and
aggregate across messages. This advances the archive's usefulness beyond
conversion without requiring a replacement parser, agent platform, or UI.

Do not add a `docetl` package extra yet. The tested locked stacks have an actual
MCP dependency conflict. A versioned JSON handoff between separate environments
works and is the smallest practical integration boundary.

## Evidence and versions

| Item | Inspected revision or observation |
| --- | --- |
| dead-letter | [`f86daf80f41afc904b485f66af52405fd8aafd83`](https://github.com/BigCactusLabs/dead-letter/commit/f86daf80f41afc904b485f66af52405fd8aafd83); source package version 0.4.8 |
| DocETL | [`4bb086253767e2fd5a708776f9a1db1bdf79a2e0`](https://github.com/ucbepic/docetl/commit/4bb086253767e2fd5a708776f9a1db1bdf79a2e0); latest inspected commit dated October 9 UTC |
| DocETL package metadata | Source version 0.3.0; [PyPI metadata](https://pypi.org/pypi/docetl/json) reports 0.3.0 uploaded June 17, 2026. The matching version string does **not** establish identical code. This probe tests the commit above, not the published wheel. |
| Runtime | CPython 3.12.14, Linux; each repository's own `uv.lock`, separate environments |
| Scope | Repository code/docs, open issues, primary papers, first-party email demonstration, and a no-inference compatibility run |

DocETL is maintained research software with recent commits and a concrete
email precedent: its [email archive showcase](https://www.docetl.org/showcase/epstein-email-explorer)
reports 2,322 messages, entity/topic extraction and summaries, and $8.04 in
pipeline execution cost. That is about $3.46 per thousand messages for that
historical workload, not a forecast for this integration or a quality benchmark.

The original [DocETL paper](https://arxiv.org/abs/2410.12189) evaluates task/data
decomposition on four workloads and reports 25–80% accuracy improvements over
its baselines. The newer [MOAR paper](https://arxiv.org/html/2512.02289v4)
optimizes whole pipelines for both cost and accuracy. Across six workloads it
reports 27% higher accuracy than ABACUS and matching its accuracy at roughly
55% of execution cost. These are paper-specific comparisons, not results for
dead-letter. Table 6 reports one-time MOAR searches costing $16.70–$188 and
roughly 35–140 minutes; reported execution savings exclude that search cost.
This argues for a manually authored baseline before optimization.

## What each project should own

| Layer | Existing behavior / proposed responsibility |
| --- | --- |
| Email ingestion | dead-letter owns MIME, HTML cleanup, forwarded/quoted attribution, MBOX framing, attachments, diagnostics and preservation. DocETL does not replace these. |
| Evidence export | Reuse `read_snapshot()` → `message_from_snapshot()` → `build_state()`. This bypasses provider-specific request construction while retaining authored/context segments and coverage. |
| Single-message triage | Existing TypeSafe profiles return bounded Choice/Noul/Score judgments. Leave that path intact and evaluate competing models on identical evidence. |
| Record extraction | DocETL `map` can return multiple decisions/commitments per email, with existing source/segment IDs. General LLM extraction complements the current fixed-answer profiles. |
| Collection synthesis | `unnest`, `resolve`, and `reduce` support grouping extracted records into project/account briefs. Exact grouping and joins should run in code where possible. |
| Results | Separate analysis artifacts and a run manifest; source mail and conversion Markdown remain the evidence record. |

Source: dead-letter's [snapshot](../../src/dead_letter/core/snapshot.py),
[projection](../../src/dead_letter/analysis/eml.py),
[state builder](../../src/dead_letter/analysis/state.py),
[analysis service](../../src/dead_letter/analysis/service.py), and
[experimental analysis contract](../reference/experimental-analysis.md).
The service currently explicitly selects TypeSafe; it is not a generic provider
interface into which an ETL runner can simply be registered.

The snapshot includes only a boolean for reply-header presence. It does **not**
export `Message-ID`, `In-Reply-To`, or `References`, nor reconstruct parent
messages stored elsewhere in the mailbox. Its zones represent content found
inside one EML, and zone order is not verified chronology. Cross-email thread
reconciliation therefore needs a separate explicit identity/context extension.
Do not group by subject and call the result an authoritative thread.

## Ranked product opportunities

| Opportunity | Fit | Recommendation |
| --- | --- | --- |
| Decisions and commitments ledger | High | First experiment: extract explicit decisions and promises with actor, source text, segment IDs and message-time scope. Multiple records per email are allowed. |
| Project/account brief | High | Next: aggregate validated records for an explicitly selected group, preserving contradictions and citations. |
| Archive topics / searchable facets | Good | Useful for navigation and downstream retrieval; compare against a simple one-call extraction baseline. |
| Exact deadline tracking | Conditional | Preserve the deadline phrase first; anchor relative dates to the originating message, leave ambiguous dates unresolved. |
| Current outstanding-task tracker | Later | Requires cross-message identity, chronology, completion/supersession matching and a chosen as-of time. Single-message intent is insufficient. |
| Replace JEV triage or MBOX execution | Low | No measured advantage; orchestration is not classification quality or durable mailbox ingestion. |

TypeSafe can already select pre-parsed source values, so extraction is not
categorically impossible there. The difference is convenience for open-ended,
variable-length records and multi-stage corpus processing. DocETL also does not
make a generated confidence number equivalent to JEV's native distributions.

## Integration findings that change the design

1. **The pinned dependency stacks cannot coexist.** dead-letter requires
   `mcp>=2.1,<3`; DocETL's lock selects `openai-agents==0.17.5`, whose installed
   metadata requires `mcp>=1.19.0,<2`. An actual `uv pip install --dry-run` with
   those local projects and that Agents pin fails resolution. This proves a
   conflict for the tested pins, not every possible future Agents release.
   Use separate environments/processes. DocETL's locked base install also
   installed 118 distributions versus 39 for dead-letter's base install in
   these fresh environments; neither count is a disk or latency benchmark.

2. **Bound the handoff.** At the inspected revision,
   [`DataLoader.load()`](https://github.com/ucbepic/docetl/blob/4bb086253767e2fd5a708776f9a1db1bdf79a2e0/docetl/dataset.py)
   returns lists; JSON uses `json.load` before slicing a requested limit.
   JSONL is rejected. CSV/Parquet can limit reads, but this is not a streaming
   MBOX execution engine. Export small JSON shards under row and byte limits;
   do not collect an entire Takeout into one list. A first pilot can start at
   100 messages / 5 MiB per shard, measured and adjusted rather than promised
   as a production bound. Do not arbitrarily split thread-sensitive groups.

3. **A cache is not a durable analysis ledger.** DocETL caches LLM responses and
   checkpoints. Its [cache key](https://github.com/ucbepic/docetl/blob/4bb086253767e2fd5a708776f9a1db1bdf79a2e0/docetl/operations/utils/cache.py)
   includes model, rendered messages, schema, system prompt and operation config.
   The runner-level `default_lm_api_base` is not an explicit key input in the
   inspected [call path](https://github.com/ucbepic/docetl/blob/4bb086253767e2fd5a708776f9a1db1bdf79a2e0/docetl/operations/utils/api.py).
   Use a dedicated `DOCETL_HOME_DIR` per run/endpoint policy and a manifest binding
   input hashes, normalization, scope, model/endpoint and pipeline hash. Rebuild
   Frames when input files change; `collect()` memoization does not detect edits.

4. **Preserve and validate provenance explicitly.** DocETL's
   [reduce lineage](https://ucbepic.github.io/docetl/operators/reduce/) can carry
   source fields through aggregation. It identifies contributing input rows;
   it does not prove that each generated claim is supported. Store
   `(source_id, segment_id)` per extracted claim, check ID membership and quoted
   text against the source, then evaluate semantic support. For order-sensitive
   reconciliation, sort explicitly, disable associative merging and sampling,
   and test updates/cancellations. The default associative setting is not a
   chronology policy.

5. **Treat pipeline files as executable configuration.** Jinja prompts,
   validators and `code_map` are developer-authored logic;
   [`resolve_transform`](https://github.com/ucbepic/docetl/blob/4bb086253767e2fd5a708776f9a1db1bdf79a2e0/docetl/operations/code_operations.py)
   uses Python `exec` for string code. MOAR can synthesize code. This differs
   from dead-letter's constrained profile data. Start with reviewed fixed
   operations in the separate worker, without tool-equipped agents or automatic
   optimization of arbitrary mail. Optimization should run in an isolated
   experiment workspace, with its resulting plan reviewed before reuse.

6. **Providers must be explicit.** DocETL supports LiteLLM providers and
   OpenAI-compatible local endpoints. That enables local inference experiments,
   but does not establish local-model extraction quality or universal operator
   compatibility. MOAR can infer models from available API keys; specify a
   deliberate model allowlist and rewrite model. Iteration and thread limits
   are not hard dollar caps. See [pipeline configuration](https://ucbepic.github.io/docetl/concepts/pipelines/)
   and [MOAR configuration](https://ucbepic.github.io/docetl/optimization/moar/configuration/).

DocETL's [BARGAIN cascades](https://ucbepic.github.io/docetl/optimization/cascades/)
are interesting later for expensive binary filters or pair matching. They learn
when to use a cheaper proxy versus an oracle on sampled labels. Their documented
guarantees concern the oracle-based evaluation setup, not certainty against
human ground truth; they do not cover arbitrary extraction or summarization.
Avoid a semantic pre-filter in the first extraction experiment: a missed email
cannot be recovered by a later map.

## Offline experiment performed

The [probe](../../benchmarks/docetl_spike.py) exports only repository-owned
synthetic fixtures. It blocks socket connections/DNS during its execution and
uses separate Python environments. It performs actual DocETL JSON loading and
a deterministic `code_map`, then constructs a real semantic-map runner for
syntax validation **without executing the semantic map**.

| Check | Observed result |
| --- | --- |
| Corpus | 11 benchmark EMLs + forwarded/reply, multilingual and empty fixtures |
| Export | 14 records; 520,396 source bytes → 32,072 JSON bytes |
| Evidence coverage | 8 records with context, 3 with attachments, 1 without authored text; no context segments excluded |
| Preservation | All source bytes unchanged; all 14 source IDs and nested states survived DocETL processing |
| Candidate extraction operation | Nested decision/segment schema and Jinja prompt accepted by actual runner syntax check |
| JSONL | Rejected by actual loader constructor |
| Inference / optimization | Neither executed; observed deterministic pipeline cost $0 |
| Dependency co-installation | Resolver rejected the pinned MCP 1.x/2.x combination |

The byte comparison excludes binary attachment payloads and most MIME structure;
it is **not** a token benchmark, fidelity score, or same-information compression
claim. An empty record surviving the handoff does not validate downstream
abstention. The probe's source hash IDs suffice for its unique fixture corpus;
a production manifest must separately preserve duplicate source occurrences.

Machine-readable results and source hashes are in
[the evidence record](benchmarks/2026-10-11-docetl-spike.json).

Reproduce from this dead-letter research branch, with a sibling DocETL checkout:

```bash
# dead-letter environment
uv sync --locked --no-dev
.venv/bin/python benchmarks/docetl_spike.py export /tmp/dl-docetl-spike.json

# Separate upstream checkout; do not install DocETL into dead-letter's venv.
git clone https://github.com/ucbepic/docetl.git ../docetl
git -C ../docetl checkout 4bb086253767e2fd5a708776f9a1db1bdf79a2e0
uv sync --project ../docetl --locked --no-dev
../docetl/.venv/bin/python benchmarks/docetl_spike.py check /tmp/dl-docetl-spike.json
```

Choose a fresh dataset output path on a rerun; export refuses to overwrite.
Package installation needs network access; the two probe phases do not.
Export hashes can change with Python/package normalization versions, which are
included in the state. This is a Linux compatibility check, not Windows/macOS
acceptance, provider testing, or multi-GB benchmarking.

## Smallest useful next experiment

**Question:** Does a declarative extraction pipeline produce more useful,
source-supported decisions/commitments than a simple structured LLM call, at
acceptable total cost?

Start with about 100 synthetic or explicitly authorized messages, stratified
across direct decisions, promises, negations, quoted superseded requests,
forwarded ownership, ambiguous dates, missing parents/attachments and empty
content. Review labels and preserve ambiguity. Split by thread/template family
before prompt tuning: approximately 60 development and 40 untouched test items
is a pilot, not enough to assert rare-error rates.

Compare:

- Deterministic candidate rules, scored as a recall/cost baseline.
- A direct structured-output LLM call on exactly the same evidence.
- A fixed DocETL map using the same model, prompt and output schema. This isolates
  orchestration effects; it should not get credit merely for choosing a stronger model.
- A decomposed DocETL map → validate → unnest pipeline if baseline errors justify it.
- Only afterward, MOAR on the development split; score its frozen plans on the
  untouched test split. For triage labels, include existing JEV profiles rather
  than pretending a new extraction task is a fair head-to-head comparison.

Score claim precision/recall, supported citation rate, actor attribution,
negation/supersession errors, missing-context handling, schema failures, missing
output rows, retries, elapsed time and actual provider usage. Validate every
source/segment reference. Reconcile the complete input-ID set against success,
failure and explicit skip outcomes; a dropped row is not a negative decision.
Repeat a small subset with cache bypass to expose run-to-run instability.

Proposed continuation rule: keep the simpler implementation unless DocETL
improves held-out extraction F1 by at least 5 percentage points, cuts measured
cost by at least 25% at comparable quality, or makes the required multi-stage
workflow materially simpler. These are engineering decision thresholds, not
research findings. A first user-facing ledger should target at least 95%
human-reviewed claim precision, complete referential validity and clear review
states; report uncertainty rather than calling a tiny pilot validated.

Measure total cost as execution + retries + evaluation + optimizer search.
Optimization breaks even after `search_cost / (baseline_cost_per_item -
optimized_cost_per_item)` items when the denominator is positive. For example,
a hypothetical $25 search saving $0.001 per item needs 25,000 items to recover
its cost. Small mailboxes may never justify MOAR even when DocETL's fixed
operators are useful. A proposed first paid pilot budget is $10 execution,
with a separate optional $25 optimization budget; neither was spent or enabled
by this research run.

## Implementation scope and existing work

| Follow-up slice | Scope / exit condition |
| --- | --- |
| Evidence export | Versioned bounded JSON shards plus local manifest, preserving occurrences and normalized zone provenance; source unchanged; no DocETL import in core. Rough estimate: 1–2 engineering days. |
| Fixed extraction experiment | Separate pinned environment, reviewed schema/prompt, explicit provider, full outcome accounting and reproducible scorer. Rough estimate: 2–3 engineering days plus label review. |
| Source-linked review artifact | Static table or Markdown ledger opening cited evidence; add project briefs only after claim extraction works. No new frontend required for the pilot. |
| Optimize or stop | Keep a frozen fixed pipeline if it wins; otherwise retain the direct-call baseline. Add MOAR only when evaluation and amortization justify it. |

These estimates assume existing local EMLs and exclude new thread reconstruction,
attachment OCR, production batch recovery and UI/MCP exposure.

Relevant existing work remains open:
[#165](https://github.com/BigCactusLabs/dead-letter/issues/165) owns bounded
directory analysis and interruption recovery;
[#166](https://github.com/BigCactusLabs/dead-letter/issues/166) owns reviewed
triage evaluation; [#196](https://github.com/BigCactusLabs/dead-letter/issues/196)
and [#197](https://github.com/BigCactusLabs/dead-letter/issues/197) cover stability
and competing signal formulations. Those issue records include later evaluation
context than some prose in the analysis reference. This spike does not close
them or treat agreement with unreviewed pre-labels as measured email accuracy.

No production dependency, source conversion behavior, provider execution path,
release metadata or license was changed. DocETL's MIT license and dead-letter's
existing PolyForm Noncommercial license remain as declared by their repositories.
