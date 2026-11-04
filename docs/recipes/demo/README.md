# Recipe demo: storyboard and capture record

A 27-second silent demo of released dead-letter 0.4.10 converting the
[synthetic recipe inbox](../fixtures/inbox/). The scenes show only text from
the run. Long content is shortened with a visible `… N lines not shown …`
marker.

| File | Format | Size |
| --- | --- | --- |
| [`dead-letter-demo.gif`](dead-letter-demo.gif) | GIF, 800×450, loops | 186,347 bytes |
| [`dead-letter-demo.mp4`](dead-letter-demo.mp4) | H.264, yuv420p, 1600×900, 30 fps, no audio | 551,078 bytes |
| [`dead-letter-demo.png`](dead-letter-demo.png) | Still of scene 3, 1600×900 | 142,079 bytes |
| [`capture.json`](capture.json) | Commands, full outputs, resolved packages, host tools | — |

## Storyboard

| Time | Scene | What it shows |
| --- | --- | --- |
| 0–5 s | Messy input | `cat` of `clients/order-update.eml`: headers, MIME boundaries and the base64 CSV part. The 9 body lines are hidden here and appear in scene 3. |
| 5–10 s | Released CLI | The two input files, `dead-letter convert inbox --output vault` (exit 0, no terminal output), and the mirrored `clients/` and `ops/` Markdown files. |
| 10–17 s | Markdown | The whole converted Markdown file: YAML front matter (date normalized to UTC) followed by the reply body. |
| 17–22 s | Cabinet | `convert_to_bundle(..., source_handling="copy")` from the Python API, the bundle files, the decoded CSV, and the source `.eml`, which is still in `inbox/clients/`. |
| 22–27 s | Diagnostics | `--report` and an excerpt of `.dead-letter-report.json` (generator and summary), next to the MCP `get_diagnostics` result for the same message. |

Each scene stays on screen for at least 5 seconds and has a short caption.
The CLI report in 0.4.10 has no `diagnostics` object, so scene 5 gets
diagnostics from the released MCP server (`dead-letter-mcp`) over stdio. This is
the route described in the [report recipe](../report-and-diagnostics.md).

## Capture

Captured on 2026-11-04 with uv 0.11.32, CPython 3.12.3, Node 22.23.4
(Playwright Chromium) and ffmpeg 6.1.1 on Linux x86_64.

- **Package:** `dead-letter==0.4.10`, installed from PyPI with
  `uv venv --python 3.12` and then
  `uv pip install --exclude-newer 2026-10-30T00:00:00Z dead-letter==0.4.10`.
  This installs into a temporary virtual environment, not the checkout. The
  script checks that `import dead_letter` reports `0.4.10` and resolves outside
  the repository.
- **Resolution:** 0.4.10 was uploaded to PyPI on 2026-10-29. The
  `--exclude-newer` cutoff keeps its dependencies at versions that existed then.
  `capture.json` lists every package that was resolved. The published wheel's
  SHA-256 on PyPI is
  `6d8c2deaefa18c1b372fc0222be8458ed65e62ae1c4b036c7548e68240b806d1`. The
  script does not re-hash the wheel it installs.
- **Commands:** the commands below ran with `bash -c` in a temporary copy of
  the fixtures, with the venv's `bin/` first on `PATH`. They are shown exactly
  as they ran. For the exact output of each, see `capture.json` (temporary paths
  are replaced with `<work>`).

  ```bash
  cat inbox/clients/order-update.eml
  find inbox -type f | sort
  dead-letter convert inbox --output vault
  find vault -type f | sort
  cat vault/clients/cedar-works-order-4821-revised-delivery.md
  python - <<'PY'   # body abbreviated: convert_to_bundle(..., bundle_root="Cabinet", source_handling="copy")
  find Cabinet -type f | sort
  cat Cabinet/order-update/attachments/order-4821.csv
  ls inbox/clients
  dead-letter convert inbox/clients/order-update.eml --output audit --report
  cat audit/.dead-letter-report.json
  ```

  It then makes one MCP `tools/call` to `get_diagnostics` with the copied
  message's absolute `eml_path`.
- **Checks before rendering:** the script stops before rendering if any of these
  fail:
  - every command exits 0
  - the output layout matches the [folder recipe](../folder-to-markdown.md)
  - the bundled `.eml` is byte-identical to its source
  - the CSV decodes to `sku,quantity` / `notebook,24` / `pen,48`
  - the report summary is `1/1/0/0`
  - diagnostics report one referenced and one retained attachment
  - neither source `.eml` changed (SHA-256 recorded in `capture.json`)

  The repository fixtures are copied and never touched.
- **Rendering:** Brume tokens from the [style guide](../../brand/style-guide.md):
  Inter for captions and the guide's mono stack for terminal text. None of the
  named mono fonts were installed on this host, so it fell back to DejaVu Sans
  Mono, and a regeneration elsewhere may use a different fallback. Frames are
  laid out at 800×450 CSS pixels. They are captured at 1× for the GIF (no
  downscaling; 13.6 px mono, about the size of GitHub code text) and at 2× for the MP4
  and the still. The GIF is built with palettegen/paletteuse and no dithering.
  The script fails if any text pane overflows.

## Regenerate

Do not hand-edit these files. To regenerate them, run from the repository root:

```bash
python scripts/render_demo.py            # rewrites docs/recipes/demo/
python scripts/render_demo.py --out /tmp/demo --keep-work   # inspect without touching docs
```

You need network access to PyPI, plus uv, bash, ffmpeg and Node with the
`playwright` package and its Chromium. This is a maintainer tool. CI and the
test suites do not run it. A new run changes the report's `created_at` and
`duration_ms` and may encode slightly different bytes. Update the sizes above
and the version in the [recipes index](../README.md) caption when you
regenerate for another release.
