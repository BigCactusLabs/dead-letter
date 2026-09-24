# Audit a conversion with a report and quality diagnostics

**Problem.** You need a persistent record of which messages converted and a
way to inspect uncertain content before using it downstream.

From the repository root, with the [CLI installed](../reference/distribution.md):

```bash
dead-letter convert docs/recipes/fixtures/inbox/clients/order-update.eml --output audit/ --report
```

For the [synthetic order message](fixtures/inbox/clients/order-update.eml),
released 0.4.0 writes:

```text
audit/
├── cedar-works-order-4821-revised-delivery.md
└── .dead-letter-report.json
```

The hidden JSON report has `schema_version: 1`, generator version `0.4.0`,
`summary: {"total": 1, "written": 1, "skipped": 0, "errors": 0}`, and a
successful `results[]` entry for `order-update.eml`. The source remains in
place. A directory run places one report at the output root and records
source-relative paths, so `clients/` and `ops/` remain distinguishable.

In released 0.4.0, this CLI report does **not** contain the detailed
`diagnostics` object. For a single message, call MCP `get_diagnostics` with
its absolute `eml_path`, or inspect the [file-job API's diagnostics](../reference/v4-runtime-contracts.md#get-apijobsid)
when using the UI. The real MCP call on this sample reported one referenced
and one retained attachment. Review `state`, `confidence`, `warnings`, and
`attachments` counts; a successful report alone does not prove that extracted
text is complete or suitable for retrieval. `get_diagnostics` writes no
permanent files. Its quality signal does not inspect the CSV's content.

See the [CLI report contract](../reference/v4-runtime-contracts.md#cli-dead-letter),
[MCP tool contract](../reference/v4-runtime-contracts.md#mcp-server-dead_letterbackendmcp_server),
and [field definitions](../reference/quality-diagnostics.md).
