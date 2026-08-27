# v1.11 "Your existing library" — design loop, pass 1

Ten artboards, two pages. Canvas URL in `LINK.md`. Rebuild with
`python3 build.py` then re-seed with the `/design` skill's `seed-canvas.mjs`
against the artboard list and `canvas.json`.

## Chosen

**Direction A, the tree.** Assign an entity to a whole level, override per row.
The deciding argument is that two folders at the same depth can legitimately
mean two different things, and only a tree can say so per row.

**Rejected: the pattern formula.** The 2–3 repeating path shapes as assignable
formulas, six decisions instead of 341. Scales better and reads faster, but a
position in a path can only carry one meaning, so per-folder overrides are
inexpressible. Dropped after the first review.

## The rule the release is built on

**A picture moves only when its folder stops being true.** Not whenever
something about it changes.

| Action | Folder still true? | Files moved |
|---|---|---|
| Import an existing library | true by construction | **none, ever** |
| Add a second project or person | yes | none |
| Rename a project | yes, under a new name | none — the **folder** is renamed |
| Remove the project its folder is named after | no | moves |
| Swap one project for another | no | moves |

Three consequences fall out rather than being designed in:

1. **Import moves nothing.** The assignments are derived *from* the paths, so
   every path is true the moment it is written.
2. **Many-to-many stops mattering.** Nothing is ever re-derived, so a picture in
   three projects never needs a winner picked after import.
3. **A folder outside the layout contradicts nothing**, so it never moves. That
   makes "drag it somewhere of your own" a permanent override needing no
   setting.

Accepted cost: the tree is never *wrong* but drifts from what the owner would
have picked. Hence **Move to match** as an offered action on a picture, never
automatic.

### The mirror, for moves made outside PixlStash

PixlStash changes an assignment only when the owner's move makes it untrue.
The ambiguity is that leaving a project's folder cannot distinguish "left the
project" from "refiled the picture", because a folder holds a picture once and
a project can share it.

Resolved on measurement, not taste. Across the owner's four real libraries
(~59,000 pictures): 91–100% of assigned pictures have exactly **one** project or
set, and nothing anywhere is in more than three. So the move is unambiguous for
almost all of them and is applied; the few with several are listed and left
alone until asked.

The one facet that genuinely breaks is **people, and only in photo libraries**:
0% multi-person in all three generation libraries, 22.4% in `family-images`.
That number only mattered under a re-derive rule. Under move-when-false it does
not, which is why Person survives as a path segment.

## Also decided

- **Person / People**, never Character. `character` is the model name only; the
  shipped UI says People.
- **"Just a folder"** replaced "Leave alone", which implied the *files* would be
  untouched. Every option leaves the files untouched. This one says the name is
  not telling us anything.
- **Managed libraries are gone as a concept.** A PixlStash folder is a
  referenced folder that starts empty, defaulting to `Project / Person or Set`.
  Today's flat libraries need no migration: files at a library root match no
  layout, contradict nothing, and stay put.
- **A layout segment can hold alternatives** (`Person or Set`), first match
  wins, and a segment with nothing to fill it is skipped. That keeps the tree
  two deep rather than five.
- **No hidden `.pixlstash-images` folder.** Pictures arriving somewhere the
  owner cannot see them is the opposite of help for a curated library.
- **Ask for an empty library, point at a folder that is not empty → two ways
  forward only**: bring them in, or pick a different folder. "Start empty in
  here anyway" is a trap.
- **Telemetry is not redesigned.** `TelemetryConsentDialog` already asks on
  first startup and is good. It appears on the first-run artboard only to fix
  the order: the question first, then the folder.
- **Settings › Libraries is drawn as the dialog it lives in** — 820px with the
  nav rail — which is why the list is compact rows rather than a table.

## Open, for pass 2

- **The fixture pack.** Every figure and folder name is placeholder except the
  membership counts. Real tree, real counts, real thumbnails, per the loop
  protocol.
- **The drag interaction**, made operable by keyboard. Pass 1 is static.
- **Keyboard keys.** Digits 1–4 and 0, because Project and Person both want P.
- **The Views spike.** Windows symlinks need admin or Developer Mode; the
  fallback is hard links, which cannot span drives, and exFAT has neither.
  Views is the release's flex candidate if that spike fails.
- **Duplicate facets in the layout builder.** `Project / Person or Set /
  Person or Set` is expressible and meaningless.

## Pass 6: MapTree

**Look at it:** open `design/1.11-existing-library/MapTree.dc.html` in a
browser (rebuilt with `python3 build.py`). The dialog is at the top; the strip
under it shows a linked selection, the Mixed band control in its two- and
three-kind forms, and the menu on a selected row.

Pass 5 invented a selection look. Pass 6 uses the app's: the rail, the wash
and the text token from visual-language §11 and `frontend/src/style.css`. And
a level whose rows do not all agree now says so on its band. Everything else
stays: 840px, the tally strip, zebra rows, banded levels, coloured dropdowns
on every row and band, the footer, no drag, no checkboxes.

### Width: 840px

Pass 1 was 1440 wide with 280 + 288px of side columns, leaving the tree 392px
and the columns mostly air. The shipped 960px dialog dropped the columns and
the tree then ran the full width with nothing to the right of a folder name.
The row is name, count and a 152px dropdown; with the dialog padding, the
band's filter box and its own dropdown, 840 is what fits and still clears a
1366px laptop with margin.

### The strip: the tally

The old "What things are" and "What this makes" panels said the same five
words, so they are one strip under the header: *Projects 2 · Sets 32 · People
118 · Tags 4 · Just a folder*, each in its kind's colour, each the live count
of what the mapping makes. A kind that makes nothing shows no number. The
strip is not interactive and carries no hint.

The **footer** holds the two ways out: *Drop this, organise later* far left,
*nothing is written yet* and **Review and import** far right.

### The dropdown carries the kind. Nothing else on the row does.

One control, `.kdd`, 152px wide, on every row and every band, filled with
the kind's wash, edged and lettered in its colour, with icon, name and
chevron. *Just a folder* is the muted one; an unset level shows a dashed
neutral *choose…*; a row with no answer of its own shows its level's default.
Opening it lists the five kinds with their digits, the two candidates first
where the read narrowed a level to two. That menu is the only place the
digits are printed.

Pass 5's kind-coloured rail on the row is gone. The row's left edge is now
the **selection rail** (§5.1, "always present and always transparent"), so
the folder icon is neutral and the dropdown is the only thing on a row that
says what it is. One colour language per element.

### Selection: the house convention

**The rule: acting on a selected row acts on the whole selection.** Its
dropdown, or a digit while it is focused. No bulk bar, no checkbox, no
marquee; the row's own dropdown is the bulk control.

- Click selects, Shift+click extends the range, Ctrl/Cmd+click toggles, Esc
  clears. Selection is scoped to one level; clicking in another level starts
  a new selection there.
- A selected row is `--active-wash` fill, `--active-bar` on the 3px rail that
  every row already has, and `--active-text`; its folder icon takes
  `--active-bar`, the way `.row.on .lead` does in the sidebar. Exactly the
  tokens, per theme: dark is `rgba(primary, .26)` with a primary bar and
  `on-primary` text, light is `rgba(accent, .2)` with an accent bar and
  `on-surface` text (`frontend/src/style.css`).
- **The mark.** Every selected row's dropdown gets a small arrow in the
  gutter to its right, pointing at it, and the band's dropdown gets it too
  while a selection exists. It is absolutely positioned, so the column keeps
  its width, and it is drawn in `--active-bar`, not the kind colour, so it
  reads as part of the selection and not as a sixth kind. (A glyph inside the
  control was tried first and did not read.) It is the one
  thing that says *these change together*, without a word on the row; the
  band label *Set 3 selected to* says the number. The selection bar's
  "N selected" cluster was considered for the band and not used: the three
  fixed labels already carry the count and a second one would repeat it.
- **Hover on a linked dropdown.** Hovering any selected row's dropdown deepens
  its fill (22% to 40% of the kind colour) and deepens every other selected
  row's dropdown too, to 31%, so the group is felt as a group before anything
  is clicked. The band's dropdown joins in the same way. Leaving clears all of
  them together.
- **Zebra under the wash.** Even rows of a level are `on-surface` at **0.06**;
  selection is `--active-wash` and always wins (the rule is
  `.tree .tnode.tnode--sel`, more specific than the stripe). Alphas for the
  implementer: stripe `rgba(var(--v-theme-on-surface), 0.06)`, selection
  `var(--active-wash)` as shipped, nothing new.
- **A known collision, accepted.** Tag is `primary`, and so is the dark
  theme's selection. A selected Tag row is olive on olive. The rail and wash
  are selection and the dropdown is kind, so it still reads, and the house
  convention wins over a per-screen exception.

The kit paints selection in the light theme's accent on its dark canvas. This
board scopes the dark values onto itself (`.mt`) by referencing `--primary`
and `--primary-on`; the kit's drift is noted here and not fixed, since the
kit follows the design system and not the other way round.

The band control has exactly three labels: *Set them all to* / *Set these N
to* (a filter is active, and the control applies to the visible rows only) /
*Set N selected to* (a selection exists). A selection inside a filtered level
reads as the selection.

### Mixed levels

When a level's rows resolve to more than one kind, the band's dropdown reads
**Mixed**: `on-surface` text on a neutral control (`--input-bg` fill,
`--border` edge, a muted rail, no icon) with a diagonal stripe alternating the
distinct child kinds' colours. Two kinds, two stripes; three, three; four,
four. Choosing a kind from it sets every row in the level; the row dropdowns
keep their own colours until then. On the board, level 2 (Project, Project,
Set, Just a folder) is the three-stripe case and level 3 (Person and Set under
the *mira* filter) the two-stripe case.

The recipe, for the implementer:

```css
background-color: rgb(var(--v-theme-input-bg));       /* the kit's --input-bg */
background-image: repeating-linear-gradient(45deg,
  rgba(var(--v-theme-<kind-1>), 0.16) 0 6px,
  rgba(var(--v-theme-<kind-2>), 0.16) 6px 12px
  /* , rgba(<kind-3>, 0.16) 12px 18px, rgba(<kind-4>, 0.16) 18px 24px */);
```

45 degrees, 6px per stripe, every stripe at 0.16, the stripes in the order
the kinds appear on the strip (Project, Set, Person, Tag, Just a folder), the
child kinds only. *Just a folder* stripes in `on-surface` at the text-muted
alpha, which is faint on purpose. The artboard sets the colours as
`--s1..--s4` custom properties on the control and mixes them with
`color-mix`; the app has the `rgba(var(--v-theme-*))` form and needs no
custom properties.

### Rows and bands

Rows are a grid, `22px | 1fr | 72px | 152px`: folder icon, name, count,
dropdown, behind a 3px transparent rail. Indent is padding on the row, so
the count and dropdown columns sit at one x on every level. A level band is
36px of `--chrome`, full width of the scroll area, with a divider above and
below; left is the chevron, *Level 3 · 149 folders* and a filter box when the
level exceeds the visible cap; right is the band label and the level
dropdown. Level 1 is the library root and has neither. A collapsed level
shows its band only.

### Keyboard

Rows are `tabindex="0"` and focusable in tree order. Space toggles selection,
Shift+arrows extend it, Esc clears it. On a focused row `1` Project · `2`
Set · `3` Person · `4` Tag · `0` Just a folder, applied to the selection if
the row is in one, else to the row. Enter opens the row's dropdown; Tab
reaches it; from a band, Tab reaches its dropdown. Both open on Enter and
Space and are the same menu. Focus is `--focus-ring`, the solid 3px accent.

**What the screen reader hears.** The level is a `role="tree"` with
`aria-multiselectable`; each row a `treeitem` with `aria-selected`, named
*2024 Shoots, 18,204 pictures*, `aria-keyshortcuts="1 2 3 4 0"`. The row
dropdown is a button named *Kind, Project* with `aria-haspopup="menu"`, and
on a selected row its description is *applies to the 3 selected folders*,
which is the mark, spoken. The band dropdown is named by its label, *Set 3
selected to, Mixed: Project, Set, Just a folder*. One polite live region
announces the result: *3 folders are now Sets* or *Level 2: all 14 folders
are Projects*; the strip is a `status` region so the tally is read after it
moves.

### Colour

A kind is drawn in its own colour in the strip chip, the row dropdown, the
level dropdown, the menu item and the Mixed stripes, and nowhere else on the
row. Selection is `--active-wash` / `--active-bar` / `--active-text`. The
artboard uses the kit's own role tokens and no hex; in the app they are the
theme tokens below.

| Kind | Digit | Token in the app | Kit variable |
|---|---|---|---|
| Project | 1 | `rgb(var(--v-theme-tertiary))` | `--tertiary` |
| Set | 2 | `rgb(var(--v-theme-accent))` | `--accent` |
| Person | 3 | `rgb(var(--v-theme-secondary))` | `--secondary` |
| Tag | 4 | `rgb(var(--v-theme-primary))` | `--primary` |
| Just a folder | 0 | `rgba(var(--v-theme-on-surface), var(--opacity-text-secondary))` | `--text-muted` |

Washes are the token at 14 to 22% (`color-mix`), never a second colour. The
dropdown label is `--text-xs` semibold on its own wash, under the ≥14px bold
floor §4 allows those tokens as foregrounds; the icon and 3px rail carry the
3:1 UI floor and the label is checked at implementation against
`frontend/src/styles/design-tokens.test.js`. No Tiny5 anywhere on this
screen.
