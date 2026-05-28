---
name: dead-letter-context
description: Primes Claude with the dead-letter plugin's commands and runtime conventions when the user is working with .eml email files, Gmail/Outlook exports, or email archive workflows.
---

# dead-letter context

This workspace ships the **dead-letter** plugin: convert `.eml` email files to Markdown with YAML front matter, triage small folders, and build self-contained archive bundles. Use the slash commands below before reaching for any other email-parsing approach.

## Commands (user-typed only)

All four slash commands are **user-typed only** — they have `disable-model-invocation: true` so the model cannot invoke them. They are shortcuts the user reaches for; they shape arguments and output. For natural-language requests (no slash command), see the **MCP tool mapping** section below.

- `/dead-letter:convert <path>` — single `.eml` → Markdown in chat
- `/dead-letter:summarize <path>` — single `.eml` → structured summary, action items, dates/people
- `/dead-letter:triage <folder>` — small folder (≤50 `.eml` files) → grouped overview with priority hints
- `/dead-letter:cabinet <path> [bundle-root]` — single `.eml` → archive bundle (markdown + attachments + source)

## MCP tool mapping (for natural-language requests)

When the user describes intent in natural language without typing a slash command, call MCP tools directly **only for read-only operations**. For side-effecting work, redirect to the slash command so the user makes the call explicitly.

| User says (paraphrased) | What to do |
|---|---|
| "convert this email" / "show me this .eml as markdown" | Call `convert_eml` with `eml_path=<path>` and `preset=default`. Return the markdown. |
| "summarize this email" / "tl;dr this email" | Call `convert_eml` with `preset=clean`, then produce a 2-3 sentence summary plus action items and dates/people. |
| "what's in this email" / "extract dates from this email" | Call `convert_eml` with the appropriate preset, then read the result and answer the user's specific ask. |
| "archive this email" / "save this email and its attachments" / "build a bundle" | **Side-effecting.** Do NOT invoke `convert_eml_to_bundle` directly. Tell the user: `Type /dead-letter:cabinet <path> [bundle-root] so the bundle is created with explicit intent.` Then wait. |
| "triage this folder" / "go through these emails" / "summarize this folder of emails" | **Batch + side-effecting (writes converted files).** Do NOT invoke `convert_directory` directly. Tell the user: `Type /dead-letter:triage <folder> — that command enforces the 50-file safety cap.` Then wait. |

The two side-effecting redirects exist because:
1. `convert_eml_to_bundle` writes files to disk; the user should commit to the destination by typing the command.
2. `convert_directory` now has a server-side cap and requires `output_directory`, but batch conversion still writes files; the slash command keeps the user's explicit intent and destination choice in the loop.

## Presets

The underlying MCP tools accept a `preset` flag that bundles common conversion options:

- `default` — strips signatures, tracking pixels, and signature images. Use for general-purpose conversion.
- `clean` — `default` plus strips disclaimers and quoted headers. Use when producing human summaries.
- `verbose` — includes all headers and raw HTML. Use for forensic / troubleshooting work only.
- `raw` — no stripping. Use only when the user asks for the email exactly as received.

Pass non-default presets when the user's intent matches: e.g., for `/dead-letter:summarize`, use `clean`.

## Runtime detection

The plugin works in two runtimes that share the plugin format:

- **Claude Cowork** — sandboxed; detect by the presence of `uploads/` and `outputs/` directories in the working directory.
- **Claude Code (local)** — full filesystem access; no `uploads/`/`outputs/` mounts.

## Path-resolution rule

Always pass the user's path to the MCP server **unchanged**. The MCP server only checks existence and raises `FileNotFoundError` on missing paths — no rewriting needed.

If you get `FileNotFoundError` and you're in Cowork:

- **The user gave a single host-OS path** (e.g., `/Users/...`, `~/Documents/...`, `C:\Users\...`): suggest "drag the file into the chat" so it lands in `uploads/`.
- **The user gave a folder path**: suggest "grant directory access to that folder" via the cowork directory request tool, then re-run.

If you're in Claude Code: surface the `FileNotFoundError` verbatim with the path. The user will fix it themselves.

## Cabinet write rule

`/dead-letter:cabinet`'s second argument is `bundle-root`, not a custom bundle name. The bundle directory is always named after the source `.eml`'s filename stem (enforced server-side). Defaults:

- **Cowork:** `bundle-root` defaults to `outputs`. Result: `outputs/<source-stem>/`.
- **Claude Code:** `bundle-root` defaults to the source `.eml`'s parent directory. Result: `<source-dir>/<source-stem>/`.

Do not propose custom bundle directory names; users who want one can `mv` the result.

## Triage cap

`/dead-letter:triage` has a soft cap of 50 `.eml` files (recursive count). Before invoking the underlying MCP tool, count `.eml` files in the folder using a recursive case-insensitive search:

- Bash: `find <folder> -type f -iname '*.eml' | wc -l`
- Glob: `**/*.eml` with case-insensitive matching

If the count is over 50, refuse the batch and tell the user to either narrow the folder or run `/dead-letter:convert` on specific files. Folders over 50 are unsupported in v1.

## Out of scope

- `.mbox` and other email-archive container formats. dead-letter currently handles `.eml` only. If a user mentions an `.mbox` file, clarify the limitation rather than attempting conversion.
- Bulk archive processing beyond 50 files. Deferred to a future v2 sub-agent.
