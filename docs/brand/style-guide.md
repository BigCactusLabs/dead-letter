---
title: dead-letter Style Guide
doc_type: style_guide
status: current
last_updated: 2026-03-16
audience:
  - maintainers
  - frontend contributors
scope:
  - src/dead_letter/frontend
  - docs/brand
---

# dead-letter Style Guide

Brume is the canonical UI language for dead-letter. This guide documents the production token set and component system used by `src/dead_letter/frontend/index.html` and `src/dead_letter/frontend/static/styles.css`.

`src/dead_letter/frontend/style-guide.html` is a legacy exploratory artifact. It is not canonical and must not override this guide or the production CSS.

---

## 1. Design Principles

1. **Precision over decoration.** Every surface, label, and line communicates state.
2. **Data-forward hierarchy.** Counts, paths, and status labels are primary content.
3. **Accumulated-edge aesthetic.** Sharp corners, thin separators, and compact spacing create a command-center feel.
4. **State-first layout.** One workspace region morphs between idle, converting, done, and settings.
5. **Accessible by default.** Keyboard navigation, focus-visible styling, and AA-minded color choices.

---

## 2. Color System

### Core Tokens

| Token | Hex | Role |
|---|---|---|
| `--bg` | `#0d1117` | App background |
| `--panel` | `#161b22` | Panel surface |
| `--panel-hover` | `#1c2128` | Interactive hover surface |
| `--line` | `#30363d` | Borders and separators |
| `--ink` | `#e6edf3` | Primary text |
| `--muted` | `#8b949e` | Secondary text |
| `--faint` | `#484f58` | Placeholder text |
| `--accent` | `#5870a8` | Primary accents, focus, progress |
| `--accent-2` | `#6b9db5` | Secondary accents and watch indicators |
| `--counter` | `#d8a050` | Counter badges and warning accents |
| `--ok` | `#4d9960` | Success |
| `--warn` | `#d8a050` | Warning |
| `--err` | `#b85a6a` | Error |

### Contrast Reference

Measured against `--bg` (`#0d1117`) and `--panel` (`#161b22`):

| Color | Contrast on `--bg` | Contrast on `--panel` | Notes |
|---|---|---|---|
| `--ink` (`#e6edf3`) | 16.02:1 | 14.64:1 | AAA body text |
| `--muted` (`#8b949e`) | 6.15:1 | 5.62:1 | AA for secondary text |
| `--accent` (`#5870a8`) | 3.87:1 | 3.54:1 | Border/large text/focus use |
| `--accent-2` (`#6b9db5`) | 6.41:1 | 5.86:1 | AA info text |
| `--counter` (`#d8a050`) | 8.17:1 | 7.47:1 | High-visibility metric text |

Rule: avoid `--accent` for small body copy on panel backgrounds; prefer `--ink` or `--accent-2`.

---

## 3. Typography

### Font Stacks

```css
--sans: Inter, "Geist", system-ui, -apple-system, sans-serif;
--mono: "IBM Plex Mono", "Source Code Pro", "Fira Mono", Menlo, Consolas, monospace;
```

### Scale

| Token | Size |
|---|---|
| `--font-size-2xs` | `0.6rem` |
| `--font-size-xs` | `0.65rem` |
| `--font-size-sm` | `0.75rem` |
| `--font-size-base` | `0.85rem` |
| `--font-size-lg` | `0.95rem` |

Usage guidance:
- Sans for titles, labels, and action controls.
- Mono for counts, status labels, diagnostics, and file-like metadata.
- Keep heading hierarchy tight; Brume favors compact vertical rhythm.

---

## 4. Spacing and Layout

### Spacing Tokens

| Token | Value |
|---|---|
| `--space-1` | `3px` |
| `--space-2` | `6px` |
| `--space-3` | `8px` |
| `--space-4` | `12px` |
| `--space-6` | `16px` |

### Layout Model

- Shell width: `min(720px, 92vw)`.
- Vertical stacking order: header bar, status strip, workspace, read-only paths strip.
- Workspace is the primary interaction plane and must preserve minimum height during state transitions.
- Mobile breakpoint: `600px`; status cards stack vertically and settings grid becomes one column.

---

## 5. Component Catalog

### Structural Components

- Header bar (`.header-bar`) with logo, watch indicator, and settings trigger.
- Status strip (`.status-strip`) with three cards: Inbox, Cabinet, Watch.
- Watch card is an interactive status control, not just a passive metric tile.
- Active Watch state uses the accent edge treatment plus an animated perimeter trace; reduced-motion users keep the static active edge only.
- Workspace (`.workspace`) that renders one of: drop zone, converting panel, done panel, settings panel.
- Read-only paths strip (`.paths-readout`) beneath the workspace showing current Inbox and Cabinet paths.

### Workspace States

- Idle: dashed drop zone with click-to-browse input.
- Converting: progress track, current file, result counts, cancel action.
- Done: aggregate result counts from history, grade badge (Pass/Review/Fail), status-colored Last Job filename, stable Cabinet output path, stripped images summary, report path, expandable error detail, diagnostics toggle (auto-expanded for Review), history disclosure with scrollable job list and per-row drill-down, next-drop hint.
- Settings takeover: two-column configuration surface for paths, watch, options, and manual job controls.

### Shared Interaction Components

- Error row (`.error-row`) and op-message rows (`.op-error-item`, `.op-notice-item`).
- Done info pair (`.done-info-pair`, `.done-info-row`) — stacked label+value rows for Last Job and Output.
- History disclosure (`.diag-bar` reuse) — collapsed shows job count and error count; expanded shows scrollable job list.
- Job row (`.job-row`) — 4-column grid with status dot, filename, error badge, relative time. Expandable inline drill-down.
- Brume scrollbar (`.brume-scroll`) — thin 4px scrollbar utility using `--line`/`--faint` tokens.
- Grade badge (`.grade-badge`) — inline-flex monospace label with 1px border, `--font-size-xs`, 2px/6px padding. Variants: `.grade-pass` (`--ok`), `.grade-review` (`--warn`), `.grade-fail` (`--err`). Contains a 10px SVG icon (`.grade-icon`, `aria-hidden`) plus text label.
- Stripped images summary (`.stripped-images-summary`) — `--font-size-2xs`, `--faint` color, clickable to expand diagnostics.
- Stripped images disclosure (`.diag-stripped-images`, `.diag-stripped-row`) — 2-column grid showing category, reason, and reference per stripped image.
- Diagnostics grid (`.diag-grid`) with state-specific color classes.
- Toggle switch (`.toggle`) and mode toggle (`.mode-toggle`).
- Meta toggle card (`.meta-card`) — panel-surface card (`--panel` bg, `1px solid --line` border) used for bulk-toggling option groups. States: `.active` (accent border when all controlled options checked), `.indeterminate` (accent border when some checked). Contains `.meta-card__icon` (10×10, shows ✓/–/empty) and `.meta-card__label` (`--accent-2` text). Two cards sit in a `.meta-toggle-row` flex container. Use `--accent` border for Strip junk, `--accent-2` border for Verbose output (via `.active--accent-2` / `.indeterminate--accent-2` modifiers).
- Option section title (`.option-section-title`) — `--font-size-2xs`, uppercase, `--faint` color, dashed bottom border. Separates domain groups (Content, Images, Output, Behavior) within the option grid. `.option-section-title--spaced` adds top margin for subsequent sections.
- Controlled option accent borders (`.opt--strip-active`, `.opt--verbose-active`) — 2px left border on option labels when their checkbox is checked, indicating meta toggle group membership. Strip uses `--accent`, Verbose uses `--accent-2`.
- Buttons:
  - `.btn-primary`
  - `.btn-ghost`
  - `.btn-cancel`

Removed from the design system:
- Hero panel, browser popover, breadcrumb browser rows, decorative background orbs, gradient panels, and legacy meter styles.

---

## 6. Borders, Radius, and Surfaces

- Corner radius is intentionally zero across panels and controls.
- Standard border is `1px solid var(--line)`.
- Drop affordances use dashed borders.
- Surfaces are flat solids only; no gradient or orb background treatment in Brume.

---

## 7. Focus and Keyboard

- Global focus style:
  - `outline: 2px solid var(--accent)`
  - `outline-offset: 2px`
- Toggle controls must remain keyboard operable with Enter/Space.
- Settings panel must close on Escape.

---

## 8. Do and Do Not

Do:
- Prefer panel-based composition with thin lines and compact spacing.
- Keep status data visible and legible with mono typography.
- Use semantic colors (`--ok`, `--warn`, `--err`) for state feedback.

Do not:
- Reintroduce warm light palettes, rounded cards, gradients, or decorative glows.
- Add parallel layout regions that duplicate the workspace state machine.
- Use large spacing scales that break the dense command-center rhythm.

---

## 9. Brand Mark

Full mascot and asset documentation lives in `docs/brand/handoff.md`. This section covers the production integration.

### Primary Mark

The Carrier bust portrait (`docs/brand/sources/logo-mark-source.png`, job `0b2b0da1`). Hooded cybernetic mail carrier with amber ring-eyes, twin antennae, gray cloak with circuit board panels. Used for README hero, GitHub avatar, package page.

### Favicon Set

Angular hood + amber eyes (job `07796a30` exports). Multi-format raster served from `src/dead_letter/frontend/static/`:

| File | Size | Context |
|---|---|---|
| `favicon-192x192.png` | 192×192 | Android/PWA, Apple touch icon |
| `favicon-128x128.png` | 128×128 | Chrome Web Store, high-DPI |
| `favicon-64x64.png` | 64×64 | Windows site tiles |
| `favicon-32x32.png` | 32×32 | Standard browser favicon |
| `favicon.ico` | 16/32/48 | Legacy multi-size bundle |

No SVG fallback — Midjourney raster output has atmospheric depth that SVG cannot reproduce.

Favicon markup in `index.html`:

```html
<link rel="icon" href="/static/favicon.ico" sizes="any">
<link rel="icon" href="/static/favicon-32x32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="/static/favicon-192x192.png">
```

### Header Lockup

32px favicon PNG (displayed at 18×18 CSS pixels) + "dead-letter" mono wordmark in `.header-bar`. Icon is decorative (`alt=""`), text carries semantic meaning.

### Mascot Color Mapping

The mascot palette maps directly to Brume UI tokens:

| Mascot Element | Token | Hex |
|---|---|---|
| Cloak body | `--panel` / `--faint` | `#161b22` / `#484f58` |
| Face void | `--bg` | `#0d1117` |
| Eyes (ring + glow) | `--counter` | `#d8a050` |
| Hood trim, piping | `--accent` | `#5870a8` |
| Borders, seam lines | `--line` | `#30363d` |
| Hardware panel surface | `--panel` | `#161b22` |

**Color rule:** The mascot uses exactly two accent colors — amber (`--counter`) and steel blue (`--accent`). Teal (`--accent-2`), rose (`--err`), and green (`--ok`) are reserved for UI state and do not appear on the character.

**Favicon color note:** The favicon source renders blue hood trim closer to cyan than canonical `--accent`. Acceptable at favicon scale — subpixel detail reads as "cool accent" without being color-pickable.

### Postmark Seal (Archived)

The original postmark seal (`DL` center, concentric rings, "DEAD LETTER / OFFICE" arc text) has been removed from active serving. It remains in git history and is available for future typographic or watermark contexts.
