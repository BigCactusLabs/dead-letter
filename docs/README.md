# dead-letter docs

Start with the task. There is one installation map and one publishing runbook;
runtime contracts and historical design notes serve different purposes.

## Use dead-letter

| Task | Read |
| --- | --- |
| Understand the product and convert a first email | [README](../README.md) |
| Follow tested Markdown, RAG, MCP, Cabinet, and audit workflows | [Conversion recipes](recipes/README.md) |
| Convert a Gmail Takeout `.mbox` export locally | [Gmail Takeout / MBOX](reference/gmail-takeout.md) |
| Contain MBOX parser hangs or crashes with per-message deadlines | [MBOX Workers](reference/mbox-workers.md) |
| Measure or audit a full MBOX import | [MBOX Validation](reference/mbox-validation.md) |
| Install the MCP server in VS Code, Cursor, or Cline | [Client Installation](reference/client-installation.md) |
| Choose CLI, UI, MCPB, plugin, container, or skill | [Installation and distribution map](reference/distribution.md) |
| Configure an agent/MCP client safely | [Agent install guide](../llms-install.md) |
| Install or update the Claude plugin | [Plugin README](../plugin/README.md) |
| Install a portable skill or understand discovery | [Agent Discovery](reference/agent-discovery.md) |
| Run isolated MCP with selected mounts | [Containers](reference/containers.md) |
| Interpret conversion quality and retained attachments | [Quality Diagnostics](reference/quality-diagnostics.md) |
| Reproduce the token/fidelity comparison | [Benchmarks](../benchmarks/README.md) |
| Preview the experimental BYOK semantic analysis from a checkout | [Experimental analysis](reference/experimental-analysis.md) |

## Develop and maintain

| Task | Read |
| --- | --- |
| Set up development and run the required checks | [Contributing](../CONTRIBUTING.md) |
| Orient a coding agent | [AGENTS.md](../AGENTS.md) |
| Change core, CLI, API, or MCP behavior | [Runtime Contracts](reference/v4-runtime-contracts.md) |
| Change UI stores, job handling, or onboarding | [Frontend State Model](reference/frontend-state-model.md) |
| Prepare, tag, publish, or recover a release | [Publishing](reference/publishing.md) |
| Reconcile shipped channels or prepare a reviewed tap PR | [Release operations](reference/release-operations.md) |
| Track the draft-first immutable-release redesign | [Immutable sequencing plan](project/immutable-release-sequencing.md) |
| Check catalog listings and pending marketplace submissions | [MCP Distribution Ledger](reference/mcp-distribution.md) |
| Validate Claude-specific behavior manually | [Plugin testing](../plugin/TESTING.md) |
| Triage a CI checkout/source provenance mismatch | [CI Provenance](reference/ci-provenance.md) |
| Work on the visual identity | [Brand & Style Guide](brand/style-guide.md) |
| Review shipped changes or report a vulnerability | [Changelog](../CHANGELOG.md) / [Security](../SECURITY.md) |

“v4” in the runtime/state document names refers to the interface generation,
not the PyPI package version. Do not infer a package release from a document
filename. Machine-readable versions live in package/distribution metadata;
`python scripts/release.py check` verifies their relationships.

## Project decisions and history

[Reach & Distribution](project/reach.md) retains the product/discovery
strategy and outstanding evidence requirements. The
[MBOX ingestion design notes](project/2026-09-18-mbox-ingestion.md) are a
history record of the research and boundaries behind the streaming Takeout
importer and its worker follow-up, both shipped in 0.4.0; current availability
lives in [Gmail Takeout / MBOX](reference/gmail-takeout.md) and
[MBOX Workers](reference/mbox-workers.md). The
[analysis foundation checkpoint](project/2026-09-18-issue-110-analysis-foundation.md)
records what #117 landed for issue #110 and what remains. The
[issue #109 discovery evidence](project/2026-09-24-issue-109-discovery-evidence.md)
records portable-skill runtime, install, ARD, and Agent Finder checks. The
[docs and release audit](project/docs-release-audit.md) records this refresh,
its scope, and remaining validation boundaries. The completed
[html-to-markdown v3 migration](reference/html-to-markdown-v3-migration.md)
remains at its original URL for design history, not as a new implementation
queue.

## Keep the map useful

Give each topic a canonical home. Link to install commands and release policy
rather than copying version pins into every guide. Put durable contracts in
`docs/reference/`, plans and audit records in `docs/project/`, and brand work
in `docs/brand/`. Preserve existing URLs and heading anchors when practical.
Label completed or superseded plans explicitly; never present pending PRs as
released features.

Update the relevant docs in the same PR as a contract/workflow change. The
link-check workflow inventories tracked root guides, docs, plugin/skill
instructions, container/bundle docs, and the benchmark guide; test fixtures
are not public documentation. `release-check` catches version drift without
installing application dependencies. A green link check is not proof that a
client install, catalog listing, or runtime claim has been tested.
