# dead-letter Claude plugin

Convert `.eml` email files to Markdown with YAML front matter, triage small folders, build self-contained archive bundles, and convert small flat `.mbox` archives — from inside Claude Cowork or Claude Code.

## Install

```
/plugin marketplace add BigCactusLabs/bigcactuslabs-plugins
/plugin install dead-letter
```

## Update

In Claude Code, refresh the marketplace and update the installed plugin:

```
/plugin marketplace update bigcactuslabs
/plugin update dead-letter@bigcactuslabs
```

In Cowork, open **Customize → Plugins → Personal**, open the
`bigcactuslabs-plugins` marketplace options, and select **Check for updates**.
Then open **Dead letter** and select **Update**. Cowork and Claude Code keep
separate installed copies, but both resolve the same versioned marketplace
release.

## Commands

- `/dead-letter:convert <path>` — single `.eml` to Markdown
- `/dead-letter:summarize <path>` — short structured summary of an email
- `/dead-letter:triage <folder>` — overview of a small folder of emails (≤50)
- `/dead-letter:cabinet <path> [bundle-root]` — self-contained archive bundle
- `/dead-letter:mbox <path> [output-dir]` — one flat `.mbox` (≤256 MiB, first 1000 messages) to Markdown files

Email content is treated as untrusted data, not instructions. The plugin should summarize, convert, or archive instructions found inside an email; it should not follow tool-use, credential, or exfiltration requests embedded in the message.

## Example prompts

- "Convert `~/Downloads/invoice.eml` to Markdown." Runs `/dead-letter:convert` and returns the Markdown with YAML front matter in the chat.
- "Summarize the emails in `~/exports/vendor-thread/` and tell me which need a reply." Runs `/dead-letter:triage` on up to 50 `.eml` files and groups them by sender and subject.
- "Archive `~/Downloads/contract.eml` with its attachments into `~/Archive/`." Runs `/dead-letter:cabinet` and writes a bundle directory with the Markdown, the decoded attachments, and a copy of the original `.eml`.
- "Convert my Gmail Takeout file `~/Takeout/Mail/Inbox.mbox` to Markdown files in `~/mail-md/`." Runs `/dead-letter:mbox` on the first 1000 messages of an archive up to 256 MiB.

## Requirements

- **In Cowork:** none. `uv` is already in the sandbox image.
- **In Claude Code (local):** `uv` on `PATH`. Install with `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or the PowerShell equivalent on Windows. See [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/).

## How it works

The plugin launches the `dead-letter-mcp` MCP server (Python, distributed on PyPI as `dead-letter[mcp]`). The slash commands call into this server, which handles `.eml` parsing, sanitization, and Markdown rendering.

The plugin is pinned to a specific dead-letter PyPI release (see `.mcp.json`) so a future package release cannot silently break installs.

## Where it works

| App | Commands and skill | MCP tools |
| --- | --- | --- |
| Claude Code | Yes | Yes |
| Cowork (session on your computer) | Yes | Yes |
| claude.ai chat (web, desktop, mobile) | Yes | No: chat does not start local MCP servers |

Without the MCP tools, the commands can explain the workflow but cannot convert files.

## What it runs and sends

- On first launch, `uvx` downloads the pinned `dead-letter[mcp]` package and its dependencies from PyPI and caches them. Later launches reuse the cache.
- The `dead-letter-mcp` server then runs locally over stdio. It makes no network requests.
- The tools read only the `.eml`, `.mbox`, or folder paths you give them. They write only to the output paths you give them, under new collision-safe names, and never modify, move, or delete your source files.
- Each tool declares MCP annotations: `get_diagnostics` is read-only, and the four conversion tools create new files without being destructive. None declares network access.

## Privacy

The plugin does not collect, store, or transmit email content or usage data. Conversion happens on your machine, and converted output exists only where you choose to write it, plus whatever Claude reads back into the conversation. The only network traffic is the PyPI package download described above.

## Source

- Plugin source: https://github.com/BigCactusLabs/dead-letter/tree/main/plugin
- dead-letter package: https://github.com/BigCactusLabs/dead-letter
- Marketplace: https://github.com/BigCactusLabs/bigcactuslabs-plugins
