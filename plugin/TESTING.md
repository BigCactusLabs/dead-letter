# Plugin manual smoke tests

Run this checklist before each plugin release. Most can run in Cowork; some require a Claude Code session with the plugin installed.

## Prerequisites

- A fixture `.eml` file. Use one of the files under `tests/core/fixtures/` if running locally.
- Plugin installed via `/plugin marketplace add BigCactusLabs/bigcactuslabs-plugins` → `/plugin install dead-letter`.

## Cowork session checks

### 1. Convert (single uploaded file)

1. Drag a fixture `.eml` into the Cowork chat. It lands in `uploads/`.
2. Type `/dead-letter:convert <uploaded-filename>`.
3. Expect: rendered Markdown returned to the chat, including YAML front matter.

### 2. Summarize (single uploaded file)

1. With the same uploaded `.eml`, type `/dead-letter:summarize <uploaded-filename>`.
2. Expect: a response with `## Summary`, `## Action items`, `## Dates and people` sections.

### 3. Cabinet (default `bundle-root`)

1. With the same uploaded `.eml`, type `/dead-letter:cabinet <uploaded-filename>`.
2. Expect: a new directory at `outputs/<source-stem>/` containing the markdown, any attachments, and the source `.eml`.

### 4. Triage with directory grant

1. Place 3-5 fixture `.eml` files in a folder on your local machine (e.g., `~/Desktop/test-eml/`).
2. Use the cowork directory request tool to grant access to that folder.
3. Type `/dead-letter:triage ~/Desktop/test-eml`.
4. Expect: a grouped triage overview by sender.

### 5. Triage cap (over-50 refusal)

1. Create or point to a folder with more than 50 `.eml` files (or fake it with a folder containing many empty `*.eml` files for cap-testing only).
2. Grant access; type `/dead-letter:triage <that-folder>`.
3. Expect: refusal message; no batch conversion runs.

### 6. Host-path remediation

1. Without any granted directory, type `/dead-letter:convert /Users/<you>/Desktop/some.eml`.
2. Expect: a `FileNotFoundError` from the MCP server, followed by Claude suggesting "drag the file into the chat" (per the SKILL.md path-resolution rule).

## Claude Code session checks

### 7. Convert (filesystem path)

1. In a Claude Code session, install the plugin.
2. Type `/dead-letter:convert tests/core/fixtures/<some-fixture>.eml`.
3. Expect: rendered Markdown.

### 8. uv-missing error (optional)

1. On a machine without `uv`, install the plugin and try any command.
2. Expect: a clear error message about the missing `uv` binary, with a pointer to https://docs.astral.sh/uv/getting-started/installation/.

## Sign-off

- [ ] All checks above pass
- [ ] No regressions vs. the previous release
- [ ] Tag dead-letter repo `plugin-v<X.Y.Z>` and push
- [ ] Update `BigCactusLabs/bigcactuslabs-plugins/.claude-plugin/marketplace.json`
      `source.ref` and push
