# Reach & Distribution Plan

_Last reviewed: 2026-09-19_

Make dead-letter discoverable where people already need email-to-Markdown
conversion. This is a product/discovery plan, not a second installation guide
or a claim of acceptance by every catalog. Use the
[distribution map](../reference/distribution.md) for routes and
[Publishing](../reference/publishing.md) for release work.

## Positioning and truthful boundaries

> Turn `.eml` email exports into clean, local, LLM-ready Markdown.

Lead with email normalization, readable archives, attachment-aware ingestion,
and Markdown knowledge bases—not only MCP. MBOX/Gmail Takeout, PST, and MSG
remain separate format-expansion work, not shipped capabilities merely
because an issue or PR exists.

Ordinary conversion retains attachment metadata; bundle workflows additionally
retain decoded files. Retained binaries are not automatically searchable
text. Local conversion does not make a cloud-connected agent offline. Use
synthetic samples, and describe the exact workflow a demo runs.

Keep [PolyForm Noncommercial 1.0.0](../../LICENSE) intact across channels.
Technical compatibility does not establish eligibility for an OSI-only
catalog or unrestricted commercial use.

## Current state and verification vocabulary

The September 17 plan's packaging pilots have advanced. Do not keep treating
MCPB, portable skills, or OCI as merely proposed when their implementations
are now in the repository. Equally, implementation is not blanket client or
catalog acceptance.

| Surface | Established repository state | Remaining evidence |
| --- | --- | --- |
| CLI, Python API, local UI, stdio MCP | Implemented runtime and tests | Release-specific installed behavior |
| PyPI / Homebrew | Package workflow and separate core-only tap route | Per-release package and formula validation |
| Official MCP Registry | Publication workflow with package/artifact metadata | Exact registry version after each publication |
| Claude plugin | BCL marketplace release workflow, exact source and package pins | Code/Cowork update and fixture tests separately |
| MCPB (#107) | Bundle source, release assets, cross-platform stdio smoke pipeline | Named desktop-client GUI installation/update results |
| OCI / GHCR (#108) | Multi-platform workflow; first public image recorded as 0.3.1 | Docker Catalog submission/outcome and actual discovery |
| Portable skill / ARD (#109) | Portable source, catalog, CI validation | Named-host usability and domain-anchored discovery |
| Client-native listings (#104) | Setup and submission guidance | Dated submission, acceptance, and client evidence |
| MBOX (#103) | Separate research/implementation effort | Merge, bounded ingestion tests, and released support |
| Demo / recipes (#105) | Product-discovery workstream | Reproducible sample output and useful integration recipes |

Use **implemented**, **published**, **submitted**, **accepted**,
**client-tested**, and **unverified** distinctly. Record date, version,
commit/digest, URL, client/OS, and what was actually exercised. A GitHub file
is not a public image; a generated catalog entry is not a listing; a green
MCPB subprocess smoke is not a tested extension installer.

## Execution order

First close release/onboarding gaps across existing channels, then make a
sample-first path useful. Avoid accumulating new integrations while current
ones lack clear installation and update evidence.

| Priority | Deliverable | Completion evidence |
| --- | --- | --- |
| Now | Consistent docs and repeatable multi-channel releases | Metadata/link gates, immutable artifact recovery, explicit plugin/tap handoff |
| Now | Sample-first proof and two recipes (#105) | Public synthetic `.eml`, real Markdown, bundle when claimed, reproduction commands |
| Next | Focused client/listing work (#104, #108, #109) | Named fresh install, four tools, fixture conversion, actual listing status |
| Next | MBOX ingestion (#103) | Bounded memory, stable identity, labels, partial failure and retry tests |
| Later | Broader catalogs/formats based on observed use | Evidence of user need, policy fit, maintainable release/update path |

No acquisition baseline has been established by this audit. Priorities are
product hypotheses, not traffic or conversion measurements.

## Client-native distribution

VS Code uses a `servers` map; Claude-style JSON uses `mcpServers`. Use the
[agent install guide](../../llms-install.md) instead of copying one universal
configuration. GitHub/VS Code gallery acceptance is separate from direct MCP
configuration and from publication in the Official Registry.

Cline, Cursor community directories, Smithery, mcp.so, mcpservers.org, and
curated lists remain candidates to evaluate individually. Read each current
license policy, local-stdio support, and update behavior at submission time.
Do not invent a remote service URL for a local stdio server, add hosted email
processing to qualify for a directory, or make automated mass submissions.
Record submission URLs and policy dates; do not imply first-party endorsement
for a community entry.

[Containers](../reference/containers.md) owns Docker build/mount/provenance
contracts and catalog submission. A generated candidate, locally imported
catalog, submitted PR, and accepted public listing are different states.
[Agent Discovery](../reference/agent-discovery.md) owns portable-skill pinning,
ARD's hosting limitation, and Agent Finder submission. Root-hosted source on
GitHub is not automatically discoverable through a domain-anchored crawler.

## Useful content before more channels

### A sample, not another feature list

Publish a synthetic input/output pair inspectable without installation: a
thread with attribution, retained attachment manifest, and plain-text fallback
case. Include local reproduction commands; do not ask people to upload
private mail to a demo server. Reuse the same example in README, docs, and
launch copy rather than inventing separate claims for each channel.

### Recipes that earn references

Start with `.eml → Markdown/Obsidian` and `.eml → RAG preprocessing`. Show real
file layout, front matter, the attachment boundary, and a worked query. Add
Cabinet/auditing and an agent workflow next. A small Python loader is more
maintainable than many framework integrations before demand is demonstrated.
A field note about a real parser edge case can link the fixture, regression
test, and recipe; do not invent adoption statistics.

### Benchmark credibility

Keep [benchmarks](../../benchmarks/README.md) reproducible and versioned.
Report corpus, tokenizer, thread mode, sender attribution, links, and exactly
what attachment retention means. Token savings against raw base64 mail do
not establish downstream answer quality, and a synthetic corpus does not
prove universal superiority. Binary attachment content is outside Markdown's
token count. Preserve the rows where naive extraction is cheaper.

### Format expansion is a separate product bet

For MBOX, stream through the existing converter with bounded memory, stable
source identity, Gmail-label preservation, per-message failures, and safe
retry/deduplication behavior. Test large mailboxes, malformed messages,
duplicates, and attachments. Evaluate PST/MSG separately; do not let search
keywords become unsupported product promises.

## Measurement without private-email telemetry

Track dated acceptances, reproducible setup results, release downloads,
meaningful bugs, and inbound workflow requests. Downloads/CI pulls are not
unique users. Aggregate GitHub traffic snapshots can help, but this plan does
not authorize adding email-content telemetry.

Use one small channel ledger with baseline, artifact/recipe, submission date,
verified listing, user-reported outcome, and next decision. Reprioritize from
observed use rather than continuing every packaging experiment automatically.

## Canonical submission copy

**Name:** dead-letter

**Description:** Convert `.eml` email exports to clean Markdown for RAG, LLM
pipelines, and local knowledge bases.

**Repository:** https://github.com/BigCactusLabs/dead-letter

**License:** PolyForm Noncommercial 1.0.0; commercial use requires separate
permission under the project's licensing terms.

Use a tested, versioned install contract. Do not claim an input format,
platform, listing, or license approval without the corresponding evidence.
The [docs/release audit](docs-release-audit.md) records the September 19
maintenance pass; release notes and linked issues retain channel outcomes.
