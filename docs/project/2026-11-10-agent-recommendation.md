# Getting AI assistants to recommend dead-letter (2026-11-10)

This record answers one question: what makes ChatGPT, Claude and similar
assistants name dead-letter when someone asks for a tool it fits, and what the
project should change. It combines a live probe of two assistants with a
review of published research and platform documentation. The probes and
lookups were read-only. No listing, form or directory was changed. The
metadata changes recommended in [What this record changes](#what-this-record-changes)
land in the same PR as this record and reach users with the next package and
plugin releases.

## Summary

An assistant names a niche tool when three things happen in order: the model
runs a web search, a search result names the tool for the user's exact task,
and that result's facts are current. On 2026-11-10 dead-letter cleared the
second step for three of five task phrasings. It failed mostly at the first
step (Claude answered three of five questions without searching) and the third
(when Claude did find dead-letter, it repeated stale claims that it has no
MBOX support and has a conflicting license).

The stale MBOX claim traces to the PyPI summary, which still described `.eml`
only, and to directories that copy it. Neither model knew dead-letter without
search, so training-data recall is not a lever on any useful timescale.

## Probe method

Five questions, written the way a user would ask, each ending with a request
for specific tools:

| ID | Question |
| --- | --- |
| q1 | Best way to convert a folder of `.eml` files into Markdown for an LLM or RAG pipeline |
| q2 | Turn a Google Takeout `.mbox` into Markdown notes searchable in Obsidian |
| q3 | An MCP server that lets an AI agent read and convert exported `.eml` or `.mbox` files locally |
| q4 | A Python library or CLI that converts EML or MBOX to Markdown with YAML front matter |
| q5 | Archiving old email as plain text or Markdown for long-term preservation |

- **GPT:** `codex exec` (codex-cli 0.167.0), model `gpt-6.1-sol` at medium
  effort, live web search, read-only sandbox, user config ignored and the
  memories feature disabled. A first run with memories on was discarded
  because the local memory store already mentioned dead-letter.
- **Claude:** `claude -p` (Claude Code 2.1.322), the `sonnet` alias, only
  `WebSearch` and `WebFetch` allowed, no MCP servers, no slash commands, and a
  neutral system prompt that permits web search. Claude's web search used
  Brave when it launched ([Simon Willison](https://simonwillison.net/2025/Mar/21/anthropic-use-brave/)).
  Later reports say Anthropic's subprocessor list names further web-search
  providers ([TofuBofu](https://tofubofu.com/blog/what-search-engine-does-claude-use),
  [Ryan Doser](https://ryandoser.com/what-search-engine-does-claude-use/)); the
  list itself could not be opened for this record. Anthropic also runs its own
  `Claude-SearchBot` crawler ([crawler documentation](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler)).
  Treat Brave as one input, not the whole route.
- **No-search controls:** one run of each model with search off.
- Each question ran once per engine. These are observations, not rates.

## Results

| Question | GPT with search | Claude with search |
| --- | --- | --- |
| q1 `.eml` folder for RAG | Searched; named dead-letter | Did not search; suggested MarkItDown, Unstructured, Pandoc |
| q2 Takeout to Obsidian | Searched; picked mbox-md | Did not search; suggested mb2md-style scripts, Pandoc |
| q3 Local MCP server | Searched; named dead-letter | Searched; named it second, after mbox-mcp |
| q4 Python library or CLI | Searched; named dead-letter | Searched; named it as the recommendation |
| q5 Preservation | Searched; did not name it | Did not search |

- With search off, neither model named dead-letter.
- Claude found dead-letter both times it searched. Its citations were the
  Enterprise DNA and Socket listings and the PyPI page, not the GitHub README.
- GitHub traffic for the 14 days to 2026-11-10 showed 108 views from 57
  unique visitors and no AI-assistant referrers. This is weak evidence:
  ChatGPT links were reported to arrive without a referrer from 2026-10-09
  ([LLM Pulse](https://llmpulse.ai/blog/chatgpt-referral-traffic-fell/)).
- Claude's connector search returned nothing for this task category.
- The [Takeout to Obsidian recipe](../recipes/takeout-to-obsidian.md) landed
  on 2026-11-05, five days before the probe, and is too new to judge.

## Wrong facts in the answers

Both Claude answers that named dead-letter added wrong caveats:

- It said it found nothing confirming that dead-letter reads MBOX, and told
  the user to split the archive into `.eml` files first. MBOX conversion has
  shipped in the CLI and Python API since 0.4.0 and in the MCP server since
  0.4.5.
- It reported a license conflict: an older listing said MIT and a newer one
  said PolyForm Noncommercial. The project switched from MIT to PolyForm
  Noncommercial 1.0.0; the MIT listings predate that change.

Sources of the stale MBOX claim:

- The PyPI summary for 0.4.10 reads "Convert .eml email exports to clean,
  LLM-ready Markdown with YAML front matter", and its keywords include no
  `mbox` or `takeout`. Both come from `pyproject.toml`, which was not updated
  when MBOX support shipped. The GitHub repository description had been.
- The [Enterprise DNA listing](https://enterprisedna.co/directories/mcp/bigcactuslabs-dead-letter)
  lists `convert_mbox` among the tools but says under its cons that
  dead-letter "only supports the .eml format".

Both models also raise the noncommercial license as a caveat whenever they
name dead-letter. That is a licensing decision, not a metadata defect, and
this record does not propose a change.

## What this record changes

| Change | Why | Reaches users |
| --- | --- | --- |
| `pyproject.toml` description names `.mbox` and Gmail Takeout; keywords add `mbox`, `mbox-to-markdown`, `gmail-takeout`, `google-takeout` | PyPI summary is what directories and search snippets copy | Next package release |
| MCP server returns `instructions` at initialization | Claude Code's tool search loads only tool names and server instructions at session start, and cuts instructions at 4,096 characters ([Claude Code MCP docs](https://code.claude.com/docs/en/mcp.md)) | Next package release |
| Portable skill description names `.mbox`, Google Takeout, Obsidian and Markdown notes, and excludes live mailboxes | Skill selection depends on how closely a request matches the description | Repository now; plugin copy with the next plugin release |
| Plugin context skill description names `.mbox`, Gmail Takeout and Obsidian workflows | Same | Next plugin release |
| ARD skill entry and Docker catalog template name `.mbox` and Gmail Takeout | Keeps catalog text consistent with the package | Next catalog render |
| README headline, lead, quick start, use cases and features name `.mbox` and Gmail Takeout; the license section says which release was MIT | Before this change the README first mentioned MBOX conversion under Known Limitations. The README is the GitHub page and the PyPI long description, the two pages assistants cited | GitHub on merge; PyPI with the next package release |
| Root `context7.json` sets a title that includes "email", a factual description, the folders to index and usage rules | A Context7 search for "dead-letter" returns message-queue libraries, and the project is not indexed. The file takes effect only after the repository is submitted (see [follow-up research](#follow-up-research-same-day)) | After a Context7 submission |

The descriptions avoid promotional wording. It raises tool selection in
experiments ([Faghih et al.](https://arxiv.org/abs/2505.18135)) but conflicts
with directory policies; query-to-description match is the documented driver
([BiasBusters](https://arxiv.org/abs/2510.00307)).

## Remaining actions, ranked

1. **Claude directory approval.** Submitted 2026-10-29 as a plugin. Anthropic
   documents that directory connectors are automatically eligible for in-chat
   Suggested Connectors, ranked by usage
   ([connectors directory](https://claude.com/docs/connectors/directory)), and
   that a local server can be listed only inside a plugin
   ([connector submission](https://claude.com/docs/connectors/building/submission)).
   Claude's plugin search already returns community plugins from the directory,
   so an approved listing should become a suggestion candidate. That last step
   is an inference from observed behavior; the documentation states it for
   connectors only.
2. **Third-party pages that name dead-letter for a specific task.** This has
   the strongest evidence and takes the longest.
   - For software-product queries in the US, web-enabled GPT cited 72.7%
     earned (third-party) sources against Google's 45.4%
     ([Chen et al.](https://arxiv.org/html/2509.08919v1), August 2025 data,
     not peer-reviewed).
   - Across 112 startups, ChatGPT recognized 99.4% by name but surfaced 3.32%
     on discovery questions. For Perplexity, referring domains (r = 0.319) and
     Reddit presence (r = 0.395) predicted discovery and GEO scores did not
     ([Sharma](https://arxiv.org/abs/2601.00912)). Both studies used older
     models and show correlation, not cause.
   - The [November 5 plan](2026-11-05-discovery-without-social-posting.md)
     already covers PyCoder's Weekly and a tested Thunderbird
     ImportExportTools NG workflow. Add answers to existing Takeout-to-Obsidian
     and `.eml`-to-Markdown questions on the Obsidian forum and Stack Exchange,
     with authorship disclosed. q2 is the question GPT gave to mbox-md.
3. **Ask Enterprise DNA to refresh its listing** so its cons stop
   contradicting its tool list. Socket mirrors PyPI and should update after
   the next release.
4. **Gemini CLI extension gallery.** Add a root `gemini-extension.json` in
   the next `prepare X.Y.Z` PR, not before. The
   [releasing docs](https://geminicli.com/docs/extensions/releasing/) ask for
   the `gemini-cli-extension` topic and a tag, and say the crawler finds
   manifests through GitHub Code Search. In a sample of 24 listed
   repositories, 14 had no such topic and 6 had no tags, so a manifest on
   `main` would probably be listed. Installation is the constraint:
   `gemini extensions install` downloads the source archive of the latest
   GitHub release ([source](https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/config/extensions/github.ts)),
   and `v0.4.10` has no manifest, so a listing made before the next release
   would advertise an install that fails. This was read from the source, not
   run. The existing `skills/dead-letter/SKILL.md` loads unchanged
   ([reference](https://geminicli.com/docs/extensions/reference/)), no topic
   needs to be dropped, and Qwen Code installs the same manifest.
5. **Company site.** `bigcactuslabs.xyz` has no sitemap (`/sitemap.xml`
   returns 404) and disallows `Google-Extended`, which removes it from Gemini
   Apps grounding but not from Google Search
   ([Google crawler docs](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers)).
   Its `robots.txt` is Cloudflare's managed list: it blocks the training
   crawlers (`GPTBot`, `ClaudeBot`, `CCBot` and others) and leaves the search
   crawlers (`OAI-SearchBot`, `Claude-SearchBot`, `PerplexityBot`, Bingbot)
   open. Whether Cloudflare blocks them at the edge was not tested.
   The effect on dead-letter is small because GitHub and PyPI are the cited
   pages and both admit AI search crawlers. Decide whether the block is
   intended, add a sitemap, and verify the site in Bing Webmaster Tools.
6. **ChatGPT apps.** OpenAI limits local MCP submissions to approved
   partners; a skills-only desktop plugin may be possible but needs submission
   access confirmed by an OpenAI representative
   ([desktop-only plugins](https://developers.openai.com/plugins/deploy/desktop-only)).
   ChatGPT already finds dead-letter through search on three of five
   questions, so items 2 and 3 are the ChatGPT plan.

Not recommended:

- More `llms.txt` work. In a log study of 137,210 domains, 97% of `llms.txt`
  files received no requests ([Ahrefs](https://ahrefs.com/blog/llmstxt-study/)).
- Rewriting pages for "generative engine optimization". GEO scores did not
  predict discovery in the Sharma study.
- Paid AI-visibility trackers. The harness below covers the same ground for
  a small API bill.

## Follow-up research (same day)

A second pass on 2026-11-10 looked for levers this record had missed. It ran
as three research passes and an independent GPT search pass. It corrected the
Gemini gallery item, the search-backend note and the referrer note above, and
added the README and `context7.json` rows to the changes table. None of the
levers below has a measured effect for a project this small.

Added levers, in order:

1. **skills.sh.** Vercel's `find-skills` skill tells agents to run
   `npx skills find` when a user asks how to do a task
   ([skill text](https://raw.githubusercontent.com/vercel-labs/skills/main/skills/find-skills/SKILL.md)).
   A search for `mbox` on 2026-11-10 returned skills with one to four
   installs and nothing from this project. The directory is filled by install
   telemetry from the `skills` CLI ([docs](https://www.skills.sh/docs)), so a
   listing starts with someone running `npx skills add BigCactusLabs/dead-letter`.
   The same skill tells agents to be cautious below 100 installs and 100
   stars, so expect caveats. Owner decision.
2. **Context7.** Submit the repository through
   [Context7's form](https://context7.com/docs/adding-libraries) once
   `context7.json` is on `main`. The benefit is correct facts for an agent
   already using dead-letter; a discovery effect is unmeasured. Owner action.
3. **Stale copies.** Extend item 3 above to every directory that copied the
   old description (Glama, PulseMCP, Socket, libraries.io). Bing says
   near-duplicate pages are grouped and one is chosen to represent the set
   ([Bing](https://blogs.bing.com/webmaster/December-2025/Does-Duplicate-Content-Hurt-SEO-and-AI-Search-Visibility)).
4. **Owned task pages.** Only a domain the project controls can have a
   sitemap, IndexNow and Bing Webmaster verification, and a title that matches
   the task wording. Brave has no webmaster console; it documents a
   [re-fetch form](https://search.brave.com/help/brave-search-crawler) for
   outdated pages. This supports the Takeout page in the November 5 plan.

Found and not recommended:

- Schema.org JSON-LD. Ahrefs tracked 1,885 pages that added it and found no
  significant change in AI citations
  ([Ahrefs](https://ahrefs.com/blog/schema-ai-citations/)).
- Tuning classifiers, docstrings or `--help` so coding agents pick the package
  unprompted. Agents added a new dependency in 1.3% of 26,760 agent-written
  pull requests ([Twist and Zhang](https://arxiv.org/html/2512.11589v2)).
- No lever was found that a tool author controls over whether Claude decides
  to search. Anthropic's guidance puts the fix in the calling application's
  prompt ([Sonnet 5.5 guide](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5)).

## Measurement plan

The harness has not been built. Four findings from 2026-11-10 change the
plan below before anyone builds it:

- Google's [Gemini API terms](https://aistudio.google.com/docs/terms) forbid
  analysing grounded results or collecting their links by automated means.
  Leave Gemini out unless that is resolved.
- Perplexity ended Sonar Chat Completions support on 2026-09-27; the
  replacement is its Agent API
  ([migration guide](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview.md)).
- Bing's AI performance report covers Copilot and Bing, and does not name
  ChatGPT ([Bing](https://blogs.bing.com/webmaster/February-2026/Introducing-AI-Performance-in-Bing-Webmaster-Tools-Public-Preview)).
- Record the system prompt and effort level with every run; both change
  whether Claude searches.

- **Prompt set:** about 20 prompts in five groups: `.eml` to Markdown, Takeout
  and Obsidian, LLM and RAG preparation, MCP servers, and branded controls
  that name dead-letter.
- **Engines:** OpenAI with `web_search`, Anthropic with `web_search`, Gemini
  with Google Search grounding, Perplexity Sonar, plus one arm with search off.
- **Sampling:** five runs per prompt, pooled per engine (100 runs). Single
  prompts are too noisy to read: with zero hits in 100 pooled runs, the 95%
  Wilson upper bound is 3.7%.
- **Scores:** mention rate, citation rate, whether search ran, the competitor
  set, and whether the stated formats, license and install command are
  correct. Not rank.
- **Cadence:** monthly, plus one run two to four weeks after each change
  ships. Expected cost is roughly $5–20 a month.
- **Passive signals:** a weekly snapshot of GitHub traffic referrers (GitHub
  keeps 14 days) and Bing Webmaster Tools' AI performance report once the
  site is verified.

## Limits

- One run per question per engine; the hit counts are observations, not
  estimates.
- Perplexity, Gemini and Copilot were not probed.
- The follow-up research relied on vendor studies for several figures, and
  the gallery, Context7 and skills.sh observations are single checks made on
  2026-11-10.
- Not reachable during research: help.openai.com (HTTP 403) and DeepWiki
  (HTTP 429).
- The research studies use models from 2025 and early 2026, and the earned-media
  results are observational.
