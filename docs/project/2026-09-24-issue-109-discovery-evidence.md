# Issue #109 discovery evidence — 2026-09-24

Audit record for the portable-skill and ARD follow-ups
[#157](https://github.com/BigCactusLabs/dead-letter/issues/157)–[#160](https://github.com/BigCactusLabs/dead-letter/issues/160).
Durable guidance lives in [Agent Discovery](../reference/agent-discovery.md);
this page records what was observed and when. It is history, not a work queue.

All runs used scratch project directories and the synthetic fixtures
`tests/core/fixtures/gmail_quote.eml` and `html_only.eml`. Nothing was
published or submitted as part of these checks.

## Runtime use (#157)

Skill installed with
`gh skill install BigCactusLabs/dead-letter dead-letter@v0.4.0 --agent codex`
(project scope, commit `049faa6`, placed in `.agents/skills/dead-letter`).
The task prompt was “Convert the exported emails in ./mail to markdown and
save them in ./out”, with no mention of dead-letter or the skill.

| Host | Path | Skill selected | Conversion |
| --- | --- | --- | --- |
| Codex CLI 0.156.1 (gh 2.101.0, uv 0.12.18) | CLI fallback, no MCP configured | Yes: read `SKILL.md`, then ran `uvx --python 3.12 dead-letter convert mail/ --output out/ …` | 2/2 files with YAML front matter; resolved package 0.4.0 |
| Codex CLI 0.156.1 | MCP (`uvx --python 3.12 --from 'dead-letter[mcp]==0.4.0' dead-letter-mcp`) | Yes: read `SKILL.md`, then called `convert_directory` | 2/2 successes; output byte-identical to the CLI path |
| GitHub Copilot CLI | — | Not tested: host not installed | — |
| Cursor / cursor-agent | — | Not tested: host not installed | — |

The installed skill contains the untrusted-email rule. Earlier Claude Code
end-to-end evidence from #112 stands and does not cover other hosts.

## Install resolution (#158)

`gh` 2.101.0, project scope, fresh repository per case. All eight installs
exited 0; installed bodies matched the source at the resolved ref, apart from
the `metadata.github-*` keys `gh` adds.

| Ref | Resolved to |
| --- | --- |
| unpinned (`--agent codex`, `claude-code`, `github-copilot`, `cursor`) | release `v0.4.0` → `049faa6` |
| `@v0.4.0` | `049faa6` |
| `@plugin-v0.4.0` | `049faa6` (both tags peel to the same commit) |
| `@049faa63…` | `049faa6` |
| `@main` | `8893a45` |

Unpinned installs resolve the latest GitHub **release**. `plugin-v*` tags
have no releases, so they are not selected. `--agent cursor` and
`--agent github-copilot` both place the skill in `.agents/skills`; Claude
Code uses `.claude/skills`.

`gh skill publish --dry-run` passed on a clean clone at `8893a45`, warning
only that no tag-protection ruleset exists. A real publish validates the
skills, offers to add the `agent-skills` topic, and creates a GitHub release
for a tag. Package releases already produce the GitHub release that
`gh skill install` resolves, so no separate skill publication was made. The
`agent-skills` repository topic was added on 2026-09-24.

## ARD (#159)

The upstream conformance tool at
[`ards-project/ard-spec@b76f235`](https://github.com/ards-project/ard-spec/tree/b76f235a8f461876ad4f1e77abd0eb0eb302b48d)
(spec v0.91, Proposal, 2026-08-26) passed the source manifest with 0 errors,
0 warnings and one informational note on the `application/ai-skill+md`
extension type. Its optional strict JSON Schema check was skipped because
`jsonschema` was not installed.

The owner selected `bigcactuslabs.xyz` as the serving host. It is served by
Cloudflare from the organization's site repository, and
`/.well-known/ard.json` returned 404 on 2026-09-24. Deployment is not done.
The resolved release asset `dead-letter-server-0.4.0.json` carries the MCPB
hash and OCI digest, so a hosted catalog can reference it instead of the
source-template `server.json`.

## Agent Finder (#160)

At [`github/agentfinder-catalog@666cf8a`](https://github.com/github/agentfinder-catalog/tree/666cf8a51e2329d577e73d6e3c00fa5eae55ce2c):

- `CONTRIBUTING.md` accepts skills and MCP servers through one PR process;
  the prepared skill entry has every documented required field.
- dead-letter is absent from `ai-catalog.json` (2,107 entries) and from
  GitHub's MCP catalog (288 entries); no open PR mentions it.
- Merged PRs between 2026-08-24 and 2026-09-23: 3, none from forks.
- The catalog repository is MIT-licensed; no rule was found requiring the
  listed skill itself to be open source. dead-letter's PolyForm Noncommercial
  license is a review point for the owner, not a documented blocker.
- Retrieval can be checked in the public search page at
  [github.com/agentfinder](https://github.com/agentfinder), but only after a
  listing exists.

The submission is prepared, not sent.
