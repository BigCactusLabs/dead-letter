# Experimental BYOK semantic analysis

Tracking: [issue #110](https://github.com/BigCactusLabs/dead-letter/issues/110).
Implementation: [PR #117](https://github.com/BigCactusLabs/dead-letter/pull/117),
merged to `main` on September 21, 2026 and first released in 0.4.5.

**Status: experimental single-message analysis, released in 0.4.5.**
Directory execution (#165) is an unreleased source capability.
Local EML preparation now connects to an explicitly enabled TypeSafe SDK adapter.
CLI and async Python consumers receive versioned JSON results. Neither candidate
profile has empirical email-triage quality results. Ordinary conversion, bundles,
MCP tools and UI remain local and do not enable analysis when a key is present.

The package includes an optional `typesafe` extra, pinned to
`typesafe-sdk==0.7.1`. Base and normal development installs remain SDK-free.
The extra and the `analyze` command are available in 0.4.5 and later; 0.4.0
and earlier do not include them. The commands below run from a development
checkout. With an installed package, drop the `uv run` prefix and its options;
remote execution needs the package installed with the `typesafe` extra.
Commands with `--extra typesafe` install the SDK into the project environment.
To return to SDK-free development after testing, run
`uv sync --extra dev --locked`.

## Start with a local preview

Run from a development checkout with its normal dependencies installed:

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
uv run --locked --extra typesafe dead-letter analyze message.eml \
  --provider typesafe --profile triage-choice-v1 --identity me@example.com
```

Selecting `--provider typesafe` without `--dry-run` is the CLI opt-in. Before the
request, a JSON disclosure on stderr identifies the effective destination host,
scope and experimental profile. If the disclosure sink fails, nothing is sent.
Single-message results are JSON on stdout. Without `--output`, single-message
execution writes no file. Directory execution requires `--output-dir`, as described
below. Source email and front matter are never changed.
Shell redirection is not an atomic sidecar/reuse contract.

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

## Saved single-message results (#164)

Add `--output PATH` to write a result or reuse a validated saved success:

```bash
uv run --locked --extra typesafe dead-letter analyze message.eml \
  --provider typesafe --output message.analysis.json --alias-max-age 86400
```

The parent directory must already exist. Before source reads or provider
preflight, an exclusive `.<name>.<uuid>.tmp` probe is created and removed to
check that the parent is writable. `PATH.attempts` must be absent or a real,
non-symlink directory; an existing attempts directory is also probed for writes.
A directory, symlink (including a broken link), other non-regular output target,
or unusable destination is refused with `analysis_output_invalid` (exit 2).
An unresolvable `~user` or destination path computation error uses the same safe
error before source reads or provider preflight.
These checks also apply to reuse and leave no probe file after success. They
cannot prevent permissions or available space from changing later. The source
`.eml` is never written or moved. `--output` with `--dry-run` is rejected;
previews remain local and write no files.

If PATH is absent, a successful envelope is saved there. Any non-success result
(failed or skipped) is instead saved as a separate record under `PATH.attempts/`,
with a unique filename. These records carry the safe status/error code, attempts,
retry count, fingerprints and creation time; they are never reused as success.
For absent PATH, provider preflight runs before the source is read. Its failures
use the same safe stderr JSON and exit 1 as stdout-only analysis, with no attempt
record. Source/preparation errors also return safe errors without a sidecar.
Cancellation still propagates. If `analyze_to_sidecar` or single-file CLI
`--output` is interrupted after at least one HTTP request starts, a safe record
is written under `PATH.attempts/` with `execution_status: interrupted`,
`error_code: analysis_interrupted` and `billing_status: unknown`. No success
sidecar is written, and cancellation before any HTTP request leaves no attempt
record. Ctrl-C returns exit 130. The original `asyncio.CancelledError` is
preserved, so outer `asyncio.timeout` and `asyncio.wait_for` still raise
`TimeoutError`; an outer timeout can also leave an interrupted attempt record.
A persistence error cannot suppress cancellation. A skip keeps exit 0 and writes
only an attempt record, **not PATH**; execution failures keep exit 1. Check `execution_status` and `sidecar.outcome`, not just the exit
code, before trying to read a successful result.

Stored envelopes add UTC ISO-8601 `created_at` and `reuse_key`. The key hashes
canonical JSON containing the result schema version, exact source SHA-256 and
byte size, existing `PreparedRequest.fingerprint`, profile SHA-256,
normalizer/state-builder versions, state SHA-256, provider, endpoint, requested
model, adapter version and SDK version. Adapter or SDK upgrades invalidate reuse.
State identity covers the focus identity and context selection. Native
answers, distributions and the provider-returned model retain their existing
meaning. No body/attachment text, raw SDK response, headers or keys are added.
Sidecars and attempt records **do contain the source basename and configured
focus identities**, just as stdout envelopes do. A source basename can itself
contain private subject text. These files are written with mode 0600 (subject to
platform permission semantics) and must be treated as sensitive.

An existing result must parse as strict JSON, pass envelope and native-answer
validation, have `execution_status: succeeded`, and match the current reuse key.
A saved success must include at least one `response_received` attempt,
`billing_status: unknown`, and a retry count consistent with its attempt count.
Inconsistent execution records are `analysis_output_corrupt`; these checks do
not add attempt or billing fields to the reuse key.
Reuse performs local destination checks, preparation and validation: no provider
call, network, SDK import, or API key is needed. It does not claim fresh inference.
Source location alone does not change identity. Stdout `source.reference` reports
the current invocation's source basename; `sidecar.stored_source_name` reports
the original basename. The stored file remains unchanged. If PATH vanishes
between the existence check and read, destination validation is retried once.
An absent PATH then follows the normal preflight-before-read inference path.
Repeated disappearance is refused as corrupt rather than retrying indefinitely.
An unknown or invalid schema, truncated/corrupt JSON, or malformed
answers produces `analysis_output_corrupt`. A valid non-success envelope or a
different binding produces `analysis_output_mismatch`. Both return exit 1,
leave PATH untouched, and tell the user to choose another path or remove the
existing file. Unknown extra fields are rejected, not passed through.

If returned model differs from requested model, `--alias-max-age SECONDS`
(default 86400; finite and nonnegative) limits reuse age. An older result is
refused with `analysis_output_stale_alias` (exit 1). A missing returned model
also uses this conservative age limit; it cannot prove an exact model pin.
Exact matches have no age limit. To tolerate clock skew, a creation time up to
300 seconds in the future has age 0; a time farther ahead or an invalid/non-UTC
timestamp is corrupt. Invalid age arguments return `invalid_alias_max_age`
(exit 2).

Stdout still contains the result JSON, with a transient `sidecar` object:
`path` and `outcome` (`written` for a successful saved result, `reused` for
validated reuse, or `attempt_recorded` for failed/skipped execution). For
`attempt_recorded`, `path` points to the attempt file. Reuse also reports
`created_at`, `age_seconds`, `returned_model` and `stored_source_name`. This
object is not stored in the result file.

If persistence fails after execution, stdout still contains the full result
envelope, with `sidecar: {"outcome": "write_failed", "error_code":
"analysis_output_write_failed"}`, and the CLI returns exit 1. The execution
status and answers are retained: a disk error does not turn a successful
inference into an empty failed inference. This also retains failure/skip
envelopes if recording their attempt fails.

Writes use a unique `.<name>.<uuid>.tmp` in the destination directory, exclusive
creation, file fsync, and no-clobber hard-link publication. Supported platforms
also fsync the directory. On macOS the file is additionally flushed with
`fcntl(F_FULLFSYNC)` when available; unsupported volumes use ordinary fsync.
A hard-link publication exposes that same flushed inode; exclusive-create
fallback flushes the directly written output. This requests stronger durability
but does not guarantee survival of power loss on all hardware or filesystems.
If a competing writer publishes first, our temporary file is discarded and the winner goes through the same validation and age
rules. A reused winner includes `sidecar.discarded_fresh_result: true`; a refused
winner reports `discarded_fresh_result: true` in stderr JSON. **The discarded
provider call was still billed; reuse cannot undo its charge.** Provider usage
for that discarded result is not added to the winner's usage.

Publication is atomic on local POSIX filesystems that support hard links.
Windows uses the same no-clobber link operation where available. If hard links
are unsupported, both platforms fall back to exclusive creation of PATH,
write, and file fsync; no existing path is replaced. This fallback prevents
clobbering but is not atomic for concurrent readers: an incomplete file is
refused, never reused. Windows directory fsync is not available through this
API. Network filesystems and power-loss durability are best-effort. A hard kill
in the exclusive-create fallback can leave an incomplete PATH; choose another
path or remove it after inspection. Catchable write exceptions clean up owned
temporary files (and an owned partial fallback output). Other temporary files
are never read as results or automatically deleted. Write/fsync errors produce
the `write_failed` result described above; an error after publication can leave
a complete result that a later run can validate. Files are created with mode
0600 and new attempt directories with mode 0700, subject to platform permission
semantics.

## Directory execution (#165)

This capability is **unreleased**. From this checkout, analyze a directory with
one independent inference per message:

```bash
uv run --locked --extra typesafe dead-letter analyze ./mail \
  --provider typesafe --output-dir ./analysis --jobs 4
```

`--output-dir` is required for a directory (`analysis_output_dir_required`, exit
2). Directory input rejects `--output`, `--dry-run` and `--show-state`; file input
rejects `--output-dir` and `--jobs` (`invalid_analysis_arguments`, exit 2).
Directory `--jobs` defaults to 4. When supplied, it must contain only ASCII
digits representing 1–16; signs, whitespace and underscores are rejected
(`invalid_analysis_jobs`, exit 2). The existing profile, identity, context, model,
alias-age, timeout, budget and retry options apply separately to each message.
Per-request disclosures still go to stderr. No messages are merged into one state.

Discovery uses the exact `convert_dir` helper: recursive `Path.rglob("*")`,
case-insensitive `.eml` suffix matching, regular-file checks, exclusion of resolved
paths outside the source root, deduplication by resolved path and final path
sorting. File symlinks inside the root can be selected; symlink directories are
not traversed by this glob. The first encountered alias supplies the retained
path, before sorting. Output must be outside the resolved source tree; a nested
output (including symlink or case aliases and missing path components) returns
`analysis_output_inside_source`, exit 2. Existing ancestors are checked by
filesystem identity as well as normalized path keys. An existing output root
that is not a directory, including a symlink to a non-directory, returns
`analysis_output_invalid`, exit 2, before any item starts or files are created.
These checks keep generated output outside the source tree.

The output tree mirrors source-relative paths; parent directories are created:

```text
mail/a/b/x.eml  -> analysis/a/b/x.analysis.json
                  analysis/a/b/x.analysis.json.attempts/<unique-id>.json
```

All targets are computed before scheduling. Sources with colliding target names,
including case-insensitive aliases and resolved parent aliases, each fail with
`analysis_output_collision` and make no request. Prefix overlaps are also
collisions: if one result path or its `.attempts` directory is an ancestor of
another result path, all involved items are refused before execution. For
example, `x.eml` conflicts with `x.analysis.json/y.eml` and
`x.analysis.json.attempts/y.eml`, regardless of scheduling order.
Existing targets use the same strict reuse/refusal rules as single-message
sidecars. Corrupt, mismatched or
stale-alias results are never overwritten. Other items can continue.

A fixed pool of `jobs` async workers consumes a bounded queue. Preflight runs once,
lazily before the first fresh item: authorization is checked before discovery,
and key/SDK checks precede reading a fresh source. A run that only reuses valid
sidecars needs no key or SDK import. Failed preflight stops scheduling with
`preflight_failed`; the failing item reports the safe preflight error code.
There is no directory retry loop. The SDK remains the only retry owner. In
particular, a server `Retry-After` at or above the call budget surfaces as
`provider_rate_limited` with `retry_count: 0`.

| Trigger | New work | In-flight work | `stop_reason` |
| --- | --- | --- | --- |
| Authentication or permission failure | Stop | Cancel | `authentication_failed` |
| First three provider-attempted completions all have validation/bad-request errors | Stop | Finish | `request_rejected` |
| Three consecutive rate-limit, unavailable, timeout, budget or connection failures | Stop | Finish | `provider_throttled` |
| External cancellation / Ctrl-C | Stop | Cancel | `interrupted` |
| Local input/output error, collision or no-authored-text skip | Continue | Continue | None |

A provider success or other provider outcome resets the throttle streak. Local
errors, skips and offline reuse do not affect either provider-completion counter.
Stop thresholds follow completion order, so concurrent calls can already be in
flight when a threshold is reached. Queued items not started get no output or
attempt record and count as `not_started`.

Completed sidecars remain reusable after failure or interruption. Re-run the
same command to resume: sidecars decide reuse, never a summary or attempt file.
A cancelled item with at least one HTTP attempt records `execution_status:
interrupted`, `error_code: analysis_interrupted` and `billing_status: unknown`
under its attempts directory and counts as `failed`. A cancelled item with no
HTTP attempt counts as `not_started` with a null error code and writes no record,
including cancellation during preparation or reuse. Client cleanup and owned
temporary-file cleanup precede propagation of cancellation. A persistence failure is reported safely as
`analysis_output_write_failed`; it cannot make cancellation succeed or prove a
request was unbilled. Hard kills and storage failures retain the filesystem
limitations described for single-message sidecars.

Stdout is a JSON summary; no summary file is written. Python returns the same
`dict` from exported async `analyze_directory(source_dir, output_dir, *, provider,
allow_remote=False, jobs=4, ...)`. Its remaining options match
`analyze_to_sidecar`. `allow_remote` must be exactly `True`, including for offline
resume, or `remote_analysis_not_authorized` is raised before source reads.
Cancellation attaches the partial summary as `exc.dead_letter_summary` to the
original `asyncio.CancelledError` and re-raises that same exception after worker
cleanup. Callers can inspect the attribute when handling cancellation. Outer
`asyncio.timeout` and `asyncio.wait_for` retain their normal `TimeoutError`
behavior; the original cancellation with its summary is the timeout's cause.
The CLI handles cancellation at the command boundary to print the partial
summary and return exit 130.

Summary schema version 1:

- `schema_version: 1`, `artifact_type: directory_analysis`.
- `status`: `completed`, `stopped` or `interrupted`; `stop_reason`: the code above
  or null. A completed run can contain per-item failures.
- `counts`: `discovered`, `succeeded`, `reused`, `skipped`, `failed`, `not_started`.
  Each discovered item occupies exactly one outcome count. Cancelled items count
  as failed only if they sent HTTP; cancelled unsent items remain not_started.
- `observed_models`: returned model IDs and counts from fresh successful results
  published by **this run only**. Reused results are excluded. Missing model IDs
  are omitted, never replaced by the requested alias.
- `usage`: sums of supplied usage fields from fresh successes published by
  **this run only**. Reused results are excluded; missing fields are not filled
  with zero. These are observed response totals, not complete billing totals or
  estimates of SDK retry usage.
- `items_with_unknown_usage`: fresh published successes lacking usage, plus every
  item that sent HTTP in this run without yielding a fresh published success.
  This includes failed/interrupted calls, failed publication and a fresh result
  discarded in favor of a concurrent reusable winner. That last item still has
  outcome `reused`, but billing is `unknown` and unknown-usage count increases.
  Offline reuse, never-started and local-only items do not enter this count.
- `billing_status`: `unknown` if this run attempted any request, otherwise
  `not_attempted`; offline reuse does not count an old request as a new one.
- `items`: source-sorted entries with `source` and `output` paths relative to the
  supplied roots, `outcome` and `error_code` (null when absent). `output` is the
  intended success path, even for failed attempts. Paths can be sensitive.

Exit 0 means every discovered item succeeded, reused or skipped (also an empty
run). Exit 1 means any item failed or scheduling stopped early. Exit 130 prints
an interrupted partial summary. Argument errors return exit 2 with safe stderr
JSON. A second forced interrupt or a hard kill cannot guarantee a summary.

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

For the saved-result contract, use the exported async helper with the same
execution options plus `output` and `alias_max_age`:

```python
from dead_letter.analysis import analyze_to_sidecar

result = asyncio.run(analyze_to_sidecar(
    "message.eml", "message.analysis.json", provider="typesafe",
    allow_remote=True, alias_max_age=86400,
))
```

`analyze_to_sidecar` can reuse a matching result with `allow_remote=False`
(the default); a missing result requires explicit remote permission. For an
existing result it reads locally for reuse validation without the optional
SDK/key. For absent output it runs provider preflight before reading the source,
matching the stdout-only `analyze_eml` ordering. A reuse validation or alias-age
failure refuses inference rather than falling through to preflight.

Inside an existing event loop, await either helper directly. `analyze_prepared`
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

The adapter lazily imports exactly SDK 0.7.1. Missing or different versions fail
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

`execution_status` is `succeeded`, `failed` or `skipped`; persisted cancelled
attempts use `interrupted`. A failed call has no
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

Still pending: reviewed held-out evaluation (#166). See the
[implementation checkpoint](../project/2026-09-18-issue-110-analysis-foundation.md).

## First-party implementation references

Checked September 18, 2026; SDK source link updated to 0.7.1 on September 27, 2026:

- [SDK 0.7.1 source](https://github.com/typesafe-ai/typesafe-sdk-python/tree/v0.7.1)
  and [changelog](https://docs.typesafe.ai/sdk/python/changelog).
- [SDK usage](https://docs.typesafe.ai/sdk/python/usage),
  [retry policy](https://docs.typesafe.ai/sdk/python/api/retries), and
  [response types](https://docs.typesafe.ai/sdk/python/api/types/responses).
- [Models](https://docs.typesafe.ai/models), [Noul](https://docs.typesafe.ai/primitives/noul),
  [Score](https://docs.typesafe.ai/primitives/score), [Choice](https://docs.typesafe.ai/primitives/choice),
  and [Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
