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

Read-only requests work as plain prompts:

- "Convert `~/Downloads/invoice.eml` to Markdown." Claude calls the conversion tool and shows the Markdown with YAML front matter in the chat.
- "Summarize `~/Downloads/invoice.eml` and list the action items." Claude converts the message and summarizes it.

Anything that writes files needs the slash command typed explicitly. If you ask in plain language, Claude tells you which command to type and waits:

- `/dead-letter:cabinet ~/Downloads/contract.eml ~/Archive` writes a bundle directory with the Markdown, the decoded attachments, and a copy of the original `.eml`.
- `/dead-letter:triage ~/exports/vendor-thread` converts up to 50 `.eml` files and groups them by sender and subject with priority hints.
- `/dead-letter:mbox ~/Takeout/Mail/Inbox.mbox ~/mail-md` converts the first 1000 messages of a flat `.mbox` archive up to 256 MiB.

## Requirements

- **In Cowork:** none. `uv` is already in the sandbox image.
- **In Claude Code (local):** `uv` on `PATH`. Follow uv's [installation guide](https://docs.astral.sh/uv/getting-started/installation/) (Homebrew, WinGet, pipx, or the standalone installer).

## How it works

The plugin launches the `dead-letter-mcp` MCP server (Python, distributed on PyPI as `dead-letter`). The slash commands call into this server, which handles `.eml` parsing, sanitization, and Markdown rendering.

The plugin is pinned to a specific dead-letter PyPI release (see `.mcp.json`) so a future package release cannot silently break installs.

## Where it works

| App | Commands and skill | MCP tools |
| --- | --- | --- |
| Claude Code | Yes | Yes |
| Cowork (session on your computer) | Yes | Yes |
| claude.ai chat (web, desktop, mobile) | Yes | No: chat does not start local MCP servers |

Without the MCP tools, the commands can explain the workflow but cannot convert files.

## What it runs and sends

- On first launch, `uvx` downloads the pinned `dead-letter` package and its dependencies from PyPI and caches them. Later launches reuse the cache. The plugin also ships a `uv.lock` with hashed dependency versions for hosts that launch from a lock; plain `uvx` resolves compatible dependency versions itself.
- The `dead-letter-mcp` server then runs locally over stdio. It makes no network requests.
- Text a tool returns to Claude enters the conversation and is sent to the model provider with it; see [Privacy](#privacy).
- The tools read only the `.eml`, `.mbox`, or folder paths you give them. They write only to the output paths you give them, under new collision-safe names, and never modify, move, or delete your source files.
- Each tool declares MCP annotations: `get_diagnostics` is read-only, and the four conversion tools create new files without being destructive. None declares network access.

## Privacy

The dead-letter server converts email on your machine and sends nothing itself. It collects no usage data, and conversions without an output path use temporary files that are deleted after each call. Error details go to the server's stderr log, which your Claude app may keep with its other MCP logs, and uv keeps a local package cache.

This does not make the whole workflow offline. Tool results, such as the Markdown `/dead-letter:convert` returns or the summary `/dead-letter:summarize` writes, become part of the conversation, and Claude sends the conversation to Anthropic's model service as with any other chat content. Files you only write to disk, without asking Claude to read them, stay local. Choose which messages to convert accordingly, especially for sensitive mail.

## Troubleshooting

- **The commands load but no tools run (Claude Code).** Run `/mcp` and check that the `dead-letter` server is connected. If it isn't, check that `uv` is on `PATH` (see [Requirements](#requirements)), then update the plugin as described in [Update](#update) and start a new session.
- **The first command is slow.** The first launch downloads the pinned package and its dependencies from PyPI. Later launches reuse uv's cache.
- **The server fails to start with `ImportError: Modest backend is deprecated since selectolax 1.0`.** Plugin versions pinned to dead-letter 0.4.5 or earlier pick up an incompatible selectolax release. Update the plugin; 0.4.6 and later pin a fixed package.
- **`File not found: <path>` in Cowork.** A Cowork session cannot read arbitrary paths on your Mac. Drag the file into the chat, or grant the session access to its folder, then retry.
- **No tools in claude.ai chat.** Expected: chat does not start local MCP servers. Use Claude Code or Cowork (see [Where it works](#where-it-works)).
- **The output looks wrong.** Ask Claude to run `get_diagnostics` on the message. It reports which body was selected, the conversion confidence, and any warnings, and writes nothing.

## Support

- Bugs and questions: [GitHub issues](https://github.com/BigCactusLabs/dead-letter/issues). Please use synthetic or redacted mail in reports, never real messages.
- Security vulnerabilities: report privately as described in [SECURITY.md](https://github.com/BigCactusLabs/dead-letter/blob/main/SECURITY.md).

## Source

- Plugin source: https://github.com/BigCactusLabs/dead-letter/tree/main/plugin
- dead-letter package: https://github.com/BigCactusLabs/dead-letter
- Marketplace: https://github.com/BigCactusLabs/bigcactuslabs-plugins
