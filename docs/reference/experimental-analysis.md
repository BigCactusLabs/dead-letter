# Experimental semantic-analysis preparation

Tracking: [issue #110](https://github.com/BigCactusLabs/dead-letter/issues/110).

**Status: offline EML/Python/CLI preparation, not a released remote-analysis
feature.** The development checkout can read a real `.eml`, prepare a candidate
request and preview it locally. It cannot yet invoke TypeSafe, write analysis
sidecars, resume analyses or process directories. Neither profile has empirical
email-triage quality results. Existing conversion defaults, MCP tools and UI stay
local; there is no `dead-letter[typesafe]` extra or provider SDK dependency yet.

## Preview an EML from the development checkout

These commands are available on the implementation branch, not in the currently
published package. Use the repository's normal uv setup:

```bash
uv sync --extra dev
uv run dead-letter analyze message.eml --provider typesafe --dry-run

# Scope recipient-directed questions to one person and their aliases.
uv run dead-letter analyze message.eml --provider typesafe --dry-run \
  --identity me@example.com --identity "My Name" --profile triage-choice-v1

# Explicit sensitive LOCAL inspection. Do not paste private state into public logs.
uv run dead-letter analyze message.eml --provider typesafe --dry-run --show-state
```

The default JSON preview on stdout omits email body, subject, addresses and source
path. It reports scope, destination host, coverage, source hash/byte count,
normalization/runtime versions, profile/model, effective-input fingerprint and
byte limits. `execution_status: skipped` and `remote_enabled: false` mean **no
inference happened**. The default host describes the prepared destination; no DNS
lookup or network request is made. `--show-state` adds normalized text/metadata and
a local source basename explicitly. The preview itself is not a result sidecar.

`--provider typesafe` and `--dry-run` are both required in this slice. Omitting
`--dry-run` returns `remote_analysis_not_implemented` before reading the email.
A present `TYPESAFE_API_KEY` never enables anything; this code does not read it.
Unknown flags, including an accidentally supplied credential flag, fail without
echoing their values. Errors are safe JSON codes on stderr: exit 2 for arguments
or unavailable remote execution, 1 for preparation failure, 130 for interruption.
Successful preparation returns 0. Ordinary bare-path conversion still dispatches
to `convert`, never to analysis.

The CLI honors an explicit process `TYPESAFE_BASE_URL`, validates it and reports
its host. Python APIs instead take explicit `base_url=` parameters and ignore
that environment variable. HTTPS is required except for literal loopback test
servers. URL credentials, queries/fragments and ambiguous paths are rejected.
Neither email content nor a profile can choose a destination.

## Python consumer recipe

```python
from dead_letter.analysis import prepare_eml

prepared = prepare_eml("message.eml", focus_identity=("me@example.com",))
print(prepared.preview())                 # No body, subject, addresses or path.

# Explicit sensitive local access; none of these calls sends anything:
# state = prepared.preview(include_state=True)
# payload = prepared.request.payload()    # Selected evidence plus questions.
# diagnostics = prepared.snapshot.diagnostics
```

`prepared.snapshot` holds local provenance and detached diagnostics separately
from `prepared.request`. Source paths/hashes, full diagnostics, raw MIME/HTML,
attachment payloads, calendar contents and arbitrary headers are not request
fields. The EML is never moved, deleted or rewritten; no output file is created.

For an already-normalized export, the lower-level API remains available:

```python
from dead_letter.analysis import NormalizedMessage, Segment, prepare_request

message = NormalizedMessage(
    subject="Draft approval", sender="author@example.test",
    to=("Alex <alex@example.test>",), sent_at="2001-01-02T10:30:00-05:00",
    normalization_version="my-normalized-export-v1",
    segments=(Segment("current-1", "authored", "Review the draft and reply with approval."),),
)
request = prepare_request(message, focus_identity=("Alex", "alex@example.test"))
print(request.preview())
```

`NormalizedMessage` is a caller-supplied evidence contract, not a sanitizer,
credential scrubber or attachment extractor. EML consumers should use the shared
snapshot path instead of constructing another MIME parser.

## Snapshot, authorship and coverage contract

`dead_letter.core.snapshot.read_snapshot` reads one regular `.eml` through a
bounded descriptor and feeds the **same immutable byte buffer** to both SHA-256
and the shared MIME parser. A file replaced after the read cannot change the
content bound to that hash. This does not promise a filesystem transaction against
an external writer modifying bytes during the read. The maximum raw input is
100,000,000 bytes; the helper accepts a smaller explicit bound. FIFOs and other
non-regular inputs are rejected. This is a bounded per-message operation, not a
streaming mailbox reader or a strict process-memory limit.

The snapshot reuses image filtering, HTML/plain segmentation, attribution,
cleanup, rendering and diagnostics. Conversion retains its existing wrapper and
defaults. Analysis consumes the **pre-render zones**, not a Markdown body whose
quote-only fallback might look like a new authored request. Unknown quoted or
forwarded authors stay unknown. Known attribution is still an untrusted claim.

Signature/disclaimer text is retained, including possible P.S. requests. Tracking
and signature images are filtered using existing core rules; inline image data
URIs, raw-HTML output and calendar summaries are disabled. Parser internals may
inspect/decode MIME parts locally; attachment/calendar contents are never selected
as analysis evidence. No URLs, remote images or external parent messages are
fetched, and no OCR is performed.

The source Date header is retained as temporal evidence instead of manufacturing
a timezone from a normalized datetime or today's processing environment. Missing
dates remain unknown. Segment IDs are deterministic normalized-zone positions,
not offsets into raw MIME. Context stays in pipeline order, which is not a
verified chronology. A forward marker and its immediately following forwarded
body share one context slot. The default selects the first three context segments;
excluded context is reported. Authored text is not silently shortened.

No focus identity means requests to intended recipients generally, not “you need
to act.” Aliases scope recipient-directed questions without proving ownership.
Sender commitments concern the current author. The timestamp anchors message-time
interpretation, not current outstanding-task or overdue calculations. Missing
parent/attachment evidence remains a coverage fact, not a negative prediction.

## Two candidates, neither stable

`triage-v1` contains reply request, non-reply action request, sender commitment,
action deadline and expressed urgency. `triage-choice-v1` replaces the first two
Nouls with one response-expectation Choice that includes insufficient context.
Each profile has a revision, exact hash and `experimental: true`; each question
repeats its own scope/evidence/trust instructions. There are no priority weights,
action cutoffs, retention decisions or assumed independence between signals.

## Fingerprints and limits

The request fingerprint covers state, profile revision/questions, requested model,
normalized endpoint and schema version. State includes identity, context policy
and a normalization fingerprint covering effective options and installed parser
versions. Moving identical EML bytes to a different filename does not change the
request fingerprint. Canonically equivalent endpoint spellings or alias ordering
do not create new effective inputs. Exported dictionaries are detached copies.

This is **not an implemented cache**. Atomic sidecars, successful-result reuse,
attempt records, evaluated-at timestamps and alias-age handling remain service
work. The default model `jev-1.13.0` is a documented identifier, not a model that
has been evaluated on these profiles.

Local UTF-8-byte guards remain 24,000 for state, 28,000 for state plus the longest
question, and 48,000 for the full request. Over-limit input raises a safe error
rather than being truncated. These are not provider-token measurements. Reverify
TypeSafe's documented token limits with the selected SDK during transport work.

## Native-answer validation

`validate_response(request, supplied_response)` accepts an allowlisted JSON
projection, not a raw SDK/client/HTTP object. It requires exactly the requested
question IDs, correct kinds, complete distributions and the requested Score
legend. Noul retains only yes-probability; no confidence is invented.

Score must match its probability-weighted level within `1e-6 * maximum_level`.
Choice must select a maximum-probability alternative within `1e-6`; ties are
allowed. Probability sums must be within `1e-6` of one. These are numerical
serialization tolerances, not semantic cutoffs. Native scores, distributions and
supplied confidence are preserved, not rounded or recalibrated. Contradictory
responses fail with `inconsistent_score_distribution` or
`inconsistent_choice_distribution`.

Missing answers, invalid numbers and incomplete maps fail closed. Extra raw/debug
fields are discarded. Missing model/request/token metadata remains unknown.
Structural validation does not establish inference execution, sufficient evidence,
calibration, correctness, legitimacy or permission to act. Future service code
must maintain separate execution/assessment statuses; valid-looking model outputs
can still be semantically manipulated.

## Evaluation and remaining transport work

The 22 cases in `tests/backend/fixtures/analysis_cases.json` remain synthetic,
unreviewed development material. New synthetic EML tests exercise the actual
preparation path, not JEV accuracy. Human-review/expand the seed, create a
family-separated held-out set and compare both formulations, context/cleanup
policies and a baseline before declaring stable semantics. Do not tune on the
held-out set or treat ambiguous human labels as certain ground truth.

Still pending: optional pinned SDK and explicit BYOK remote invocation; transport
body-log and redirect/auth safeguards; service envelopes, sidecars and resume;
bounded batch/retry/cancellation behavior; transport-aware doctor checks and
privacy/setup guidance. No private-email upload or live inference is part of this
implementation. The updated [implementation checkpoint](../superpowers/specs/2026-09-18-issue-110-analysis-foundation.md)
records the next work without implying that #110 is complete.

## First-party references checked September 18, 2026

- [SDK changelog](https://docs.typesafe.ai/sdk/python/changelog): version 0.7.0
  changes serialization to Pydantic; pin/test the SDK rather than using old examples.
- [SDK usage](https://docs.typesafe.ai/sdk/python/usage): body logging and skipped
  unknown answer kinds make logging isolation and completeness checks necessary.
- [Models](https://docs.typesafe.ai/models): preserve returned identifiers and do
  not invent alias resolution.
- [Noul](https://docs.typesafe.ai/primitives/noul),
  [Score](https://docs.typesafe.ai/primitives/score), and
  [Choice](https://docs.typesafe.ai/primitives/choice): native answer semantics,
  weighted Score mean and highest-probability Choice selection.
- [Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13): adversarial
  content and numeric/date reasoning limitations remain relevant.
