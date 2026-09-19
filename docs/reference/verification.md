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
| `packaging` | Build wheel/sdist once, check embedded README metadata and checksums, `twine check --strict`, six isolated installed-package probes |

For a shared-interface or release-preparation change, run `full` **and**
`packaging`. `full` is not an assertion that every possible distribution or
client was tested. MCPB's three-OS matrix, native Docker architectures, docs
links, and fresh GUI-client installation remain separate checks. Missing a
Docker daemon is not a passing container test; record it as unverified.

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
can require network access even though it only converts synthetic local mail.

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
and `benchmark`. A sixth fresh venv installs the sdist core, checking that
it can build/install independently too. The installer receives a direct
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

The README remains hand-written. Its logo and outbound repository links are
absolute so PyPI does not interpret them relative to a project page. The
renderability check runs on **built metadata**, not just README source.

See [Publishing](publishing.md) for artifact handoff, approval, and recovery.
