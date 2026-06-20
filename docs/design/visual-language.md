# PixlStash Visual Language

This is what PixlStash looks like. Not a mood board, a spec. Every value here is a
token you can build against, and every rule has a reason. Follow it and the product
reads as one considered thing on every screen. Ignore it and you get the drift this
document exists to stop (see `drift-audit-2026-06.md` for what that drift looks like
in numbers).

Owner: lead designer. Anything that changes *behaviour* (a flow, a state, what a
control does) is agreed with the UI/UX expert first. Anything that changes *values*
(color, type, spacing, radius, shadow, motion) is in here, and changes go through the
lead designer.

The companion file `design-tokens.css` is the machine-readable half of this document:
the spacing, radius, type, elevation, and motion tokens, copy-pasteable. Color tokens
live in the Vuetify themes in `frontend/src/main.js`.

---

## 1. The idea behind the look

PixlStash is a self-hosted image library. The screen is mostly a dense grid of the
user's own photos, so **the photos are the color and the chrome stays quiet**. Three
words to hold in your head while you design anything here:

- **Warm.** Never cold LCD grey, never pure `#000`. The neutral ramp is a warm
  near-black on a warm near-white, and the one accent is amber.
- **Quiet.** Chrome recedes. One accent, used sparingly, on the primary action and
  key state. When everything is colored, nothing reads as important.
- **Pixel-honest.** The brand is a pixel-art padlock and a pixel font (Tiny5). That
  heritage shows up in the *brand* moments (wordmark, logo, empty states), never in
  the working UI, which stays clean and legible.

---

## 2. Brand

### Logo
`assets/logo/PixlStash-Logo.png` — a pixel-art padlock in amber/gold whose top-right
edge dissolves into scattered pixels. The padlock says self-hosted and private; the
dissolving pixels are the "stash." It is the source of the amber accent and the pixel
motif. Use it on a clear background; give it clear space equal to the height of one
padlock "stud" on every side. Do not recolor it, add a drop shadow, or sit it on a
busy photo without a solid backing.

### Wordmark
The word **PixlStash** set in Tiny5, rendered by `WordmarkLogo.vue`. Two-tone: "Pixl"
in `currentColor`, "Stash" in `var(--wordmark-accent)` (which falls back to
`currentColor`). Set `--wordmark-accent` to the accent token for the two-tone split,
or leave it for a single-tone wordmark. Size with `font-size` on the host. This is the
*only* place Tiny5 appears in chrome.

### `assets/logo/PixlStash-Watermark.png`
A horizontal lockup (384×64) for footers, exported images, and share pages. Same
rules: clear space, no recolor.

### Favicon
`assets/logo/favicon.ico`. The padlock mark, nothing else.

---

## 3. Typography

One workhorse family carries the whole UI. We do not pair display + text faces; the
pixel face is a brand accent, not a second UI font.

| Role | Token | Stack |
|---|---|---|
| UI / body / everything readable | `--font-ui` | platform system sans |
| Brand wordmark, brand moments | `--font-pixel` | Tiny5 |
| Hashes, tokens, file paths, code | `--font-mono` | platform mono |

**Why system-ui for the UI:** no webfont to load, instant render, and it feels native
inside a dense tool on any OS. A bulk image manager does not need a couture text face;
it needs text that gets out of the way.

**Tiny5 is brand-only.** Wordmark, logo lockups, login/startup splash, empty-state
headline at most. It is a 5-pixel display face: gorgeous as a mark, illegible as body.
Never set a label, button, menu item, or any reading text in Tiny5. (PressStart2P is
retired — if you find it referenced anywhere outside a stale `dist/` build artifact,
it is drift; remove it. Tiny5 is the pixel face going forward.)

### The ramp
Base body is **14px** because the app is dense. Sizes come from `--text-*` in
`design-tokens.css`, **in rem**, off a 16px root. The single biggest typographic drift
in the codebase is mixing `px`, `rem`, and `em`; `em` compounds with its parent and is
why nested labels wander. **Size text from the ramp, in rem, full stop.**

| Token | Size | Use |
|---|---|---|
| `--text-2xs` | 11px | uppercase section labels, badge counts |
| `--text-xs` | 12px | captions, metadata, dense secondary text |
| `--text-sm` | 13px | secondary body, toolbar labels |
| `--text-base` | 14px | **default** body and controls |
| `--text-md` | 16px | emphasised body, dialog body |
| `--text-lg` | 18px | card titles, dialog headings |
| `--text-xl` | 22px | view titles |
| `--text-2xl` | 28px | login / startup / empty-state display |

**Weight:** body 400, medium 500, headings **600** (not 700 — 700 reads heavy on the
warm near-black). 700 is reserved. **Hierarchy comes from weight, color, and space as
much as size** — a 14px 600-weight label in full-strength text over a 13px 400-weight
secondary in 60% text separates cleanly without changing point size.

### The section label
The recurring uppercase label is already a global class. Use it; do not re-roll it:
```css
.section-label { /* in style.css */
  font-size: var(--text-2xs); font-weight: var(--weight-semibold);
  text-transform: uppercase; letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.5);
}
```

### Reading text
Line-height 1.5 for body (`--leading-body`), 1.35 for single-line UI, 1.2 for display.
Keep measure (line length) under ~75 characters for any real paragraph.

---

## 4. Color

Color lives in two Vuetify themes in `frontend/src/main.js`: `pixlStashLight` (the
default) and `pixlStashDark`. They are the source of truth. **You consume them as
`rgb(var(--v-theme-<token>))` and `rgba(var(--v-theme-<token>), <alpha>)`. You never
write a hex literal in a component.** The audit found 38 distinct hardcoded hex values
across components — that is the color drift, and every one of them maps to a token
below.

### The system, not a swatch list
- **Neutrals are warm.** Text is a warm near-black (`#23211d`), never `#000`.
  Backgrounds are a warm near-white (`#faf9f7`), never cold grey. Borders and dividers
  are warm low-contrast lines (`--v-theme-border`, `--v-theme-divider`).
- **Elevation inverts between themes.** In **light**, the content canvas is the
  *brightest* surface and chrome (sidebar, toolbar, panels) recedes to a warm tinted
  grey; raised controls (cards, inputs) go pure white. In **dark**, chrome is a raised
  dark surface and elevation reads by *lightness*, not heavy shadow.
- **One accent.** Amber: `#b0732b` (light) / `#f28f3b` (dark). It marks the primary
  action and key state. Spend it sparingly.

### Token map (what to reach for)
| Need | Token |
|---|---|
| Page / grid canvas | `background` / `onBackground` |
| Raised control surface (card, input, menu) | `surface` / `onSurface` |
| Sidebar, toolbar, panels | `sidebar` / `toolbar` / `panel` (+ their `-text`) |
| Brand accent, key state | `accent` / `onAccent` |
| Primary action button | `primary` / `onPrimary` |
| Secondary / tertiary action | `secondary` / `tertiary` |
| Divider line | `divider` (subtle) / `border` (visible) |
| Success / error / warning / info | `success` / `error` / `warning` / `info` |
| Hover / selection wash | `--hover-wash` / `--active-wash` (in `style.css`) |
| Shadow color (for elevation) | `shadow` |

Status meaning never rides on color alone — pair it with an icon or text. The many
ad-hoc greens (`#1e7d44`, `#258a4d`, `#81c995`…) and reds (`#e53935`, `#c62828`,
`#c5362d`…) in components collapse to the single `success` / `error` tokens.

### Contrast (proven, not eyeballed)
WCAG floors: body text **≥ 4.5:1**, large text and meaningful UI **≥ 3:1**.

Two findings to honour:
- **`primary` (olive `#5c7c0a`) with white text = 4.83:1.** Passes. This is the
  primary-action button color for normal-size labels.
- **`accent` (amber `#b0732b`) with white text = 3.94:1.** Fails the 4.5:1 body floor;
  passes the 3:1 large/UI floor. So **the amber accent is for large labels (≥18px or
  ≥14px bold), icons, borders, and state washes — not a background behind small white
  body text.** Want an amber-backed button with a small label? Darken the amber or use
  `primary`. This is exactly the kind of "looks fine to the eye, fails the checker"
  trap the system exists to catch.

---

## 5. Spacing & layout

Everything sits on a **4px grid**. Padding, margin, and gap come from `--space-*`.
The dominant values in the codebase (4, 8) are already on-grid; the drift is the tail
of 5, 7, 10, 11, 14, 18, 26, 30, 36, 78px. Snap those to the nearest token.

| Token | px | Typical use |
|---|---|---|
| `--space-1` | 2 | hairline inset, optical nudge only |
| `--space-2` | 4 | icon-to-label, chip padding |
| `--space-3` | 8 | default control padding, small gap |
| `--space-4` | 12 | gap inside a group |
| `--space-5` | 16 | gap between groups, card padding |
| `--space-6` | 24 | section spacing |
| `--space-7` | 32 | dialog padding, major section |
| `--space-8` | 48 | page rhythm |
| `--space-9` | 64 | empty-state breathing room |

**Whitespace is structure.** Consistent spacing groups related controls and gives the
eye somewhere to rest. Cramped chrome reads as cheap. Density is earned: the *image
grid* can be tight (it is the user's work), the *controls around it* stay calm.

**Alignment is most of what reads as polished.** Things that belong to the same row
share a baseline; columns of controls share a left edge. The desktop shell already
lines the sidebar header, toolbar, and stats header to a 48px band — respect that kind
of intentional alignment everywhere.

---

## 6. Radius

Four steps and a pill, from `--radius-*`. The codebase had 14 distinct radii; that is
visual noise. Map everything onto:

| Token | px | Use |
|---|---|---|
| `--radius-sm` | 4 | dense controls: chips, small buttons, tight inputs |
| `--radius-md` | 8 | **default**: cards, inputs, menus, image tiles |
| `--radius-lg` | 12 | dialogs, panels, popovers |
| `--radius-pill` | 999 | toggles, status pills, avatar rings |

Keep radii consistent *within* a component family. A card with an 8px outer radius and
a 4px button inside it is correct; an 8px card with a 6px sibling card is drift.

---

## 7. Elevation & shadow

Four levels, `--elevation-1` through `--elevation-4`, **all built on the
`--v-theme-shadow` token** so shadows warm and cool with the theme. The codebase has
58 distinct shadows, most hardcoding `rgba(0,0,0,…)`, which reads cold and flat on the
warm canvas. Stop. Use the ladder:

| Token | Use |
|---|---|
| `--elevation-1` | resting cards, hovered grid tiles |
| `--elevation-2` | menus, dropdowns, raised controls |
| `--elevation-3` | popovers, floating panels |
| `--elevation-4` | dialogs, lightbox chrome |

In **dark mode**, lean on lightness for elevation and keep shadows subtle; a heavy
shadow on a dark surface just muddies. In **light mode**, the warm shadow does the
lifting.

---

## 8. Iconography

**One family: Material Design Icons** (`@mdi/font`, already installed). One family,
one weight, one grid. Mixing icon sets is an instant tell of an unloved UI.

- Default icon size tracks the adjacent text; align icon optical center to the text
  baseline, gap `--space-2` between icon and label.
- Icons inherit `currentColor`. Tint with a theme token, never a hex.
- Brand/source marks (e.g. the Google Photos glyph on the import source) are *content*,
  not chrome icons — they live in their own context and are exempt from the
  one-family rule. Do not pull a third icon set into toolbars, menus, or buttons.

---

## 9. Imagery & the grid

The photos are the hero. The chrome frames them; it does not compete.

- **Consistent aspect handling.** Tiles share a radius (`--radius-md`) and a restrained
  border. No per-tile bespoke framing.
- **States are designed, not defaulted.** Every tile has a real hover, a real selected
  state (`--active-wash` / `--active-bar`), and a focus state (`--focus-ring`). The
  selected state is how bulk work feels confident; make it unambiguous.
- **Empty, loading, error.** Use the existing `Empty.png` / `EmptyTrash.png` art for
  empty states with a `--text-2xl` Tiny5 headline and a `--text-sm` line of guidance.
  Loading is a skeleton at tile dimensions, not a spinner over a blank canvas. These
  three states are where amateur and polished part ways — design all three.

---

## 10. Motion

Motion is feedback. Three durations, one easing, from `design-tokens.css`:

- `--dur-1` (150ms): hover, press, micro-feedback.
- `--dur-2` (200ms): panels, expand/collapse — the default.
- `--dur-3` (250ms): overlays, dialog enter/leave.
- `--ease-standard` for most; `--ease-decelerate` for elements entering the screen.

Nothing on a routine bulk action should animate slower than `--dur-3`. **Respect
`prefers-reduced-motion`** — the token file already enforces it globally; do not
override it.

---

## 11. Focus, hover, selected (the small stuff that is the class)

These get skipped and that is exactly why a UI looks cheap.

- **Focus:** every focusable element shows `--focus-ring`. Never remove an outline
  without replacing it. This is a keyboard user's only cursor.
- **Hover:** `--hover-wash` (accent-tinted on light, on the chrome surfaces). Subtle.
- **Selected:** `--active-wash` fill plus `--active-bar` edge and `--active-text`.
  Tuned per theme in `style.css` because the same alpha reads differently on a
  near-white canvas than a dark one.
- **Disabled:** drop to ~38% opacity of the token, never a different grey.

---

## 12. Using this system

1. Reach for a token before you type a value. Spacing, radius, type, elevation, motion
   are in `design-tokens.css`; color is `rgb(var(--v-theme-*))` from `main.js`.
2. If the value you want is not a token, you almost certainly want the nearest token.
   If you genuinely need a new one, that is a design decision — raise it with the lead
   designer, do not inline a one-off.
3. Anything that changes on-screen behaviour or a flow goes past the UI/UX expert.
4. Hand the frontend exact values, not adjectives. "`--space-5` padding, `--radius-md`,
   `--elevation-2`," not "a bit more room and rounder corners."

See `drift-audit-2026-06.md` for the current gap between this spec and the codebase,
and the order to close it in.
