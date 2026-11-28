# Distribution refresh (2026-11-18)

Read-only status check of the channels tracked by issues #150, #151, #152,
#153 and #160, plus the VS Code by-name link and a new-directory search.
Observations ran 2026-11-18 from about 16:19 UTC. Every request was an HTTP
GET, a read-only `gh api` GET, a `git ls-remote`, or a signed-out headless
browser visit. The lookups executed no upstream code. No sign-in, claim, form,
fork, PR, payment, publish, or catalog edit was made, and **no submission was
made to any directory** in this run. The baseline is the
[November 10 refresh](2026-11-10-distribution-refresh.md) and the
[distribution ledger](../reference/mcp-distribution.md).

Not rechecked this run: the Official MCP Registry entry, Glama, PulseMCP,
mcp.so (#154), cursor.directory (#155), Awesome, the ARD hosting (#159),
Cursor, and Copilot. Their November 10 states stand as recorded there.

Two changes since November 10. The mcpservers.org page for dead-letter now
loads in a browser, so that channel is **listed**. Agent Finder merged its
first fork PR, but it was a first-party catalog fix, so its submission policy
is unchanged. GitHub's MCP Registry still does not list dead-letter; it grew
from 771 to 896 entries. In VS Code stable the registry gallery is disabled by
default, which lowers the value of a by-name link.

## Summary

| Channel | State on 2026-11-18 | Changed since 11-10? | Next maintainer action | Confidence |
| --- | --- | --- | --- | --- |
| GitHub MCP Registry (#150) | **not listed**: 896 entries over 9 pages at limit 100; only text match is the same false positive as 11-10 | Count 771 to 896; dead-letter state unchanged | Email partnerships@github.com is still the only owner-documented route; the maintainer decides | direct API (listing); reported (onboarding) |
| VS Code by-name link (#156/#157) | stable 1.147.0; the product gallery is GitHub's registry but `chat.mcp.gallery.enabled` defaults to false on Stable | Yes: the 11-10 open item is settled | Keep `vscode:mcp/install` links as the primary path | consensus (source); reported (product.json URL) |
| Cline (#151) | not listed in either catalog | No | Submit the prepared `entry.json` PR when authorized; no merge upstream since 2026-09-18 | consensus |
| mcpservers.org (#153) | **listed**, verified by browser | Yes: 11-10 was submitted, not yet listed | Close or update #153 (maintainer's decision) | direct observation |
| Agent Finder (#160) | not listed; HEAD `b8f7aa4` (1,843 entries) | Yes: #89 and fork PR #102 merged | Unchanged: owner-held submission; re-run the generator at the new pin before any PR | consensus; reported (reading of #102) |
| Smithery (#152) | no nomination route documented | No | Close or defer #152 (maintainer's decision) | reported |
| New directories (hunt) | mcp.directory, skills.sh, agenticskills.io examined; dead-letter absent from the first two; not conclusively checked on the third | n/a | None recommended; see the section | early signal |

## GitHub MCP Registry (#150)

- **State:** not listed.
- **Endpoint and method:** `GET https://api.mcp.github.com/v0.1/servers?limit=100`, cursor-paged through `metadata.nextCursor` until absent (run at 2026-11-18T16:19Z): 9 pages, 896 entries, 896 unique names, `metadata.total` 896. A case-insensitive search of each entry's JSON for `dead-letter`, `dead_letter` and `bigcactus` matched only `com.streamkap/tools`, the dead-letter-queue false positive recorded on 11-10. `GET .../v0.1/servers/io.github.BigCactusLabs%2Fdead-letter/versions/latest` returns 404 `{"error":"Server not found"}`.
- **Changed since 11-10:** the count rose from 771 to 896 (+125 in 8 days). The 11-10 list was not saved, so the 125 additions cannot be identified; today's dump was saved for the next diff.
- **Entry shape:** each entry's `publishedAt` equals the official registry's publish time for that version, and `updatedAt` is 2026-11-18 on 867 of 896 entries, which looks like a sync timestamp. The API exposes no listing-date field, so the date a server was added cannot be read from it.
- **Onboarding, owner and maintainer sources:**
  - [Discussion #1257](https://github.com/github/github-mcp-server/discussions/1257) in `github/github-mcp-server`: on 2026-11-05 SamMorrowDrums (author association COLLABORATOR) wrote that initial onboarding "is still manually curated" and that publishing to the open-source MCP Registry "does not guarantee inclusion in the GitHub MCP Registry or imply an ETA".
  - From 2026-11-13 the same account answers per-server onboarding discussions created after 11-05 by pointing to the [October 24, 2025 GitHub Blog post](https://github.blog/ai-and-ml/generative-ai/how-to-find-install-and-manage-mcp-servers-with-the-github-mcp-registry/), whose "How to publish your own MCP server" section ends with "email partnerships@github.com". A GraphQL search found 79 such discussions, and every one sampled has the reply. The blog page still says the registry "has 44 MCP servers", so it has not been updated.
  - [modelcontextprotocol/registry#1758](https://github.com/modelcontextprotocol/registry/issues/1758) was closed on 2026-11-04 by a registry member who wrote that publishing to the official registry does not automatically onboard a server to the separately curated gallery at github.com/mcp.
  - The github.blog changelog feed and a search of github.blog and docs.github.com found no newer owner source than the 2025 posts and the 2026-10-28 connectors changelog already recorded on 11-10.
- **Requester reports (early signal, self-reported, not confirmed by GitHub):** posters in #1257 say requested servers went live, in at most about 6 to 18 days. Several also emailed partnerships@github.com, and which channel triggered a listing is unknown. A regex match of registry names in #1257 request comments against today's dump found 35 of 294 October comments and 9 of 284 November comments naming a now-listed server. Name extraction is noisy, so treat this as a rough bound. Listed servers include small local stdio PyPI servers, so being local, Python and low-star does not exclude a server.
- **Auto-import:** no evidence of blanket or filtered auto-import. A partial sample of the official registry (61 pages, 3,338 latest-version names) overlaps the GitHub list on 93 names, and neither repository, star count nor transport predicts listing. The newest-published entries are new versions of servers already onboarded.
- **Next maintainer action:** the partnerships email is the only owner-documented step, and sending it is the maintainer's decision. A per-server discussion in `github/github-mcp-server` is now redirected by that repository's maintainer, so it is not a route.

## VS Code by-name link (#156/#157)

- **State:** stable is 1.147.0 (GitHub release published 2026-11-18T09:45Z; 1.145.2 on 11-10). Its built-in MCP gallery is GitHub's registry, and `chat.mcp.gallery.enabled` has `default: false` for Stable. That settles the 11-10 open item.
- **Evidence (VS Code source at tag 1.147.0):** [`chat.shared.contribution.ts`](https://github.com/microsoft/vscode/blob/1.147.0/src/vs/workbench/contrib/chat/browser/chat.shared.contribution.ts) registers the setting with `default: false` and `included: product.quality === 'stable'`; 1.145.2 is the same. [microsoft/vscode#347747](https://github.com/microsoft/vscode/pull/347747) (merged 2026-11-11, in 1.147.0) describes the setting as controlling "the product-provided GitHub MCP Registry". With no manifest, the by-name handler warns that the gallery is not configured; when the name is not found it warns "not found in the configured registry".
- **Would `vscode:mcp/by-name/io.github.BigCactusLabs/dead-letter` resolve today?** In a default Stable install, no: the gallery is off. With the setting on, no: GitHub's registry returns 404 for the name. Insiders has the gallery on, but the name would still be absent. After a GitHub listing, the link would help only users who enabled the gallery or run Insiders. Whether the Customizations marketplace surfaces registry servers while the gallery is off was not traced.
- **Reported, not verified:** the gallery URL (`https://api.mcp.github.com`) was read from a locally installed VS Code 1.137.0 `product.json`; that 1.147.0 uses the same URL is inferred.
- **Next action:** none. Keep `vscode:mcp/install?{json}` links as the primary install path.

## Cline (#151)

- **State:** not listed in either catalog.
- **Endpoint and method:** `git ls-remote` HEADs `6969aa8` (`cline/marketplace`) and `87390ab` (`cline/mcp-marketplace`), both unchanged. `https://cline.github.io/marketplace/catalog.json` has `generatedAt` 2026-09-18T20:15:28Z and 203 entries (149 mcp, 38 skill, 16 plugin); `https://api.cline.bot/v1/mcp/marketplace` has 199 entries. Neither has a dead-letter, dead_letter or bigcactus match.
- **Activity:** `gh api search/issues` finds no PR merged in `cline/marketplace` after 2026-09-18. Open PRs rose from 398 (11-10) to 453. `cline/mcp-marketplace` has 3,509 open issues (3,293 on 11-10); the seven closed since 11-09 were each closed by their own author. Issues and PRs created since 11-01 all come from authors with no association to the repository. The only `dead-letter` search hit is the unrelated cline/marketplace#251 (Streamkap).
- **Owner statements:** none found about the backlog or the submission route. `CONTRIBUTING.md` last changed 2026-06-17, so the route is unchanged. [Cline docs](https://docs.cline.bot/usage/cline-desktop) mention browsing the Marketplace but not how entries get in.
- **Next action:** unchanged. Submit the prepared entry from an authorized fork when the maintainer authorizes. Upstream has merged nothing in two months, so expect no quick review.

## mcpservers.org (#153)

- **State:** listed. Verified by browser on 2026-11-18 at about 16:23 UTC, signed out, in headless Chromium (Playwright).
- **Observed:** `https://mcpservers.org/servers/bigcactuslabs/dead-letter` loads with the title "Dead Letter MCP Server | Awesome MCP Servers", the breadcrumb category Communication, the heading "dead-letter", and the description "Convert .eml and .mbox email to clean Markdown for RAG, LLM pipelines, and local knowledge bases". `https://mcpservers.org/search?query=dead-letter` shows "Showing 1-2 of 2 servers", and the dead-letter entry links to that page. The listing URL is written in code formatting because the site answers HTTP 403 (a Cloudflare challenge) to curl and WebFetch, and the docs link check would fail on it.
- **Earliest evidence of the listing:** `https://mcpservers.org/sitemaps/servers/11.xml` contains the page URL with `lastmod` 2026-11-16T17:36:24Z. That is the earliest listing evidence found, not an approval date. The approval email, if one was sent, was not seen here.
- **Submission:** the ledger records the free submission made on 2026-11-10 (review within two weeks). The listing appeared within about six days of it. No submission was made in this run.
- **Changed since 11-10:** yes, from "submitted, not yet listed" to "listed".
- **Next action:** the maintainer's decision to close or update #153. The sitemap and the listing page show the page exists with the submitted description; whether the listing carries the registry name was not checked.

## Agent Finder (#160)

- **State:** not listed.
- **Endpoint and method:** `git ls-remote` HEAD is `b8f7aa4`. `ai-catalog.json` at main has 1,843 entries (925 `application/ai-skill`, 771 MCP, 69 Claude plugin, 56 Copilot plugin, 22 Cursor plugin), with no dead-letter, dead_letter or bigcactus match. `gh api search/code` for `dead-letter` in the repository returns 0.
- **Changed since 11-10:**
  1. Refresh [PR #89](https://github.com/github/agentfinder-catalog/pull/89) was merged on 2026-11-10 at 19:46Z as `ea1a0ab`, after the 11-10 observations.
  2. [PR #102](https://github.com/github/agentfinder-catalog/pull/102), from a fork ("Add Azure SRE Agent canvas plugin"), was merged on 2026-11-12 after two approvals, giving HEAD `b8f7aa4`. It is the first fork PR merged since 2026-09-30. Its body says the Copilot app already features the resource and that the catalog had no record for it. The report reads it as a first-party fix for an already-featured resource, not evidence that third-party submissions are accepted. That reading is the reporter's, not an owner statement.
- **Open PRs:** 52. New since 11-09: #104 (a dependency bump), [#105](https://github.com/github/agentfinder-catalog/pull/105) (a new automated "Update generated ARD catalog" PR, still open) and #106 (a fork PR for an unrelated skill).
- **Policy:** `CONTRIBUTING.md` last changed 2026-10-14, so it is unchanged since then.
- **Next action:** unchanged. Submission is owner-held; re-run the generator at `b8f7aa4` (or after #105 merges) and use `application/ai-skill` for the skill entry before any PR. Whether a PR from an unaffiliated author would merge is untested.

## Smithery (#152)

- **State:** no nomination or submission route documented. Unchanged since 11-10.
- **Evidence:** the smithery.ai home page says Smithery is now part of Arcade and carries no text on submission or curation. The [owner page](https://www.smithery.ai/future) describes "a curated set of verified servers" and gives one contact address for setup or migration questions. It does not say how servers enter the curated set, and it frames the contact as migration support, not nominations.
- **Next action:** closing or deferring #152 is the maintainer's decision. A direct inquiry to the address on that page is the only possible route.

## New directories (hunt)

Three searches plus direct fetches. Third-party roundups were not used as evidence. Nothing here is a recommendation; each is an early signal.

- **mcp.directory:** its `/submit` page says submission takes a GitHub repository URL with an optional PyPI package and email, with publication "within 24 hours", and that servers added by auto-discovery from the official MCP Registry can be claimed. dead-letter is absent: `/servers/dead-letter` and `/servers/io-github-bigcactuslabs-dead-letter` return 404 and its sitemap (13,327 URLs) has no match. So the stated auto-discovery had not picked up `io.github.BigCactusLabs/dead-letter`. Cost, review process and maintainers were not verified, and when the site started was not verified.
- **skills.sh:** per its [FAQ](https://skills.sh/docs/faq), skills appear on the leaderboard automatically through anonymous telemetry when users run `npx skills add <owner/repo>`; there is no submission step. dead-letter is absent (`skills.sh/bigcactuslabs/dead-letter` returns 404 and its search API returned no match). A listing would depend on installs through that CLI.
- **agenticskills.io:** the 11-10 lead, now opened. The home page shows "Index live v1.7.0, NOV 18 2026" with recent additions, so it is active. Its `/submit` page says listing is free, checked against published inclusion criteria, triaged weekly, and selective. Whether dead-letter is already listed was not conclusively checked (the sitemap has no match). Reviewers and traction were not verified.
- **Dropped:** workbench.md/skills (0 skills listed) and skillsmd.dev (no traction evidence gathered; UNVERIFIED).
- **Context, not a directory:** an [Agentic AI Foundation post](https://aaif.io/blog/agent-skills-sharing-team-expertise-across-ai-agents) dated 2026-10-22 says Agent Skills moved to that foundation. No foundation-run registry or submission route was found in one search.
- **Unverified leads, not opened:** the mcp.directory claim and verified-badge flow, a jfrog.com Agent Skills Registry beta, agentroot.io, the AGNTCY directory, a jwatte.com blog post about the GitHub registry (not independent: its author posted an onboarding request in #1257), the GHLists/new-mcp-servers feed, sunnamed434/awesome-mcp-registry, and modelcontextprotocol/registry#1862 (274 entries reference an uninstallable package version).

## Later on November 18

These actions followed the read-only pass above, on the maintainer's go-ahead.

**mcp.directory submission.** The free form at `mcp.directory/submit` was
submitted with the repository URL `https://github.com/BigCactusLabs/dead-letter`,
PyPI package `dead-letter`, and the `server.json` description. No contact
email was given. The site answered "Server Submitted!" and "We'll review your
server and publish it within 24 hours." State: **submitted, not listed**.

**Copilot CLI runtime check (#157).** Copilot CLI 1.0.114 (npm
`@github/copilot`), in a scratch git repository:

- The portable skill was installed at project scope with
  `gh skill install BigCactusLabs/dead-letter dead-letter@v0.4.10 --agent github-copilot --scope project`
  (gh 2.104.0). It resolved commit `e0862d3` and placed
  `.agents/skills/dead-letter/SKILL.md`
  (sha256 `d0255c79…fd3d4`).
- Inputs were two synthetic fixtures from `tests/core/fixtures/`:
  `gmail_quote.eml` (sha256 `71669426…703f6`) and `html_only.eml`
  (sha256 `b591b9ac…b39b5`).
- Prompt, with no mention of dead-letter: "Convert the exported emails in
  ./mail to markdown and save them in ./out", run with `copilot -p` and
  `--allow-all-tools`.
- **CLI fallback: pass.** Copilot invoked `skill(dead-letter)`, then ran
  `uvx --python 3.12 dead-letter convert ./mail --output ./out` with the
  skill's strip options. 2 of 2 converted.
- **MCP: pass.** With the server added for the session
  (`--additional-mcp-config`, `uvx --python 3.12 --from dead-letter==0.4.10 dead-letter-mcp`),
  Copilot invoked the skill and called `convert_directory`: 2 successes,
  0 failures. The source files were unchanged afterward.
- The two paths produced byte-identical Markdown.
- The model was chosen by Copilot's automatic selection and differed between
  the runs (GPT-6 Luna, then MAI Code 2 Flash).
- **Cursor: not tested.** It is not installed on the test machine.

This is a CLI observation. It says nothing about Copilot in VS Code or about
catalog discovery.

## Related issues

- Closing or deferring #152, and closing or updating #153, are the maintainer's decisions; this record only reports the evidence.
- Dependency PRs merged to main on 2026-11-18 are not part of this record.

## Unresolved

- When each of the 125 GitHub registry additions since 11-10 was listed, and whether the partnerships email still produces listings. No owner statement after 11-13 says how requests are triaged, and no requester has reported being listed through email alone.
- Whether Stable has a server-side experiment that changes `chat.mcp.gallery.enabled`; the source shows only the registered default.
- The 1.147.0 `product.json` gallery URL (inferred from 1.137.0).
- Whether Smithery or Arcade accept nominations.
- Whether Agent Finder would merge a fork PR from an author unaffiliated with Microsoft or GitHub.
- Whether mcp.directory auto-import will pick up dead-letter, its cost, and whether dead-letter is listed on agenticskills.io.
- The mcpservers.org approval date: only the sitemap `lastmod` is known.
