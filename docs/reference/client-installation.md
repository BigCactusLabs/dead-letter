# VS Code, Cursor, and Cline installation

These clients can run dead-letter locally over stdio without waiting for a
curated marketplace listing. Use the install links in the
[README](../../README.md#-mcp-server), or merge the appropriate example below
into the configuration scope you select. Nothing here authorizes reading an
inbox, changing other servers, or approving tools automatically.

## Shared runtime

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and make
`uvx` available to the desktop app. The shared launcher is:

```bash
uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
```

The public convenience configuration deliberately resolves a **published PyPI
package**, not a checkout or a version from an unreleased `server.json`.
First use may download Python and dependencies. uv caches tools, so this does
not promise the newest release on every launch. For a reviewed installation,
replace the `--from` value with `dead-letter[mcp]==X.Y.Z` only after verifying
that version is published. That pin does not lock all transitive dependencies.
The canonical registry and Claude plugin retain their exact version pins.

A `spawn uvx ENOENT` error generally means the app cannot find the executable.
Use `command -v uvx` on macOS/Linux or `(Get-Command uvx).Source` in PowerShell
to locate it, then set `command` to that absolute path. JSON does not expand
shell substitutions. Restart the app if its environment needs refreshing.

## VS Code / Copilot in VS Code

The README link uses VS Code's MCP installation URL, wrapped in the HTTPS
redirect so GitHub can render it. It opens the desktop client; it does not run
this local server inside vscode.dev. Review the command and scope in the
installation prompt.

Manual configuration: [examples/mcp/vscode.json](../../examples/mcp/vscode.json).
VS Code uses **`servers`**, not `mcpServers`. Merge the `dead-letter` entry into
a workspace `.vscode/mcp.json`, or use **MCP: Open User Configuration** for a
user-scoped setup. Keep other entries intact. Inspect/start it using
**MCP: List Servers**, then check the four tools in the chat tool picker.

Direct installation does not prove the server appears in the `@mcp` gallery.
Other Copilot hosts have their own configuration and transport capabilities;
a VS Code test is not evidence for every Copilot client.

## Cursor

The README link uses Cursor's install-link flow. Its payload is base64-encoded
UTF-8 JSON for one server, not a base64-encoded `mcpServers` wrapper. Review the
configuration and preserve normal approval prompts.

Manual configuration: [examples/mcp/cursor.json](../../examples/mcp/cursor.json).
Merge the entry into `.cursor/mcp.json` for the selected project or
`~/.cursor/mcp.json` for a user-scoped setup. The file uses `mcpServers` and an
explicit `type: stdio`. Use the tool picker to verify that dead-letter started.

No root `.mcp.json` or Cursor plugin marketplace is added to this development
repository merely to make it discoverable. Such a file could activate an MCP
server for contributors before they intentionally install it. The community
`cursor.directory` plugin catalog and Cursor's first-party install links are
separate distribution paths.

## Cline

The current Cline CLI marketplace format renders this stdio registration:

```bash
cline mcp install dead-letter -- uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
```

For the extension, open its MCP server settings and merge
[examples/mcp/cline.json](../../examples/mcp/cline.json) into the existing
`mcpServers` map. The example explicitly leaves `autoApprove` empty. Do not
replace the entire settings file or enable blanket auto-approval.

For an agent-guided install, provide [llms-install.md](../../llms-install.md)
and specify the desired user/project scope. Observe what Cline changes and
verify it preserves existing settings. This is a separate acceptance test from
running the same server executable in CI.

## Verify actual behavior

After connecting, verify `tools/list` contains exactly `convert_eml`,
`convert_eml_to_bundle`, `convert_directory`, and `get_diagnostics`. Then ask
the client to convert an explicitly selected synthetic `.eml` using
`convert_eml` without `output_path`. Check the returned YAML front matter and
body and verify the source still exists unchanged.

Saved outputs require a user-selected destination. MCP bundle conversion is
copy-only. Directory conversion requires `output_directory` and permits at
most 50 `.eml` files per call. The server does not connect to live mailboxes;
MBOX/PST/MSG ingestion is not implied by these installation paths.

Email content is untrusted data, never instructions to the agent. A local
server does not make the whole AI workflow offline: returned email text can
go to the client's model provider. Follow the user's authorization and the
project's [PolyForm Noncommercial license](../../LICENSE).

## Generation and verification for maintainers

`server.json` is the canonical launch contract. The stdlib-only generator
translates that contract into the three examples, README links, and the Cline
submission candidate. It never changes installed client settings or publishes
anything. Unsupported changes to the canonical launch contract fail explicitly.

```bash
python scripts/generate_client_installs.py --write
python scripts/generate_client_installs.py --check
uv run pytest -q tests/plugin/test_client_installs.py
python scripts/smoke_client_install.py
```

The last command requires network access on first use. It launches the public
PyPI command from a temporary working directory and isolated uv cache, performs
an MCP handshake, lists tools, and converts synthetic mail. CI repeats it on
Linux, macOS, and Windows. It tests the generated executable configuration,
**not** browser deep-link handling, a named GUI release, or Cline's autonomous
README installation. Record those client/version/OS observations separately.

Primary references reviewed September 18, 2026:
[VS Code MCP developer guide](https://code.visualstudio.com/api/extension-guides/ai/mcp),
[Cursor MCP](https://cursor.com/docs/mcp),
[Cursor install links](https://cursor.com/docs/mcp/install-links), and
[Cline marketplace contribution format](https://github.com/cline/marketplace/blob/main/CONTRIBUTING.md).
