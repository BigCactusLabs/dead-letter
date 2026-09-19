# Documentation and release-flow audit

Reviewed September 19, 2026, against main commit
`350a2be2dcbd33661b5d0b1b721fac5c6a898a90`.
Implementation and validation record: [PR #123](https://github.com/BigCactusLabs/dead-letter/pull/123).

## Scope and outcome

This pass audits the reader/developer/maintainer entry points, documentation
layout, installation claims, discovery guidance, version-sync surfaces, and
package/plugin release topology. Runtime and frontend contract references
remain canonical; this is not a claim that every GUI instruction or every
runtime sentence was re-executed on every supported client.

The README retains its logo, headings, practical examples, fidelity-per-token
positioning, and “Tools We Love.” Installation navigation improves without
turning its opening into a release checklist. The docs index routes by task;
the distribution map owns channel choices; Publishing owns release mechanics.
No branding assets, public runtime APIs, or package versions change.

## Findings addressed

| Finding | Change |
| --- | --- |
| Every published GitHub release could enter package publication; version checks were downstream of PyPI | Read-only stable-package preflight before publishing, exact tag/source checks, locked source tests |
| Version relationships spread across prose and manual lists | Stdlib metadata checker and reviewable dry-run version patch; independent plugin sequence retained |
| Plugin tag identity/ordering could be checked too late | Peeled commit identity, ancestry check before marketplace writes, serialized workflow and exact PyPI availability |
| A retry could replace a bundle whose hash was already advertised | Reuse published bytes, compare existing assets, refuse replacement, archive resolved registry metadata |
| MCP publisher resolved an unpinned latest executable | Reviewed version plus archive SHA-256 |
| Maintainer handoff blurred package/plugin/tap/catalog updates | Explicit event table, completion ledger, partial-failure recovery, manual core-only Homebrew handoff |
| Agent guide referenced removed docs directories | Canonical `docs/reference`, `docs/project`, and `docs/brand` map |
| New distribution docs escaped the old link inventory | Tracked root/plugin/skill/bundle/container/benchmark inventory; regression executes the real command |
| Current pins and channel availability were duplicated in prose | Link to reviewed releases/metadata, distinguish source templates from published artifacts |
| `gh skill install` latest assumed a package tag | Explicit skill-name tag/commit pin guidance, independent plugin-tag caveat |
| Onboarding and reproducibility claims exceeded the evidence | Correct UI setup gating, registration vs handshake, uv persistence/downloads, top-level vs transitive pins, checksum vs GUI verification |
| Reach plan still called shipped implementations proposed pilots | Refresh state matrix and keep catalog/client acceptance separate |
| Benchmark copy implied a universal fidelity/token result | Scope to measured representations and synthetic corpus; distinguish attachment names from binary content |

## Validation contract

`release-check` runs the source metadata check and stdlib helper regressions.
The new tests cover mismatched sync points, malformed tags/pins, independent
plugin versions, monotonically increasing preparation, `git apply` patches,
bounded PyPI readiness, and immutable asset behavior. Workflow tests cover
preflight order, resolved-manifest archival, plugin ordering, and doc inventory.
The backend inventory regression now executes the actual workflow's command
against a tracked fixture repository, including exclusions and an untracked
file; it no longer asserts a removed directory glob.

The PR records actual CI outcomes for core/backend/plugin/frontend, maintained
links, MCPB on three operating systems, and native container architectures.
Do not treat a published workflow file or this document as proof of a passing
run. Local helper tests do not require app dependencies; full repository and
packaging checks run in GitHub CI for this change.

## Deliberate boundaries and follow-through

No tag, release publication, marketplace pointer, tap formula, registry
listing, or catalog enrollment is changed by this audit. The production
publication path still needs its next authorized release rehearsal; no
credentialed publish is performed just to test a docs PR. New registry
manifest assets apply to future releases using the workflow, not older ones.

Desktop extension installation/update and separate Code/Cowork caches require
named-client checks. Docker Catalog and Agent Finder acceptance remain
external outcomes. ARD's owned-domain hosting gap is not solved by enabling
GitHub Pages or pretending a raw GitHub URL is domain-anchored. Homebrew
automation and a future prerelease publication policy remain separate work.

The completed HTML-to-Markdown migration stays at its original URL for
history. Detailed runtime/state/brand references are not mechanically renamed
or flattened. Keep their implementation-specific content with the code it
describes and update it when that behavior changes.

## Maintenance rule

Add new channels to the distribution map, version checks, workflow contracts,
and completion ledger together. Link rather than copy current pins. Preserve
historical evidence, mark implementation/publication/acceptance separately,
and keep the README a product introduction rather than a status dashboard.
