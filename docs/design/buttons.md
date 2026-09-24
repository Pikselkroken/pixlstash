# Buttons

The button system is **two dialects**. A raised control for dialogs, panels and
forms, and a flat one for the toolbar and the selection bar. Everything else
in the app that is pressable is a menu row, a choice control or an in-content
affordance, and reaching for a button dialect to build one of those is what
produced five parallel button families.

Owner: **lead designer** for the values, **UI/UX expert** for anything that
changes what a control does or moves pixels on a shipped bar.

Proposal and specimens (rendered, both themes):
<https://claude.ai/code/artifact/2dbc66a6-0d06-4712-9216-2af52bbe1556>.
Its specimens predate the hue decision and show an olive key action; where they
disagree with "Choice controls, and the hues" below, this file wins.
Design-system card: `guidelines/button-system.card.html` in the published
Claude Design project, with the primitives at `components/core/Button.*` and
`components/core/BarButton.*`.

---

## Status

**The approved rows are in the code, with the exceptions listed under "In the
code" below.** That second table is the one to check before reading a
screenshot as evidence for or against a row. The **Open** rows are not built.

| Decision | Status |
|---|---|
| Control height 28px, compact 24px | **Approved** 2026-09-12 |
| Control radius `--radius-sm` (4px), the tier rule | **Approved** 2026-09-12 |
| Chrome height 32px | **Approved** 2026-09-12 |
| Two dialects, and the reclassification of the rest | **Approved** 2026-09-12 |
| `AppButton` replaces `v-btn` in all dialogs | **Approved** 2026-09-12 |
| Outlined sites folding into the filled neutral | **Open.** Recommended. |
| An `on-dark` context on both dialects | **Open.** Recommended. |
| Selection-pill verbs 34px down to 32px | **Open, UI/UX gated.** See `design-system-handoff.md` §8. |
| One field height, `--control-h`, everywhere in the shell | **Approved** 2026-09-12 |
| The label inside the field retired outright | **Approved** 2026-09-12 |
| `--font-mono` on path, hash, port and id fields | **Approved** 2026-09-12 |
| Status hues as foregrounds: the `surface-*` family | **Approved** 2026-09-12 |
| Amber acts, olive selects | **Approved** 2026-09-12 |
| Selected items are olive too: tile, row, tab, focused group | **Approved** 2026-09-13 |
| Focus is ink: 2px `--text` ring with a 2px gap, on everything | **Approved** 2026-09-13 |
| Hover is an ink wash: `--text` at 16%, fills darken 20% | **Approved** 2026-09-13 |
| No olive text or icon on an olive wash or fill: selected rows take `--text` | **Approved** 2026-09-13 |
| Text contrast floor is **4:1**, not 4.5:1. Icons and marks keep 3:1 | **Approved** 2026-09-13 |
| Olive marks, words stay text: a current value, selected tab or active bar button keeps olive on its check, underline or icon, and its words take `--text` | **Approved** 2026-09-13 |
| Status words on a hovered row | **Closed**, no change: 4.09 to 4.45:1 clears the 4:1 floor |
| `--accent-on` becomes pure white; amber stays `#c47a1e` | **Approved** 2026-09-12; light amber superseded 2026-09-23 |
| Light theme amber deepens to `#9e6727` (attention dot under 3:1, #1413) | **Approved** 2026-09-23 |
| Amber marks on a hovered or selected row may dip under 3:1 (light 2.46 - 3.32:1), an accepted exception for a transient state | **Approved** 2026-09-23 |
| `primary_green` retired; its 16 sites become `primary` | **Approved** 2026-09-12 |
| Focus ring gets its own per-theme token | **Approved** 2026-09-12 |
| Pick one: `Segmented` for 2 to 5, `OptionRows` for sort by | **Approved** 2026-09-12 |
| Option rows take no fill: trailing check and an olive label | **Approved** 2026-09-12; superseded 2026-09-23 |
| Option rows take no fill: a leading radio on every row, olive when selected, words in `--text` | **Approved** 2026-09-23 |
| Segmented track: trough, inset ring, concentric corners | **Approved** 2026-09-12 |
| Popovers adopting the 28px control | **Open.** Recommended. |
| Menus: one surface, one 32px row, one hover wash | **Approved** 2026-09-12 |
| `.tbm` is a panel, not a menu, and keeps `--radius-lg` | **Approved** 2026-09-12 |
| Vuetify's `v-list` leaves, as `v-btn` and `v-text-field` do | **Approved** 2026-09-12 |
| All 44 dialogs take the `AppDialog` chrome | **Approved** 2026-09-12 |
| Dialog widths: four steps, `md` 520px the default | **Approved** 2026-09-12 |
| One 16px dialog gutter, body down from 24px | **Approved** 2026-09-12 |
| The dialog body spaces children with `gap`, not margins | **Approved** 2026-09-12 |
| One `Tooltip` surface; `HelpTip` becomes its preset | **Approved** 2026-09-13 |
| A `tooltip` prop sets the name and the tip from one string | **Approved** 2026-09-13 |
| Native `title` survives only for clipped-text reveals | **Approved** 2026-09-13 |
| The z-index ladder stops at `--z-drawer` | **Approved** 2026-09-13 |
| A raw value equal to a token is always a bug | **Approved** 2026-09-13 |
| No sub-pixel type; a progress track takes `--radius-pill` | **Approved** 2026-09-13 |

### In the code

Checked against `develop` on 2026-09-23. A row not listed here is not built
(the **Open** ones) or is covered by a line below.

| Area | State | Evidence |
|---|---|---|
| Control heights 28 / 24px, bar 32px, `--radius-sm` | Done | `--control-h`, `--control-h-sm`, `--control-h-bar` in `design-tokens.css`; `AppButton` and `AppBarButton` read them |
| `AppButton` replaces `v-btn`; `v-text-field` and `v-list` gone | Done | No `<v-btn>`, `<v-text-field>` or `<v-list>` left under `frontend/src` |
| One field height, label inside the field retired, `--font-mono` fields | Done | `AppInput`: `--control-h`, no floating label, a `mono` prop |
| Hues: `surface-*` foregrounds, amber acts / olive selects, `primary_green` retired, `--accent-on` white | Done | #1411; no `primary_green` site left; `on-accent` is `#ffffff` in both themes |
| Focus ring token, ink hover wash | Done | `--focus-stroke` / `--focus-ring-inset`; `--hover-wash` in 105 places |
| `Segmented` and `OptionRows` | Done | 11 and 6 users |
| Menus on one 32px row | Done | `styles/context-menu.css` rows at `--control-h-bar` |
| All 44 dialogs on `AppDialog`, four widths | Done | 44 `AppDialog` users, no bare `v-dialog`; `--dialog-w-sm` to `--dialog-w-xl` |
| One `Tooltip` surface, `HelpTip` its preset, the `tooltip` prop | Done | `HelpTip.vue` renders `Tooltip`; `AppBarButton` takes `tooltip` |
| No sub-pixel type | Done | No fractional `px` font size under `frontend/src` |
| The z-index ladder stops at `--z-drawer` | **Partial** | 18 files still set a raw `z-index`; `styles/designDrift.test.js` lists the known ones above the ladder |
| Native `title` only for clipped-text reveals; a raw value equal to a token; the 16px dialog gutter and `gap` spacing; the pill progress track | **Not audited** | Not checked site by site |
| Outlined sites folding into the filled neutral (Open) | Not built | 4 `variant="outlined"` sites remain |
| Selection-pill verbs 34 to 32px (Open) | Not built | Still 34px |
| An `on-dark` context on both dialects (Open) | Not built | No such prop on either button |
| Icons get no scale: a component owns its own icon slot | **Approved** 2026-09-13 |
| A free-standing icon tracks its text; the default is 16px | **Approved** 2026-09-13 |
| A menu row's two glyph slots are both `--gutter-glyph` | **Approved** 2026-09-13 |
| An icon-only control at 32px or taller takes a 24px glyph | **Approved** 2026-09-13 |
| Tooltip corner is `--radius-md` (8px), like cards and menus | **Approved** 2026-09-13 |
| Popovers hold controls, so they take `--radius-lg` (12px); menus `--radius-md` | **Approved** 2026-09-13 |
| Selection-pill verbs are `--control-h-bar` (32px) with 24px glyphs | **Approved** 2026-09-13 |
| `ToggleButton` retired: pick-one is `OptionRows` (long lists) or `Segmented` (2–5) | **Approved** 2026-09-13 |
| Badges: amber and olive variants removed, a `teal` variant added; counts are neutral (`--cancel-bg`) | **Approved** 2026-09-13 |
| Chips take the control corner, `--radius-sm` | **Approved** 2026-09-13 |
| Checked checkbox, on switch and slider stay deep olive with a white mark in both themes (fill against a dark ground 2.32–2.72:1, accepted) | **Approved** 2026-09-13 |
| Retired tokens (`--focus`, `--ps-purple`, `--ps-accent-bright`, `--ps-orange`, `--sp-*`, `--radius-xs/base/full`, `--shadow-*`, `--font-sans/display/system` and the typography aliases) migrated and deleted | **Approved** 2026-09-13 |
| Design System keeps folder browser, inspector styles and tag chips; stats sidebar and the tweaks scaffold deleted; toolbar menus and the inspector-uses page move to App Designs | **Approved** 2026-09-13 |

---

## Why two, and why the count was seven

Three properties were carrying no information.

1. **Radius said nothing about what a thing is.** A button, a text field, a
   card, an image tile and a dropdown all sat at 8px. Nothing in the corner
   told you whether you could press it or whether it was the surface the
   pressable thing sat on.
2. **`AppButton` had been rewritten five times.** Not varied, rewritten: five
   class families, 59 sites, each re-declaring the same fill-plus-variant-plus-
   hover-brightness-plus-disabled-fade grammar. The fingerprint is the height,
   hand-typed as 27px in seven files because there was no `--control-h`.
3. **Two colours claimed to be the primary action.** Amber filled 24
   `AppButton`s while also being the focus ring, the hover wash, the
   light-theme selection wash and the attention dot.

**The direction: radius states tier, height states density, colour states
consequence.**

That reframes "slightly taller and less round" as a **control tier** change
rather than a button change. Buttons, inputs, selects and steppers sit side by
side in every settings row and dialog footer, so they move together or they
stop matching. A 4px button beside an 8px input looks like a mistake in a way
that neither does alone.

---

## Tokens

Three new dimension tokens. No new radius, no new colour, no fifth value in a
set of four.

| Token | Value | Replaces |
|---|---|---|
| `--control-h` | 28px | An untokenised 27px in seven files |
| `--control-h-sm` | 24px | An untokenised 23px, below the pointer-target floor |
| `--control-h-bar` | 32px | The de-facto `.bar-btn` height, and 34px in the selection pill |

`--radius-sm` and `--radius-md` do not change value. They change meaning:

- `--radius-sm` (4px) is the **control** tier: buttons, inputs, selects,
  steppers, chips, `kbd`, segments.
- `--radius-md` (8px) is the **surface** tier: cards, menus, image tiles.
- `--radius-lg` (12px) stays dialogs, panels, popovers.

### Why 28, not 30

- It is on the 4px grid. 27 and 23 never were, and 30 and 26 are not either.
- **It lands exactly on `v-btn size="small"`,** which is 28px. That is the
  largest single cohort being absorbed, 28 of the 65 `v-btn` sites, and it
  migrates without moving a pixel.
- The 19px label box gets 4px of air above and below, one spacing step.
- 30px is the choice if the height should carry more of the change. It costs
  the grid and re-opens every band tuned against a 27px button, including the
  duplicate queue's 36px strip, whose own CSS comment says the 27px button and
  the 36px band never grow.

### Why 24 for the compact size is not a preference

WCAG 2.2 puts the minimum pointer target at 24 by 24 CSS pixels. The compact
`AppButton` is 23px, and icon-only it is 23 by 23, so today it clears that
rule only through the spacing exception. 24px satisfies it outright.

### Why 4, not 6

- Four radii exist because fourteen were collapsed into four. A
  `--radius-btn: 6px` would be a fifth.
- **About half the app's buttons are already at 4px,** and three of the five
  hand-rolled families chose it independently. Every `v-btn` is at 4px because
  that is Vuetify's default; so are the review overlay's family, both copies
  of the dialog-footer button, the filled one-offs, the toolbar buttons, the
  segmented controls and the settings chips. Roughly 129 at 4px against 152 at
  8px, and the 8px group is `AppButton` plus the two families that copied it.
- If 4px reads too hard in situ, the lever is 6px as a deliberate fifth token,
  not a compromise applied to some controls and not others.

---

## Dialect one: the raised control (`AppButton`)

Dialogs, panels, forms, settings, popover footers. It has a fill, it sits on a
surface, and it is the only thing in the app that looks like a button in the
ordinary sense.

| Property | md | sm |
|---|---|---|
| Height | `--control-h` 28px | `--control-h-sm` 24px |
| Side padding | `--space-5` 16px | `--space-4` 12px |
| Icon gap | `--space-2` 4px | `--space-2` 4px |
| Label | `--text-base` 14px / `--weight-medium` | `--text-sm` 13px / `--weight-medium` |
| Radius | `--radius-sm` | `--radius-sm` |
| Icon | 18px | 16px |

Never all-caps, and no letter-spacing. Uppercase tracked labels are the
Material dialect this system replaces.

### Roles, named by job rather than by hue

| Role | Today's prop | Fill | Label | For |
|---|---|---|---|---|
| Key action | `primary` | `accent` | `accent-on` (pure white) | The one thing the surface exists to do. One per surface. |
| Neutral | `secondary` | `cancel-button` | `cancel-button-text` | Cancel, Close, Copy, and everything currently outlined or tonal. |
| Destructive | `danger` | `error` | `on-error` | Deletes bytes. Never a dialog's default focus. |
| Quiet | `ghost` | transparent | `on-surface` at 0.7 | Tertiary actions, icon-only affordances, help tips. |

The prop names can stay as they are. What matters is that one hue means "key
action" and that the names stop naming colours. `primary_green` retires: it was
a paint swatch that leaked into the API, and an olive button now reads as a
chosen option. Its sites take `primary`, the amber.

### States, which are already right and do not change

`AppButton`'s state handling is the reason to build on it rather than replace
it. Disabled fades to `--opacity-disabled` (0.38) with `cursor: not-allowed`,
and prefers `aria-disabled` wherever the control is blocked for a reason so it
keeps its place in the tab order and its `aria-describedby` reason stays
reachable. **Pending is not disabled:** the leading icon swaps for the
spinner, the label stays legible at full opacity, the cursor is `progress`,
`aria-busy` is set, and focus is handed back when the request settles. There is
no loading-text prop, because the label is the accessible name. The focus ring
is ink: 2px `--focus-stroke` (`--text`) with a 2px gap, drawn as `outline` plus
`outline-offset`. Full rationale in `visual-language.md` §11.

### The three things it needs first

These are the three reasons it was rewritten, so they come before any
migration.

| Addition | Unblocks | Sites |
|---|---|---|
| `block` | Full width, for a panel-width primary. The popover dialect grew `--full` and `--lg` for exactly this. | 5 |
| `on-dark` context | A button on a surface that stays dark in both themes: the lightbox and the review overlay. Nine `rs-` classes exist because there is no answer here. | ~40 |
| `--control-h` | A height to reach for, so the next dense control does not pick its own. | 7 |

No `icon-right` is needed: there is no `append-icon` in the codebase.

---

## Dialect two: the flat bar control (`AppBarButton`)

Toolbar, selection pill, undo group, overflow trigger, title bar, stats rail,
lightbox chrome. Transparent until hovered, icon-first, sized to the band it
sits in rather than to its text.

| Property | Value | Note |
|---|---|---|
| Height | `--control-h-bar` 32px | Already `.bar-btn`. Sits in the 36px toolbar band with 2px either side. |
| Icon-only width | 32px | Square. The overflow trigger and undo buttons already are. |
| Side padding | `--space-3` 8px | |
| Label | `--text-base` 14px / `--weight-regular` | Regular, not medium. Chrome does not compete with content. |
| Colour | `toolbar-text` | Not `on-surface`, so the toolbar and sidebar read as one strip. |
| Shape, boxed | `--radius-sm` | Default, for a strip. |
| Shape, round | `--radius-pill` | For the selection pill, where a square verb fights its container. |
| Hover | `--hover-wash`, `--text` at 16% | Wash only, and a muted label or icon goes to `--text`. A border per verb would make seven of them a fence. |
| Open | wash plus a 1px `border` | A transparent 1px border is reserved at rest so the box never jumps. |
| Active | `--selected-ink` label and icon | Olive, lifted in dark. **Not** `primary`. See the defect below. |

### It is a separate component, not a variant

It models different anatomy, not different paint: a count badge overlaid on
its icon, a dim prefix before its label ("Sort:"), an ellipsizing label with a
max width, and split pairs with joined corners. A variant flag on `AppButton`
would have to carry all four. Both dialects share the disabled, pending and
focus contract, deliberately, so those cannot drift apart.

---

## What the two dialects absorb

The population is 496 hand-written `<button>` elements plus 117 `AppButton`
and 65 `v-btn` components, classified by what they are rather than what they
are made of.

| What it is | Sites | Distinct classes | Answer |
|---|---|---|---|
| Standalone action | 84 + 117 + 65 | ~30 | **`AppButton`**, 266 sites |
| Chrome | 116 | ~40 | **`AppBarButton`**, mostly already 32px |
| Close and dismiss | 16 | 16 | **`AppBarButton`** icon. Sixteen closes, sixteen classes, no two alike. |
| Menu row | 162 | ~9 families | Stays. **Not a button.** Adopts the tokens, keeps its density. |
| Choice segment | 37 | ~20 | Stays. **Not a button.** Segments take `--control-h`; consolidating the four is its own decision. |
| In-content affordance | 80 | ~55 | Stays, correctly bespoke. Structurally part of a tile or a row. |

398 sites onto two components. The remaining 279 are not a backlog, they are
three families that were being counted as buttons. Naming them is most of the
simplification, because it stops the next person reaching for a button dialect
to build a menu.

### The five parallel `AppButton`s, and what each was missing

| Family | Sites | Height | Radius | Why it exists |
|---|---|---|---|---|
| `.tbm-action` + `--primary/--secondary/--outline/--lg/--full` | 16 | 36 / 42 | 8px | The closest thing to a second `AppButton`, with its own variant *and* size system. Needed full width and a taller box in a panel. |
| The `.rs-*` review family, nine+ classes | ~28 | 26 to 36 | 4px | Buttons on a surface that stays dark in both themes. One shape copy-pasted at different heights, because there was no on-dark context. |
| `.gbtn` / `.gcompare` / `.dq-btn` / `.qdecided` | ~19 | 27 | 8px | Near-exact copies of `.app-btn--md`, border and radius and type included. `.dq-btn--accent` is `.app-btn--primary` re-typed. |
| `.btn` family + `.dlg-btn` family | 9 | padding-set | 4px | Two *independent* copies of the same dialog-footer button, in three dialogs that predate the primitive. |
| Filled one-offs: `.restore-btn`, `.login-button`, `.assign-btn`, `.progress-overlay__abort` and friends | ~6 | various | 4px | Each re-declares a fill, its `on-` pair and a radius. No shared parent at all. |

Three of the five chose 4px without being asked to.

---

## Measured defects this work surfaced

Both are pre-existing and independent of the migration. Recorded with their
numbers so the implementation lane does not have to re-measure.

### The active bar label fails in the default theme

`.bar-btn--active` paints its label `primary`, the deep olive, which measures
**2.72:1** on the dark toolbar (`#567309` on `#23282f`). That fails both the
4.5:1 floor for normal-size text and the 3:1 floor for a UI component. It
passes in light (4.67:1 on `#f0ede9`), which is why it went unnoticed.

The fix needs no new value. The repo already ships `dark-surface-primary`
(`#8EA604`), whose documented job is exactly this, olive as a foreground on a
dark surface rather than as a fill, and it measures **5.37:1** on dark chrome.
So the active label is per theme: the lifted olive in dark, the deep olive in
light. This is the same shape as the rest of the `dark-surface-*` family.

### All four status hues fail as foregrounds, and the fix is settled

`error` as a destructive menu label measures **2.20:1** on a hovered dark row
and **4.63:1** on a hovered light one, against a 4.5:1 floor for 13px text (the floor is 4:1 since 2026-09-13).
The trash glyph at 75% opacity measures 1.77:1 in dark against a 3:1 floor. In
light it technically passes while its sibling rows sit at 16:1, so the one row
you must not misclick is the faintest in the menu.

Measuring it showed the whole family has the defect. On a hovered menu row:

| Hue | Dark | Light |
|---|---|---|
| `error` | 2.20:1 | 4.63:1 |
| `warning` | 4.84:1 | 2.10:1 |
| `success` | 2.33:1 | 4.37:1 |
| `info` | 2.22:1 | 4.59:1 |

Six of the eight combinations fail. The cause is the same each time: a status
hue is tuned to be a **fill** that carries the warm near-white label at
4.5:1, so it has no budget left as a **foreground**.

**Approved 2026-09-12: a per-theme `surface-*` family.** One value cannot
serve both themes, because 4.5:1 on the hovered dark row needs luminance at or
above 0.346 and on the hovered light row at or below 0.149. These are the
existing `dark-surface-*` hues lifted (dark) or deepened (light) one rung, at
the same hue angle, except `warning`, which moves from 32 to 38 degrees so a
warning can never be misread as the destructive red two rows above it.

| Token | Dark | Light |
|---|---|---|
| `surface-error` | `#eda79c` | `#9a3327` |
| `surface-warning` | `#e2b05a` | `#755215` |
| `surface-success` | `#7ec892` | `#226534` |
| `surface-info` | `#9bbbd4` | `#2b5c82` |

Worst case across all three row backgrounds, resting, ink-hover and the
shelf's amber hover: 5.94 to 6.24:1 for the label, 3.49 to 4.13:1 for a glyph.
Green takes 40% saturation rather than the family's 65%, because green carries
more luminance per unit of saturation and goes neon against a warm palette.

**Three declarations carry the destructive red:** the shared context-menu
sheet, which covers the image-grid menu, the selection menu and the toolbar
dropdown; the sidebar context menu's own copy; and the shelf menu's. The
lightbox menu already reaches for `dark-surface-error` and is correct.

**Use the family only where the colour has to be on the words.** For a hint or
a status line beside a glyph, the notice surface already shows the better
pattern: the rail and the glyph carry the hue and the sentence stays
`on-surface` at 12:1. That needs no token and cannot drift.

`AppInput` is also affected: its error border measures **2.42:1** on the dark
field against the 3:1 non-text floor, and its error message 2.20:1.
`surface-error` takes those to 6.63:1 and 6.03:1.

### The design system's focus ring was the superseded one

The published Claude Design project still carried
`--focus-ring: 0 0 0 3px rgba(var(--accent-rgb), 0.55)`, the tinted ring this
repo replaced because it measured 1.96:1 light and 3.01:1 dark against the 3:1
focus-indicator floor. Corrected there to the solid value, since the repo is
the law on token values. Its `Button` primitive had also drifted to 32px at
`--radius-base` with `opacity: 0.35`, `pointer-events: none` and
`brightness(1.15)`, none of which match what ships.

---

## What the migration forces

Recorded here so none of it reads as a regression when it lands.

### Fields: one height, one label convention (approved 2026-09-12)

The button decision forced the field decision, because they share the tier.
The app asked for text at four heights: `AppInput` 27px, `.tbm-input` 36px,
`v-text-field` outlined compact 40px and filled comfortable 48px, plus about
twenty fields computing their own height from padding, which all land between
25 and 28px.

**Height was not serving the content, the label was setting it.** There is one
label convention, an uppercase caption above the field, used about sixty times
across the App* primitives and every popover section. The 48px tier existed to
accommodate a second one, Material's floating label, which lives *inside* the
box and therefore needs room for a label and a value stacked. That was used on
twelve sites, all `v-text-field` inside raw dialogs.

Three decisions:

1. **One height for every field inside the app shell:** `--control-h`. Login
   and first-run stay a deliberate exception at about 40px with 16px text,
   because that is a full-page moment rather than dense chrome.
2. **The label inside the field is retired outright.** No label goes inside a
   box anywhere in the app, and `v-text-field` is not to be reached for in
   order to get one.
3. **The font carries the kind of value, the height never does.** A field
   holding a filesystem path, a hash, a port or an id takes `--font-mono` at
   `--text-sm`; a name or a phrase does not. The app already renders read-only
   paths in mono in eight places, so this is the editable half of a convention
   it already has. A path is hard to read because proportional type spaces
   glyphs unevenly and confuses `l` with `1`, not because the box is short, and
   mono is narrower so more of a long path fits before it scrolls. The rule is
   checkable by looking at the value, which is why it replaced "is this field
   important enough to be tall".

**A field and the button beside it are the same height, by construction.** The
app ships Vuetify's `ress` reset, which puts `box-sizing: border-box` on
everything, so a control's declared height is its rendered height whatever
border it carries. Both take `--control-h`. Today that pair is already correct
at 27 against 27; it is the 48px and 40px fields that break it, by 21 and 13
pixels against the button they sit beside.

Specimens: <https://claude.ai/code/artifact/9908c288-629f-49e8-b0f9-10e8f810ef1a>.

### Choice controls, and the hues (approved 2026-09-12)

**Amber acts, olive selects.** Amber is the action button where a surface has
one. Olive is everything that is merely chosen: a selected segment, a selected
option row, the active bar button. `primary_green` retires, because an olive
button now reads as a chosen option rather than an action, and its 16 sites
become `primary`.

**Selected items are olive too** (2026-09-13). A selected tile, the active
sidebar row, a focused group in a review, a selected tab or table row take
`--active-bar` (ring or edge) and `--active-wash`, and both tokens are now olive
per theme: the lifted `#8EA604` with a 0.20 wash in dark, the deep `#567309`
with a 0.16 wash in light. One hue for "selected", whether the thing chosen is
an item or a value.

What amber keeps: the key action fill and the attention dot. Nothing that marks a selection is amber, and
neither does focus.

**The amber does not move (in the dark theme; light superseded 2026-09-23, below). Its label goes to pure white, and the 3.41:1 that
leaves is an accepted exception.** Settled after all five candidates were
rendered at button size.

`--accent` keeps `#c47a1e` (light superseded 2026-09-23, see below). `--accent-on` becomes `#ffffff` rather than the
warm near-white, which is worth 0.37 for nothing and which the handoff's §9.1
had already queued. 3.41:1 clears the 3:1 floor WCAG applies to a UI component
and misses the 4.5:1 it applies to normal-size text. The shortfall is accepted
on the reasoning that the 4.5 rule exists for paragraphs, and this is one short
word, centred, on a 28px target the user is deliberately aiming at.

**Do not "fix" it.** Both alternatives were built, shown and rejected:
darkening the amber by the ~15% needed for 4.53:1 reads as brown, and flipping
the label to the warm near-black reaches 4.71:1 but puts dark text on the
product's primary button. It was changed twice and reverted twice. If it is
ever reopened, the question is not which amber but whether amber should be the
action fill at all, given it is also the attention colour.

The "never pure white" rule this looks like it breaks is about body copy on a
canvas, not a label on a deep fill. `--primary-on`, `--secondary-on` and the
rest of the tier are deliberately left on the warm near-white: olive carries
its label at 4.86:1 and needs no help.

Specimens: <https://claude.ai/code/artifact/cd5d72f8-6635-4916-b40b-8ec9bb577cf0>.

**Superseded for the light theme 2026-09-23 (#1413).** Amber is also the
attention dot and the busy stats icon, and #c47a1e measured 2.93:1 on the light
sidebar and toolbar, under the 3:1 floor for a mark. Light `--accent` is now
`#9e6727` everywhere, the key action fill included (white label 4.75:1, 4.07:1
on the sidebar). One amber per theme, not a separate dot colour. Dark keeps
`#c47a1e` and the 3.41:1 exception above. Two consequences were accepted
with it: an amber mark on a hovered or selected light row measures 2.46 - 3.32:1
(the pointer is already on the row, and every case beats develop), and the
hover shade, glows and washes read browner in light than they did.

**The focus ring needs its own per-theme token.** A stroke must contrast with
the canvas, so it wants to be light on dark and dark on light; a fill must
carry a light label, so it wants to be dark always. No single amber does both:
the best any of them manages across both themes is 2.62:1 against the 3:1
focus floor, which is today's value, so the ring has been failing all along.
`--focus-stroke` is the bright amber in dark (4.72:1 worst) and the deep amber
in light (3.93:1 worst).

**Superseded 2026-09-13: focus is ink.** Once amber became the action fill and
olive the selection, an amber ring measured 1.27:1 against the Save button it
surrounds, read as a second mark on an olive selection, and the light deep
amber measured 2.92:1 on `--panel`. `--focus-stroke` is now `--text` in both
themes: 2px, with a 2px gap (`--focus-width`, `--focus-offset`) so the ring
only ever meets the ground, never a fill. Worst case 10.24:1 dark and 13.74:1
light, both on `--panel`. A 32px menu row keeps the inset form. Options
compared: <https://claude.ai/code/artifact/2b675a9b-b3a7-4421-a13c-e0ee6218b98c>.

#### Pick one: two shapes, and the boundary is arity

Eleven of the sixteen pick-one sites have exactly two options and three more
have two or three. Only sort has many, and the grid's list comes from the
server so its length is not knowable from the source.

- **`Segmented`** for two to five short options, which is fifteen of the
  sixteen. Four icon variants: label only, icon plus label, icon only at
  32 x 24px, and **stacked**, a 40px media box above the label at 76px in an
  84px track, for a picker whose icon draws the shape of the outcome. It
  absorbs both sets of bespoke option cards, `.folder-type-option`,
  `.rs-dialog-order-btn`, `.remix-seg-btn`, `.dc-zv`, `.shelf-viewseg`, the
  `v-btn-toggle`, and the shelf's folder-layout and group-by rows.
- **`OptionRows`** for sort by, and only sort by.

**The track is a trough, not an outlined box.** Filled with `--input-bg` and
outlined with `--border` it measured **1.04:1** and **1.15:1** against the
`--panel` it sits on, so it had no edge. It takes `--track-trough`, which
darkens in both themes, plus a 1px **inset** `--track-ring`. Inset, because a
real border would make the track 34px and nothing would line up.

**Concentric corners, and this rule is reusable.** An inner radius must equal
the outer radius minus the inset, or the corners do not nest and the inner
curve reads as a different radius even when the number is the same. A
`--radius-md` track with `--space-2` padding and a `--radius-sm` segment: 8
minus 4 is 4 exactly. That constraint is why the segment is `--control-h-sm`
and not `--control-h`: 4 + 24 + 4 is 32, which keeps the track on
`--control-h-bar`.

#### The option row takes no fill

Every row leads with a radio; the selected one is marked in olive and its
label takes medium weight in `--text`. A trailing check sat where the next
column starts in a two-column list, so it read as belonging to the wrong
option; a leading radio is read with its own label. A filled row with an icon and a label, at the same height as a
filled action button, is the same object at a different width, which is why the
old one read as pressed. Nothing else in the app fills a row. **Rule: a fill
means press me, so if it is not an action it does not get one.**

The olive is `--selected-ink`, per theme: the deep olive measures 2.32:1 on a
dark panel and the lifted one 2.36:1 on a light one. Weight carries a second
cue so the answer survives without colour. ARIA is `radiogroup` and `radio`
with `aria-checked`, not the `aria-pressed` the old toggle grid used, which
announces "pressed" for what is a choice.

#### What stays out of this

**Tabs are navigation, not choice.** A tab changes which panel is in front of
you rather than setting a value. Five strips exist and they want one
implementation with `role="tab"`, which is its own job.

**Stack threshold is stranded, not in scope.** Its five options are an ordered
scale, but the control is unreachable: the toolbar filters `LIKENESS_GROUPS`
out of the only sort list left in the UI, and the sidebar's picker is gone from
its template. The saved config restores the sort with no validation, so anyone
who already had it keeps it and nobody new can reach it. That needs a product
decision, not a control decision.

### Menus: one surface and one row (approved 2026-09-12)

Proposal and specimens (rendered, both themes):
<https://claude.ai/code/artifact/7742c961-233a-403e-8623-f0da401affbc>.
Primitive: `components/core/Menu.*` in the published Claude Design project,
shown in the Core Components card.

The audit line said four menu surfaces. Counted by parsing the templates for
real element tags, it is **ten menu containers and twenty row families across
155 row tags**, plus one surface that is not a menu at all.

**`.tbm` is a panel.** It has a header with a title and a count, bordered
sections with uppercase field labels, controls inside those sections, and a
footer stating what Enter and Escape do. Nine part classes. That is a
mini-dialog anchored to a button. The radius scale already says
`--radius-lg` is for "dialogs, panels, popovers" and `--radius-md` for "cards,
inputs, menus", so the 12-against-8 difference flagged as drift is the tier
working correctly. It is out of scope and does not change. A panel holds
controls; a menu holds rows; do not merge them, and do not put a control in a
menu.

The other ten collapse to one surface and the twenty row families to one row.

| Part | Value | What it replaces |
|---|---|---|
| Fill | `surface` | 8 of 10 already were; the others a 96% translucency and a `color-mix` of the shadow colour |
| Radius | `--radius-md` 8px | 5 already were; the rest 6px, 10px and Vuetify's 4px, none on the scale |
| Border | 1px `rgba(on-surface, .14)` | Two containers had none, which is why they floated unanchored |
| Shadow | `--elevation-3` | One had none, one was on 2, one on 4 |
| Block padding | `--space-2` 4px | Mixed |
| Row height | `--control-h-bar` 32px | Only 3 of 20 declared one; the other 17 computed 25 to 34px out of padding |
| Row padding | `0 --space-4` 12px | A literal 14px, a literal 10px, and four `--space-*` mixes |
| Row label | `--text-sm` 13px | A literal 13px, `--text-sm`, `--text-xs`, `--text-2xs` |
| Icon, gap | 16px at 0.75, `--space-3` | Unchanged from the commonest |
| Hover | `--hover-wash` | Four washes (see below) |
| Destructive row | `--surface-error` | `error`, which measures 2.20:1 as text on a hovered row |
| Current value | `--selected-ink` + trailing check | Six different active treatments |
| On a dark ground | the `dark-surface` family | A bespoke 10px menu in the lightbox |

The design-system primitive draws the border as `--border`, because the
published tokens carry no `on-surface` channel. The two are close but not
identical on dark: `rgba(on-surface, .14)` composites to `#404247` where
`--border` is `#363d45`. The repo value is the one to ship; the token gap is
why the card cannot show it exactly.

#### Why 32 and not the 28px control tier

`--control-h` is the dense-control tier: the things that sit side by side in a
settings row. A menu row is not one of those. It is chrome you point at, it
never sits beside an input, and 32px is both the median of the spread it
replaces and the height of the bar button the menu opens from. It also clears
the WCAG 2.5.8 24px pointer floor with room, which four of the old row
families did not.

#### Four hover washes, and one of them is invisible

Measured as luminance steps against each row's own resting ground. `style.css`
records the app's own tuning floor as roughly 1.10 in light and 1.26 in dark.

| Wash | Dark | Light | Families |
|---|---|---|---|
| `rgba(on-surface, .08)` | 1.243 | 1.171 | 4 |
| `--hover-wash`, `rgba(accent, .14)` | 1.194 | 1.165 | 7 |
| `rgba(accent, .08)` | **1.110** | **1.092** | 1 |
| `rgba(on-dark-surface, .08)` | 1.217 | n/a | 2 |

The third is `.sidebar-project-menu-item`, and it is the one menu in the app
whose hover you have to hunt for. `--hover-wash` wins on being the app's hover
token everywhere else and on carrying the hue; the ink wash is a slightly
larger step but says nothing.

The design system's `tokens/colors.css` had `--hover-wash` at 0.10, which is
the invisible one. The repo is the law on token values and ships 0.14.
Corrected 2026-09-12.

**Superseded 2026-09-13: hover is an ink wash at 0.16.** In use, 0.14 amber
was still reported as practically invisible, and on an olive selection it
turned brown. `--hover-wash` is now the text colour at 16% per theme: 1.57 on
dark chrome, 1.56 on dark panel, 1.37 in light, 1.53 / 1.35 layered over a
selection. It carries no hue, because amber is the action and olive the
selection. Filled controls darken 20% (`--hover-shade`) instead of
`brightness(1.15)`, which raises the white label on amber from 3.41 to 5.03:1;
a neutral fill takes `--hover-neutral`, lighter in dark and darker in light.
Options compared, with rest and hover side by side:
<https://claude.ai/code/artifact/1a6b338f-7dba-469b-9c5c-03ad7f56741e>.

**Open: coloured text on a hovered row.** A stronger wash is a lighter ground
in dark and a darker one in light, so coloured words lose contrast while the
row is hovered. Measured on the 0.16 wash: `surface-*` status words 4.03 to
4.09:1 on a hovered dark panel row and 4.41 to 4.45:1 in light, and
`--selected-ink` 2.94:1 on a hovered dark panel row and 3.41:1 in light, all
against 4.5:1.

**Approved 2026-09-13: no olive text on olive.** Olive text on the olive
selection wash measured 3.37 to 3.88:1 at rest and 2.26 to 2.82:1 hovered, so
it failed before hover made it worse. A selected item with an olive wash or
fill behind it takes `--text` for its label, icon and count (7.5 to 11.2:1 at
rest, 5.05 to 8.3:1 hovered); the olive lives in the wash and the edge bar.
A solid `--primary` fill keeps its `--primary-on` label. 

**Approved 2026-09-13: 4:1 is enough for text.** The floor this system holds
text to is 4:1, not WCAG's 4.5:1; icons, checks and underlines keep 3:1. Do not
propose tokens or treatments whose only purpose is to lift a 4.0 to 4.5:1
measurement to 4.5. Measurements elsewhere in this file that are called failing
against 4.5:1 but sit at or above 4:1 are passes under this rule. As a result,
status words on a hovered row (4.09 to 4.45:1) need no change.

**Approved 2026-09-13: olive marks, words stay text.** On a plain row, olive
words fall to 2.95 to 3.95:1 when hovered, under even the 4:1 floor. A current
value in a menu or option list, a selected tab and an active bar button keep the
olive on their mark (check, radio, underline, icon) and set their words in
`--text`. Every active bar button in the app today is icon-only (filters,
search, stats toggle, the shelf's show filter), so the toolbar does not change.
The one mark under 3:1 is the option-row radio on a hovered dark panel, at
2.95:1, accepted as the radio plus the medium weight still read as selected. The status-word figures under the `surface-*` family, earlier in
this file, were taken with the 0.14 amber wash.

#### The `on-<x>`-on-a-tint trap, fourth occurrence

`.sidebar-project-menu-item.active` paints `rgba(tertiary, .30)` and puts
`on-tertiary` on it. An `on-` token is only ever correct on a **solid,
full-opacity** fill; on a 30% tint it is measuring against the wrong thing.
The result is **1.35:1** in light. It passes in dark at 9.89:1, which is why it
survived. The surface's own ink on that same tint gives 8.97:1 dark and
10.60:1 light. Already recorded in `design-system-handoff.md` §9.2 and still
shipping; folding these menus onto one row fixes it as a side effect.

#### Vuetify's list is the one that cannot be reconciled

48px rows against the app's 25 to 34, Material padding, Material hover, a 4px
radius, and no prop that brings it to this vocabulary. Two sites use it, the
training-runs menu and the folder-mapping tree. It goes the way `v-btn` and
`v-text-field` go.

#### An open note on the elevation token

`--elevation-2`'s own comment claims "menus, dropdowns", but six of the nine
shipped menus use `--elevation-3` and that is what was approved here. The
comment is the thing that is wrong. Fix it when the elevation scale is next
touched; nothing depends on it.

### Dialogs: one chrome, four widths (approved 2026-09-12)

Proposal and specimens (rendered, both themes):
<https://claude.ai/code/artifact/7a73a444-70bf-457d-9067-15599d42c57f>.
Primitive: `components/core/Dialog.*` and the `guidelines/dialog-chrome` card
in the published Claude Design project.

The audit line said two chromes at 22 each. It is **three chromes across 44
dialogs**, counted by matching each `<v-dialog>` and `<AppDialog>` tag to its
closing tag and reading the whole subtree.

| Chrome | Dialogs | Shape |
|---|---|---|
| `AppDialog` | 23 | Header row, footer slot, always a close button. 61 `AppButton`, **zero** `v-btn`. |
| `v-card` | 18 | 4px corner, no heading element, uppercase `v-btn`. 15 with a title, 12 with an actions bar, 3 with neither. |
| hand-rolled | 3 | No `v-card` at all. Bare `div` and `button`: delete-forever, retention-reduction, library-switch. |

There are 22 `v-dialog` tags; one is `AppDialog`'s own root, leaving 21 raw
sites in 17 files. **52 `v-btn` tags sit inside them and not one overrides
Vuetify's `text-transform: uppercase`** (verified across every file and both
shared stylesheets; the only `text-transform: none` in the app is on
`.app-btn-base`, which is already slated for deletion).

`AppDialog` wins on every part that matters: the radius tier, a real heading
element, a title that wraps, an unconditional close button, the Escape/Enter
contract, the app's focus ring, and sentence-case buttons. It also has five
faults of its own, corrected before the other 21 arrive.

#### The five corrections

**1. One 16px gutter, and it is tighter than what shipped.** The body was
`--space-6` 24px against a footer on `--space-5` 16px, so every confirm button
in the app sat 8px further right than the content above it. Both resolve to
16px, and the **body comes in** rather than the footer going out, because the
body was also too airy. Measured in a browser from the shelf-edit dialog's real
markup:

| Rename dialog, 420px | Body | Content | Empty | Whole dialog |
|---|---|---|---|---|
| as shipped | 122px | 58px | 52% | 234px |
| corrected | 81px | 49px | 40% | 193px |

The header's 12px right padding stays: the 32px close button is optically
centred 28px in, which lands its glyph on the footer's right edge.

**2. The body spaces its own children** with `gap: --space-5` on a flex column,
not a `margin-bottom` on each field. A trailing margin has nothing after it to
separate from, so it stacks under the body's padding and becomes dead space.
Five dialogs use margins today, one already uses `gap`.

**3. The close button takes `--radius-sm`,** not the `--radius-md` it has.
Missed by the button work because it is not an `AppButton`.

**4. The dialog names itself.** Vuetify sets `role="dialog"` and
`aria-modal="true"` but wires no name, and **zero of the 44** carry
`aria-labelledby`. The 18 `v-card` ones have no heading element either, because
`v-card-title` renders a `div`. A screen reader announces "dialog" and stops.
The title is already an `h2`; it gets an id and the dialog points at it, which
fixes the name for all 44 as they migrate.

**5. The motion is a phone pattern.** `dialog-bottom-transition` enters from
`translateY(calc(50vh + 50%))` and animates no opacity, so a centred desktop
dialog is a solid object crossing the whole screen in 225ms. It becomes the
default `dialog-transition`, which is `scale(0.9)` plus a fade and is what the
other 18 already do.

#### Widths: sixteen become four

| Step | Width | Absorbs | Dialogs |
|---|---|---|---|
| `--dialog-w-sm` | 420px | 380 - 440 | 11 |
| `--dialog-w-md` | 520px | 460 - 560 | 21 |
| `--dialog-w-lg` | 720px | 620 - 736 | 8 |
| `--dialog-w-xl` | 840px | 820 - 840 | 3 |
| `fullscreen` | 96vw | 980 | 1 |

17 dialogs do not move, 17 grow, 9 shrink by at most 40px. Only three widths
shrink at all: the 440s to 420, the 560s to 520, and the 736 to 720. Those nine
want a look at their content before the change lands; growth is free. The old
480px default sat in no cluster and becomes `md`. **Never a pixel width at a
call site** is the rule that stops sixteen coming back.

#### Reduced motion does not work, and not only on dialogs

Measured in a headless browser on the two real rules rather than reasoned about:

```
prefers-reduced-motion: no-preference  -> transition-duration: 0.225s
prefers-reduced-motion: reduce         -> transition-duration: 0.225s
```

Vuetify writes its duration `!important` on a class selector
(`.dialog-bottom-transition-enter-active`); `design-tokens.css` writes
`0.001ms !important` on the universal selector. Same importance, and the class
wins on specificity. **All 44 dialogs animate at full travel whatever the user
has asked for.** The fix is a matching class-level rule, not a stronger
universal one, and the general lesson belongs beside the reduce block: a reduce
override must match or exceed the specificity of what it overrides.

#### Two defects in the chrome that is going away

**A data-driven title clips silently.** `v-card-title` ships
`white-space: nowrap` with `text-overflow: ellipsis`. `TaggerPluginSettingsDialog`
renders `{{ plugin.display_name }} - Settings` in a 460px dialog; after the 16px
side padding that leaves 428px, roughly 40 characters at 20px. A long plugin
name loses its own name to the ellipsis and the user reads "Settings".
`AppDialog`'s `h2` wraps and the header grows, which is correct as-is.

**One dialog has no visible way out.** `ShortcutsDialog` is 480px of `v-card`
holding a table: no close button, no footer, no button of any kind, no key
handler of its own. Escape and a scrim click work and its own content lists
`?` / `F1` as the toggle, so a keyboard user is told; a pointer user is given
nothing to aim at. Six of the 18 have no action bar; this is one of the two
with no button anywhere.

#### What the focus-ring exclusion becomes

`style.css` excludes `.v-btn`, `.v-overlay__content` and
`.v-overlay__content > .v-card` from the app-wide 3px ring, for the documented
reason that Vuetify paints elevation with a box-shadow. The consequence is that
the 52 buttons in those 18 dialogs never get the app's focus indicator. Once
`v-btn` and `v-card` leave the dialogs, the exclusion is deleted rather than
narrowed.

### Tooltips: one surface, and one narrow survival for `title` (approved 2026-09-13)

Proposal and specimens (rendered, both themes):
<https://claude.ai/code/artifact/0f1be078-e5e1-48a4-967e-189f30daba58>.
Primitive: `components/core/Tooltip.*` and the `guidelines/tooltips` card in the
published Claude Design project.

Two parts of the audit line were wrong, and the second changes the work.

**The count is 391, not 242.** A naive string count gives 816, because `title`
is also an ordinary prop: 134 of the matches are `AppDialog`, `SettingsSection`
and friends taking a heading. Counted per tag, there are 391 real browser
tooltips: 254 on `<button>` and 137 on spans, divs, labels and selects.

**`HelpTip` is not a third form.** It is a thin wrapper around the same
`v-tooltip` with one prop the other ten do not set, and it is the only one
configured correctly. Its docblock already reasons the whole thing out. So this
is not three forms converging on one; it is one correct answer that stayed
inside the component that needed it.

| Mechanism | Count | Where |
|---|---|---|
| native `title=` | 391 | 254 on buttons, 137 elsewhere |
| `<v-tooltip>` | 10 | Vuetify's defaults, in five files |
| `HelpTip` | 2 | the same `v-tooltip`, plus `interactive` |

#### What each mechanism reaches

| | native, 391 | v-tooltip, 10 | HelpTip, 2 |
|---|---|---|---|
| Opens on hover | yes | yes | yes |
| Opens on keyboard focus | **no** | yes | yes |
| Reachable on touch | **no** | yes | yes |
| Hoverable (1.4.13) | exempt | **no** | yes |
| Escape dismisses | **no** | yes | yes |
| Our type and surface | **no** | Vuetify's | Vuetify's |
| Open delay | ~1s, the browser's | **none at all** | none at all |

**The 1.4.13 exemption is real and it cuts the other way.** The criterion
excuses content whose presentation "is controlled by the user agent and is not
modified by the author". The native tooltip is exactly that, so the 391 are not
a conformance failure. They are simply invisible to anyone not using a mouse.
The ten Vuetify ones get no such excuse, because the author styled them, and
`VTooltip.css` sets `pointer-events: none` on the surface. **Ten of the eleven
fail the hoverable criterion; the eleventh is `HelpTip`.**

**Neither delay was chosen.** Vuetify sets none: `useDelay` reads an undefined
prop, `Number(undefined)` is `NaN`, and `setTimeout(cb, NaN)` fires on the next
tick, so a Vuetify tooltip opens the instant the pointer crosses the control.
`--tooltip-delay: 400ms` is a starting value, not a measurement.

**One thing the defaults get right by accident.** `openOnFocus` has no default
of its own and resolves to `openOnHover`, which is true for a tooltip. All
eleven already open on focus, and `ScrapheapSection`'s explicit `open-on-focus`
is asking for what it already had.

#### What the 391 are carrying

The 254 on buttons:

| | Count | What `title` is doing |
|---|---|---|
| icon-only, no `aria-label` | 47 | it is the control's **only** name |
| icon-only, with `aria-label` | 44 | a name for AT and a tooltip for the mouse |
| labelled | 158 | supplementary: a shortcut, a caveat, a full path |

Of the 71 carrying both, **42 repeat the identical string** and 29 differ. Some
differ on purpose, where the name is the action and the title is the evidence.
Others have drifted: `TitleBar` names a button `"Dismiss update alert"` and
titles it `` `Dismiss v${latestVersion} update alert` ``, so the pointer user
learns the version and the screen-reader user does not.

The 137 that are not on buttons: **107 carry information available nowhere
else**, 30 restate text already clipped on screen. That ratio is the finding.
It would be comfortable if the native tooltips were mostly redundant reveals of
ellipsised text. Four in five are the only place the information exists.

#### The three moves

1. **Name the configuration.** `Tooltip` is the `HelpTip` settings on the app's
   own surface rather than Vuetify's 92% grey: `--text-sm` on `surface`,
   `--radius-md` (changed from `--radius-sm`, 2026-09-13), 1px border, `--elevation-3`, `--tooltip-max-w` 280px,
   `--tooltip-delay` 400ms, and `interactive`. `HelpTip` stays as the preset
   for the "why is this unavailable" mark, built on `Tooltip`.
2. **One string, both jobs.** `AppButton` and `AppBarButton` take a `tooltip`
   prop that sets the accessible name *and* renders the tip, so the 42
   duplicated strings become 42 single strings that cannot drift. An explicit
   `aria-label` still wins where the two genuinely differ.
3. **`title` survives in exactly one case:** where it restates text already on
   screen that is clipped. 30 sites qualify. That rule is enforceable by
   reading one attribute against its own element's content, which matters more
   than the sites it saves. **Everywhere else a `title` is a bug report.**

#### Not a mechanical conversion

The 107 non-button tooltips carrying new information want arguing about one at
a time. A `<div title="Show sidebar">` wrapped around an icon is not a tooltip
problem, it is a control implemented as a `div`. A filter option whose
explanation lives only in `opt.tip` may want that sentence on the page rather
than behind a hover.

#### The standing rule

A tooltip is a second route to information, never its home. `HelpTip` gets this
right: its reason is also rendered as visible text and pointed at by the
blocked control's `aria-describedby`. Nothing that a user must have may live
only in a tip.

### Off-token values, and the layer boundary (approved 2026-09-13)

Audit: <https://claude.ai/code/artifact/4c017a07-5e00-427f-9108-1535bb92652c>.
Rules: `guidelines/layers-and-scales.md` in the published Claude Design project,
with the ladder mirrored into `tokens/spacing.css`.

Read as parsed CSS declarations from every `<style>` block and `.css` file, with
comment bodies stripped first so a token value named in a note is never counted
as a use.

| Family | Tokenised | Raw | Off | Distinct | Diagnosis |
|---|---|---|---|---|---|
| `font-size` | 826 | 75 | 8% | 28 | one author's habit, in three files |
| `border-radius` | 485 | 91 | 16% | 11 | a rule that did not exist until this week |
| control height | 21 | 54 | 72% | 17 | decided 2026-09-12, not yet built |
| `z-index` | 29 | 89 | 75% | 40 | two stacking systems in one number space |

**Three of the four need no new tokens.** Reading a bypassed scale as "too
coarse" is wrong three times out of four here.

#### The one that needed a decision: the ladder stops at `--z-drawer`

Vuetify assigns every overlay its z-index at runtime. `useStack` takes the
current top of the global stack plus ten, from a base of 2000:

```js
const lastZIndex = globalStack.at(-1)?.[1];
_zIndex.value = lastZIndex ? lastZIndex + 10 : +zIndex.value;
```

A menu opened inside a dialog inside an overlay is therefore not at 2000. It is
wherever the stack has climbed to, and it climbs further with every overlay the
user opens. A hand-written `2010` ties with Vuetify's second overlay and loses
to its third. **Everything the app writes above 1000 is betting against a
number that moves.**

Three signatures, each naming its cause:

- **The plus-one.** `1001` over `1000`, then `2001`, `2002`, `2010`, `301`.
  Every one means "just above my own layer", which an absolute-integer ladder
  cannot say. Rule: those two elements belong in the same stacking context and
  should be ordered by DOM order.
- **The hand-cranked staircase.** `4210`, `4250`, `4300`, `4350`, `4400`, all
  wedged between `--z-modal` and `--z-titlebar` by one feature whose overlay is
  itself modal, so everything inside it has to clear it.
- **The nine-thousand.** Five dropdowns at `9999`, every one anchored to a
  control that can appear inside a dialog. `--z-dropdown` is 300, right for a
  toolbar menu and hopeless for a field inside a modal at 4000.

**The rule: if it must clear a modal, it must *be* an overlay,** not a
positioned element with a larger number. Anything that teleports to the body
leaves the ladder and takes its order from the stack, where relative order is
correct by construction. The rungs above `--z-drawer` stay only for surfaces
that remain in the document, and no fourth one gets added.

None of the three signatures is a mistake, which is why none can be linted away.

#### The three that were already answered

**A raw value equal to a token is always a bug.** 21 raw radii write a value
that *is* a token: `999px` nine times where `--radius-pill` exists, `8px` six,
`4px` five, `12px` once. 65 raw font sizes are the same story. No design
question in any of them.

**A scale whose rungs are 1px apart cannot be missing a rung.** Ten font sizes
land on a half pixel (10.5, 11.5, 12.5, 13.5) and all ten are in three files of
the review feature. The ramp's bottom four rungs are 11, 12, 13 and 14px.

**A progress track takes `--radius-pill`,** which rounds by height, not a
literal `2px` that is correct only while the bar is that tall.

And the radius tier approved this week already claims 14 more of the raw radii,
which is why they need no separate decision: the values were not drift so much
as the absence of a rule, and the rule now exists.

### Icon sizes: no scale, two rules (approved 2026-09-13)

Examples: <https://claude.ai/code/artifact/158678cd-76d4-466d-afff-1b07a9566091>.
Rules: `guidelines/layers-and-scales.md`; the glyph box is now mirrored into the
design system as `--gutter-glyph`.

712 `v-icon` tags, 669 with a literal size, **23 distinct values**, every
integer from 10 to 20 in use. `SideBar.vue` alone uses twelve sizes across 118
icons. But two thirds are already on the type ramp, so this is one convention
holding for most uses and a tail that is not.

**Icons get no scale of their own.** A third scale would give people a fourth
number to pick from. Two rules cover it and only one is new.

1. **A component owns the size of its own icon slot,** and a call site never
   passes one. `AppButton` already binds `:size="size === 'sm' ? 16 : 18"` and
   **zero of its call sites pass a size**, which is why it contributes no drift
   at all. The size is drifting because the decision is at the call site, not
   because the scale lacks a rung.
2. **A free-standing icon tracks its adjacent text.** This is already
   `visual-language.md` §8 and 66% comply. What it lacked was a stated default:
   **16px**, the size that fills `--gutter-glyph`, whose own comment already
   called it "the de-facto glyph box: chevrons and row icons". The answer was
   written down and then used twice.

#### Two carve-outs, and I got the first one wrong

**13px and under is an inline mark and does not move.** A 10px lock in a
context-menu header pill, 11px clear glyphs in the stats sidebar, 11px lock and
shared marks inside sidebar labels. My first draft swept these up because they
sit inside a menu or a button, and would have pushed an 11px badge beside 11px
text to 16px. A component owns its own slot, not every glyph inside it.

**Above 28px is display art and does not move.** The type ramp stops at 28, so
an empty-state graphic has nothing to track. 20 icons.

#### Rule three: an icon-only control's glyph is sized to the control

**Settled at 24px, 2026-09-13, after two wrong answers.** A labelled button's
glyph sits beside text and tracks it, so 18px. An icon-only button has no text
to track: the glyph **is** the control's content, so it takes its size from the
control. At `--control-h-bar` (32px) or taller that is **24px**.

**The discriminator is the CONTROL's height, not the icon's.** Sizing by icon
size sweeps up things that are not bar chrome: `.app-stepper__btn` is 26px tall
with a 17px glyph, and a 24px glyph in a 26px button is absurd. Bucketed from
the CSS rather than the markup:

| Icon-only buttons | Count |
|---|---|
| at 32px or taller, take 24px | 42 |
| below 32px, unchanged | 22 |
| height not declared in CSS, needs a look | 18 |

**This cannot be scripted from the templates.** The icon size lives in the
markup and the control height lives in CSS keyed by class, so the migration
goes class by class.

#### Why 24, and not 20 or 22

MDI paths are authored on a 24-unit grid, so 24px is the only size at which a
glyph renders as drawn.

| Size | Scale | A 2-unit stroke renders at | Fill of a 32px button |
|---|---|---|---|
| 18px | 0.750 | 1.50px | 56.2% |
| 20px | 0.833 | 1.67px | 62.5% |
| 22px | 0.917 | 1.83px | 68.8% |
| 24px | 1.000 | 2.00px | 75.0% |

Every other size lands the stroke on a fraction and the browser softens the
edge. 22px is the fallback if 24 reads too large; it is at least on the ramp.

#### Nothing gets taller

The band is 36px, the button 32px, so a 24px glyph has 4px inside the button
and 6px to the band edge. `.bar-btn` draws no background and no border at rest,
so the 32px is a pointer target sized to clear WCAG 2.5.8, not a frame.

One thing moves sideways: a split button holding an icon and a chevron goes
from 57px to 62px, so a toolbar with three of them is **15px wider**. The
icon-only buttons are fixed squares and do not move. The selection-pill verbs
are 34px and the grid's clear button 40px, so 24 fills 71% and 60% there.

#### The one exception, stated rather than hidden

**The dialog close button stays at 20px.** It is a 32px icon-only control and
the rule would give it 24, but it shares a header row with an 18px title, and a
close affordance that is the largest glyph in the row inverts the hierarchy of
a header whose subject is the title.

#### Three attempts, and why the first two were wrong

**18** was derived from `AppButton`, a 28px *labelled* control where 18 is
right precisely because the glyph has words beside it. It would have shrunk
every bar icon. **20** was where six of nine toolbar buttons happened to sit,
which is not a decision. **24** is the one with a reason behind it.

#### What it changes

241 of 669. 158 move one pixel, 41 move two, and 42 move four or more, which is
the bar chrome finally growing.

| Move | Icons |
|---|---|
| 15 to 16 | 135 |
| 14 to 16 | 29 |
| 20 to 24 | 17 |
| 19 to 24 | 15 |
| 19 to 18 | 11 |
| 20 to 18 | 9 |
| 18 to 24 | 9 |
| everything else | 16 |

Twenty-three distinct sizes become nine: `11, 12, 13, 14, 16, 18, 22, 24, 28`.
Eight are the type ramp; the ninth is the icon-only bar size.

Twenty-four icons are left exactly as they are because their control's height
is not declared in CSS. One of the 42 is a 14px glyph in a bar-height button, a
ten-pixel jump, which is more likely a mistake in the markup than a case for
the rule.

#### A menu row has two glyph slots and they are one size

The 14px cluster in the image-grid context menu is not an inconsistent row
icon. It is the **trailing submenu chevron**, a second slot. `--gutter-glyph`'s
comment reads "chevrons and row icons", so both slots were always the same box;
the app drew them 14 and 15, which is two wrong numbers rather than a
distinction. `MenuRow` now takes a `submenu` prop that renders the chevron at
16px with `aria-haspopup`.

#### Why bother when nothing visibly moves

The 15px cluster is one pixel on a glyph alone in a 16px gutter. It is worth
removing because the sidebar and image-grid context menus both use 15px while
the shelf menu uses 16px, so two menus that open on the same screen draw the
same row differently. The toolbar is the closest to visible: nine glyphs in one
band alternating 20px and 19px in the order the buttons were written. Even
there, nobody would report it.

### The control tier moves as a set

| Component | Today | Proposed | Why it has to move |
|---|---|---|---|
| `AppInput` | 27px, r8 | 28px, r4 | Sits beside a button in every dialog form |
| `AppSelect` | 27px, r8 | 28px, r4 | Same row as the input it follows |
| `AppStepper` | 26px, r8 | 28px, r4 | Picked 26 for no stated reason |
| `AppTextarea` | r8 | r4 | Padding-sized, so radius only |
| `.tbm-input`, `.tbm-num` | 36px, r8 | 28px, r4 | Or popovers keep a taller field than dialogs do |
| The hand-copied 27px sites | 27px | `--control-h` | Seven places that would otherwise silently stay at 27 |

### Visible changes to expect

- **Uppercase labels disappear from nine dialogs.** The largest single visual
  change, and the point of the exercise.
- **Migrated buttons gain the real focus ring.** `style.css` excludes `.v-btn`
  from the app-wide 3px accent ring because Vuetify paints elevation with a
  box-shadow. Once `v-btn` leaves, that exclusion goes.
- **Disabled loses the grayscale filter.** `App.css` fades `.v-btn--disabled`
  to 0.35 with a 30% grayscale; `AppButton` uses the 0.38 token and no filter.
- **Eleven outlined buttons become filled neutral,** pending that decision.
- **Popover actions shrink 36 to 28,** and the selection verbs 34 to 32.

### What gets deleted, which is what pays for the work

- Three components carry `:deep(.v-btn--loading)` patches to stop Vuetify
  blanking the label while pending, which `visual-language.md` §11 forbids and
  Vuetify does anyway. Roughly 30 lines in `FolderEditor`, `FolderBrowser` and
  their reduced-motion siblings.
- `SideBar.css` patches `.v-btn__content` font-size twice, because `v-btn`
  sizes its own label wrongly for this app.
- `App.css`: the `.v-btn--disabled` override, the `.v-btn:focus` override,
  `.app-btn-base`, the whole `.tbm-action` family, `.selbar-btn`.
- `style.css`: the `.v-btn` exclusion in the global focus rule.
- The five parallel families above, roughly 78 class declarations for
  behaviour `AppButton` already has, and sixteen close buttons that become one.

### What it does not touch

No test asserts button geometry. `AppButton`'s suite covers pending and focus
continuity only, and the end-to-end suite references neither `.v-btn` nor
`.app-btn`, so the geometry is free to move. Six unit tests query `v-btn` and
follow the components they test.
