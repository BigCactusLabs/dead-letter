# Agent Discovery

How dead-letter reaches coding agents outside Claude Code: the portable Agent
Skill in [`skills/dead-letter/`](../../skills/dead-letter/), the ARD catalog in
[`.well-known/ard.json`](../../.well-known/ard.json), and the submission steps
for GitHub's Agent Finder.

## Two skills, two audiences

| | `skills/dead-letter/` | `plugin/skills/dead-letter-context/` |
|---|---|---|
| Audience | Any host that reads Agent Skills | Claude Code and Cowork only |
| Spec | [agentskills.io](https://agentskills.io/specification) | Claude plugin skill |
| Content | MCP tools plus the `uvx` CLI path | Slash commands, Cowork runtime detection |
| Shipped by | `gh skill install`, `npx skills add`, manual copy | The plugin marketplace |

The portable skill must never mention `/dead-letter:` slash commands, Cowork, or
the Cowork `uploads/` directory: no other host has them. The Claude-specific
skill keeps that material. `tests/plugin/test_portable_skill.py` enforces the
split.

`skills/` at the repo root is deliberate. Claude Code reads `.claude/skills`,
while Codex, Cursor, Copilot, Amp, and Gemini CLI share `.agents/skills`, and
Copilot also reads `.github/skills` and `~/.copilot/skills`. No host auto-loads
a root `skills/` directory, so the portable skill stays out of the way during
development on this repo and is only loaded where someone installs it.

## Install

The skill is independent of the Claude plugin. Installing it does not install
the plugin, and the plugin does not install it.

With the GitHub CLI (`gh` 2.90 or newer), pick the agent you want:

```bash
gh skill install BigCactusLabs/dead-letter dead-letter --agent claude-code
gh skill install BigCactusLabs/dead-letter dead-letter --agent codex
gh skill install BigCactusLabs/dead-letter dead-letter --agent github-copilot
```

Add `--scope user` to install for every repository instead of the current one.

`gh skill install` resolves the repository's latest tag before the default
branch, so the unpinned form installs the skill as of the newest `vX.Y.Z`
release. Add a `@main` pin to track the default branch instead.

The installed frontmatter gains `metadata.github-*` tracking keys, and `gh`
rewrites the key order; both are expected and `gh skill update` relies on them.
See [`gh skill install`](https://cli.github.com/manual/gh_skill_install).

With the Vercel skills CLI:

```bash
npx skills add BigCactusLabs/dead-letter
```

Manually, copy the directory into whichever path your host reads:

```bash
cp -R skills/dead-letter .claude/skills/     # Claude Code
cp -R skills/dead-letter .agents/skills/     # Codex, Cursor, Copilot, Amp, Gemini CLI
```

Cursor reads `.agents/skills/`, so `gh skill install ... --agent cursor` and
the manual copy both work. Cursor's own "import from GitHub" UI only accepts a
Cursor plugin marketplace (`.cursor-plugin/marketplace.json`), which this repo
does not ship.

## Local validation

Both gates run in CI; run them locally before pushing a skill or catalog change.

```bash
gh skill publish --dry-run
```

This discovers `skills/*/SKILL.md` and, in this repo, also
`plugin/skills/dead-letter-context/SKILL.md`, so the gate covers both skills.
It checks that the frontmatter `name` matches the directory name, that `name`
and `description` are present, and that `allowed-tools` is a string rather
than a list. It publishes nothing and needs no usable token. Renaming either
skill directory without changing its `name` turns this step red. CI runs it
as the `Agent Skill validation` step in the `test` job of
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). See
[`gh skill publish`](https://cli.github.com/manual/gh_skill_publish).

For the ARD catalog, use the conformance tool from
[`ards-project/ard-spec`](https://github.com/ards-project/ard-spec):

```bash
conformance/bin/conformance-test manifest .well-known/ard.json
```

`uv run pytest -q tests/plugin` covers the repo-side contracts for both files.

## The ARD catalog

[`.well-known/ard.json`](../../.well-known/ard.json) is an
[Agentic Resource Discovery](https://agenticresourcediscovery.org/spec/) v0.91
manifest with two entries:

- `urn:air:github.com:bigcactuslabs:dead-letter:mcp-server` — type
  `application/mcp-server-card+json`, pointing at `server.json`
- `urn:air:github.com:bigcactuslabs:dead-letter:skill` — type
  `application/ai-skill+md`, pointing at the portable `SKILL.md`

Each entry carries `representativeQueries` covering the four intents the
catalog is meant to answer: converting exported email to Markdown, reading a
`.eml` file, preparing email for RAG, and building a local email archive.

**Known limitation.** ARD is domain-anchored by spec: a crawler expects the
manifest at `https://<domain>/.well-known/ard.json`. This repo has no GitHub
Pages site and no custom domain, so today the catalog is only reachable at

```
https://raw.githubusercontent.com/BigCactusLabs/dead-letter/main/.well-known/ard.json
```

That path is not domain-anchored, so spec-conformant crawlers will not find it
on their own. Hosting the manifest at an organization root domain is the
follow-up. Do not enable GitHub Pages for this repository to work around it.

**Release note.** Both entries carry a `version` field that means the package
release, which makes this file a version sync point alongside `server.json`.
The first release tag that ships the skill is the first one whose `version`
is accurate for the skill entry. Release prep bumps it; see
[publishing.md](publishing.md). `tests/plugin/test_ard_catalog.py` fails if
either entry drifts from `server.json`.

## Submitting to GitHub Agent Finder

[Agent Finder](https://github.blog/changelog/2026-06-17-agent-finder-for-github-copilot-now-available/)
implements ARD, but its public catalog is PR-curated at
[`github/agentfinder-catalog`](https://github.com/github/agentfinder-catalog)
rather than crawled.

MCP servers reach Agent Finder through GitHub's MCP catalog, which is fed from
the official MCP registry where dead-letter is already published. So the
community submission covers the **skill only**.

Steps:

1. Fork `github/agentfinder-catalog`.
2. Add `catalog/bigcactuslabs/dead-letter.json` with the entry below.
3. Run `python3 scripts/generate_ai_catalog.py`, then
   `python3 scripts/generate_ai_catalog.py --check`.
4. Open a pull request containing both the catalog file and the regenerated
   output.

```json
{
  "identifier": "urn:ai:github.com:bigcactuslabs:dead-letter:dead-letter",
  "displayName": "dead-letter",
  "mediaType": "application/ai-skill",
  "url": "https://github.com/BigCactusLabs/dead-letter/blob/main/skills/dead-letter/SKILL.md",
  "description": "Converts .eml email exports (Gmail, Outlook, Apple Mail) to Markdown with YAML front matter for reading, summarizing, RAG, or a local email archive; runs locally via uvx or the dead-letter MCP server.",
  "tags": ["email", "eml", "markdown", "rag", "email-archive", "local-first"],
  "metadata": { "sourceSet": "BigCactusLabs/dead-letter", "repoPath": "skills/dead-letter/SKILL.md" }
}
```

Note the `urn:ai:` prefix and `application/ai-skill` media type: the Agent
Finder catalog uses its own identifier scheme, not the `urn:air:` scheme in
`.well-known/ard.json`.

## Sources

- [agentskills.io specification](https://agentskills.io/specification)
- [`gh skill install`](https://cli.github.com/manual/gh_skill_install) and
  [`gh skill publish`](https://cli.github.com/manual/gh_skill_publish)
- [ards-project/ard-spec](https://github.com/ards-project/ard-spec) and the
  [ARD spec](https://agenticresourcediscovery.org/spec/)
- [github/agentfinder-catalog](https://github.com/github/agentfinder-catalog)
- [Agent Finder changelog](https://github.blog/changelog/2026-06-17-agent-finder-for-github-copilot-now-available/)
