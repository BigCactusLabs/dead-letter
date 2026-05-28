---
description: Build a self-contained archive bundle for a single .eml — markdown, retained attachments, and source file in one directory.
disable-model-invocation: true
argument-hint: <path-to-eml> [bundle-root]
---

# /dead-letter:cabinet

Convert a single `.eml` into a self-contained bundle using the `convert_eml_to_bundle` MCP tool.

## Usage

```
/dead-letter:cabinet <path-to-eml> [bundle-root]
```

- `<path-to-eml>` (required): the source `.eml` file.
- `[bundle-root]` (optional): the **parent** directory where the bundle will be created.

## Bundle naming (important)

The bundle directory name is **always derived from the source filename stem** — this is enforced server-side by `convert_eml_to_bundle` (see `_bundle_slug` in `src/dead_letter/core/_pipeline.py`). The plugin command does not override it.

Example: `cabinet ~/Documents/q3-budget.eml ~/archive` produces `~/archive/q3-budget/` (containing the markdown, attachments, and the source `.eml`).

## Default `bundle-root` by runtime

If the user does not supply `bundle-root`:

- **Cowork** (detected by `uploads/` and `outputs/` existing in the working directory): default `bundle-root` to `outputs`. Result: `outputs/<source-stem>/`.
- **Claude Code** (local): default `bundle-root` to the parent directory of the source `.eml`. Result: `<source-dir>/<source-stem>/`.

## What to do

1. Resolve `bundle-root` per the rule above.
2. Call `convert_eml_to_bundle` with:
   - `eml_path=<path>`
   - `bundle_root=<resolved-bundle-root>`
   - `preset=default`
   - `source_handling=copy` (preserves the original file)
3. The tool returns JSON with `bundle_path`, `markdown_path`, `attachment_paths`, and optionally `diagnostics`.
4. Tell the user where the bundle landed (the `bundle_path` field), and surface any `diagnostics.warnings` if present.

## Custom bundle names

Out of scope for v1. The bundle directory is always the source stem. Users who want a different name can `mv` the result. This is the only command where the user might expect to control the output name; explain the constraint plainly if they ask.
