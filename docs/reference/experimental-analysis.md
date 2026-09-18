# Experimental semantic-analysis contracts

Tracking: [issue #110](https://github.com/BigCactusLabs/dead-letter/issues/110).

**Status: offline Python foundation, not a released analysis feature.** This
change provides deterministic request preparation and native-response validation.
It does **not** parse EML for analysis, connect to TypeSafe, add an `analyze` CLI
command, write sidecars, or change conversion/MCP/UI behavior. There is no
`dead-letter[typesafe]` extra yet. No API key is needed or read by this layer.

## Inspect a candidate request locally

Run from a development checkout with its normal dependencies installed:

```python
from dead_letter.analysis import NormalizedMessage, Segment, prepare_request

message = NormalizedMessage(
    subject="Draft approval",
    sender="author@example.test",
    to=("Alex <alex@example.test>",),
    sent_at="2001-01-02T10:30:00-05:00",
    normalization_version="my-normalized-export-v1",
    segments=(
        Segment("current-1", "authored", "Review the draft and reply with approval."),
        Segment("prior-1", "quoted", "Earlier context", "prior@example.test"),
    ),
)
request = prepare_request(message, focus_identity=("Alex", "alex@example.test"))
print(request.preview())                    # No subject/body/addresses/path.
# Explicit sensitive inspection, still local:
# print(request.preview(include_state=True))
# payload = request.payload()               # Includes email text and questions.
```

This is a consumer recipe for an **already-normalized text export**, not advice
to build another MIME parser. EML users should continue using ordinary conversion
until the shared read-only core snapshot adapter is implemented. Supplying a
`NormalizedMessage` is the caller's assertion that its text is normalized; this
module is not a sanitizer, credential scrubber or attachment extractor.

`Segment` distinguishes authored, quoted, forwarded, signature-candidate and
disclaimer-candidate text. Signature candidates are preserved. Context keeps the
supplied order; the first three context segments are included by default. Put the
most relevant parent first. The selection policy and excluded count are visible;
this is not a semantic-retrieval or chronological-order guarantee. Text is never
silently shortened. Missing authors/timestamps remain `None`.

No focus identity means requests to intended recipients generally, not “you need
to act.” Supplying aliases scopes recipient-directed questions; it does not prove
ownership. Sender commitments concern the current author. The timestamp anchors
message interpretation, not overdue-task calculations.

## Two candidates, neither declared stable

`triage-v1` contains reply request, non-reply action request, sender commitment,
action deadline and expressed urgency. `triage-choice-v1` replaces the first two
Nouls with a focused response-expectation Choice, including insufficient context.
Both include a revision, exact profile hash and `experimental: true`. Each question
repeats its scope/evidence/trust instructions because question IDs are not model
instructions and questions cannot read each other's answers.

There are no ranking weights, action thresholds, archive-retention decisions or
assumed statistical independence between signals. A historical request does not
establish a current outstanding obligation or permission to act.

## Provenance, preview and budgets

The request fingerprint covers the exact state, profile revision/questions,
requested model, normalized endpoint and schema version. Identity, normalization
version and context policy are part of state. Canonically equivalent endpoint
spellings and alias ordering do not create distinct requests. Returned payloads
are detached copies; editing one does not change the prepared request.

These are **input identity primitives, not a completed cache**. Source-file hashes,
atomic sidecars, attempts, successful-result validation and alias-age-aware resume
remain service work. The default requested model is `jev-1.13.0`, selected from
current documentation, **not yet evaluated on this email profile**.

Local guards use UTF-8 bytes: 24,000 state bytes, 28,000 state plus the longest
question, and 48,000 full request bytes. These intentionally do not claim exact
provider-token accounting. Over-limit requests raise safe errors rather than
truncate. TypeSafe currently documents 64k total tokens and 32k state plus longest
question; reverify and test at transport integration time.

Endpoints are explicit parameters, not email/profile fields. HTTPS is required
except for loopback test servers. Credentials, query/fragment components and
ambiguous encoded/dot-segment paths are rejected. No DNS lookup or request is made.
This module deliberately ignores `TYPESAFE_BASE_URL` and `TYPESAFE_API_KEY`; the
future explicitly enabled provider adapter must read/validate environment-owned
configuration and disclose the effective destination before sending anything.

## Validate a response without inventing semantics

```python
from dead_letter.analysis import validate_response

# supplied_response is a complete JSON projection from a fixture or future
# adapter, NOT a raw SDK response/client/HTTP object.
# checked = validate_response(request, supplied_response)
```

The validator requires exactly the requested question IDs and correct kinds.
Noul keeps only its yes-probability; it never acquires synthetic confidence.
Choice/Score preserve confidence and full distributions. Score uses zero-based
levels and validates the legend against the requested criteria. Missing answers,
non-finite values, booleans-as-numbers, incomplete probability maps and unexpected
choices fail with safe codes. Probability-sum tolerance is `1e-6`; no rounding or
thresholding is performed. Unknown extra fields are discarded, including raw
request/body/debug fields. Missing model, request ID or token metadata remains
unknown rather than being fabricated.

Validation is **structural**, not an execution record, calibration test, abstention
policy or proof of sufficient context. Coverage remains separate from answers.
The service must implement execution and assessment statuses independently and
must not convert unavailable evidence into a negative prediction. Even valid
outputs can be semantically manipulated and never authorize mailbox actions.

## Evaluation material

`tests/backend/fixtures/analysis_cases.json` contains 22 synthetic development
cases covering negation, payment, commitments, overlapping requests, focus
ownership, quoted completion, missing/available parents, adopted forwards,
deadlines, urgent promotion, attachments, P.S. requests, prompt injection,
Spanish requests and historical timestamps. Labels are **proposals awaiting human
review**, not ground truth or model outputs. All cases are development-only;
related template families are explicit and must stay together during splitting.

The tests check contracts and fixture construction, not JEV accuracy. Before
stabilizing either profile, add reviewed, authorized examples and a separate
held-out set; compare both formulations, context/cleanup policies and a simple
baseline. Report per-label precision/recall, false positives per 100 messages,
missed requests, calibration and error-versus-review coverage separately. No live
request or private-email submission has been made for this implementation.

## First-party references checked September 18, 2026

- [SDK changelog](https://docs.typesafe.ai/sdk/python/changelog): 0.7.0 released
  today, replacing msgspec serialization with Pydantic; 0.6.0 changed Score
  criteria to an ordered sequence. Pin/test the SDK when adding the optional extra.
- [SDK usage](https://docs.typesafe.ai/sdk/python/usage): debug logging includes
  unredacted request/response bodies; unknown answer kinds can be skipped. A safe
  logging boundary and complete-answer validation are transport requirements.
- [Models](https://docs.typesafe.ai/models): versioned model IDs, movable aliases
  and context limits. Preserve the returned identifier; do not invent resolution.
- [Noul](https://docs.typesafe.ai/primitives/noul) and
  [Score](https://docs.typesafe.ai/primitives/score): native answer semantics.
- [Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13): adversarial
  content and numeric/date reasoning limitations remain relevant.
