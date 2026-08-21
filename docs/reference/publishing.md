# Publishing

This is the maintainer runbook for cutting a dead-letter release and updating
the Homebrew tap.

## Policy

- GitHub releases publish to PyPI through `.github/workflows/release.yml`.
- The same workflow then publishes server metadata to the Official MCP
  Registry (`registry.modelcontextprotocol.io`) via the `publish-mcp` job,
  which runs after the PyPI publish succeeds. It authenticates with GitHub
  OIDC (no stored secret) to claim the `io.github.BigCactusLabs/*` namespace.
- The Homebrew tap is updated manually after PyPI publish succeeds.
- The Homebrew formula installs the core CLI only: `dead-letter convert` and
  `dead-letter doctor`.
- Do not bundle the optional UI or MCP dependency stacks in the tap. Users who
  need those should install `dead-letter[ui]` or `dead-letter[mcp]` with
  `pipx`, or run them from source with `uv`.
- Cowork detects personal-marketplace updates from commits to the marketplace
  repository. Moving a branch in a downstream plugin repository does not make
  that marketplace stale. Each plugin release must therefore update the
  marketplace entry's `version`, tag `ref`, and commit `sha` together.
- A Claude community marketplace listing must not track this repository's
  default-branch `HEAD`. The community catalog pins plugins to a SHA and its
  current updater follows upstream `HEAD` by default. Before submission, record
  the source repository, plugin path, source ref, initial SHA, and automatic
  update target; submit only when ordinary `main` pushes cannot advance the
  catalog pin. A `ref: release` field alone is not proof that the catalog's
  updater honors the release pointer. As of August 11, 2026, the existing
  candidate source (`BigCactusLabs/dead-letter`, path `plugin`, ref `release`)
  is blocked from submission because the community workflow resolves repository
  `HEAD` and does not wire its available release-only tracking option.

## Prepare The Release

1. Choose the next semantic version.
2. Update release metadata:
   - `pyproject.toml`
   - `uv.lock`
   - `src/dead_letter/__init__.py`
   - `CHANGELOG.md`
   - `server.json` — bump `version`, `packages[0].version`, and the
     `dead-letter[mcp]==X.Y.Z` pin in `packages[0].runtimeArguments`. The
     `publish-mcp` job re-stamps these from the release tag, so this is
     belt-and-suspenders for local `mcp-publisher publish` runs, but keep it
     in sync.
   - `plugin/.claude-plugin/plugin.json` — bump `version` to `X.Y.Z`.
   - `plugin/.mcp.json` — bump the `dead-letter[mcp]==X.Y.Z` pin.

   Bump the plugin files **in lockstep with the package by default**: an
   aligned release ships the same version to PyPI and to plugin users, so the
   plugin bump belongs in this same release-prep commit. The only reason to
   skip them here is a deliberate plugin-only patch or an intentional decision
   *not* to adopt this package version into the plugin yet — see
   [Plugin Versioning Model](#plugin-versioning-model). Forgetting them is what
   left the marketplace on a stale version before; CI now emits a warning when
   the plugin pin lags `pyproject.toml` (see `.github/workflows/ci.yml`).
3. Verify the version import:

   ```bash
   uv run python -c "import dead_letter; print(dead_letter.__version__)"
   ```

4. Run local validation. This block mirrors the `ci.yml` job steps so a local
   pass implies a CI pass; if you intentionally skip a step (for example, the
   pinned plugin validator cannot be downloaded), note that in the release-prep
   PR description so reviewers know CI is the first place that step runs.

   ```bash
   uv sync --extra dev --locked
   uv run pytest -q tests/core
   uv run pytest -q tests/backend
   uv run pytest -q tests/plugin
   npx --yes @anthropic-ai/claude-code@2.1.145 plugin validate plugin/
   node --test tests/frontend/*.test.js
   node --check src/dead_letter/frontend/static/app.js
   uv build
   ```

5. Commit and push the release-prep change to `main`.
6. Wait for the `main` CI and docs-link-check workflows to pass.

If dead-letter is listed in Anthropic's community marketplace, inspect its
catalog entry before step 5. Stop if the entry follows default-branch `HEAD` or
if its update target cannot be proven release-only: pushing release-prep to
`main` could otherwise publish a plugin whose exact PyPI pin is not live yet.

```bash
gh api -H 'Accept: application/vnd.github.raw+json' \
  repos/anthropics/claude-plugins-community/contents/.claude-plugin/marketplace.json \
  | jq -e '.plugins[] | select(.name == "dead-letter")'
```

## Publish To PyPI

Create and publish the GitHub release from the verified release-prep commit:

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --verify-tag --title vX.Y.Z --notes-file /path/to/release-notes.md
```

Publishing the GitHub release triggers `.github/workflows/release.yml`, which
builds the package and publishes it to PyPI with attestations. Confirm the
release workflow succeeds:

```bash
gh run list --all --limit 10 --json databaseId,status,conclusion,workflowName,event,headBranch,headSha,url
gh run watch RUN_ID --compact --exit-status
```

Confirm PyPI has the new version:

```bash
curl -fsSL https://pypi.org/pypi/dead-letter/X.Y.Z/json
```

## MCP Registry Publish (Automatic)

The `publish-mcp` job in `.github/workflows/release.yml` runs after the PyPI
publish and pushes `server.json` to the Official MCP Registry. From there the
listing propagates automatically to the GitHub MCP Registry (`github.com/mcp`),
PulseMCP, and other aggregators — no separate submission.

How it works:

- Ownership is proven by two things that must both be true for a given
  version: the `<!-- mcp-name: io.github.BigCactusLabs/dead-letter -->` marker
  in `README.md` (which becomes the PyPI package description) and GitHub OIDC
  proving the workflow runs under the `BigCactusLabs` org.
- The job waits for PyPI to serve the new version before publishing, because
  the registry validates the marker against the live PyPI description.
- Because the marker ships in the package README, the registry publish only
  succeeds for releases cut **after** the marker landed on PyPI. `0.2.3` — the
  last release before the marker — predates it and was never registry-published;
  the first successful MCP publish landed with the `0.2.4` release. No backfill
  is possible for `0.2.3`.
- The MCP Registry is in preview and may reset its data. Because every release
  re-publishes, a reset self-heals on the next release; to force a re-publish
  without a version bump, run the steps below locally.

Manual publish (only if you need to re-publish outside a release, e.g. after a
registry data reset):

```bash
# One-time: install the CLI
brew install mcp-publisher   # or download from the registry releases page

# From a checkout whose server.json version matches a version already on PyPI:
mcp-publisher login github    # browser device-code flow
mcp-publisher publish
```

## Update The Homebrew Tap

Update `BigCactusLabs/homebrew-tap` only after the PyPI release is live.

1. Get the new sdist URL and SHA from PyPI:

   ```bash
   curl -fsSL https://pypi.org/pypi/dead-letter/X.Y.Z/json
   ```

2. Update `Formula/dead-letter.rb` in `BigCactusLabs/homebrew-tap`:
   - set the formula URL and SHA to the new PyPI sdist
   - update Python resource URLs and SHAs for the core CLI dependency set
   - keep `dead-letter-ui` and `dead-letter-mcp` removed from `bin`
   - keep the formula core CLI only

3. Validate the formula from the tap checkout:

   ```bash
   HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_DEVELOPER=1 HOMEBREW_NO_INSTALL_FROM_API=1 brew fetch --formula Formula/dead-letter.rb
   HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_DEVELOPER=1 HOMEBREW_NO_INSTALL_FROM_API=1 brew style Formula/dead-letter.rb
   HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_DEVELOPER=1 HOMEBREW_NO_INSTALL_FROM_API=1 brew install --formula Formula/dead-letter.rb
   HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_DEVELOPER=1 HOMEBREW_NO_INSTALL_FROM_API=1 brew test dead-letter
   ```

4. Push the tap update:

   ```bash
   git add Formula/dead-letter.rb
   git commit -m "dead-letter X.Y.Z"
   git push origin main
   ```

5. Verify GitHub sees the tap formula:

   ```bash
   gh api repos/BigCactusLabs/homebrew-tap/contents/Formula/dead-letter.rb --jq .download_url
   ```

## Plugin Release

These are the closing **ship** steps of an aligned release: the plugin's
`version` and MCP pin were already bumped in [Prepare The Release](#prepare-the-release)
(lockstep by default), so all that remains is to tag the plugin and advance the
marketplace pointer. If you are instead cutting a plugin-only patch, do the
version bump here in a small standalone commit first.

The Claude plugin under [`plugin/`](../../plugin/) has its own release tag,
which is *permitted* to diverge from the package version (see
[Plugin Versioning Model](#plugin-versioning-model)) even though the two are
aligned today. The
[`BigCactusLabs/bigcactuslabs-plugins`](https://github.com/BigCactusLabs/bigcactuslabs-plugins)
marketplace points at this directory via `git-subdir` and pins the plugin's
explicit version, `plugin-vX.Y.Z` tag, and commit SHA. This makes Claude Code
and Cowork resolve the same immutable plugin assets. The `release` branch is
also advanced for compatibility with marketplace copies created before this
pinning model was adopted.

Release the plugin only after the PyPI release the plugin's `.mcp.json` is
pinned to has been published (verify with the PyPI `curl` check —
`https://pypi.org/pypi/dead-letter/X.Y.Z/json` — under Publish To PyPI above). The MCP launcher pin in `plugin/.mcp.json` is the
runtime contract — see `tests/plugin/test_plugin_structure.py` for the
enforced shape.

1. Confirm `main` CI is green and includes the `Plugin tests` and
   `Plugin schema validation` steps:

   ```bash
   gh run list --branch main --workflow ci.yml --limit 1
   ```

2. Tag the plugin release on the current `main` commit. The tag name is
   `plugin-vX.Y.Z` (note the `plugin-` prefix; the package's own release tag is
   plain `vX.Y.Z`). The `X.Y.Z` is the plugin's asset version — independent of
   the package version, though they're aligned today. The tag exists for the
   changelog, humans, and emergency rollback:

   ```bash
   git tag -a plugin-vX.Y.Z -m "Plugin release vX.Y.Z"
   git push origin plugin-vX.Y.Z
   ```

3. Pushing the `plugin-v*` tag automatically runs
   [`.github/workflows/plugin-release.yml`](../../.github/workflows/plugin-release.yml).
   It verifies the package version pinned in `.mcp.json` is live on PyPI, then
   opens and merges a marketplace pull request that pins the plugin version,
   tag, and SHA. The merged marketplace commit is what makes the update visible
   to Cowork. The workflow then fast-forwards `release` for older marketplace
   copies. If the workflow needs a manual fallback or an emergency advance,
   first update the marketplace entry, then use the `^{}` suffix because the
   tag is annotated:

   ```bash
   git push origin 'plugin-vX.Y.Z^{}:release'
   ```

   > **One-time setup — `RELEASE_PAT` secret.** The workflow authenticates with
   > a repository secret named `RELEASE_PAT`, not the built-in `GITHUB_TOKEN`.
   > Create a **fine-grained PAT** that can access both
   > `BigCactusLabs/dead-letter` and `BigCactusLabs/bigcactuslabs-plugins`.
   > Grant **Contents: write** and **Workflows: write** on `dead-letter`, plus
   > **Contents: write** and **Pull requests: write** on
   > `bigcactuslabs-plugins`. Add it under *Settings → Secrets and variables →
   > Actions* as `RELEASE_PAT`. The workflow stops before advancing `release`
   > if it cannot publish the marketplace pointer, preventing Claude Code and
   > Cowork from splitting again.

   Rollback, if ever needed:
   `git push --force origin 'plugin-vOLD.X.Y^{}:release'` — or, in an
   emergency, temporarily re-pin the marketplace
   [`marketplace.json`](https://github.com/BigCactusLabs/bigcactuslabs-plugins/blob/main/.claude-plugin/marketplace.json)
   `ref` to an old tag.

4. Smoke-test the live install in a fresh Claude Code session:

   ```
   /plugin marketplace add BigCactusLabs/bigcactuslabs-plugins
   /plugin install dead-letter
   /dead-letter:convert <path-to-fixture>.eml
   ```

   Use `claude plugin details dead-letter` from the shell to inspect what was
   resolved.

5. In Cowork, open **Customize → Plugins → Personal**, open the
   `bigcactuslabs-plugins` marketplace options, and select **Check for
   updates**. Confirm the marketplace's synced commit is the merge commit from
   step 3 and that Dead letter shows version `X.Y.Z` before running the MCP
   smoke test.

6. Run the full manual checklist in [`plugin/TESTING.md`](../../plugin/TESTING.md).

7. If the Claude community marketplace listing exists, wait for its nightly
   catalog update and run the catalog query from [Prepare The Release](#prepare-the-release).
   Record the listed `source.sha`, source path, and source ref. Confirm that the
   SHA resolves to the released plugin content only after the pinned PyPI
   version is live. If the SHA advanced early, stop distribution work and ask
   Anthropic to freeze or correct the entry.

### Plugin Versioning Model

There are four independent versions to keep straight:

| Version | Source of truth | When it bumps |
|---|---|---|
| Package | `pyproject.toml` `version` field | Per package release (PyPI tag `vX.Y.Z`) |
| Plugin asset | `plugin/.claude-plugin/plugin.json` `version` | Per plugin-only change (tag `plugin-vX.Y.Z`) |
| MCP pin | `plugin/.mcp.json` `--from dead-letter[mcp]==X.Y.Z` | Per package release the plugin should adopt |
| Community catalog pin | Anthropic catalog entry `source.sha` plus its recorded update target | Only after an approved plugin release whose MCP pin is live on PyPI |

A plugin-only patch (skill copy, command wording) bumps the plugin asset
version without touching the MCP pin. A new package release bumps the
package version and may bump the MCP pin in a follow-up plugin release. The
exact-pin rule in `tests/plugin/test_plugin_structure.py::test_mcp_json_pins_exact_dead_letter_version`
forbids unpinned or range pins so a future PyPI release cannot silently break
installed plugins.

## Do Not Automate The Tap Yet

Do not expand `.github/workflows/release.yml` to update the tap in this pass.
Cross-repository tap automation would require credentials, secret management,
and a rollback policy. Keep tap updates manual until that workflow is designed
deliberately.
