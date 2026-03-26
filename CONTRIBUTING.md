# Contributing to dead-letter

Thanks for your interest in contributing to dead-letter! This guide covers the
development workflow, code conventions, and how to submit changes.

## Code of Conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md). By
participating you agree to uphold its terms.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/BigCactusLabs/dead-letter.git
cd dead-letter

# Install dependencies (requires uv and Python 3.12+)
uv sync --extra dev

# Verify the setup
uv run pytest tests/core
uv run pytest tests/backend
node --test tests/frontend/app.test.js
```

## Test Commands

| Suite | Command |
|---|---|
| Core | `uv run pytest tests/core -v` |
| Backend | `uv run pytest tests/backend -v` |
| Frontend | `node --test tests/frontend/app.test.js` |
| Frontend syntax | `node --check src/dead_letter/frontend/static/app.js` |
| Stop on first failure | `uv run pytest -x` |
| Single test | `uv run pytest -k "test_name"` |
| Coverage | `uv run pytest --cov` |
| Lint | `uv run ruff check .` |
| Format check | `uv run ruff format --check .` |
| Type check | `uv run pyright` |

Run the most targeted suite first, then broaden if your change touches shared
interfaces.

## Code Style

### Commit Messages

This project uses **conventional commits**. Each commit message should follow
the format:

```
<type>: <short summary>
```

Common types:

| Type | Use |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `chore` | Build process, dependency updates, tooling |
| `ci` | CI configuration changes |
| `build` | Build system changes |

Examples:

```
feat: add vCard attachment extraction
fix: handle missing Content-Type header in MIME parts
docs: clarify CLI watch mode usage
```

### Python

- Follow existing patterns in the codebase.
- Format with `ruff format`.
- Lint with `ruff check`.
- Type-annotate public APIs; verify with `pyright`.
- Keep changes scoped and minimal. Avoid drive-by refactors.
- Add tests for behavior changes.

## Pull Request Process

1. **Fork** the repository and create a feature branch from `main`.
2. **Branch naming**: use a descriptive slug, e.g. `fix/mime-charset-fallback`
   or `feat/ics-recurrence`.
3. **Keep PRs focused.** One logical change per pull request.
4. **Write or update tests** for any changed behavior.
5. **Run the full check suite** before pushing:
   ```bash
   uv run pytest tests/core
   uv run pytest tests/backend
   uv run ruff check .
   uv run ruff format --check .
   ```
6. **Open a PR** against `main` and fill in the pull request template.
7. A maintainer will review and may request changes.

For large changes or new features, please open an issue first to discuss the
approach.

## Scope Guidance

dead-letter converts `.eml` files to Markdown with YAML front matter. Changes
that fit well:

- Improvements to MIME parsing, HTML sanitization, or Markdown rendering
- Better handling of attachments, calendar data, or threading
- CLI and web UI enhancements
- Performance and reliability fixes
- Documentation and test coverage

Changes that are likely out of scope:

- Support for non-email formats (mbox, PST, etc.) without prior discussion
- Large dependency additions -- open an issue first
- UI redesigns -- discuss in an issue before investing effort

## Reporting Bugs

Please use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml)
when filing issues. Include reproduction steps, expected vs. actual behavior,
and your Python version / OS.

## Questions?

Open a discussion or file an issue. We're happy to help.
