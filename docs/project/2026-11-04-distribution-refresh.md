# Distribution refresh (2026-11-04)

Read-only status check of the MCP and agent-discovery channels tracked by
issues #150–#153, #156, #159 and #160. Observations ran 2026-11-04 between
06:01 and about 06:20 UTC from checkout `359fbb9` (2026-11-03). Every request
was an HTTP GET, a shallow git clone of an upstream repository into an isolated
scratch directory, or an npm, PyPI or MCP Registry API read. The lookups executed
no upstream code; the ARD conformance check below is the one exception. No sign-in, claim, form, fork, PR, payment, or catalog edit was
made. The baseline is the [September 30 refresh](2026-09-30-distribution-refresh.md)
and the [distribution ledger](../reference/mcp-distribution.md).

| Surface | Issue | State on 2026-11-04 | Evidence |
| --- | --- | --- | --- |
| Official MCP Registry | #104 | **present**, 0.4.10 `isLatest`, `active`; 10 versions, all `active`; published 2026-10-29T21:02:46Z. PyPI latest is 0.4.10. | `registry.modelcontextprotocol.io/v0.1/servers/io.github.BigCactusLabs%2Fdead-letter/versions/latest` (06:01Z) |
| GitHub MCP Registry / VS Code / Copilot | #150 | **not_in_snapshot (derived)**. Agent Finder's import of GitHub's MCP catalog has 647 entries at `agentfinder-catalog@d6c5e84` (2026-10-29) and 773 at refresh PR head `1467118` (2026-11-04T05:20Z); no dead-letter match. | `ai-catalog.json` in each commit. Direct `api.mcp.github.com` was not reachable. |
| Cline `marketplace` | #151 | **not_in_snapshot (source)**. HEAD still `6969aa8` (2026-09-18). 203 entries (149 mcps, 16 plugins, 38 skills). 397 `refs/pull/*`; no PR head adds a BigCactusLabs entry. Published catalog unknown. | git clone |
| Cline `mcp-marketplace` (legacy) | #151 | HEAD still `87390ab` (2025-06-24). | git clone |
| Smithery | #152 | **unknown** (API not reachable). `@smithery/cli` 4.11.1 and `smithery` 1.2.0 both reject `server.type: "uv"`. | npm, `smithery-ai/cli@407ac3b` |
| mcpservers.org | #153 | **unknown** (host not reachable). `wong2/awesome-mcp-servers@39eb1d7` (2026-07-13) still says it does not accept PRs and has no dead-letter. | git clone |
| VS Code direct install | #156 | Format unchanged: `vscode:mcp/install?{json-configuration}`, built with `encodeURIComponent(JSON.stringify(obj))`. Matches `scripts/generate_client_installs.py:63`. | `microsoft/vscode-docs@464237e`, `api/extension-guides/ai/mcp.md` |
| Cursor direct install | #156 | Deeplink shape unchanged (`cursor://anysphere.cursor-deeplink/mcp/install?name=&config=<base64>`). Search summary of the official page only; the page was not fetched and the `cursor.com/en/install-mcp` wrapper was not verified. | `cursor.com/docs/mcp/install-links` (practitioner tier) |
| Agent Finder | #160 | **not_in_snapshot**. HEAD `3286bfd` (2026-10-28) has 1,718 entries (925 skills, 647 MCP, 69 Claude plugins, 55 Copilot plugins, 22 Cursor plugins); `1467118` has 1,844. No dead-letter match in either. | git clone |
| ARD | #159 | Spec still v0.91 at `ards-project/ard-spec@f073b1e` (2026-10-25). Source manifest skill entry moved to the standard media type; see below. `bigcactuslabs.xyz` unknown. | ard-spec history |
| `punkpeye/awesome-mcp-servers` | #104 | **present**, line 996 under Communication at `29f2d49` (2026-11-01); 5,095 lines. Text unchanged, still no `.mbox` or MCPB. | git clone |

## VS Code discovery for #150

VS Code's `@mcp` search no longer lists gallery results by default. With
`chat.customizations.marketplace.enabled` on (the default), searching `@mcp`
in the Extensions view or running **MCP: Browse MCP Servers** opens **Find MCP
Servers in Customizations**. Discovery there is Browse, then GitHub Feed, then
a search by name or `@type:mcp`. The docs say the experimental GitHub Feed
needs a harness that provides marketplace search, and that Local sessions
don't provide this source.

The docs do not say which catalog backs GitHub Feed. Linking it to the GitHub
MCP Registry is unverified. Copilot connectors ("GitHub-managed MCP servers")
are a separate hosted-service source on the same Browse page. They are not a
route for local stdio servers.

Next step for #150: reword the acceptance test from the gallery search
`@mcp dead-letter` to Customization Marketplace, GitHub Feed, `@type:mcp`, in
a Copilot-harness session. That is a recommendation; the issue text is the
maintainer's to change. The partnerships@github.com step is unchanged but was
not verified this run. A search summary of a 2026-10-28 GitHub changelog
("Connect your tools to Copilot without all the setup") describes verified
connectors, not registry self-publishing. The page could not be fetched, so
that is unverified.

Agent Finder has refreshed its MCP import hourly since 2026-10-08
(`d13c305`). A GitHub registry listing would surface in a refresh PR within
about an hour, though the PR still needs merging.

## Smithery renamed CLI keeps the uv rejection

The npm package `smithery` replaced `@smithery/cli` at 1.0.0 (2026-05-01).
Its latest, 1.2.0 (2026-05-31), has the same runtime detection as 4.11.1 and
throws "Could not determine bundle runtime from manifest" for `server.type:
"uv"` with no `bin/` entries. The code is `src/lib/mcpb.ts:720-736` at
`smithery-ai/cli@407ac3b` (repo HEAD, 2026-05-31) and in the 1.2.0 tarball
(sha256 `b35aaff6…`). `smithery mcp publish <bundle.mcpb> -n <org/server>` is
still in its README. `@smithery/cli` 4.11.1 (2026-04-30) is still npm latest
for that name and is not deprecated (tarball sha256 `2d3a60fb…`).

Upstream has had no commits since 2026-05-31, so waiting for an upstream
change is a weaker option than the September 30 record implied. The step for
#152 holds: a Smithery-specific bundle variant with a supported type. This was
read from code; no publish was attempted. Smithery presence is unknown because
the API was not reachable.

## Agent Finder contract for #160

`CONTRIBUTING.md` at `github/agentfinder-catalog@3286bfd` was revised on
2026-10-13:

- `metadata.sourceSet` and `repoPath` are required only for GitHub-file
  references. The skill entry is one, so it keeps them. All 1,071 `sourceSet`
  values at HEAD use the `owner/repo` form, as our entry does.
- A new "MCP registry entries" section says to prefer the hourly-imported
  registry entry, use versioned `api.mcp.github.com/v0.1/servers/<name>/versions/<v>`
  URLs, and not use raw GitHub descriptors.
- Validation needs `python3 -m pip install -r requirements.txt`
  (`Pillow==12.3.0`) before `generate_ai_catalog.py`.
- An optional icon overlay was added.
- The source schema is still `urn:ai:` plus `mediaType`.

The catalog shrank from 2,143 to 1,718 entries. Commits `b331d55`, `4f61215`,
`d729644` and `7d5f2c8` removed skills covered by a listed plugin. `3ccfd31`
restored 41 standalone skills that no plugin declares. `skills/dead-letter/`
has no plugin manifest above it, so it falls under that retained rule. This is
inferred from commit messages, not a written policy. All 22 merge commits
since `abb4a13` come from `github/` branches; squash and rebase merges cannot
be told apart without the API.

The prepared entry still fits the rules in shape. The generator was not re-run
at the new pin. Re-run it before any PR.

## ARD media type (#159)

ARD is still v0.91. On 2026-10-11, ard-spec `cbe4bbf` ("spec: list standard
recognized media types in §3.3 and align skill example") changed the skill
example from `application/ai-skill+md` to `application/agent-skills+md` and
added it to the conformance tool's standard types. The old type now classifies
as info ("valid application extension"), not a warning. It was non-breaking but
non-standard.

The skill entry in `.well-known/ard.json` moved to
`application/agent-skills+md` on 2026-11-04. The conformance tool at
`ards-project/ard-spec@f073b1e` (HEAD, 2026-10-25) passed the source manifest
with 0 errors and 0 warnings, including the strict JSON Schema check
(`jsonschema` installed), run separately from the read-only lookups. This changes source metadata on `main`.
No ARD file is served from `bigcactuslabs.xyz`, whose state is unknown from
here. The earlier pin `b76f235` was not found in `main` history since
2026-09-15. Pin conformance at `f073b1e` or later when hosting.

## Access-limited

These hosts could not be reached from this environment. Presence there is
unknown, not absent.

- `api.mcp.github.com`: direct enumeration was not possible. The Agent Finder
  import stands in as a derived count, not a one-to-one comparison (the import
  held 288 entries when the direct count was 339 on September 30).
- `cline.github.io/marketplace/catalog.json` and `api.cline.bot`: the
  published Cline catalog is unknown.
- `api.smithery.ai` and `registry.smithery.ai`: Smithery presence is unknown.
- `api.github.com` for repositories outside this session: the audit script's
  Awesome check returned HTTP 403 (verified through git instead). Open PR
  counts and fork authorship for Cline and Agent Finder could not be listed.
- `bigcactuslabs.xyz`: `/.well-known/ard.json` was not checked.
- `github.blog` and `mcpservers.org`: not fetched. mcpservers.org presence and
  terms, the GitHub onboarding posts, `cursor.com` docs and the live Smithery
  publish docs are unconfirmed.

Not rechecked in this run: Glama, PulseMCP, mcp.so (#154) and cursor.directory
(#155). The `vscode.dev/redirect?url=` wrapper used by the generator was not
compared with current docs. No natural-language retrieval tests were run; the
project is not listed anywhere new.
