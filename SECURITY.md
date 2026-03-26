# Security Policy

## Reporting a Vulnerability

**Please do not open public issues for security vulnerabilities.**

Use [GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
to submit a report directly through this repository's **Security** tab when
available. If you cannot access that flow, email
`bonjour@bigcactuslabs.xyz` instead.

We will acknowledge receipt within 72 hours and aim to provide a resolution
timeline within one week.

## Scope

dead-letter is a **local-only**, single-user tool. The web UI server binds to
`127.0.0.1` by default, and network exposure via `--host` override or other
deployment changes is unsupported. Security concerns relevant to this project
include:

- **Filesystem boundary violations** — Path traversal, symlink escape, unsafe
  staging/import behavior, or writes outside configured Inbox, Cabinet, or
  output roots.
- **Unsafe rendered output** — Malicious HTML or metadata that survives
  sanitization in a way that could execute or materially mislead when viewed
  downstream.
- **Destructive local operations** — Unintended overwrite, move, delete, or
  watch-mode behavior affecting user files.
- **Malicious input resource exhaustion** — Specially crafted `.eml` input that
  triggers severe crashes, hangs, or resource exhaustion.

## Out of Scope

- Vulnerabilities that require intentionally exposing the UI server to
  untrusted networks (this is an unsupported configuration).
- Social engineering attacks against end users or maintainers.
- Routine crash-only bugs, malformed-message handling issues, or performance
  problems with no meaningful security impact.

## Supported Versions

| Version | Supported |
| --- | --- |
| Latest release | Yes |
| Older releases | No |

Security fixes are applied to the latest release only. There is no backport
policy for older versions at this time.
