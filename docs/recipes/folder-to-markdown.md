# Put an EML folder in Obsidian or another Markdown tree

**Problem.** You have exported `.eml` files arranged in folders and want
readable Markdown in an Obsidian vault or any ordinary directory.

From the repository root, with the [CLI installed](../reference/distribution.md):

```bash
dead-letter convert docs/recipes/fixtures/inbox/ --output vault/Email/
```

The [synthetic inbox](fixtures/inbox/) has `clients/order-update.eml` and
`ops/loading-window.eml`. On released 0.4.0, the result is:

```text
vault/Email/
├── clients/cedar-works-order-4821-revised-delivery.md
└── ops/loading-window-for-cedar-works.md
```

The source folders remain intact, and their relative paths are mirrored in
the output. Each Markdown file has YAML front matter for source, subject,
sender, date, and attachment names, followed by readable body text. The
default latest-message mode keeps the authored reply body; use
`--thread-mode structured` when you also need detected earlier messages.

The CSV is named in the Markdown metadata, but flat conversion does not write
decoded attachment bytes. Use the [Cabinet recipe](durable-cabinet.md) when
you need them. An Obsidian vault is only a destination directory here; no
Obsidian plugin or indexing step is required.

See the [CLI and directory rules](../reference/v4-runtime-contracts.md#cli-dead-letter)
and [quality diagnostics](../reference/quality-diagnostics.md).
