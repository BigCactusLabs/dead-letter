# Reach & Distribution Plan

_Last reviewed: 2026-09-17_

Make dead-letter discoverable where people already need email-to-Markdown conversion. This is an execution plan, not a claim that every proposed client, catalog, or artifact is already supported.

## Positioning and truthful boundaries

> Turn `.eml` email exports into clean, local, LLM-ready Markdown.

Lead with email normalization, not only MCP. Target `.eml to Markdown`, local email archives, attachment-aware ingestion, and Markdown knowledge bases. MBOX/Gmail Takeout, PST, and MSG are roadmap inputs, not currently supported inputs.

Normal conversion produces Markdown plus attachment metadata. Bundle workflows additionally retain decoded files; retained binaries are not automatically parsed into searchable text. Describe exactly which operation a demo runs.

Local conversion does not mean a cloud-connected agent keeps tool results offline. State the converter's privacy boundary and let the user choose the downstream model. Keep synthetic fixtures separate from private archives.

The project uses [PolyForm Noncommercial 1.0.0](../../LICENSE). Preserve that license in package, image, bundle, skill, and marketplace metadata. Do not label the product unrestricted open source or assume eligibility for a catalog that requires an OSI-approved license. Commercial licensing is a separate maintainer decision; this work does not relicense anything.

## Current state and verification vocabulary

Use these states in issue tracking: **implemented**, **published**, **submitted**, **accepted**, **client-tested**, and **unverified**. They mean different things. A Dockerfile is not a published image; a valid manifest is not a tested installer; registry publication is not acceptance by every downstream catalog.

| Surface | Evidence in this repository / remaining verification |
| --- | --- |
| CLI, Python API, local UI, stdio MCP | Implemented; keep runtime and install tests current |
| PyPI / Homebrew tap | Existing distribution paths; verify each release and distinguish core-only Homebrew from optional UI/MCP extras |
| Official MCP Registry | Publication workflow and `server.json` implemented; verify the released version's registry response after publication |
| Claude plugin | Existing plugin and release workflow; this is the BCL marketplace, not a claim of first-party catalog acceptance |
| Glama / other aggregators | Glama was observed in the earlier pass; record a dated listing URL and release version before marking any listing current |
| Agent install guide | `llms-install.md` added in #106; includes client-specific schemas and non-destructive setup guidance |
| GitHub / VS Code, Cline, Cursor, curated lists | Separate submission and client-validation work in #104; not implied by Official Registry publication |
| MCPB desktop bundle | Proposed in #107; no bundle is shipped by #106 |
| OCI/GHCR / Docker Catalog | Dockerfile exists; publishing, mounts, provenance, and catalog acceptance remain #108 work |
| Portable skill / ARD | Proposed in #109; the existing Claude-only skill is not a tested cross-client skill |
| MBOX | Proposed in #103; not implemented by the reach PR |
| Demo / workflow recipes | #105; favor a real, reproducible conversion over promotional claims |

No reliable acquisition baseline has been collected in this review. Priority below is a product hypothesis, not measured traffic or conversion data.

## Execution order

| Priority | Deliverable | Completion evidence |
| --- | --- | --- |
| Now | Repair runtime/onboarding regressions | #102 MCP error-contract fix passes; #106 instructions and metadata pass CI; publishing changes reviewed |
| Now | Sample-first proof + two useful recipes (#105) | Public synthetic `.eml`, real generated Markdown, attachment bundle where claimed, copyable commands |
| Next | Client setup and selected listings (#104) | Fresh client installation, four tools discovered, fixture conversion succeeds, listing URL/status recorded |
| Next | MBOX ingestion (#103) | Bounded-memory archive processing, stable identities, labels, per-message failures, repeatable tests |
| Next | One packaging pilot (#107 or #108) | Clean-machine install and conversion on the chosen host; only then broaden platform coverage |
| Later | Portable skill, then ARD (#109) | Reviewed/pinned skill works in named hosts; discovery tested against an explicitly configured registry |

Do not block useful `.eml` recipes on MBOX or a desktop bundle. Conversely, do not market unsupported formats simply to capture their search queries.

## Install and publication contracts

Quick CLI trial, with a user-selected input and a separate output directory:

```bash
uvx --python 3.12 dead-letter convert message.eml --output converted/
```

MCP launch:

```bash
uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
```

These avoid a global package install but are **not zero-persistence**: uv caches tools/dependencies and may download Python. Unpinned commands do not promise a fresh latest version on every run. Pin the reviewed package version for deployments; record a resolved dependency lock/artifact for full reproducibility. See [uv's tool documentation](https://docs.astral.sh/uv/guides/tools/).

Use [the agent installation guide](../../llms-install.md) rather than guessing a universal configuration schema. Merge settings without removing other MCP servers. Keep write destinations explicit, preserve source email, and distinguish host paths from sandbox/container paths.

Before release, validate the registry description length, identity marker, package/version pins, Python selector, and executable. #106 adds offline regression checks for these known failure modes; they are not a replacement for the publisher's complete schema validation.

**Dependency warning:** pinning `dead-letter==0.2.5` alone does not freeze its MCP dependency. The SDK error-visibility regression is fixed on #102, not in an already published package merely because this document exists. Ship and verify the runtime fix through the normal maintainer-owned release process.

## Client-native distribution (#104)

### GitHub / VS Code / Copilot

VS Code supports `@mcp` gallery discovery and separate workspace/user configuration. Its `mcp.json` uses a `servers` map; Claude Desktop-style configuration uses `mcpServers`. The install guide now shows both. [Official VS Code documentation](https://code.visualstudio.com/docs/agent-customization/mcp-servers).

Check the curated catalog independently. Direct configuration is still useful when listing acceptance is pending. Do not infer that every Copilot host supports identical transport, configuration scope, or gallery behavior.

### Cline, Cursor, and curated directories

Retain Cline marketplace, Cursor deep-link/community-directory, `punkpeye/awesome-mcp-servers`, Smithery, mcp.so, and mcpservers.org as candidates. At submission time read each current contribution policy, license criteria, and local-stdio support. A community directory must not be represented as a first-party marketplace.

Do not submit a remote service URL for this stdio server. Do not add hosted email processing merely to satisfy a directory. Avoid copying manifests by hand; generate or test configuration against `server.json`.

For each candidate record: policy/source URL, reviewed date, artifact/version, submission URL, status, and the tested client/OS. No automated mass submissions, unsolicited repository comments, or duplicate entries.

## Desktop bundle and container pilots

### MCPB (#107)

The official MCPB repository includes a [uv runtime example](https://github.com/modelcontextprotocol/mcpb/tree/main/examples/hello-world-uv). This validates the architectural direction, not dead-letter's compatibility with every client or platform.

Acceptance: validate the manifest with a pinned toolchain, install the release artifact on a clean named desktop-client version, complete `initialize` and `tools/list`, convert a fixture with attachments, and verify uninstall/restart behavior. Record macOS architecture and Windows results separately. Test network-restricted first installation, GUI PATH behavior, runtime downloads, and native dependency wheels. Publish only the combinations actually tested.

### OCI / Docker (#108)

Extend the existing Dockerfile. Publish immutable version/digest references with source, revision, license, and MCP identity metadata. Generate provenance/SBOM where supported; do not confuse an attestation with a security audit.

Test with a non-root user, explicit user-selected mounts, read-only source mail and a separate writable output, writable temporary storage, no unnecessary network port, and graceful stdio shutdown. Check that returned container paths are usable/mappable by the host client. A successful handshake alone does not prove access to the user's files.

A GHCR image and Docker Catalog acceptance are independent deliverables. Follow the current [Docker MCP registry contribution process](https://github.com/docker/mcp-registry); do not assume Docker will ingest a GHCR tag automatically. Docker remains optional for ordinary CLI/MCP use.

## Portable skill and task-level discovery (#109)

Use `skills/dead-letter/SKILL.md` as the proposed portable distribution location; avoid putting an installation-oriented skill in an automatically loaded development directory unnecessarily. Keep Claude-specific slash-command/Cowork conventions in the existing plugin.

The [GitHub CLI manual](https://cli.github.com/manual/gh_skill_install) documents per-host installation and tag/commit pinning. Validate the selected host's behavior, not just its ability to copy `SKILL.md`. Preview skill contents before installation, preserve the untrusted-email rule, and avoid automatic installation or broad filesystem approval merely because a task matches.

GitHub [announced Agent Finder on June 17, 2026](https://github.blog/changelog/2026-06-17-agent-finder-for-github-copilot-now-available/). It searches the registry the user/organization configures and does not automatically install matches. Publishing an ARD document therefore does **not** guarantee public indexing or installation.

Once the skill works, validate metadata against a pinned ARD schema and test natural-language retrieval against a named registry: `read an eml file`, `convert exported email to Markdown`, and `prepare email for RAG`. Record the query/result and registry revision; treat discovery effectiveness as an experiment.

## Additional non-catalog avenues

### A useful sample page, not another feature list

Publish a synthetic input/output pair that visitors can inspect without installing anything. Include thread attribution, a retained attachment manifest, and a plain-text fallback case. Offer a local reproduction command. Do not require users to upload private mail to a demo server.

### Integration recipes that can earn references

Start with `.eml -> Markdown/Obsidian` and `.eml -> RAG preprocessing`. Show the actual file layout, front matter, attachment boundary, and a worked query. Then add Cabinet/auditing and an agent workflow. A small Python loader example is preferable to maintaining full integrations for every RAG framework before there is demand.

A BCL Field Note can explain a real parser edge case and link to the fixture, regression test, and recipe. Reuse the same canonical example across README, documentation, and launch copy; do not invent adoption statistics.

### Benchmark credibility as distribution

Keep comparisons reproducible and versioned. Report input corpus, tokenizer, body/thread fidelity, sender attribution, links, and retained attachments. Token reduction against raw base64 email does not establish superior downstream answer quality, and externalized attachment contents are not represented by the Markdown token count. Avoid universal superiority claims from a synthetic corpus.

### Input-format expansion as a separate product bet

For #103, stream messages through the existing converter, preserve Gmail labels and source identity, bound memory, surface partial failures, and prevent duplicate output on retry. Add a large-mailbox test plus malformed/duplicate/message-with-attachment fixtures. Evaluate PST/MSG separately; neither is promised by the existing `.eml` converter.

## Measurement without private-email telemetry

Track dated catalog acceptances, reproducible setup results, release downloads, meaningful bug reports, and inbound workflow requests. Downloads and CI/container pulls are not unique users. Record available aggregate GitHub traffic snapshots before they expire; do not add email-content telemetry for acquisition measurement.

Keep a small channel ledger with baseline, artifact/recipe, submission date, verified listing, user-reported outcomes, and next decision. After a few real observations, reprioritize the packaging and content hypotheses rather than accumulating more integrations automatically.

## Canonical submission copy

**Name:** dead-letter

**Description:** Convert `.eml` email exports to clean Markdown for RAG, LLM pipelines, and local knowledge bases.

**Repository:** https://github.com/BigCactusLabs/dead-letter

**License:** PolyForm Noncommercial 1.0.0; commercial use requires separate permission under the project's licensing terms.

Use the tested versioned install contract when a catalog requires one. Do not claim MBOX support, native desktop bundles, OCI availability, or cross-client skill compatibility until those artifacts exist and their acceptance checks pass.
