# Issue #103: streaming MBOX ingestion — research and implementation decisions

Reviewed September 18, 2026. This is an implementation/design record, not a
claim of universal MBOX conformance or empirical coverage of every Gmail export.
Public recipe: [Gmail Takeout to Markdown/Cabinet](../reference/gmail-takeout.md).

## Sources that changed the implementation

1. [RFC 4155](https://www.rfc-editor.org/rfc/rfc4155.html), especially format
   variation, escaping ambiguity and the default postmark description. Decision:
   binary framing, documented postmark grammar, no universal autodetection claim.
2. [Dovecot implementers' MBOX notes](https://doc.dovecot.org/2.3/admin_manual/mailbox_formats/mbox/),
   especially From escaping, Content-Length validation, dialects and incompatible
   locks. Decision: explicit preserve/mboxrd/mboxo handling; refuse length-framed
   mboxcl/mboxcl2 before their bodies. mboxcl uses **mboxo**, not mboxrd quoting.
   Process immutable exports, not live system spools. This is archival format
   guidance, not a recommendation to deploy that historical Dovecot release.
3. [Python's mailbox API](https://docs.python.org/3/library/mailbox.html) and
   CPython 3.13.5 `mailbox.mbox._generate_toc`, inspected locally. The latter
   allocates starts/stops lists and a complete offset dictionary, using uncapped
   `readline()`. A convenient message iterator is not necessarily constant-memory
   in message count. Decision: small independent framing layer; keep the existing
   MIME parser. The stdlib reader is useful as a differential-test oracle for
   its own writer's dialect, not as a universal conformance oracle.
4. [Benjamin Yolken's first-person Gmail archive analysis](https://yolken.net/blog/six-years-of-emails),
   September 26, 2020. Its Takeout example has a numeric timezone **before** the
   year, X-GM-THRID and X-Gmail-Labels. Decision: include that postmark shape and
   metadata in synthetic tests rather than accepting only timezone-free ctime.
   This example does not prove every Takeout export uses one quoting dialect.
5. [Google's Gmail export guide](https://support.google.com/mail/answer/10016932?hl=en).
   Decision: preserve labels/headers/attachment-aware output and document local
   extraction of the downloaded container. Encrypted messages remain encrypted;
   an export capability is not permission to send private mail to another service.
6. [RFC 5322 section 2.2.3](https://www.rfc-editor.org/rfc/rfc5322.html#section-2.2.3).
   Folded header continuations belong to the preceding field. The review finding
   on PR #115 was valid: trimming leading whitespace before identifying
   Content-Length turned ordinary folded text into an archive-fatal condition.
   Decision: exclude SP/TAB continuations before testing a storage-header name.
7. [SQLite authors' testing practice](https://www.sqlite.org/testing.html),
   especially I/O-error injection and integrity checks after failure. Adapted
   the method, not a database dependency: inject partial writes, flush failures,
   pre-commit interruption and final-copy interruption, then parse the produced
   report and check its counts against actual committed entries. This does not
   claim SQLite's power-loss durability or transactional guarantees.
8. [Python 3.12 regex reference](https://docs.python.org/3.12/library/re.html).
   Possessive quantifiers prevent backtracking through their matched text.
   A malformed postmark with a long whitespace-only `remote from` tail and lone
   CR reproduced quadratic time in the original pattern. Making that tail
   possessive removes the overlapping redistribution against optional padding;
   valid LF/CRLF/EOF and padding cases retain their behavior. A subprocess timeout
   bounds the regression test itself if the vulnerable pattern returns.
9. [Python filesystem APIs](https://docs.python.org/3.12/library/os.html) and
   the Windows implementations in CPython 3.12.10
   [fileutils.c](https://github.com/python/cpython/blob/v3.12.10/Python/fileutils.c)
   and [posixmodule.c](https://github.com/python/cpython/blob/v3.12.10/Modules/posixmodule.c).
   Bind descriptor bytes to path identity, but compare each metadata API against
   its own initial timestamp baseline. The Windows path-stat wrapper substitutes
   birth time for ctime; blindly requiring identical cross-API tuples produced
   false source-change failures in the new Windows CI tests. No timestamp checks
   are disabled: identity is compared across APIs and mutation within each API.

## Architecture

`binary capped reader -> one staged EML -> existing _build_rendered_markdown ->
existing serializer/attachment helpers -> exclusive output + streamed report`.

No remote provider, no second MIME parser, no new dependency, no release bump.
The EML CLI, MCP and UI contracts remain intact. Three separate concerns:
`core/mbox.py` frames bytes; `core/mbox_import.py` adapts records to existing core;
`backend/mbox_cli.py` consumes results and uses `core/stream_report.py`.

Each record uses source ordinal plus stored-byte SHA-256, with archive basename
and byte ranges in metadata. Message-ID is useful metadata but not unique enough
to drive collision safety or deduplication. Subject is never a path component.
The full hash is independent of selected unquoting. Blank storage lines are kept
in the extracted EML instead of guessed away; the existing renderer handles
presentation whitespace.

Only one line fragment, staged message and parsed message are active at a time.
Oversized inputs are drained, not parsed. Reports spool entries on disk and
publish a valid schema-1 JSON object atomically with counters. Python callers
must also consume lazily: accumulating returned results negates that property.
Source byte limits bound admission, not peak MIME/HTML memory or CPU time.

## Hardening and recovery contract

Source integrity checks use the opened descriptor and the current named path,
with independent initial metadata baselines. They run before emitting records
and when resuming after each yield. Reads stop at the initially observed file
size. Ordinary append, truncation, rewriting or replacement terminates the
archive rather than silently mixing sources or following a growing spool.
These are best-effort metadata checks, not an immutable snapshot, content-level
concurrency protocol or defense against metadata restoration. Already emitted
results cannot be retracted; callers must still supply immutable exports.

Report append now commits one `(position, counters)` checkpoint only after the
whole entry is written and flushed. A failed/interrupted append leaves an
uncommitted tail; the next append or final publication truncates it. Tests cover
failure during partial write, flush and position capture, with zero/one earlier
entry, followed by valid JSON publication and another successful append.
This checkpoint is in-process only; it is not a durable resume journal.

File creation and receipt append are not one transaction. Ctrl-C between them
can leave a complete output that is absent from the committed report prefix.
The report's counters describe committed receipts, not all surviving files.
Final report publication is a separate atomic replacement: interruption before
replacement keeps the previous report and returns 130 without a traceback.
Hard kills, native crashes and power loss remain outside this recovery claim.

Shared report sanitization now handles all lone surrogates instead of raising
UnicodeEncodeError for those outside surrogateescape's supported range. Existing
round-trip behavior for surrogateescaped UTF-8 is preserved. This repair applies
to ordinary EML reports too; it does not change the underlying email text.

Temporary EML/report storage is private. User-selected outputs are subject to
filesystem permissions; report publication remains last-writer-wins for two
imports targeting the same directory. No version/release pointers were changed.

## Validation evidence and reproducibility

- The first slice added 41 tests. The hardening pass adds 60 more cases across
  framing, report fault injection, actual CLI behavior and platform contracts.
- Seeded differential tests cover 240 messages written/read independently by
  CPython. Comparisons include exact stored bytes, hashes and offsets; the
  stdlib's deliberate removal of one storage blank line is accounted for
  explicitly, not by stripping arbitrary whitespace. Another 240 generated
  mboxrd messages round-trip through an independently implemented quoting rule.
- A 10,000-message test enforces capped reads and confirms the first message is
  yielded after reading only it plus the next envelope, not indexing the archive.
- Local isolated framing/report verification after the hardening fixes:
  **73 passed** on Python 3.13.5. The local harness bypassed package initialization
  to load the actual stdlib-only modules; it is not an end-to-end MIME test.
  The full dependency-backed gates run in GitHub CI.
- The existing three-OS CI matrix now runs the MBOX/report contract suites on
  Linux, macOS and Windows before building/smoking the MCP bundle. One POSIX
  open-file replacement test is explicitly skipped on Windows; a portable
  identity-mismatch test still checks that invariant there. A pre-existing test
  now reads UTF-8 report text explicitly rather than using the Windows locale.
- A rerun of `scripts/benchmark_mbox_stream.py --gib 2` on the first hardening
  commit scanned **2,147,483,864 bytes**, rejected the giant record and recovered
  the following valid message: **9.798 seconds; 3,189,384 peak traced Python
  bytes**. This is a sparse-file framing/offset/overflow probe, not real-mailbox
  MIME throughput, process RSS or representative disk I/O. Times depend on the
  environment. Run with `uv run python scripts/benchmark_mbox_stream.py --gib 2`.
- Synthetic Takeout metadata/attachments, malformed MIME, collision safety,
  dry run and actual latest/structured EML rendering parity remain covered.
  PR Checks and linked CI logs are authoritative for each commit's final outcome.

No user's email was used or uploaded. An authorized real multi-GB Takeout corpus
has not been exercised. Generated testing is useful evidence, not a substitute
for that corpus or proof of universal dialect compatibility.

## Focused next experiments, not blockers for this slice

1. Authorized Takeout samples from multiple export dates and label structures;
   compare record counts, attachment checksums and damaged-message diagnostics.
2. Extend the deterministic differential/quoting tests into shrinking property
   tests and mutation fuzzing for the chosen dialect. Include ambiguous fake
   postmarks, damaged headers and delimiter-adjacent long lines. Do not compare
   against a reference implementation on inputs where its dialect differs.
3. Length-framed dialects require validated endpoints and fixtures before being
   advertised; blindly trusting or ignoring Content-Length is not acceptable.
4. Source-fingerprint-validated resume/checkpointing and a durable journal for
   hard-kill recovery. Reconcile complete-but-unreceipted outputs. Do not describe
   the current suffix behavior or in-process checkpoint as resumability.
5. Profile single-message MIME/HTML amplification and consider subprocess resource
   budgets when converting hostile archives. Preserve existing EML behavior.
6. Broaden discovery/portable-skill copy only when release surfaces can actually
   run the importer; add MCP/UI ingestion deliberately rather than making their
   current per-file size/count contracts accept multi-GB uploads accidentally.
