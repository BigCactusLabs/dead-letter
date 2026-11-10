# Distribution refresh (2026-11-10)

Read-only status check of the MCP and agent-discovery channels tracked by
issues #104, #150–#153, #156, #157, #159 and #160. Observations ran 2026-11-10
from about 16:42 UTC. Every request was an HTTP GET, a read-only `gh api` GET,
or `git ls-remote`. The lookups executed no upstream code. No sign-in, claim,
form, fork, PR, payment, publish, or catalog edit was made, and **no submission
was made to any directory**. The baseline is the
[November 4 refresh](2026-11-04-distribution-refresh.md), the
[September 30 refresh](2026-09-30-distribution-refresh.md) and the
[distribution ledger](../reference/mcp-distribution.md).

Several hosts that were unreachable on November 4 answered this run:
`api.mcp.github.com`, `api.smithery.ai`, the published Cline catalog,
`bigcactuslabs.xyz` and `mcpservers.org/submit`. This record queries them
directly instead of using derived counts. Not reachable this run: the
mcpservers.org search and listing pages (HTTP 403 to curl and WebFetch) and
`api.smithery.ai` search (HTTP 500). Presence on those two is unknown, not
absent.

The largest change since November 4: Smithery shut down its API, accounts and
CLI on 2026-10-31, so the uv-bundle blocker recorded for #152 no longer has a
route to block. GitHub's MCP Registry was enumerated directly for the first
time since September 30: 771 entries, and dead-letter is not among them.

## Summary

| Channel | State on 2026-11-10 | Changed since 11-04? | Next maintainer action | Confidence |
| --- | --- | --- | --- | --- |
| Official MCP Registry (#104) | **present**; 0.4.10 `isLatest`, `active`; 10 versions, all `active` | No | None | direct API |
| GitHub MCP Registry (#150) | **not listed**: 771 entries over 26 pages, no dead-letter or BigCactusLabs match | Yes: directly enumerated (11-04 was derived only); count 339 (09-30) to 771 | Email partnerships@github.com (still the only owner-documented step, dated 2025-10-24) or wait; keep `vscode:mcp/install` links | reported (owner docs thin) |
| Cline (#151) | not in the published catalog (203 entries, generated 2026-09-18) or `api.cline.bot` (199); one search hit is a false match | No (upstream HEADs unchanged) | Submit the prepared `entry.json` PR to `cline/marketplace` when authorized | consensus |
| Smithery (#152) | `api.smithery.ai/servers/bigcactuslabs/dead-letter` 404; **API, accounts and CLI shut down 2026-10-31**; directory curated | **Yes, major** | Close or defer #152; no documented self-serve route | reported (single owner page) |
| mcpservers.org (#153) | listing **access-limited** (search 403); submit form live | Partly (form fields observed) | Free submission with maintainer email when authorized | reported |
| Agent Finder (#160) | not listed; HEAD `3286bfd` unchanged (1,718 entries); refresh PR #89 head `d3024d9` has 1,842 (771 MCP) | No (refresh PR moved) | Unchanged: owner-held submission; re-run the generator at the new pin before any PR | consensus |
| ARD (#159) | spec v0.91, `ard-spec@f073b1e` unchanged; `bigcactuslabs.xyz/.well-known/ard.json` returns 404; hosted catalog prepared as open, unmerged site PR `BigCactusLabs/bcl-web#9` | Host answered (404); PR prepared | Review and merge bcl-web#9 when ready, then verify production serving | consensus (spec); production serving unverified |
| VS Code (#156/#157) | stable 1.145.2 (2026-11-06); `vscode:mcp/install?{json}` unchanged; new `vscode:mcp/by-name/<name>` and documented `vscode.dev/redirect?url=` wrapper | Yes (docs commit `b1aa992`, 2026-11-04T10:21Z) | Optionally add a by-name link once listed in an active gallery | consensus |
| Cursor (#156/#157) | stable 3.29.15; `cursor://anysphere.cursor-deeplink/mcp/install?name=$NAME&config=$BASE64_ENCODED_CONFIG` | Verified from the owner page (11-04 was a search summary) | None | consensus |
| Copilot | CLI 1.0.108 (npm, 2026-11-09); skills read from `.github/skills`, `.claude/skills`, `.agents/skills` | First owner-doc check | None; `skills/dead-letter/` is a distribution directory, not an auto-load path | consensus |
| New directories (hunt) | nothing credible found that appeared after 11-04 | n/a | None | early signal, thin |

## Official MCP Registry (#104)

- **State:** present.
- **Endpoint and method:** `GET https://registry.modelcontextprotocol.io/v0.1/servers/io.github.BigCactusLabs%2Fdead-letter/versions/latest` returns version 0.4.10, status `active`, `isLatest: true`, published 2026-10-29T21:02:46Z. `.../versions/0.4.10` also returns 200. `.../versions` returns 10 versions (0.4.10, 0.4.9, 0.4.8, 0.4.7, 0.4.6, 0.4.5, 0.4.0, 0.3.1, 0.2.5, 0.2.4), all `active`.
- **Changed since 11-04:** no.
- **Source catalog link:** `GET https://registry.modelcontextprotocol.io/v0/servers/io.github.BigCactusLabs/dead-letter` returns 404. `.well-known/ard.json` used that URL in its MCP entry's `metadata.registry`. The same PR as this record points it at the `v0.1` latest URL above, still unpinned to a version.
- **Next action:** none.

## GitHub MCP Registry (#150)

- **State:** not listed.
- **Endpoint and method:** `GET https://api.mcp.github.com/v0.1/servers`, cursor-paged through `metadata.nextCursor` until exhausted: 26 pages, 771 entries, 771 unique names, `metadata.total` 771. A case-insensitive search of each entry's JSON for `dead-letter`, `dead_letter` and `bigcactus` matched only `com.streamkap/tools`, whose text is about triaging dead-letter queues. That is a false positive. `https://github.com/mcp` also shows 771 servers and no dead-letter match.
- **Cross-check:** Agent Finder refresh PR #89 head `d3024d9` (updated 2026-11-10T16:20Z) imports 771 `application/mcp-server+json` entries, the same count.
- **Owner sources:** GitHub Docs ([MCP overview](https://docs.github.com/en/copilot/concepts/customizations/mcp)) describes the registry as a curated list from partners and the community, in public preview, and gives no submission route. The [October 24, 2025 GitHub Blog post](https://github.blog/ai-and-ml/generative-ai/how-to-find-install-and-manage-mcp-servers-with-the-github-mcp-registry/) remains the newest owner text on listing: email partnerships@github.com. It also expected self-publication "in the next couple months". Searches of github.blog and docs.github.com (two searches) found no later owner source confirming that self-publication shipped. The [2026-10-28 GitHub changelog](https://github.blog/changelog/2026-10-28-connect-your-tools-to-copilot-without-all-the-setup/) describes GitHub-verified connectors (26 services at launch, public preview). It says nothing about the registry or about how third-party servers are added, so it is not a route for dead-letter. On 11-04 this page was unverified; it is now read.
- **Changed since 11-04:** direct enumeration works again. Count: 288 (09-23), 339 (09-30), 771 (11-10). No owner onboarding change.
- **Next action:** the partnerships email is the only owner-documented step and the maintainer's decision. A listing would also make `vscode:mcp/by-name/io.github.BigCactusLabs/dead-letter` resolve, but only if the registry backs the active gallery in the user's VS Code. That is UNVERIFIED (see Unresolved).

## Cline (#151)

- **State:** not listed in either catalog.
- **Endpoint and method:** `GET https://cline.github.io/marketplace/catalog.json` has `generatedAt` 2026-09-18T20:15:28Z and 203 entries (149 mcps, 16 plugins, 38 skills), with no dead-letter or BigCactusLabs entry. On 11-04 this host was unreachable; it now matches the September source count. `GET https://api.cline.bot/v1/mcp/marketplace` returns 199 entries, no match, the same as 09-30. `git ls-remote`: `cline/marketplace` HEAD `6969aa8` and `cline/mcp-marketplace` HEAD `87390ab`, both unchanged.
- **Search:** `gh api search/issues` across both repositories finds one `dead-letter` hit, [cline/marketplace#251](https://github.com/cline/marketplace/pull/251) "Add Streamkap MCP", which is unrelated, and no `BigCactusLabs` hit. `cline/marketplace` has 398 open PRs and no PR merged after 2026-09-18. `cline/mcp-marketplace` has 3,293 open issues; the two issues closed since 11-04 (#799, #532) were closed by their own authors.
- **Submission contract:** unchanged. [`CONTRIBUTING.md` at `6969aa8`](https://github.com/cline/marketplace/blob/6969aa8505f2be81bb3627e47ab0037d234aeaf7/CONTRIBUTING.md) says to create `registry/<type>s/<slug>/entry.json`, run `npm run validate`, and open a PR. The legacy repository README at `87390ab` still routes to the `mcp-server-submission.yml` issue template.
- **Next action:** submit the prepared entry PR from an authorized fork. Upstream has merged nothing since 2026-09-18, so expect a slow review.

## Smithery (#152)

- **State:** not found; self-serve publishing discontinued.
- **Endpoint and method:** `GET https://api.smithery.ai/servers/bigcactuslabs/dead-letter` returns 404 `{"error":"Server not found"}`. `registry.smithery.ai/...` is also 404, and so is the web listing `https://www.smithery.ai/servers/bigcactuslabs/dead-letter`. `api.smithery.ai/servers?q=dead-letter` and `?q=email` return 500, so search is broken and its results are unknown, not empty.
- **Shutdown (owner page):** [smithery.ai/future](https://www.smithery.ai/future) states that on 2026-10-31 Smithery shut down its API, Connect, Toolbox, accounts, API keys, CLI and SDKs, and that smithery.ai remains only as a public directory, now powered by Arcade. The directory shows a curated set of verified servers. The page is signed by Smithery (now part of Arcade). Both the report's worker and the lead read this page on 2026-11-10. It does not say whether the curated directory takes nominations.
- **CLI:** npm `smithery` latest is still 1.2.0 (2026-05-31) and `@smithery/cli` latest is still 4.11.1 (2026-04-30); neither is marked deprecated in npm metadata yet. `smithery-ai/cli` HEAD is still `407ac3b`. Publishing through the CLI is presumably impossible now that accounts and the API are gone; that was not tested.
- **MCPB spec:** [`MANIFEST.md` at `70fe3b3`](https://github.com/modelcontextprotocol/mcpb/blob/70fe3b34cd6dff1b3bba046638edc72a6467a4fb/MANIFEST.md) lists `uv` among four server types (UV runtime, v0.4+). Unchanged.
- **Changed since 11-04:** major. The November 4 step ("a Smithery-specific bundle variant with a supported type") has no route to use.
- **Next action:** close or defer #152 as "Smithery self-serve publishing discontinued 2026-10-31" (the maintainer's call). The ledger no longer carries `smithery mcp publish` copy. Optionally ask Smithery or Arcade whether the curated directory takes nominations.

## mcpservers.org (#153)

- **State:** listing presence **access-limited**; absence cannot be inferred.
- **Endpoint and method:** `GET https://mcpservers.org/submit` (browser user agent) returns 200. Required fields: Server Name, Category (15 options, including Communication), Short Description, a repository, website or documentation URL (max 512), and Contact Email. Optional: "Official MCP Registry Name" (placeholder `io.github.example/my-server`) and a remote-connections checkbox. Plans: free ($0, review within 2 weeks) or Premium ($39 one-time, review within 24 hours, with a badge and search priority). The page also says listings are free. `mcpservers.org/search?query=dead-letter` and a guessed `/servers/BigCactusLabs/dead-letter` returned 403 to both curl and WebFetch.
- **Changed since 11-04:** the form was observed for the first time. Whether the registry-name field is new is unknown.
- **Next action:** when the maintainer authorizes, use the free path with a real maintainer contact email, registry name `io.github.BigCactusLabs/dead-letter`, category Communication, and the remote checkbox unchecked. A signed-out browser visit could check listing presence.

## Agent Finder (#160)

- **State:** not listed.
- **Endpoint and method:** `git ls-remote` and `gh api repos/github/agentfinder-catalog/commits/main`: HEAD `3286bfd` (2026-10-29T02:09Z, merge of #97), the same as 11-04, so no merges since. `ai-catalog.json` at `3286bfd` has 1,718 entries (925 `application/ai-skill`, 647 MCP, 69 Claude plugin, 55 Copilot plugin, 22 Cursor plugin). Refresh PR #89 head `d3024d9` has 1,842 entries (771 MCP). Neither contains dead-letter or BigCactusLabs.
- **PRs:** `gh api pulls?state=all`, paginated: 53 open. No PR from a fork has been merged since 2026-09-30; every merged PR has head repository `github/agentfinder-catalog`. Open fork PRs include #98, #100, #101, #102 and #103 (2026-10-29 to 2026-11-08). A repository search for `dead-letter` returns 0.
- **Policy:** `CONTRIBUTING.md` last changed in `6eea851` and `8859953` (2026-10-14 UTC), and the policy is the same as recorded on 11-04. The catalog's skill entries use `application/ai-skill`, not ARD's `application/agent-skills+md`; check the prepared entry against the catalog's convention before any PR.
- **Next action:** unchanged. Submission is owner-held. Re-run the generator at the current pin before any PR.

## ARD (#159)

- **State:** not hosted.
- **Endpoint and method:** `https://bigcactuslabs.xyz/.well-known/ard.json` returns HTTP 404 with `content-type: text/html` from Cloudflare (`curl -I -L`); the site root returns 200. On 11-04 this was not checked.
- **Upstream:** `ards-project/ard-spec` HEAD `f073b1e` (2026-10-25), unchanged, spec v0.91, no commits since. The `application/agent-skills+md` recommendation (`cbe4bbf`, 2026-10-11) still stands. Open PRs since 11-04: #116 (2026-11-04) renames the JSON Schema key `TrustManifest` to `trustManifest`, and #115 adds `SECURITY.md`. Our `.well-known/ard.json` has no `trustManifest`, so #116 does not affect it.
- **Hosted catalog (verified by the lead, 2026-11-10):** Pull request 9 in the `BigCactusLabs/bcl-web` site repository prepares the hosted catalog. It is not linked here because an unauthenticated request for it returns 404. It is open, unmerged and undeployed. It pins the released 0.4.10, uses the skill type `application/agent-skills+md`, and passes `ard-spec@f073b1e` conformance with JSON Schema validation (0 errors, 0 warnings). A local `wrangler dev` serves it with `Content-Type: application/json`. **Production serving is unverified until the PR is merged and deployed.**
- **Next action:** review and merge bcl-web#9 when the maintainer is ready, then fetch the production URL and confirm the status and content type. Pin conformance at `f073b1e` or later.

## VS Code, Cursor and Copilot (#156, #157)

**VS Code**

- Stable version: `update.code.visualstudio.com/api/update/darwin-universal/stable/latest` reports 1.145.2; the GitHub release was published 2026-11-06T10:21Z.
- [`vscode-docs@4dd36a8`](https://github.com/microsoft/vscode-docs/blob/4dd36a8c30fb0255eabf83d6b5701d5ba6a86b1b/api/extension-guides/ai/mcp.md) (HEAD, 2026-11-10), `api/extension-guides/ai/mcp.md`: the install link is unchanged (`vscode:mcp/install?{json-configuration}`, built with `encodeURIComponent(JSON.stringify(obj))`). Commit `b1aa992` (2026-11-04T10:21Z, "docs: document plugin, customization, and MCP deep links") added `vscode:mcp/by-name/<registry-server-name>`, which resolves the exact registry name against the active MCP gallery. It also added `vscode:mcp/<https-server-url-without-scheme>` and a badge wrapper, `https://vscode.dev/redirect?url=<url-encoded-vscode-URL>`. That settles the November 4 open item: the wrapper the install-link generator uses is now documented by the owner.
- Skills ([`agent-skills.md` at `4dd36a8`](https://github.com/microsoft/vscode-docs/blob/4dd36a8c30fb0255eabf83d6b5701d5ba6a86b1b/docs/agent-customization/agent-skills.md), DateApproved 11/4/2026): Copilot harness `.github/skills/` and `~/.copilot/skills/`; Claude `.claude/skills/` and `~/.claude/skills/`; Codex `.agents/skills/` and `~/.agents/skills/`. Local sessions also read project skills from `.claude/skills/` and `.agents/skills/`. The `chat.agentSkillsLocations` setting is now marked deprecated.

**Cursor**

- Stable: `cursor.com/api/download?platform=darwin-universal&releaseTrack=stable` reports `3.29.15`.
- [Install links](https://cursor.com/docs/mcp/install-links) (owner page): `cursor://anysphere.cursor-deeplink/mcp/install?name=$NAME&config=$BASE64_ENCODED_CONFIG`, with the configuration JSON-stringified and then base64 encoded. The `cursor.com/en/install-mcp` web wrapper does not appear in the page text, so the wrapper is still unverified.
- [Skills](https://cursor.com/docs/skills): loaded from `.agents/skills/` and `.cursor/skills/` (project) and `~/.agents/skills/` and `~/.cursor/skills/` (user), plus `.claude/skills/`, `.codex/skills/` and their `~/` equivalents for compatibility.

**GitHub Copilot**

- Copilot CLI: npm `@github/copilot` latest is 1.0.108 (2026-11-09).
- [About agent skills](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills): project skills in `.github/skills`, `.claude/skills` or `.agents/skills`; personal skills in `~/.copilot/skills` or `~/.agents/skills`. The page notes that supported locations depend on the surface.

All three discover `.agents/skills/` and `.claude/skills/`. The repository's `skills/dead-letter/` is a distribution directory and is not auto-loaded, which matches `AGENTS.md`. Users install it with `gh skill` or copy it into one of those paths.

**Next action:** none required. A by-name link is only useful once the server is in the active gallery.

## New directories (hunt)

No credible directory was found that appeared or gained traction after 2026-11-04 and accepts local stdio Python servers or Agent Skills for free. Evidence is thin: two extended searches turned up either items older than 11-04 or SEO roundups, which were dropped. Two leads were not opened and stay UNVERIFIED: a 2026-11-09 press release about Neoverse, described in a search summary as a security risk catalog (not a submission directory), and agenticskills.io, which has a free submit route but no date or traction evidence. Neither is a recommendation.

## Related issues

- #105 was closed on 2026-11-10; its demo and reusable versions were delivered in #276 and #277.
- Closing or deferring #152 is the maintainer's decision; this record only reports the Smithery shutdown.

## Unresolved

- Whether the "active MCP gallery" in a default VS Code install is the GitHub MCP Registry. This decides whether a by-name link works after a listing. The VS Code `chat.mcp.gallery` setting docs would settle it.
- mcpservers.org listing presence (search returned 403).
- Whether Smithery's curated directory accepts nominations. Only a direct inquiry would settle it.
- Whether self-publication to GitHub's registry ever shipped. No owner source says it did.
- Production serving of the ARD catalog, pending bcl-web#9.

Not rechecked: Glama, PulseMCP, mcp.so (#154) and cursor.directory (#155). No natural-language retrieval tests were run; the project is not listed anywhere new.
