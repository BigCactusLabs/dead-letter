---
title: html-to-markdown v3 Migration Plan
doc_type: reference
status: completed
last_updated: 2026-05-25
audience:
  - maintainers
scope:
  - src/dead_letter/core/html.py
  - src/dead_letter/core/quotes.py
  - src/dead_letter/core/_pipeline.py
---

# html-to-markdown v3 Migration Plan

This document defines the migration from `html-to-markdown` 2.x to 3.x.

## Current State

- Runtime now depends on `html-to-markdown>=3.1.0,<4.0`.
- `src/dead_letter/core/quotes.py` uses DOM-based quote-pattern detection and no longer imports `convert_with_visitor`.
- `src/dead_letter/core/html.py` calls the internal adapter in `src/dead_letter/core/html_to_markdown_adapter.py`.
- Core and backend suites have been validated against `html-to-markdown==3.1.0`
  and the locked `html-to-markdown==3.4.0`.

## Migration Goal

Upgrade to `html-to-markdown` v3 without changing user-facing conversion contracts:

- No breaking change to output schema (`message.md` front matter/runtime contracts).
- Existing quote/client-hint behavior remains at parity or improves measurably.
- Core and backend suites remain green.

## Strategy

1. Decouple quote detection from `html-to-markdown` visitor APIs.
2. Keep markdown conversion behind an internal adapter boundary.
3. Validate parity and resilience before removing the `<3.0` guardrail.

## Completion Notes

- Quote detection now traverses sanitized HTML with `selectolax` and preserves
  the existing `gmail`, `outlook`, `yahoo`, `generic`, `thunderbird`, and
  `apple_mail` signals.
- The conversion adapter normalizes v2 string results, early v3 dict results,
  and current v3 `ConversionResult.content` results into the string expected by
  the pipeline.
- The guardrail has been removed in favor of `html-to-markdown>=3.1.0,<4.0`.

## Implementation Plan

### Phase 1: Decouple quote detection

- Replace `convert_with_visitor` usage in `src/dead_letter/core/quotes.py` with DOM-based scanning using `selectolax`.
- Preserve existing rule signals:
  - `gmail`, `outlook`, `yahoo`, `generic`, `thunderbird`, `apple_mail`
- Keep `detect_quote_patterns(html: str) -> set[str]` public behavior unchanged.
- Re-run/adjust quote and HTML tests to assert unchanged outputs.

Exit criteria:

- `tests/core/test_quotes.py` and `tests/core/test_html.py` pass with identical expected pattern sets.
- No import of `convert_with_visitor` remains in source.

### Phase 2: Introduce conversion adapter

- Add an internal adapter module wrapping `html-to-markdown` calls (single entrypoint for convert options/output).
- Move direct `ConversionOptions`/`convert` references behind this adapter.
- Keep `dead_letter.core.html.html_to_markdown(...)` behavior unchanged for callers.

Exit criteria:

- `src/dead_letter/core/html.py` depends on internal adapter only.
- Core regression and panic-repair tests still pass.

### Phase 3: v3 trial and compatibility checks

- In isolated env, run:
  - `uv run --with html-to-markdown==3.1.0 pytest -q tests/core tests/backend`
  - `uv run --with html-to-markdown==3.4.0 pytest -q tests/core`
  - `uv run --with html-to-markdown==3.4.0 pytest -q tests/backend`
- Fix API or behavior differences in adapter only (avoid broad pipeline rewrites).
- Keep diagnostics semantics stable (`html_markdown_failed`, repair/fallback behavior).

Exit criteria:

- Core + backend suites green under v3 trial.
- No contract doc updates required for end users.

### Phase 4: Remove guardrail and finalize

- Update dependency constraint from `<3.0` to `>=3.1.0,<4.0`.
- Refresh lockfile.
- Add changelog entry declaring v3 migration complete.

Exit criteria:

- Standard CI jobs pass on locked v3 dependency.
- Dependency refresh workflow no longer reintroduces v2-only assumptions.

## Risks and Mitigations

- Risk: quote-pattern drift from visitor removal.
  - Mitigation: preserve existing tests and add fixtures for Gmail/Outlook/Yahoo cite patterns.
- Risk: markdown output diffs in edge HTML.
  - Mitigation: use adapter-layer normalization and quality regression tests before rollout.
- Risk: platform packaging gaps for newer v3 releases.
  - Mitigation: first target `3.1.0`, then expand once wheel/sdist support is confirmed for supported runtimes.

## Rollback Plan

If v3 migration fails at any phase:

- Keep dependency guardrail `html-to-markdown>=2.9.1,<3.0`.
- Revert adapter changes behind the boundary while preserving quote-detector decoupling if already validated.
- Re-open migration from the last completed phase gate.
