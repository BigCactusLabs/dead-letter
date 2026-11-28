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

The unpinned command resolves the latest **GitHub release** (not the newest
tag), falling back to the default branch when no release exists. Package
releases (`vX.Y.Z`) are GitHub releases; `plugin-v*` tags are bare tags with
no release, so they do not change what the unpinned command installs. That
holds only while plugin tags stay release-less. Pin a reviewed package
release that contains the skill, replacing `X.Y.Z` below:

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
synthetic conversion—not merely file presence. Recorded host results live in
[the #109 discovery evidence record](../project/2026-09-24-issue-109-discovery-evidence.md);
a result covers only the host and version it names.

## The ARD catalog

[`.well-known/ard.json`](../../.well-known/ard.json) is the repository's
[ARD](https://agenticresourcediscovery.org/spec/) v0.91 manifest with two entries:

- `urn:air:github.com:bigcactuslabs:dead-letter:mcp-server`, type
  `application/mcp-server-card+json`, pointing at source `server.json`.
- `urn:air:github.com:bigcactuslabs:dead-letter:skill`, type
  `application/agent-skills+md`, pointing at the portable `SKILL.md`. This is
  the ARD v0.91 standard skill type (ard-spec `cbe4bbf`, 2026-10-11); the entry
  used `application/ai-skill+md` before 2026-11-04. See the
  [November 4 refresh](../project/2026-11-04-distribution-refresh.md#ard-media-type-159).

Representative queries cover exported email conversion, reading `.eml`, RAG
preparation, and local archives. These are discovery intents, not measured
search ranking or permission to install.

**Source versus publication.** `server.json` on `main` is a release template:
its MCPB hash is a placeholder and OCI is injected after verification. It is
not the final registry manifest. Resolve installation against a published
registry record/release, not an assumed live artifact in source metadata.
The [publishing runbook](publishing.md) explains archived resolved manifests
for releases using the refreshed workflow.

**Known limitation.** Discovery fetches a manifest at
`https://<host>/.well-known/ard.json`. This repo's raw source URL is:

```text
https://raw.githubusercontent.com/BigCactusLabs/dead-letter/main/.well-known/ard.json
```

That is not a well-known discovery endpoint. In ARD v0.91, domain anchoring
applies to the URN publisher segment (here `github.com`) and to any
`trustManifest` identity; the spec does not require the serving host to match
that segment or to be an organization apex domain. A hosted copy
is served from `https://bigcactuslabs.xyz/.well-known/ard.json` (#159, live
since 2026-11-10) and is maintained in the site repository, not here. It
references released artifacts, the tag-pinned `SKILL.md` and the resolved
`dead-letter-server-X.Y.Z.json` release asset, not this source template, so
it needs a site change after each package release. The host rejects the
default `Python-urllib` user agent with HTTP 403 (Cloudflare error 1010);
other clients tested receive the catalog. Do not enable GitHub Pages or claim crawler discovery as
part of publishing this source file.

**Release note.** Both entry versions follow the package version and are
checked against `server.json`. They do not prove a matching skill was present
on every older package tag. Inspect the selected tag's files. Use the release
helper to keep source metadata aligned without copying current versions into
multiple guides.

## Submitting to GitHub Agent Finder

[Agent Finder](https://github.blog/changelog/2026-06-17-agent-finder-for-github-copilot-now-available/)
and its [public catalog](https://github.com/github/agentfinder-catalog) have a
separate curation process. Check the current contribution policy before
submission. Skills and MCP servers use the same catalog pull-request process,
but each needs its own entry. MCP-server entries are also merged in
automatically from GitHub's MCP registry ([github.com/mcp](https://github.com/mcp)),
whose onboarding route is contested (see
[MCP distribution](mcp-distribution.md#github-mcp-registry-and-agent-finder));
publication to the open MCP Registry does not imply a GitHub MCP registry
listing. MCP registry
publication alone is not evidence that a named client query returns
dead-letter; record the actual catalog/client result. The
[Agent Finder search page](https://github.com/agentfinder) accepts
natural-language queries for retrieval checks once an entry is listed.
The catalog's `CONTRIBUTING.md` was revised on 2026-10-13. `sourceSet` and
`repoPath` are required for GitHub-file references, which this skill entry is.
Validation now needs `python3 -m pip install -r requirements.txt` before the
generator runs.

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

This shape matches the required fields in the catalog's `CONTRIBUTING.md` at
[`666cf8a`](https://github.com/github/agentfinder-catalog/blob/666cf8a51e2329d577e73d6e3c00fa5eae55ce2c/CONTRIBUTING.md)
and passes the generator and `--check` at
[`abb4a13`](https://github.com/github/agentfinder-catalog/tree/abb4a13e3c27e46e66ee6a103cc3ca2ae370cbd8)
(September 30, 2026) and again, unchanged, at
[`3286bfd`](https://github.com/github/agentfinder-catalog/tree/3286bfd843a3831b52a8a72b0ff6f3eb0f730d7f)
(November 10, 2026; 1,843 generated entries with it added).
The catalog's generator enforces fewer fields than that guide, so a generator
pass is not proof of a complete entry. In a fork of that catalog, add
`catalog/bigcactuslabs/dead-letter.json`, check it with `python -m json.tool`,
run `python3 -m pip install -r requirements.txt`, then
`python3 scripts/generate_ai_catalog.py` and its `--check` mode; the
generator fetches GitHub's live MCP catalog. Fork workflows validate but
cannot push the regenerated `ai-catalog.json`; upstream regenerates it after
merge. The guide's skill example uses a `blob/main` URL; its 40-hex
commit-pin rule covers reviewed icon assets, not skill URLs. Do not invent
acceptance of a released commit URL for skills.
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
