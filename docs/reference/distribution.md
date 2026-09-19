# Installation and distribution map

Choose a way to run dead-letter, not a collection of channels to install.
The CLI, web UI, Python API, and MCP server share one Python package. The
Claude plugin and portable skill add instructions and integration, not a
second email converter.

## Choose a route

| You need | Route | What it includes | Instructions |
| --- | --- | --- | --- |
| Convert a file or folder | `uvx`, pip, or pipx | Core converter and CLI | [README](../../README.md#-install) |
| A Homebrew-managed CLI | `BigCactusLabs/tap` | Core CLI only; no UI or MCP extras | [README](../../README.md#-install) |
| Drag-and-drop and Inbox/Cabinet | `dead-letter[ui]` | Local UI, API, and watch dependencies | [Quick Start](../../README.md#-quick-start) |
| Tools in an MCP client | `dead-letter[mcp]` via `uvx` or pipx | Local stdio server and four tools | [Agent install guide](../../llms-install.md) |
| Claude Desktop extension | Release `.mcpb` plus its checksum | Managed uv launcher with an exact package pin | [MCP setup](../../README.md#-mcp-server) |
| Claude Code / Cowork commands | BCL plugin marketplace | Plugin commands and pinned MCP launcher | [Plugin guide](../../plugin/README.md) |
| Container-isolated MCP | GHCR version or digest | Non-root stdio server; explicit input/output mounts | [Containers](containers.md) |
| A portable task skill | `skills/dead-letter/` | Instructions for an existing MCP server or CLI | [Agent Discovery](agent-discovery.md) |

Normal installs do not require Docker. Homebrew's formula intentionally does
not install the optional Python dependency stacks. Installing a skill does
not install the Claude plugin; installing the plugin does not install the
portable skill.

## Artifacts are not listings

| Surface | Role | Release authority |
| --- | --- | --- |
| PyPI | Python distributions, including optional extras | Stable `vX.Y.Z` GitHub release |
| GitHub release assets | MCPB, checksum, and (for releases using the refreshed workflow) resolved registry manifest | Same package release |
| GHCR | Tested multi-platform OCI image | Same package release; version and digest, no `latest` alias |
| Homebrew tap | Separate core-only formula | Maintainer update after PyPI |
| BCL plugin marketplace | Versioned plugin source pointer | Separate `plugin-vX.Y.Z` tag and marketplace PR |
| Official MCP Registry | Discovery metadata for existing packages and artifacts | After PyPI, MCPB, and OCI verification |
| Portable skill / ARD | Repository-hosted task instructions and discovery metadata | Reviewed source tag/commit; package version in ARD |
| Docker Catalog, Agent Finder, other curated directories | Third-party discovery and review | Separate submission, acceptance, and client validation |

A repository file is **implemented**. An accessible release artifact is
**published**. A catalog PR is **submitted**, not **accepted**. A successful
CLI/MCP smoke test is not a GUI **client-tested** result. Record the version,
commit or digest, date, platform/client, and evidence with each status.

The first public GHCR image is recorded as `0.3.1`; the failed `0.3.0` attempt
must not be advertised as an available container. See the dated
[container availability record](containers.md#availability). The repository's
[reach plan](../project/reach.md) tracks unfinished discovery work, not an
alternate source of current installation instructions.

## Pin the thing you actually install

A package version, plugin asset version, and plugin's exact package pin may
legitimately differ. Consult the selected release or plugin manifest, not
`main`, to identify a published version. `main` can contain the next release's
metadata before its artifacts exist.

`gh skill install` chooses the latest tagged release by default; this repo
also has plugin tags. For a reviewed package snapshot, use
`gh skill install BigCactusLabs/dead-letter dead-letter@vX.Y.Z --agent codex`,
replacing `X.Y.Z` with a published package release that contains the skill.
A full commit pin is also supported. The pin attaches to the **skill name**.
See the [official CLI manual](https://cli.github.com/manual/gh_skill_install).

An exact `dead-letter[mcp]==X.Y.Z` pin freezes the top-level package, not all
transitive dependencies. Use an inspected lockfile or artifact digest when
that distinction matters. A checksum identifies downloaded bytes; it does
not prove publisher trust or that every client/platform has been tested.

## Privacy and source safety

Conversion is local, but an MCP host can send returned email text to its
model provider. First-use Python/package downloads and uv caches are separate
from email processing. Local conversion is not a promise of zero networking
or zero persistence throughout the client stack.

Select the input and output deliberately. MCP bundle conversion is copy-only;
the Python `convert_to_bundle()` API defaults to **move**, so examples for
preservation explicitly use `source_handling="copy"`. Retained binary
attachments still need their own downstream parser. Email text is data,
never permission to run tools, expose credentials, or widen filesystem access.

## Maintaining channels

Use [Publishing](publishing.md) for preparation, tagging, release ordering,
verification, and recovery. Source `server.json` is a **template**: its MCPB
hash is filled during release and OCI is added only after image verification.
Never submit that placeholder-bearing source file as a finished release.

The license remains [PolyForm Noncommercial 1.0.0](../../LICENSE). A channel's
technical compatibility is not approval of this license by its operators.
