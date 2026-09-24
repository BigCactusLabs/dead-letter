# Verification

One entry point for local checks and the independent CI suites. This is a thin
stdlib subprocess runner, not a task framework. It never tags, publishes,
updates a marketplace, or edits the Homebrew tap.

## Choose a mode

After `uv sync --extra dev --locked`:

```bash
python scripts/verify.py quick
python scripts/verify.py full
python scripts/verify.py packaging
```

| Mode | Checks |
| --- | --- |
| `quick` | Core, backend, frontend entrypoint syntax |
| `full` | Core, backend, plugin contracts, pinned plugin schema validator, Agent Skill dry run, frontend tests/syntax, offline release metadata |
| `packaging` | Build wheel/sdist once, check embedded README metadata and checksums, `twine check --strict`, eight isolated installed-package probes |

For a shared-interface or release-preparation change, run `full` **and**
`packaging`. `full` is not an assertion that every possible distribution or
client was tested. MCPB's three-OS matrix, native Docker architectures, docs
links, workflow lint, the optional-SDK
[analysis contract workflow](../../.github/workflows/typesafe-contracts.yml),
and fresh GUI-client installation remain separate checks.
Missing a Docker daemon is not a passing container test; record it as unverified.

CI calls the same command definitions rather than copying them into shell:

```bash
python scripts/verify.py full --suite core
python scripts/verify.py full --suite backend
python scripts/verify.py full --suite plugin
python scripts/verify.py full --suite frontend
```

`--suite` is repeatable and also accepts `metadata`. Core/backend/plugin/
frontend run as independent CI jobs, so a Python failure cannot hide the
JavaScript result. The old `test` status remains as an aggregate requiring
all four jobs plus packaging. Source-import provenance and the synthetic
MBOX audit remain in CI; MCPB platform checks are unchanged.

Individual commands remain listed in [Contributing](../../CONTRIBUTING.md).
Source Python checks use `uv run --locked --no-sync`: setup is explicit,
and a verification run does not silently refresh the development environment.
Schema/skill checks need Node/npm and `gh` with skill support (2.90+).
Packaging resolves build/runtime dependencies and pinned `twine==7.0.0`; it
can require network access for installation. Runtime probes use synthetic local
mail and fake HTTP for analysis; they make no live provider requests.

## Workflow syntax and security

The separate [workflow-lint job](../../.github/workflows/workflow-lint.yml)
runs on every pull request and pushes to `main` / `feat/**`, without path
filters or publication credentials. It uses **actionlint 1.7.12** for Actions
syntax, expressions, action inputs, and embedded shell checks, plus
**zizmor 1.30.1** with its intended CI **regular** persona. These tools
complement the repository-specific workflow tests; they do not replace them.

From the repository root, with actionlint 1.7.12 and ShellCheck installed:

```bash
actionlint -version
command -v shellcheck
shellcheck --version
actionlint -color
uv tool run --from zizmor==1.30.1 zizmor --no-progress --persona=regular .github/workflows
uv run --locked --no-sync pytest -q tests/plugin/test_workflow_security.py
```

Run both linters even when the first fails. CI enforces their exit codes and
checks that ShellCheck is installed, because actionlint otherwise skips that
integration. A local missing tool is **not tested**, not a clean audit.
Zizmor's online checks use `GH_TOKEN` for GitHub metadata; keep it read-only.
An offline/unauthenticated audit is not equivalent to the authenticated CI
check of pinned action refs. Do not use `--no-exit-codes` or SARIF output as
the sole enforcement command: SARIF reporting disables finding-based exits.

CI verifies actionlint's Linux amd64 archive SHA-256 before extraction. The
version and hash are adjacent in the workflow and covered by regression tests;
update them together from the upstream release asset. Zizmor's PyPI version
is exact. Existing action executables remain SHA-pinned; use exact release
version comments rather than moving-major comments that become misleading.
The initial #126 audit corrected comments, not executable action versions.

### Credential and cache boundaries

Every checkout explicitly disables credential persistence. Plugin validation
uses the default read token instead of `RELEASE_PAT`. The existing token-presence
check and marketplace checkout still use the release token, but neither leaves
it in Git configuration. The two plugin pushes obtain their step-owned token
through a command-scoped `gh auth git-credential` helper; no global Git auth
setup or token-bearing command argument is needed. Pushes remain non-forced,
and tag, ancestry, version, and PyPI-readiness checks stay in place.

Package release Node setup disables implicit package-manager caching. Package
release uv setup and the reusable container publishing job disable uv caching.
Ordinary development CI can still cache dependencies. No repository permissions,
secret values, environments, tags, or publishing authority are changed here.

The credential regressions use a synthetic token and only Git's `fill` /
`approve` protocol operations, never a network push. One runs with a fake CLI;
one exercises the actual `gh` when available, using isolated config directories
and checking that no token or credential configuration was persisted. The
actual-CLI test is explicitly skipped on machines without `gh`. Neither test
proves that the real release token has sufficient remote scopes.

### Suppressions

There is **no `zizmor.yml` suppression file**. One inline exception is attached
to `release.yml`'s `build-container` **job-level** `uses` declaration:
`self-repository`. GitHub documents that this existing `./.github/workflows/...`
form resolves from the same commit as the caller, just like the newer `$/`
form. It is not a local step action loaded from mutable workspace files.
We retain the documented same-commit call rather than bundle a syntax migration
into this audit. Do not copy the exception to step-level local actions.

Every future ignore requires a rule-specific YAML comment with a concrete
reason and review of `test_workflow_security.py`; directory-wide security
exemptions are not the default. Remove the exception when a separately validated
migration adopts the newer syntax. Regular persona filtering is tool policy,
not a repository-owned suppression list or a claim that all risks are absent.

Primary references, reviewed September 19, 2026:
[actionlint release](https://github.com/rhysd/actionlint/releases/tag/v1.7.12),
[zizmor usage and exit codes](https://docs.zizmor.sh/usage/),
[zizmor audits](https://docs.zizmor.sh/audits/),
[GitHub same-commit workflow reuse](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows),
and [GitHub CLI credential helper](https://github.com/cli/cli/blob/0cf1092493af067646fc5f3db9421c6a6ec9c938/pkg/cmd/auth/gitcredential/helper.go).

## Machine-readable results

The runner sends subprocess logs to stderr and exactly one JSON document to
stdout. Independent checks continue after a failure:

```bash
python scripts/verify.py full > verification.json
```

`schema_version: 1` includes the mode, each check's name/status/command where
available, counts, and `exit_code`. Per-check statuses are `passed`, `failed`,
and `could-not-run`. A missing executable, unsupported `gh skill` command,
or launch error is not reported as a failed test or a pass. A process that
runs and exits nonzero, or exceeds its timeout, is a failure; consult stderr
for network, dependency, or assertion details.

Exit **0** means all selected checks passed; **1** means at least one failed;
**2** means checks could not run but none failed. Invalid CLI usage also exits
2 through argparse. Do not discard the exit code when capturing JSON. An
invalid build/metadata prerequisite blocks installs of that artifact, with
those probes explicitly reported as `could-not-run` rather than passed.

## Packaged-install boundary

The default packaging mode uses a temporary build directory and deletes it
on exit. To test an existing recorded build without rebuilding or changing
its checksum evidence:

```bash
python scripts/verify.py packaging \
  --dist-dir build/package/dist \
  --checksums build/package/SHA256SUMS
```

The release build job creates that pair with `uv build` and
`scripts/package_artifacts.py record`. `verify` rejects tampering, missing or
extra distributions, version/name mismatches, duplicate metadata headers,
non-Markdown descriptions, missing ownership markers, relative links/logos,
and differing wheel/sdist README bodies. It reads archives without extracting
or importing them. Keep `SHA256SUMS` outside the distribution directory.

Each wheel profile gets its own clean venv: core only, `cli`, `mcp`, `ui`,
`benchmark`, and `typesafe`. Two further venvs install the sdist as core and
with `typesafe`, checking that it can build/install independently too.
The installer receives a direct
local artifact URL, not a package name that could resolve to PyPI. Transitive
dependencies may still resolve from package indexes.

Probes run with isolated Python (`-I`) from an empty working directory
outside the repository, with host Python/project path overrides removed.
They verify the imported path is inside that venv and `direct_url.json`
identifies the exact artifact, rejecting editable or index-substituted
installs. Set `TMPDIR` outside the checkout when overriding the system default.

All profiles exercise CLI help and synthetic conversion, entrypoint metadata,
and source preservation. Core verifies optional stacks did not leak in;
extras exercise watchfiles, a bounded four-tool MCP stdio session plus
conversion, UI imports/packaged static resources, and a tokenizer without
remote vocabulary downloads. A stdio handshake is not a GUI install test.

Core and TypeSafe profiles check offline analysis previews and lazy SDK imports.
The TypeSafe wheel/sdist profiles require the exact SDK before running the
existing analysis contracts against the installed package with synthetic keys
and fake HTTP. These checks establish packaging and transport behavior, not
empirical profile quality. See [experimental analysis](experimental-analysis.md).

The README remains hand-written. Its logo and outbound repository links are
absolute so PyPI does not interpret them relative to a project page. The
renderability check runs on **built metadata**, not just README source.

See [Publishing](publishing.md) for artifact handoff, approval, and recovery.
