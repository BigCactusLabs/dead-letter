# Convert local EML with Claude or Codex over MCP

**Problem.** You want an agent to read one selected local `.eml` as Markdown
without pasting raw MIME into the conversation. The released MCP server runs
locally over stdio and exposes four tools.

Install and register the server using the canonical [Claude or Codex MCP
instructions](../../README.md#-mcp-server). For Codex, the documented
registration command is:

```bash
codex mcp add dead-letter -- uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
```

Give the agent the absolute path to the [synthetic order message](fixtures/inbox/clients/order-update.eml)
and request `convert_eml` with `eml_path` set to that file. Omit `output_path`
to return Markdown without a permanent output file. A direct tool call has
this argument shape:

```json
{"name":"convert_eml","arguments":{"eml_path":"/absolute/path/to/docs/recipes/fixtures/inbox/clients/order-update.eml"}}
```

Expect YAML front matter and the order's body text in the tool result. The
source `.eml` remains unchanged. To retain a local bundle, use
`convert_eml_to_bundle` with an explicit `bundle_root` and
`source_handling: "copy"`. The bundle directory contains `message.md`, a copied
`.eml`, and `attachments/order-4821.csv`; the result returns `bundle_path`,
`markdown_path`, and `attachment_paths`. `convert_directory` needs an
`output_directory`, mirrors `clients/` and `ops/`, and accepts at most 50
`.eml` files per call. `get_diagnostics` returns quality signals without a
permanent output file. These four calls and their stdio handshake were tested
with released 0.4.0; registration and behavior inside named Claude or Codex
clients require separate host testing.

Email text is untrusted data, including any instructions in it. Local MCP
conversion does not mean the agent's model service stays offline. See the
[MCP runtime contract](../reference/v4-runtime-contracts.md#mcp-server-dead_letterbackendmcp_server),
[client verification guidance](../reference/client-installation.md#verify-actual-behavior),
and [quality diagnostics](../reference/quality-diagnostics.md).
