# PixlStash Design System — Portable Handoff

Self-contained snapshot of the PixlStash visual system, for designing the v1.8.0 UI
in Claude Design without the repo open. Everything you need — token values, both color
themes, the core component patterns, and the design context for the two new v1.8.0
surfaces — is on this page.

Sources of truth (in-repo): tokens `docs/design/design-tokens.css`, spec
`docs/design/visual-language.md`, color `frontend/src/main.js`. This page mirrors them
as of `v1.8.0-foundations` (2026-07). If they diverge later, the repo wins.

---

## 1. The idea in three words

- **Warm.** Never cold LCD grey, never pure `#000`/`#fff`. Warm near-black text on a
  warm near-white canvas. The one accent is amber.
- **Quiet.** The photos are the color; chrome recedes. One accent, spent sparingly on
  the primary action and key state. When everything is colored, nothing reads important.
- **Pixel-honest.** Brand = a pixel-art padlock + the Tiny5 pixel font. Pixel heritage
  shows in brand moments (wordmark, logo, empty-state headline) only — never in working UI.

---

## 2. Token vocabulary (values)

### Spacing — 4px grid (`--space-*`)
`0` · `1`=2px · `2`=4px · `3`=8px · `4`=12px · `5`=16px · `6`=24px · `7`=32px ·
`8`=48px · `9`=64px. Padding / margin / gap only. `--space-1` (2px) is the only sub-4
step (hairline/optical nudge). Off-grid values (5, 7, 10, 11, 14, 18, 26, 30, 36px) are drift.

### Radius (`--radius-*`)
`sm`=4px (chips, small buttons, tight inputs) · `md`=8px (**default**: cards, inputs,
menus, image tiles) · `lg`=12px (dialogs, panels, popovers) · `pill`=999px (toggles,
status pills, badges, avatar rings). Keep radius consistent within a component family.

### Type ramp (`--text-*`, rem off a 16px root — base body is 14px)
| Token | px | Use |
|---|---|---|
| `--text-2xs` | 11 | uppercase section labels, **badge counts** |
| `--text-xs` | 12 | captions, metadata, dense secondary |
| `--text-sm` | 13 | secondary body, toolbar labels |
| `--text-base` | 14 | **default** body & controls |
| `--text-md` | 16 | emphasised body, dialog body |
| `--text-lg` | 18 | card titles, dialog headings |
| `--text-xl` | 22 | view titles |
| `--text-2xl` | 28 | login / startup / empty-state display |

Rule: size text from this ramp, in **rem only**. Never `em` for body text (it compounds
with the parent and drifts nested labels).

Weights: `--weight-regular` 400 · `--weight-medium` 500 · `--weight-semibold` 600
(**headings are 600, not 700** — 700 reads heavy on warm near-black; 700 is reserved).
Line-height: `--leading-tight` 1.2 (display) · `--leading-snug` 1.35 (single-line UI) ·
`--leading-body` 1.5 (reading). Tracking: `--tracking-label` 0.06em (uppercase labels).

Fonts: `--font-ui` = system-ui stack (all UI/body) · `--font-pixel` = "Tiny5"
(brand/wordmark ONLY) · `--font-mono` (hashes, tokens, paths).
The shared `.section-label` class = `--text-2xs` / semibold / uppercase /
`--tracking-label` / `on-surface` @ 0.5 alpha.

### Elevation (`--elevation-1..4`) — all on the theme `--v-theme-shadow` token
`1` = `0 1px 2px shadow/.12` (resting cards, hovered tiles) · `2` = `0 2px 6px shadow/.18`
(menus, dropdowns, raised controls, action bars) · `3` = `0 4px 16px shadow/.22`
(popovers, floating panels) · `4` = `0 8px 28px shadow/.30` (dialogs, lightbox chrome).
Never hardcode a shadow color (`rgba(0,0,0,…)` reads cold and flat on the warm canvas).
Dark mode leans on lightness for elevation; keep shadows subtle.

### Focus & scrims
- `--focus-ring` = `0 0 0 3px rgba(accent, .55)` — on every focusable element. Never
  remove an outline without replacing it with this.
- `--scrim-surface` = `rgba(surface, .55)` — light warm chip over the bright grid/sidebar
  canvas (dark glyph).
- `--scrim-photo` = `rgba(scrim, .55)` — dark chip directly over an arbitrary photo
  (light glyph). Full-screen backdrops use `rgba(scrim, …)` at their own tuned opacity.

### Component dimensions
- `--badge-size` = 16px (count-pill min height/width) · `--badge-size-dot` = 8px
  (attention dot). See §5 Badges.
- `--bar-height` = 48px (contextual action-bar / toolbar band). See §6 Action bars.
- `--notice-max-w` = 420px (the notice card, and any floating card of that family).

### Layers (`--z-*`)
Eight named steps replace 40+ ad-hoc z-index values. **Pick a name, not a number.**

`--z-base` 0 (in-flow) · `--z-raised` 10 (over an immediate sibling) · `--z-sticky`
100 (sticky headers in a scroll container) · `--z-floating` 200 (chrome anchored to
the content area: selection pill, breadcrumb) · `--z-dropdown` 300 (menus, popovers,
tooltips) · `--z-drawer` 1000 (full-panel overlays: the lightbox) · `--z-overlay`
2000 (app-level overlays, context menus) · `--z-modal` 4000 (dialogs and scrims) ·
`--z-notice` (the notice surface, above everything).

Steps are 10× apart so a component can be wedged between strata. `--z-notice` is
parked at `100000` rather than its ladder slot of 5000 because three un-migrated
call sites still sit above 5000; it drops when they move. Existing raw z-indexes
migrate opportunistically — a raw z-index in **new** code is drift.

### Interactive washes (in `style.css`, tuned per theme)
`--hover-wash` · `--active-wash` (selection fill) · `--active-bar` (selection edge) ·
`--active-text`. Light selection reads **accent-warm**; dark keeps the olive `primary`
convention. Disabled = ~38% opacity of the token, never a different grey.

### Motion (`--dur-*`, `--ease-*`)
| Token | Value | Use |
|---|---|---|
| `--dur-1` | 150ms | hover, press, micro-feedback |
| `--dur-2` | 200ms | panels, expand/collapse — default |
| `--dur-3` | 250ms | overlays, dialog enter/leave — **routine ceiling** |
| `--dur-4` | 420ms | expressive **one-shot** (FLIP flight, badge/sticker landing) — delight only |
| `--ease-standard` | `cubic-bezier(.4,0,.2,1)` | most transitions |
| `--ease-decelerate` | `cubic-bezier(0,0,.2,1)` | elements entering |
| `--ease-accelerate` | `cubic-bezier(.4,0,1,1)` | elements leaving |
| `--ease-spring` | `cubic-bezier(.2,1.4,.4,1)` | landing with overshoot (flight punctuation) |

Nothing routine animates slower than `--dur-3`. `prefers-reduced-motion` is enforced
globally; every expressive one-shot also needs a plain-fade reduced-motion fallback.

**Named motion patterns:** *Attention pulse* (looping "look here", ~1.4s ease-in-out,
scale+opacity) · *Landing pulse* (one-shot accent glow ring when something new arrives,
no layout shift) · *Flight/FLIP* (travel on standard/decelerate over `--dur-4`, land on
`--ease-spring`).

---

## 3. Color themes (source of truth: `frontend/src/main.js`)

Consumed in CSS as `rgb(var(--v-theme-<token>))` / `rgba(var(--v-theme-<token>), <a>)`.
**Never write a hex literal in a component.** Neutrals are warm; elevation inverts
between themes (light: canvas is brightest, chrome recedes to warm grey, raised controls
go white; dark: chrome is a raised dark surface, elevation reads by lightness).

| Token | Light | Dark | Meaning |
|---|---|---|---|
| `background` / `onBackground` | `#faf9f7` / `#23211d` | `#1b1f24` / `#f2e5da` | page / grid canvas |
| `surface` / `onSurface` | `#ffffff` / `#23211d` | `#23282f` / `#f2e5da` | raised control (card, input, menu) |
| `sidebar` / `sidebar-text` | `#f0ede9` / `#25231e` | `#23282f` / `#d8d0c8` | sidebar chrome |
| `toolbar` / `toolbar-text` | `#f0ede9` / `#25231e` | `#23282f` / `#d8d0c8` | toolbar chrome |
| `panel` / `onPanel` | `#efede9` / `#23211d` | `#313337` / `#f2e5da` | panels |
| `accent` / `onAccent` | `#b0732b` / `#ffffff` | `#f28f3b` / `#1b1b1b` | **brand accent, key state** |
| `primary` / `onPrimary` | `#5c7c0a` / `#ffffff` | `#8EA604` / `#111111` | primary-action button, count badges |
| `secondary` / `onSecondary` | `#cb3a72` / `#ffffff` | `#DA4167` / `#ffffff` | secondary action |
| `tertiary` / `onTertiary` | `#5f8790` / `#ffffff` | `#77A0A9` / `#0f1418` | tertiary action |
| `border` / `divider` | `#d8d3c8` / `#e8e4dc` | `#363d45` / `#2c323a` | visible / subtle line |
| `success` / `on-success` | `#2e7d32` / `#ffffff` | `#4caf50` / `#1b1b1b` | success state |
| `error` / `on-error` | `#cf3b30` / `#ffffff` | `#f44336` / `#1b1b1b` | error / destructive |
| `warning` / `on-warning` | `#b8861f` / `#23211d` | `#db7900` / `#1b1b1b` | warning |
| `info` / `on-info` | `#1a6ec4` / `#ffffff` | `#2196F3` / `#1b1b1b` | info |
| `dark-surface` / `on-dark-surface` | `#242628` / `#f2e5da` | `#181b20` / `#f2e5da` | deliberately-dark chrome (lightbox) |
| `dark-surface-success` | `#4caf50` | `#4caf50` | status hue **inside** a `dark-surface` |
| `dark-surface-error` | `#f44336` | `#f44336` | " |
| `dark-surface-warning` | `#db7900` | `#db7900` | " |
| `dark-surface-info` | `#2196F3` | `#2196F3` | " |
| `sidebar-hover` / `on-sidebar-hover` | `#b0732b` / `#ffffff` | `#f28f3b` / `#f2e5da` | sidebar row hover |
| `focus` | `#7c4dff` | `#7c4dff` | (legacy; focus rings use `--focus-ring`) |
| `overlay` | `#00000033` | `#00000066` | legacy overlay wash |
| `hover` | `#2d200f0f` | `#ffffff14` | warm hover wash source |
| `input-background` / `input-text` | `#ffffff` / `#23211d` | `#2b3138` / `#f2e5da` | inputs |
| `cancel-button` / `cancel-button-text` | `#e6e1d8` / `#23211d` | `#3a4047` / `#f2e5da` | quiet/secondary button |
| `shadow` | `#1c160c` | `#2a2f36` | elevation shadow color |
| `scrim` | `#000000` | `#000000` | full-screen backdrops, `--scrim-photo` |

**Key spelling is load-bearing.** Vuetify emits `--v-theme-<key>` verbatim, so a
camelCase `onSurface` key produces `--v-theme-onSurface` (which nothing reads) and
Vuetify then auto-derives the `--v-theme-on-surface` the app *does* read as pure
`#000`/`#fff`. **Every `on-*` pair is spelled in kebab-case**; do not "tidy" them back.

**Contrast (proven, WCAG floors: body ≥4.5:1, large/UI ≥3:1):**
- `primary` olive `#5c7c0a` + white = **4.84:1** — passes. The primary-action button
  color, and the count-badge fill.
- `accent` amber `#b0732b` + white = **3.94:1** — **fails** the 4.5:1 body floor, passes
  the 3:1 large/UI floor. So amber is for large labels (≥18px, or ≥14px bold), icons,
  borders, state washes, and badge **dots/glow** — **not** a background behind small
  white text. This is the single most-missed trap; honor it.
- Status hues on their own canvas (light / dark): `error` 4.62 / 4.50 · `warning`
  3.09 / 5.32 · `success` 4.87 / 5.96 · `info` 4.90 / 5.30.
- The eight `on-<status>` values on their **solid** fill: light 4.86 / 4.95 / 5.13 /
  5.16, dark 4.68 / 5.53 / 6.20 / 5.51 (error / warning / success / info).

**Three status jobs, three tokens.** `<status>` = hue as foreground, border or tint
on the theme's own canvas. `on-<status>` = text or glyph on a **solid** status fill,
and *only* there — on a translucent tint it is the wrong color (the near-black
`on-warning` measures 1.41:1 on a 20% tint; use `on-surface` / `on-dark-surface`
instead). `dark-surface-<status>` = the hue anywhere inside a `dark-surface`, which
stays dark in both themes and so needs its own set.

Status never rides on color alone — always pair with an icon or text.
**Named decorative exemption:** the review-celebration confetti palette
(`#ffd166 #06d6a0 #ef476f #4cc9f0 #f78c6b`) is intentionally off-token; do not tokenize it.

---

## 4. Core component patterns

- **Image grid (the hero).** Dense tiles share `--radius-md` and a restrained border;
  no per-tile framing. Every tile has real hover, selected (`--active-wash` fill +
  `--active-bar` edge + `--active-text`), and focus (`--focus-ring`) states. Empty /
  loading / error are all designed: `Empty.png` / `EmptyTrash.png` art + `--text-2xl`
  Tiny5 headline + `--text-sm` guidance; loading is a skeleton at tile dimensions, not
  a spinner on blank canvas.
- **Dialogs & panels.** `--radius-lg`, `--elevation-4` (dialogs) / `--elevation-3`
  (floating panels), `--space-7` padding, `--text-lg` heading, `--text-md` body.
- **Buttons.** Primary = `primary`/`onPrimary`. Destructive = `error` + confirm.
  Quiet/secondary = `cancel-button`. Icon + label gap `--space-2`, icons from Material
  Design Icons (`@mdi/font`) — one family, tinted with `currentColor`/a theme token.
- **Chips.** `--radius-sm`, `--text-sm`, `--space-2` padding.
- **Badges** — see §5. **Action bars** — see §6.
- **Section label.** The recurring uppercase label is the global `.section-label` class;
  reuse it, don't re-roll it.

---

## 5. Badges (pattern)

Small count pill or attention dot pinned to a control, overlaid without shifting layout.

| Shape | When | Fill / text | Size |
|---|---|---|---|
| **Count pill** | a number matters (`3`, `99+`) | `primary` / `onPrimary` | `--badge-size` (16px) min, `--radius-pill` |
| **Attention dot** | just "here / live / just landed" | `accent`, no text | `--badge-size-dot` (8px), `--radius-pill` |

- **Why the split:** the count pill carries small text → needs 4.5:1 → `primary`
  (4.84:1). The amber `accent` fails behind small text (3.94:1), so accent is spent on
  the *dot and glow* (a ≥3:1 UI mark, no small text on it). Matches the shipped toolbar
  filter-count badge (`primary`) and activity dot (accent/primary glow).
- Count text: `--text-2xs`, `--weight-semibold`, `font-variant-numeric: tabular-nums`.
- Overlay absolutely on the host; never resize/shift the control.
- On landing (badge appears or count ticks because work just landed): one-shot
  **landing pulse** (accent glow, §2 motion). Loop the **attention pulse** only while
  work is genuinely live.

---

## 6. Contextual action bar (pattern)

A bar that appears to act on a context — the bulk **selection bar**, the image-overlay
top bar, and the **Trash restore/purge bar**. The grid is reused; only the bar changes.
There are **two shipped shapes**, and the earlier version of this section described the
first while pointing at a component that is the second.

**a) Full-width band.** Left cluster (count/title: "12 selected", "Trash") + right
cluster of actions, on a chrome surface (`toolbar`/`panel`), `--elevation-2`, full
width, height `--bar-height` (48px). Reference: the image-overlay top bar.

**b) Floating pill.** `SelectionBar.vue` — **`frontend/src/components/panels/`, not
`components/widgets/`**. It is *not* a full-width band: it is a centred pill sized to
its content, `border-radius: var(--radius-pill)`, `background: rgba(surface, .86)`
with `backdrop-filter: blur(12px)`, `--elevation-3`, `bottom: var(--space-5)` inside
`.grid-content-area` (its positioned ancestor and its `container: selbar` context).
Occupied height ~50px, and it grows when it wraps or on coarse pointers — which is why
the notice surface *measures* it rather than assuming a height (`notice-surface.md`
§2.2). It is the reference for floating contextual actions.

- **Action weight** (both shapes): affirmative (Restore) = `primary` button;
  destructive (Purge / Delete forever) = `error`, visually distinct, **always behind a
  confirm**. Secondary actions quiet.
- **Open:** whether (b) converges onto (a). It moves pixels on the app's most-used
  control, so it is UI/UX-gated. Until then both shapes are legitimate and this section
  names which is which.

---

## 7. New-feature design context (v1.8.0)

Visual foundations for the two new surfaces. Flows/behaviour are UI/UX's + the
maintainer's call; below is only which tokens/patterns to build the *look* on.

### Async imports (#459)
- **Non-blocking import dialog:** standard dialog look — `--radius-lg`, `--elevation-4`,
  `--space-7` padding, `--text-lg` heading. "Non-blocking" (dismissible while work
  continues) is a flow decision (UI/UX); visually it is the normal dialog that can be
  sent to the background.
- **File chip → sidebar FLIP flight:** the chip is the standard chip (`--radius-sm`,
  `--text-sm`, `--space-2`). The flight uses the **Flight/FLIP** motion pattern — travel
  on `--ease-standard`/`--ease-decelerate` over `--dur-4` (420ms), land on `--ease-spring`
  for the overshoot. Precedent to copy: `rs-sticker-fly` / `rs-sticker-land`. Provide a
  reduced-motion fallback (plain fade, no travel).
- **Sidebar task/upload badge + pulse-on-landing:** the badge is the §5 pattern — a
  **count pill** (`primary`/`onPrimary`, `--badge-size`) when a queue count matters, or
  an **attention dot** (`accent`, `--badge-size-dot`) for "work is live". On landing,
  play the one-shot **landing pulse** (accent glow ring, `gridNewPulse` model); loop the
  **attention pulse** (`tb-stats-pulse` model) only while imports are genuinely running.

### Trash (DAM 1.1)
- **Trash view = the picture grid reused** (§4 grid pattern, all its tile states).
- **Restore/purge action bar = the §6 contextual action bar** at `--bar-height`:
  left "Trash" / selected-count, right actions — **Restore** (`primary`) and **Purge /
  Delete forever** (`error`, behind a confirm; deletion is irreversible).
- **Empty state:** the existing `EmptyTrash.png` art + `--text-2xl` Tiny5 headline +
  `--text-sm` guidance.

---

## 8. Open design decisions (for the maintainer / UI/UX in Claude Design)

The visual system is settled; these are deliberately left open — they are flow/UX
choices or pixel-moving reconciliations that need UI/UX sign-off, not lead-designer calls:

- **Import dialog dismissal model** — how "non-blocking" presents (minimize-to-sidebar
  vs. toast vs. background task list). Flow decision; the visuals reuse the dialog +
  badge patterns above.
- **Badge: dot vs. count default** for the sidebar import indicator — whether the
  resting state shows a live count or just an attention dot. Both are specified above;
  which leads is a UX call.
- **Trash retention / purge affordance** — auto-purge window, select-all-in-trash,
  bulk vs. per-item purge. Behaviour; the destructive-confirm visual is fixed.
- **Action-bar height unification** — migrating the drifting 34/40/48/56px bars onto
  `--bar-height` moves pixels, so it is UI/UX-gated (not done here).
- **Centralizing badges/action bars into shared components** — today both are
  hand-rolled per component; consolidating onto shared components (the `.section-label`
  precedent) is the durable anti-drift fix, but it is a frontend refactor, not a token change.
