# Reach & Distribution Plan

_Last updated: 2026-09-17_

This document tracks how dead-letter gets discovered, installed, and recommended. The goal is not generic promotion; it is to make dead-letter show up wherever someone is already expressing the problem it solves.

## Positioning

Primary promise:

> Turn `.eml` email exports into clean, local, LLM-ready Markdown.

The strongest discovery intents are:

1. `eml to markdown` / `email to markdown`
2. `email archive markdown` / `email archive obsidian`
3. `email rag` / `email llm ingestion`
4. `eml parser llm` / `email parser markdown`
5. `email mcp server` / `mcp email archive`
6. `digital preservation email markdown`

Avoid positioning dead-letter as only an MCP server. MCP is an important distribution surface, but the underlying job is broader: reliable email normalization and archival.

## Current distribution surface

| Surface | Status | Notes |
| --- | --- | --- |
| GitHub | Live | Primary source and documentation surface |
| PyPI | Live | `dead-letter`; release provenance and Trusted Publishing already in place |
| Homebrew | Live | BigCactusLabs tap; core CLI |
| Official MCP Registry | Automated | `server.json` publishes from the release workflow after PyPI |
| Glama | Live | Indexed; ownership can still be claimed |
| PulseMCP / downstream registry consumers | Indirect | Official MCP Registry is the canonical upstream; verify propagation after releases |
| Claude plugin marketplace | Live | Dedicated plugin with convert/summarize/triage/cabinet commands |
| Claude Desktop / Code | Live | Direct local MCP via `uvx` |
| Codex | Live | Direct local MCP via `uvx` |
| Agent-readable install guide | Added in #106 | `llms-install.md` gives agents a short deterministic install path |
| GitHub MCP Registry / VS Code MCP Gallery | Missing / curated | Official Registry publication is necessary but GitHub's curated catalog may still require onboarding; acceptance would expose `@mcp` discovery in VS Code/Copilot |
| GitHub Agent Finder / ARD | Missing | New task-level discovery surface: Copilot can search for MCP servers, tools, agents, and skills by natural-language need |
| Cline Marketplace | Missing | First-party Cline catalog; supports one-click MCP installation and explicitly accepts README / `llms-install.md` based setup |
| Cursor community directory / plugin | Missing | `cursor.directory` accepts GitHub-backed plugins and auto-detects `.mcp.json` and `skills/*/SKILL.md` |
| Cursor one-click install link | Missing | Can be generated directly from the stdio MCP config without marketplace acceptance |
| VS Code one-click install link | Missing | `vscode.dev/redirect/mcp/install` can install a local MCP configuration directly |
| MCPB bundle | Missing | One-click local bundle format used by Claude Desktop; official MCP Registry can publish MCPB release artifacts |
| Docker MCP Catalog / Toolkit | Missing | Existing Dockerfile makes this unusually low-friction; accepted servers surface in Docker Desktop MCP Toolkit and Catalog |
| GHCR / OCI MCP artifact | Missing | Official MCP Registry supports OCI packages from GHCR/Docker Hub; useful for container-first clients and Docker Catalog submission |
| Portable Agent Skill | Missing | Claude-specific skill exists today; a generic Agent Skills package could reach Copilot, Codex, Claude, Cursor and other skill-aware hosts |
| punkpeye/awesome-mcp-servers | Missing | Large curated GitHub discovery surface |
| Smithery | Not confirmed | Publish/index local stdio package if absent |
| mcp.so | Not confirmed | Separate directory submission if absent |
| mcpservers.org | Not confirmed | Separate directory submission if absent |
| Product demo asset | Missing | No 20–30 second visual demo at the top of the funnel |
| Integration recipes | Thin | Obsidian, local RAG, and archive workflows should have dedicated examples |
| GitHub topics / social preview | Manual gap | Add high-intent repository topics and a clear social preview image so shared links carry the product story |

## Highest-leverage next moves

### 1. Win the non-MCP search intent

MCP directories are useful, but people usually start with the job rather than the protocol. Keep the README, PyPI description, registry metadata, GitHub topics, and external field notes centered on:

- `.eml` → Markdown
- local email archive
- RAG / LLM-ready email
- attachment-aware conversion
- thread preservation
- local-first / no-upload processing

Package/repository metadata should cover `email-archive`, `email-parser`, `email-export`, `mime`, `llm`, `rag`, `mcp`, `digital-preservation`, `knowledge-base`, `local-first`, and `yaml-frontmatter` in addition to generic converter terms.

### 2. Make the first successful run one command

Preferred zero-persistence trial:

```bash
uvx --python 3.12 dead-letter convert message.eml
```

Preferred MCP launch:

```bash
uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
```

These should appear before source-checkout instructions. Pin `--python 3.12` so users with older default interpreters do not fail dependency resolution.

`llms-install.md` should remain intentionally short and deterministic so Cline and other agents can install dead-letter without parsing the full README.

### 3. Turn MCP into multiple install artifacts, not one protocol listing

The existing stdio MCP server is already a distribution primitive. Package it in the forms current clients actually discover.

#### MCPB — high priority

MCP Bundles (`.mcpb`) give local servers a one-click desktop installation path. The format supports `uv`-managed Python servers, so dead-letter does not need to become a Node project or bundle a private Python runtime.

Target:

- build a release `.mcpb` artifact from the existing MCP server
- attach it to GitHub Releases
- include its SHA-256 in `server.json`
- publish the MCPB package alongside the PyPI package in the Official MCP Registry
- document drag/drop or double-click installation for Claude Desktop

This is a conversion win as much as a distribution win: non-developer users no longer need to edit JSON or know what `uvx` is.

#### OCI / Docker — high priority

The repository already has a dedicated stdio MCP Dockerfile. Extend that path instead of inventing a container architecture:

- add the MCP registry identity OCI label to the image
- publish versioned images to GHCR on release
- optionally add the OCI package to `server.json`
- submit the image/server metadata to Docker's MCP Registry

Docker acceptance would surface dead-letter in Docker Desktop's MCP Toolkit and MCP Catalog. It also adds isolation and provenance signals that are useful for users evaluating a local email parser.

### 4. Capture client-native marketplaces and deep links

Do not assume Official MCP Registry publication reaches every curated client catalog.

#### GitHub / VS Code / Copilot

GitHub has a curated MCP Registry used by Copilot and the VS Code `@mcp` gallery. Check whether dead-letter is present after the next Official Registry publish. If not, use the current GitHub onboarding path for OSS MCP servers.

This matters beyond VS Code: GitHub's registry/spec surfaces are consumed across Copilot-capable IDE and CLI experiences.

Also add a direct VS Code install link so people do not need to wait for curated catalog acceptance.

#### Cline

Submit to Cline's first-party marketplace after validating that Cline can install the server from the README / `llms-install.md`. The repo now has the short install guide Cline explicitly recommends for deterministic setup.

#### Cursor

Two paths are useful:

1. add an official Cursor MCP deep link to the README for immediate one-click install;
2. prepare a community plugin submission for `cursor.directory`.

Cursor's community plugin format can auto-detect a root `.mcp.json` and `skills/*/SKILL.md`. If we add those surfaces, keep them generated or tested against the canonical MCP/skill definitions so they do not drift.

### 5. Add a portable Agent Skill distribution path

Agent Skills have become a separate discovery/install ecosystem from MCP. GitHub Copilot can search/install/publish skills with `gh skill`, and Codex/Claude/Cursor support the same `SKILL.md` convention.

The existing `plugin/skills/dead-letter-context/SKILL.md` proves the workflow, but it is Claude-plugin-specific. Create a portable skill whose job is narrower:

- recognize `.eml` / email archive conversion tasks
- install or invoke dead-letter locally with `uvx --python 3.12`
- prefer the MCP server when already configured
- preserve the untrusted-email-content safety rule
- never require cloud upload

Publish it in a standard `skills/dead-letter/` or `.agents/skills/dead-letter/` shape that can be installed by skill-aware agents. Avoid copying the Claude-only slash-command behavior into the portable version.

This gives dead-letter two ways to be discovered by an agent: as a tool server and as procedural capability metadata.

### 6. Experiment with Agentic Resource Discovery (ARD)

ARD is an emerging federated discovery layer for MCP servers, tools, agents, and skills. GitHub Copilot's Agent Finder already searches this class of resource by natural-language task rather than exact package name.

Explore publishing a dead-letter ARD catalog entry once the portable MCP/skill surfaces are stable. The target query is not "dead-letter"; it is intent such as:

- "convert exported email to markdown"
- "prepare email for RAG"
- "read an eml file"
- "build a local email archive"

If GitHub Agent Finder onboarding remains curated, publish a standards-compliant first-party catalog anyway so other ARD consumers can index it.

### 7. Build a visual proof asset

Create one short GIF/video that shows:

1. a messy `.eml` or folder of messages,
2. one conversion command or drag/drop,
3. the clean Markdown + front matter,
4. extracted attachments / diagnostics,
5. optional agent use through MCP.

The asset should be understandable with audio off and work in the GitHub README, the BCL site, launch posts, social previews, and directory listings.

### 8. Publish concrete recipes instead of generic feature copy

High-intent recipes:

- Convert an exported message folder into an Obsidian vault
- Prepare a folder of `.eml` files for RAG / embeddings
- Give Claude, Codex, Copilot, Cursor, or Cline safe local access to email exports through MCP
- Build a durable local email archive with Cabinet mode
- Audit a conversion run using the JSON report and quality diagnostics

Each recipe should have a copy/paste command, expected output layout, and a short explanation of why dead-letter preserves more useful structure than naive text extraction.

### 9. Expand upstream input formats

The largest product-led reach opportunity is still input format coverage.

#### MBOX — highest priority

Google Takeout commonly exports Gmail mailboxes as MBOX, not individual `.eml` files. Supporting MBOX would let dead-letter own the larger intent cluster around:

- Gmail Takeout → Markdown
- Gmail archive → Obsidian
- Gmail Takeout → RAG / LLM
- MBOX → Markdown

A good implementation should stream messages rather than loading a multi-gigabyte mailbox into memory, preserve `X-Gmail-Labels`, expose per-message diagnostics, and feed each extracted RFC 822 message through the existing conversion pipeline.

#### Outlook archive formats — follow-on

Outlook bulk export commonly uses PST, while individual messages can be saved/downloaded as EML or MSG. PST/MSG support would expand Windows and enterprise archival use cases, but MBOX is the cleaner first reach multiplier.

### 10. Keep the Official MCP Registry canonical

The release workflow already publishes `server.json` to the Official MCP Registry. Keep this path authoritative and add package forms (PyPI, MCPB, OCI) to the same server identity rather than creating competing registrations.

For directories that require separate submission, link back to the GitHub repo and use the same one-sentence description.

## Directory / marketplace submission copy

**Name:** dead-letter

**Short description:**

> Convert `.eml` email exports to clean Markdown for RAG, LLM pipelines, and local knowledge bases.

**MCP launch:**

```bash
uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
```

**Repository:** https://github.com/BigCactusLabs/dead-letter

## Content angles that can earn durable search traffic

Prefer technical field notes over launch-post repetition:

- Why raw `.eml` is terrible LLM input
- What gets lost when you naively convert email to text
- Turning an email archive into a local knowledge base
- How to preserve attachments and thread attribution when ingesting email into RAG
- A reproducible benchmark: raw EML vs cleaned Markdown vs naive extraction
- Designing a local-only MCP server for untrusted email content
- Packaging one local Python MCP server for PyPI, MCPB, OCI, Claude, Copilot, Cursor, Cline, and Codex
- Gmail Takeout to Markdown once MBOX support lands

The benchmark work already in the repository is particularly useful because it gives other developers something concrete to cite rather than another product claim.

## Repository-native discovery hygiene

A few small surfaces compound everywhere else:

- set GitHub topics such as `eml`, `email`, `markdown`, `email-parser`, `mcp`, `model-context-protocol`, `rag`, `llm`, `local-first`, and `digital-preservation`
- set a GitHub social preview image that communicates `.eml → Markdown` rather than only the logo
- keep PyPI project links complete (docs, changelog, source/issues)
- link the visual demo from README, PyPI, BCL project page, and marketplace submissions
- keep `llms-install.md` current whenever the runtime/install contract changes

## Success signals

Do not optimize only for stars. Track signals that show the tool is becoming a default answer to the problem:

- PyPI installs / release uptake
- Homebrew installs
- MCPB release downloads
- container pulls once GHCR/Docker distribution exists
- external links and directory placements
- GitHub clones / unique visitors when available
- issues from real archives and edge cases
- mentions in other repositories, blog posts, and integration guides
- MCP/client marketplace usage where available
- skill installs / references if portable Agent Skill distribution lands
- Agent Finder / ARD search presence
- inbound requests for new input formats or integrations

## Guardrail

Reach work should not turn dead-letter into a cloud email client. Its strongest differentiation is local, inspectable conversion with high fidelity and multiple interfaces. Expand the set of archives it can ingest, the package forms it can ship, and the places agents/users can discover it without weakening that core.
