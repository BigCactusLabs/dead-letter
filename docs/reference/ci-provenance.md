# CI checkout provenance

When a traceback names a source interface or test path that is absent from the
reported commit, establish the tested checkout before modifying production code
or memory thresholds. A passing job for a different commit is not validation.

## Evidence in each test job

The `Checkout and import provenance` step runs after dependency setup and before
pytest, using the same uv environment. Both the main test job and the Linux,
macOS and Windows MBOX/MCPB jobs print a JSON report from
`scripts/ci_provenance.py`. No new action, dependency, permission or release
operation is introduced.

The report contains:

- Checked-out HEAD, its parents, tracked-file dirty state, and allowlisted run
  identifiers. The pull-request event contributes only its number and head/base
  SHAs; its title, body and the rest of the environment are not printed.
- The pre-test resolver's `dead_letter.core.mbox` origin, without executing
  package initializers. It must resolve to this checkout's `src/` parser.
- Git index blob IDs and bounded-read SHA-256 fingerprints of monitored workflow,
  dependency, MBOX source/test and streamed-report test files. File contents and
  mailbox fixtures are not printed. Raw working-tree hashes can differ across
  operating systems because of checkout line-ending conversion; they are not
  compared directly to Git index blob IDs.
- Whether `tests/core/test_mbox_stream.py`, named in the disputed historical
  Windows log, actually exists and is tracked. Its absence is informational,
  not a reason to create that test or to fail an otherwise correct checkout.

The check fails before tests if HEAD differs from `GITHUB_SHA`, tracked files
are dirty, a monitored file is missing or not a regular checkout file, or the
parser resolves elsewhere. Failure to collect provenance is also a failure.
Untracked build output does not make a clean checkout fail.

## Reproduce locally

From a complete development checkout:

```bash
uv sync --extra dev --locked
uv run python scripts/ci_provenance.py
uv run pytest -q tests/core/test_ci_provenance.py
```

The script uses only the Python standard library and Git. Its diagnostic tests
create synthetic repositories and need no MIME dependencies:

```bash
uv run python -m unittest discover -s tests/core -p test_ci_provenance.py
```

Run it with the actual test interpreter. A globally installed copy of the package
is intentionally not accepted as evidence for the checkout under test.

## Interpreting a failure

Read the report and traceback from the **same job and run attempt**. For a normal
pull-request checkout, `GITHUB_SHA` and HEAD can be the synthetic merge commit;
they need not equal the pull request's head SHA. The report records both instead
of replacing merge testing with a head-only checkout.

Use the recorded commit to fetch the failing path, confirm its hash and imported
interface, then reproduce the exact test. If source and log still disagree,
treat the result as unresolved provenance rather than inventing missing source,
loosening memory limits, or claiming a parser regression was fixed.

Security scanning is independent: inspect that workflow's actual error before
changing dependencies or scanner policy. This diagnostic does not fix or waive
a Grype finding, and does not retrospectively validate earlier CI reports.

## Limits

This is diagnostic metadata from a CI runner, not a signed attestation. It records
pre-test resolution, not modules subsequently imported by pytest or a worker.
It does not fingerprint a built bundle's runtime; the existing bundle smoke test
remains necessary. Local synthetic tests do not replace the full OS matrix or
real-mail validation. No mail is processed by the diagnostic.
