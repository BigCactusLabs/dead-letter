# Discovery without regular social posting

Research date: **2026-11-05**. Repository baseline:
[`4ff528a`](https://github.com/BigCactusLabs/dead-letter/commit/4ff528a55c8886d977eef32913e3a56890553c79).
Status: **proposed distribution experiments**, with dated observations below.

## Recommendation

Prioritize a complete **Google Takeout to Obsidian archive** workflow: one
searchable public page, one inspectable synthetic sample vault, and one tested
path through the released package. Then test a small number of placements in
existing tool documentation, a preservation registry, and a Python publication.
Evaluate one optional MarkItDown integration after checking its audience and
licensing fit.

These are assets and contributions that can remain useful between releases.
The plan does not depend on a recurring social-posting schedule. The working
hypothesis is that development has outpaced the routes from a specific email
problem to discovery, successful use, and a visit to the repository. Traffic
and user evidence are needed to test that hypothesis.

This document records research and proposed work. It does not establish a new
site, implemented integration, submitted pitch, accepted listing, licensing
change, or growth result. The [reach plan](reach.md) owns current priorities;
the [distribution ledger](../reference/mcp-distribution.md) owns catalog state;
the [distribution map](../reference/distribution.md) and
[publishing guide](../reference/publishing.md) own install and release details.

## Observed baseline

| Observation on November 5 | Implication and limit |
| --- | --- |
| The [repository][repo] has 8 stars. [PyPI Stats][pypi-stats] reports 1,305 downloads in the last month. | Downloads, successful use, and stars are different events. The [PyPI Stats FAQ][pypi-faq] explicitly includes CI/CD; repeat installs, our own builds, and automated checks are not separated here. This is not a count of users or evidence of retention. |
| The [distribution map](../reference/distribution.md) covers CLI/Python, UI, Homebrew, MCP, MCPB, OCI, the Claude plugin, and the portable skill. | More installation formats are not the first experiment. Actual catalog acceptance and named-client tests remain separate evidence. |
| [Five recipes](../recipes/README.md) and a [27-second released-package demo](../recipes/demo/README.md) already exist. | Reuse them. Do not reopen the completed demo work as a prerequisite. The current recipe fixtures require a repository checkout. |
| The repository homepage points to PyPI, its About description leads with EML, and GitHub Pages is disabled. | A task-oriented public site is not linked from this repository. This observation does not prove that no other project website exists. |
| The released [UI HTML][released-ui] and the baseline frontend HTML/JavaScript have no project, documentation, or GitHub return link. | Someone can successfully use the UI without visiting the repository. This is a concrete gap to test, not evidence that adding a link will produce stars. |
| [MCP Find][mcp-find] generates `uvx dead-letter` as an MCP launch command. [mcpcost][mcpcost] reports `needs args`, with a CLI error containing `invalid choice: '3.12'`. | Public discovery routes can present a failing invocation. These observations do not identify the root cause or demonstrate that the documented MCP server is broken. |

The [November 4 channel refresh](2026-11-04-distribution-refresh.md) remains the
detailed catalog baseline. Glama ownership was already resolved; do not infer
that it needs claiming from a stale search snapshot. Existing owner holds in
the distribution ledger, including Agent Finder and mcpservers.org, remain in
effect. An indexed result or a prepared entry is not an accepted submission.

### Released support versus pending work

At the research date, the published package is 0.4.10. Plain MBOX, compressed
Takeout CLI/Python input, bounded MCP MBOX conversion, and opt-in flat-archive
resume are already released. Local-path MBOX web/API jobs and Windows worker
CPU/memory budgets shipped in [0.4.10][release]. Browser archive upload,
compressed web imports, and durable web-job resume are newer source work and
are still unreleased. The [Takeout guide](../reference/gmail-takeout.md) owns
the current availability matrix.

Real multi-GB Takeout acceptance remains tracked by
[#138](https://github.com/BigCactusLabs/dead-letter/issues/138). A shipped
importer does not establish successful whole-account migration at every size.

## 1. Publish task pages from the existing documentation

Searchable destinations should describe the job a person recognizes before
they know the package name, MIME terminology, or MCP. Start with Takeout to
Obsidian; add other pages only when each has a distinct, tested workflow.

| Task page | Useful result | Existing foundation |
| --- | --- | --- |
| Google Takeout to Markdown | An export-to-output walkthrough with explicit label, attachment, and source behavior | [Takeout guide](../reference/gmail-takeout.md), [resume contract](../reference/mbox-resume.md) |
| Import a Gmail archive into Obsidian | An inspectable sample vault and a repeatable path for a user's own archive | [Folder recipe](../recipes/folder-to-markdown.md), [Cabinet recipe](../recipes/durable-cabinet.md) |
| Convert Thunderbird email to Markdown | A tested export-and-convert sequence with folder and source behavior | EML/MBOX conversion and the upstream export workflow below |
| Prepare email for local RAG | A working handoff for text, provenance, and separately retained binaries | [RAG preprocessing](../recipes/rag-preprocessing.md), [quality diagnostics](../reference/quality-diagnostics.md) |

Dedicated task pages already exist for [inktomd][inktomd] and
[mdgate][mdgate]; mdgate also exposes a local browser conversion experience.
This establishes competing presentation patterns, not their traffic, output
quality, or conversion rates. Dead-letter's archive workflow is the proposed
starting point; privacy alone is not unique to this project.

### First deliverable

Publish accessible HTML from maintained documentation with stable URLs,
clear page titles, ordinary internal navigation, and a sitemap. Reuse the
existing demo and link to canonical installation instructions. Point the
repository homepage and package documentation link at the resulting site;
reconcile the About description and relevant topics with released MBOX
capabilities. These are follow-up changes, not changes made by this plan.

Keep the source of each explanation in one place. A site should not create a
second manually maintained copy of installation contracts. The existing
owned-domain discovery work in
[#159](https://github.com/BigCactusLabs/dead-letter/issues/159) identifies
`bigcactuslabs.xyz` as the selected host; deployment and retrieval are not
established by a manifest stored in GitHub.

Package a small versioned sample containing synthetic EML and MBOX inputs,
expected Markdown/Cabinet output, a conversion report, and short reproduction
instructions. A downloadable finished vault lets someone examine the outcome
before installing. Generate it from the released package and record the
version and command. Reuse existing fixtures where possible.

### Completion evidence

- The public page loads without authentication and explains one complete task.
- The downloadable sample contains no private mail and is clearly synthetic.
- A fresh run of the documented release reproduces the expected output and
  leaves the input archive intact. Preservation examples that use Python
  bundles explicitly select `source_handling="copy"`.
- The sample opens in the named destination. Check rendered content, properties,
  and any advertised attachment links; a directory of Markdown alone does not
  establish a polished Obsidian import.
- Explain actual label semantics: `gmail_labels` is a preserved string, not
  automatically inferred Obsidian tags. Ordinary reruns do not imply
  deduplication, and separate archive records are not automatically grouped
  into conversations. Link to the Takeout contract for current details.
- The page distinguishes attachment filenames from extracted attachment bytes
  and from searchable attachment text. Demonstrated scale and release status
  match the evidence.
- Record indexing and inbound use when observed. Publishing a sitemap is not
  proof of indexing, ranking, or demand.

[Google's search guidance][google-search] supports clear crawlable content and
ordinary SEO foundations. It says special AI text files and markup are not
required for Google's generative search features. That statement does not
describe every assistant, and an agent install guide still serves its own
configuration purpose.

## 2. Contribute a complete workflow to an adjacent tool

### Thunderbird / ImportExportTools NG

[ImportExportTools NG][ietng] exports folders and subfolders as MBOX and
messages as EML and other formats. Markdown is absent from its documented
export list. A tested Thunderbird export to dead-letter to Markdown guide is
a candidate for its documentation or wiki.

First validate the actual export dialect, folder behavior, attachments, and
output on a small synthetic collection. The contribution should supply exact
steps and expected results. Upstream maintainers decide whether an external
tool workflow belongs in their documentation; a useful guide is not a
guaranteed placement. Keep this an external-tool pipeline rather than copying
or bundling differently licensed code into the upstream extension.

### Obsidian import documentation

The official [Obsidian Importer][obsidian-importer] currently omits email/MBOX
from its documented supported formats. It welcomes contributions to import
guides and new formats. A verified Takeout-to-vault workflow is a candidate
for an appropriate documentation contribution.

Individual-email importing already has competition: [Postbox][postbox]
advertises EML/MSG imports, attachments, frontmatter, watched folders, and
duplicate handling. Its advertised features do not include MBOX. This makes
bulk archive migration a more specific hypothesis than generic email import;
an omitted feature is not proof that every competing implementation lacks it.

A native plugin is a later option. Official Importer contributions must be
TypeScript and should keep dependencies light; its
[contribution guide][obsidian-contributing] also addresses mobile compatibility.
The Python engine is not a drop-in format contribution. A separate desktop
companion would introduce helper installation, versioning, and support costs.
Build it only if observed use shows that invoking the existing converter is
the remaining obstacle.

**Completion evidence:** a tested workflow, a suitable upstream contribution,
and a recorded accepted URL or rejection/defer decision. Maintained referral
placement and successful user imports matter more than a submitted PR alone.

## 3. Evaluate one optional converter integration

### Preferred pilot: MarkItDown

[MarkItDown][markitdown] explicitly supports independently published plugins,
directs users to the `markitdown-plugin` GitHub topic, and provides a
[sample plugin][markitdown-sample]. Its contribution guidance recommends
plugins for many new formats, particularly when they add dependencies.

[Issue #1662][markitdown-email] describes inbox exports and attachment-bearing
emails for which body-only extraction is insufficient. This is specific
problem evidence, not a market-size estimate or a claim that dead-letter
already solves the requested recursive attachment-content conversion.

Propose a small separately installable adapter around the existing API. Show
why someone would choose its thread handling, message metadata, cleanup, and
explicit attachment behavior. Keep binary extraction and downstream content
parsing distinct. Avoid a new conversion engine or a broad framework campaign.

Plugins require explicit installation and activation. A topic tag does not
put the adapter in the default MarkItDown installation or establish Microsoft
endorsement. Check overlapping email plugins and current upstream behavior
before implementation.

**Completion evidence:** a runnable installed adapter, a representative
synthetic input/output example, a supported dependency range, explicit license
information, and an actual discovery URL. Continue based on user requests or
downstream use, not merely publication.

### Alternative: Haystack

[Haystack's integration catalog][haystack-integrations] documents a PR route
for community-maintained integrations, including data ingestion and custom
components. A small converter returning documents with stable email/source
metadata could be considered. Catalog eligibility and downstream permission
to use dead-letter are separate questions; the published process is not
unconditional acceptance of this license.

Choose MarkItDown or Haystack for the first experiment. A collection of
unmaintained adapters would add support obligations without demonstrating a
working discovery channel.

## 4. Reach preservation practitioners through COPTR

[COPTR][coptr-about] is a registry for discovering digital-preservation tools
and workflows. Its scope includes migration, access, rendering, and search,
and explicitly spans open-source through commercial software. It already
carries email-conversion tools such as [Emailchemy][emailchemy]. The current
license is therefore not an obvious category-level exclusion.

The proposed entry describes readable Markdown **access and analysis
derivatives**, with original email archives retained for recovery. Link to a
sample containing the original archive, output, retained attachments, source
references, and a conversion report. Cleaned Markdown is not an archival
preservation master or a guarantee of evidentiary fidelity.

COPTR's [contribution guidance][coptr-add] allows factual tool entries. Its
About page currently requires an account request because of spam. This is a
one-time submission path with later maintenance; a listing is not an
endorsement or evidence of traffic.

**Completion evidence:** confirm current account/submission instructions,
prepare accurate format/function/license fields, and record the actual entry
URL and any workflow feedback. No account request or submission was made in
this research. Some COPTR pages returned HTTP 503 during direct retrieval;
their indexed primary guidance was used. Recheck availability before acting.

## 5. Make a small number of editorial submissions

| Outlet | Verified route | Proposed angle |
| --- | --- | --- |
| [PyCoder's Weekly][pycoders] | Project and article links can be submitted for editorial consideration; inclusion is not guaranteed | A Python library/CLI for difficult email exports, with a reproducible example |
| [Console][console] | Accepts tool suggestions and publishes selection criteria; it does not sell sponsored reviews | Local email normalization as a useful step in a developer ingestion workflow |

Try PyCoder's first because its Python-project remit is direct. Console's
criteria emphasize tools used in developers' regular workflows, so that use
case needs evidence. Submit a concrete task page with the existing demo,
runnable sample, installation route, and accurate license/support boundaries.
Neither route requires maintaining a recurring social feed.

**Completion evidence:** record a submission and its outcome separately. An
editorial feature provides an archived reference only if it is actually
published. Do not predict audience size, visitors, or stars from the existence
of a submission form. No pitches were sent in this research.

## 6. Repair missing steps in existing discovery and use

### Return links in the local UI

Add a small persistent project/help area with documentation, GitHub, issue
reporting, and version information. Consider a restrained project link after
successful conversion. Keep the links in the application interface; do not
add attribution watermarks to converted email or interrupt the workflow.

**Completion evidence:** links resolve from an installed release, appear at an
appropriate point in the UI, and do not affect the conversion contract. This
can help a successful user find the repository, but does not prove why the
star count has stalled.

### Generated MCP launch instructions

Reproduce the exact configurations shown by [MCP Find][mcp-find] and
[mcpcost][mcpcost], then compare them with the
[canonical install guide](../../llms-install.md). The documented MCP
entrypoint is `dead-letter-mcp`; `uvx dead-letter` invokes the ordinary CLI.
Check published registry metadata and the downstream generator separately.

Record the listing URL, displayed version, complete generated command, actual
process result, and a known-good synthetic conversion using the documented
configuration. Correct the responsible layer once the mismatch is understood.
Do not change runtime behavior to match an erroneous listing or treat a
scanner result as a reproduced product failure.

The earlier [Glama correction #149][glama-issue] addressed a different
generated-contract problem and is completed. Neither MCP Find nor mcpcost
was tracked in the inspected ledger/open-issue titles. A bounded search does
not prove that no historical discussion exists.

**Completion evidence:** a corrected listing/configuration and successful
initialization plus fixture conversion through that exact route. Updating
source metadata alone is not proof that a downstream catalog refreshed.

## License and audience fit

Retain [PolyForm Noncommercial 1.0.0](../../LICENSE). Its terms permit
noncommercial purposes and expressly cover the organizations enumerated in
the license, including educational and government institutions. Personal email
archives, eligible research, and preservation workflows are coherent initial
audiences. Commercial use requires the separate permission described by the
project; a permissively licensed wrapper does not expand the engine's license.

Licensing may add adoption friction for commercial ingestion dependencies.
That is an inference, not a measured explanation for eight stars. If broad
commercial dependency adoption becomes the primary goal, evaluate licensing
as a separate strategy decision. Do not change it for a star target or assume
every open-source-only catalog accepts this project.

## Execution order and decisions

All work below is proposed. Existing acceptance work such as #138 remains
separate; publication of this plan does not complete those issues.

| Priority | Deliverable | Evidence for the next decision |
| --- | --- | --- |
| 1 | Public Takeout-to-Obsidian page and finished sample vault | Reproducible released workflow, verified destination behavior, indexing/inbound use when observed |
| 2 | UI project links and diagnosis of generated MCP commands | Installed UI links; exact listing-derived command tested and corrected |
| 3 | COPTR entry and one PyCoder's submission | Actual published URLs or explicit outcomes; subsequent referrals/use |
| 4 | Thunderbird export guide and appropriate documentation contribution | Verified export/import path and upstream acceptance or feedback |
| 5 | One MarkItDown or Haystack pilot | License/audience fit, installed example, discovery placement, downstream use |
| 6 | Native Obsidian integration, conditionally | Users identify CLI/helper friction that an integration would materially resolve |

Before increasing implementation scope, ask what evidence the smaller
experiment supplied. A page or adapter receiving no useful traffic or usage
evidence should prompt examination of its placement and fit rather than
automatic expansion to more integrations. Search indexing and editorial
review have uncertain timing; a proposed four-week review after an experiment
goes live is a learning checkpoint, not a guaranteed growth deadline.

### Measurement without private-email telemetry

Capture a baseline of aggregate repository visits/referrers and clones,
task-page search impressions, sample downloads, and inbound workflow requests
where available. [GitHub traffic][github-traffic] retains a rolling 14-day
window; its referrer view excludes search engines and GitHub itself. Search
Console can complement it for an owned site.

Keep a small experiment record: hypothesis, artifact URL, baseline/date,
submitted/accepted/live state, observed outcome, and next decision. Stars are
one outcome; package downloads and clones are not unique people, and cannot
establish retention or attribution. No repository traffic baseline or
successful-user count was collected for this research. Do not introduce
email-content telemetry or infer private usage from public download counts.

## External sources

Sources were reviewed for the November 5 research. Repository-local contracts
above are authoritative for current dead-letter behavior; external pages and
their submission rules can change. COPTR retrieval limits are recorded in its
section. The cited upstream issues show specific problems, not market size.

[repo]: https://github.com/BigCactusLabs/dead-letter
[pypi-stats]: https://pypistats.org/packages/dead-letter
[pypi-faq]: https://pypistats.org/faqs
[release]: https://github.com/BigCactusLabs/dead-letter/releases/tag/v0.4.10
[released-ui]: https://github.com/BigCactusLabs/dead-letter/blob/v0.4.10/src/dead_letter/frontend/index.html
[mcp-find]: https://www.mcpfind.org/servers/io-github-bigcactuslabs-dead-letter
[mcpcost]: https://mcpcost.ulehla.dev/s/io.github.BigCactusLabs/dead-letter
[inktomd]: https://www.inktomd.com/email-to-markdown
[mdgate]: https://convert.mdgate.dev/eml-to-markdown
[google-search]: https://developers.google.com/search/docs/fundamentals/ai-optimization-guide
[ietng]: https://github.com/thunderbird/import-export-tools-ng
[obsidian-importer]: https://github.com/obsidianmd/obsidian-importer
[postbox]: https://community.obsidian.md/plugins/postbox
[obsidian-contributing]: https://github.com/obsidianmd/obsidian-importer/blob/master/CONTRIBUTING.md
[markitdown]: https://github.com/microsoft/markitdown
[markitdown-sample]: https://github.com/microsoft/markitdown/blob/main/packages/markitdown-sample-plugin/README.md
[markitdown-email]: https://github.com/microsoft/markitdown/issues/1662
[haystack-integrations]: https://github.com/deepset-ai/haystack-integrations
[coptr-about]: https://coptr.digipres.org/index.php/About_COPTR
[coptr-add]: https://coptr.digipres.org/index.php/Add_a_tool_to_COPTR
[emailchemy]: https://coptr.digipres.org/Emailchemy
[pycoders]: https://pycoders.com/submissions
[console]: https://console.dev/selection-criteria
[glama-issue]: https://github.com/BigCactusLabs/dead-letter/issues/149
[github-traffic]: https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-traffic-to-a-repository
