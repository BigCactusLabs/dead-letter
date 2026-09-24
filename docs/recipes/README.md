# Email conversion recipes

These five recipes use the same [synthetic two-message inbox](fixtures/inbox/).
Run shell commands from the repository root after installing the relevant
[CLI or MCP route](../reference/distribution.md). The sample contains a quoted
reply and a small CSV attachment; it contains no private mail.

| Goal | Recipe |
| --- | --- |
| Put an `.eml` folder in an Obsidian vault or Markdown tree | [Folder to Markdown](folder-to-markdown.md) |
| Prepare structured text for a RAG pipeline | [RAG preprocessing](rag-preprocessing.md) |
| Ask a local Claude or Codex MCP client to convert mail | [Local agent MCP](local-agent-mcp.md) |
| Keep Markdown, source mail, and decoded files together | [Durable Cabinet](durable-cabinet.md) |
| Audit a conversion and inspect quality signals | [Report and diagnostics](report-and-diagnostics.md) |

The CLI and Python examples in these guides were exercised against the released 0.4.0
package. The MCP examples were exercised against its real stdio server; this
does not establish a named GUI client's install or model behavior. All five
preserve the input `.eml` files. See the [runtime contract](../reference/v4-runtime-contracts.md)
and [quality diagnostics](../reference/quality-diagnostics.md) for exact behavior.
