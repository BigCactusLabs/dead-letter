# Contributing to dead-letter

Thanks for your interest in contributing to dead-letter! This guide covers the
development workflow, code conventions, and how to submit changes.

## Code of Conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md). By
participating you agree to uphold its terms.

## Development Setup

Use Python 3.12+, uv, and Node 22 for the repository's test workflow.

```bash
git clone https://github.com/BigCactusLabs/dead-letter.git
cd dead-letter
uv sync --extra dev --locked
python scripts/release.py check
```

`--locked` detects unexpected lockfile drift instead of refreshing dependencies
as a side effect of setup. For an intentional dependency change, edit the
project constraints, run `uv lock`, and review the resulting lock diff.
The frontend is static Alpine.js/JavaScript; it has no application build step.

## Test Commands

Use the shared runner after setup:

```bash
python scripts/verify.py quick
python scripts/verify.py full
python scripts/verify.py packaging
```

`full` covers the source suites and validators; `packaging` separately builds
and tests installed artifacts outside the checkout. Independent checks keep
running after failures. JSON goes to stdout, logs to stderr; exit 0 is all
passed, 1 is a failure, and 2 is unable to run without a test failure. Missing
`gh`/Node is never silently skipped. CI reuses `--suite` selections from this
runner. See [Verification](docs/reference/verification.md) for exact coverage,
prerequisites, JSON fields, and testing existing artifacts without rebuilding.

Individual commands remain useful for focused debugging:

| Suite | Command |
| --- | --- |
| Core | `uv run pytest -q tests/core` |
| Backend | `uv run pytest -q tests/backend` |
| Plugin, skill, packaging, release contracts | `uv run pytest -q tests/plugin` |
| Frontend | `node --test tests/frontend/*.test.js` |
| Frontend syntax | `node --check src/dead_letter/frontend/static/app.js` |
| Plugin schema | `npx --yes @anthropic-ai/claude-code@2.1.145 plugin validate plugin/` |
| Agent Skill validation | `gh skill publish --dry-run` |
| Distribution metadata, offline | `python scripts/release.py check` |
| Release-helper regressions, no app dependencies | `for t in test_release.py test_release_status.py test_homebrew_prepare.py; do python -m unittest discover -s tests/plugin -p "$t" -v || exit 1; done` |
| Packaging-helper regressions, no app dependencies | `python -m unittest discover -s tests/plugin -p test_package_verification.py -v` |
| Single test | `uv run pytest -k "test_name"` |
| Stop on first failure | `uv run pytest -x` |
| Coverage | `uv run pytest --cov` |
| Advisory lint / format / types | `uv run ruff check .` / `uv run ruff format --check .` / `uv run pyright` |

The skill command needs `gh` with skill support (2.90+); the dry run publishes
nothing. Run the targeted suite first, then all Python/frontend checks before
claiming a shared-interface change is complete. CI also checks maintained
Markdown links, builds/smokes MCPB on Linux/macOS/Windows, and tests containers
natively on amd64/arm64 for container-related changes. Offline contract tests
are not substitutes for a real Docker run or a fresh desktop-client install.
Record precisely which checks ran and which remain unverified.

## Code Style

Use existing patterns, type-annotate public APIs, and keep changes scoped.
Ruff and Pyright are advisory; do not turn an unrelated change into a sweeping
formatting migration. Behavior changes ship with regression tests.

### Commit Messages

Use conventional commits, `<type>: <short summary>`, with `feat`, `fix`,
`docs`, `test`, `refactor`, `chore`, `ci`, or `build`:

```text
feat: add vCard attachment extraction
fix: handle missing Content-Type header in MIME parts
docs: clarify CLI watch mode usage
```

## Pull Request Process

Create a descriptive branch from `main` (a fork for external contributions).
Keep each PR focused, add tests and the relevant docs, run the checks above,
and open against `main` using the PR template. User-facing behavior changes
need an entry in [CHANGELOG.md](CHANGELOG.md); do not invent a release date.
For a substantial new feature or dependency, discuss the design in an issue
before coupling it to existing contracts.

## Documentation

The [docs index](docs/README.md) routes by task. Use the
[distribution map](docs/reference/distribution.md) for channel choices and
[Publishing](docs/reference/publishing.md) for release mechanics. Keep one
canonical explanation instead of copying current version numbers between
README, agent instructions, and channel guides.

Durable contracts belong in `docs/reference/`; plans and audit records in
`docs/project/`; visual identity in `docs/brand/`. Preserve old URLs/anchors
when practical, identify completed plans as history, and separate implemented,
published, submitted, accepted, and client-tested states. The runtime/state
references' “v4” names describe an interface generation, not a PyPI version.

Keep the README's logo, voice, useful examples, and “Tools We Love.” Move
maintainer detail to the runbook instead of burying the first conversion.

## Maintainer Publishing

Follow the [publishing runbook](docs/reference/publishing.md) for dry-run
version preparation, stable package tags/releases, MCPB, OCI, registry
metadata, independent plugin tags, and recovery. `prepare` emits a patch;
it does not edit files, tag, or publish. Review and apply it explicitly.

Package publication, plugin adoption, Homebrew, and curated listings are not
one atomic event. The Homebrew tap remains a manual core-only update. Do not
advance pointers or publish merely because a docs/development PR is ready.

## Scope Guidance

dead-letter currently converts `.eml` files to Markdown with YAML front matter.
Useful contributions improve MIME parsing, sanitization, rendering, attachment
retention, calendars, threading, UI/CLI/agent workflows, performance, tests,
and docs. Keep private email out of public fixtures and treat embedded
instructions as untrusted data.

MBOX/Gmail Takeout, PST, and MSG are **other email/archive formats**, not
non-email formats. Expansion belongs in its own reviewed implementation and
acceptance plan; an issue or open PR is not shipped support. Large dependency
additions and visual redesigns likewise benefit from an explicit design
rather than a drive-by change.

## Reporting Bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml). Include
reproduction steps, expected vs. actual behavior, Python/OS/client version,
and a sanitized or synthetic fixture when possible. Use the private route in
[SECURITY.md](SECURITY.md) for vulnerabilities; do not publish private mail or
credentials in an issue.

## Questions?

Open a discussion or file an issue. We're happy to help.
