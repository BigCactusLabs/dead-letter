# Publishing

This is the maintainer runbook for cutting a dead-letter release and updating
the Homebrew tap.

## Policy

- GitHub releases publish to PyPI through `.github/workflows/release.yml`.
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

The Claude plugin under [`plugin/`](../../plugin/) has its own release tag
independent of the package version. The
[`BigCactusLabs/bigcactuslabs-plugins`](https://github.com/BigCactusLabs/bigcactuslabs-plugins)
marketplace points at this directory via `git-subdir` with
`"ref": "release"` — a fast-forward-only branch in this repo. Shipping a
plugin release means advancing `release`; the marketplace manifest is never
edited per release.

Release the plugin only after the PyPI release the plugin's `.mcp.json` is
pinned to has been published (verify with the `dead-letter==X.Y.Z` step under
Publish To PyPI above). The MCP launcher pin in `plugin/.mcp.json` is the
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

3. Fast-forward the `release` branch to the tagged commit. This is the step
   that ships — marketplace auto-update follows `release` and picks up the
   bumped `plugin.json` `version`. The `^{}` suffix is required because the
   tag is annotated:

   ```bash
   git push origin 'plugin-vX.Y.Z^{}:release'
   ```

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
