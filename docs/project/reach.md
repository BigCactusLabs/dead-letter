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
| Official MCP Registry | Automated | `server.json` is published from the release workflow after PyPI |
| Glama | Live | Indexed with strong quality/maintenance signals; ownership can still be claimed |
| Claude plugin marketplace | Live | Dedicated plugin with convert/summarize/triage/cabinet commands |
| Claude Desktop / Code | Live | Direct local MCP via `uvx` |
| Codex | Live | Direct local MCP via `uvx` |
| punkpeye/awesome-mcp-servers | Missing | High-reach curated GitHub list; submit under an email / file-conversion-adjacent category |
| Smithery | Not confirmed | Publish/index local stdio package if absent |
| mcp.so | Not confirmed | Separate directory submission if absent |
| mcpservers.org | Not confirmed | Separate directory submission if absent |
| Product demo asset | Missing | No 20–30 second visual demo at the top of the funnel |
| Integration recipes | Thin | Obsidian, local RAG, and archive workflows should have dedicated examples |

## Highest-leverage next moves

### 1. Win the non-MCP search intent

MCP directories are useful, but people usually start with the job rather than the protocol. Keep the README, PyPI description, and registry metadata centered on:

- `.eml` → Markdown
- local email archive
- RAG / LLM-ready email
- attachment-aware conversion
- thread preservation

The package metadata should include `email-archive`, `email-parser`, `llm`, `rag`, `mcp`, `digital-preservation`, and `yaml-frontmatter` in addition to the generic converter terms.

### 2. Make the first successful run one command

Preferred zero-persistence trial:

```bash
uvx dead-letter convert message.eml
```

Preferred MCP launch:

```bash
uvx --from 'dead-letter[mcp]' dead-letter-mcp
```

These should appear before source-checkout instructions. The first interaction should not require cloning the repository or editing a Python environment.

### 3. Build a visual proof asset

Create one short GIF/video that shows:

1. a messy `.eml` or folder of messages,
2. one conversion command or drag/drop,
3. the clean Markdown + front matter,
4. extracted attachments / diagnostics,
5. optional agent use through MCP.

The asset should be understandable with audio off and work in the GitHub README, the BCL site, launch posts, and directory listings.

### 4. Publish concrete recipes instead of generic feature copy

High-intent recipes:

- Convert an exported message folder into an Obsidian vault
- Prepare a folder of `.eml` files for RAG / embeddings
- Give Claude or Codex safe local access to email exports through MCP
- Build a durable local email archive with Cabinet mode
- Audit a conversion run using the JSON report and quality diagnostics

Each recipe should have a copy/paste command, expected output layout, and a short explanation of why dead-letter preserves more useful structure than naive text extraction.

### 5. Expand upstream input formats

The largest product-led reach opportunity is input format coverage.

#### MBOX — highest priority

Google Takeout commonly exports Gmail mailboxes as MBOX, not individual `.eml` files. Supporting MBOX would let dead-letter own the much larger intent cluster around:

- Gmail Takeout → Markdown
- Gmail archive → Obsidian
- Gmail Takeout → RAG / LLM
- MBOX → Markdown

A good implementation should stream messages rather than loading a multi-gigabyte mailbox into memory, preserve `X-Gmail-Labels`, expose per-message diagnostics, and feed each extracted RFC 822 message through the existing conversion pipeline.

#### Outlook archive formats — follow-on

Outlook bulk export commonly uses PST, while individual messages can be saved/downloaded as EML or MSG. PST/MSG support would expand Windows and enterprise archival use cases, but MBOX is the cleaner first reach multiplier.

### 6. Use the official MCP Registry as the canonical source

The release workflow already publishes `server.json` to the official MCP Registry. Keep this path authoritative and let downstream registries ingest it where possible rather than maintaining many divergent manifests.

For directories that require separate submission, link back to the GitHub repo and use the same one-sentence description.

## Directory submission copy

**Name:** dead-letter

**Short description:**

> Convert `.eml` email exports and archives to clean Markdown for RAG, LLM pipelines, and local knowledge bases, with thread splitting, attachment extraction, diagnostics, and a local MCP server.

**Install:**

```bash
uvx --from 'dead-letter[mcp]' dead-letter-mcp
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
- Gmail Takeout to Markdown once MBOX support lands

The benchmark work already in the repository is particularly useful because it gives other developers something concrete to cite rather than another product claim.

## Success signals

Do not optimize only for stars. Track signals that show the tool is becoming a default answer to the problem:

- PyPI installs / release uptake
- Homebrew installs
- external links and directory placements
- GitHub clones / unique visitors when available
- issues from real archives and edge cases
- mentions in other repositories, blog posts, and integration guides
- MCP directory usage / favorites where available
- inbound requests for new input formats or integrations

## Guardrail

Reach work should not turn dead-letter into a cloud email client. Its strongest differentiation is local, inspectable conversion with high fidelity and multiple interfaces. Expand the set of archives it can ingest and the places it can be discovered without weakening that core.
