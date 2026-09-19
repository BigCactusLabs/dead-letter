# Docs

`docs/` is the public documentation surface for `dead-letter`.

## Public Docs

- [Gmail Takeout / MBOX](reference/gmail-takeout.md) — local streaming Markdown/Cabinet recipe, metadata, and format boundaries
- [MBOX Workers](reference/mbox-workers.md) — optional per-message deadlines and precise crash-containment limits
- [MBOX Validation](reference/mbox-validation.md) — full-import measurements, byte-level audits, and a local real-archive validation procedure
- [Runtime Contracts](reference/v4-runtime-contracts.md) — canonical backend and core runtime behavior
- [Frontend State Model](reference/frontend-state-model.md) — frontend state and interaction contract
- [Quality Diagnostics](reference/quality-diagnostics.md) — conversion grading and warning semantics
- [html-to-markdown v3 Migration Plan](reference/html-to-markdown-v3-migration.md) — completed migration reference from 2.x visitor APIs
- [Publishing](reference/publishing.md) — maintainer release and Homebrew tap update runbook
- [Agent Discovery](reference/agent-discovery.md) — portable Agent Skill install, ARD catalog, and Agent Finder submission
- [Brand Style Guide](brand/style-guide.md) — canonical visual language and production integration notes

## Integration

- [MCP Server](../README.md#-mcp-server) — Claude Desktop, Claude Code, and Codex integration paths
- [Claude plugin](../plugin/README.md) — one-command install for Claude Code and Cowork (recommended for those clients); distributed via the [`BigCactusLabs/bigcactuslabs-plugins`](https://github.com/BigCactusLabs/bigcactuslabs-plugins) marketplace
