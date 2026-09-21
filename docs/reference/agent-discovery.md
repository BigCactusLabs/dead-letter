# Agent Discovery

How dead-letter reaches coding agents beyond its Claude plugin: the portable
[Agent Skill](../../skills/dead-letter/), repository-hosted
[ARD catalog](../../.well-known/ard.json), and separate submission paths.
For running the converter itself, use the [distribution map](distribution.md)
and [agent install guide](../../llms-install.md).

## Two skills, two audiences

| | `skills/dead-letter/` | `plugin/skills/dead-letter-context/` |
| --- | --- | --- |
| Audience | Hosts that support Agent Skills | Claude Code and Cowork |
| Spec | [agentskills.io](https://agentskills.io/specification) | Claude plugin skill |
| Content | MCP tools and the `uvx` CLI path | Slash commands and Cowork runtime conventions |
| Distribution | GitHub skills CLI, compatible installers, manual copy | BCL plugin marketplace |

Keep `/dead-letter:` commands, Cowork, and its `uploads/` conventions out of
the portable skill. `tests/plugin/test_portable_skill.py` enforces that split.
The root `skills/` directory is distribution source, not a host configuration
directory. Installing instructions does not itself authorize package
installation, email access, or broader tool permissions.

## Install

The portable skill and Claude plugin are independent. With GitHub CLI 2.90+
(or another version with skill support), select the intended host:

```bash
gh skill install BigCactusLabs/dead-letter dead-letter --agent claude-code
gh skill install BigCactusLabs/dead-letter dead-letter --agent codex
gh skill install BigCactusLabs/dead-letter dead-letter --agent github-copilot
```

Use project scope by default; add `--scope user` only when installing for
all repositories is intended. Review the skill before installation and
preserve existing host configuration.

The unpinned command resolves the latest tagged release, falling back to the
default branch when necessary. **This repository also has `plugin-v*` tags;
latest is not a guarantee of a `vX.Y.Z` package snapshot.** Pin a reviewed
package release that contains the skill, replacing `X.Y.Z` below:

```bash
gh skill install BigCactusLabs/dead-letter dead-letter@vX.Y.Z --agent codex
```

The `@tag`/`@commit` suffix belongs to the **skill name**, not the repository.
A full commit pin avoids a moving ref; `@main` deliberately selects development
source and is not a stable release pin. Do not silently switch an existing
installation to `main`. See the [CLI manual](https://cli.github.com/manual/gh_skill_install)
for `--pin`, update behavior, scopes, and supported hosts.

GitHub CLI may add `metadata.github-*` tracking keys and reorder front matter;
those are expected installer metadata, not a reason to rewrite the upstream
skill. Its update command relies on that tracking information.

Other installers can be used deliberately. With the Vercel skills CLI:

```bash
npx skills add BigCactusLabs/dead-letter
```

Select the portable `dead-letter` skill rather than automatically installing
every skill found in the repository. For manual installation from an inspected
checkout, create the intended host directory and copy only the portable skill;
do not overwrite an existing installation without reviewing it:

```bash
mkdir -p .claude/skills
cp -R skills/dead-letter .claude/skills/   # Claude Code
# For a host configured to read .agents/skills instead:
mkdir -p .agents/skills
cp -R skills/dead-letter .agents/skills/
```

Confirm the chosen host's skill directory and behavior. A successful copy does
not prove loading, task selection, or converter availability. This repository
does not ship a `.cursor-plugin/marketplace.json`; do not confuse a portable
skill install with Cursor's native plugin-marketplace import.

## Local validation

The repository CI gates are:

```bash
gh skill publish --dry-run
uv run pytest -q tests/plugin
python scripts/release.py check
```

The skill dry run discovers both the portable skill and
`plugin/skills/dead-letter-context/SKILL.md`. It validates front matter,
including names matching directory names and string-valued `allowed-tools`.
It publishes nothing. See [the workflow](../../.github/workflows/ci.yml) and
[`gh skill publish`](https://cli.github.com/manual/gh_skill_publish).

An upstream ARD conformance run is an **additional** check, not a command
currently wired into this CI. Use a reviewed checkout of
[ards-project/ard-spec](https://github.com/ards-project/ard-spec), record its
commit, follow its prerequisites, and pass the absolute path to this catalog:

```bash
conformance/bin/conformance-test manifest /absolute/path/to/dead-letter/.well-known/ard.json
```

Repo-side JSON/version/shape tests are useful but are not proof of all future
upstream schema rules or live discovery. Host tests should record agent
version, install scope, skill revision, selected workflow, and successful
synthetic conversion—not merely file presence.

## The ARD catalog

[`.well-known/ard.json`](../../.well-known/ard.json) is the repository's
[ARD](https://agenticresourcediscovery.org/spec/) v0.91 manifest with two entries:

- `urn:air:github.com:bigcactuslabs:dead-letter:mcp-server`, type
  `application/mcp-server-card+json`, pointing at source `server.json`.
- `urn:air:github.com:bigcactuslabs:dead-letter:skill`, type
  `application/ai-skill+md`, pointing at the portable `SKILL.md`.

Representative queries cover exported email conversion, reading `.eml`, RAG
preparation, and local archives. These are discovery intents, not measured
search ranking or permission to install.

**Source versus publication.** `server.json` on `main` is a release template:
its MCPB hash is a placeholder and OCI is injected after verification. It is
not the final registry manifest. Resolve installation against a published
registry record/release, not an assumed live artifact in source metadata.
The [publishing runbook](publishing.md) explains archived resolved manifests
for releases using the refreshed workflow.

**Known limitation.** ARD is domain-anchored: discovery expects a manifest at
`https://<domain>/.well-known/ard.json`. This repo's raw source URL is:

```text
https://raw.githubusercontent.com/BigCactusLabs/dead-letter/main/.well-known/ard.json
```

That is not an owned-domain discovery endpoint. Organization-domain hosting
is separate follow-through. Do not enable GitHub Pages or claim automatic
crawler discovery as part of publishing this source file.

**Release note.** Both entry versions follow the package version and are
checked against `server.json`. They do not prove a matching skill was present
on every older package tag. Inspect the selected tag's files. Use the release
helper to keep source metadata aligned without copying current versions into
multiple guides.

## Submitting to GitHub Agent Finder

[Agent Finder](https://github.blog/changelog/2026-06-17-agent-finder-for-github-copilot-now-available/)
and its [public catalog](https://github.com/github/agentfinder-catalog) have a
separate curation process. Check the current contribution policy before
submission. MCP registry publication alone is not evidence that a named
client query returns dead-letter; record the actual catalog/client result.
The skill submission is separate from MCP-server metadata.

The prepared skill-entry shape below follows the catalog's own `urn:ai:`
identifier and `application/ai-skill` media type, not ARD's `urn:air:` scheme:

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

In a fork of that catalog, add `catalog/bigcactuslabs/dead-letter.json`, run
`python3 scripts/generate_ai_catalog.py` and its `--check` mode according to
the upstream instructions, and submit the source entry plus generated output.
Review whether the catalog permits a released commit URL instead of the
moving `main` example; do not invent acceptance of either update policy.
Record submission URL, source revision, license review, and eventual outcome.
A prepared entry or submitted PR is not an accepted listing.

## Sources

- [Agent Skills specification](https://agentskills.io/specification)
- [`gh skill install`](https://cli.github.com/manual/gh_skill_install) and
  [`gh skill publish`](https://cli.github.com/manual/gh_skill_publish)
- [ARD spec](https://agenticresourcediscovery.org/spec/) and
  [conformance project](https://github.com/ards-project/ard-spec)
- [Agent Finder catalog](https://github.com/github/agentfinder-catalog) and
  [announcement](https://github.blog/changelog/2026-06-17-agent-finder-for-github-copilot-now-available/)
