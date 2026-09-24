# Prepare exported email for RAG

**Problem.** Raw MIME boundaries and encoded attachments make poor retrieval
input. Convert email to readable text and retain detected thread structure
before your own chunking and indexing steps.

From the repository root, with the [CLI installed](../reference/distribution.md):

```bash
dead-letter convert docs/recipes/fixtures/inbox/ --output rag-ready/ --thread-mode structured
```

The reusable [synthetic inbox](fixtures/inbox/) produces:

```text
rag-ready/
├── clients/cedar-works-order-4821-revised-delivery.md
└── ops/loading-window-for-cedar-works.md
```

Relative source folders remain distinct. The loading-window file has YAML
`thread_messages: 1` and an `## Earlier message` section for the quoted
request. The order file has sender/date/subject metadata, readable paragraphs,
and the attachment name. The original `.eml` files remain in place.

This is **preprocessing only**. It creates no chunks, embeddings, vector
index, or retrieval quality claim. The Markdown lists `order-4821.csv`, but
does not contain its decoded bytes or extracted table text. If attachment
content matters, first retain its bytes with the [Cabinet recipe](durable-cabinet.md),
then pass that file through an appropriate downstream parser and indexer.

See the [thread and CLI contract](../reference/v4-runtime-contracts.md#thread-history)
and [quality diagnostics](../reference/quality-diagnostics.md) before trusting
converted content for answers.
