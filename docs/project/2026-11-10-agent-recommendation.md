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
  neutral system prompt that permits web search. Claude's web search is
  backed by Brave ([Simon Willison](https://simonwillison.net/2025/Mar/21/anthropic-use-brave/)).
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
  unique visitors and no AI-assistant referrers.
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
4. **Gemini CLI extension gallery.** The gallery is filled by a daily crawl of
   public repositories that have the `gemini-cli-extension` topic, a root
   `gemini-extension.json` and a release tag
   ([releasing docs](https://geminicli.com/docs/extensions/releasing/)). An
   extension can bundle stdio MCP servers and `skills/`
   ([reference](https://geminicli.com/docs/extensions/reference/)). Needs a
   manifest and the next tag.
5. **Company site.** `bigcactuslabs.xyz` has no sitemap (`/sitemap.xml`
   returns 404) and disallows `Google-Extended`, which removes it from Gemini
   Apps grounding but not from Google Search
   ([Google crawler docs](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers)).
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

## Measurement plan

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
- Not reachable during research: help.openai.com (HTTP 403) and DeepWiki
  (HTTP 429).
- The research studies use models from 2025 and early 2026, and the earned-media
  results are observational.
