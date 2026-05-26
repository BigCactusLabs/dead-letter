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

4. Run local validation:

   ```bash
   uv sync --extra dev --locked
   uv run pytest -q tests/core
   uv run pytest -q tests/backend
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

## Do Not Automate The Tap Yet

Do not expand `.github/workflows/release.yml` to update the tap in this pass.
Cross-repository tap automation would require credentials, secret management,
and a rollback policy. Keep tap updates manual until that workflow is designed
deliberately.
