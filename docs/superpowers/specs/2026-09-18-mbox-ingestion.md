# Issue #103: streaming MBOX ingestion — research and implementation decisions

Reviewed September 18, 2026. This is an implementation/design record, not a
claim of universal MBOX conformance or empirical coverage of every Gmail export.
Public recipe: [Gmail Takeout to Markdown/Cabinet](../../reference/gmail-takeout.md).

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
   MIME parser. Do not copy an entire `mailbox.mbox` into a list/DataFrame.
4. [Benjamin Yolken's first-person Gmail archive analysis](https://yolken.net/blog/six-years-of-emails),
   September 26, 2020. Its Takeout example has a numeric timezone **before** the
   year, X-GM-THRID and X-Gmail-Labels. Decision: include that postmark shape and
   metadata in synthetic tests rather than accepting only timezone-free ctime.
   This example does not prove every Takeout export uses one quoting dialect.
5. [Google's Gmail export guide](https://support.google.com/mail/answer/10016932?hl=en).
   Decision: preserve labels/headers/attachment-aware output and document local
   extraction of the downloaded container. Encrypted messages remain encrypted;
   an export capability is not permission to send private mail to another service.

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

Per-record parser/render failures continue. Framing ambiguity or read/source
mutation errors terminate the archive with an explicit fatal result and preserve
prior outputs. Ctrl-C unwinds generators, cleans only newly created incomplete
output, and lets the CLI publish an interrupted report. Neither native crashes,
SIGKILL, nor power loss are claimed recoverable. Temporary EML/report storage is
private, but normal user-selected outputs remain governed by filesystem policy.

## Validation evidence and reproducibility

- Initial isolated scanner suite: **17 passed**, Python 3.13.5 in the editing
  environment. This does not substitute for the actual EML integration suite.
- A 10,000-message test enforces capped reads and confirms the first message is
  yielded after reading only it plus the next envelope, not indexing the archive.
- `scripts/benchmark_mbox_stream.py --gib 2` reads a sparse synthetic archive
  containing valid / giant newline-free / valid records. Local observation:
  **2,147,483,864 bytes scanned; 9.032 seconds; 3,189,272 peak traced Python bytes**.
  The giant record was rejected and the later record recovered. This is a framing
  and >2 GiB offset/recovery probe, not a real mailbox or MIME throughput/RSS
  benchmark. Times are environment-specific; sparse data is not representative
  disk I/O. Run it with `uv run python scripts/benchmark_mbox_stream.py --gib 2`.
- Integration tests cover synthetic Takeout labels and a real-format attachment,
  malformed MIME recovery, collisions, injected per-message exceptions,
  source preservation, dry run, cancellation, stream-report atomicity and CLI
  result contracts. An existing long Gmail HTML thread fixture is compared with
  standalone EML output to guard against a second rendering implementation.
- Run the complete core/backend/plugin/frontend gates from AGENTS.md. PR check
  results, not this document, are the authority for the latest test outcome.

No user's email was used or uploaded. A real, consented multi-GB Takeout corpus
has not been exercised in this editing session.

## Focused next experiments, not blockers for this slice

1. Authorized Takeout samples from multiple export dates and label structures;
   compare record counts, attachment checksums and damaged-message diagnostics.
2. Grammar/property-based framing fuzzing and differential tests against an
   explicitly chosen dialect implementation. Compare bytes, not only subjects.
   Include fake postmarks, folded headers and delimiter-adjacent long lines.
3. Length-framed dialects require validated endpoints and fixtures before being
   advertised; blindly trusting or ignoring Content-Length is not acceptable.
4. Source-fingerprint-validated resume/checkpointing and a durable JSONL journal
   for hard-kill recovery. Do not describe the current suffix behavior as resume.
5. Profile single-message MIME/HTML amplification and consider subprocess resource
   budgets when converting hostile archives. Preserve existing EML behavior.
6. Broaden discovery/portable-skill copy only when release surfaces can actually
   run the importer; add MCP/UI ingestion deliberately rather than making their
   current per-file size/count contracts accept multi-GB uploads accidentally.
