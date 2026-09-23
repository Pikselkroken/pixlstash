<!--
  AGENT-FACING DESIGN RECORD.
  SOURCE OF TRUTH: the Claude Design project "PixlStash Design System"
  (https://claude.ai/design/p/ac544c9e-b278-4439-be75-e442fca29d41), read with
  the DesignSync tool: tokens/colors.css, tokens/typography.css,
  tokens/spacing.css, readme.md and ui_kits/app/unified-shell.html. This file
  mirrors it for agents. When this file, frontend/src/main.js or
  frontend/src/styles/design-tokens.css disagree with the design system, the
  design system wins and the repo is drift: fix the repo, not the design.
  The shipped app is still behind it in places; see "Where the app is behind".
-->
---
name: PixlStash
description: A warm, quiet, dark-led library for reviewing AI-generated images at volume — the photos are the color, the chrome stays out of the way.
colors:
  amber: "#c47a1e"
  amber-light: "#9e6727"
  amber-on: "#ffffff"
  olive: "#567309"
  olive-lifted: "#8ea604"
  raspberry: "#bb3566"
  teal: "#46707a"
  violet: "#7c55ae"
  on-fill: "#f7f1ea"
  error: "#b0392b"
  warning: "#e8912f"
  warning-on: "#1b1b1b"
  success: "#2a7d3e"
  info: "#30558c"
  dark-bg: "#1b1f24"
  dark-surface: "#23282f"
  dark-panel: "#313337"
  dark-input: "#2b3138"
  dark-border: "#363d45"
  dark-divider: "#2c323a"
  dark-text: "#f2e5da"
  dark-cancel: "#3a4047"
  light-bg: "#faf9f7"
  light-surface: "#ffffff"
  light-panel: "#efede9"
  light-chrome: "#f0ede9"
  light-border: "#d8d3c8"
  light-divider: "#e8e4dc"
  light-text: "#23211d"
  light-cancel: "#e6e1d8"
  surface-error-dark: "#eda79c"
  surface-warning-dark: "#e2b05a"
  surface-success-dark: "#7ec892"
  surface-info-dark: "#9fbce8"
  surface-error-light: "#9a3327"
  surface-warning-light: "#755215"
  surface-success-light: "#226534"
  surface-info-light: "#30558c"
typography:
  display:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "normal"
  headline:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "normal"
  title:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "normal"
  body:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "0.06em"
rounded:
  sm: "4px"
  md: "8px"
  lg: "12px"
  pill: "999px"
spacing:
  "1": "2px"
  "2": "4px"
  "3": "8px"
  "4": "12px"
  "5": "16px"
  "6": "24px"
  "7": "32px"
  "8": "48px"
  "9": "64px"
components:
  button-key:
    backgroundColor: "{colors.amber}"
    textColor: "{colors.amber-on}"
    rounded: "{rounded.sm}"
    height: "28px"
    padding: "0 16px"
  button-neutral:
    backgroundColor: "{colors.dark-cancel}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.sm}"
    height: "28px"
    padding: "0 16px"
  button-danger:
    backgroundColor: "{colors.error}"
    textColor: "{colors.on-fill}"
    rounded: "{rounded.sm}"
    height: "28px"
    padding: "0 16px"
  bar-button:
    backgroundColor: "transparent"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.sm}"
    height: "32px"
    padding: "0 8px"
  input:
    backgroundColor: "{colors.dark-input}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.sm}"
    height: "28px"
    padding: "0 8px"
  chip:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  card:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.md}"
    padding: "16px"
  dialog:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.lg}"
    padding: "24px"
---

# Design System: PixlStash

## Overview

**Creative North Star: "The Quiet Darkroom"**

PixlStash is a self-hosted library where people who generate AI images at volume triage, score, and organize thousands of frames. The screen is mostly a dense grid of the user's own pictures, so the design works like a darkroom: the chrome dims and the photos are the only thing that glows. Nothing in the interface competes with the work.

The system is **warm, quiet, and pixel-honest**. Warm: the neutral ramp is a warm near-black on a warm near-white, never pure `#000`/`#fff` for text. Quiet: chrome recedes, one amber action per surface, hierarchy carried by weight, color and space before size. Pixel-honest: the brand is a pixel-art padlock and the Tiny5 pixel face, which sets the **wordmark only**; working UI, headlines and empty states stay system sans.

Both themes ship and every decision must hold in both, but the app **defaults to dark and is designed dark-first**. `:root` in the design system's `tokens/colors.css` *is* the dark palette; light is `[data-theme="light"]`.

**Key Characteristics:**
- The photos are the color; the chrome is a warm, dim frame around them.
- **Amber acts. Olive selects. Raspberry, teal and violet only identify.**
- Hover is an ink wash and focus an ink ring; neither carries a hue.
- Dense by design (14px base), but density is earned by the grid, not the controls.
- Every value comes from a token; a new value is a design decision, not an inline tweak.

## Colors

The brand and status hues are **one value shared by both themes**, except amber; otherwise only the neutrals switch. Consumed as tokens (`var(--accent)`, `rgb(var(--v-theme-*))`), **never a hex literal in a component.**

**Contrast floors: text 4:1, icons and marks 3:1** (owner decision, 2026-09-13). 4:1 is enough; do not add tokens or treatments only to lift text from 4 to 4.5:1.

### Brand
- **Amber** (`--accent` #c47a1e dark, **#9e6727 light**, label `--accent-on` **#ffffff**): **the action**. The key action fill (one per surface, including the verb that finishes something), links, the attention dot, and the "Stash" wordmark. Not selection, focus, hover, data or decoration. The only brand hue that differs by theme (owner decision, 2026-09-23, #1413): the shared #c47a1e measured 2.93:1 on the light sidebar, under the 3:1 floor for the attention dot, so light takes the deep amber (4.07:1 on the sidebar, white label 4.75:1). In dark the pure-white label measures 3.41:1, an accepted exception; do not flip the label dark.
- **Olive** (`--primary` #567309, lifted `#8ea604` in dark as `--selected-ink` / `--active-bar`): **selection**, for items and chosen values alike. A selected tile, row, tab or focused group (`--active-bar` ring + `--active-wash`), a selected segment, an option row's check, an active bar button's icon, and the good/high end of a scale. Olive marks, words stay `--text`. **Never a button fill.** Checkbox, switch and slider are deep olive with a white mark in both themes (accepted 2.32–2.72:1 against a dark ground).
- **Raspberry** (`--secondary` #bb3566): category / identity (person accents, grouping chips). Never an action.
- **Teal** (`--tertiary` #46707a): quiet category, the default chart hue. Never an action.
- **Violet** (`--quaternary` #7c55ae): a fourth category and chart hue. Never an action or a selection. It stays apart from Info for colour-blind viewers because Info is darker; do not lighten Info toward it.
- **Warm near-white** (`--ps-on-fill` #f7f1ea): the label on olive, raspberry, violet and the status fills (not amber, not warning).

### Neutral (switches per theme: `dark / light`)
- **Text** #f2e5da / #23211d; **muted** is that text at 55%.
- **Canvas** (`--bg`) #1b1f24 / #faf9f7 · **surface** #23282f / #ffffff · **panel** #313337 / #efede9 · **chrome** #23282f / #f0ede9 · **input** #2b3138 / #ffffff.
- **Border** #363d45 / #d8d3c8 · **divider** #2c323a / #e8e4dc.
- **Cancel** (`--cancel-bg` / `--cancel-text`): #3a4047 + text / #e6e1d8 + text.
- In light, the chrome is *darker* than the canvas; in dark it is lighter.

### Status (semantic only)
- **Error** #b0392b · **Warning** #e8912f (dark label #1b1b1b) · **Success** #2a7d3e · **Info** #30558c. They carry the warm near-white label on their fill (warning excepted) and appear only on their own meaning, always with an icon or text.
- **On words, use the `--surface-*` family, never the fill.** A status fill fails the text floor as text: dark `--surface-error` #eda79c, `--surface-warning` #e2b05a, `--surface-success` #7ec892, `--surface-info` #9fbce8; light #9a3327, #755215, #226534, #30558c. Prefer the notice pattern where you can: the glyph and the rail carry the hue and the sentence stays `--text`.

### Color usage policy
Neutrals carry ~95% of the screen; never more than ~2–3 hues visible at once on a working screen.

| Color | Role | Use for | Not for |
|---|---|---|---|
| **Amber** (accent) | The action | The key action fill; links; attention dot (≤~10% of a screen) | Selection; focus; hover; data; two amber actions competing |
| **Olive** (primary) | Selection | Selected tile, row, tab, segment, option; active bar button; good-high end of a scale | Any button fill |
| **Raspberry** (secondary) | Category / identity | Person accents, grouping chips | Actions |
| **Teal** (tertiary) | Quiet category | A third grouping color, neutral highlights, default chart hue | High emphasis; actions |
| **Violet** (quaternary) | Fourth category | A fourth grouping color, a fourth chart hue | Actions; selection; the only cue that separates it from Info |
| **Error** | Destructive / error | Delete, error states, penalised tags | Decoration; color alone |
| **Warning** | Caution | Stale / needs-review, non-blocking warnings | Blocking errors |
| **Success** | Success / complete | Done confirmations, completed reviews | A general action button |
| **Info** | Neutral information | Notices, tooltips | Emphasis or actions |
| **Cancel gray** | Low-emphasis secondary | Cancel, dismiss, "not now" | Anything you want noticed |

### Data
A chart picks **one** identity hue (teal by default, olive, raspberry or violet) and varies opacity for magnitude within it. Adjacent charts take different hues; hue never varies inside one chart. **Amber never appears in data.** What is yours or current is marked within the chart's own hue, at full strength with its label stated. Status hues appear only on facts.

### Named Rules
**The One Action Rule.** Amber marks intent. One amber action per surface, small footprint. Its rarity is what makes it read as "the thing to do".

**The Olive-Selects Rule.** Olive marks what is chosen, never a button. An olive button reads as a selected option.

**The Unified-Palette Rule.** Brand and status colors are the same hex in both themes; only neutrals flip. A theme-specific brand color is drift.

**The Status-Is-Not-Color-Alone Rule.** Error/warning/success/info always pair color with an icon or text, and never appear as decoration.

**The Categorizers-Label-Not-Act Rule.** Raspberry, teal and violet identify and group; they never sit on a button.

**The No-Hex Rule.** A hex literal in a component is drift. If the color you want isn't a token, you want the nearest one.

## Typography

**UI / body font:** platform system sans (`--font-ui`: `system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`). No webfont load, native feel in a dense tool.
**Brand font:** Tiny5 pixel face (`--font-pixel`), **the wordmark only**. "Pixl" takes the text color, "Stash" takes `--wordmark-accent` amber.
**Mono font:** platform mono (`--font-mono`): hashes, tokens, file paths, config values.
**Website only:** Space Grotesk and IBM Plex Mono belong to pixlstash.dev. Never in the app.

### Hierarchy
Base body is **14px**. Size text **only** from the ramp and **only in rem**: `--text-2xs` 11px (section labels, badge counts) · `--text-xs` 12px (captions, metadata) · `--text-sm` 13px (secondary body, toolbar labels) · `--text-base` 14px (default body and controls) · `--text-md` 16px (emphasised and dialog body) · `--text-lg` 18px (card titles, dialog headings) · `--text-xl` 22px (view titles) · `--text-2xl` 28px (login, startup, empty-state display).

- **Display** (600, 28px, 1.2), **Headline** (600, 22px, 1.2), **Title** (600, 18px, 1.35), **Body** (400, 14px, 1.5).
- **Label** (600, 11px, `--tracking-label` 0.06em, UPPERCASE, `--text-muted`): the one way to name a group. Use the `SectionLabel` component.

### Named Rules
**The rem-Only Rule.** Never `em` for text, never a raw px one-off.

**The 600-Not-700 Rule.** Headings are 600. 700 is reserved.

**The Tiny5-Is-The-Wordmark Rule.** The pixel face never sets a label, button, headline or empty state.

## Layout

Everything sits on a **4px grid**: `--space-0`…`--space-9` = 0, 2, 4, 8, 12, 16, 24, 32, 48, 64px. `--space-1` (2px) is for hairline insets and optical nudges only. Most app spacing is `--space-2`–`--space-5`.

**The shell** is fixed and every screen lives inside it (a task screen is a screen, not an application mode):
- **Title bar** `--titlebar-height` 34px: brand, breadcrumb, window controls.
- **Toolbar** `--toolbar-height` **36px band** holding 32px bar buttons (`--control-h-bar`). Left changes the *view*; right is global to the app. Sidebar tabs and inspector tabs sit on the same band, so one bottom rule runs across the window.
- **Sidebar** `--sidebar-width` 280px on `--chrome`.
- **Inspector** `--stats-width` 288px on `--chrome`: one component for overlay metadata, grid statistics, model detail and the duplicates evidence pane.
- **Content** on `--bg`, edge to edge.

**Control heights:** `--control-h` 28px (buttons, inputs, selects), `--control-h-sm` 24px (compact), `--control-h-bar` 32px (toolbar, selection pill, title bar, lightbox). **Dialog widths:** `--dialog-w-sm` 420 · `--dialog-w-md` 520 (default) · `--dialog-w-lg` 720 · `--dialog-w-xl` 840, and nothing else.

**Density is earned.** The grid can be tight; the controls around it stay calm.

## Elevation & Depth

Four levels, built on the per-theme `--shadow-rgb`, never a hardcoded `rgba(0,0,0,…)`:
- **`--elevation-1`**: hovered tiles.
- **`--elevation-2`**: raised controls.
- **`--elevation-3`**: menus, popovers, tooltips, floating panels.
- **`--elevation-4`**: dialogs, lightbox chrome, the selection pill.

**Cards carry no resting shadow.** Elevation is for things that actually float. Overlays use `--scrim` (`rgba(0,0,0,.80)`), one value for dialogs and the lightbox. No backdrop blur.

## Shapes

**Four radii and a pill, tiered by class of thing:** controls (buttons, inputs, selects, chips, kbd, checkbox, segments) `--radius-sm` 4px · surfaces (cards, menus, tiles, tooltips) `--radius-md` 8px · dialogs, panels and popovers `--radius-lg` 12px · toggles, badges, progress tracks and the selection pill `--radius-pill`. A button and the card it sits on never share a corner. Inner radii nest: outer radius minus inset. `--radius-xl` 22px is website-only.

## Interaction States

- **Hover** is an ink wash, `--hover-wash` (the text color at 16%). Transparent controls take it as background and a muted label or icon goes to `--text`. A selected item layers it over `--active-wash`. Filled colored controls take `background-image: var(--hover-shade)` (20% darken); Cancel takes `--hover-neutral`. Never amber, never a brightness filter.
- **Press:** `--hover-shade`.
- **Selected:** `--active-wash` + olive `--active-bar` edge or ring; label, icon and count stay `--text`.
- **Focus:** the ink ring on everything focusable, `outline: var(--focus-width) solid var(--focus-stroke)` (2px `--text`) with `outline-offset: var(--focus-offset)` (2px), from one global `:focus-visible` rule. Menu rows use the inset form, `box-shadow: inset 0 0 0 2px var(--focus-stroke)`. Never `outline: none` without that replacement.
- **Disabled:** `--opacity-disabled` 0.38, prefer `aria-disabled`. **Pending is not disabled and never dims.**
- **Motion:** `--dur-1` 150ms (hover, press), `--dur-2` 200ms (panels, default), `--dur-3` 250ms (overlays, dialogs), `--ease-standard` / `--ease-decelerate`. No bounces, no springs; reduced motion shows the end state, but spinners keep turning.

## Components

Build on the design system's components and `ui_kits/app/unified.css`; **do not hand-roll a checkbox, switch, segmented control, button, tag, input or star rating.** If a control is missing, add it to the design system.

### Buttons (two dialects)
- **Raised `Button`**: dialogs, panels, forms, settings, popover footers. 28px (24px compact) at `--radius-sm`, weight 500. Roles: **key** (amber, white label, one per surface), **neutral** (`--cancel-bg`), **danger** (`--error`), **quiet** (transparent). No olive variant.
- **Flat `BarButton`**: toolbar, selection pill, undo group, title bar, lightbox. 32px, transparent until hovered, icon-first, regular weight. Supports a count badge (neutral `--cancel-bg`), a dim `Sort:` prefix, and joined split pairs. Open state: `--hover-wash` with a `--border` edge. Active: the icon takes `--selected-ink`, the label stays `--text`.
- **A keyboard hint rides inside the control it triggers** (`Kbd` chip in the button).

### Floating things
A **menu** holds rows: `--surface`, 1px `--border`, `--elevation-3`, `--radius-md`, 32px rows. A **panel** holds controls (the filter popover): `--radius-lg`. A **tooltip** is `--surface`, `--text-sm`, 1px border, `--elevation-3`, `--radius-md`, at most `--tooltip-max-w` 280px, after `--tooltip-delay` 400ms. A menu that needs a control is a panel.

### Selection pill
Acting on a selection means the floating pill: bottom centre, `--radius-pill`, `--elevation-4`. Count first, then round 32px `BarButton` verbs; destructive last in `--surface-error`. Single-item verbs ride along disabled rather than disappearing. The grid, the models list and the dedup queue all use it.

### Cards / Containers
`--radius-md`, 1px `--border`, no resting shadow, no colored left-border accent, `--space-5` padding. Dialogs use the `Dialog` component: `--radius-lg`, `--elevation-4`, `--surface`, 1px `--border`, `--dialog-w-*` widths.

### Forms
`Input` (label, hint, error), `Checkbox` (olive when checked), `Switch` (settings rows), `Segmented` (2–5 options; track `--track-trough` with inset `--track-ring`, selected segment olive), `OptionRows` (long single-select lists: olive check, no fill), `Slider` (olive).

### Image Grid Tile (signature)
Uniform tiles share `--radius-md`; no per-tile bespoke framing. Hover `--elevation-1`; selected `--active-wash` + `--active-bar` ring; focus the ink ring. Loading is a skeleton at tile dimensions.

## Where the app is behind

The shipped app has not caught up with this system everywhere. Treat each of these as drift to fix toward the design, never as the reference:
- Controls are still 27/23px at 8px radius, not 28/24px at `--radius-sm`.
- Olive is still used as a button fill in places, and the active bar label is the deep olive on dark chrome.
- The amber button label is still the warm near-white, not pure white.
- Hover (`--hover-wash`), focus (`--focus-ring`, `--focus-glow`) and, in dark, selection (`--active-bar`, `--active-wash`) are still amber in `style.css` and `styles/design-tokens.css`.
- Light `error`, `warning` and `success` in `main.js`, and dark `error`, differ from the design system's unified status values.

## Do's and Don'ts

### Do:
- **Do** read the design system (`DesignSync`) before building or restyling a surface, and build app screens on `unified.css`.
- **Do** reach for a token before typing a value.
- **Do** design focus, hover, selected, empty, loading and pending states for anything interactive.
- **Do** validate both themes and lead from dark.
- **Do** name groups with the ALL-CAPS section label and nothing else.

### Don't:
- **Don't** fill a button with olive, or use amber for selection, hover, focus or data.
- **Don't** hardcode a hex, a `rgba(0,0,0,…)` shadow or scrim, an off-grid size or an off-tier radius.
- **Don't** size text in `em`, or outside the ramp.
- **Don't** set anything but the wordmark in Tiny5.
- **Don't** pull in a second icon set: Material Design Icons only.
- **Don't** let chrome compete with the photos.
