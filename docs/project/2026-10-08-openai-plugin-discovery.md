# ChatGPT and Codex plugin discovery exploration

Date: 2026-10-08 (America/Chicago). Status: research and proposed experiment.
Source checkout: `3b262099a9a36d83b23edd24c97a0554823d3450`.
Branch: `cdx/openai-plugin-discovery` in a separate managed worktree.
No plugin has been installed, submitted, accepted, or published by this work.

## Recommendation

A hosted HTTPS MCP service is a viable candidate for public discovery. Start
with a small remote adapter for one uploaded `.eml`, reusing the converter.
Keep the existing local CLI and stdio MCP routes available for local archives.
The first experiment should establish upload compatibility, conversion fidelity,
cleanup, and client behavior before choosing a host or preparing submission.
This is an engineering recommendation, not evidence of directory eligibility.

OpenAI now documents one public plugin directory shared by ChatGPT and Codex.
A plugin can package skills, MCP connections, or both. A custom connection or a
local marketplace is a test/distribution route; it is not public directory
publication. [Plugin architecture](https://developers.openai.com/plugins/concepts/plugins)

## Existing building blocks

Verified in this checkout:

- [Portable skill](../../skills/dead-letter/SKILL.md): provider-neutral email
  workflows; uses an existing MCP server or `uvx`. It requires a working
  runtime. There is no `agents/openai.yaml` in this skill directory.
- [Claude package](../../plugin/.claude-plugin/plugin.json) and
  [launcher](../../plugin/.mcp.json): a Claude manifest plus a local `uvx`
  command. This is not already an OpenAI public plugin package.
- [MCP implementation](../../src/dead_letter/backend/mcp_server.py): five
  tools using local paths. Existing annotations distinguish reads from file
  creation. MBOX conversion is bounded to 256 MiB and 1000 messages, but the
  tool explicitly does not support cancellation or a time limit.
- [Local UI API](../../src/dead_letter/backend/api.py): `.eml` upload streaming,
  filename handling, and byte/batch limits provide patterns to inspect and
  reuse. They do not establish a multi-user hosted service contract.
- [Python metadata](../../pyproject.toml): Python 3.12+, native parsing
  dependencies, and the shared conversion package. A Python container is a
  natural candidate; an edge-runtime port is not necessary for this experiment.
- [Distribution map](../reference/distribution.md): local conversion, MCPB,
  OCI, Claude integration, and portable skills are separate channels. ARD and
  registry metadata do not establish acceptance in OpenAI's directory.
- [License](../../LICENSE): PolyForm Noncommercial 1.0.0. Preserve notices and
  have the publisher settle intended hosted-service terms and submission rights.
  This research does not establish platform acceptance of those terms.

## Routes and their tradeoffs

| Route | What it enables | Main unresolved item |
| --- | --- | --- |
| Local/repository plugin | Test packaging and discovery on supported local clients | Runtime and client installation must be tested; no public listing implied |
| Skills-only public plugin | Reuse portable instructions with less service infrastructure | Clean-environment Python, package access, and file access; local-execution eligibility |
| Skill plus hosted HTTPS MCP | Standard remote service path for a public listing | Upload adapter, isolation, operations, and review |
| Local MCP through a tunnel | Private end-to-end connection experiment | Does not replace the public endpoint required for public submission |

New portable packages use root `plugin.json`, `skills/`, and optional
`mcp.json`; OpenAI presentation metadata belongs under
`extensions.com.openai`. The `.codex-plugin/plugin.json` format is also
supported. Local catalogs can expose a package through
`.agents/plugins/marketplace.json`.
[Packaging](https://developers.openai.com/plugins/build/plugins)

OpenAI's Claude migration guide says to host local MCP at public HTTPS or
contact OpenAI for local MCP support. It also directs builders to contact
OpenAI when core value needs local execution, arbitrary local files, or offline
operation. Therefore the existing skill's `uvx` dependency cannot simply be
assumed to work everywhere. Reuse the portable skill as the starting point,
rather than carrying over Claude slash commands and Cowork paths.
[Claude migration](https://developers.openai.com/plugins/guides/submit-claude-plugin)

## Proposed hosted design

```text
User selects an email in the client
  -> client passes an authorized file reference to an HTTPS MCP tool
  -> service validates and downloads into an isolated temporary workspace
  -> bounded worker calls dead-letter's existing conversion pipeline
  -> tool returns Markdown and structured diagnostics
  -> service removes its input and temporary output
```

OpenAI documents `_meta["openai/fileParams"]` for tool file inputs. A file
object schema declares `download_url`, `file_id`, `mime_type`, and `file_name`;
only the first two are required. The service receives a temporary download
URL. This supports the proposed transfer mechanism, but does not prove that
`.eml` or `.mbox` uploads work on every target surface. Test actual uploads;
never infer support for an extension from generic file APIs.
[File input reference](https://developers.openai.com/plugins/reference)

Proposed initial tool: `convert_uploaded_email(file, preset)` returning bounded
Markdown, diagnostic fields, and clear failure reasons. Reuse conversion code;
do not expose user-supplied filesystem paths, directory traversal, or the full
local UI API. Do not require an LLM call inside the service for conversion.
Optional semantic analysis remains a separate feature.

Start with one `.eml` and a text result. Add downloadable Markdown only after
verifying artifact delivery and access control. Defer MBOX, batch archives,
extracted attachment downloads, persistent libraries, and a custom widget.
These are proposed scope limits, not changes to existing local capabilities.

The remote adapter must implement:

- Download validation: HTTPS, restricted destinations, redirect and DNS checks,
  blocked private/metadata addresses, response size limits, and timeouts. Treat
  every URL and filename as untrusted even when provided through a host field.
- Processing isolation: separate temporary files per request, bounded CPU,
  memory, input/output size and wall time; terminate a worker on timeout.
- Cleanup on success, error, cancellation and process failure. If artifacts
  are later stored, use expiring access and a documented deletion schedule.
- Minimal logs: no raw email, decoded attachments, or signed download URLs.
- Defined authentication, authorization, quotas and abuse controls. Do not
  treat an MCP session identifier as proof of a user's identity.
- Copy-only source handling and prompt-injection boundaries. Email text and
  filenames cannot authorize network requests or subsequent tool calls.

These controls are proposed engineering acceptance criteria. They are not
implemented by this research. They apply OpenAI's data-minimization, retention,
logging and input-validation guidance to the existing converter.
[Security and privacy](https://developers.openai.com/plugins/guides/security-privacy)

Public MCP submission requires a stable HTTPS endpoint with Streamable HTTP.
A custom UI is optional. A container service is a candidate deployment shape;
select the provider after measuring startup time, memory, conversion duration,
concurrency and request limits. No hosting cost has been estimated or incurred.
[Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)

## Privacy and product implications

A hosted route sends email content to the service operator. It cannot carry the
local route's claim that conversion runs entirely on the user's computer.
Describe the boundary explicitly: local conversion remains local; hosted
conversion processes selected uploads remotely. Our deletion policy would cover
our infrastructure, not copies retained by ChatGPT or another client.

OpenAI requires a published privacy policy and accurate product claims.
Skills-only plugins also undergo scanning and may have additional eligibility
requirements. Position this around deterministic MIME conversion, metadata,
thread handling and diagnostics; do not imply mailbox access or generic file
conversion. [Plugin guidelines](https://developers.openai.com/plugins/plugin-guidelines)

## First experiment and acceptance checks

1. Build a separate remote adapter and draft plugin package in this branch,
   preserving the current stdio API. Use synthetic email only.
2. Exercise the remote contract locally with real MCP initialization, tool
   enumeration and calls. Test URL rejection, malformed MIME, byte/time limits,
   cleanup after failure, source preservation and parallel request isolation.
3. Compare converted content and diagnostics with the same fixture through the
   existing core. Test HTML, forwarded threads, Unicode and attachment metadata.
4. In a separately approved test deployment or tunnel, test actual `.eml`
   selection and transfer in ChatGPT and Codex. Record each client/version and
   identify any differences. A protocol smoke is not a GUI installation test.
5. Test discovery with direct, indirect and negative prompts. Confirm unrelated
   requests do not select dead-letter and injected email instructions are ignored.
6. Record latency, resource use, cold starts and failure rates. Use those results
   to choose service limits, hosting and the next scope increment.

OpenAI supports private MCP testing through HTTPS or Secure MCP Tunnel, followed
by complete plugin testing through a local source. The tunnel route does not
satisfy the public endpoint requirement for directory submission.
[Connect and test](https://developers.openai.com/plugins/deploy/connect-chatgpt)

## Public submission decisions

Before the first public submission, choose skills-only or MCP-backed deliberately:
OpenAI currently does not support adding MCP to an existing skills-only plugin.
An MCP-backed submission needs a verified publishing identity and appropriate
organization permissions, listing assets, website/support/privacy/terms URLs,
domain verification, five positive and three negative review cases, and a demo
recording. Approval and publication are separate steps.
[Submission](https://developers.openai.com/plugins/deploy/submission)

Use remote review requirements to verify endpoint readiness and review access.
Account eligibility, publisher verification, domain ownership, review outcome,
and file support across clients remain unverified.
[Remote MCP review](https://developers.openai.com/plugins/deploy/app-review)

This exploration authorizes no deployment, account connection, submission,
publication, release pointer change, or external message. The next implementation
slice is the local single-email HTTP adapter and its regression checks.
