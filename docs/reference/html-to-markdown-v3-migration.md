---
title: html-to-markdown v3 Migration Plan
doc_type: reference
status: completed
last_updated: 2026-09-19
audience:
  - maintainers
scope:
  - src/dead_letter/core/html.py
  - src/dead_letter/core/quotes.py
  - src/dead_letter/core/_pipeline.py
---

# html-to-markdown v3 Migration Plan

Completed migration record, retained at its original URL. The implementation
phases below are history, not a request to restart the migration. For current
behavior, use the [runtime contract](v4-runtime-contracts.md).

## Current State

- Runtime depends on `html-to-markdown>=3.1.0,<4.0`.
- `src/dead_letter/core/quotes.py` uses DOM-based quote detection rather than
  importing `convert_with_visitor`.
- `src/dead_letter/core/html.py` calls the internal
  `src/dead_letter/core/html_to_markdown_adapter.py` adapter.
- `uv.lock` is the authority for the resolved dependency version; do not copy
  a moving lock version into this completed migration record. Compatibility
  checks still include the lower bound `html-to-markdown==3.1.0`.

## Migration Goal

Upgrade to v3 without changing user-facing conversion contracts: retain the
output schema, preserve or improve quote/client-hint behavior, and keep core
and backend regression suites green.

## Strategy

Decouple quote detection from visitor APIs, isolate Markdown conversion behind
an adapter, and validate parity/resilience before replacing the old `<3.0`
guardrail. The current runtime range is `>=3.1.0,<4.0`.

## Completion Notes

Quote detection traverses sanitized HTML with `selectolax`, preserving the
existing Gmail, Outlook, Yahoo, generic, Thunderbird, and Apple Mail signals.
The conversion adapter normalizes older string/dict results and v3
`ConversionResult.content` into the pipeline's string contract. This adapter
boundary remains useful for future dependency changes.

## Implementation Plan

The following phases document the completed work.

### Phase 1: Decouple quote detection

Replace visitor usage in `quotes.py` with DOM scanning. Preserve
`detect_quote_patterns(html: str) -> set[str]` and the provider signals.
Exit evidence: quote/HTML tests preserve expected pattern sets, with no
`convert_with_visitor` import remaining in runtime source.

### Phase 2: Introduce conversion adapter

Route direct conversion options/calls through the internal adapter. Preserve
`dead_letter.core.html.html_to_markdown(...)` for callers and keep dependency
API differences out of the rest of the pipeline.

### Phase 3: v3 trial and compatibility checks

Exercise the lower bound and locked context independently:

```bash
uv run --with html-to-markdown==3.1.0 pytest -q tests/core tests/backend
uv run pytest -q tests/core
uv run pytest -q tests/backend
```

Keep diagnostics semantics stable, including `html_markdown_failed` and the
explicit repair/fallback paths. A trial dependency override is not a request
to rewrite the committed lockfile.

### Phase 4: Remove guardrail and finalize

Replace the v2 constraint with `>=3.1.0,<4.0`, refresh the reviewed lock,
record completion, and ensure dependency-refresh automation does not restore
v2-only assumptions.

## Risks and Mitigations

Provider quote drift is covered by fixtures; edge HTML/output changes by
adapter and panic-repair regression tests. Platform wheel/sdist compatibility
still needs testing when dependency versions change. Completion of this
migration is not evidence that every later upstream version is compatible.

## Rollback Plan

Narrow the v3 range or pin the last known-good v3 release, refresh `uv.lock`,
and retain the adapter while fixing the regression. Run lower-bound and
locked-version checks before widening the range. Do not silently roll back
to the old architecture based on a completed phase checklist.
