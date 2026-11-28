# Synthetic Google Takeout sample

A small, synthetic Gmail export and the Obsidian vault that released
dead-letter 0.4.10 produces from it. It is the sample for
[Move a Gmail Takeout export into an Obsidian vault](../takeout-to-obsidian.md).
Every message is invented; there is no real mail here.

| Path | Contents |
| --- | --- |
| [`takeout-20260921T120000Z-001.zip`](takeout-20260921T120000Z-001.zip) | Input: `Takeout/Mail/All mail Including Spam and Trash.mbox` and a placeholder `Takeout/archive_browser.html` |
| [`Takeout/`](Takeout/) | The same two files, extracted for reading |
| [`vault/`](vault/) | Expected output of the recipe's command, including `vault/Email/Gmail/.dead-letter-report.json` |
| [`capture.json`](capture.json) | Release, Python, uv, resolved packages, input hashes, commands, output, and a second-run record |

The five messages carry `X-GM-THRID` and `X-Gmail-Labels` headers, two
attachments (a CSV and a one-page PDF), and an HTML-only newsletter. The
report's `job.input_path` is relative because the command ran from the
working folder. The report keeps that run's timestamp, duration, and the ZIP's
inode and file times.

## Why a committed folder rather than a release zip

The sample ships as files in the repository, not as a zip attached to a
GitHub release:

- You can read every file on GitHub (the MBOX, each note, the report) before
  installing anything, and the input ZIP downloads on its own from its file
  page.
- It changes through ordinary pull requests, so a regenerated sample shows up
  as a reviewable diff and the docs link check covers its Markdown.
- It is about 30 KB, and it describes 0.4.10. No package release is planned
  until the current release hold lifts, so a release asset could not ship
  now and would describe a different version when it did.

GitHub has no one-click download for a single folder: to get the whole vault,
clone the repository or download its source ZIP, or run the recipe's command
on the input ZIP. If a single download proves useful, a zip of this folder can
be attached to a later release; this folder stays the source.

## Regenerate

```bash
python scripts/render_takeout_sample.py
```

The script needs `uv`. It installs `dead-letter==0.4.10` from PyPI into a
temporary environment, rebuilds the ZIP byte for byte, converts it, and
rewrites this folder apart from this README. It is never run in CI or tests.
Pass `--version` and `--exclude-newer` to target another release, then update
the recipe page to match what that release does.
