# Synthetic analysis evaluation

Status: offline corpus and harness implemented; **reviewed held-out quality
evidence remains pending**. All 192 messages and their labels are model-authored,
public synthetic material. Every label is a `model_prelabel` with
`human_review_status: unreviewed`. They are not gold labels. The baseline, scorer,
identity staging helper, review-artifact workflow and repeat-run comparator run
locally without credentials.
Issues [#196](https://github.com/BigCactusLabs/dead-letter/issues/196) and
[#197](https://github.com/BigCactusLabs/dead-letter/issues/197) record a September
28 live run against those pre-labels. Its result trees are not bundled here;
that historical report does not establish human agreement or reviewed quality.

See the [evaluation design](../../docs/project/2026-09-28-issue-166-evaluation-design.md)
for the frozen split and planned measurement policy.

## Offline corpus checks

Run from the repository root:

```bash
uv run --locked --extra dev --extra typesafe python -m benchmarks.analysis_eval.check_split
uv run --locked --extra dev --extra typesafe pytest -q tests/backend/test_analysis_eval.py tests/backend/test_analysis_contracts.py
uv run --locked --extra dev --extra typesafe dead-letter analyze benchmarks/analysis_eval/corpus/seed-reply-positive.eml --provider typesafe --profile triage-v1 --model jev-1.13.0 --dry-run --show-state
```

The generator rewrites corpus files and model pre-labels in this directory. MIME
boundaries are deterministic. Human annotations belong in a separate review
artifact described below. Regenerating different source bytes or pre-label content makes
existing review bindings stale; the importer rejects them rather than silently
retargeting judgments. The generator does not remove stale files; the checker
fails if an extra `.eml` exists.

```bash
uv run --locked --extra dev --extra typesafe python -m benchmarks.analysis_eval.generate
```

The JSON Schema is `labels.schema.json`. Schema validation uses the already locked
`jsonschema` dependency; the split checker uses only the standard library.
All 22 original seed cases stay in dev. Paired additions keep the same family,
thread root and mailbox. Dates are fabricated and do not describe real traffic.

## Review annotations without replacing pre-labels (#166)

Create an editable review revision in a new file:

```bash
uv run --locked --extra dev python -m benchmarks.analysis_eval.reviewed_labels template \
  --revision review-v1 --out /tmp/triage-review-v1.json
```

The template copies all 192 draft label sets from `labels.json`. Every row starts
`pending`; reviewer names, review time and adjudicator stay empty. Creating the
file does not claim that anyone has reviewed it. The original model pre-labels,
their schema and corpus remain unchanged. The output uses
`reviewed-labels.schema.json` and refuses to overwrite an existing file.

Each row binds the original case ID, immutable metadata (including split,
thread/template/mailbox, identity scope and source path) and exact source SHA256.
The artifact also binds the canonical original-label document hash. Leave those
bindings unchanged. Editing source bytes, changing a split/identity/family,
using another pre-label revision, or adding/removing/duplicating a case causes
validation to fail. Keep unfinished rows as `pending`.

A reviewer edits only `labels`, `ambiguity_notes` and `review`. Preserve `null`
for unknown binary/urgency judgments; `insufficient_context` requires both
reply/action labels to be null. Provide a nonblank per-question ambiguity note
for every unknown judgment. Notes can also record uncertainty around a
determinate judgment. Urgency is an integer 0–2 or null, never a Boolean.

Once a case has actually been reviewed, set `review.status` to `reviewed`, enter
at least one nonblank reviewer identifier and a timestamp such as
`2026-10-27T12:00:00Z` in `reviewed_at` (seconds and timezone required; fractional
seconds and a numeric timezone offset are accepted). Retain any useful review
note and leave `adjudicator` null.
For an adjudicated case, use `adjudicated`, at least two distinct reviewer
identifiers, an adjudicator identifier, a timestamp and a nonblank decision note.
These fields record entered review provenance; the tool cannot authenticate the
reviewers or independently establish annotation quality. It fills none of them.

```bash
# Structural/provenance check; pending rows are valid drafts.
uv run --locked --extra dev python -m benchmarks.analysis_eval.reviewed_labels validate \
  /tmp/triage-review-v1.json

# Also check that review coverage is complete for scoring the test split.
uv run --locked --extra dev python -m benchmarks.analysis_eval.reviewed_labels validate \
  /tmp/triage-review-v1.json --split test
```

Scoring requires every case in the requested split **and every clean dev case**
to be reviewed or adjudicated, even if some cases have no successful predictions.
For this corpus, `--split dev` requires 72 reviewed cases; `--split test` requires
120 test cases plus 37 clean dev cases. Other rows may remain pending. Pending
rows have no usable truth in the imported projection, and missing review never
falls back to a model pre-label. The same checks apply to programmatic
`apply_review(..., split=...)` and `score.evaluate(..., reviewed_labels=...)`.

Select the review file explicitly when scoring saved results or a local baseline:

```bash
uv run --locked --extra dev python -m benchmarks.analysis_eval.score \
  --reviewed-labels /tmp/triage-review-v1.json \
  --baseline /tmp/triage-eval/baseline.json --split test \
  --out /tmp/triage-eval/reviewed-test-report
```

Add the usual `--run PROFILE=DIR` arguments to score captured provider results.
The `--labels` option still identifies the **original** pre-label file and corpus;
the review file can live anywhere. The scorer records review revision/hash,
original-label hash, reviewer/adjudicator records and exact coverage in
`metrics.json`, and labels the Markdown report accordingly. Omitting
`--reviewed-labels` retains the existing model-prelabel mode and wording.
Threshold/review-band fitting still uses clean dev cases only. Test labels never
enter fitting; full required review coverage does not invent missing predictions.

This workflow prepares the annotation handoff. No human annotations or new live
provider results are supplied by this implementation.

## Evidence limits

Both profiles receive `prepare_eml` state, using three context segments and the
case's `focus_identity` aliases. Quoted-body context is exposed; attachment text
is not. The attachment injection cases therefore test an **unexposed control**,
not model resistance to an attachment payload. Transcribed-attachment pairs
compare quoted in-body instructions with attachment-only instructions. They do
not test attachment extraction. Missing context has two strata: relevant missing
evidence (null reply/action labels), and missing detail that does not prevent
recognizing an explicit work request.

## Offline harness

All commands below start from the repository root. Use new output paths: staging,
baseline output and report directories refuse to replace prior artifacts.

```bash
uv run --locked --extra dev --extra typesafe python -m benchmarks.analysis_eval.stage --split dev --limit 10 --out /tmp/triage-eval/pilot
uv run --locked --extra dev --extra typesafe python -m benchmarks.analysis_eval.baseline --out /tmp/triage-eval/baseline.json
uv run --locked --extra dev --extra typesafe python -m benchmarks.analysis_eval.score --baseline /tmp/triage-eval/baseline.json --split dev --out /tmp/triage-eval/offline-baseline
```

Staging copies bytes and emits `manifest.json`: `groups` maps deterministic
identity-directory names to alias arrays; `cases` maps each case ID to its staged
path, source hash, split and arm. Selection is sorted by case ID, then limited.
Use `--arm clean` (repeatable) or `--split dev|test` to select a stratum; omit both
to stage the full corpus. A limited pilot need not contain every clean pair, and
the scorer reports dropped pairs. An empty alias array means recipient-group scope.

### Compare already captured repeat runs (#196)

Use the same staged selection, profile, endpoint, model and identity aliases for
each independently executed run. Give every run a fresh output tree: analysis
sidecars are reusable caches, so rerunning against the same output path does not
establish run-to-run stability. Obtaining fresh provider responses remains a
separate, explicitly authorized live run. This command only reads saved artifacts:

```bash
uv run --locked --extra dev python -m benchmarks.analysis_eval.stability \
  --profile triage-v1 --split dev --arm clean --limit 10 \
  --run first=/tmp/triage-eval/repeat-01/triage-v1 \
  --run repeat=/tmp/triage-eval/repeat-02/triage-v1 \
  --out /tmp/triage-eval/stability-report
```

Repeat with `--profile triage-choice-v1` and its corresponding run trees. The
selection uses the staging helper's sorted case IDs and filters. Apply identical
filters when staging and comparing. Omitting `--limit` compares the full selected
stratum and counts absent cases. Additional `--run NAME=DIR` arguments each compare
with the first named run; runs are never pooled. Names and trees must be distinct,
and trees cannot overlap. Distinct runs with identical saved answers are accepted,
but artifacts alone cannot prove that independent provider calls occurred.

`metrics.json` and `report.md` include selected/matched counts, excluded case IDs,
per-question label flip rates and probability drift. Source/state/profile hashes
and the pinned requested model reuse the quality scorer's checks. Matching
provider, endpoint, adapter/SDK, normalization/state-builder versions, request
fingerprint and known returned-model identity are required for each compared
pair. Missing/failed/malformed results and incompatible provenance never count
as agreement; zero matched cases produces no estimate. Duplicate or unknown
sidecars fail explicitly. Reports refuse to overwrite an existing directory or
write inside an input run tree.

Stability uses fixed diagnostic decisions: Noul probability at least 0.5,
provider-returned Choice (including `insufficient_context`), and the highest
probability Score level with lower-level tie breaking. A native tied Choice can
change its returned label even when its probability vector is unchanged; an
inconsistent Choice is excluded. Probability drift is absolute Noul change or
half the L1 distance between reported Choice/Score distributions. Rounded vectors
are not renormalized. Score's native numeric change is reported separately.
Native confidence is unused. No thresholds are fitted and no label truth enters
these metrics; the label-file hash and review-status counts identify the selected
corpus revision without turning stability into a correctness claim.

This is the offline first slice of #196. Wording/option-order variants,
probability remapping and log-loss remain separate work. It does not claim live
Jev stability or modify shipped profiles.

### Deterministic baseline

The baseline uses the exact normalized authored evidence, including signature
candidates exposed by `prepare_eml`, but deliberately excludes quoted/forwarded
context. Its English lexical rules are versioned as `authored-rules-v1`. They
recognize reply phrases/question marks, a fixed imperative-verb list, date bounds,
commitments, explicit negatives and urgency phrases. Focus aliases in headers/body
provide a simple scope guard; this is not a recipient-ownership resolver. No rule
for a question means abstain. Rule outputs are categorical, not calibrated
probabilities; the scorer does not invent Brier/ECE values for them. English-only
rules are a stated limitation for the multilingual arm.

## Phase 2: lead-run pilot and full inference

These commands use directory analysis from #165 (released in 0.4.9).
The lead owns paid inference. Human review and adjudication must precede a held-out
quality claim. Keep the original model pre-labels and the reviewed annotation
revision distinguishable. The phase-1 tests establish structure, not label truth.

First run the single-file dry-run command above for **both** profile names.
For focused cases add every alias as a repeated `--identity ALIAS`. Inspect
normalized authored/context segments locally; no key is required for dry-run.

Start with the staged ten-item dev pilot. Do not run the corpus with one global
identity. The following lead-run example invokes each
profile once per identity group, pins `jev-1.13.0`, and preserves the mirrored
output tree and stdout summaries. It makes live requests and needs the lead's
configured key. Do not execute it during phase 1.

```bash
export EVAL_STAGE=/tmp/triage-eval/pilot
export EVAL_OUT=/tmp/triage-eval/pilot-runs
export EVAL_JOBS=1
uv run --locked --extra dev --extra typesafe python - <<'PYTHON'
import json
import os
import subprocess
from pathlib import Path

stage = Path(os.environ["EVAL_STAGE"])
out = Path(os.environ["EVAL_OUT"])
manifest = json.loads((stage / "manifest.json").read_text())
out.mkdir(parents=True, exist_ok=False)
(out / "source-commit.txt").write_text(
    subprocess.check_output(["git", "rev-parse", "HEAD"], text=True)
)
for profile in ("triage-v1", "triage-choice-v1"):
    for group, aliases in sorted(manifest["groups"].items()):
        target = out / profile / group
        target.mkdir(parents=True)
        command = ["dead-letter", "analyze", str(stage / group),
                   "--provider", "typesafe", "--profile", profile,
                   "--model", "jev-1.13.0", "--output-dir", str(target),
                   "--jobs", os.environ["EVAL_JOBS"]]
        for alias in aliases:
            command += ["--identity", alias]
        with (out / f"{profile}-{group}.summary.json").open("x") as stdout, \
             (out / f"{profile}-{group}.stderr.log").open("x") as stderr:
            completed = subprocess.run(command, stdout=stdout, stderr=stderr)
        (out / f"{profile}-{group}.exit").write_text(str(completed.returncode) + "\n")
        if completed.returncode:
            raise SystemExit(completed.returncode)
PYTHON
```

Inspect pilot errors, usage, identity/profile hashes and returned-model IDs before
choosing full-run concurrency. Score pilot results with `--split dev`; missing
non-pilot cases are explicitly counted. If no successful clean dev observations
exist for a Noul, fitting fails instead of inventing a threshold.

After review, stage all cases in a **new** directory, set `EVAL_STAGE` and
`EVAL_OUT` to the new full-run paths, and use the same loop. Include dev and test
in the full run: the scorer needs successful clean dev predictions for fitting.
Never mix pilot and full results under one profile tree; duplicate case matches
are rejected. Keep sidecars and `.analysis.json.attempts` directories.

```bash
uv run --locked --extra dev --extra typesafe python -m benchmarks.analysis_eval.stage --out /tmp/triage-eval/full
# After the lead runs both profiles on this full staging tree:
uv run --locked --extra dev --extra typesafe python -m benchmarks.analysis_eval.score --labels benchmarks/analysis_eval/labels.json --run triage-v1=/tmp/triage-eval/full-runs/triage-v1 --run triage-choice-v1=/tmp/triage-eval/full-runs/triage-choice-v1 --baseline /tmp/triage-eval/baseline.json --split test --out /tmp/triage-eval/test-report
```

## Reading the report

`metrics.json` contains every metric and denominator; `report.md` links it and
summarizes clean error, calibration/review metrics, paired comparisons, diagnostics,
and dev fitting. Reports compare with model pre-labels by default, or the explicitly
selected reviewed revision when `--reviewed-labels` is supplied. Neither mode
automatically establishes general quality. Test labels do not enter fitting. Each Noul threshold
uses only successful clean dev observations; thresholds, dev IDs, input digests
and the objective are frozen in the report. See the design for the exact grid,
tie-breaks and review-band policy. No profile or production threshold is modified.

The loader accepts per-identity output subtrees directly, matching the globally
unique case basename (`CASE.analysis.json` or `CASE.eml.analysis.json`). No manual
flattening or manifest argument is needed. It checks source, state and profile
hashes and the requested model. Invalid successes are counted as malformed;
missing/skipped/failed cases are excluded from accuracy and counted. Attempt
records are counted separately and never replace a success. Unknown cases,
duplicate matches and wrong profile runs fail explicitly.

Clean cases alone form headline accuracy. Missing-context, long-thread and
adversarial arms are separate. Attachment injections are reported as unexposed
controls and excluded from the exposed flip headline. Null binary labels are
excluded, not treated as negatives. CC false-positive rates use only actual
negative labels in the frozen CC stratum; corrected positive and unknown review
labels are excluded with visible counts. Returned Choice supplies categorical decisions;
its distribution supplies marginals without removing insufficient-context mass.
Argmax disagreements (including deterministic alphabetical tie-breaks) are counted.
Four-way abstentions count as errors against determinate labels; five-way Choice
Brier also includes insufficient-context truth. The two Nouls do not establish a
joint probability, so no four-way Noul Brier is fabricated.

ECE has ten equal-width bins. Multiclass Brier is the unnormalized sum over classes;
top-label calibration uses argmax probability/correctness. Native `confidence` is
never interpreted as correctness probability. SmoothECE imports `relplot` lazily
and says `not computed` if absent; numpy is not in the locked environment. Optional:

```bash
uv run --locked --extra dev --extra typesafe --with relplot python -m benchmarks.analysis_eval.score --run triage-v1=RUN1 --run triage-choice-v1=RUN2 --baseline BASELINE.json --split test --out NEW_REPORT_DIR
```

This overlay does not add a locked dependency. The optional API is
[`relplot.smECE`](https://pypi.org/project/relplot/). Optional plots and SmoothECE
bootstrap confidence bands are not produced by this harness; the reported paired
metric intervals use the standard-library thread bootstrap.

Risk/coverage ranks accepted cases by decision probability, with case-ID ties.
Noul four-way ranking uses the minimum probability of its two thresholded binary
decisions, not an invented joint probability. A dev-fitted review band is separate
from the forced four-way confusion matrix; if no band qualifies, all Noul items
are routed to review. Choice insufficient-context and baseline no-rule results
also consume review. AURC/AUGRC are right-endpoint discrete integrals over only
attainable coverage; compare maximum coverage and denominators too. Review targets
accept `floor(n * (1 - budget))` items, capped by non-abstentions; actual review can
exceed the target through rounding or mandatory abstentions. Empty acceptance has
no risk estimate. Per-item attempt milliseconds exclude retry sleep and local
processing. Response usage is not proof of total billed usage, especially for
failed retries; missing usage/model fields remain unknown.
