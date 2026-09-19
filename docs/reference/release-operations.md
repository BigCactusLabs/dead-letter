# Release status and reviewed Homebrew preparation

Companion to [Publishing](publishing.md). These commands operate on an explicitly
selected stable package version, not the checkout's current version. They never
select a new version, create a package/plugin tag, publish a package, or merge a
PR. Source availability is not evidence that these helpers are in a published
package; run them from the reviewed source checkout.

## Reconcile what actually shipped

```bash
python scripts/release.py status --version X.Y.Z
python scripts/release.py status --version X.Y.Z --json \
  --checksums /absolute/path/to/original-build/SHA256SUMS \
  --oci-digest sha256:REPLACE_WITH_RECORDED_64_CHARACTER_INDEX_DIGEST
```

`--checksums` is the original wheel/sdist manifest produced by the build-once
release gate, recovered from its Actions artifact or retained release ledger.
It must contain the target version's wheel and sdist with their original SHA-256
values. **Do not regenerate it from the current checkout or from PyPI**: comparing
PyPI to itself would not establish that the tested build was published.

Without that evidence, available PyPI files and a matching tap cannot become
`verified`. Older releases, including ones that predate the build-once gate,
may never have had the new evidence assets. Their absence is reported, not
silently reconstructed or treated as proof of a broken original release.

`--oci-digest` accepts the index digest recorded by the release workflow. When
omitted, status can use the OCI digest from the release's downloaded,
checksummed resolved server manifest. It never uses the current GHCR tag as
its own expected value. A supplied ledger digest that disagrees with the
archived manifest is a conflict.

| Channel | Evidence checked |
| --- | --- |
| PyPI | Exact version, complete wheel/sdist pair, unyanked state, advertised SHA-256 versus original build evidence |
| GitHub release | Stable published release; MCPB and `dead-letter-server-X.Y.Z.json` plus both sidecars; actual downloaded bytes versus sidecars; manifest identity, runtime pin, bundle URL/hash |
| GHCR | Anonymous pull-token/index GET; manifest bytes versus digest header; recorded index digest; amd64 and arm64 platform entries |
| MCP Registry | Exact version endpoint, active record, complete package records versus the checksummed archived manifest; never `latest` |
| Plugin marketplace | Snapshot of marketplace `main`, plugin manifest, exact runtime pin, peeled plugin tag, and compatibility `release` branch; runtime package exists on PyPI |
| Homebrew | Snapshot of tap `main`, literal core-only formula metadata, sdist URL/hash versus PyPI and original build evidence |

Status is a metadata and checksum reconciliation, **not** a fresh installation,
GUI test, image-layer pull, or replay of release CI. Sidecars establish internal
consistency, not independent cryptographic attestation of the publisher.

### Results and recovery

Each channel includes `status`, `detail`, `next_action`, and bounded selected
`evidence`. JSON also includes `schema_version: 1`, target `version`, UTC
`checked_at`, counts, and `exit_code`. The exit code is 0 only when every
channel is `verified` or `deferred`; `missing`, `conflicting`, and
`unable-to-verify` exit 1. An `unable-to-verify` channel records the failure
class and message under `evidence` so a parser defect is distinguishable from
an endpoint outage. GitHub returns HTTP 404 for resources the caller cannot
access, so `missing` on a GitHub-hosted channel can also mean lost access.

| Status | Meaning |
| --- | --- |
| `verified` | All checks for this channel's documented scope passed |
| `missing` | Exact resource returned 404, or an authoritative inventory omitted a required file/entry |
| `deferred` | A valid independently adopted plugin runtime or manual tap is on another package version |
| `conflicting` | Evidence contradicts the expected identity, hash, pin, or pointer relationship |
| `unable-to-verify` | Network/authentication failure, malformed evidence, unsupported layout, or required original evidence unavailable |

Exit **0** means all six channels verified; **1** means something needs attention,
including intentional deferrals. Invalid argument syntax exits 2; invalid local
evidence is rejected before network access and uses the release helper's error
exit 1. Do not discard the exit code when capturing JSON.

Independent plugin asset numbering is not a conflict. A different *existing*
exact runtime pin is deferred; a nonexistent or entirely yanked runtime is a
conflict. Pointer/hash disagreement remains a conflict even when adoption is
independent. A stale tap with a mismatching sdist is not hidden as deferred.

PyPI partial releases whose earliest available upload is older than 14 days get
**needs new version**, not retry guidance. Missing/unparseable timestamps do not
produce an invented upload window. PyPI currently has no definitive closed-state
API: a younger release is not a promise that another upload will be accepted.

GitHub immutable releases with missing assets are not repairable by appending
assets. Do not enable immutability on the present publish-then-attach pipeline;
see the [sequence redesign](../project/immutable-release-sequencing.md).

### Network and authority boundary

The status path uses bounded HTTPS GETs to fixed provider/repository identities,
with a 15-second socket timeout and 64 MiB response ceiling. There is no automatic
retry, repair, disk evidence cache, or shell command. Repeated channel reads reuse
in-memory results/errors for this invocation. Asset redirects are HTTPS and
host-allowlisted. Authenticated requests cannot redirect.

Optional `GH_TOKEN`/`GITHUB_TOKEN` is sent only to `api.github.com`; it need only
read these public repositories. It is never forwarded to PyPI, release asset
hosts, or the MCP Registry. GHCR uses its own anonymous pull-only token. Tokens
and raw error response bodies are not included in reports. A 401, 403, 429,
timeout, DNS failure, or invalid JSON is never inferred to mean missing.

## Prepare a Homebrew update

The tap remains **manual, core-only, and Apple-silicon-specific**. Preparation is
not publication. Existing Python, architecture, installer, and test logic must
survive resource regeneration. A valid new release and original build checksums
are prerequisites; this helper does not backfill evidence for historical builds.

### 1. Inspect the plan, with no local or remote writes

```bash
VERSION=X.Y.Z
EVIDENCE=/absolute/path/to/original-build/SHA256SUMS
python scripts/release.py homebrew-prepare \
  --version "$VERSION" --checksums "$EVIDENCE"
```

The plan uses PyPI's exact released sdist URL/hash only after cross-checking the
complete published wheel/sdist pair against the original build. It prints argv
arrays for `brew bump-formula-pr --write-only --python-package-name=dead-letter`
and the `brew update-python-resources --package-name=dead-letter` fallback.
It never passes `--commit`, extras, or an ignore-errors option to Homebrew.

### 2. Generate the formula diff on a native Apple-silicon Mac

Use the installed tap checkout and a dedicated, clean local preparation branch.
No staged, unstaged, or untracked work may be present. The tap's declared Python
must already be installed; preparation checks its native architecture/version.

```bash
TAP=$(brew --repo BigCactusLabs/tap)
git -C "$TAP" switch -c "prepare/dead-letter-$VERSION"
REVIEW=/absolute/path/outside-the-tap/new-review-directory
python scripts/release.py homebrew-prepare \
  --version "$VERSION" --checksums "$EVIDENCE" \
  --tap "$TAP" --output-dir "$REVIEW" --write
```

`--write` refuses main, another repository, an unexpected installed tap path,
same-version rewrites, rollbacks, symlinked formulas, and reused review directories.
It asks Homebrew to regenerate resources. When the bump leaves the resource
blocks unchanged, it invokes the dedicated resource updater explicitly.

Homebrew commonly emits source archives. The existing tap deliberately uses
wheels, including native binaries: blindly replacing them with sdists would
change its build/toolchain requirements. The helper therefore uses the declared
Homebrew Python's isolated `pip download --only-binary=:all: --no-deps` to select
compatible wheels for **Homebrew's resolved exact versions**. Downloads are checked
against PyPI URLs and hashes; no dependency sdist build hooks are run by that pip
step. Missing compatible wheels stop preparation rather than adding Rust/build
requirements or silently retaining stale resources.

A successful run changes only `Formula/dead-letter.rb` and creates three external
review files: `formula.patch`, `preparation.json`, and `pr-body.md`. The packet
records the base commit and patch/formula SHA-256. `brew style` runs, but package
installation, conversion, and `brew test` are **not** claimed. Failure restores
only the known formula output when it still matches this invocation's recorded
state; unrelated or concurrent edits are left for inspection. Inspect the tap
and any partial review packet before rerunning.

### 3. Review, then explicitly open a draft PR

Read the diff and checklist. The separate remote-write phase requires the exact
patch hash from the packet and refuses edits made after preparation:

```bash
python scripts/release.py homebrew-prepare \
  --version "$VERSION" --checksums "$EVIDENCE" \
  --tap "$TAP" --output-dir "$REVIEW" \
  --open-pr --reviewed-diff-sha256 REVIEWED_PATCH_SHA256
```

This phase commits the reviewed formula, pushes the preparation branch without
force, and opens a **draft** PR against `BigCactusLabs/homebrew-tap` `main`. It uses
normal Git/GitHub CLI authorization only in this explicit phase. No auto-merge,
release/tag command, or tap-main update is used. If commit/push/PR creation fails
partway, inspect which write succeeded; do not blindly rerun or reset history.

The PR checklist leaves native source installation, `brew test`, synthetic email
conversion/source preservation, and absence of optional entrypoints unchecked.
Merge remains a maintainer action. Status reports a different valid tap version
as deferred until its main-branch formula adopts the requested package.

## Verification and primary references

```bash
python -m unittest discover -s tests/plugin -p test_release_status.py -v
python -m unittest discover -s tests/plugin -p test_homebrew_prepare.py -v
```

The tests use synthetic provider responses, real temporary Git repositories,
and fake Homebrew/pip/remote-write commands. They do not stand in for the native
macOS acceptance checklist. Both files are also included by the existing plugin
suite; no new scheduled status crawler or release authority is introduced.

Reviewed September 19, 2026:
[PyPI's 14-day upload restriction](https://blog.pypi.org/posts/2026-07-22-releases-now-reject-new-files-after-14-days/),
[GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases),
[Homebrew command reference](https://docs.brew.sh/Manpage), and
[MCP Registry API](https://registry.modelcontextprotocol.io/docs).
