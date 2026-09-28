# Synthetic analysis evaluation

Status: corpus checkpoint only; **live evaluation not yet run**. All 192 messages
and their labels are model-authored, public synthetic material. Every label is a
`model_prelabel` with `human_review_status: unreviewed`. They are not gold labels.
The baseline, scorer, phase-2 staging helper and metric tests remain to be built.
Do not treat this checkpoint as a complete evaluation harness.

See the [evaluation design](../../docs/project/2026-09-28-issue-166-evaluation-design.md)
for the frozen split and planned measurement policy.

## Offline corpus checks

Run from the repository root:

```bash
uv run --locked --extra dev --extra typesafe python -m benchmarks.analysis_eval.check_split
uv run --locked --extra dev --extra typesafe pytest -q tests/backend/test_analysis_eval.py tests/backend/test_analysis_contracts.py
uv run --locked --extra dev --extra typesafe dead-letter analyze benchmarks/analysis_eval/corpus/seed-reply-positive.eml --provider typesafe --profile triage-v1 --model jev-1.13.0 --dry-run --show-state
```

The generator rewrites only corpus files and labels in this directory. MIME
boundaries are deterministic. Regenerate only before labels have been reviewed;
regeneration overwrites those labels. The generator does not remove stale files;
the checker fails if an extra `.eml` exists.

```bash
uv run --locked --extra dev --extra typesafe python -m benchmarks.analysis_eval.generate
```

The JSON Schema is `labels.schema.json`. Schema validation uses the already locked
`jsonschema` dependency in tests; the split checker uses only the standard library.
All 22 original seed cases stay in dev. Paired additions keep the same family,
thread root and mailbox. Dates are fabricated and do not describe real traffic.

## Evidence limits

Both profiles receive `prepare_eml` state, using three context segments and the
case's `focus_identity` aliases. Quoted-body context is exposed; attachment text
is not. The attachment injection cases therefore test an **unexposed control**,
not model resistance to an attachment payload. Transcribed-attachment pairs
compare quoted in-body instructions with attachment-only instructions. They do
not test attachment extraction. Missing context has two strata: relevant missing
evidence (null reply/action labels), and missing detail that does not prevent
recognizing an explicit work request.

## Phase 2 handoff (commands not yet executable end to end)

The lead owns live paid inference. Human review must precede a held-out quality
claim. First run dry-run checks for both profiles with the recorded identities.
Then stage about ten **dev** cases, grouped by identical `focus_identity`, and run
a pilot. Directory mode and `--output-dir`/`--jobs` come from **#165**, which is not
part of this branch. After #165 is integrated, the planned directory invocation is:

```text
dead-letter analyze <staged-identity-group> --provider typesafe --profile triage-v1 --model jev-1.13.0 --output-dir <run-dir> --jobs N [--identity ALIAS ...]
dead-letter analyze <staged-identity-group> --provider typesafe --profile triage-choice-v1 --model jev-1.13.0 --output-dir <other-run-dir> --jobs N [--identity ALIAS ...]
```

Do not run the entire corpus with one identity: the seed cases include recipient
scope and named-person scope. Keep case basenames, reconstruct mirrored output
paths, preserve sidecars and attempts, and record both profile hashes and the
source commit. Inspect the pilot's usage, failures and observed model IDs before
the lead chooses full-run concurrency. Always request `jev-1.13.0`.

The continuation must implement and verify the scorer command below before use:

```text
python -m benchmarks.analysis_eval.score --labels benchmarks/analysis_eval/labels.json --run triage-v1=RUN1 --run triage-choice-v1=RUN2 --baseline BASELINE.json --split test --out REPORT_DIR
```

The scorer must work without optional packages. SmoothECE must import `relplot`
lazily and report `not computed` when it is unavailable. Once implemented, use
`uv run --with relplot python -m benchmarks.analysis_eval.score ...` to enable it;
this must not change the committed lockfile. `numpy` is not in this lockfile.
