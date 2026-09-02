# PixlStash Visual Language

> **⚠️ Colors superseded (2026-07-24).** The color system moved to the unified
> "Camp B" palette: ONE brand palette shared by both themes with warm-white
> `#f7f1ea` labels — accent/amber **`#c47a1e`** (glow `#e08a2a`), primary/olive
> `#567309`, secondary/raspberry `#bb3566`, tertiary/teal `#46707a`. The canonical
> color sources are now **`frontend/DESIGN.md`** and the design-system
> `tokens/colors.css`; the running values live in the Vuetify themes in
> **`frontend/src/main.js`**. The color hexes, contrast tables, and per-theme
> accent values in the sections below describe the *previous* (white-label,
> theme-dependent) system and are kept only as history — do not build against
> them. Everything non-color here (type, spacing, radius, elevation, motion,
> layout rules) is still current.

This is what PixlStash looks like. Not a mood board, a spec. Every value here is a
token you can build against, and every rule has a reason. Follow it and the product
reads as one considered thing on every screen. Ignore it and you get the drift this
document exists to stop.

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
  color: rgba(var(--v-theme-on-surface), 0.7);
}
```
The alpha is `0.7` because 11px semibold is **small** text under §4 and owes 4.5:1;
composited on `surface` it measures 5.94:1 light / 6.65:1 dark. It shipped at `0.5`
(3.19:1 light) until 2026-08. Do not lower it to restore a "quieter than the body"
look — an uppercase tracked semibold already separates on case, weight and tracking,
and a heading below its own hint text is inverted hierarchy, not restraint.

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
- **One accent.** Amber: `#9e6727` (light) / `#b85c0c` (dark). It marks the primary
  action and key state. Spend it sparingly.

### Token map (what to reach for)
| Need | Token |
|---|---|
| Page / grid canvas | `background` / `onBackground` |
| Raised control surface (card, input, menu) | `surface` / `onSurface` |
| Sidebar, toolbar, panels | `sidebar` / `toolbar` / `panel` (text pair: `sidebar-text` / `toolbar-text`, but `onPanel` for panel) |
| Brand accent, key state | `accent` / `onAccent` |
| Primary action button | `primary` / `onPrimary` |
| Secondary / tertiary action | `secondary` / `tertiary` |
| Divider line | `divider` (subtle) / `border` (visible) |
| Success / error / warning / info | `success` / `error` / `warning` / `info` (+ `on-*`, see below) |
| The same four, **inside a `dark-surface`** | `dark-surface-success` / `-error` / `-warning` / `-info` |
| `primary` as a foreground **inside a `dark-surface`** | `dark-surface-primary` |
| Hover / selection wash | `--hover-wash` / `--active-wash` (in `style.css`) |
| Shadow color (for elevation) | `shadow` |

Status meaning never rides on color alone — pair it with an icon or text. The many
ad-hoc greens (`#1e7d44`, `#258a4d`, `#81c995`…) and reds (`#e53935`, `#c62828`,
`#c5362d`…) in components collapse to the single `success` / `error` tokens. As of
2026-07 the live chrome offenders are down to a handful — a `#888` set-color-dot
fallback and `#c96000` / `#c62828` hover states in `SideBar.vue`, plus scattered
`#fff` glyph fills that should read `onAccent` / `on-dark-surface` / `currentColor`.

**Named decorative exemptions.** One multi-hue palette is *deliberately* off-token:
the review-celebration confetti in `ReviewCelebration.vue` (`#ffd166 #06d6a0 #ef476f
#4cc9f0 #f78c6b`). A burst of party colour is the point; forcing it onto the amber
accent would make it sad. It is exempt the same way brand/source marks are (§8) —
**named, scoped to one decorative component, and rare.** Do not "fix" it into tokens,
and do not treat it as licence for a second off-token palette without a new named
exemption here.

### The action-fill tier: `accent`, `primary`, `secondary`, `tertiary`

**The foreground on all four is `#ffffff`, in both themes, always.** This is a fixed
rule, not a per-fill contrast lookup. One label colour on every branded fill is what
makes a row of mixed buttons read as one family, and it removes the whole class of bug
where an `on-*` pair silently disagrees with its fill (§3.3 of `notice-surface.md`).

A white label forces the fill's lightness. The arithmetic, on the sRGB relative
luminance `L` of the fill:

| Requirement | Constraint on `L` |
|---|---|
| white on the fill ≥ **4.5:1** (body text) | `L ≤ 0.1833` |
| fill ≥ **3:1** on the dark canvas `background` `#1b1f24` (`L` 0.0134) | `L ≥ 0.1402` |
| fill ≥ **3:1** on dark `surface` / `sidebar` `#23282f` (`L` 0.0208) | `L ≥ 0.1624` |

So every dark-theme action fill lives in the window **`L` ∈ [0.1624, 0.1833]** — a
band roughly one HSL lightness step wide. The light theme only has the upper bound
(its canvas is near-white, so any fill this dark clears 3:1 there by a mile). All
eight values below sit at `L` ≈ 0.17.

**What this rules out, permanently.** A fill cannot both carry white at 4.5:1
(`L ≤ 0.1833`) and read as 4.5:1 *small text* on the dark canvas (`L ≥ 0.2353`).
The two windows do not overlap. That is arithmetic, not a compromise: **`accent`,
`primary`, `secondary` and `tertiary` are never small body text on a canvas.** As
foregrounds they are icons, borders, rails, ≥18px text, and ≥14px bold — the 3:1 UI
floor. The light theme already lived under this rule — its accent measured **3.74:1**
as a foreground on the canvas before this change, i.e. below the body floor already —
and the "amber is for large labels, icons, borders and washes" line has been in this
section since it was first measured. What changes is that the rule now covers all four
tokens, and covers the dark theme too, where these hues used to be comfortably above
4.5:1 as foregrounds (5.8 – 6.9:1) and no longer are.

#### The values

Only HSL **lightness** moves. Hue is untouched in the dark theme (H28 / H69 / H345 /
H191 before and after) and moves ≤1° in light. Saturation is held within 1–5 points.
No hue is re-picked, so nothing is re-branded — the amber is the same amber, deeper.

**Dark theme** (canvas `#1b1f24`, `surface`/`sidebar` `#23282f`, `dark-surface` `#181b20`):

| Token | Was | Is | white on it | vs `background` | vs `surface` | vs `dark-surface` |
|---|---|---|---|---|---|---|
| `accent` | `#f28f3b` (L59%) | **`#b85c0c`** (L38%) | 2.40 → **4.59** | 6.91 → 3.61 | 6.19 → 3.23 | 3.76 |
| `primary` | `#8EA604` (L33%) | **`#6b7d04`** (L25%) | 2.76 → **4.60** | 6.00 → 3.60 | 5.37 → 3.22 | 3.75 |
| `tertiary` | `#77A0A9` (L56%) | **`#547b84`** (L42%) | 2.84 → **4.62** | 5.82 → 3.58 | 5.22 → 3.21 | 3.73 |
| `secondary` | `#DA4167` (L55%) | **`#d13a5f`** (L52%) | 4.26 → **4.69** | 3.89 → 3.53 | 3.48 → 3.16 | — |

**Light theme** (canvas `#faf9f7`, `surface` `#ffffff`, `sidebar`/`toolbar` `#f0ede9`):

| Token | Was | Is | white on it | vs `background` | vs `surface` | vs `sidebar` |
|---|---|---|---|---|---|---|
| `accent` | `#b0732b` (L43%) | **`#9e6727`** (L39%) | 3.94 → **4.75** | 3.74 → 4.51 | 3.94 → 4.75 | 4.07 |
| `primary` | `#5c7c0a` | **unchanged** | **4.84** | 4.60 | 4.84 | 4.14 |
| `secondary` | `#cb3a72` | **unchanged** | **4.79** | 4.55 | 4.79 | 4.10 |
| `tertiary` | `#5f8790` (L47%) | **`#557982`** (L42%) | 3.92 → **4.73** | 3.73 → 4.49 | 3.92 → 4.73 | 4.05 |

All eight `on-*` values become `#ffffff`. Four of them already were; the four that
change are dark `on-accent` `#1b1b1b`, `on-primary` `#111111`, `on-tertiary` `#0f1418`
and (unchanged in value, now correct in ratio) `on-secondary`.

#### How far the brand moves

- **Light theme: barely.** `accent` drops 4 points of HSL lightness, `tertiary` 5.
  Side by side you can see it; from memory you cannot. `primary` and `secondary` — the
  two most-used fills in the light theme — **do not move at
  all.** The light theme is effectively unchanged. (It was the default theme when
  this was measured; dark is the default now, which raises the stakes of the
  dark-theme paragraph below rather than changing any value in it.)
- **Dark theme: visibly.** `accent` drops 21 points of lightness (`#f28f3b` bright
  orange → `#b85c0c` burnt amber) and `primary` 8. This is the real cost, and it is the
  most visible colour change in the product: dark-mode chrome that was lit by a bright
  amber now carries a deeper one, and accent-tinted marks on the dark canvas fall from
  ~6.9:1 to ~3.6:1 — still clearly visible, no longer *glowing*.
- **Verdict: this is the minimum viable shift, not a comfortable one.** With white
  labels mandatory, `L ≤ 0.1833` is a hard ceiling, and the chosen values sit at the
  *bright* end of the legal window (white at 4.59–4.75:1, i.e. 2–5% above the 4.5 floor
  rather than a safe 5.5:1). Any brighter and white fails; any darker and the fill stops
  separating from the dark canvas. There is no value that is both brighter and legal.
- **Reversal is one line per token.** If the dark accent reads too muddy in situ, the
  only lever is the directive itself: revert `on-accent` to the warm near-black
  `#1b1b1b` and restore `#f28f3b`. Nothing else in the system depends on which of the
  two it is.

#### Knock-ons this forces (all of them, measured)

Four things read the accent through an alpha and therefore move with it.

1. **`--focus-ring` must go solid.** `rgba(accent, .55)` was **already failing** before
   this change: 1.96:1 on the light canvas and 3.01:1 on the dark one, against a 3:1
   floor (WCAG 1.4.11 / 2.4.11). With the deeper accent it would fall to 1.90 – 1.95:1
   (on `surface` and on `background` respectively).
   The fix is full opacity, not a new colour:
   `--focus-ring: 0 0 0 3px rgb(var(--v-theme-accent))` — dark **3.61** on `background`,
   **3.23** on `surface`, **3.76** on `dark-surface`; light **4.51** / **4.75** / **4.07**
   (sidebar). Width stays 3px; changing it is a separate, pixel-moving decision.
2. **The dark washes need their alpha compensated** so the hover and selection steps
   keep the same perceived weight. In `style.css`, dark theme only — **the real
   from-values are `.08` and `.18`**, and `--active-wash` there is built on `primary`,
   not `accent`:
   `--hover-wash` `rgba(accent, .08)` → **`.14`** (step 1.138 → 1.072 after the deepen,
   restored to **1.136**) and `--active-wash` `rgba(primary, .18)` → **`.26`**
   (1.340 → 1.202, restored to **1.322**). The light washes are unchanged.

   > **Corrected.** This item originally published `.14`/`.20` as the from-values and
   > described `--active-wash` as accent-built — those are the *light* block's alphas
   > and the *dark* block's construction, i.e. the two themes were transposed. Applying
   > `.24`/`.34` to the actual `.08`/`.18` would have overshot to 1.263/1.453, visibly
   > stronger than before rather than equal to it. The implementer caught it by
   > reproducing the published step figures and finding they only hold for alpha `.14`
   > against the *dark* surface. The targets above **restore** today's weight rather
   > than strengthen it, so the accent deepen stays the only visible change.
3. **`sidebar-hover` is the accent duplicated, and both themes were broken.** Light
   `#b0732b` + `#ffffff` = 3.94:1; dark `#f28f3b` + `#f2e5da` = **1.94:1**. Point
   `sidebar-hover` at the new accent value in each theme and set `on-sidebar-hover` to
   `#ffffff`: **4.75:1** light, **4.59:1** dark.
4. **The retired bright olive gets a home:** `dark-surface-primary` — see the next
   subsection.

### `dark-surface-primary` — the fifth member of the `dark-surface-*` family

`dark-surface` stays dark in **both** themes, so a theme's own hue — tuned for that
theme's canvas — is the wrong value inside it. That is why `dark-surface-<status>`
exists, and `primary` has exactly the same problem: `.rs-tally-added` and
`.rs-archived-added` (`ReviewSessionView.vue`, `ReviewArchivedReceipt.vue`) set small
text in `primary` on a `dark-surface` card, measuring **3.14:1** in light and, after the
deepen, **3.30:1** in dark — both below the 4.5:1 body floor. Their immediate siblings
`.rs-tally-kept` / `.rs-archived-kept` already read `dark-surface-success`.

**Decision: extend the family.** `dark-surface-primary: #8EA604`, identical in both
themes, exactly like the four status members:

| | on `dark-surface` `#242628` (light) | on `#181b20` (dark) | on its own 16% tint over `#242628` |
|---|---|---|---|
| `dark-surface-primary` `#8EA604` | **5.50** | **6.25** | 4.27 |

The value is the dark theme's outgoing `primary`. That is the point: the bright olive is
a good foreground on a dark card and a bad fill under a white label, so it moves to the
token whose entire job is "foreground on a dark card" instead of being deleted. Reusing
`dark-surface-success` was rejected — "added" and "kept" are adjacent tallies on the same
card and must not be the same colour — and restyling the two spans was rejected because
the sibling already establishes the pattern.

Olive `#8EA604` and green `#4caf50` are adjacent on the same card; they are
distinguishable but close, so the tallies keep their word labels ("+ 12 added" /
"kept"). Status never rides on colour alone (below), and this is that rule applied to a
non-status pair.

### Status colors, and the three ways to get them wrong

There are three separate status jobs, and they need three different tokens. Reaching
for the wrong one is the single most common color bug in this codebase.

| The job | Reach for | Floor |
|---|---|---|
| Status hue as a **foreground / border / tint** on the theme's own canvas | `success` `error` `warning` `info` | 3:1 (UI) |
| Text or a glyph **on a SOLID status fill** | the matching `on-<status>` | 4.5:1 |
| Status hue anywhere inside a **`dark-surface`** (lightbox, review overlay) | `dark-surface-<status>` | 3:1 |

**`on-<status>` means "on the solid fill" — literally.** It is authored against
`rgb(var(--v-theme-<status>))` at full opacity. Put it on a translucent tint
(`rgba(status, .2)`) and it is simply the wrong color: the fill blends toward the
surface, and the near-black `on-warning` measured **1.41:1** on a 20% warning tint
over the lightbox. On a tint, the foreground is the *surface's* own — `on-surface`
or `on-dark-surface`. Three shipped components had this bug; all three now either
use a solid fill or the surface foreground.

**And it is not only a status-token bug.** The same mistake is live in four more places
with `on-primary`, `on-tertiary` and `on-secondary` — including light `--active-text`
(white on an 18% olive tint, **1.32:1**) and six `.sidebar-project-menu-*` rules that set
`on-tertiary` as a plain menu foreground with no tertiary fill under it at all
(**1.43 – 1.70:1** in light). Sites, measurements and fixes: `design-system-handoff.md`
§9.2. Generalise the rule: **an `on-<x>` token is correct only on a solid, full-opacity
`<x>` fill.** Seeing `on-<something>` in the same rule as an `rgba(...)` background — or
in a rule with no `<x>` background at all — is the tell.

**`dark-surface` needs its own status set** because it stays dark in *both* themes,
so a light-theme hue tuned for a near-white canvas is exactly wrong on it. The four
`dark-surface-*` values are identical in both themes and measure 4.12:1 – 5.46:1
there. This is why the light theme's status hues can be deepened at all.

### Contrast (proven, not eyeballed)
WCAG floors: body text **≥ 4.5:1**, large text and meaningful UI **≥ 3:1**.

Findings to honour:
- **Every action fill carries white at ≥ 4.5:1, in both themes** — 4.59 – 4.84:1
  across the eight values. See the action-fill tier above for the table and for the
  arithmetic that fixes their lightness. A white label on `accent`, `primary`,
  `secondary` or `tertiary` is now always correct, and is the only correct label.
- **The same four are never small body text on a canvas.** As foregrounds they measure
  3.53 – 3.61:1 (dark) and 4.49 – 4.60:1 (light) against `background`. Dark clears the
  3:1 UI floor and not the 4.5:1 body floor, and no value can clear both while carrying
  white — the windows are disjoint. So they are for icons, borders, rails, ≥18px text,
  and ≥14px bold. Historically this rule was written about the amber accent alone
  (3.94:1 in light); it now covers the whole tier.
- **The status hues on their own canvas** (light on `#faf9f7`, dark on `#1b1f24`):
  `error` 4.62 / 4.50 · `warning` 3.09 / 5.32 · `success` 4.87 / 5.96 · `info`
  4.90 / 5.30. All clear the 3:1 UI floor. Light `success` and `info` were Material
  500s until 2026-07 and measured 2.64 / 2.97 — **below the floor** — which is the
  reason they were deepened to `#2e7d32` and `#1a6ec4`.
- **A multi-segment meter steps its neutral to the extreme *away* from the canvas**,
  never to a mid-grey between the accent and the track. The range between the accent
  and the canvas is not wide enough for two 3:1 steps; the range *past* the accent is.
  Proven on the model shelf's drive band (#893), whose three segments are `ours` /
  `other` / `free` and therefore have two adjacencies to keep readable. Composited over
  `background`, light on `#faf9f7` / dark on `#1b1f24`:

  | pair | light | dark |
  |---|---|---|
  | `ours` `#567309` : `other` (`#1c160c` / `#d4c9c1`) | **3.29** | **3.36** |
  | `other` : `free` (`#e5e3e1` / `#121518`) | **14.03** | **11.28** |
  | `free` : canvas | 1.22 | 1.11 — *drawn*, not inferred (see below) |

  Three things this pins down, each of which was got wrong first:
  **(a)** The neutral is what steps per theme, not the accent — `primary` stays the
  "ours" colour in both, because a legend swatch that is orange in one theme and olive
  in the other is not one system. **(b)** Dark is stepped *independently*, never a
  flipped alpha: on a dark canvas an ink alpha can only lighten, so the neutral cannot
  pass below the olive, and the shipped `rgba(ink, .28)` measured **1.34:1** — the
  deuteranopia failure that opened the issue. The neutral goes light and the *track*
  takes the dark end via a `scrim` tint. **(c)** In light the neutral must go past the
  ink itself: `on-background` tops out at L .0153 and measures 2.95, just under the
  floor, so the fill is `shadow` `#1c160c` (L .0085).
  Separating the segments with a hairline instead was rejected: the issue's requirement
  is a *luminance* separation, and a drawn line is not one — with a quiet mid-grey
  neutral the pair measures 1.73:1, so in greyscale you would see two segments and be
  unable to tell which was which.
  The track is only 1.22 / 1.11 against the canvas, which would make a nearly-empty
  drive a ghost, so it carries a 1px `outline` at 4.24 / 5.67 rather than relying on a
  colour step. A meter's own frame is a legitimate substitute for a fill boundary; a
  line *between two fills* is not.
- **The eight `on-<status>` values, on their solid fill:** light `on-error` #ffffff
  4.86 · `on-warning` #23211d 4.95 · `on-success` #ffffff 5.13 · `on-info` #ffffff
  5.16; dark, all four `#1b1b1b`: `on-error` 4.68 · `on-warning` 5.53 · `on-success`
  6.20 · `on-info` 5.51. All clear 4.5:1.

**Author every `on-*` pair explicitly, and spell the key in kebab-case.** Vuetify
emits one CSS variable per theme key *verbatim* — `--v-theme-<key>` — so a camelCase
`onSurface` produces `--v-theme-onSurface`, which nothing reads, and Vuetify then
derives the `--v-theme-on-surface` the app actually consumes as pure `#000`/`#fff`
by APCA. That silently overrode seven authored pairs here: the warm near-black text
ramp was rendering as pure `#000` (banned above), and dark `on-accent`, `on-primary`
and `on-tertiary` were rendering as white at **2.40:1, 2.76:1 and 2.84:1**. A missing
or misspelled `on-*` is never absent — it is present and wrong.

Those three numbers are what the action-fill tier above resolves, and note *which way*
it resolved: the first pass fixed them by flipping the labels to the warm near-black,
which passed the checker and produced three fills with dark labels sitting next to five
with white ones. The rule is now "white always", so the fills moved instead of the
labels. Whenever a foreground and its fill disagree, decide which of the two is the
system's invariant before you pick the value that passes.

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
share a baseline; columns of controls share a left edge. The shell lines the sidebar
header, toolbar, and stats header to one horizontal band and you respect that band
everywhere. The band height is `--bar-height` (48px) in the browser; the desktop
Electron shell compresses the top strip (title bar `--titlebar-h` 34px, sidebar
header 36px) so the custom title bar and controls fit — see the overrides in
`style.css`. (Heads-up: the per-component action-bar heights still drift — 34 / 36 /
40 / 48 / 56px across `ImageGrid`, `ImageOverlay`, and the selection bar. The **three
view toolbars are the one settled group**: the grid, Duplicates and model-shelf bars
all sit at 36px, pinned to each other by a guardrail rather than to this token
(`toolbar-responsive-decisions.md` Amendment #5) — so no view toolbar honours
`--bar-height` today, deliberately. Unifying the rest onto `--bar-height` is an open
reconciliation item; it moves pixels, so it needs
UI/UX sign-off — see §13.)

### 5.1 The row grid (how a list row shares a left edge)

The claim above — *columns of controls share a left edge* — is the one §5 made and
never enforced. A list row is **four columns**, and the first two are reserved:

```
[disclosure gutter] [identity mark] [label 1fr] [actions auto]
   --gutter-glyph      --entity-thumb
```

**Columns 1 and 2 are never conditional.** A row with no chevron reserves the
gutter anyway; a row with no thumbnail reserves the identity column anyway. That is
the whole mechanism: a character with a face photo and one without share a left edge
because neither column ever collapses. Optional glyphs go `visibility: hidden`, never
`display: none`, and never `v-if`.

**Depth is a number, not a class.** A row carries `--depth: 0 | 1 | 2` and the grid
multiplies it:

```css
padding-inline-start: calc(var(--space-3) + var(--depth, 0) * var(--indent-step));
```

No per-level classes, no `!important` override, no nested wrapper adding padding and
a margin and a border that sum to an off-grid step.

**The selection rail is always present and always transparent.** Only its colour
changes on `.active`:

```css
border-left: 3px solid transparent;   /* base — always */
.active { border-left-color: var(--active-bar); }
```

Never add the border on select, and never "compensate" for it with padding. Adding
3px of border and 8px of padding on `.active` moves the label 3px right; that is a
bug that has shipped here twice, once under a comment asserting it does not happen.

**Rank is carried by type; depth is carried by indent.** Two rows at different
ranks must differ in *type* even when they also differ in indent, so neither
signal is load-bearing on its own. This is not decoration. When a project row and
the section captions nested under it were set identically (both `--text-2xs`,
semibold, uppercase), indentation had to signal both containment *and* rank, and
the only way to make rank legible was a bigger step. Three levels of that pushed
entity names to 91px in a 240px rail. Two ramp steps of size between project and
caption is what makes a 16px indent enough.

**Rank is size, weight and tracking. It is never opacity.** A structural label
must not be dimmer than the content it heads. Dimming the section captions to 0.7
made them the quietest thing in the column, sitting between a full-strength
project above and full-strength entity names below, which reads as a hole rather
than a hierarchy. Chrome recedes by being *smaller*, not by fading.

**A row's caret takes its row's colour and never its own opacity.** When only the
caption was dimmed, its caret was not, so the caret outshone the label it
belonged to while the project's caret sat far below its own. Tying a caret to a
fraction of its label does not fix it either: 0.7 of a 0.7 label is 0.49, which
measures 2.95:1 against the light sidebar and misses the 3:1 that a disclosure
glyph owes as a meaningful graphical object (WCAG 1.4.11). Leave both alone.

**The indent step is set by legibility, not by glyph arithmetic.** `--indent-step`
is 16px because that is the smallest on-grid step that still reads as nesting at
this density. It is deliberately *not* derived from the glyph column. An earlier
version set it to `--gutter-glyph + --space-3` = 24px so that one chevron would
advance exactly one level, and tuned a margin onto the glyph slot to make the sum
come out. That bought a child's chevron landing precisely under its parent's
label, which nobody notices, and charged 24px per level for it. Do not tune the
step and the glyph advance to each other; they answer different questions.

**Why the other two widths.** `--gutter-glyph` is 16px because the sidebar already
forced every chevron and row icon to 16px with `!important`. `--entity-thumb` is
24px, promoted from the sidebar's own `--sidebar-thumb-size`.

**Children indent past their parent's glyph, not past its row.** A disclosure
group's children step in once from the group's own glyph column, so a child's mark
lands under its parent's label. That is what every file tree does. Do not flatten
it to save width; take the width out of the step size instead.

**A caption with nothing under it is not a disclosure.** Rendering a disclosure
affordance for an empty group is wrong whether the chevron is visible or hidden,
and reserved-but-blank is the worst of the three because the empty box reads as a
missing glyph. Make the row inert instead: `aria-disabled`, no hover response,
label at `--opacity-disabled`. The dimmed label is what explains the blank slot.
Keep the slot reserved so the caption does not shift when the first child
arrives, and **keep the group's own add action at full strength and operable**,
because it is the only way out of the empty state.

**Scoped CSS does not cross a component boundary.** A row rendered by a child
component gets none of the parent's scoped rules, so a recursive tree component must
consume these tokens itself rather than keep a private copy. Two copies of a row
style drift, and the copy is where the rail bug survives.

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

### Scrims (badges over imagery)

A corner badge or chip that sits on top of a photo needs a translucent backing so
its glyph stays legible over unknown content. Three tokens cover it, and none is a
raw `rgba(0,0,0,…)`:

| Token | Use |
|---|---|
| `--scrim-surface` | Light warm chip over the **bright grid/sidebar canvas** (dark glyph). Matches the other grid badges. |
| `--scrim-photo` | Dark chip **directly over an arbitrary photo** (light glyph, `on-dark-surface`). On the theme `scrim` token so it stays reliably dark in both themes. |
| `--scrim-photo-strong` | The same chip deepened to 0.78, for a **coloured** glyph over an arbitrary photo (the stack badge's per-stack hue). |

The strong variant exists because contrast is not a property of the glyph alone.
`--scrim-photo` at 0.55 is sized for a near-white glyph, which reaches for white's
luminance and clears 4.76:1 over even a white photo. A hue cannot: normalised to the
badge tint, the darkest colour in the stack palette measures **1.62:1** on that chip
and is effectively invisible on a bright photo. Deepening the chip to 0.78 makes the
backing photo-independent and lifts that worst case to **3.98:1**, over the 3:1
non-text floor (WCAG 1.4.11). So: a coloured glyph over imagery buys its legibility
with a darker chip, never by darkening the colour until it stops reading as a colour.
Small text is not covered by this: no hue in the palette reaches 4.5:1 on any usable
chip opacity, which is why the stack badge's **count stays `on-dark-surface`** and
only its icon takes the hue (§13).

Pick by what the chip sits on, and match the sibling chips already on that surface
(a lock badge on a review card matches that card's tag chips; a lock badge on the
grid matches the grid badges). Full-screen backdrops still use `rgba(var(--v-theme-scrim), …)`
directly at their own tuned opacity; these tokens are for the corner-chip case.

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

Motion is feedback. Durations and easings live in `design-tokens.css`:

- `--dur-1` (150ms): hover, press, micro-feedback.
- `--dur-2` (200ms): panels, expand/collapse — the default.
- `--dur-3` (250ms): overlays, dialog enter/leave. **The routine ceiling.**
- `--dur-4` (420ms): the one exception — an expressive **one-shot** for a delight
  moment (a chip flying into the sidebar, a badge landing, a sticker drop). Never
  for a routine interaction; a bulk action that animates this slow feels sluggish.
- `--ease-standard` for most; `--ease-decelerate` for elements entering the screen;
  `--ease-accelerate` for elements leaving it; `--ease-spring` for a physical
  **landing with a slight overshoot** — the punctuation at the end of a flight, not
  its travel.

Nothing on a routine bulk action animates slower than `--dur-3`. **Respect
`prefers-reduced-motion`** — the token file already enforces it globally; do not
override it. And every expressive one-shot needs a reduced-motion fallback (a plain
fade, no travel) — the sticker land already models this (`rs-sticker-land` →
`rs-sticker-fade`).

### Named motion patterns (build the new ones on these)

These already exist hand-rolled; the tokens above consolidate them. Reuse the
pattern, don't re-roll the numbers.

- **Attention pulse** — a soft, looping "look here" on a live indicator. Reference:
  `tb-stats-pulse` (Toolbar activity dot), `tm-dot-pulse` (stats). ~1.4s
  `ease-in-out infinite`, scale + opacity. Looping cadence is contextual, so it
  lives with the pattern, not as a raw duration token.
- **Landing pulse (one-shot)** — a single glow-and-settle when something new
  arrives. Reference: `gridNewPulse` — a one-shot `--v-theme-accent` glow ring, no
  layout shift. This is the model for the sidebar badge's *pulse-on-landing*.
- **Flight / FLIP** — an element travels from A to B along an arc and lands.
  Reference: `rs-sticker-fly` / `rs-sticker-land`. Travel on `--ease-standard`
  (or `--ease-decelerate` into rest) over `--dur-4`; land on `--ease-spring` for
  the overshoot. This is the model for the async-import chip-into-sidebar flight.

---

## 11. Focus, hover, selected (the small stuff that is the class)

These get skipped and that is exactly why a UI looks cheap.

- **Focus:** every focusable element shows `--focus-ring`. Never remove an outline
  without replacing it. This is a keyboard user's only cursor. **The ring is a solid
  accent stroke, not a tinted one** — `0 0 0 3px rgb(var(--v-theme-accent))`. The
  previous `rgba(accent, .55)` measured 1.96:1 (light) and 3.01:1 (dark) against the
  canvas, i.e. it failed the 3:1 focus-indicator floor outright in the default theme.
  A focus ring is the one place in this system where "subtle" is a defect.
  The one exception to the stroke is a Vuetify field, whose floating label rides on the
  top border: there the stroke is a bar through the label, so the field takes
  `--focus-glow` (the same accent, 2px solid spread plus a 6px blur) painted beneath the
  outline and clipped off the top edge, so it runs down the sides and under the field
  and never behind the label. It steps aside while a select's menu is open, since the
  menu drops flush against that bottom edge. Same colour, same contrast at the edge, and
  keyboard-only via `:has(:focus-visible)`.
  **There is exactly one focus language.** The theme's legacy `focus` key (`#7c4dff`
  violet) is still consumed as `outline: 2px solid` by 10 review-surface components, so
  the app currently shows an amber ring in the grid and a violet outline in reviews.
  That is a consistency defect (the violet clears contrast at 3.15 – 4.81:1, which is
  why nobody caught it). The review surfaces migrate onto `--focus-ring` and the `focus`
  key retires after they do; `rgb(var(--v-theme-focus))` in new code is drift. Sites and
  measurements: `design-system-handoff.md` §9.4.
- **Hover:** `--hover-wash` (accent-tinted on light, on the chrome surfaces). Subtle.
  The alpha is **per theme and is not a free number**: it is chosen so the wash's
  contrast step against its own surface lands at ≈1.10 (light) and ≈1.26 (dark). If
  the accent's lightness ever changes, the dark alpha has to be re-solved to hold that
  step — see the action-fill knock-ons in §4.
- **Selected:** `--active-wash` fill plus `--active-bar` edge and `--active-text`.
  Tuned per theme in `style.css` because the same alpha reads differently on a
  near-white canvas than a dark one. **`--active-text` is a foreground on the *wash*,
  not on a solid fill**, so it is the surface's own text colour. The light theme had it
  as `on-primary` (white) over an 18% olive tint — **1.32:1**, invisible; it is
  `on-surface` (11.1 – 12.1:1), which is what the dark theme already did. This is the
  same "`on-<x>` on a tint" trap as `on-<status>` (§4); it will keep recurring, so check
  it every time an `on-*` token appears next to an `rgba(...)` fill.
- **Disabled:** drop to `--opacity-disabled` (0.38) of the token, never a
  different grey. The fade is legal here and only here: WCAG 1.4.3 exempts an
  inactive control, so a disabled label may sit below the contrast floor.
- **Secondary text:** a timestamp, a count, a hint, a reason — copy that is still
  meant to be *read* — fades to `--opacity-text-secondary` (0.7) and to nothing
  else. Never a raw number: the repo copied `0.6` into 37 places and it fails AA
  in **both** themes, because the canvas that decides it is the **sidebar**, not
  the grid — 4.01:1 light and 4.48:1 dark, against the 4.5:1 floor (#836). At 0.7
  the worst pair is 5.41:1. Either form is fine —
  `opacity: var(--opacity-text-secondary)` or
  `rgba(var(--v-theme-on-surface), var(--opacity-text-secondary))` — but measure
  on the sidebar, not on `surface`, or you will pass a test the user fails.
  This is a *legibility* floor, not a ranking device: rank is still weight,
  color and space, never opacity (§3). A glyph, chevron or icon button is not
  text — it is a non-text UI component with a 3:1 floor (§4, "Contrast") and may
  stay quieter. The ratios above are measured against the *live* themes in
  `frontend/src/main.js`, not against §4's superseded contrast tables, and
  `frontend/src/styles/design-tokens.test.js` recomputes them on every CI run —
  so the Camp B palette migration cannot invalidate this rule quietly.
- **Pending (busy):** a control that is working is disabled, but it is *not* the
  disabled state. "Not allowed" may fade out of the reading order; "working" is
  the only thing telling the user their click landed, so it has to stay legible.
  **A pending control does not dim.** Group opacity composites the whole control
  toward its surface and drags a filled variant's white label down with the fill:
  on the light `accent` fill the label measures 4.75:1 at full opacity, 3.97:1 at
  0.9, 2.70:1 at 0.7 and 1.67:1 at the disabled 0.38. The white-label rule (§4)
  only survives above ~0.97, so no perceptible dim is legal and there is no
  `--opacity-busy` token. Four things carry the state instead: the leading icon
  swaps to the shared spinner idiom (`mdi-loading` + `mdi-spin`, the one
  `NewReviewDialog`, `TagHealthBoard` and the overlay panels already use), the
  cursor becomes `progress` (busy, but the rest of the surface is still live,
  unlike disabled's `not-allowed`), the control is natively `disabled` so a
  second click cannot fire, and it carries `aria-busy="true"`.
  **The label does not change.** "Save" stays "Save" and never becomes
  "Saving...". The label is the accessible name and is often the
  `aria-keyshortcuts` target, so mutating it mid-flight renames the control under
  a screen reader, and a longer word resizes the button and reflows the action
  row. The spinner already says "working", so `AppButton` has no loading-text
  prop. (`TbTagPanel`'s hand-rolled "Applying..." predates the primitive: drift
  to converge, not precedent.) No live region on the button either, because the
  outcome is announced by the notice surface and a second announcer would
  double-speak, the same reason `OverlayActionReceipt` has none.
  **The spinner keeps spinning under `prefers-reduced-motion`.** The global reset
  in `design-tokens.css` would freeze it into a static broken ring that reads as a
  rendering fault, and a spinner is a status readout rather than decoration, which
  is the exception `ActionReceipt`'s countdown hairline already takes (§10).
  Restore it with a scoped override on `::before`, where `@mdi/font` puts the
  animation. Never by weakening the global reset.
- **Scrollbars:** a scroll region inside a `dark-surface` panel styles its own bar,
  because the global `.is-desktop` treatment in `style.css` keys off `on-surface`
  (the light-chrome pair) and does not apply in a plain browser at all. The pattern
  is `scrollbar-width: thin` plus
  `scrollbar-color: rgba(var(--v-theme-on-dark-surface), 0.4) transparent`, going to
  `0.55` on hover, with `scrollbar-gutter: stable` so content does not reflow when
  the bar appears. **0.4 is a floor, not a taste call:** it measures 3.28:1 against
  the overlay panel and clears WCAG 1.4.11's 3:1 for a UI component, where the
  0.1 - 0.2 alphas this system uses for *borders* land near 2:1. The bar is usually
  the only signal that a bounded list continues, so it stays visible rather than
  fading until hover, and the track stays transparent so it does not draw a second
  line next to an existing border. Reference: the four scroll regions of the image
  overlay sidebar (`.tag-list`, `.tag-drop-zone--predictions`, `.face-assign-grid`,
  and the description `textarea`).

---

## 12. Badges

A badge is a count pill or an attention dot pinned to a control (a folder's picture
count, the filter-count on the toolbar icon, the sidebar's task/upload indicator).
It is small, it overlays without shifting layout, and it recurs — so it is a pattern,
not a per-component invention. Today every badge is hand-rolled; these are the shared
rules and tokens they collapse onto.

**Two shapes, and the color split is a contrast decision, not taste:**

| Shape | When | Fill / text | Size |
|---|---|---|---|
| **Count pill** | a number matters (`3`, `12`, `99+`) | `primary` / `onPrimary` | `--badge-size` (16px) min, `--radius-pill` |
| **Attention dot** | just "something here / live / just landed", no number | `accent` (no text on it) | `--badge-size-dot` (8px), `--radius-pill` |

- **Why the split — and note the reason changed.** It used to be forced by contrast:
  `primary` + white was 4.84:1 and the amber `accent` + white was 3.94:1, so a numeral
  could not sit on amber. Since the action-fill tier landed (§4) **both pass** —
  `accent` + white is 4.75:1 light / 4.59:1 dark, `primary` + white 4.84 / 4.60 — and
  the arithmetic no longer decides it. **The split stays anyway, now on meaning:** a
  count is information and reads on the workhorse fill; the accent is the brand's
  attention colour and is spent on the dot and the glow, where there is nothing to read.
  Keeping one thing amber is what makes amber mean anything. This also matches the
  shipped convention: the toolbar filter-count badge is `primary`/`on-primary`, the
  activity dot is accent.
  *(Consequence: an amber count pill is no longer illegal, just off-pattern. Do not
  introduce one without changing this section.)*
- **Type:** count text is `--text-2xs` (11px, the ramp's reserved badge size) at
  `--weight-semibold`, `font-variant-numeric: tabular-nums` so counts don't jitter.
- **Overlay, don't reflow:** absolutely position it on its host (see
  `.bar-icon-badge-wrap` in `Toolbar.vue`); it must never resize or shift the control.
- **Landing:** when a badge appears or its count ticks up because work *just landed*,
  play the one-shot **landing pulse** (§10, `gridNewPulse` model, accent glow) — this
  is the sidebar task/upload badge's pulse-on-landing. Loop the **attention pulse**
  only while work is genuinely live; stop it when idle.

### The outline count pill (a count in a different unit)

A count that is **not pictures** must not wear the filled count pill, or it
reads as one more picture count sitting in a column of them. The outline
variant — no fill, a 1px border, tabular numerals, same pill radius and size —
is the answer when such a count must be shown, with a tooltip naming the unit.

*History:* introduced 2026-07-29 for the Duplicates sidebar count, and retired
from that spot the same day: the group count moves with the tier gate and the
threshold, so it read as churn, and the owner replaced it with the plain
**attention dot** ("there are duplicates to review" — the queue's own header
carries the numbers). The variant remains available for a stable non-picture
count.

### The key-hint badge (`kbd`)

Dialog action buttons wear their keys (owner decision, 2026-07-29; the behavioural
contract lives in `frontend_architecture.md`, "App* design-system layer"): the
accept/confirm button carries an **↵** badge and the cancel/abort button an **Esc**
badge, rendered by `AppButton`'s `key-hint` prop as a `<kbd>` chip — `--font-mono` at
`--text-2xs`, a 1px `currentColor` border at `--radius-sm`, the whole chip at 0.55
opacity. `currentColor` is what makes it legal on every variant fill without a new
token pair, and the opacity is what keeps it a hint rather than a second label. It is
`aria-hidden` (the accessible name must stay the verb); the machine-readable copy is
`aria-keyshortcuts` on the button itself.

The full-width bar that appears above the grid to act on a context — the bulk
**selection bar**, the image-overlay top bar, and the new **Trash restore/purge
bar** — is one pattern. It reuses the grid; only the bar changes.

- **Anatomy:** a left cluster (a count or title: "12 selected", "Trash") and a right
  cluster of actions, on a chrome surface (`toolbar` / `panel`), `--elevation-2`,
  full width, at height **`--bar-height`** (48px).
- **The shipped `SelectionBar.vue` is not this bar** — do not use it as the reference
  for the full-width pattern. It lives in `components/panels/` (not `widgets/`) and is
  a **floating centred pill**: `--radius-pill`, `rgba(surface, .86)` with
  `backdrop-filter: blur(12px)`, `--elevation-3`, `bottom: var(--space-5)` inside
  `.grid-content-area`, sized to its content rather than to the viewport. That is
  deliberate for a bulk-selection affordance over a photo grid, and it is also why it
  owns `--floating-bottom-h` for the notice stack (`notice-surface.md` §2.2). Whether
  it converges on the full-width pattern or stays a pill is the **One band** open item
  below; until that is decided, the pill is the reference for **floating** contextual
  actions and the overlay top bar is the reference for the full-width one.
- **Action weight:** the primary/affirmative action (Restore) is a `primary` button;
  a destructive action (Purge / Delete forever) is `error`, kept visually distinct,
  and **always behind a confirm** — deletion is irreversible and must never be a
  single mis-click. Secondary actions are quiet (text/`cancel-button`).
- **One band.** Action bars share `--bar-height` so they line up with the shell's
  top strip (§5). The current per-component heights (34 / 36 / 40 / 48 / 56px) are
  drift to migrate onto this token; because it moves pixels it is UI/UX-gated. The
  36px group is the exception that is already settled: the three **view toolbars**
  agree with each other at 36px and are guarded there, so they migrate together or
  not at all.
- **Empty & the Trash view.** Trash is the picture grid reused with the restore/purge
  bar. Its empty state uses the existing `EmptyTrash.png` art (§9) with a
  `--text-2xl` Tiny5 headline and a `--text-sm` line of guidance.

### The toolbar overflow (⋯)

When a 36px toolbar runs out of width, controls do not shrink, wrap, or
vanish — they **fold** into the ⋯ overflow (`TbOverflowMenu`, a
`bar-btn--icon` trigger wearing `mdi-dots-horizontal`, opening an in-place
`.tbm` panel at `--z-dropdown`). The rules of the pattern:

- **Fold = CSS both ways.** A foldable control exists twice with one `v-if`:
  as its bar button and as a `.tbm-action` row in the panel. The bar's
  container queries flip which of the pair is visible; no JS ever measures
  the bar. Each bar's ladder is a decision, not an accident — recorded in
  `toolbar-responsive-decisions.md`.
- **The trigger earns its place.** The ⋯ stays hidden until the first fold
  step; an overflow with nothing in it is chrome without a job.
- **Some controls never fold.** Undo is the recovery control and stays a
  single visible target at every width. A standing state (the scope pill)
  compresses to its icon rather than folding — a filter that hides is a
  filter the user forgets.
- **State travels with the row.** A folded toggle wears `aria-pressed` and
  the primary-token pressed colour; the stats toggle's attention dot moves to
  the ⋯ trigger while folded (same pulse recipe, `prefers-reduced-motion`
  honoured), so background work never goes invisible.

---

## 14. Layers (`--z-*`)

There was no stacking scale: 40+ distinct raw z-index values from `0` to `99999`,
two of them `!important`. "Put it above that thing" was luck, not a rule, and every
new floating layer was placed by guessing higher than whatever was found nearby.
Nine named steps replace the guessing. **A new floating layer picks a name, not a
number.**

| Token | Value | Stratum |
|---|---|---|
| `--z-base` | 0 | in-flow content; the grid itself |
| `--z-raised` | 10 | lifted over an immediate sibling: a tile badge, a hover scrim |
| `--z-sticky` | 100 | sticky headers/toolbars inside a scroll container |
| `--z-floating` | 200 | chrome anchored to the content area: selection pill, breadcrumb, range pill |
| `--z-dropdown` | 300 | menus, popovers, tooltips anchored to a control |
| `--z-drawer` | 1000 | full-panel overlays inside the shell: the lightbox |
| `--z-overlay` | 2000 | app-level overlays and context menus |
| `--z-modal` | 4000 | modal dialogs and their scrims |
| `--z-titlebar` | 4500 | the desktop title strip: drag region and window controls, never occludable |
| `--z-notice` | 5000 | the notice surface, above everything |

Steps are 10× apart so a component can always be wedged between two strata without
inventing a ninth. Vuetify's own teleported overlays land at ~2000 and sit outside
these stacking contexts; they are kept off the desktop title strip by anchoring to
`--titlebar-h`, not by z-index.

**The top two rungs are closed.** Exactly one component sits on each: `TitleBar.vue`
on `--z-titlebar`, `NoticeHost.vue` on `--z-notice`. A sticky toolbar is not a title
bar (`--z-sticky`); a floating status pill is not a notice (`--z-floating`). Nothing
else takes these two values, and that constraint is the point of the rung — without
it, 4500 becomes the new 99999 within two sprints.

The title bar needs a rung above `--z-modal` because in the desktop build it carries
the window drag region and the window controls: an overlay painting over it costs the
user the ability to move or close the window. `--z-modal` is not enough, because
`ReviewSessionsOverlay` is itself a 4000 stacking context and its full-viewport
sub-scrims would win that tie on DOM order. A notice then needs one rung above that.

**The ladder lives inside one stacking context.** `.app-viewport` is
`position: fixed; z-index: 0`, so every value here competes only with its siblings
inside the app shell, and a raw `9999` nested inside the lightbox or the review
overlay is trapped at its parent's rung — which is why four such values survive
harmlessly. Only two squatters were ever in `.app-viewport`'s own context
(`TitleBar.vue` at 100000 and `ImageImporter.vue`'s `.dlg-scrim` at 99999); both are
migrated. `.import-fly-chip` is appended to `<body>`, so it lives in the ROOT context
where any positive value already clears the shell.

Before this ladder, `--z-notice` tied the maximum and `NoticeHost` won only by being a
later sibling of `.app-viewport` — moving it earlier silently hid every notice behind
the chrome. Stacking now holds by value, proven by moving the host to first child and
re-checking.

**Migration is opportunistic, not a big-bang.** Touch a rule that carries a raw
z-index, move that rule onto the ladder. A wholesale rewrite is not worth it: each
move is pixel-visible on a different screen and has to be eyeballed there, and a
mistake in stacking order is invisible until the exact combination of overlays that
exposes it. From now on a raw z-index in new code is drift.

---

## 15. Using this system

1. Reach for a token before you type a value. Spacing, radius, type, elevation, motion
   are in `design-tokens.css`; color is `rgb(var(--v-theme-*))` from `main.js`.
2. If the value you want is not a token, you almost certainly want the nearest token.
   If you genuinely need a new one, that is a design decision — raise it with the lead
   designer, do not inline a one-off.
3. Anything that changes on-screen behaviour or a flow goes past the UI/UX expert.
4. Hand the frontend exact values, not adjectives. "`--space-5` padding, `--radius-md`,
   `--elevation-2`," not "a bit more room and rounder corners."

See `design-system-handoff.md` §9 for findings that are measured and decided but not
yet implemented.
