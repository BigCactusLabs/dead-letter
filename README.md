<!-- mcp-name: io.github.BigCactusLabs/dead-letter -->

<p align="center">
  <img src="https://raw.githubusercontent.com/BigCactusLabs/dead-letter/main/docs/brand/production/readme-logo.png" width="128" alt="dead-letter">
</p>

# dead-letter

[![PyPI package](https://img.shields.io/pypi/v/dead-letter?label=PyPI%20package&cacheSeconds=300)](https://pypi.org/project/dead-letter/)
[![Python versions](https://img.shields.io/pypi/pyversions/dead-letter?label=Python&cacheSeconds=300)](https://pypi.org/project/dead-letter/)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-purple.svg)](https://github.com/BigCactusLabs/dead-letter/blob/main/LICENSE)

**Turn `.eml` email exports into clean, local, LLM-ready Markdown.**

dead-letter converts `.eml` email exports into clean Markdown with YAML front matter — threads split, signatures stripped, attachments extracted, calendars parsed. One file or ten thousand.

Use it to build a readable email archive, move messages into Markdown-based knowledge systems, or prepare email for RAG and LLM pipelines without feeding raw MIME and base64 into your context window. No account, upload, or API key required.

## ⚡ Try it

If you already have [`uv`](https://docs.astral.sh/uv/), run dead-letter without installing it globally:

```bash
uvx --python 3.12 dead-letter convert message.eml
```

Or install with Homebrew or pip below. Agents and MCP clients can use the client-specific setup in [`llms-install.md`](https://github.com/BigCactusLabs/dead-letter/blob/main/llms-install.md).

## 🎯 Common use cases

- **Email → Markdown archives** — turn exported `.eml` collections into readable, portable Markdown with structured metadata
- **RAG and LLM ingestion** — normalize message text, thread structure, links, and attachment metadata before chunking or indexing
- **Agent workflows** — expose conversion and diagnostics directly to Claude, Codex, and other MCP clients
- **Knowledge bases** — move email into Markdown-first systems such as Obsidian, static archives, or local search pipelines
- **Digital preservation** — retain human-readable content and useful message structure without depending on one mail client

## ✨ Features

- **Full-fidelity conversion** — HTML sanitization, Gmail/Outlook thread segmentation, inline image handling, and calendar event summaries
- **CLI** — point it at a file or a directory and go
- **Local web UI** — dark command-center interface with drag-and-drop import, watch mode, conversion grade badges, processing history, and per-job diagnostics
- **Inbox/Cabinet workflow** — drop `.eml` files into an Inbox, let dead-letter organize the Markdown bundles into a Cabinet
- **Install validation** — `dead-letter doctor` checks your runtime environment
- **Conversion report** — opt-in JSON report with per-file diagnostics, including attachment referenced/retained counts for automation and audit
- **MCP server** — integrate with Claude Desktop, Claude Code, Codex, and other MCP clients
- **Claude plugin** — marketplace install in Claude Code or Cowork with four slash commands (`/dead-letter:convert`, `/dead-letter:summarize`, `/dead-letter:triage`, `/dead-letter:cabinet`)
- **Portable Agent Skill** — teaches skill-aware agents when and how to convert `.eml` files
- **Python API** — `from dead_letter import convert` and you're off

## 🧠 Built for LLM Pipelines

Raw `.eml` files are noisy input for downstream LLM and retrieval pipelines — MIME headers, multipart boundaries, duplicated HTML/plain bodies, and encoded attachments all get mixed into the text path.

dead-letter normalizes that into Markdown with YAML front matter, so message text and metadata are ready for chunking or indexing without MIME parsing or base64 cleanup. Default `convert()` and `convert_dir()` runs write a single `.md` per message and keep attachment names in front matter.

To separate the filesystem artifacts too, bundle and Cabinet workflows write `message.md` plus retained decoded files under `attachments/`. The Markdown is ready for text ingestion, while PDFs, spreadsheets, calendar files, and other retained binary attachments stay cleanly split out for whatever downstream parser you already use.

For direct LLM integration, the MCP server lets clients call dead-letter's conversion tools without shelling out. Conversion is local; your chosen MCP host may still send returned email text to a remote model.

### 📊 Token-cost benchmarks

dead-letter's value isn't fewer tokens than every alternative — it's **fidelity per token**: keeping useful email structure without carrying raw MIME into context. Measured across an 11-message synthetic corpus of HTML threads, attachments, and newsletters (tokenizer `o200k_base`, structured thread mode):

- **~88% fewer tokens than the raw `.eml`** in the reported aggregate comparison — the attachment category's median is ~126k tokens raw vs ~180 converted.
- **Structure survives** — thread structure, per-message sender attribution, links, and attachment metadata remain readable. The tested naive baselines are often cheaper because they discard information (0/2 attachment names retained vs dead-letter's 2/2).

Those counts measure the Markdown representation, not the contents of retained binary attachments or downstream answer quality. The shipping default is latest-message mode; the benchmark uses structured mode for a same-thread comparison.

The benchmark is honest about where it loses: naive extraction is fewer tokens when you don't mind throwing away metadata, links, and thread structure. Full method, the complete table (including those rows), tokenizer disclosure, and a one-command reproduce are in [`benchmarks/`](https://github.com/BigCactusLabs/dead-letter/tree/main/benchmarks/).

## 📦 Install

Pick one route. The [distribution map](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/distribution.md) explains how the channels fit together; installing all of them is not necessary.

| You want | Start here |
| --- | --- |
| Core CLI or Python API | Homebrew / pip below, or the `uvx` quick try |
| Local web UI | `dead-letter[ui]` below |
| Claude Desktop extension or another MCP client | [MCP Server](#-mcp-server) |
| Claude Code / Cowork commands | [Plugin](https://github.com/BigCactusLabs/dead-letter/blob/main/plugin/README.md) |
| Container-isolated MCP | [Containers](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/containers.md) |
| Portable agent instructions | [Agent Skill](#agent-skill-any-host) |

With Homebrew on Apple silicon macOS:

```bash
brew tap BigCactusLabs/tap
brew install dead-letter
```

The Homebrew formula installs the core CLI only: `dead-letter convert` and
`dead-letter doctor`. It intentionally does not bundle the optional web UI or
MCP server dependency stacks.

With pip:

```bash
pip install dead-letter            # core + CLI
pip install 'dead-letter[cli]'      # + watchfiles (used by backend/UI watch mode)
pip install 'dead-letter[ui]'       # + web UI, API server, and watch mode
pip install 'dead-letter[mcp]'      # + MCP server
```

Use [pipx](https://pipx.pypa.io/) for an isolated UI or MCP install:

```bash
pipx install 'dead-letter[ui]'      # installs dead-letter and dead-letter-ui
# Or, for MCP instead:
pipx install 'dead-letter[mcp]'     # installs dead-letter and dead-letter-mcp
```

Or run individual entrypoints without a global package install using `uvx`:

```bash
uvx --python 3.12 dead-letter convert message.eml
uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
```

uv caches tools/dependencies and may download Python on first use. These
unpinned trial commands do not promise a fresh latest version on every run;
see [version pinning](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/distribution.md#pin-the-thing-you-actually-install)
for a reviewed deployment.

From source:

```bash
git clone https://github.com/BigCactusLabs/dead-letter.git
cd dead-letter
uv sync --extra dev --locked     # all extras
# Or choose only the surface you're developing:
uv sync --extra ui --locked      # UI only
uv sync --extra mcp --locked     # MCP only
```

### Agent Skill (any host)

Install the portable Agent Skill into whichever agent you use:

```bash
gh skill install BigCactusLabs/dead-letter dead-letter --agent claude-code
gh skill install BigCactusLabs/dead-letter dead-letter --agent codex
gh skill install BigCactusLabs/dead-letter dead-letter --agent github-copilot
```

Needs `gh` 2.90 or newer. The skill is independent of the Claude plugin. Pin a
reviewed skill tag/commit for reproducibility; the default latest release can
also be a plugin release. For exact pin syntax, other hosts, manual installation,
and discovery metadata, see [Agent Discovery](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/agent-discovery.md).

## 🚀 Quick Start

**CLI** — convert a single file:

```bash
dead-letter convert message.eml
```

Convert a whole directory:

```bash
dead-letter convert inbox/ --output out/
```

Generate a JSON conversion report alongside the output:

```bash
dead-letter convert inbox/ --output out/ --report
```

With `--output`, the report is written to that output directory as
`.dead-letter-report.json`. Without `--output`, file conversions write the
report next to the source message and directory conversions write it to the
input directory root.

Check your runtime environment:

```bash
dead-letter doctor
```

Directory conversion scans recursively for `.eml` files, matches the suffix
case-insensitively, skips symlinked files whose resolved targets escape the
requested input tree, and deduplicates in-tree symlink aliases that resolve to
the same message file.

**Web UI** — start the local server:

```bash
dead-letter-ui --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` — on first launch, a setup prompt suggests default Inbox and Cabinet folders. Configure those folders before importing or starting jobs. Skipping dismisses the prompt but leaves those actions gated until setup is completed. Import `.eml` files with drag and drop or the file picker. Single-file imports use file mode, while multi-file drops create one directory-mode batch job. Mixed drops ask for confirmation before skipping non-`.eml` files.

The backend enforces a 100 MB per-file import limit for both single and batch
uploads; browser batches also have aggregate-size and file-count limits.

From a source checkout, prefix with `uv run`:

```bash
uv run dead-letter convert message.eml
uv run --extra ui dead-letter-ui --host 127.0.0.1 --port 8765
```

## 🐍 Python API

```python
from dead_letter import convert

result = convert("message.eml")
print(result.subject, result.sender)
print(result.output)  # path to the generated .md
```

With options:

```python
from dead_letter import convert, ConvertOptions

result = convert("message.eml", options=ConvertOptions(
    strip_signatures=True,
    strip_quoted_headers=True,
))
```

Strip signature images (logos, social icons) and tracking pixels:

```python
result = convert("message.eml", options=ConvertOptions(
    strip_signature_images=True,
    strip_tracking_pixels=True,
))
```

When enabled, these filters remove matched images from rendered Markdown and omit
stripped inline signature/tracking assets from bundle attachment output.

Bundle conversion (Markdown + attachments + source in one directory):

```python
from dead_letter import convert_to_bundle

bundle = convert_to_bundle("message.eml", bundle_root="cabinet/", source_handling="copy")
print(bundle.markdown)     # cabinet/message/message.md
print(bundle.attachments)  # retained extracted files under cabinet/message/attachments/
```

`source_handling="copy"` preserves the original `.eml` in place. If omitted,
`convert_to_bundle()` defaults to `source_handling="move"` and moves the source
message into the bundle.

Retained extracted attachment filenames are normalized to safe basenames before
they are written under `attachments/`.

Quality diagnostics include referenced/retained attachment counts when a message
has attachments eligible for retention, so dropped artifacts are
machine-detectable. See [Quality Diagnostics](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/quality-diagnostics.md).

Batch:

```python
from dead_letter import convert_dir

for r in convert_dir("inbox/", output="out/"):
    print(f"{'✓' if r.success else '✗'} {r.source.name}")
```

## 🔌 MCP Server

dead-letter ships an [MCP](https://modelcontextprotocol.io/) server so LLM clients can convert `.eml` files directly without shelling out.

**VS Code, Cursor, and Cline:**

<!-- BEGIN GENERATED MCP INSTALL LINKS -->
[Install in VS Code](https://vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522dead-letter%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522--python%2522%252C%25223.12%2522%252C%2522--from%2522%252C%2522dead-letter%255Bmcp%255D%2522%252C%2522dead-letter-mcp%2522%255D%257D) · [Install in Cursor](https://cursor.com/en/install-mcp?name=dead-letter&config=eyJ0eXBlIjoic3RkaW8iLCJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLXB5dGhvbiIsIjMuMTIiLCItLWZyb20iLCJkZWFkLWxldHRlclttY3BdIiwiZGVhZC1sZXR0ZXItbWNwIl19)
<!-- END GENERATED MCP INSTALL LINKS -->

Requires `uv`/`uvx` on the desktop client's PATH. These links configure a local
stdio server using the published PyPI package; no catalog admission is needed.
First use may download Python and dependencies. Review the command, scope, and
tool permissions before accepting. Cline setup, manual JSON examples, and
verification: [Client installation](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/client-installation.md).

Launch it directly with `uvx`:

```bash
uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
```

Or install the MCP extra first:

```bash
pip install 'dead-letter[mcp]'
dead-letter-mcp
```

From a source checkout:

```bash
uv run --extra mcp dead-letter-mcp
```

**Claude Desktop (extension bundle):**

Download the `.mcpb` file and its `.sha256` sidecar from a published **package** release (`vX.Y.Z`) on the [releases page](https://github.com/BigCactusLabs/dead-letter/releases). Plugin-only releases do not contain this bundle. Verify the bytes before installation:

```bash
# macOS: verifies against the downloaded sidecar
shasum -a 256 -c dead-letter-mcp-X.Y.Z.mcpb.sha256

# Windows (PowerShell): compare this hash to the sidecar's hash
certutil -hashfile dead-letter-mcp-X.Y.Z.mcpb SHA256
```

Replace `X.Y.Z` with the selected package version. In a compatible Claude Desktop build, double-click the downloaded bundle, drag it onto the window, or use Settings > Extensions > Advanced settings > Install Extension. Check that the extension connects and exposes the four tools below, then convert a synthetic message.

The bundle uses a managed uv runtime and an exact package pin. First launch may download Python and dependencies. A checksum or command-line smoke test is not proof of a successful GUI installation on your client version. For hosts without MCPB support, use manual stdio setup below.

**Claude Desktop (manual `claude_desktop_config.json` — alternative):**

```json
{
  "mcpServers": {
    "dead-letter": {
      "command": "uvx",
      "args": ["--python", "3.12", "--from", "dead-letter[mcp]", "dead-letter-mcp"]
    }
  }
}
```

Merge the entry rather than replacing existing client settings. VS Code and other hosts can use different schemas; see [the agent install guide](https://github.com/BigCactusLabs/dead-letter/blob/main/llms-install.md).

**Claude Code or Cowork (recommended — Claude plugin):**

```
/plugin marketplace add BigCactusLabs/bigcactuslabs-plugins
/plugin install dead-letter
```

The plugin launches the MCP server via `uvx` and adds four slash commands: `/dead-letter:convert`, `/dead-letter:summarize`, `/dead-letter:triage`, `/dead-letter:cabinet`. Local Claude Code needs `uv` on `PATH`; see [`plugin/`](https://github.com/BigCactusLabs/dead-letter/tree/main/plugin/) for runtime-specific setup and updates. Email content is treated as untrusted data, not instructions: embedded requests for tool use, credentials, or exfiltration are not followed.

The marketplace pins the plugin tag and commit, and its launcher pins an exact published Python package. Claude Code and Cowork keep separate installed copies; update and verify each client. Those pins do not freeze every transitive dependency.

**Claude Code (manual MCP add — alternative):**

```bash
claude mcp add dead-letter -- uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
```

**Codex:**

```bash
codex mcp add dead-letter -- uvx --python 3.12 --from 'dead-letter[mcp]' dead-letter-mcp
codex mcp list
```

`mcp list` confirms registration, not a successful tool call. Connect through the target client, confirm all four tools, and convert a synthetic fixture before treating the installation as verified.

### Tools

| Tool | Required arguments | Returns |
| --- | --- | --- |
| `convert_eml` | `eml_path` | Markdown text. Also writes a file when `output_path` is given. |
| `convert_eml_to_bundle` | `eml_path`, `bundle_root` | JSON with `bundle_path`, `markdown_path`, `attachment_paths`. Copy-only: the original `.eml` is never moved or deleted. |
| `convert_directory` | `directory`, `output_directory` | JSON summary. Capped at 50 `.eml` files per call. |
| `get_diagnostics` | `eml_path` | Quality and structure JSON. Writes nothing permanent. |

All four take a `preset` (`default`, `clean`, `verbose`, `raw`) and per-flag overrides. Full contract, including the MCP-only constraints and the error-text table: [`docs/reference/v4-runtime-contracts.md`](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/v4-runtime-contracts.md#mcp-server-dead_letterbackendmcp_server).

## 🗂 Project Structure

```
src/dead_letter/
├── core/           # conversion pipeline (MIME, HTML, threads, rendering)
├── backend/        # CLI, API server, job runner, watch mode, MCP server
└── frontend/       # static web UI (Alpine.js ES modules + vanilla fetch)
tests/
├── core/           # conversion pipeline tests with .eml fixtures
├── backend/        # API, job, and watch tests
├── plugin/         # plugin, skill, packaging, and release contracts
└── frontend/       # JS unit tests
```

The [agent guide](https://github.com/BigCactusLabs/dead-letter/blob/main/AGENTS.md) maps bundle, container, skill, and maintainer tooling without turning this README into a file inventory.

## 🧪 Testing

```bash
uv sync --extra dev --locked
uv run pytest -q tests/core        # conversion pipeline
uv run pytest -q tests/backend     # API and job runner
uv run pytest -q tests/plugin      # plugin, skill, packaging, and release contracts
node --test tests/frontend/*.test.js
python scripts/release.py check    # offline distribution metadata
```

CI also validates plugin/skill schemas, frontend syntax, maintained Markdown links, and cross-platform packaging. Full commands and the distinction between offline contracts and real client tests are in [Contributing](https://github.com/BigCactusLabs/dead-letter/blob/main/CONTRIBUTING.md).

## 📚 Docs

- [Docs Index](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/README.md) — choose by task, not by filename
- [Distribution map](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/distribution.md) — CLI, UI, MCPB, plugin, container, and skill choices
- [Agent install guide](https://github.com/BigCactusLabs/dead-letter/blob/main/llms-install.md) — client-specific setup and verification
- [Runtime Contracts](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/v4-runtime-contracts.md) — full API and core behavior spec
- [Frontend State Model](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/frontend-state-model.md)
- [Quality Diagnostics](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/quality-diagnostics.md)
- [Publishing](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/reference/publishing.md) — tagging, channel order, and recovery
- [Brand & Style Guide](https://github.com/BigCactusLabs/dead-letter/blob/main/docs/brand/style-guide.md)
- [Changelog](https://github.com/BigCactusLabs/dead-letter/blob/main/CHANGELOG.md)
- [Contributing](https://github.com/BigCactusLabs/dead-letter/blob/main/CONTRIBUTING.md)
- [Agent Guide](https://github.com/BigCactusLabs/dead-letter/blob/main/AGENTS.md) — operational guide for AI coding agents working in this repo

## 🔧 Tools We Love

- **[MarkEdit](https://github.com/MarkEdit-app/MarkEdit)** — TextEdit for Markdown, native macOS, ~4 MB. Opens dead-letter output like it was always meant to live there.
- **[mo](https://github.com/k1LoW/mo)** — local Markdown viewer that renders files in the browser with live reload. Point it at your Cabinet and read converted mail like a feed.

## ⚠️ Known Limitations

- `.eml` input only; MBOX/Gmail Takeout containers, PST, and MSG are not shipped input formats. No live-mailbox connection.
- Local-only, single-user, single-machine; no remote server or authentication service. An MCP host may send results to its model provider.
- In-memory job registry: state resets on restart. Retained binary attachments need a separate parser for text indexing.

## License

[PolyForm Noncommercial 1.0.0](https://github.com/BigCactusLabs/dead-letter/blob/main/LICENSE) — free for personal, educational, and nonprofit use. Commercial use requires a separate license from [Big Cactus Labs](https://github.com/BigCactusLabs).
