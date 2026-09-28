# Issue #166: synthetic profile evaluation design

Design date: September 28, 2026. **Not yet run.** Corpus milestone complete;
baseline, scorer and metric regression tests are pending. This document freezes
the intended protocol before inference. It contains no model-quality results.
The [corpus guide](../../benchmarks/analysis_eval/README.md) records current commands.
Directory execution belongs to #165; this work does not implement it.

## Scope and review gate

Compare `triage-v1`, `triage-choice-v1` and a deterministic rules baseline on
identical normalized evidence. Pin the requested model to `jev-1.13.0`, preserve
observed returned-model IDs, profile revisions/hashes and source commit. Run no
inference in phase 1. The lead runs a roughly ten-item dev pilot before any full
paid phase-2 run. Test labels must never enter threshold fitting.

All labels are model pre-labels, not human judgments. Human reviewers must review
and adjudicate labels before any held-out quality claim. Preserve unknown labels
as null, not false. Human agreement statistics are not available. Double annotation
and per-question agreement remain later review work, not claims of this corpus.

## Corpus and frozen split

192 synthetic messages: 72 dev (37.5%) and 120 test (62.5%). There are 43 template
families and thread groups. All 22 pre-existing seed scenarios stay in dev, with
two clean controls added for seed pairs. Eight new families are dev; twenty are
test. Each new family includes a direct message, context-supplied follow-up,
context-omitted follow-up, two injection locations and a long-thread variant.
Families describe separate fictional work settings and have distinct authored
wording. Pair variants deliberately share their family. Shared generation style
is not evidence of independent natural-mail diversity.

| Split | Clean | Missing context | Adversarial | Long thread |
| --- | ---: | ---: | ---: | ---: |
| Dev | 37 | 10 | 17 | 8 |
| Test | 40 | 20 | 40 | 20 |

Dev dates are in 2001; test dates are in June–July 2002. The split checker requires
`max(dev.sent_at) < min(test.sent_at)` and prevents any thread root, template family
or mailbox crossing the boundary. The two mailboxes are synthetic storage cohorts,
not identities inferred from headers. Pairs must refer to a clean case in the
same thread, family, mailbox and split. Assignment is explicit and reproducible,
not an individual-message random split. The time holdout is fabricated: it does
not measure actual temporal drift.

The corpus covers sender commitments, bounded actions, explicit and implied
requests, named recipients, CC-only informational copies, negations, quoted
history, forwards, plain text, multipart HTML, Spanish, French and German.
Direct messages oversample requests and commitments intentionally. Report counts
and unweighted within-stratum rates; do not claim production prevalence weights.

## Arms and effective evidence

The five injection locations are authored text, quoted text, forwarded text,
display name/subject and text attachment. Payloads are original label-steering
text inspired by the trust-boundary pattern in LLMail-Inject, not copied dataset
rows. They target a label different from the clean expectation. Keep adversarial
items out of headline accuracy. Compare each to its clean partner, by location.

`prepare_eml` currently exposes attachment counts but never attachment contents.
Attachment payloads are unexposed controls. The two new attachment-context families
and the seed attachment pair supply an instruction through a quoted transcription
in the clean item, then omit that transcription. Do not claim that adding an
attachment makes its bytes available to either profile. No source change is needed
or authorized for this corpus milestone.

Relevant missing context gets null binary reply/action labels and the Choice
label `insufficient_context`. Missing details that do not change whether work was
requested keep a determinate label. Report the two strata separately. The long
arm adds 70 irrelevant archive entries inside quoted context while preserving the
current author's text. Tests verify substantial context survives normalization
and that both profiles remain within request byte limits. Use the default three
context segments, not a hidden special long-thread configuration.

## Frozen scoring policy for the continuation

The scorer consumes successful sidecar envelopes only (`execution_status` is
`succeeded`), but counts every missing, skipped, failed and malformed item. Match
by preserved corpus path/basename and reject duplicate case matches. Validate the
profile identity; do not silently pool runs. Capture usage and attempt durations
only when present; missing usage remains unknown, never zero inferred billing.

For each binary question fit a Noul threshold using **dev clean determinate**
items only. Candidate thresholds are 0.10 through 0.90 in steps of 0.05. Minimize
binary error; break ties by distance to 0.50, then lower threshold. If no eligible
dev labels/predictions exist, fail threshold fitting explicitly. Freeze fitted
thresholds, eligible dev IDs, their digest and objective into each report. Test
scoring must not refit, and a test-label mutation regression must prove this.

Map reply/action booleans to `none`, `reply_only`, `non_reply_action_only`, `both`.
Choice marginal probabilities are P(reply_only)+P(both) and
P(non_reply_action_only)+P(both), without renormalizing away insufficient-context
mass. Derive supported Choice keys from the runtime profile and fail on drift.
Compute argmax from distributions and count disagreement with returned `choice`.
The runtime rejects substantive mismatches; offline foreign/fake results still
need a diagnostic. Do not interpret the native `confidence` statistic as P(correct).

Use the returned Choice for categorical decisions; report argmax disagreement
separately. Shared urgency uses argmax over its three level probabilities.
Report per-question confusion and errors, four-way confusion plus an explicit
abstention column, per-label denominators and Wilson 95% intervals. Exclude null
labels from determinate accuracy/calibration with an explicit excluded count.
Headline accuracy uses clean cases only; all other arms remain separate strata.

Report binary Brier for Nouls and marginalized Choice; unnormalized multiclass
Brier for Choice/urgency; top-label calibration based on maximum probability;
ten equal-width ECE bins on [0,1], with the last bin including 1. SmoothECE is
optional through a lazy `relplot` import, reported as `not computed` when absent.
No new locked dependency. The optional invocation is `uv run --with relplot ...`.

Pair the two profiles on intersecting successful determinate items. Report
coverage and dropped pairs. Bootstrap thread roots, preserving all items and
pairs within each sampled root; seed 166, 2,000 replicates, percentile 95% bounds.
Apply this paired procedure to correctness differences and adversarial flips.
Degenerate or empty samples must report their limits rather than fabricate bounds.

Risk/coverage ranks by decision probability, not vendor confidence, with case ID
as a deterministic tie-break. Report all curve points, AURC, AUGRC and selective
error at 10% and 20% review budgets, counting explicit abstentions as review.
For Nouls select an abstention band on dev clean cases: half-width candidates
0, .05, .10, .15, .20, .25 around each fitted threshold, clipped to [0,1]. Choose
maximum coverage meeting <=10% observed selective error; if none qualifies,
report no qualifying band. Do not describe this small-dev policy as calibrated.

Report insufficient-context precision against the ambiguity annotation and
separately against the missing-context arm. Report CC'd-but-not-asked false
positives; missing-context paired flips and abstention changes; input tokens per
item and latency percentiles from real attempt fields; observed returned-model
counts. Failure/unknown counts must accompany all summaries.

The rules baseline uses authored normalized segments only, excluding quotes and
forwards, with question-mark, imperative, please, let-me-know, date-bound and
commitment patterns. No fired rule means abstain; it must not infer an assessed
negative from absent evidence. Record its rule version and use the same scorer.

## Precision limits and sample planning

This table is an illustrative Wilson 95% calculation at 90% observed success,
not a result or a power guarantee. It shows the interval width at rare-label
sample sizes; correlated families can make uncertainty larger still.

| Positive examples | Illustrative correct | Wilson 95% interval |
| ---: | ---: | --- |
| 10 | 9 | 59.6%–98.2% |
| 20 | 18 | 69.9%–97.2% |
| 30 | 27 | 74.4%–96.5% |
| 50 | 45 | 78.6%–95.7% |
| 100 | 90 | 82.6%–94.5% |

Only twenty test thread groups exist; clean per-label denominators are much
smaller than 120. Rare-label and CC-subgroup claims must show their denominator
and wide interval. The single OpenAI generator family, synthetic wording,
pre-label bias and manually selected families limit external validity. There is
no natural-inbox representativeness, human agreement statistic, real model
accuracy, calibration, cost or latency result in this document.
