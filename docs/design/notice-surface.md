# The Notice Surface

Owner: lead designer. Behaviour changes (what a control does, when a message appears,
what Esc does) go past the UI/UX expert; the values below are the design system's.

**Status: specification. Nothing here is implemented.** `frontend/src/stores/useNoticeStore.js`
is a headless scaffold whose own header says the visible host "is deliberately NOT built
here… pushing a notice is a harmless no-op on screen." This document is the design for
that host. It also names four store-shape changes the host needs before it can be
adopted (§9) and the drift it uncovered on the way (§10).

Sources of truth this is built on: `docs/design/visual-language.md`,
`docs/design/design-tokens.css`, the themes in `frontend/src/main.js`. Every colour is
`rgb(var(--v-theme-*))`; every size, radius, shadow and duration is an existing token,
with two new tokens requested explicitly in §11.

---

## 1. What a notice is, and what stays a dialog

A **notice** reports the outcome of something the user already did. A **dialog** asks
for a decision before something happens. The line is not "how bad is it" — it is
"is consent still outstanding".

**It is a dialog if any of these is true:**

1. It requires a decision before the app proceeds (consent-bearing, blocking).
2. The act is irreversible or destroys data on disk.
3. It must enumerate content the user has to read before consenting — a file list, a
   variable-length set of affected objects.

**Otherwise it is a notice.** Concretely:

| Message | Surface | Why |
|---|---|---|
| `DeleteForeverDialog` | **stays a dialog** | Consent, irreversible, type-to-confirm, enumerates protected on-disk originals. All three tests. |
| `RestoreConfirmDialog` | **stays a dialog** | Consent before a mutation. |
| Post-purge snapshot privacy notice (`frontend_architecture.md` §"Post-purge privacy notice") | **stays a dialog** | Enumerates a variable-length snapshot list and routes to Settings. A notice is one sentence. |
| `LockedDeleteNoticeDialog` | **becomes a notice** (`warning`) | Purely informational, reports an outcome already committed, one "Got it" button. Its own header says it is a dialog only because no notice host exists. `title` + `body` collapse to one sentence; `hint` becomes the action. |
| The 31 native `alert()` calls across 8 files (`ImageGrid`, `ImageOverlay`, `SideBar`, `CharacterEditor`, `PictureSetEditor`, `OverlayDescriptionPanel`, `useGridDragDrop`, `useStackOrdering`) | **become notices** | A native `alert()` is unstyled, blocking, steals focus, and cannot be dismissed by anything but a click. It is the anti-pattern this surface replaces. `alert("Failed to restore scrapheap.")` (`ImageGrid.vue:4046`) is the model case. |
| The silent `catch` blocks in `ImageGrid.vue` (`:3061`, `:3081`, `:4049`, `:6389`, and the `console.error`-only ones at `:4708`, `:4721`) | **become notices** (`error`) | A failure the user is never told about is the bug the store was written to fix. |

**The one-sentence rule.** A notice is a single sentence, ≤ 120 characters, plus at
most **one** action. If it needs a list, a second action, or a decision, it is a dialog.
This is what keeps the surface from growing into a second dialog system.

---

## 2. Placement, and the SelectionBar problem

### 2.1 The shipped situation

`SelectionBar.vue` (in `components/panels/`, not `widgets/`) is **not** the full-width
`--bar-height` action bar that `design-system-handoff.md` §6 describes. It is a floating
pill:

```
position: absolute; bottom: 18px; left: 50%; transform: translateX(-50%);
z-index: 200; width: max-content; max-width: calc(100% - 24px);
border-radius: 999px; padding: 6px 10px;
```

positioned inside `.grid-content-area` (`ImageGrid.vue`), which is its containing block
and the `container: selbar / inline-size` context for its own container query. Measured
occupied height is **54px** (40px controls + 2×6px padding + 2×1px border), and it grows
on coarse pointers and when it wraps. Two other things also live on the bottom edge:
`.grid-breadcrumb` (bottom-left, `bottom: 12px`, z 50, with an existing
`--above-bar` variant that lifts it to `bottom: 48px`) and nothing else.

There is no z-index scale in the repo: 40+ distinct values from `0` to `99999`, two of
them `!important`. "Put the toast above things" is therefore not a rule, it is luck.

### 2.2 The rule

**The bottom edge is one column with one owner of its inset.**

```
Anchor    fixed, bottom-centre of the app viewport
Mount     exactly one host, the LAST child of `.app-viewport` in App.vue
Inline    left: 0; right: 0; display: flex; flex-direction: column;
          align-items: center; pointer-events: none
Card      pointer-events: auto; width: min(100% - 2 * var(--space-5), var(--notice-max-w))
Block     bottom: var(--notice-safe-bottom)
Stack     gap: var(--space-3); newest card nearest the bottom edge
Layer     z-index: var(--z-notice)
```

`--notice-safe-bottom` is the contract. It is declared once, on `.app-viewport`:

```css
.app-viewport {
  /* Height occupied by bottom-anchored floating chrome inside the notice
     column's footprint. 0px when nothing is parked there. */
  --floating-bottom-h: 0px;
  --notice-safe-bottom: calc(var(--space-5) + var(--floating-bottom-h));
}
```

**Whoever parks something on the bottom edge owns `--floating-bottom-h`.** Today that is
exactly one component. While `SelectionBar` is `visible`, the app root carries:

```css
--floating-bottom-h: calc(var(--selbar-h, 56px) + var(--space-3));
```

`--selbar-h` is the pill's **measured** height (a `ResizeObserver` on the pill writes it
to the app root), not a constant. It must be measured because the pill wraps, grows on
coarse pointers, and changes height with its own content. `56px` is the fallback for the
first frame, and it is a measured current value, not a new design token.

So: pill hidden → the stack rests at `--space-5` (16px). Pill visible → 16 + 54 + 8 =
**78px**, i.e. the stack sits exactly `--space-3` above the pill's top edge and can never
touch it. When the pill appears while notices are on screen, the stack rises at `--dur-2`
/ `--ease-standard`; when it goes, the stack settles back.

**Notices move; the pill does not.** The pill is a control the user's cursor is heading
for. A transient message must never displace it.

### 2.3 Why bottom-centre, and why the host does not track the sidebar

The host spans the full viewport width and centres on the viewport. It does **not** track
the sidebar or the stats panel.

- The host is global. It has to render on the login screen, over `ImageOverlay`, over
  `ReviewSessionsOverlay` and inside Settings — none of which have a grid column to
  centre on. One stable anchor beats a conditional one.
- Bottom-centre is already the app's transient-status position (the selection pill).
  Top-centre is taken by the toolbar and `.grid-range-pill`; bottom-left by the
  breadcrumb; bottom-right by the stats panel.
- Consequence: with the sidebar open, the notice stack and the pill are centred on
  slightly different axes. They are 78px apart vertically and never on one line, so this
  does not read as a misalignment. **If review disagrees, the fix is to move
  `SelectionBar` into this same bottom-stack container — not to make the notice host
  sidebar-aware.** Logged as an open item in §12.

### 2.4 Narrow viewports

Below **600px** the card goes edge-to-edge minus 12px gutters and the visible cap drops:

```css
@media (max-width: 600px) {
  .notice-card  { width: calc(100% - 2 * var(--space-4)); }
  /* max visible notices: 2 (see §4) */
}
```

At that width the centred card's footprint now covers the bottom-left breadcrumb, so the
breadcrumb becomes a contributor to the same variable:

```css
@media (max-width: 600px) {
  .app-viewport { --floating-bottom-h: calc(max(var(--selbar-h, 0px), var(--breadcrumb-h, 0px)) + var(--space-3)); }
}
```

This is the general form of the rule: **`--floating-bottom-h` is the height of the
tallest bottom-anchored floating element currently visible *inside the notice column's
footprint*.** Above 600px the breadcrumb is outside that footprint and contributes 0.

### 2.5 Over the lightbox and other dark surfaces

`ImageOverlay` is a deliberately-dark surface (`dark-surface` / `on-dark-surface`, its
own z-index 1000–5000). A white `surface` card floating on it is legible but reads as
foreign chrome. The host takes a modifier in any `dark-surface` context:

| Part | Default | `--on-dark` modifier |
|---|---|---|
| Card background | `rgb(var(--v-theme-surface))` | `rgb(var(--v-theme-dark-surface))` |
| Message text | `rgb(var(--v-theme-on-surface))` | `rgb(var(--v-theme-on-dark-surface))` |
| Status tint | `rgba(var(--v-theme-<status>), 0.08)` | `rgba(var(--v-theme-<status>), 0.14)` |
| Border | `rgba(var(--v-theme-<status>), 0.5)` | `rgba(var(--v-theme-<status>), 0.5)` |

Verified: `on-dark-surface` on the 14% tinted dark card is **9.86:1 – 11.03:1** across
all four variants.

---

## 3. Variants and token mapping

Four variants, matching the store's four levels. **Warning is needed and stays.** It is
not a weaker error: an error means the action failed, a warning means it partly
succeeded and the user should know what did not happen. `LockedDeleteNoticeDialog` is
exactly that case ("12 deleted, 3 are frozen by a locked set") and it is the reason the
level exists.

### 3.1 The mapping

Every variant is the **same card**, differing only in its status token and glyph. The
card reuses the status-panel vocabulary already shipped in `DeleteForeverDialog`
(`.ref-warn`, `.lock-note`) and `LockedDeleteNoticeDialog` (`.lock-hint`) — a
`rgba(status, .5)` border over a `rgba(status, .08)` fill — so the notice introduces no
new visual language, plus one solid status rail so it is identifiable at a glance from
across a dense grid.

| Variant | Status token | MDI glyph | Fill | Border | Rail |
|---|---|---|---|---|---|
| `info` | `info` | `mdi-information-outline` | `rgba(var(--v-theme-info), 0.08)` | `1px solid rgba(var(--v-theme-info), 0.5)` | `rgb(var(--v-theme-info))` |
| `success` | `success` | `mdi-check-circle-outline` | `rgba(var(--v-theme-success), 0.08)` | `1px solid rgba(var(--v-theme-success), 0.5)` | `rgb(var(--v-theme-success))` |
| `warning` | `warning` | `mdi-alert-outline` | `rgba(var(--v-theme-warning), 0.08)` | `1px solid rgba(var(--v-theme-warning), 0.5)` | `rgb(var(--v-theme-warning))` |
| `error` | `error` | `mdi-alert-circle-outline` | `rgba(var(--v-theme-error), 0.08)` | `1px solid rgba(var(--v-theme-error), 0.5)` | `rgb(var(--v-theme-error))` |

Fill sits over an opaque `rgb(var(--v-theme-surface))` base. **The card is opaque.** The
selection pill's `rgba(surface, .86)` + `backdrop-filter` is a legibility gamble over an
arbitrary photo grid; a message the user must read does not take that gamble.

Outline glyphs throughout, matching `mdi-lock-outline` / `mdi-tag-off-outline` elsewhere
in the app. (`DeleteForeverDialog` uses a filled `mdi-alert` — minor drift, §10.)

### 3.2 The glyph and the message are `on-surface`, not the status colour

**The status hue appears only in the rail, the border and the 8% fill. The glyph and the
text are `rgb(var(--v-theme-on-surface))` in every variant.** This is the load-bearing
decision in this document and it is not a stylistic preference — it is what the contrast
maths forces.

Measured, status colour as a **foreground** on its own 8%-tinted card:

| | light | dark |
|---|---|---|
| `error` | 4.32:1 | 3.75:1 |
| `warning` | **2.99:1** | 4.32:1 |
| `info` | **2.88:1** | 4.25:1 |
| `success` | **2.59:1** | 4.72:1 |

Three of four fail the 3:1 non-text floor in the light theme. A coloured glyph would be
a variant that silently fails on one theme — the exact class of bug §4 of the visual
language exists to catch, and the same trap as the amber accent.

`on-surface` as the glyph and message colour measures **14.30:1 – 14.97:1** (light) and
**10.61:1 – 11.18:1** (dark) across all four variants. It also does three other things
the system asks for: it obeys "quiet — spend colour sparingly"; it makes literal §4's
"status meaning never rides on colour alone" (the glyph *shape* carries the variant, the
hue reinforces it); and it lets the host ship with **zero theme edits**. The light-theme
status-hue weakness is a real problem, but it is a theme problem (§10, finding 8), not
something the notice host should paper over.

### 3.3 `on-error` — no, and not the other three either

> **Status: FIXED (2026-07).** All eight `on-<status>` values are now authored in
> `main.js` and every one clears 4.5:1 — see "What was actually authored" at the end
> of this section. The analysis below is kept because it is the evidence, and because
> the root cause it exposes (Vuetify key spelling) recurs in any new theme key.
>
> §3.2's decision is **unchanged**: the notice card still uses none of the four, and
> its glyph and message stay `on-surface`. The card has no solid status fill, so
> `on-<status>` is still the wrong token for it.

`on-error` was not authored in either theme in `main.js`. Vuetify 3.6.7 auto-generates it:
`theme.mjs:138-143` fills any missing `on-<color>` via `getForeground()`, which picks pure
`#000` or `#fff` by **APCA** contrast. Run against the shipped themes it returned:

| | derived `on-*` | WCAG vs the fill | verdict |
|---|---|---|---|
| light `on-error` `#cf3b30` | `#fff` | 4.86:1 | passes |
| light `on-warning` `#b8861f` | `#fff` | 3.25:1 | **fails 4.5** |
| light `on-info` `#2196F3` | `#fff` | 3.12:1 | **fails 4.5** |
| light `on-success` `#4caf50` | `#fff` | **2.78:1** | **fails 4.5 and 3.0** |
| dark `on-error` `#f44336` | `#fff` | 3.68:1 | **fails 4.5** |
| dark `on-warning` `#db7900` | `#fff` | 3.11:1 | **fails 4.5** |
| dark `on-info` `#2196F3` | `#fff` | 3.12:1 | **fails 4.5** |
| dark `on-success` `#4caf50` | `#fff` | **2.78:1** | **fails 4.5 and 3.0** |

**Seven of eight fail.** `on-error` is the right token in exactly one place — small text
on a solid light-theme `error` fill — and nowhere else. The notice surface does not use a
solid status fill, so **it uses none of the four**, and this document does not depend on
them being fixed.

**Seven of eight failed.** They resolved at runtime, so anything reaching for one got a
silent failure (`SelectionBar.vue:805` `.remove-btn` did).

#### The root cause: Vuetify key spelling

The four status pairs were not the only casualties, and "nobody wrote them" was not the
whole story. `genCssVariables` emits `--v-theme-<key>` **verbatim, without kebab-casing**.
So a camelCase key such as `onSurface` emits `--v-theme-onSurface`, which nothing in the
app reads, and the auto-derivation then fills the `--v-theme-on-surface` the app *does*
read with `getForeground()`'s pure black or white. Every camelCase `on*` key in both
themes was therefore inert. Measured at runtime before the fix:

| Key | Authored | Actually rendered | Ratio on its fill |
|---|---|---|---|
| light `onSurface` / `onBackground` / `onPanel` | `#23211d` | `#000000` | pure black, banned by visual-language §4 |
| dark `onSurface` / `onBackground` / `onPanel` | `#f2e5da` | `#ffffff` | — |
| dark `onAccent` | `#1b1b1b` | `#ffffff` | **2.40:1** |
| dark `onPrimary` | `#111111` | `#ffffff` | **2.76:1** |
| dark `onTertiary` | `#0f1418` | `#ffffff` | **2.84:1** |

The kebab-cased keys already in the theme (`on-dark-surface`, `on-sidebar-hover`) always
worked. A missing or misspelled `on-*` is never *absent* — it is present and wrong.

> **Superseded for the last three rows (2026-07-23).** Dark `on-accent`, `on-primary`
> and `on-tertiary` were first fixed by flipping the *labels* to the warm near-black,
> which cleared the checker and left three fills with dark labels beside five with white
> ones. The maintainer has since set a system invariant: **the foreground on `accent`,
> `primary`, `secondary` and `tertiary` is `#ffffff`, in both themes, always.** So the
> *fills* moved instead — dark `accent` `#f28f3b`→`#b85c0c`, `primary`
> `#8EA604`→`#6b7d04`, `tertiary` `#77A0A9`→`#547b84`, `secondary` `#DA4167`→`#d13a5f`,
> light `accent` `#b0732b`→`#9e6727` and `tertiary` `#5f8790`→`#557982` — and all eight
> `on-*` are now white at 4.59 – 4.84:1. Values, arithmetic and knock-ons (the focus
> ring, the dark washes, `sidebar-hover`, `dark-surface-primary`) are in
> `visual-language.md` §4, "The action-fill tier". **The four `on-<status>` values below
> are unaffected** — statuses are not part of that tier and keep their measured
> foregrounds.

#### What was actually authored

All keys kebab-cased, and the light theme's `success` / `info` deepened first (§10.8),
which changes which foreground is correct for them:

```js
// pixlStashLight — on the deepened hues, white is what clears 4.5:1
'on-error':   '#ffffff',  // 4.86:1 on #cf3b30
'on-warning': '#23211d',  // 4.95:1 on #b8861f  (warm near-black, not #000)
'on-success': '#ffffff',  // 5.13:1 on #2e7d32
'on-info':    '#ffffff',  // 5.16:1 on #1a6ec4

// pixlStashDark — bright fills, so all four take the warm near-black
'on-error':   '#1b1b1b',  // 4.68:1 on #f44336
'on-warning': '#1b1b1b',  // 5.53:1 on #db7900
'on-success': '#1b1b1b',  // 6.20:1 on #4caf50
'on-info':    '#1b1b1b',  // 5.51:1 on #2196F3
```

The warm near-black proposed here for light `on-success` / `on-info` does **not**
survive the deepen, and that is arithmetic, not preference. Keeping it would require the
hue's contrast against white to stay in the window 3.16 – 3.50:1: below 3.16 the hue
fails the 3:1 UI floor on the canvas, above 3.50 the near-black fails 4.5:1 on the fill.
That window has under 0.1 of margin at both ends, so a hue inside it is one rounding
error from failing in both directions. Deepen properly and take the white.

#### `on-<status>` means "on the SOLID fill"

The one real trap the fix introduced. `on-<status>` is authored against
`rgb(var(--v-theme-<status>))` at full opacity. On a **translucent** status fill the
value is simply wrong, because the fill blends toward the surface underneath. Three
shipped components had exactly that, and the fix would have broken them:

| Site | Fill | Before | After the naive fix | Resolution |
|---|---|---|---|---|
| `ImageOverlay .overlay-comfy-warning` | `rgba(warning, .2)` on the lightbox | 11.38 / 12.99 | **1.41 / 1.33** | tint → `dark-surface-warning`, text → `on-dark-surface` (9.18 / 10.51) |
| `ImageOverlay .overlay-comfy-error` | `rgba(error, .2)` on the lightbox | 12.92 / 13.89 | 12.92 / **1.24** | same (9.89 / 11.24) |
| `SideBar .sidebar-error-bubble` | `rgba(error, .8)` | 3.73 / 4.94 | 3.73 / **3.49** | fill made solid (4.86 / 4.68) |
| `ProjectFiles .pf-file-delete:hover` | `rgba(error, .8)` | 3.59 / 4.94 | 3.59 / **3.49** | fill made solid (4.86 / 4.68) |

Rule: on a tint, the foreground is the *surface's* own (`on-surface` /
`on-dark-surface`), never `on-<status>`.

#### `dark-surface-<status>` — the fourth token family

Deepening the light theme's `success` and `info` broke a use the light values were
quietly serving: as **foregrounds on the deliberately-dark surfaces**, which stay dark
in *both* themes. Light `success` on `dark-surface` went 5.46 → **2.96**, and on its own
16% tint 4.23 → **2.55**. Eleven declarations across the review overlay and the lightbox
were affected.

A `dark-surface` cannot borrow the theme's status hue, because that hue is tuned for the
theme's *canvas*, which is the opposite lightness. So there is now a fourth family,
identical in both themes and equal to the dark-tuned hues:

```js
'dark-surface-error':   '#c9786f',   // 4.63:1 on #242628, 5.26:1 on #181b20
'dark-surface-warning': '#e8912f',   // 6.16 / 7.01
'dark-surface-success': '#5d9c6c',   // 4.65 / 5.29
'dark-surface-info':    '#6b92b0',   // 4.60 / 5.23
```

**This family is a FOREGROUND family, and that is the whole point.** Updated
2026-07-26, after the values above were briefly replaced by the deep fill hues.
`6e14c32c` deepened the four status hues — correct for the fill tier, where a deep
hue is what lets a warm near-white label clear 4.5:1 — and the same four values were
applied here in the same pass. That inverted the family's purpose: these are not
fills that carry a label, they are the label. On `#242628` they fell to `error`
**2.51**, `info` **2.48**, `success` **2.97**, against the 4.5:1 those 11px semibold
`ReviewRail` buttons need. The current values are the same four hues lifted toward
white until they read as text, so the palette stays one family with two lightness
poles: deep for fills, light for foregrounds on dark chrome.

The earlier values (`#f44336` / `#db7900` / `#4caf50` / `#2196F3`) were not restored:
`#f44336` measured 4.12:1 here, already under the floor, and saturated Material
primaries now sit beside the deepened fills.

Arithmetic for every pair in both themes is enforced by
`frontend/src/utils/contrastAudit.test.js`; `npm run audit:contrast` prints the table.

The eleven `success` declarations moved onto it, as did the notice host's `--on-dark`
variant (§2.5) and the two `ImageOverlay` chips above. **Not** migrated: the ~40
`error`-on-dark-surface declarations in the review overlay, which measure 3.12:1 with
the light theme's `error` — a **pre-existing** failure unrelated to this change, and a
mechanical migration large enough to want its own eyeball pass. Logged in §10.

---

## 4. Anatomy

```
┌─┬────────────────────────────────────────────────────────┬───┐
│▐│  ⚠   Couldn't restore the scrapheap.        UNDO       │ ✕ │
└─┴────────────────────────────────────────────────────────┴───┘
 rail  glyph  message                          action    dismiss
```

| Part | Spec |
|---|---|
| **Card** | `background: rgb(var(--v-theme-surface))` with the `rgba(status, .08)` tint layer over it · `border: 1px solid rgba(status, .5)` · `border-radius: var(--radius-md)` · `box-shadow: var(--elevation-3)` · `overflow: hidden` (so the rail follows the radius) |
| **Width** | `min(100% - 2 * var(--space-5), var(--notice-max-w))`, `min-width: 280px` |
| **Padding** | `var(--space-4)` block, `var(--space-5)` inline (the rail sits outside the inline padding) |
| **Rail** | inline-start edge, `var(--space-2)` (4px) wide, full card height, `background: rgb(var(--v-theme-<status>))` |
| **Glyph** | MDI outline icon per §3.1, `font-size: var(--text-md)` (16px), `color: rgb(var(--v-theme-on-surface))`, optical centre aligned to the message's first line, `aria-hidden="true"` |
| **Gap glyph→message** | `var(--space-3)` |
| **Message** | `var(--text-base)` (14px) · `var(--weight-regular)` · `line-height: var(--leading-snug)` (1.35) · `color: rgb(var(--v-theme-on-surface))` · clamps at 3 lines (`-webkit-line-clamp: 3`) with the full string in `title` |
| **Action** (optional) | text button · `var(--text-sm)` (13px) · `var(--weight-semibold)` · `color: rgb(var(--v-theme-on-surface))` · `text-decoration: underline; text-underline-offset: 2px` · `padding: var(--space-2) var(--space-3)` · `border-radius: var(--radius-sm)` · hover `background: var(--hover-wash)` · focus `box-shadow: var(--focus-ring)` |
| **Gap message→action** | `var(--space-5)`, action pushed to the inline end with `margin-inline-start: auto` |
| **Dismiss** | icon button, `mdi-close` at 16px, 24×24 visual box, `border-radius: var(--radius-sm)`, `color: rgba(var(--v-theme-on-surface), 0.7)` → `1` on hover, hover `background: var(--hover-wash)`, focus `box-shadow: var(--focus-ring)`, `aria-label="Dismiss notification"` |
| **Dismiss hit area** | expanded to 40×40 with a transparent `::before` inset expansion — clears WCAG 2.5.8 (24×24) with room, without a 40px hole in the layout |
| **Gap action→dismiss** | `var(--space-2)` |

**Action labels name the destination, not the question.** The label is a button, so it
reads as the thing the user gets: `Undo`, `Retry`, `Help`, `Open set`. A label phrased as
the user's own question — `Why?` — is the one shape to avoid: it puts a question mark in a
row of verbs, reads as the card interrogating itself, and at 13px semibold a two-character
word plus punctuation is a thin target next to a 24px ✕. `Why?` on the locked-delete card
was corrected to `Help` for exactly this reason.

**Why the action is not `primary`.** `primary` olive on the tinted card measures
**4.30 – 4.45:1** in light (at or under the 4.5 floor for 13px text) and, since the
action-fill deepen (`visual-language.md` §4), **2.85 – 3.00:1** in dark — it was
4.75 – 5.00:1 before, so this is now a clear fail rather than a near miss. Underlined
semibold `on-surface` is 14.3:1, is unambiguously actionable without colour (WCAG 1.4.1),
and keeps the accent unspent. This is also a worked example of the tier rule: an action
fill is not a small-text foreground on a canvas or on a tint.

**Why `--elevation-3` and not `-4`.** `-4` is reserved for dialogs and lightbox chrome
(§7 of the visual language). A notice must read as lighter than a modal, and this is one
of the few places the elevation ladder actually carries meaning.

**Why `--radius-md` and not `--radius-pill`.** The pill radius belongs to short
single-line status pills. A notice holds a sentence and possibly two controls; at pill
radius the inline padding has to grow to keep the text off the curve, and the card stops
matching the dialogs it is a sibling of.

**No title, no multi-paragraph body, no second action.** See the one-sentence rule (§1).

---

## 5. Stacking, limits and ordering

- **Max visible: 3** (2 below 600px). Beyond that the surface stops being a notification
  and becomes a wall.
- **Newest at the bottom**, closest to the bottom edge; older cards are pushed up.
  Entry motion is a rise from below (§7), so the newest card arrives where the eye
  already is and never displaces the one being read at the top of the stack.
- **Overflow waits in the store.** Notices past the cap are not rendered; they appear as
  visible ones dismiss, in push order. This requires a store change — see §9.2, because
  today the store's timers run on invisible notices and a queued notice can expire before
  anyone sees it.
- **Errors outrank.** If the cap is reached and an `error` arrives while the stack holds
  only non-errors, the **oldest non-error is dismissed immediately** to make room. An
  error is never queued behind a success.
- **Duplicates coalesce.** Two notices with the same variant and the same text collapse
  into one card carrying a `×N` count (`--text-2xs`, `--weight-semibold`,
  `font-variant-numeric: tabular-nums`, `color: rgba(var(--v-theme-on-surface), 0.7)`,
  placed immediately after the message). This is not a nicety: a bulk operation over 50
  selected pictures that fails per-picture would otherwise push 50 sticky error cards and
  bury the application. Requires a store change — §9.1.
- Stack reflow when a card is added or removed animates per §7.

---

## 6. Duration and dismissal

The store's `DEFAULT_TIMEOUTS` are close to right and are kept:

| Variant | Auto-dismiss | Verdict |
|---|---|---|
| `success` | 3000 ms | keep |
| `info` | 4000 ms | keep |
| `warning` | 6000 ms | keep — a partial outcome takes longer to parse than a confirmation |
| `error` | `0` = **sticky** | keep. **Errors must not auto-dismiss.** A failure the user has to act on cannot vanish while they are reading it. |

Three rules on top, none of which the store enforces today:

1. **A notice carrying an `action` does not auto-dismiss *by default*,** whatever its
   variant. A 3s window to hit "Undo" is a failure of WCAG 2.2.1 and of common sense. If
   a level default would apply, it is overridden to sticky.

   **This is a default, not a law.** A caller passing an explicit `timeout` overrides it.
   The rule was written for actions that stay true forever ("Undo", "Retry"); it is wrong
   for a card whose sentence is about the *current* state, which should expire rather than
   sit there needing a manual ✕. Two things make the override safe: the hover/`:focus-within`
   pause (rule 3) already satisfies WCAG 2.2.1 on its own, and rule 2's floor still guarantees
   the sentence is readable. Use it only where the sentence has its own expiry — and pair it
   with a scope (§9.6), because a timeout alone does not know the selection changed.

   **Do not stretch the window to cover the fix the card names.** A notice that says
   "unlock the set" should be long gone before the unlock happens: that is several clicks
   deep in a sidebar context menu, and no notice should follow a user through a menu. The
   card's job is to report and to point; the lock badge's tooltip carries the same copy for
   anyone who needs it after the card is gone.
2. **Reading-time floor.** Effective timeout =
   `max(levelDefault, min(12000, 2000 + 60 × characterCount))`. A 110-character success
   message does not get 3 seconds. The 12s ceiling bounds the *computed* reading time; it
   does not cap a window a caller chose deliberately.
3. **The timer pauses** on `:hover`, on `:focus-within`, and while `document.hidden`;
   it resumes on leave / on visibility. Required by WCAG 2.2.1 (Pause, Stop, Hide) —
   `setTimeout` alone cannot satisfy it. §9.3.
4. **A notice that describes the current context dies with it.** Rules 1 and 3 mean a card
   carrying an action has *no* deadline at all, which is right for "Undo" (true forever)
   and wrong for "3 of the selected pictures are in locked sets" (true only of that
   selection). The second kind declares a scope; when the scope changes, it is dismissed.
   §9.6.

**Dismissal routes**

- The ✕ button (always present, every variant).
- The action, if invoked: **invoking the action dismisses the notice** unless its handler
  explicitly returns `false`. §9.4.
- Timeout, for non-sticky variants.
- `Esc` dismisses the newest notice **only while focus is inside the notice host.**
  A global `Esc` binding is not available: `Esc` already clears the grid selection and
  closes the plugin/ComfyUI/tag menus in `SelectionBar`. Making `Esc` steal from those is
  a behaviour change and is UI/UX's call, not this document's.
- Scope invalidation, for a notice that declared one (§9.6).
- `clear()` on route change: **no.** A notice reporting a failed background operation
  must survive navigation; that is the whole point of a central queue. Scope
  invalidation is not a version of this and does not weaken it: it retires *named* keys
  whose own sentence has expired, chosen by the code that pushed them. A blanket clear
  destroys bystanders — the failed-upload card the user navigated away to go fix.

---

## 7. Motion

All existing tokens. Nothing here uses `--dur-4` — a notice is routine feedback, not a
delight moment.

| Transition | Tokens | Properties |
|---|---|---|
| **Enter** | `var(--dur-2)` (200ms), `var(--ease-decelerate)` | `opacity: 0 → 1`, `transform: translateY(var(--space-4)) → none` (a 12px rise from below) |
| **Exit** | `var(--dur-1)` (150ms), `var(--ease-accelerate)` | `opacity: 1 → 0`, `transform: none → translateY(var(--space-2))` |
| **Stack reflow** (siblings moving when one is added or removed) | `var(--dur-2)`, `var(--ease-standard)` | `transform` only — a FLIP translate, never an animated `height`, which reflows the whole column every frame |
| **Safe-bottom change** (the pill appears or goes) | `var(--dur-2)`, `var(--ease-standard)` | `bottom` on the host container |

Enter uses `--ease-decelerate` (entering the screen), exit uses `--ease-accelerate`
(leaving it), per §10 of the visual language. Exit is deliberately faster than enter: a
notice that is being dismissed has already been read.

**Reduced motion.** `design-tokens.css` already collapses every duration globally under
`prefers-reduced-motion: reduce`; do not override it. The notice needs its own explicit
fallback on top, because a zero-duration transition still *snaps* a `translateY` into
place, which is the flicker the setting exists to prevent:

```css
@media (prefers-reduced-motion: reduce) {
  /* opacity-only cross-fade: no rise, no exit slide, no FLIP reflow */
  .notice-enter-from, .notice-leave-to { transform: none; }
  .notice-move { transition: none; }
}
```

No pulse, no glow, no sound, no attention loop. The landing pulse (§10 of the visual
language) belongs to work landing in the sidebar, not to a message about it.

---

## 8. Accessibility

**Roles, per card, derived from the variant.**

| Variant | `role` | `aria-live` |
|---|---|---|
| `info`, `success`, `warning` | `status` | `polite` (implicit) |
| `error` | `alert` | `assertive` (implicit) |

Only `error` interrupts. A `warning` is a partial outcome, not an emergency; announcing
it assertively would train users to ignore assertive announcements, which is how the real
errors get missed. Every card also carries `aria-atomic="true"` so the message is read as
a unit rather than word-diffed as it clamps.

The host container itself carries **no** role and no `aria-live` — it must not
double-announce its children. It is `aria-label`-less and invisible to the accessibility
tree except through the cards.

If QA finds a screen reader that misses a dynamically inserted `role="alert"` node, the
fallback is a pair of always-present visually-hidden `role="status"` / `role="alert"`
mirror regions that receive the text while the visible cards go `aria-hidden`. Specify it
only if measured; do not build it speculatively.

**Focus.**

- **A notice never takes focus.** No `autofocus`, no focus trap, no programmatic
  `.focus()`. It appears while the user is mid-task and must not move their cursor.
- The host mounts as the **last child of `.app-viewport`**, so its action and dismiss
  buttons come last in DOM order — a keyboard user reaches them after the page content,
  not before it.
- Both buttons are real `<button>`s in the natural tab order, showing `--focus-ring` on
  `:focus-visible`.
- Reaching an action by keyboard is guaranteed by the §6 rule that any notice with an
  action is sticky. Without that rule, a keyboard-only user can be structurally unable to
  reach an "Undo".
- `:focus-within` pauses the timer, so tabbing into a notice does not race it.

**Contrast, verified against the shipped themes** (WCAG floors: text 4.5:1, non-text 3:1):

| Element | light | dark | floor |
|---|---|---|---|
| Message, `on-surface` on the tinted card | 14.30 – 14.97 | 10.61 – 11.18 | 4.5 |
| Glyph, `on-surface` | 14.30 – 14.97 | 10.61 – 11.18 | 3.0 |
| Action label, underlined `on-surface` | 14.30 – 14.97 | 10.61 – 11.18 | 4.5 |
| Dismiss glyph, `on-surface` @ 0.7 | 5.60 | 6.32 | 3.0 |
| `×N` count, `on-surface` @ 0.7 | 5.60 | 6.32 | 4.5 |
| `--on-dark` variant, `on-dark-surface` on the 14% tinted dark card | 9.86 – 11.03 | — | 4.5 |

The rail and the 1px border are decorative reinforcement — the glyph shape and the text
carry the variant, so neither is a "graphical object required to understand the content"
under 1.4.11. They are still solid, large-area marks and read clearly at 2.59:1 – 4.72:1.

**Other.**

- Dismiss hit area 40×40 (WCAG 2.5.8 floor is 24×24).
- Auto-dismiss is pausable and every notice is manually dismissible (WCAG 2.2.1).
- Variant is never carried by colour alone: glyph shape + message text (WCAG 1.4.1).
- The action is underlined, not colour-differentiated (WCAG 1.4.1).
- Message clamps at 3 lines but the full text stays available via `title`, so nothing is
  permanently truncated out of reach.

---

## 9. What the store has to change

`useNoticeStore.js` is close, and its `{ id, level, text, timeout, action }` shape is the
right shape. Four gaps block adoption, and one is serious.

**9.1 No coalescing key — this one is a blocker.** `push()` always appends. The store's
first real adopters are the `ImageGrid` catch blocks, which run per-picture inside
`Promise.all` over a bulk selection. Fifty failures become fifty sticky error cards.
Add an optional `key` to `push()`; pushing with a key that is already live updates that
notice's text and increments a `count` instead of appending. The host renders `count > 1`
as the `×N` badge (§5).

**9.2 No cap, and timers start on invisible notices.** `notices` is unbounded and every
notice's `setTimeout` starts at push time. If the host renders only 3, the 4th's timer
runs while it is off-screen and it can expire before anyone sees it — a silently lost
message, which is the bug the store exists to prevent. Either cap in the store and hold
the overflow in a separate pending queue whose timers start on promotion, or have the
host report visibility and start timers from that. The store should own it; the host
should not have to.

**9.3 No pause/resume.** WCAG 2.2.1 needs the timer to pause on hover, on focus and while
the tab is hidden. `setTimeout` is fire-and-forget. Add `pause(id)` / `resume(id)`
(remaining-time bookkeeping), or `pauseAll()` / `resumeAll()`.

**9.4 `action` has no dismissal contract.** `{ label, handler }` says nothing about
whether invoking it closes the notice. Specify: **invoking the action dismisses the
notice unless the handler returns `false`**, and enforce §6 rule 1 in `push()` — if
`action` is set, the resolved timeout becomes `0`.

**9.5 Minor.** `text` is coerced with `String(text ?? "")`, so `push({})` yields a card
with no message. The store should refuse an empty-text notice (and log it) rather than
leave the host to filter blanks.

**9.6 Scoped notices — added in use, not in review.** The five gaps above were found by
reading the store. This one was found by using it: the locked-delete warning stayed on
screen after the selection it described was gone, because rule 1 of §6 makes any
action-carrying notice sticky and nothing else could take it down. A sticky card that
asserts something about the *present* has a second deadline the store knew nothing about.

**Two deadlines, not one.** Scope answers "is this still true?"; the timeout answers "has
this been read?". A card about the current state needs both, and the fix is not to pick
one. Scope alone leaves the card up indefinitely whenever the user simply stops touching
the selection — reading their email with a stale warning on screen. A timeout alone leaves
it up through a selection change it no longer describes. So the locked-delete card takes
the ordinary `warning` window (explicitly, to opt out of rule 1's sticky default) *and*
declares a scope, and whichever deadline lands first wins.

The contract:

- `dismissByKey(key)` retires every live notice under a key. Coalescing already caps that
  at one, but a scope owns a small **family** (a card and its follow-up), and the
  invalidating code never saw their ids.
- `useScopedNotice(keys, signature)` (`composables/useScopedNotice.js`) binds a family to
  a getter over everything the message asserts. Signature changes → family dismissed.
  For the locked-delete pair that is: the selected ids, the view they are selected in,
  and the locked-set membership. The last matters most — unlocking the set is the fix the
  card asks for, so leaving the warning up would make the fix look like it failed.
- **`arm()` is deferred by one tick, and that is load-bearing.** The operation that pushes
  such a notice usually mutates the state the signature watches (the bulk delete narrows
  the selection to the frozen survivors, *then* reports what it skipped). Arming at push
  time makes the watcher fire on the pusher's own mutation and dismiss the card before it
  is read. `arm()` therefore marks the family pending synchronously — so the already-queued
  watcher job stands down — and records the real baseline in `nextTick`. Without the
  synchronous half, the *second* locked delete in a row is silent: the first card is still
  live, its context change is queued, and that pending job kills the refreshed card.
  Both halves are pinned by tests.

This applies to any future notice whose sentence contains "the selected", "this", or a
count of the current state. It does **not** apply to event reports ("Import finished"),
which are true forever and need only a timeout.

Nothing else needs to change. In particular the store should **not** grow a `title`
field, a second action, or per-notice styling — those all break the one-sentence rule and
turn the surface into a second dialog system.

---

## 10. Drift found on the way

Items 7 and 8 were the significant ones. **All eleven are now resolved or explicitly
carried**, with the disposition on each item below (2026-07). Everything marked FIXED
moves pixels somewhere; the list is the record of where to look.

| # | Item | Disposition |
|---|---|---|
| 1 | handoff §6 misdescribes `SelectionBar` | **FIXED** — §6 now names both bar shapes |
| 2 | `SelectionBar` is in `panels/`, not `widgets/` | **FIXED** — corrected in §6 and visual-language §13 |
| 3 | hardcoded cold shadows (4 sites) | **FIXED** — pill and menu → `--elevation-3`, the three `ImageGrid` pills → `--elevation-2` |
| 4 | off-grid / `em` values in `SelectionBar` | **FIXED** except block padding — see below |
| 5 | `.selbar-pop` raw `0.22s ease` | **FIXED** — split into decelerate-in / accelerate-out |
| 6 | `.remove-btn` broken `on-warning` | **FIXED** — the token is now authored (4.95 / 5.53) |
| 7 | four `on-<status>` derived, 7/8 fail WCAG | **FIXED** — all eight authored, plus 7 sibling pairs the same bug had silenced (§3.3) |
| 8 | light `success` / `info` never deepened | **FIXED** — `#2e7d32` / `#1a6ec4`, and the comment corrected |
| 9 | 31 native `alert()` calls | **FIXED** by the notice-host lane, not this one |
| 10 | filled `mdi-alert` | **FIXED** — 4 sites onto `mdi-alert-outline` |
| 11 | no z-index scale | **SCALE SHIPPED, migration carried** — see below |

**Carried, deliberately:**

- **`SelectionBar`'s block padding (`6px`)** stays off-grid. It sets the pill's occupied
  *height*, and that dimension belongs to the UI/UX-gated action-bar reconciliation
  (visual-language §5/§13: the 34 / 40 / 48 / 56px drift). Moving it piecemeal pre-empts
  that decision. Its inline padding, gap, radii, shadow and type are all on tokens.
- **The ~40 `error`-on-`dark-surface` declarations** in the review overlay measure 3.12:1
  and want `dark-surface-error` (4.12:1). Pre-existing, unrelated to this change, and a
  large mechanical migration that deserves its own eyeball pass.
- ~~**`primary` on `dark-surface`** (`.rs-tally-added`, `.rs-archived-added`)…~~
  **DECIDED, 2026-07-23.** The family gets a fifth member: **`dark-surface-primary:
  #8EA604`**, identical in both themes, exactly like the four status members. Measured
  **5.50:1** on `#242628` and **6.25:1** on `#181b20`, against 3.14:1 for the light
  theme's olive today (and 3.30:1 for the deepened dark one) — both of which fail the
  4.5:1 body floor these two spans need, because they are small text. The value is the
  dark theme's *outgoing* `primary`: a good foreground on a dark card, a bad fill under a
  white label, so it moves to the token whose whole job is the former. The two
  declarations point at it; their siblings `.rs-tally-kept` / `.rs-archived-kept` already
  read `dark-surface-success`. Full rationale in `visual-language.md` §4.
- **The 40+ raw z-index call sites.** The ladder is shipped (`--z-base` … `--z-modal`,
  visual-language §14) so new code has a target; retrofitting the existing ones is
  opportunistic, because each move is pixel-visible on a different screen.

The original findings, kept for the evidence:

1. **`design-system-handoff.md` §6 misdescribes its own reference component.** It states
   the selection bar is a full-width bar on `toolbar`/`panel` at `--bar-height` with
   `--elevation-2`. The shipped `SelectionBar.vue` is a floating centred pill on
   `rgba(surface, .86)` with `border-radius: 999px`, `backdrop-filter: blur(12px)` and a
   hardcoded shadow. §6 needs to either describe the pill or state that the pill is drift
   scheduled for migration; right now it describes something that does not exist.
2. **`SelectionBar.vue` lives in `components/panels/`, not `components/widgets/`.**
   The handoff and the surrounding docs imply `widgets/`.
3. **Hardcoded cold shadows.** `SelectionBar` `.floating-selection-bar`
   `0 8px 28px rgba(0,0,0,0.35)` and `.plugin-menu-panel` `0 8px 28px rgba(0,0,0,0.3)`
   are `--elevation-4`'s exact geometry with the token colour replaced by cold black.
   `.grid-range-pill` and `.grid-breadcrumb` in `ImageGrid.vue` both use
   `0 1px 6px rgba(0,0,0,0.22)`. All four should be `var(--elevation-N)`.
4. **Off-grid and off-ramp values in `SelectionBar`:** `bottom: 18px`, `gap: 6px`,
   `padding: 6px 10px`, `max-width: calc(100% - 24px)`, `border-radius: 999px` (literal,
   not `--radius-pill`), `border-radius: 5px` on the buttons (not in the radius set),
   40px button boxes, and `font-size: 1.1em / 0.85em / 0.88em / 0.92em` — `em` sizing is
   explicitly forbidden by §3 of the visual language. Fixing `bottom: 18px` → `--space-5`
   is a prerequisite for the §2.2 arithmetic landing on tokens.
5. **`.selbar-pop` uses `0.22s ease`**, not `--dur-2` / `--ease-*`.
6. **`SelectionBar.vue:805`** `.remove-btn` uses `rgb(var(--v-theme-on-warning))` — an
   implicitly Vuetify-derived token that resolves to `#fff` at 3.25:1 on the light
   `warning` fill. Same class as the `on-error` finding.
7. **All four `on-<status>` tokens are Vuetify-derived and seven of eight fail WCAG.**
   Full table and evidence in §3.3. `on-success` at 2.78:1 fails even the 3:1 non-text
   floor. Vuetify's `getForeground()` also returns pure `#000`/`#fff`, and pure black is
   explicitly banned by §4 of the visual language. The handoff's §3 token table omits all
   four (as it does `sidebar-hover`, `on-sidebar-hover`, `overlay`, `focus` and `hover`),
   so nothing in the documentation reveals that they exist and are wrong.
8. **The light theme's `success` and `info` were never deepened.** `main.js` says the
   light theme's "status hues are deepened so they hold contrast on the light canvas" —
   true for `error` (`#cf3b30` vs `#f44336`) and `warning` (`#b8861f` vs `#db7900`), but
   `success` (`#4caf50`) and `info` (`#2196F3`) are the unmodified Material 500s in both
   themes. As a foreground on the light canvas they measure 2.59:1 and 2.88:1 — both
   below the 3:1 UI floor. This is why §3.2 keeps status hue out of the glyph.
9. **31 native `alert()` calls** across `ImageGrid.vue`, `ImageOverlay.vue`,
   `SideBar.vue`, `CharacterEditor.vue`, `PictureSetEditor.vue`,
   `OverlayDescriptionPanel.vue`, `useGridDragDrop.js` and `useStackOrdering.js`. Each is
   an unstyled, blocking, focus-stealing OS dialog inside a design-system app.
10. **`DeleteForeverDialog.vue` uses the filled `mdi-alert`** where the rest of the app
    uses the outline family.
11. **There is no z-index scale.** 40+ distinct values from `0` to `99999`, including two
    `z-index: 1000 !important`. Any new floating layer is currently placed by guesswork.
    A `--z-*` ladder is the right fix; §11 requests only the one value this surface needs.
    *(Correction: the codebase maximum is **100000**, not 99999 — `TitleBar.vue:188`,
    which `--z-notice` therefore ties rather than clears. The tie is harmless today only
    because the notice host is a later sibling in `.app-viewport` and the two never
    overlap; that is precisely the luck this item is about. Recorded in visual-language
    §14 as what has to move before `--z-notice` can drop to its ladder slot.)*

---

## 11. New tokens requested

Two were requested here; six more followed from the §3.3 / §10.8 fix.

**`--z-notice`** — **at its ladder slot of `5000`**, the top of a declared nine-step
`--z-*` ladder (`design-tokens.css`; documented in visual-language §14). No longer parked.

The migration that unblocked it: `TitleBar.vue:188` (100000) moved to a new closed rung
`--z-titlebar: 4500`, and `ImageImporter.vue:848` `.dlg-scrim` (99999) onto `--z-modal`.
Only those two were ever in `.app-viewport`'s stacking context — this spec's earlier
"three squatters" count was wrong: `.import-fly-chip` is appended to `<body>` and lives in
the ROOT context, where any positive value already clears the shell.

The title bar needed its own rung because `--z-modal` was not enough: `ReviewSessionsOverlay`
is itself a 4000 stacking context, so a tie there hands the win to whichever is later in the
DOM, and its full-viewport sub-scrim would paint over the window drag region and controls.

**Stacking no longer depends on DOM order**, which was the actual defect: `--z-notice`
previously *tied* the maximum and `NoticeHost` won only by being a later sibling of
`.app-viewport`. Proven by moving the host to first child and re-checking — before the
change the title bar won, after it the notice does, in both orders. Retrofitting the
remaining raw z-indexes stays opportunistic per finding 10.11; four survive harmlessly,
each verified to be inside a nested stacking context where it cannot compete.

**`--notice-max-w: 420px`** — in `design-tokens.css`, next to `--badge-size` and
`--bar-height`, which is the existing home for fixed component dimensions that are
neither spacing nor radii. `420px` is not invented: it is already the de-facto width of
`LockedDeleteNoticeDialog` (`max-width="420"`) and `.plugin-menu-panel` (`width: 420px`),
with `DeleteForeverDialog` at 440. Naming it stops the next floating card picking a
fifth number.

**The seven `--z-*` ladder steps** (`--z-base` … `--z-modal`) — see visual-language §14.
Declared so new code has a name to reach for; nothing is migrated onto them yet.

**Four theme colours, `dark-surface-<status>`** — in `main.js`, identical in both themes.
Not optional: deepening the light theme's `success` and `info` (§10.8) breaks their use
as foregrounds on the deliberately-dark surfaces, which stay dark in both themes. Full
reasoning and measurements in §3.3.

**Eight theme colours, `on-<status>`** — the §3.3 fix itself, plus the kebab-case
respelling of the seven camelCase pairs the same bug had silenced.

**One theme colour, `dark-surface-primary` (`#8EA604`, both themes)** — the fifth member
of the `dark-surface-*` family, for `primary` used as a foreground on a deliberately-dark
card. Decided 2026-07-23; see §10 and `visual-language.md` §4. It is not part of the
notice host, but it comes out of the same colour pass and is recorded here so the token
requests stay in one list.

**Not tokens, and deliberately so:** `--notice-safe-bottom`, `--floating-bottom-h`,
`--selbar-h` and `--breadcrumb-h` are **runtime layout variables**, computed per frame
from what is on screen. They belong in `style.css` alongside `--hover-wash`,
`--active-wash` and `--active-bar` — not in `design-tokens.css`, which holds fixed
scales. Putting a measured value in the token file would be the drift the token file
exists to stop.

---

## 12. Handoff

**Affected files (implementation, not done here):**

- New `frontend/src/components/widgets/NoticeHost.vue` (host + card) — one instance.
- `frontend/src/App.vue` — mount the host as the last child of `.app-viewport`; own
  `--floating-bottom-h` and the `ResizeObserver` that writes `--selbar-h`.
- `frontend/src/stores/useNoticeStore.js` — the five changes in §9.
- `frontend/src/styles/design-tokens.css` **and** `docs/design/design-tokens.css` — the
  two tokens in §11, kept in sync (the two files are deliberately not byte-identical, but
  their values must match).
- `frontend/src/style.css` — the `--notice-safe-bottom` / `--floating-bottom-h`
  declarations.
- `frontend/src/components/widgets/LockedDeleteNoticeDialog.vue` — retired in favour of a
  `warning` notice once the host is live (a behaviour change: UI/UX signs it off).

**Affected files for the action-fill tier (`visual-language.md` §4) — a separate lane:**

- `frontend/src/main.js` — six fill values, four `on-*` flips to `#ffffff`, the
  `sidebar-hover` / `on-sidebar-hover` pair in both themes, and one new key
  `dark-surface-primary` in both themes.
- `frontend/src/styles/design-tokens.css` — `--focus-ring` goes solid.
- `frontend/src/style.css` — dark `--hover-wash` `.14`→`.24`, dark `--active-wash`
  `.20`→`.34`, light `--active-text` `on-primary`→`on-surface`.
- `ReviewSessionView.vue` `.rs-tally-added`, `ReviewArchivedReceipt.vue`
  `.rs-archived-added` — onto `dark-surface-primary`.
- The `on-tertiary`-on-a-non-tertiary-surface sites in `SideBar.vue` and the
  `on-secondary` ones in `App.css` — see the drift list in `visual-language.md` §4 and
  `design-system-handoff.md` §9.

**Acceptance checks:**

1. With 12 images selected, push an error. The card sits exactly `--space-3` above the
   selection pill in both themes, at every sidebar state, and neither overlaps.
2. Clear the selection while a notice is visible. The stack settles down to
   `--space-5` at `--dur-2`, without a jump.
3. At 375px width the card is full-width minus 12px gutters, the cap is 2, and it does
   not cover the breadcrumb.
4. Push 6 notices. Exactly 3 render, newest at the bottom; the other 3 arrive as the
   earlier ones dismiss; none expires unseen.
5. Push the same error text 50 times. One card, `×50`.
6. An error card never auto-dismisses. A success card with an action never auto-dismisses.
7. Hovering any card stops its countdown; leaving resumes it.
8. Focus never moves to a notice on appearance. Tabbing from the last page control
   reaches the action, then the dismiss, both with a visible `--focus-ring`.
9. VoiceOver / NVDA announce an error immediately and a success politely, once each.
10. With `prefers-reduced-motion: reduce`, cards cross-fade with no vertical travel and
    the stack does not slide on reflow.
11. Open the lightbox and push a notice: the `--on-dark` variant renders, above the
    overlay chrome.
12. No hardcoded hex, no `rgba(0,0,0,…)`, no `em` font-size, no off-grid spacing, and no
    radius outside the four steps anywhere in the new component.

**Additional acceptance checks for the colour fix (§3.3, §10.7, §10.8):**

13. Every text/glyph on a **solid** status fill is legible in both themes. The sites are
    `TitleBar .tb-close:hover`, `AppButton --danger`, `ProgressOverlay __abort`,
    `EmptyScrapHeap .delete-btn`, `SelectionBar .remove-btn`, `ImageOverlay
    .overlay-draw-cancel`, `ImageGrid`'s touch-select tick, `SideBar .sidebar-error-bubble`,
    `ProjectFiles .pf-file-delete:hover`, and the `variant="elevated"/"flat"` `color="error"`
    buttons in `RestoreConfirmDialog` and `FolderEditor`. **In dark mode these all flip
    from white to near-black text** — that is the fix, not a regression.
14. The review overlay and the lightbox render in the **light** theme: green ticks,
    tallies and the archive button still read as green against the dark card.
15. Warm neutrals: body text is `#23211d`, not pure `#000` (light), and `#f2e5da`, not
    pure `#fff` (dark). Status fills in dark mode (`error`, `warning`, `success`, `info`)
    carry the warm near-black `#1b1b1b`, not white. **`accent`, `primary`, `secondary`
    and `tertiary` buttons carry white labels in both themes** — that is the action-fill
    invariant (`visual-language.md` §4), and it is the opposite of what an earlier
    revision of this check said.
16. Focus a control in the **light** theme: the ring is a solid amber stroke, clearly
    visible against the near-white canvas (4.51:1). The old translucent ring measured
    1.96:1 there and was effectively absent.

---

## 13. The §12 open items, decided (2026-07-23)

The maintainer asked for calls rather than questions. Each item below is now a decision
with its rationale and its reversal cost. Two of the three are pure design; the third
touches a key binding, and the boundary with UI/UX is drawn explicitly inside it.

### 13.1 Notices while a blocking modal is open — **no suppression, all four variants render**

**Decision: every variant renders normally while a modal is open.** `info` and `success`
are not queued, not suppressed, not demoted.

Why, in order of weight:

1. **Suppression's failure mode is the bug this surface exists to fix.** A queued
   `success` whose timer is paused behind a dialog that stays open for two minutes is a
   message the user never sees — §9.2's "silently lost message", re-created deliberately.
   The alternative (queue it *and* start its timer) is worse: it expires unseen.
2. **It needs a global "a modal is open" signal that does not exist.** Every dialog in
   the app would have to register and de-register, and the first one that forgets
   reintroduces the noise inconsistently. That is a new cross-cutting invariant bought
   for a cosmetic gain.
3. **The geometry already separates them.** The notice host is bottom-anchored at
   `--notice-safe-bottom`; dialogs are centred. A notice does not cover a dialog, and at
   `--z-notice` it is legible above the scrim, which is the behaviour `error` needs and
   is not worth branching per variant.

Reversible cheaply: one guard in the host's render condition, if it ever reads as noisy
in practice. Flow-wise this is a rendering rule rather than a change to what any control
does, so it is decided here; UI/UX keeps a standing veto.

### 13.2 `Esc` — **host-scoped, and it stays that way until UI/UX says otherwise**

**Decision: `Esc` dismisses the newest notice only while focus is inside the notice
host.** No global binding.

`Esc` in this app already means three destructive-ish things: clear the grid selection
(which can be hundreds of pictures and is not undoable in one keystroke), close the
plugin / ComfyUI / tag menus in `SelectionBar`, and close the lightbox or a dialog. A
global notice binding would make the meaning of `Esc` depend on whether a transient card
happens to be on screen at that instant — the user presses `Esc` to clear a selection,
a success toast is still up, and the toast eats the keystroke. There is no affordance
that could tell them which one will win.

Every notice already has three other dismissal routes (the ✕, the timeout for non-sticky
variants, and the action). Nothing is unreachable, and the ✕'s hit area is 40×40.

**This one genuinely changes what a control does, so the line is:** *narrowing* to the
host is a confirmation of the safe default and is decided here; *widening* `Esc` to
global is a keyboard-model change across three existing consumers and belongs to
`ui-ux-expert`. Do not widen it in an implementation pass.

### 13.3 The centre-axis offset — **accepted; the host stays viewport-centred**

**Decision: keep the notice host centred on the viewport. Do not make it sidebar-aware,
and do not move `SelectionBar` into the notice stack.**

The offset is measurable and bounded. The pill is centred on `.grid-content-area`, whose
centre is displaced from the viewport centre by `(sidebarWidth − statsWidth) / 2`.
Sidebar is user-resizable **140 – 300px** (`SideBar.vue:1497-1498`); the stats panel is a
fixed **288px** (`StatsSidebar.vue:1805-1807`). So:

| Shell state | Axis offset (pill vs notice) |
|---|---|
| both panels closed | **0px** |
| sidebar only, at min / max width | **+70px / +150px** |
| stats only | **−144px** |
| both open, sidebar at min / max | **−74px / +6px** |

Worst case is **150px of horizontal offset at 78px of vertical separation** (§2.2), on
two objects of different width, shape and radius. Two things that never share a line and
never share an edge do not read as a failed alignment; they read as two objects. The
common case is smaller still — with both panels open the axes are within 74px and often
within 6px.

The rejected alternative is worse than the problem. Putting `SelectionBar` inside the
notice column couples a permanent control's position to a transient surface, which
breaks §2.2's rule that **notices move and the pill does not** — the pill would shift
whenever a message arrived, under a cursor already travelling toward it. Making the host
sidebar-aware instead breaks §2.3: the host renders on the login screen, over the
lightbox and inside Settings, none of which have a grid column to centre on.

Reversible cheaply (one container change) if it looks wrong on a wide monitor with only
the sidebar open, which is the worst case above.

### 13.4 Previously closed, kept for the record

- ~~Authoring the four `on-<status>` theme colours (§3.3) and deepening the light theme's
  `success` / `info` (§10.8).~~ **Done, 2026-07** — maintainer-approved. It reached
  further than expected: eight `on-<status>` values, seven camelCase `on*` keys
  respelled, four new `dark-surface-<status>` colours, and four components whose
  translucent status fills made `on-<status>` the wrong token. See §3.3.
- ~~The right token for `primary` on a `dark-surface`.~~ **Decided, 2026-07-23** —
  `dark-surface-primary: #8EA604`. See §10.

**Still open, and still not this document's call:**

- Migrating the ~40 `error`-on-`dark-surface` declarations in the review overlay onto
  `dark-surface-error` (3.12:1 → 4.12:1). Pre-existing, mechanical, large enough to want
  its own eyeball pass.
