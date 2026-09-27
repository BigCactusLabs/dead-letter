# Keep a durable Cabinet bundle

**Problem.** A flat Markdown file names attachments but cannot preserve their
decoded bytes. A bundle keeps the Markdown, original mail, and retained files
together for later review or archival.

From the repository root, with the [Python package installed](../reference/distribution.md):

```bash
python - <<'PY'
from dead_letter import convert_to_bundle

result = convert_to_bundle(
    "docs/recipes/fixtures/inbox/clients/order-update.eml",
    bundle_root="Cabinet/",
    source_handling="copy",
)
assert result.success, result.error
print(result.bundle)
PY
```

For the [synthetic order message](fixtures/inbox/clients/order-update.eml),
released 0.4.0 writes:

```text
Cabinet/order-update/
├── message.md
├── order-update.eml
└── attachments/order-4821.csv
```

`message.md` has YAML metadata and a relative
`attachments/order-4821.csv` reference. The CSV has its original decoded
bytes (`sku,quantity` plus two rows), and the bundled `.eml` is byte-identical
to the input. `source_handling="copy"` leaves that input in place. This argument
matters: the Python API's default is `"move"`. Each bundle is named from the
source stem; collision suffixes can change the exact directory name on a
repeat run. A bundle stores attachment bytes, not extracted PDF/CSV prose for
search. Parse retained files separately if their content must be indexed.

See the [bundle and source-handling contract](../reference/v4-runtime-contracts.md#convert_to_bundlepath--bundle_root-optionsnone-source_handlingmove---bundleresult)
and [quality diagnostics](../reference/quality-diagnostics.md).
