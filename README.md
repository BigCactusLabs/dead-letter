<p align="center">
  <img src="docs/brand/production/readme-logo.png" width="128" alt="dead-letter">
</p>

# dead-letter

[![PyPI](https://img.shields.io/pypi/v/dead-letter?cache=bust)](https://pypi.org/project/dead-letter/)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-purple.svg)](LICENSE)

**Your `.eml` files deserve a second life.**

dead-letter converts email exports into clean Markdown with YAML front matter — threads split, signatures stripped, attachments extracted, calendars parsed. One file or ten thousand.

## ✨ Features

- **Full-fidelity conversion** — HTML sanitization, Gmail/Outlook thread segmentation, inline image handling, and calendar event summaries
- **CLI** — point it at a file or a directory and go
- **Local web UI** — dark command-center interface with drag-and-drop import, watch mode, conversion grade badges, processing history, and per-job diagnostics
- **Inbox/Cabinet workflow** — drop `.eml` files into an Inbox, let dead-letter organize the Markdown bundles into a Cabinet
- **Install validation** — `dead-letter doctor` checks your runtime environment
- **Conversion report** — opt-in JSON report with per-file diagnostics for automation and audit
- **Python API** — `from dead_letter import convert` and you're off

## 🧠 Built for LLM Pipelines

Raw `.eml` files are noisy input for downstream LLM and retrieval pipelines — MIME headers, multipart boundaries, duplicated HTML/plain bodies, and encoded attachments all get mixed into the text path.

dead-letter normalizes that into Markdown with YAML front matter, so message text and metadata are ready for chunking or indexing without MIME parsing or base64 cleanup. Default `convert()` and `convert_dir()` runs write a single `.md` per message and keep attachment names in front matter.

If you want the filesystem artifacts separated too, bundle and Cabinet workflows write `message.md` plus decoded files under `attachments/`. The Markdown is ready for text ingestion, while PDFs, spreadsheets, calendar files, and other binary attachments stay cleanly split out for whatever downstream parser you already use.

## 📦 Install

```bash
pip install dead-letter            # core + CLI
pip install dead-letter[cli]       # + watch mode (watchfiles)
pip install dead-letter[ui]        # + web UI, API server, and watch mode
pip install dead-letter[mcp]       # + MCP server
```

Or use [pipx](https://pipx.pypa.io/) for an isolated install:

```bash
pipx install 'dead-letter[ui]'   # installs dead-letter and dead-letter-ui commands
```

From source:

```bash
git clone https://github.com/BigCactusLabs/dead-letter.git
cd dead-letter
uv sync --extra dev     # all extras
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
case-insensitively, and skips symlinked files whose resolved targets escape the
requested input tree.

**Web UI** — start the local server:

```bash
dead-letter-ui --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` — on first launch, a setup prompt suggests default Inbox and Cabinet folders. Configure or skip to start converting. Import `.eml` files with drag and drop or the file picker. Single-file imports use file mode, while multi-file drops create one directory-mode batch job. Mixed drops ask for confirmation before skipping non-`.eml` files.

From a source checkout, prefix with `uv run`:

```bash
uv run dead-letter convert message.eml
uv run dead-letter-ui --host 127.0.0.1 --port 8765
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

Bundle conversion (Markdown + attachments + source in one directory):

```python
from dead_letter import convert_to_bundle

bundle = convert_to_bundle("message.eml", bundle_root="cabinet/")
print(bundle.markdown)     # cabinet/message/message.md
print(bundle.attachments)  # [cabinet/message/attachments/logo.png, ...]
```

Extracted attachment filenames are normalized to safe basenames before they are
written under `attachments/`.

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

**Claude Code:**

```bash
claude mcp add dead-letter -- uv run --extra mcp dead-letter-mcp
```

## 🗂 Project Structure

```
src/dead_letter/
├── core/           # conversion pipeline (MIME, HTML, threads, rendering)
├── backend/        # CLI, API server, job runner, watch mode
└── frontend/       # static web UI (htmx + Alpine.js)
tests/
├── core/           # conversion pipeline tests with .eml fixtures
├── backend/        # API, job, and watch tests
└── frontend/       # JS unit tests
```

## 🧪 Testing

```bash
uv run pytest tests/core           # conversion pipeline
uv run pytest tests/backend        # API and job runner
node --test tests/frontend/*.test.js     # frontend
```

CI runs all three on every push and PR.

## 📚 Docs

- [Docs Index](docs/README.md) — public docs landing page
- [Runtime Contracts](docs/reference/v4-runtime-contracts.md) — full API and core behavior spec
- [Frontend State Model](docs/reference/frontend-state-model.md)
- [Quality Diagnostics](docs/reference/quality-diagnostics.md)
- [Brand & Style Guide](docs/brand/style-guide.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

## ⚠️ Known Limitations (v0.1)

- Local-only — no remote server, no auth
- In-memory job registry (state resets on restart)
- Single-user, single-machine

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal, educational, and nonprofit use. Commercial use requires a separate license from [Big Cactus Labs](https://github.com/BigCactusLabs).
