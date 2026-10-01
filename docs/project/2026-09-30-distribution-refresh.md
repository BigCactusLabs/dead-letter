# Distribution refresh after 0.4.5 (2026-09-30)

Read-only status check of every MCP and agent-discovery channel after the
0.4.5 release (2026-09-28). Lookups ran 2026-09-30 in New York, which is
2026-10-01 around 01:50 UTC. Every request was an HTTP GET, a read-only `gh`
call, or a signed-out browser visit. No sign-in, claim, form, fork, PR,
payment, or catalog edit was made. Issues: #148–#153, #160.

| Channel | State | Evidence |
| --- | --- | --- |
| Official MCP Registry | **present**, 0.4.5 `isLatest`, published 2026-09-28T19:43Z with pypi, mcpb, and oci packages; 0.2.4, 0.2.5, 0.3.1, 0.4.0 still active | `registry.modelcontextprotocol.io/v0.1/servers?search=io.github.BigCactusLabs/dead-letter` |
| Glama | **present**, unclaimed (Claim button, no owner). Schema changelog shows v0.4.5 on 2026-09-30. `convert_directory` now reads "at most 50 per call … output_directory is required", so the #176 fix reached the listing without a claim. | [listing](https://glama.ai/mcp/servers/BigCactusLabs/dead-letter), [schema tab](https://glama.ai/mcp/servers/BigCactusLabs/dead-letter/schema) |
| PulseMCP | **present**. Shows registry name `io.github.BigCactusLabs/dead-letter` and "server.json file available"; the page does not state how it was ingested. A site banner says new submissions and listing changes are paused. | [listing](https://www.pulsemcp.com/servers/bigcactuslabs-dead-letter) |
| `punkpeye/awesome-mcp-servers` | **present**, README line 884 at `3c30195`. Description does not mention `.mbox` or MCPB. | [README@3c30195](https://github.com/punkpeye/awesome-mcp-servers/blob/3c30195615095be4f22ac120a7cee82f215cf547/README.md) |
| GitHub MCP registry | **not_in_snapshot**. All 4 pages, 339 entries (288 on 2026-09-23); no `dead-letter` or `bigcactus` match. | `api.mcp.github.com/v0.1/servers`, cursor-paged |
| Cline | **not_in_snapshot**. `api.cline.bot/v1/mcp/marketplace` returned 199 entries (203 on 2026-09-23). That endpoint answered but was not confirmed as the one Cline documents. No PR or issue in `cline/mcp-marketplace` mentions dead-letter. | API + `gh search` |
| Smithery | **not_in_snapshot**; `api.smithery.ai/servers/bigcactuslabs/dead-letter` returns 404. **Publishing blocker:** see below. | API search, CLI source |
| Agent Finder | **not_in_snapshot**. `ai-catalog.json` at `abb4a13` has 2,143 entries, none for dead-letter; no PR mentions it; no fork PR merged since 2026-09-23. | [catalog@abb4a13](https://github.com/github/agentfinder-catalog/tree/abb4a13e3c27e46e66ee6a103cc3ca2ae370cbd8) |
| mcpservers.org | **not_in_snapshot** for the query `dead-letter` (one unrelated Azure Service Bus result). Browser search was not blocked this time. | `mcpservers.org/search?query=dead-letter` |
| Homebrew tap | **present but stale**: `Formula/dead-letter.rb` at `c5ab27e` is 0.4.0. | [formula](https://github.com/BigCactusLabs/homebrew-tap/blob/c5ab27ed42dfb52024c983ea3a3e5265c3b4af0e/Formula/dead-letter.rb) |

## Smithery CLI rejects uv bundles

Smithery's [publishing guide](https://smithery.ai/docs/build/publish) accepts
local MCPB bundles but does not mention uv. The published CLI
(`@smithery/cli` 4.11.1, npm latest) picks a bundle runtime this way, quoted
from `dist/index.js`:

```js
if (basename(t.server?.mcp_config?.command ?? "") === "bun") return "bun";
if (t.server?.type === "python") return "python";
if (t.server?.type === "node") return "node";
if (t.server?.type === "binary" || Object.keys(e).some(n => n.startsWith("bin/"))) return "binary";
throw new Error("Could not determine bundle runtime from manifest");
```

Our manifest declares `"type": "uv"` (`mcpb/manifest.json`) and ships no
`bin/` entries, so `smithery mcp publish` fails before upload. This was read
from code; no publish was attempted. Whether Smithery's server API would accept
a uv runtime is unverified. Options for later: a Smithery-specific bundle
variant with a supported type, or an upstream change.

## Agent Finder entry revalidated

The prepared skill entry from [Agent Discovery](../reference/agent-discovery.md#submitting-to-github-agent-finder)
was added to a scratch clone of `github/agentfinder-catalog` at `abb4a13`.
`python -m json.tool` passed, the skill URL returned 200, and
`scripts/generate_ai_catalog.py` plus its `--check` mode passed (2,195 entries
after merging GitHub's live MCP catalog). The entry matches existing skill
entries' conventions (`sourceSet` as `owner/repo`, `blob/main` URL).
Submission is held by the owner (2026-09-30); nothing was pushed.
