# Publishing

The maintainer runbook for package releases, Claude plugin releases, and
channel recovery. For user-facing installation choices, use the
[distribution map](distribution.md).

## Policy

Release preparation is reviewable source work. Tagging, GitHub release
publication, marketplace updates, and Homebrew changes are separate,
intentional maintainer actions. An agent doing development or a docs audit
must not infer permission to perform them.

| Event | Effect |
| --- | --- |
| Push `vX.Y.Z` | Creates a package source tag; does **not** itself publish to PyPI |
| Publish a stable GitHub release for `vX.Y.Z` | Read-only source preflight → build once → packaged-install/README gate → upload those exact bytes to PyPI → install readiness → MCPB and OCI → resolved MCP Registry metadata |
| Push `plugin-vX.Y.Z` | Plugin checks → exact PyPI pin available → marketplace PR/merge → compatibility `release` branch |
| Publish a plugin GitHub release or a prerelease | Package publication jobs are skipped |
| Edit the Homebrew tap | Separate maintainer-owned core-only formula update |

Only stable `X.Y.Z` package releases are supported by the publication gate.
Do not sneak a prerelease, build suffix, or plugin tag into that channel. Add
a separately reviewed prerelease policy before expanding it. Plugin asset
versions are independent of package versions; see [the model below](#plugin-versioning-model).

PyPI and the MCP Registry use job-scoped OIDC. The existing `release` GitHub
environment remains the PyPI boundary. PR checks never receive publication
credentials. The registry publisher is pinned to a reviewed version and
archive checksum in the workflow; upgrade both together, not `latest`.

Do not enable a HEAD-tracking community marketplace while merging unreleased
plugin assets to `main`. The previous review of Anthropic's community updater
found a HEAD-tracking blocker. Recheck its current policy before submission;
a separate catalog's automation must not bypass this repo's release-only
contract. No catalog enrollment or license change is implied by this runbook.

## Prepare The Release

Start from a reviewed working branch. Choose the next package version
explicitly; the helper does not choose a version, create tags, or publish a
package. Python 3.12+ is sufficient for the offline commands:

```bash
python scripts/release.py check

VERSION=X.Y.Z # replace with the intended next stable package version
python scripts/release.py prepare "$VERSION" > /tmp/dead-letter-release.patch
git apply --check /tmp/dead-letter-release.patch
# Review the patch before this deliberate source edit:
git apply /tmp/dead-letter-release.patch
uv lock --check
```

`prepare` prints a patch and changes **no files**. `git apply` applies it only
after review. It updates the project and import versions, the editable root
lock record, PyPI/MCPB registry metadata, bundle metadata and exact dependency,
ARD entries, and (by default) the plugin asset version and exact runtime pin.
Dependency versions and checksums in `uv.lock` are not refreshed by this
operation. Dependency changes require their own reviewed lock update.

Use `prepare "$VERSION" --keep-plugin` to defer plugin adoption explicitly,
or `--plugin-version A.B.C` when the plugin's independent sequence is ahead.
The helper rejects version reuse/rollback. A deliberately different exact
plugin pin is a warning, not a package-release failure; record the deferral.
For a plugin-only release, change its manifest version and, only when needed,
its exact `.mcp.json` package pin; do not bump every package artifact.

Finalize a dated `## [X.Y.Z] - YYYY-MM-DD` entry in `CHANGELOG.md`. Do not
fabricate release notes from a version bump. Keep the PyPI ownership marker
in README. The source `server.json` retains a zero MCPB hash and no OCI entry:
those values are **build-time placeholders**, not publishable metadata.

Run the source and packaged-install checks before merging:

```bash
python scripts/release.py check --tag "v$VERSION"
uv sync --extra dev --locked
python scripts/verify.py full
python scripts/verify.py packaging
```

The [verification guide](verification.md) defines the modes and JSON outcomes;
[Contributing](../../CONTRIBUTING.md#test-commands) retains individual commands.
`full` includes Python/frontend suites, frontend syntax, plugin schema, Agent
Skill dry run, and offline release metadata. `packaging` checks the distributions
independently of the editable development environment.

`gh skill publish --dry-run` requires a CLI version with skill support and
publishes nothing. CI also runs cross-platform local MCPB smoke tests and,
for container-related paths, native amd64/arm64 container checks. The docs
link check and the fast `release-check` metadata job must pass. Neither CLI
validation nor a stdio smoke test proves a GUI client's installation flow.

Merge the reviewed preparation PR, wait for its checks, and record the exact
main-branch commit to release. Do not replace that recorded SHA with whatever
`main` happens to point at later.

## Packaged-artifact gate

Source tests are necessary but cannot prove that a wheel contains its static
resources or installs without development dependencies. Release jobs therefore
form a strict chain: `preflight` → `build` → `test-package` → `publish`.

`build` creates one wheel and one sdist with `uv build`, validates their embedded
name/version/Markdown README metadata, and records SHA-256 checksums outside
the distribution directory. Both files and `SHA256SUMS` travel together in the
run-scoped `python-package-<commit>` Actions artifact. The release summary
records the checksums even when a downstream job fails. Preserve this evidence
in the completion ledger before the artifact's 30-day retention expires.

`test-package` downloads that artifact and calls `verify.py packaging` with
`--dist-dir` and `--checksums`; those arguments prohibit rebuilding. It runs
pinned `twine==7.0.0` with `check --strict` on the actual distribution metadata,
then installs the wheel core and each documented extra in separate venvs,
plus the sdist core in another clean venv. Probes run outside the checkout
with isolated Python and verify import origin/direct-artifact provenance,
CLI conversion, MCP tool discovery/conversion, UI assets, extra imports, and
source preservation. Full details and the local command are in [Verification](verification.md).

`publish` has no checkout, dependency setup, or build step. It downloads the
same artifact, verifies its checksums again, then passes only the distribution
directory to the PyPI publisher under the existing environment/OIDC boundary.
It cannot silently build different bytes after the tests.

Use **rerun failed jobs**, retaining the successful build artifact. Do not
rerun a successful build or overwrite its artifact name to recover a downstream
failure. Missing/expired evidence requires an explicit maintainer recovery,
not an untested rebuild labeled as the original package. MCPB and OCI retain
their separate post-PyPI build/smoke contracts; this gate does not claim those
channels directly embed the tested wheel or constitute fresh GUI tests.

## Publish To PyPI

From the recorded, reviewed release checkout, replace the placeholders below:

```bash
VERSION=X.Y.Z
RELEASE_SHA=FULL_40_CHARACTER_REVIEWED_MAIN_COMMIT

git fetch origin main --tags
git switch --detach "$RELEASE_SHA"
python scripts/release.py check --tag "v$VERSION"
git merge-base --is-ancestor HEAD origin/main
# No local changes may be left in the release checkout.
test -z "$(git status --porcelain)"
git tag -a "v$VERSION" "$RELEASE_SHA" -m "dead-letter $VERSION"
git push origin "refs/tags/v$VERSION"
gh release create "v$VERSION" --verify-tag --latest \
  --notes-file /absolute/path/outside-the-repo/release-notes.md
```

Do not reuse or move an existing release tag. The workflow checks the tag
namespace, every version relationship, dated changelog, tag-to-commit
identity, main ancestry, source tests, and the packaged-artifact gate **before**
PyPI publication. Publishing a GitHub release does not mean every downstream
channel has succeeded: inspect the workflow's channel summary and individual
job steps. `python scripts/release.py status --version X.Y.Z` is the read-only
way to check every channel after a release; PyPI is only reported verified when
the run's original `SHA256SUMS` is supplied with `--checksums`. See
[Release Operations](release-operations.md) for details.

The package upload includes PyPI attestations. Confirm the intended sdist and
wheel are available, then exercise the published version with synthetic mail.
A successful upload can precede availability on the simple index used by
installers. The separate, read-only `pypi-ready` job uses the same bounded
helper as plugin publication, checking the JSON API, unyanked distribution
availability, and simple index before MCPB, container, or registry work starts:

```bash
python scripts/release.py wait-pypi "$VERSION"
```

If `publish` succeeds but `pypi-ready` fails, rerun **failed jobs**. The
readiness retry does not re-upload the successful package. The readiness gate
also protects the container's comparison against the exact PyPI tool schemas;
it must not be removed just because a local container build needs no PyPI
release. Avoid duplicating readiness checks in separate shell loops.

Use a maintainer-authorized CLI/session for the release event. A release
created by another workflow's default `GITHUB_TOKEN` generally does not start
a new release-triggered workflow; do not add an unattended tag/release bot
without designing its authorization and event chain.

## MCP Registry Publish (Automatic)

The registry job waits for successful PyPI readiness, MCPB, and OCI jobs. It
stamps the bundle's actual SHA-256 and URL, adds the tested OCI index digest,
archives `dead-letter-server-X.Y.Z.json` plus its checksum, then publishes
using GitHub OIDC. The archived manifest is the exact input to the publisher,
not proof that registry publication succeeded; verify the registry's exact version.

The committed `server.json` is a source template. **Never manually publish it
with a zero bundle hash or invent an OCI digest.** The resolved manifest
assets are produced only by releases using this refreshed workflow; do not
expect them on older releases such as `0.3.1`.

For a registry-only recovery, prefer rerunning the failed registry job with
its successful upstream outputs. For a maintainer-authorized manual recovery,
download the matching archived manifest and sidecar into an isolated working
directory, verify the checksum, inspect the package version, bundle URL/hash,
and OCI digest against the successful run, and use that file as `server.json`
with the workflow's pinned/checksummed publisher. Do not overwrite an existing
registry version to hide a mismatch. For older releases without an archive,
recover the exact original artifact/digest evidence from the successful jobs;
absence of that evidence is not permission to reconstruct guessed metadata.

Ownership depends on the README/PyPI MCP marker and repository identity. The
initial publication was `0.2.4`; do not treat pre-marker releases as
backfillable simply because today's template is valid. Aggregators and
curated directories can lag or require separate review.

## MCPB Bundle (Automatic)

The source lives in `mcpb/`; `scripts/build_mcpb.py` validates matching versions
and packages the exact published Python dependency. `scripts/smoke_mcpb.py`
checks a real stdio session before upload. CI's `--local-source` bundles are
local test artifacts, not release downloads.

The release attaches the `.mcpb` and `.sha256` sidecar. On retry, a published
bundle is downloaded, checked against its sidecar, and smoke-tested again
instead of silently rebuilt and replaced. Asset upload compares existing
bytes first: identical assets are reused; different bytes fail closed.
Missing or invalid sidecars need explicit recovery of the original bytes.
Never repair a published registry hash with `--clobber`.

A CLI smoke test is distinct from opening the extension in a named Claude
Desktop version on macOS/Windows. Record fresh install, restart/update,
four-tool discovery, conversion, and source preservation separately. First
launch can download Python/dependencies; an exact direct dependency pin alone
does not freeze every transitive dependency.

## Container Image (Automatic)

The optional reusable [container workflow](../../.github/workflows/container.yml)
tests native Linux amd64/arm64, publishes a run-specific candidate, verifies
both platform child digests and PyPI tool-schema parity, proves anonymous
pull access, and only then promotes the tested index digest to `X.Y.Z`.
There is no `latest` alias. The registry advertises that digest, not a mutable
candidate name. See [Containers](containers.md) for mount safety, provenance,
platform tests, and Docker Catalog submission requirements.

A first GHCR package may need a maintainer to enable public visibility. That
step is recorded as complete for dead-letter's `0.3.1` image; the workflow
continues to verify anonymous access on each release. Do not infer Docker
Catalog acceptance from GHCR publication.

Rebuilding can change attestation/index digests even with unchanged source.
Rerun failed jobs, not successful publication jobs. Never overwrite a different
existing version digest to make a retry pass.

## Update The Homebrew Tap

Homebrew remains a manual, core-CLI-only channel after PyPI succeeds. In a
checkout of `BigCactusLabs/homebrew-tap` (installed as `BigCactusLabs/tap`),
update `Formula/dead-letter.rb` to the exact released sdist URL and SHA-256
from PyPI. Review its dependency resources; do not accidentally add the
UI/MCP extras or unrelated development packages.

`scripts/release.py homebrew-prepare` can generate the formula change and open
the draft tap PR, and it requires the original build's checksums. It never
merges: a maintainer still reviews the generated diff,
runs the manual `brew` install and test steps below, and records the tap commit
in the release checklist. See [Release Operations](release-operations.md) for
the full flag reference and the plan/write/open-pr phases.

Allow at least 24 hours after the target sdist's PyPI upload before running
`homebrew-prepare --write`. Homebrew's Python resolver excludes newer uploads;
its resource-updater fallback has the same delay. The helper's plan reports the
earliest preparation time in UTC, and `--write` refuses an earlier attempt with
that retry time before changing the tap. See
[Homebrew preparation](release-operations.md#prepare-a-homebrew-update) for the
age check and command-error diagnostics.

```bash
# In the tap checkout, after editing and reviewing the formula:
brew fetch --build-from-source dead-letter
brew style Formula/dead-letter.rb
brew install --build-from-source BigCactusLabs/tap/dead-letter
brew test BigCactusLabs/tap/dead-letter
dead-letter doctor
```

Use `brew reinstall --build-from-source` instead of `install` on a test machine
where the formula is already installed. Convert a synthetic fixture, then
commit/push the reviewed formula. Record the tap commit and version in the
release checklist. A successful Python release does not update the tap.

## Plugin Release

The normal plugin release adopts an already-published exact package version.
A plugin-only instruction/command update can keep the prior exact package
pin. Both cases require a new plugin asset version and reviewed main commit.
For a lockstep release, use the recorded package release commit, not a later
unreviewed `main` tip.

```bash
PLUGIN_VERSION=A.B.C
PLUGIN_SHA=FULL_40_CHARACTER_REVIEWED_MAIN_COMMIT

git switch --detach "$PLUGIN_SHA"
python scripts/release.py check --plugin-tag "plugin-v$PLUGIN_VERSION"
# Check the exact package version in plugin/.mcp.json before tagging:
MCP_VERSION=$(python scripts/release.py check | python -c 'import json,sys; print(json.load(sys.stdin)["plugin_pin"])')
python scripts/release.py wait-pypi "$MCP_VERSION"
git tag -a "plugin-v$PLUGIN_VERSION" "$PLUGIN_SHA" -m "dead-letter plugin $PLUGIN_VERSION"
git push origin "refs/tags/plugin-v$PLUGIN_VERSION"
```

The workflow serializes pointer changes, validates the tag and peeled commit,
and rejects a commit behind the compatibility `release` branch **before**
editing the marketplace. It checks the exact package pin on both PyPI JSON
and simple indexes, then updates marketplace `version`, `source.ref`, and
`source.sha` together through a PR. Finally it fast-forwards `release` without
force. Annotated tags use their commit SHA, not a tag-object SHA.

Do not queue a burst of plugin tags: GitHub concurrency is not a durable FIFO
queue. Wait for each pointer update and record the result. A concurrent manual
branch update can still cause the final non-forced push to stop safely.

`RELEASE_PAT` must be configured on dead-letter with access to both repositories:
Contents read/write and Workflows write for dead-letter; Contents and Pull
requests read/write for `bigcactuslabs-plugins`. Keep the existing repository
approval/protection model. Do not print, commit, or replace credentials as a
release shortcut. A future GitHub App migration is separate work.

After the marketplace update, use [the plugin update instructions](../../plugin/README.md)
and [manual tests](../../plugin/TESTING.md) in **both** Claude Code and Cowork.
They retain separate installed copies. Matching source/pins does not prove
matching client caches or successful runtime resolution.

## Plugin Versioning Model

| Field | Meaning | Relationship |
| --- | --- | --- |
| `pyproject.toml` / `vX.Y.Z` | Python package release | All package, MCPB, and ARD sync points match |
| `plugin/.claude-plugin/plugin.json` / `plugin-vA.B.C` | Plugin assets and instructions | Independent, increasing sequence |
| `plugin/.mcp.json` | Exact package the plugin launches | Must be published; may deliberately lag |
| Marketplace `version`, `source.ref`, `source.sha` | What plugin clients resolve | Same plugin release and peeled commit |
| Community catalog source SHA | Third-party accepted snapshot | Separate review and update policy |

Do not solve version drift by forcing these independent concepts to be equal.
Do not leave a plugin pin floating. Document adoption or deferral at release.

## Release completion and recovery

Keep one ledger in the release notes/issue, not duplicate version claims in
several docs:

| Check | Record |
| --- | --- |
| Source | Package tag, exact commit, CI run, dated changelog |
| Python build | Wheel/sdist filenames and SHA-256, build artifact/run, packaged-install and README outcomes |
| PyPI | Version, sdist/wheel availability on both indexes, attestation, fixture result |
| MCPB | Asset/checksum, stdio smoke; GUI client/OS results separately |
| OCI | Index digest, both platforms, anonymous pull, provenance |
| Registry | Resolved manifest checksum and actual published version |
| Plugin | Asset tag/commit, exact package pin, marketplace PR; or explicit deferral |
| Homebrew | Formula version, tap commit, test result; or explicit deferral |
| Discovery | Submission/listing URL and status, tested client/version; no inferred acceptance |

For partial failure, first identify the last successful external write. A red
job is not proof that nothing was published. Retry a failed downstream job
with the existing artifacts; do not rerun PyPI blindly, move tags, rewrite
checksums, or replace OCI digests. If the marketplace merged but the final
branch push failed, inspect both pointers and ancestry before retrying. A
new forward plugin version can select an older known-good exact package pin;
that is preferable to silently rewinding release history. Missing evidence or
conflicting published bytes calls for an explicit maintainer recovery/new
release, not automatic deletion.

## Do Not Automate The Tap Yet

Do not extend package publication to modify the tap in this pass. Cross-repo
credentials, tests, approval, and rollback need their own design. Keep the
manual core-only update visible in the completion ledger.

## Primary references

Reviewed September 19, 2026:
[GitHub workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows),
[workflow triggering and token behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow),
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/),
[PyPA separated build/publish jobs](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/),
[Twine changelog](https://twine.readthedocs.io/en/stable/changelog.html),
[MCP Registry GitHub Actions](https://modelcontextprotocol.io/registry/github-actions),
[gh skill pinning](https://cli.github.com/manual/gh_skill_install), and
[uv locking](https://docs.astral.sh/uv/concepts/projects/sync/).
