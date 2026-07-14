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
marketplace points at this directory via `git-subdir` with
`"ref": "release"` — a fast-forward-only branch in this repo. Shipping a
plugin release means advancing `release`; the marketplace manifest is never
edited per release.

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
   fast-forwards `release` to the tagged commit. This is the step that ships —
   marketplace auto-update follows `release` and picks up the bumped
   `plugin.json` `version`. If the workflow needs a manual fallback or an
   emergency advance, the `^{}` suffix is required because the tag is
   annotated:

   ```bash
   git push origin 'plugin-vX.Y.Z^{}:release'
   ```

   > **One-time setup — `RELEASE_PAT` secret.** The workflow authenticates the
   > push with a repository secret named `RELEASE_PAT`, not the built-in
   > `GITHUB_TOKEN`: advancing `release` carries `.github/workflows/*` changes,
   > and GitHub refuses `GITHUB_TOKEN` pushes that create or update workflow
   > files (the `workflows` permission cannot be granted to it). Create a
   > **fine-grained PAT** scoped to this repository with **Contents: write** and
   > **Workflows: write**, then add it under *Settings → Secrets and variables →
   > Actions* as `RELEASE_PAT`. If the secret is absent the workflow fails fast
   > with a pointer here; the manual `git push … :release` fallback above always
   > works with your own workflow-scoped credentials.

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

5. Run the full manual checklist in [`plugin/TESTING.md`](../../plugin/TESTING.md).

### Plugin Versioning Model

There are three independent versions to keep straight:

| Version | Source of truth | When it bumps |
|---|---|---|
| Package | `pyproject.toml` `version` field | Per package release (PyPI tag `vX.Y.Z`) |
| Plugin asset | `plugin/.claude-plugin/plugin.json` `version` | Per plugin-only change (tag `plugin-vX.Y.Z`) |
| MCP pin | `plugin/.mcp.json` `--from dead-letter[mcp]==X.Y.Z` | Per package release the plugin should adopt |

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
