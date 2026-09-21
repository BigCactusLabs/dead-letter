# Experimental BYOK semantic analysis

Tracking: [issue #110](https://github.com/BigCactusLabs/dead-letter/issues/110).
Implementation: [PR #117](https://github.com/BigCactusLabs/dead-letter/pull/117),
merged to `main` on September 21, 2026 and not yet in a PyPI release.

**Status: development-checkout single-message analysis, not a released feature.**
Local EML preparation now connects to an explicitly enabled TypeSafe SDK adapter.
CLI and async Python consumers receive versioned JSON results. Neither candidate
profile has empirical email-triage quality results. Ordinary conversion, bundles,
MCP tools and UI remain local and do not enable analysis when a key is present.

There is **no `dead-letter[typesafe]` package extra yet**. The tested SDK is an
optional exact `typesafe-sdk==0.7.0` uv overlay. The base dependency lock and release
versions are unchanged. Packaging the extra with a regenerated, verified lock is
still tracked under #110. Do not use the commands below with the older PyPI release.

## Start with a local preview

Run from the implementation checkout with its normal dependencies installed:

```bash
uv sync --extra dev --locked
uv run dead-letter analyze message.eml --provider typesafe --dry-run

# Scope requests to one recipient and their aliases; still no inference.
uv run dead-letter analyze message.eml --provider typesafe --dry-run \
  --identity me@example.com --identity "My Name" --profile triage-choice-v1

# Explicit private LOCAL inspection, without an SDK or key.
uv run dead-letter analyze message.eml --provider typesafe --dry-run --show-state
```

The default preview omits body, subject, addresses and source path. It reports
scope, destination host, coverage, source hash/size, normalization versions,
profile/model, effective-input fingerprint and byte limits. `execution_status:
skipped` and `remote_enabled: false` mean no inference happened. The host is a
proposed destination, not evidence that a DNS lookup or connection succeeded.
`--show-state` adds normalized evidence and the local source basename; treat this
output as private. It is rejected without `--dry-run`.

## Explicit remote execution

First supply `TYPESAFE_API_KEY` through the process environment or a trusted secret
manager. Never put the actual key in an argument, profile, source email, repository,
log, or saved JSON. A populated environment variable is not consent by itself.

**The following command sends normalized email text and selected metadata to
TypeSafe and may incur provider charges:**

```bash
uv run --locked --with typesafe-sdk==0.7.0 dead-letter analyze message.eml \
  --provider typesafe --profile triage-choice-v1 --identity me@example.com
```

Selecting `--provider typesafe` without `--dry-run` is the CLI opt-in. Before the
request, a JSON disclosure on stderr identifies the effective destination host,
scope and experimental profile. If the disclosure sink fails, nothing is sent.
Results are JSON on stdout. No file is written, no email is moved/deleted, and
front matter is not changed. Directory input, `--output`, sidecars and resume are
not implemented yet. Shell redirection is not an atomic sidecar/cache contract.

Results can still be sensitive: they contain local source basenames, configured
identity aliases, provenance and judgments. They do not contain raw body text,
full headers, attachment contents, SDK/HTTP objects or the API key. Restrict access
to any result you retain. Classifications never authorize replies, payments,
link-following, file reads, mailbox mutations or additional tool access.

Exit codes: 0 for success or a documented skip; 1 for preparation/provider failure;
2 for invalid arguments; 130 for interruption. Preflight failures return safe
stderr JSON. Provider failures return a failed result envelope on stdout with a
safe error code. Always inspect `execution_status` and `assessment_status`; a
zero exit alone does not mean a semantic assessment occurred.

## Python consumer recipes

Preparation remains entirely local and takes an explicit endpoint parameter;
it does not read provider environment variables:

```python
from dead_letter.analysis import prepare_eml

prepared = prepare_eml("message.eml", focus_identity=("me@example.com",))
print(prepared.preview())
# Explicit sensitive local access:
# payload = prepared.request.payload()
# diagnostics = prepared.snapshot.diagnostics
```

The execution API requires an explicit provider and `allow_remote=True`. Its
configuration can be read from the process environment, or supplied separately
as a trusted `TypeSafeConfig`. Keys always come from the environment:

```python
import asyncio
from dead_letter.analysis import analyze_eml

result = asyncio.run(analyze_eml(
    "message.eml", provider="typesafe", allow_remote=True,
    profile_name="triage-choice-v1", focus_identity=("me@example.com",),
))
print(result["execution_status"], result["assessment_status"])
```

Inside an existing event loop, await `analyze_eml` directly. `analyze_prepared`
executes a `PreparedEmail` with the same explicit permission and disclosure.
Endpoint configuration must agree with the prepared request; changing the
process endpoint after preparation cannot silently redirect a previously
prepared payload. Applications replacing `on_disclosure` must display the
provided disclosure in their own trusted interface; it is not email-supplied code.

The lower-level `NormalizedMessage`, `Segment`, `prepare_request` and
`validate_response` APIs remain available for already-normalized exports.
`NormalizedMessage` is not a sanitizer or secret scrubber. EML consumers should
use the shared snapshot rather than build another MIME parser.

## Provider, credentials and privacy

The adapter lazily imports exactly SDK 0.7.0. Missing or different versions fail
preflight rather than silently using an untested API. Normal conversion, previews,
help and imports do not load the SDK. `dead-letter doctor` reports only installed
and configured booleans and does not contact TypeSafe or validate a real key.
Absence of this optional setup does not make a local-only installation unhealthy.

`TYPESAFE_BASE_URL` is trusted process configuration for the CLI/execution API,
never an email/profile field. The default is `https://api.typesafe.ai`. HTTPS is
required except for literal loopback test servers. Credentials, query/fragment
components and ambiguous paths are rejected. Only POST to the configured API root
plus `/v1/systemone` is permitted; redirects, including same-host redirects, are
rejected. Implicit HTTP(S) proxy environment configuration is not honored. Use an
explicit validated API-root proxy when needed and assess its privacy policy too.

The SDK can log unredacted request/response bodies at DEBUG. The adapter installs
source-logger filters before importing it, including under `TYPESAFE_LOG_LEVEL`,
and suppresses SDK/HTTP logs for the private call's context. Filters are inert
outside that context; unrelated SDK requests retain their logging. No global
logger level or environment value is overwritten. This is integration-level
logging containment, not protection from a hostile host, custom instrumentation
or application code deliberately dumping in-memory state. Raw SDK exceptions are
never printed or returned. Tests include a fresh interpreter with verbose logging.

TypeSafe's [privacy policy](https://typesafe.ai/legal/privacy-policy), checked
September 18, 2026, states that Input is not used to train/fine-tune models. The
policy also describes retention and service providers: this is **not zero
retention or local processing**. Confirm authorization before submitting any
email; public availability of an archive alone is not permission to upload it.

## Retry, timeout and response boundaries

Defaults: 15-second HTTP operation timeout, 45-second total execution budget,
and up to two SDK retries after the first attempt. CLI options are
`--timeout-seconds`, `--budget-seconds` and `--max-retries`; budgets must be positive
and at most 300 seconds, retries 0–3. The SDK is the only retry owner. There is no
outer retry multiplier, silent alternate model or alternate provider. The async
wall-clock budget also bounds a hanging request or long retry delay.

Authentication/permission/validation failures fail fast. Rate limits, overload,
connection failures and operation timeouts follow the bounded SDK policy. HTTP
operation timeouts have `provider_timeout`; total wall-clock expiry has
`provider_budget_exceeded`. External task cancellation propagates after client
cleanup. Attempt records include sequence, status, safe request ID, actual wire
hash and observed duration. They are not evidence that a request was unbilled.
`billing_status` stays `unknown` once an HTTP attempt has begun. Returned usage is
only what the successful response actually supplies; missing retry usage is not
estimated or turned into a total-cost claim.

Responses are streamed with a 512,000-byte decompressed-content limit. Non-JSON,
duplicate JSON keys, non-finite numbers, missing/extra answer IDs, unknown answer
kinds and inconsistent distributions are rejected, not converted to negatives.
A strict custom response projection preserves answer objects until dead-letter's
validator checks them; the SDK cannot silently skip an unknown answer kind.

## Result and assessment semantics

Version 1 `message_analysis` envelopes preserve requested/returned model,
profile/revision/hash, source/state/request hashes, normalization/runtime versions,
identity scope, message-time policy, coverage, timestamps, attempts, usage and
native answers. Missing returned model/request ID/token data remain unknown.
Source hash alone is never presented as sufficient input identity.

`execution_status` is `succeeded`, `failed` or `skipped`. A failed call has no
assessment or filled-in answers. A message with no authored evidence is skipped
locally as `insufficient_context`, without asking about quoted text as though it
were newly authored. Successful candidate results are `review_suggested` under
named policy `experimental_review-v1`, because profile quality is not yet
validated. This is not a probability cutoff or calibration claim. A returned
response-expectation Choice of `insufficient_context` preserves that assessment.

Noul remains a yes-probability, never an invented confidence score. Score/Choice
retain their full distributions and native confidence. Score must agree with its
weighted level mean within `1e-6 * maximum_level`; Choice must select a
maximum-probability alternative within `1e-6`, with ties allowed. These tolerances
address numeric serialization, not semantic correctness. There are no universal
priority weights or assumptions of statistical independence between questions.

## Snapshot and context contract

`core.snapshot.read_snapshot` uses the existing MIME/normalization pipeline and
hashes the same immutable byte buffer passed to `parse_eml_bytes`. It rejects
non-regular inputs and bounds raw input to 100,000,000 bytes by default. It is not
a streaming mailbox reader, strict process-memory bound, or filesystem transaction
against an external process changing a file during the read.

Analysis consumes pre-render zones. Quote-only rendering fallback cannot promote
old requests to new authored text. Signature/disclaimer/P.S. text is retained;
known quoted attribution remains an untrusted claim and missing attribution is
unknown. Original Date headers anchor message-time interpretation without invented
time zones or current-overdue arithmetic. Inline data URIs, raw HTML and calendar
summaries are excluded. Parser internals can inspect MIME parts locally, but
attachment/calendar text, arbitrary headers and full diagnostics are never sent.
No email URLs, images, external parents or unrelated files are fetched; no OCR.

First-N context follows pipeline order, not verified chronology or semantic
retrieval. Default: three quoted/forwarded segments; a forward marker shares its
following body's slot. Exclusions are visible; authored text is not silently
shortened. Local guards are 24,000 UTF-8 state bytes, 28,000 state plus longest
question bytes, and 48,000 total request bytes. These are not provider-token
measurements. State identity includes normalization, identity and context policy;
model, endpoint and exact profile participate in the request fingerprint.

No focus identity means intended recipients generally, not “you need to act.”
Aliases scope judgments without proving ownership. Commitments concern the current
author. Missing attachment/parent content is coverage, not an assessed absence.
`triage-v1` and competing `triage-choice-v1` concern requests communicated at
message time, not unresolved obligations or today's user priority.

## Evaluation and remaining work

The 22 synthetic seed cases remain unreviewed development material. Tests exercise
actual core/SDK contracts with synthetic EML and fake HTTP; they are not empirical
JEV inference, accuracy, calibration or latency benchmarks. No real key or private
email was submitted during this implementation. Human-review/expand the seed and
compare both profiles on a family-separated held-out set before freezing semantics.

Still pending: packaged optional extra plus verified lock, atomic collision-safe
sidecars, valid-result-only resume/alias-age handling, directory concurrency and
partial-success persistence. See the [implementation checkpoint](../project/2026-09-18-issue-110-analysis-foundation.md).

## First-party implementation references

Checked September 18, 2026:

- [SDK 0.7.0 source](https://github.com/typesafe-ai/typesafe-sdk-python/tree/v0.7.0)
  and [changelog](https://docs.typesafe.ai/sdk/python/changelog).
- [SDK usage](https://docs.typesafe.ai/sdk/python/usage),
  [retry policy](https://docs.typesafe.ai/sdk/python/api/retries), and
  [response types](https://docs.typesafe.ai/sdk/python/api/types/responses).
- [Models](https://docs.typesafe.ai/models), [Noul](https://docs.typesafe.ai/primitives/noul),
  [Score](https://docs.typesafe.ai/primitives/score), [Choice](https://docs.typesafe.ai/primitives/choice),
  and [Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
