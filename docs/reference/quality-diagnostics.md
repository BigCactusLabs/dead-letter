---
title: Quality Diagnostics
doc_type: reference
status: canonical
last_updated: 2026-03-19
audience:
  - operators
  - maintainers
scope:
  - src/dead_letter/core
  - src/dead_letter/backend
  - src/dead_letter/frontend
---

# Quality Diagnostics

`dead-letter` exposes a summary quality signal for the current file job through `GET /api/jobs/{id}` and the frontend diagnostics disclosure in done state.

This summary is intended for operator review. It is not the raw internal conversion trace.

## Fields

- `state`: `normal | degraded | review_recommended`
- `selected_body`: `html | plain`
- `segmentation_path`: `html | plain_fallback`
  - `html` means the selected HTML body stayed on an HTML rendering path.
  - `plain_fallback` means the final zoning path was plain text. This can be a normal outcome for plain-text mail, not just an error fallback.
- `client_hint`: `gmail | outlook | generic | null`
- `confidence`: `high | medium | low`
- `fallback_used`: `plain_text_reply_parser | html_failure_plain_text_fallback | html_markdown_panic_repaired | null`
  - `plain_text_reply_parser` means the plain-text conversation parser was used for the final zoning step.
- `html_failure_plain_text_fallback` means HTML rendering failed and the operator-enabled override allowed the job to continue on the plain-text path.
- `html_markdown_panic_repaired` means HTML rendering initially panicked, then succeeded on the explicit repair retry path with reduced markup fidelity.
- Strict-mode HTML failures that do not convert do not emit diagnostics; eligible single-file failures instead expose a retry action through `GET /api/jobs/{id}`.
- `warnings`: list of `code`, `message`, `severity`
- `stripped_images`: list of `{category, reason, reference}` objects (present when `strip_signature_images` or `strip_tracking_pixels` is enabled and images were removed)
  - `category`: `signature_image | tracking_pixel`
  - `reason`: detection layer that matched (e.g., `gmail_signature_wrapper`, `front_signature_wrapper`, `thunderbird_signature_wrapper`, `apple_mail_signature_wrapper`, `gmail_mail_sig_url`, `filename_pattern:logo`, `structural_boundary_extension`, `dimension_heuristic`, `hidden_image`)
  - `reference`: the image `src` or CID that was stripped

## State Meanings

- `normal`
  - Conversion completed without low-confidence or warning flags.
  - A plain-text-only message may still report `segmentation_path="plain_fallback"` here.
  - Typical operator action: none.
- `degraded`
  - Conversion succeeded, but the pipeline recorded recoverable quality warnings.
  - Typical operator action: skim the output before filing it away.
- `review_recommended`
  - Conversion succeeded, but confidence is low enough that the Markdown should be reviewed before relying on it.
  - Typical operator action: compare `message.md` against the source `.eml`.

## Confidence Meanings

- `high`
  - Strong structure signals were available.
  - Example: Gmail or Outlook quote boundaries were recognized directly.
- `medium`
  - Conversion succeeded on a standard path without strong provider-specific evidence.
- `low`
  - The pipeline had enough uncertainty that operator review is recommended.

## Warning Categories

Warning codes emitted by the pipeline:

- `mime_defect` — structural MIME parsing defects (e.g., malformed headers, encoding issues) recovered during parsing
- `html_markdown_failed` — HTML-to-Markdown conversion panicked; plain-text fallback was used
- `html_markdown_repaired` — HTML-to-Markdown conversion initially panicked, then succeeded on the explicit repair retry path

Warnings are additive. The pipeline prefers preserving extra content over deleting potentially authored content.

## Common Outcomes

### HTML conversation segmented normally

```json
{
  "state": "normal",
  "selected_body": "html",
  "segmentation_path": "html",
  "client_hint": "gmail",
  "confidence": "high",
  "fallback_used": null,
  "warnings": []
}
```

### Plain-text message on the normal path

```json
{
  "state": "normal",
  "selected_body": "plain",
  "segmentation_path": "plain_fallback",
  "client_hint": "generic",
  "confidence": "medium",
  "fallback_used": "plain_text_reply_parser",
  "warnings": []
}
```

### Review recommended after low-confidence HTML fallback

```json
{
  "state": "review_recommended",
  "selected_body": "html",
  "segmentation_path": "plain_fallback",
  "client_hint": "gmail",
  "confidence": "low",
  "fallback_used": "plain_text_reply_parser",
  "warnings": []
}
```

### Degraded after operator-enabled HTML failure fallback

```json
{
  "state": "degraded",
  "selected_body": "html",
  "segmentation_path": "plain_fallback",
  "client_hint": "generic",
  "confidence": "low",
  "fallback_used": "html_failure_plain_text_fallback",
  "warnings": [
    {
      "code": "html_markdown_failed",
      "message": "html-to-markdown panic during conversion: ...",
      "severity": "warning"
    }
  ]
}
```

### Degraded after explicit HTML repair retry

```json
{
  "state": "degraded",
  "selected_body": "html",
  "segmentation_path": "html",
  "client_hint": "generic",
  "confidence": "medium",
  "fallback_used": "html_markdown_panic_repaired",
  "warnings": [
    {
      "code": "html_markdown_repaired",
      "message": "html-to-markdown panic during conversion: ...",
      "severity": "warning"
    }
  ]
}
```

### Normal with stripped signature images

```json
{
  "state": "normal",
  "selected_body": "html",
  "segmentation_path": "html",
  "client_hint": "gmail",
  "confidence": "high",
  "fallback_used": null,
  "warnings": [],
  "stripped_images": [
    {"category": "signature_image", "reason": "gmail_signature_wrapper", "reference": "cid:logo.png"},
    {"category": "tracking_pixel", "reason": "dimension_heuristic", "reference": "https://t.example.com/pixel.gif"}
  ]
}
```

## Conversion Grade

The frontend computes a categorical grade from `state` for glanceable trust signal:

- `state: normal` → **Pass** (green, `--ok`)
- `state: degraded` or `state: review_recommended` → **Review** (amber, `--warn`)
- job `status: failed` → **Fail** (red, `--err`)
- diagnostics unavailable → no badge

The grade badge renders inline in the done workspace header with an SVG icon. Diagnostics auto-expand when grade is Review.

`stripped_images` does not affect grade — stripping is expected behavior when enabled. Stripped images are surfaced as a clickable summary below the done counts.
Those entries correspond to assets removed from the retained output, so the same
images may be absent from rendered Markdown and bundle attachment artifacts.

## UI Scope

- Diagnostics appear only for the current polled file job.
- Directory jobs return `diagnostics: null`.
- The frontend can now surface the latest watch-created file job through the same workspace job view, but raw trace inspection remains out of scope in v1.
