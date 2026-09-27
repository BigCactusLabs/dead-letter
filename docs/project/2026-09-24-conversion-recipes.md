# Issue #162: five conversion recipes

On September 24, 2026, issue #162 added five task-specific guides under
[docs/recipes/](../recipes/README.md). They use a public synthetic two-message
inbox with a quoted reply and a base64-encoded CSV attachment. This record is
for the documentation slice; it does not change a product runtime or publish a
new release.

| Workflow | Guide |
| --- | --- |
| EML folder to Obsidian/Markdown | [Folder to Markdown](../recipes/folder-to-markdown.md) |
| RAG preprocessing | [RAG preprocessing](../recipes/rag-preprocessing.md) |
| Local Claude/Codex MCP conversion | [Local agent MCP](../recipes/local-agent-mcp.md) |
| Durable Cabinet archive | [Durable Cabinet](../recipes/durable-cabinet.md) |
| Report and quality auditing | [Report and diagnostics](../recipes/report-and-diagnostics.md) |

## Release verification

`python scripts/verify_conversion_recipes.py --python <isolated-release-python>`
used a fresh isolated PyPI installation of `dead-letter==0.4.0` on Python
3.12. Its import resolved under the isolated environment's `site-packages`,
outside the source checkout. The script exercised the installed CLI for folder,
structured-thread, and single-file report output; the installed Python API for
copy-only Cabinet bundling; and the installed MCP stdio executable for
`initialize`, `tools/list`, and all four real tool calls. Exact output paths,
report counts, thread marker, CSV decoded bytes, source copy bytes, diagnostics
attachment counts, and input hashes passed. The two source SHA-256 values were:

```text
clients/order-update.eml: b472dfb168cd5efc9c993878b86ea1b7dc4f29659413283375328c10583e4b0c
ops/loading-window.eml: 40dce67047d4191b23a09aeea98e6459de2427c5fdb7c3644df56d21d0b98322
```

The stdio run proves the released tool transport and output. It is not a
Claude/Codex GUI install, agent selection, or model acceptance test. The CLI
report in this release records summary and per-file success, but no detailed
diagnostics object; use MCP `get_diagnostics` or the file-job API for those
fields. Flat Markdown names the CSV without extracting its bytes or text;
bundles retain the decoded bytes. Neither route creates embeddings or an index.

The canonical [distribution map](../reference/distribution.md),
[runtime contract](../reference/v4-runtime-contracts.md), and
[quality guide](../reference/quality-diagnostics.md) remain the source of truth
for installation and behavior.

The Codex registration syntax was checked against local `codex mcp add --help`
and [official MCP guidance](https://learn.chatgpt.com/docs/extend/mcp) on the
same date. The setup command was not run against a user client configuration.
