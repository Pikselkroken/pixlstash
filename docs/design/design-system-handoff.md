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
- `--focus-ring` = `0 0 0 3px rgb(accent)` — a **solid** accent stroke, on every
  focusable element. Never remove an outline without replacing it with this. It was
  `rgba(accent, .55)` and that measured 1.96:1 (light) / 3.01:1 (dark) against the
  canvas, below the 3:1 focus-indicator floor; solid measures 4.51 / 3.61.
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
`--active-text`. Light selection is built on `primary`; dark on `accent`. Disabled =
~38% opacity of the token, never a different grey.

The alphas are **solved, not chosen**: they hold the wash's contrast step against its own
surface at ≈1.10 (light hover) and ≈1.26 (dark hover) / ≈1.43 (dark selection). When the
dark accent deepened for the action-fill invariant, dark `--hover-wash` moved `.14`→`.24`
and `--active-wash` `.20`→`.34` to keep those steps. Re-solve them if the accent moves
again. `--active-text` is a foreground on the **wash**, not on a solid fill, so it is the
surface's own text colour (`on-surface`) in both themes — never `on-primary`.

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
| `accent` / `on-accent` | `#9e6727` / `#ffffff` | `#b85c0c` / `#ffffff` | **brand accent, key state** |
| `primary` / `on-primary` | `#5c7c0a` / `#ffffff` | `#6b7d04` / `#ffffff` | primary-action button, count badges |
| `secondary` / `on-secondary` | `#cb3a72` / `#ffffff` | `#d13a5f` / `#ffffff` | secondary action |
| `tertiary` / `on-tertiary` | `#557982` / `#ffffff` | `#547b84` / `#ffffff` | tertiary action |
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
| `dark-surface-primary` | `#8EA604` | `#8EA604` | `primary` as a foreground **inside** a `dark-surface` |
| `sidebar-hover` / `on-sidebar-hover` | `#9e6727` / `#ffffff` | `#b85c0c` / `#ffffff` | sidebar row hover (= the accent value) |
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

**The action-fill invariant.** The foreground on `accent`, `primary`, `secondary` and
`tertiary` is **`#ffffff`, in both themes, always**. It is a fixed rule, not a
per-fill lookup: one label colour on every branded fill is what makes a row of mixed
buttons read as one family. A white label forces the fill's luminance to `L ≤ 0.1833`,
and a fill still has to clear 3:1 on the dark canvas (`L ≥ 0.1624` on `surface`), so
every dark-theme action fill lives in one narrow band and all eight values sit at
`L ≈ 0.17`. The corollary is permanent: **those four are never small body text on a
canvas** (as foregrounds they are 3.53 – 3.61:1 in dark), only icons, borders, rails,
≥18px text and ≥14px bold. Full table and arithmetic: `visual-language.md` §4.

**Contrast (proven, WCAG floors: body ≥4.5:1, large/UI ≥3:1):**
- White on the action fills: light `accent` **4.75** · `primary` **4.84** ·
  `secondary` **4.79** · `tertiary` **4.73**; dark **4.59** / **4.60** / **4.69** /
  **4.62**. All pass, none by much — the values sit deliberately at the bright end of
  the legal window.
- The same fills against their own canvas (`background` light / dark): 4.51 / 3.61
  (accent), 4.60 / 3.60 (primary), 4.55 / 3.53 (secondary), 4.49 / 3.58 (tertiary).
- Status hues on their own canvas (light / dark): `error` 4.62 / 4.50 · `warning`
  3.09 / 5.32 · `success` 4.87 / 5.96 · `info` 4.90 / 5.30.
- The eight `on-<status>` values on their **solid** fill: light 4.86 / 4.95 / 5.13 /
  5.16, dark 4.68 / 5.53 / 6.20 / 5.51 (error / warning / success / info). Statuses are
  **not** part of the action-fill tier: in dark they keep the warm near-black `#1b1b1b`.
- `dark-surface-primary` `#8EA604` on `dark-surface`: **5.50** (light `#242628`) /
  **6.25** (dark `#181b20`).

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

- **Why the split:** it used to be contrast (`primary` 4.84:1, amber 3.94:1). Since the
  action-fill invariant both clear 4.5:1, so the split is now **semantic**: a count is
  information and rides the workhorse fill; the accent is the brand's attention colour
  and is spent on the dot and the glow, where there is nothing to read. Keeping one thing
  amber is what makes amber mean anything. Matches the shipped toolbar filter-count badge
  (`primary`) and activity dot (accent). An amber count pill is no longer illegal, just
  off-pattern — do not introduce one without changing this section.
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

---

## 9. Carried findings — measured, decided, not yet implemented

These are decisions, not questions. They are recorded with their numbers so the
implementation lane does not have to re-measure anything. Grouped by how expensive they
are to reverse.

### 9.1 The action-fill change set (one commit; every value is in `visual-language.md` §4)

| File | Change |
|---|---|
| `frontend/src/main.js` | dark `accent` `#f28f3b`→`#b85c0c`, `primary` `#8EA604`→`#6b7d04`, `tertiary` `#77A0A9`→`#547b84`, `secondary` `#DA4167`→`#d13a5f`; light `accent` `#b0732b`→`#9e6727`, `tertiary` `#5f8790`→`#557982` |
| `frontend/src/main.js` | dark `on-accent` / `on-primary` / `on-tertiary` → `#ffffff` (the other five `on-*` in the tier already are) |
| `frontend/src/main.js` | `sidebar-hover` → the new accent value per theme; `on-sidebar-hover` → `#ffffff` in **both** themes (light was 3.94:1, dark was **1.94:1**) |
| `frontend/src/main.js` | **new key, both themes:** `dark-surface-primary: "#8EA604"` |
| `frontend/src/styles/design-tokens.css` | `--focus-ring` → `0 0 0 3px rgb(var(--v-theme-accent))` |
| `frontend/src/style.css` | **as applied (row corrected):** dark `--hover-wash` `rgba(accent, .08)`→`.14`; dark `--active-wash` `rgba(primary, .18)`→`.26`. These RESTORE today's perceived step (1.136 / 1.322) after the accent deepen dropped them to 1.072 / 1.202 — they do not strengthen it. The originally-published `.14`→`.24` / `.20`→`.34` transposed the two themes; see the correction note in visual-language §4. `--active-text` needs no change (see the withdrawn row in §9.2). |
| `ReviewSessionView.vue:789`, `ReviewArchivedReceipt.vue:116` | `.rs-tally-added` / `.rs-archived-added`: `primary` → `dark-surface-primary` |

Keep `docs/design/design-tokens.css` and `frontend/src/styles/design-tokens.css` in sync
(they are deliberately not byte-identical, but their **values** must match).

**The sweep this needs afterwards.** The change is safe for every *fill* by construction,
but it lowers these four tokens as *foregrounds* in the dark theme (5.8 – 6.9:1 →
3.5 – 3.6:1). The codebase currently has **77** `color: rgb(var(--v-theme-accent))`-style
declarations, **59** for `primary`, **6** for `tertiary` and **3** for `secondary`. Most
are icons, rails, borders and headings, which are fine at the 3:1 UI floor. The ones to
find and re-point at `on-surface` are the **small text** ones — anything at
`--text-sm` (13px) or below in one of these four colours on a canvas. This is a read-only
grep-and-eyeball pass, not a blocker, and it is the same review the light theme should
have had when its accent was measured at 3.74:1.

**Reversal cost.** Cheap and total: every row above is a one-line value swap with no
structural dependency. The one irreversible-ish part is perceptual, not technical — the
dark accent drops 21 points of HSL lightness and people will notice. If it reads muddy in
situ, the lever is the invariant itself (restore `#f28f3b` and put `on-accent` back to
`#1b1b1b`), not a compromise value: there is no fill that is both brighter and legal
under a white label.

### 9.2 `on-<x>` used on a surface that is not `<x>` — the recurring trap, four more sites

The `on-<status>`-on-a-tint bug (`visual-language.md` §4) has three siblings outside the
status family. All four are **pre-existing and independent of the action-fill change**;
they are recorded here because they are the same mistake and will otherwise be
rediscovered one at a time.

| Site | What it does | Measured | Fix |
|---|---|---|---|
| ~~`style.css` light `--active-text`~~ **WITHDRAWN — not a defect** | The claim transposed the themes. Light `--active-text` **already reads `on-surface`** (**10.84:1** at the new accent); dark's is `on-primary` (white) over the now-26% `primary` tint on `#23282f` = **11.22:1**. | both pass | none — leave the code alone. Verified against `style.css` at implementation time; do not "re-fix" this against the 1.32:1 figure. |
| `SideBar.vue:7187, 7197, 7202, 7216, 7236, 7246` (`.sidebar-project-menu-*`) | `on-tertiary` as the **menu's** text colour; only `.active` has a `rgba(tertiary, .3)` tint under it | light **1.43 – 1.70:1**; dark 1.74 – 2.27:1 today | `on-surface` / `on-panel` → **6.6 – 11.2:1** |
| `SideBar.vue:8522-8523` | `on-secondary` on `rgba(secondary, .75)` | light **3.42:1** | solid `secondary` fill → 4.79:1 |
| `App.css:289` (`.media-type-toggle .v-btn`) | `on-secondary` (white) on `rgba(surface, .3)` — white on near-white in light mode | fails | `on-surface`; keep `on-secondary` only on the `.v-btn--active` **solid** `secondary` fill (line 297-298), where it is correct |

Note the `SideBar` rows move in the *right* direction when dark `on-tertiary` flips to
white (dark goes 1.74 → 10.66 on the tint), but the light theme stays broken either way,
so the flip is not the fix. Use the surface's own foreground.

**Rule to carry:** an `on-<x>` token is only ever correct on a **solid, full-opacity
`<x>` fill**. The moment you see `on-<something>` in the same rule as an `rgba(...)`
background — or in a rule with no `<x>` background at all — it is wrong. This is now the
fourth distinct occurrence of that bug in this codebase.

### 9.3 Carried from earlier passes, unchanged

- **~40 `error`-on-`dark-surface` declarations** in the review overlay measure 3.12:1 and
  want `dark-surface-error` (4.12:1). Pre-existing, mechanical, large enough to want its
  own eyeball pass.
- **`SelectionBar`'s `6px` block padding** stays off-grid until the action-bar height
  reconciliation (34 / 40 / 48 / 56px → `--bar-height`), which is UI/UX-gated because it
  moves pixels on the app's most-used control.
- **The 40+ raw z-index call sites.** The ladder is shipped; retrofitting is
  opportunistic (touch a rule, move it onto the ladder). The ladder's own values and the
  migration of the remaining squatters are owned by the concurrent layering lane — read
  `frontend/src/styles/design-tokens.css` for the current rungs rather than any copy of
  them here.
### 9.4 There are two focus languages, and the second one is not documented anywhere

`--focus-ring` (a 3px accent box-shadow) is the system's focus treatment. But the theme
also carries a `focus` key, `#7c4dff` violet, and **10 review-surface components use it
as a competing `outline: 2px solid rgb(var(--v-theme-focus))`**: `NewReviewDialog.vue`
(×2), `ReviewSessionView.vue`, `TagHealthBoard.vue`, `ReviewPairCard.vue`,
`ReviewSessionsOverlay.vue`, `ReviewRail.vue`, `ReviewDecisionBar.vue`,
`ReviewArchivedReceipt.vue`, `ReviewBinaryCard.vue`. So a keyboard user gets an amber
ring in the grid and a violet outline in the review flow, with a different width and a
different geometry (outline vs box-shadow), and nothing in the design docs said so.

The violet is not *broken* — it measures 3.15 – 4.81:1 depending on the surface, above
the 3:1 floor everywhere — which is exactly why it has survived unnoticed. It is a
consistency defect, not a contrast one.

**Decision: one focus language. The review surfaces migrate onto `--focus-ring`, and the
`focus` theme key is retired once they do** (retire it *after*, not before — removing a
theme key while a consumer still reads it is a runtime failure, not a lint error). The
migration is 10 mechanical edits, but each is pixel-visible on a different screen, so it
is opportunistic follow-up work in the same spirit as the z-index ladder, not a
prerequisite for anything.

Until it happens, `--focus-ring` is the only correct choice in **new** code, and
`rgb(var(--v-theme-focus))` in new code is drift.
