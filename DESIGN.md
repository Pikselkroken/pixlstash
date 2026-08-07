<!--
  AGENT-FACING DESIGN RECORD.
  COLOR: the unified palette (Camp B — one brand palette shared by both themes,
  warm-white labels). The port landed: frontend/src/main.js (pixlStashLight /
  pixlStashDark) carries these exact values and is the runtime source. This file
  is the agent-readable record of them; main.js wins if they ever disagree.
  Non-color scales (spacing, radius, type, elevation, motion) live in one file
  only — frontend/src/styles/design-tokens.css, which docs/design/design-tokens.css
  symlinks to. Rationale in docs/design/visual-language.md.
-->
---
name: PixlStash
description: A warm, quiet, dark-led library for reviewing AI-generated images at volume — the photos are the color, the chrome stays out of the way.
colors:
  amber: "#c47a1e"
  amber-glow: "#e08a2a"
  olive: "#567309"
  raspberry: "#bb3566"
  teal: "#46707a"
  warm-near-black: "#23211d"
  warm-near-white: "#faf9f7"
  raised-white: "#ffffff"
  warm-tinted-grey: "#f0ede9"
  cancel-surface: "#e6e1d8"
  warm-border: "#d8d3c8"
  warm-divider: "#e8e4dc"
  on-fill: "#f7f1ea"
  error: "#b0392b"
  warning: "#e8912f"
  warning-on: "#1b1b1b"
  success: "#2a7d3e"
  info: "#2f6690"
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
  button-primary:
    backgroundColor: "{colors.amber}"
    textColor: "{colors.on-fill}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  button-commit:
    backgroundColor: "{colors.olive}"
    textColor: "{colors.on-fill}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  button-cancel:
    backgroundColor: "{colors.cancel-surface}"
    textColor: "{colors.warm-near-black}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  input:
    backgroundColor: "{colors.raised-white}"
    textColor: "{colors.warm-near-black}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  chip:
    backgroundColor: "{colors.warm-tinted-grey}"
    textColor: "{colors.warm-near-black}"
    rounded: "{rounded.sm}"
    padding: "4px 8px"
  card:
    backgroundColor: "{colors.raised-white}"
    textColor: "{colors.warm-near-black}"
    rounded: "{rounded.md}"
    padding: "16px"
---

# Design System: PixlStash

## Overview

**Creative North Star: "The Quiet Darkroom"**

PixlStash is a self-hosted library where people who generate AI images at volume triage, score, and organize thousands of frames. The screen is mostly a dense grid of the user's own pictures, so the design works like a darkroom: the chrome dims to a warm safelight and the photos are the only thing that glows. Nothing in the interface competes with the work. The single amber accent is the safelight — used on the primary action and the key state, and almost nowhere else.

The system is **warm, quiet, and pixel-honest**. Warm: the neutral ramp is a warm near-black on a warm near-white, never cold LCD grey and never pure `#000`/`#fff`; the one accent is amber. Quiet: chrome recedes, one accent spent sparingly, hierarchy carried by weight, color, and space before size. Pixel-honest: the brand is a pixel-art padlock and the Tiny5 pixel face, and that heritage appears **only** in brand moments (wordmark, logo, splash, empty-state headline) — never in working UI, which stays clean system-sans.

Both a light and a dark theme are first-class and every decision must hold in both, but the **design is led from dark** — that is where volume review actually happens. Confirmed anti-references: no cold grey, no pure black or white, no second UI typeface (PressStart2P is retired; Tiny5 is brand-only), no per-tile bespoke framing, no icon set other than Material Design Icons in the chrome. The current honest weakness is drift, not taste: sizes, alignment, and colors have wandered off-token across components, and closing that gap is the active priority.

**Key Characteristics:**
- The photos are the color; the chrome is a warm, dim frame around them.
- One amber accent, spent sparingly, on primary action and key state.
- Warm neutrals only — near-black on near-white, never cold grey or pure black/white.
- Dense by design (14px base), but density is earned by the grid, not the controls.
- Polish lives in designed focus / hover / selected / empty / loading states.
- Every value comes from a token; a new value is a design decision, not an inline tweak.

## Colors

The palette is **unified across both themes**: the four brand hues and the four status colors are a single hex value shared by dark and light, each deep enough to carry the warm near-white label (`on-fill` #f7f1ea) at ≥4.5:1. Only the **neutrals** switch per theme. Warm neutrals carry ~95% of every screen, the photos are the color, and the brand hues are spent sparingly on top. Consumed as tokens (`var(--accent)`, `rgb(var(--v-theme-*))`), **never a hex literal in a component.**

### Brand
- **Amber** (`accent` #c47a1e; glow #e08a2a): the one "safelight." The primary action, the selected/active state, focus ring, key emphasis. The accent was brightened from the old deep #9c6016 to this warmer, more-orange #c47a1e (2026-07-24) so it reads as amber, not brown; the brighter #e08a2a is used where amber is a *glow* not a fill (selection wash, focus ring, hover). Warm-white label contrast is ~3:1 — enough for the semibold button label (AA large), but drop to olive or dark text for small text on amber.
- **Olive** (`primary` #567309): the "commit / go" action (Create, Apply, Save changes) and the good/high end of a scale (smart-score, tag coverage). Distinct from the default amber primary.
- **Raspberry** (`secondary` #bb3566): category / identity (person accents, grouping chips). A label color, never an action.
- **Teal** (`tertiary` #46707a): the quiet tertiary — a third category color, neutral-accent highlights, chart series.

### Neutral (switches per theme — `light / dark`)
- **Text** #23211d / #f2e5da and **canvas** #faf9f7 / #1b1f24 — warm, never `#000`/`#fff`.
- **Raised surface** #ffffff / #23282f · **chrome** #f0ede9 / #23282f · **border** #d8d3c8 / #363d45 · **divider** #e8e4dc / #2c323a.
- **Warm-white label** #f7f1ea: text/icon on any deep brand or status fill (except warning).
- **Cancel** (neutral secondary): #e6e1d8 + warm-black (light) / #3a4047 + cream (dark).

### Status (semantic only)
- **Error** #b0392b (warm brick) · **Warning** #e8912f (bright orange, **dark text** #1b1b1b) · **Success** #2a7d3e (forest) · **Info** #2f6690 (muted slate-blue). One token each; they appear only on their own meaning.

### Color usage policy
Neutrals carry ~95% of the screen; never more than ~2–3 of these colors visible at once on a working screen.

| Color | Role | Use for | Not for |
|---|---|---|---|
| **Amber** (accent) | The one safelight | Primary action; selected/active; focus; key emphasis (≤~10% of a screen) | Decoration; two amber actions competing |
| **Olive** (primary) | Commit / go | Create / Apply / Save; the good-high end of a scale | A generic button color |
| **Raspberry** (secondary) | Category / identity | Person accents, grouping chips | Actions |
| **Teal** (tertiary) | Quiet category | A third grouping color, neutral highlights, chart series | High emphasis |
| **Error** | Destructive / error | Delete, error states, penalised tags | Decoration; color alone |
| **Warning** | Caution | Stale / needs-review, non-blocking warnings | Blocking errors |
| **Success** | Success / complete | Done confirmations, completed reviews | A general action button |
| **Info** | Neutral information | Notices, links, tooltips | Emphasis or actions |
| **Cancel gray** | Low-emphasis secondary | Cancel, dismiss, "not now" | Anything you want noticed |
| **Wordmark amber** | Logo | The "Stash" wordmark (#c47a1e) | Anywhere in the working UI |

### Named Rules
**The One Safelight Rule.** Amber marks intent, not decoration. One amber action per view, ≤~10% of a screen. Its rarity is the point.

**The Unified-Palette Rule.** Brand and status colors are the same hex in both themes; only neutrals flip. A theme-specific brand color is drift.

**The Warm-White-Label Rule.** Text on any deep brand/status fill is the warm-white `on-fill` (#f7f1ea), never pure `#fff`. The one exception is Warning, whose bright orange takes dark text (#1b1b1b) — the hazard convention.

**The Status-Is-Not-Color-Alone Rule.** Error/warning/success/info always pair color with an icon or text; color never carries meaning by itself, and status colors never appear as decoration.

**The Categorizers-Label-Not-Act Rule.** Raspberry and teal identify and group; they never sit on a button.

**The No-Hex Rule.** A hex literal in a component is drift. Every color is a token; if the color you want isn't one, you want the nearest one.

**The Warmth Rule.** Never cold grey, never pure `#000`/`#fff`; text, canvas, borders, and shadows all stay warm.

## Typography

**UI / body font:** platform system sans (`system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`) — no webfont load, instant render, native feel in a dense tool.
**Brand font:** Tiny5 pixel face (`--font-pixel`) — wordmark, logo lockups, splash, empty-state headline **only**.
**Mono font:** platform mono (`--font-mono`) — hashes, tokens, file paths, code.

**Character:** one workhorse family carries the entire working UI; the pixel face is a brand accent, not a second UI typeface. Text should get out of the way of the images.

### Hierarchy
Base body is **14px** because the app is dense; 16px body would waste vertical space in the grid chrome. Size text **only** from the ramp and **only in rem** (off a 16px root, so user zoom scales it). Between the roles below sit intermediate steps used for secondary/dense text: `--text-xs` (12px) captions/metadata, `--text-sm` (13px) secondary body/toolbar labels, `--text-md` (16px) emphasised/dialog body.

- **Display** (600, 1.75rem/28px, line-height 1.2): login, startup, empty-state headlines. The largest type in the product.
- **Headline** (600, 1.375rem/22px, 1.2): view titles.
- **Title** (600, 1.125rem/18px, 1.35): card titles, dialog headings.
- **Body** (400, 0.875rem/14px, 1.5): default body and controls. Keep measure under ~75 characters for real paragraphs.
- **Label** (600, 0.6875rem/11px, letter-spacing 0.06em, UPPERCASE): the recurring section label. Use the global `.section-label` class — do not re-roll it.

### Named Rules
**The rem-Only Rule.** Size type in rem, from the ramp. Never `em` (it compounds with its parent and is the main reason nested labels drift), never a raw px one-off. This is the single biggest typographic drift to hold the line on.

**The 600-Not-700 Rule.** Headings are weight 600. 700 reads heavy against the warm near-black and is reserved for rare true emphasis. Hierarchy comes from weight, color, and space at least as much as from size.

**The Tiny5-Is-Brand-Only Rule.** The pixel face never sets a label, button, menu item, or any reading text — it is gorgeous as a mark and illegible as body. Brand moments only.

## Layout

Everything sits on a **4px grid**. Padding, margin, and gap come from the `--space-*` scale (2 / 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64px); `--space-1` (2px) is the only sub-4 step, for hairline insets and optical nudges only. Off-grid values (5, 7, 10, 11, 14, 18, 26, 30, 36px) are drift — snap to the nearest token.

The product is a desktop shell: a left sidebar (folders / navigation), a top toolbar, a central image grid, and a right stats/detail area, with full-screen overlays for the viewer and dialogs. The shell aligns the sidebar header, toolbar, and stats header to a shared **48px band** — respect that kind of intentional alignment everywhere: things in the same row share a baseline, columns of controls share a left edge. Alignment is most of what reads as polished.

**Density is earned.** The image grid can be tight — it is the user's work. The controls *around* it stay calm and well-spaced; cramped chrome reads as cheap. Whitespace is structure: consistent spacing groups related controls and gives the eye somewhere to rest.

**Responsive:** desktop-first and desktop-primary (Vuetify's breakpoint system underneath). Mobile web is a secondary target that should degrade acceptably, not a first-class layout yet — do not sacrifice desktop density to chase it.

## Elevation & Depth

Depth is **warm and restrained**, built on four levels (`--elevation-1`…`--elevation-4`), all composed on the theme `--v-theme-shadow` token so shadows warm and cool with the theme — never a hardcoded `rgba(0,0,0,…)`, which reads cold and flat on the warm canvas. Elevation **inverts between themes**: in light, the content canvas is the brightest surface and a warm shadow does the lifting; in dark, elevation reads by *lightness* (raised surfaces get lighter) and shadows stay subtle.

### Shadow Vocabulary
- **Level 1** (`--elevation-1`, `0 1px 2px rgba(var(--v-theme-shadow), .12)`): resting cards, hovered grid tiles.
- **Level 2** (`--elevation-2`, `0 2px 6px …/.18`): menus, dropdowns, raised controls.
- **Level 3** (`--elevation-3`, `0 4px 16px …/.22`): popovers, floating panels.
- **Level 4** (`--elevation-4`, `0 8px 28px …/.30`): dialogs, lightbox chrome.

### Named Rules
**The Warm-Shadow Rule.** Every shadow is built on `--v-theme-shadow`. Never hardcode a shadow color; a cold shadow on a warm canvas is an instant tell.

**The Dark-Leans-On-Lightness Rule.** In dark mode, convey elevation by making the surface lighter, not by stacking a heavier shadow (which just muddies).

## Shapes

Four corner steps and a pill, from `--radius-*`: **sm 4px** (dense controls — chips, small buttons, tight inputs), **md 8px** (the default — cards, inputs, menus, image tiles), **lg 12px** (dialogs, panels, popovers), **pill 999px** (toggles, status pills, avatar rings). Borders are warm and low-contrast (`border` / `divider`). Corners are gently rounded, never sharp and never fully soft — the 8px default is the product's resting geometry.

**The Consistent-Radius Rule.** Radii stay consistent *within* a component family. An 8px card with a 4px button inside it is correct; an 8px card beside a 6px sibling card is drift.

## Components

The UI is built on **Vuetify** components themed by the palette above, plus scoped patterns. Lead with the token, not a bespoke value.

### Buttons
- **Shape:** gently rounded (`--radius-sm` 4px for dense controls).
- **Primary:** Olive (`primary`) fill, white label, `--space-3`/`--space-5` padding. Olive (not amber) so a small label clears AA contrast.
- **Cancel / secondary:** warm `cancel-button` surface (#e6e1d8 / #3a4047), warm-near-black label.
- **Hover / focus:** hover lifts subtly with `--hover-wash` and `--dur-1` motion; focus always shows `--focus-ring` (3px accent-tinted). Never remove an outline without replacing it.

### Chips
- **Style:** warm-tinted-grey or surface background, warm-near-black text, `--radius-sm`, `--space-2` padding.
- **Over imagery:** a chip sitting on a photo uses a scrim backing for legibility — `--scrim-surface` (light warm chip on the bright grid canvas, dark glyph) or `--scrim-photo` (dark chip directly over an arbitrary photo, light glyph). Match the sibling chips already on that surface. Never a raw `rgba(0,0,0,…)`.

### Cards / Containers
- **Corner:** `--radius-md` (8px).
- **Background:** Raised White (`surface`) above the canvas.
- **Elevation:** `--elevation-1` at rest (see Elevation & Depth).
- **Border:** warm `border`/`divider` when a line is needed.
- **Padding:** `--space-5` (16px) internal.

### Inputs / Fields
- **Style:** Raised White (`surface`) background, warm border, `--radius-md`.
- **Focus:** `--focus-ring` (3px accent glow), not a bare browser outline.
- **Disabled:** drop to ~38% opacity of the token — never swap to a different grey.

### Navigation (sidebar)
- **Style:** warm-tinted-grey chrome that recedes; system-sans labels; section headers in `.section-label`.
- **Hover:** `--hover-wash`. **Selected:** `--active-wash` fill + `--active-bar` edge + `--active-text`, tuned per theme. **Focus:** `--focus-ring`.

### Image Grid Tile (signature)
The defining component. Uniform tiles share `--radius-md` and a restrained warm border — no per-tile bespoke framing. Every tile designs three states beyond default: **hover** (`--elevation-1` lift), **selected** (`--active-wash` + `--active-bar` — this is how bulk work feels confident, so it must be unambiguous), and **focus** (`--focus-ring`). Loading is a skeleton at tile dimensions, not a spinner over a blank canvas. Empty states use the existing `Empty.png` / `EmptyTrash.png` art with a Tiny5 `--text-2xl` headline and a `--text-sm` line of guidance.

## Do's and Don'ts

### Do:
- **Do** reach for a token before typing a value — color as `rgb(var(--v-theme-*))`, everything else from `--space-*` / `--radius-*` / `--text-*` / `--elevation-*` / `--dur-*`. A genuinely new value is a design decision, raised with the lead designer, not an inline tweak.
- **Do** size type from the ramp, in rem, at 14px base; headings at weight 600.
- **Do** design focus, hover, selected, empty, and loading states for anything interactive — that is where polish lives.
- **Do** validate both themes on every change, and **lead from dark** since that is where review happens; keep light fully first-class.
- **Do** align to the grid and to the 48px shell band — same row shares a baseline, columns share a left edge.
- **Do** use `--focus-ring` on every focusable element; keyboard flow is a product requirement here, not a nicety.

### Don't:
- **Don't** hardcode a hex color, a `rgba(0,0,0,…)` shadow/scrim, or an off-grid size/radius — those are exactly the size/alignment/color drift to eliminate.
- **Don't** size text in `em`, or introduce a point size outside the ramp.
- **Don't** set UI, labels, or body text in Tiny5 — brand moments only — and don't reintroduce PressStart2P.
- **Don't** spend the amber accent broadly; one safelight, used sparingly.
- **Don't** pull in a second icon set — Material Design Icons only in the chrome.
- **Don't** let chrome compete with the photos; when in doubt, make the chrome quieter.
