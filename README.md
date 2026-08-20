<!-- mcp-name: io.github.BigCactusLabs/dead-letter -->

<p align="center">
  <img src="docs/brand/production/readme-logo.png" width="128" alt="dead-letter">
</p>

# dead-letter

[![PyPI package](https://img.shields.io/pypi/v/dead-letter?label=PyPI%20package&cacheSeconds=300)](https://pypi.org/project/dead-letter/)
[![Python versions](https://img.shields.io/pypi/pyversions/dead-letter?label=Python&cacheSeconds=300)](https://pypi.org/project/dead-letter/)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-purple.svg)](LICENSE)

**Your `.eml` files deserve a second life.**

dead-letter converts email exports into clean Markdown with YAML front matter — threads split, signatures stripped, attachments extracted, calendars parsed. One file or ten thousand.

## ✨ Features

- **Full-fidelity conversion** — HTML sanitization, Gmail/Outlook thread segmentation, inline image handling, and calendar event summaries
- **CLI** — point it at a file or a directory and go
- **Local web UI** — dark command-center interface with drag-and-drop import, watch mode, conversion grade badges, processing history, and per-job diagnostics
- **Inbox/Cabinet workflow** — drop `.eml` files into an Inbox, let dead-letter organize the Markdown bundles into a Cabinet
- **Install validation** — `dead-letter doctor` checks your runtime environment
- **Conversion report** — opt-in JSON report with per-file diagnostics, including attachment referenced/retained counts for automation and audit
- **MCP server** — integrate with Claude Desktop, Claude Code, Codex, and other MCP clients
- **Claude plugin** — one-command install in Claude Code or Cowork with four slash commands (`/dead-letter:convert`, `/dead-letter:summarize`, `/dead-letter:triage`, `/dead-letter:cabinet`)
- **Python API** — `from dead_letter import convert` and you're off

## 🧠 Built for LLM Pipelines

Raw `.eml` files are noisy input for downstream LLM and retrieval pipelines — MIME headers, multipart boundaries, duplicated HTML/plain bodies, and encoded attachments all get mixed into the text path.

dead-letter normalizes that into Markdown with YAML front matter, so message text and metadata are ready for chunking or indexing without MIME parsing or base64 cleanup. Default `convert()` and `convert_dir()` runs write a single `.md` per message and keep attachment names in front matter.

If you want the filesystem artifacts separated too, bundle and Cabinet workflows write `message.md` plus retained decoded files under `attachments/`. The Markdown is ready for text ingestion, while PDFs, spreadsheets, calendar files, and other retained binary attachments stay cleanly split out for whatever downstream parser you already use.

For direct LLM integration, the MCP server lets Claude Desktop, Claude Code, Codex, and other MCP clients call dead-letter's conversion tools without shelling out.

### 📊 Token-cost benchmarks

dead-letter's value isn't fewer tokens than every alternative — it's **fidelity per token**: the cheapest representation that keeps the email *intact*. Measured across a synthetic corpus of HTML threads, attachments, and newsletters (tokenizer `o200k_base`, medians):

- **~88% fewer tokens than the raw `.eml`** — a single email with a PDF attachment is ~126k tokens raw vs ~180 converted.
- **The only representation that keeps the email whole** — thread structure, per-message sender attribution, links, and attachment metadata all survive. Naive text extraction is cheaper precisely because it *drops* them (0/2 attachments retained vs dead-letter's 2/2).

The benchmark is honest about where it loses: naive extraction is fewer tokens when you don't mind throwing away attachments, links, and thread structure. Full method, the complete table (including those rows), tokenizer disclosure, and a one-command reproduce are in [`benchmarks/`](benchmarks/).

## 📦 Install

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
pip install dead-letter[cli]       # + watchfiles (used by backend/UI watch mode)
pip install dead-letter[ui]        # + web UI, API server, and watch mode
pip install dead-letter[mcp]       # + MCP server
```

Use [pipx](https://pipx.pypa.io/) for isolated UI or MCP installs:

```bash
pipx install 'dead-letter[ui]'    # installs dead-letter and dead-letter-ui
pipx install 'dead-letter[mcp]'   # installs dead-letter and dead-letter-mcp
```

From source:

```bash
git clone https://github.com/BigCactusLabs/dead-letter.git
cd dead-letter
uv sync --extra dev     # all extras
uv sync --extra ui      # UI only
uv sync --extra mcp     # MCP only
```

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

Open `http://127.0.0.1:8765` — on first launch, a setup prompt suggests default Inbox and Cabinet folders. Configure or skip to start converting. Import `.eml` files with drag and drop or the file picker. Single-file imports use file mode, while multi-file drops create one directory-mode batch job. Mixed drops ask for confirmation before skipping non-`.eml` files.
The backend enforces a 100 MB per-file import limit for both single and batch
uploads.

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
machine-detectable. See [Quality Diagnostics](docs/reference/quality-diagnostics.md).

Batch:

```python
from dead_letter import convert_dir

for r in convert_dir("inbox/", output="out/"):
    print(f"{'✓' if r.success else '✗'} {r.source.name}")
```

## 🔌 MCP Server

dead-letter ships an [MCP](https://modelcontextprotocol.io/) server so LLM clients can convert `.eml` files directly without shelling out.

Install and launch:

```bash
pip install dead-letter[mcp]
dead-letter-mcp
```

From a source checkout:

```bash
uv run --extra mcp dead-letter-mcp
```

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dead-letter": {
      "command": "uv",
      "args": ["--directory", "/path/to/dead-letter", "run", "--extra", "mcp", "dead-letter-mcp"]
    }
  }
}
```

**Claude Code or Cowork (recommended — Claude plugin):**

```
/plugin marketplace add BigCactusLabs/bigcactuslabs-plugins
/plugin install dead-letter
```

The plugin bundles the MCP server (via `uvx`, no `pip install` needed — just `uv` on `PATH`) and adds four slash commands: `/dead-letter:convert`, `/dead-letter:summarize`, `/dead-letter:triage`, `/dead-letter:cabinet`. Email content handled through the plugin is treated as untrusted data, not instructions, so tool-use, credential, and exfiltration requests embedded in messages are not followed. Source under [`plugin/`](plugin/).

The marketplace distributes the plugin from this repo's `release` branch, which only moves on a published release; the bundled MCP server is pinned to an exact PyPI version, so installs are reproducible and auto-update only delivers released versions.

**Claude Code (manual MCP add — alternative):**

```bash
claude mcp add dead-letter -- uv run --extra mcp dead-letter-mcp
```

**Codex:**

```bash
codex mcp add dead-letter -- uv run --extra mcp dead-letter-mcp
codex mcp list
```

The `codex mcp add` command registers the local `dead-letter` MCP server, and `codex mcp list` verifies that it's available.

### Tools

| Tool | Required arguments | Returns |
| --- | --- | --- |
| `convert_eml` | `eml_path` | Markdown text. Also writes a file when `output_path` is given. |
| `convert_eml_to_bundle` | `eml_path`, `bundle_root` | JSON with `bundle_path`, `markdown_path`, `attachment_paths`. Copy-only: the original `.eml` is never moved or deleted. |
| `convert_directory` | `directory`, `output_directory` | JSON summary. Capped at 50 `.eml` files per call. |
| `get_diagnostics` | `eml_path` | Quality and structure JSON. Writes nothing permanent. |

All four take a `preset` (`default`, `clean`, `verbose`, `raw`) and per-flag overrides. Full contract, including the MCP-only constraints and the error-text table: [`docs/reference/v4-runtime-contracts.md`](docs/reference/v4-runtime-contracts.md#mcp-server-dead_letterbackendmcp_server).

## 🗂 Project Structure

```
src/dead_letter/
├── core/           # conversion pipeline (MIME, HTML, threads, rendering)
├── backend/        # CLI, API server, job runner, watch mode, MCP server
└── frontend/       # static web UI (Alpine.js ES modules + vanilla fetch)
tests/
├── core/           # conversion pipeline tests with .eml fixtures
├── backend/        # API, job, and watch tests
├── plugin/         # Claude plugin manifest, skill, and command tests
└── frontend/       # JS unit tests
```

## 🧪 Testing

```bash
uv run pytest -q tests/core        # conversion pipeline
uv run pytest -q tests/backend     # API and job runner
uv run pytest -q tests/plugin      # Claude plugin manifest, skill, and command surfaces
node --test tests/frontend/*.test.js     # frontend
```

CI runs all four on PRs and on pushes to `main` or `feat/**` branches with the
same commands, plus
`npx --yes @anthropic-ai/claude-code@2.1.145 plugin validate plugin/` and
`node --check src/dead_letter/frontend/static/app.js`.

## 📚 Docs

- [Docs Index](docs/README.md) — public docs landing page
- [Runtime Contracts](docs/reference/v4-runtime-contracts.md) — full API and core behavior spec
- [Frontend State Model](docs/reference/frontend-state-model.md)
- [Quality Diagnostics](docs/reference/quality-diagnostics.md)
- [Brand & Style Guide](docs/brand/style-guide.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Agent Guide](AGENTS.md) — operational guide for AI coding agents working in this repo

## 🔧 Tools We Love

- **[MarkEdit](https://github.com/MarkEdit-app/MarkEdit)** — TextEdit for Markdown, native macOS, ~4 MB. Opens dead-letter output like it was always meant to live there.
- **[mo](https://github.com/k1LoW/mo)** — local Markdown viewer that renders files in the browser with live reload. Point it at your Cabinet and read converted mail like a feed.

## ⚠️ Known Limitations

- Local-only — no remote server, no auth
- In-memory job registry (state resets on restart)
- Single-user, single-machine

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal, educational, and nonprofit use. Commercial use requires a separate license from [Big Cactus Labs](https://github.com/BigCactusLabs).
