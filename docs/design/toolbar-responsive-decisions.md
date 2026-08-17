# Toolbar decisions: undo/redo placement and responsive collapse (2026-07-30)

> **Read this file as a log, newest last.** Amendment #4 (2026-08-17) is the
> current record for the **Duplicates** bar: it measured the ladder the earlier
> sections designed, found the bar had never fitted its own container at any
> width, and replaced every rung. Any width given for that bar before amendment
> #4 — 860 / 720 / 600, and the fold classes named after them — is history, not
> the shipped CSS. The grid bar's ladder is unchanged, and amendment #2 remains
> the record for it.

## Current state (verified, not assumed)

**Grid toolbar** (`Toolbar.vue`, `.selection-bar-overlay`, 36px band, container
`selbar`, container queries at 960/840/800/740/580px):

- Left group: Sort split-button → **UndoControl** (owner-only) → Filter (count
  badge) → View → separator → Search → Export → Import (owner-only) → ComfyUI
  (if configured)
- Right group: separator → Review-and-fix-tags → TbGlobalActions (Settings,
  Stats toggle with activity dot)

**Duplicates toolbar** (`DuplicateQueue.vue`, `.dq-toolbar`, 36px band, **no
container queries today**):

- Left: count headline → "N done this session" → Decided toggle → separator →
  Tier gate button → Scope pill (when scoped)
- Right: size slider + label → Auto-stack button (conditional, accent) →
  separator → **UndoControl** → TbGlobalActions

So undo/redo sits mid-left in the grid and right-adjacent-to-TbGlobalActions in
Duplicates. A user who learns one position loses it in the other view. That is
the inconsistency to resolve.

## Decision 1: undo/redo placement

Canonical tail of EVERY toolbar: `[separator] [UndoControl] [TbGlobalActions]`.
The Duplicates bar is already correct — no change there. Grid toolbar
(`Toolbar.vue`): (1) remove UndoControl from the left group (between the sort
split-button and Filter); (2) the right group DOM order becomes: existing
separator → Review-and-fix-tags (unchanged) → NEW bar-separator → UndoControl
(`v-if !isReadOnly`) → TbGlobalActions. The new separator is required (Gestalt
boundary between view-local and app-wide chrome, matching the Duplicates bar's
boundary comment). The read-only tail degrades identically in both bars.

## Decision 2: responsive collapse

Conventions: container queries (extend the shipped selbar convention); shared
container name — the grid bar declares `container-name: selbar toolbar;`, the
dq bar gains `container-type: inline-size; container-name: dqbar toolbar;` —
shared components (UndoControl, TbGlobalActions, overflow) write scoped
`@container toolbar (max-width: …)` rules so they degrade identically in both
bars. New `TbOverflowMenu.vue` in `panels/`: a `.bar-btn.bar-btn--icon`
trigger with `mdi-dots-horizontal`, an IN-PLACE absolute `.tbm` panel copying
the dq-tier-wrap positioning + Escape-to-trigger pattern (NOT a teleported
v-menu — teleport escapes the container so the rows could not share the bar's
container queries), rows in the gb-recent-row/tbm-action recipe,
`--z-dropdown`, `aria-haspopup`/`aria-expanded`, focus returns to the trigger,
slot-based rows. Fold = CSS both ways: each foldable control exists as a
toolbar button AND an overflow row with the same `v-if` conditions; container
queries flip visibility; the ⋯ trigger stays hidden until the first fold step.
No ResizeObserver, no JS measurement. Existing touch-mode overrides untouched.

### Grid ladder (container selbar / shared toolbar)

- Shipped steps stay (≤960 sort icons, ≤800 gap-guard).
- selbar ≤700px: ⋯ appears in the right group between the second separator and
  UndoControl; Export, Import, ComfyUI, Review-and-fix-tags fold into it as
  rows (same v-ifs/emits: "Export grid to zip", "Import photos…", "Generate
  with ComfyUI…", "Review and fix tags…").
- toolbar ≤600px: Settings + Stats fold into ⋯ (rows "Settings…", "Stats
  sidebar" with pressed state); the tb-stats-activity dot MOVES to the ⋯
  trigger when `tasksStore.hasActiveTasks` and stats is folded (reuse the
  keyframes, respect prefers-reduced-motion); View folds into ⋯ ("View
  options…" opening the existing panel).
- toolbar ≤480px: UndoControl's history chevron hides; ⋯ gains a "History…"
  row calling `openHistory()`.
- toolbar ≤420px: redo hides. FLOOR: Sort (icons), Filter, Search, separator,
  Undo, ⋯. Undo NEVER folds or hides at any width.

### Duplicates ladder (container dqbar / shared toolbar)

> **SUPERSEDED by amendment #4 (2026-08-17).** Every width in this subsection
> is obsolete: the rungs were placed against an inventory rather than a
> measurement, and the bar overflowed at all of them. The shipped ladder is
> the table in amendment #4.

- dqbar ≤860px: qsub ("N done this session") hides entirely (no overflow row);
  the dq-size-value label hides (the slider keeps its aria-label).
- dqbar ≤720px: ⋯ appears before UndoControl; the Decided toggle + size slider
  fold into it ("Show decided" row with pressed state; "Thumbnail size" row
  hosting the same slider); the Auto-stack label shortens to "Auto-stack N".
- toolbar ≤600px: Settings/Stats fold + dot-to-trigger (identical shared
  rule); the scope pill compresses to icon + dismiss with the full label as
  tooltip; Auto-stack compresses to the flash icon + count, label as tooltip
  and aria-label.
- toolbar ≤480px / ≤420px: shared chevron/redo rules. FLOOR: count, Tier gate,
  scope pill (if scoped), Auto-stack (compressed, if present), separator,
  Undo, ⋯.

Undo never enters the overflow in either bar (a recovery control must stay a
single visible target); the "Changed elsewhere" warning therefore stays
surfaced at all widths.

### Ranking tables

> The Duplicates ranking below is **superseded by amendment #4**; the grid
> ranking still stands, with amendment #2's corrections.

Grid ranking: 1 Sort (never folds) / 2 Filter (never folds) / 3 Search (never
folds) / 4 Undo (never folds or hides) / 5 Redo (hides last step) / 6 History
chevron (folds to row at 480) / 7 View (folds 600) / 8 Stats (folds 600, dot
survives on trigger) / 9 Review-and-fix-tags (folds 700) / 10 Export (700) /
11 Import (700) / 12 ComfyUI (700) / 13 Settings (600).

Duplicates ranking: 1 Tier gate (never folds; label may ellipsize) / 2 Count
headline (stays, may truncate) / 3 Undo (never folds) / 4 Scope pill (never
folds while scoped; compresses at 600) / 5 Auto-stack (compresses, never
folds) / 6 Redo+chevron (shared 420/480) / 7 Decided toggle (folds 720) /
8 Size slider (folds 720 to a row hosting the slider) / 9 Stats/Settings (600
shared) / 10 qsub (hides 860, no row).

## Implementation checklist

> Historical: this was the plan for the 2026-07-30 change. Items 5 and 6 (the
> dq ladder's widths, and what the tests can cover) were overtaken by
> amendment #4.

1. `Toolbar.vue`: the move + separator; the container-name addition; the ≤700
   fold rules; mount TbOverflowMenu with mirrored rows; the View fold at 600.
2. New `TbOverflowMenu.vue` as specified (attention-dot logic included).
3. `UndoControl.vue`: scoped `@container toolbar` rules — 480 hides
   `.uc-btn--chevron`, 420 hides `.uc-btn--redo`; `openHistory()` is already
   exposed.
4. `TbGlobalActions.vue`: scoped `@container toolbar` 600 hides both buttons.
5. `DuplicateQueue.vue`: the dqbar container; ladder steps at 860/720/600.
6. Tests: the UndoControl aria contract with the chevron hidden; both hosts
   assert the tail order [separator][UndoControl][TbGlobalActions]; overflow
   rows honour read-only (Import row + UndoControl hidden). jsdom does not
   evaluate container queries — test DOM order/classes/row-mirroring and the
   v-if logic; the width steps are covered by the CSS being shared rather than
   simulated.
7. Docs: `frontend_architecture.md` UndoControl paragraph → right-side
   app-wide cluster; the §9 Duplicates paragraph gains a grid-now-matches
   sentence; the toolbar section gains the overflow pattern note. Append the ⋯
   overflow as a named toolbar pattern in `docs/design/visual-language.md`.

## Amendment (2026-07-30): separators and the tier label

**Separators — the principle:** a separator marks a SEMANTIC boundary, not a
group edge; the elastic gap already draws the left|right boundary. Inventory:
grid G-S1 (between View and Search), G-S2 (gap-guard), G-S3 (first child of
`.selection-bar-right`), G-S4 (between Review and the ⋯/Undo/Global tail);
duplicates D-S1 (between the Decided toggle and the tier gate), D-S2 (before
the tail).

1. G-S3 is DELETED at all widths — its boundary is already the elastic gap;
   it is what boxed the Review button.
2. G-S2 (gap-guard) and its whole `@container selbar (max-width: 800px)`
   block are DELETED — it existed only to patch G-S3's gap-dependence, and
   below 800 it drew a double rule 8px from G-S3.
3. G-S4 stays at every width (the canonical tail boundary, mirroring D-S2).
4. Mechanism for narrowing: a separator wears the fold class of the group it
   bounds. G-S1 gains `tb-fold-700` (when Export/Import/ComfyUI fold, a lone
   Search must join the lens run, not sit boxed). D-S1 gains `dq-fold-720`
   (also fixes: on an empty queue the headline is v-if'd away and D-S1
   rendered as the bar's LEADING rule). D-S2 stays at every width.
5. Testable invariants: no separator is the first or last visible child; no
   two separators are visibly adjacent; every visible separator has a visible
   control on each flank.

Full-width grid result:
`Sort Filter View │G-S1│ Search Export Import ComfyUI …gap… Review │G-S4│ Undo Settings Stats`.

**Tier label:** the tier gate button's `{{ tierLabel }}` span wrapped because
`.dq-btn` lacked nowrap and the shrink chain (`.dq-tb-left` → `.dq-tier-wrap`
→ button) had no `min-width` guards; Auto-stack's `.dq-auto-full` shared the
latent wrap.

1. Structural no-wrap at every width: `.dq-btn { white-space: nowrap;
   min-width: 0; }`; the label span (`.dq-tier-label`) gets `min-width: 0;
   overflow: hidden; text-overflow: ellipsis;`; `.dq-btn .v-icon
   { flex-shrink: 0; }`; `.dq-tier-wrap { min-width: 0; }`. One line with an
   ellipsis under pressure; the 36px band and the 27px button never move.
2. `.dq-tier-label { display: none; }` joins the EXISTING dqbar ≤720 block
   (no new breakpoint) — the compressed form is [filter icon][chevron],
   matching the grid Filter trigger's grammar.
3. A11y, mandatory: the tier button had NO title/aria-label — with the span
   hidden its accessible name would be empty (WCAG 4.1.2). It carries
   `:title="tierLabel"` and `:aria-label="tierLabel"` at all widths.
4. Siblings hardened: `.dq-auto-full { min-width: 0; overflow: hidden;
   text-overflow: ellipsis; }`; `.qtitle { flex-shrink: 0; }` (the count
   must never truncate).

## Amendment #2 (2026-07-30): burger placement

**Principle:** a burger may only collapse controls from its OWN visual group,
and it stands where those controls stood; a fold that would cross a group
boundary must instead stay, compress, or hide. The first ladder violated this
twice — the grid's ⋯ sat in the right tail while collapsing left-group
actions, and it swallowed Review/Settings/Stats/History from across the
boundary.

**Grid:**

1. The ⋯ moves to the END of `.selection-bar-left` (after the ComfyUI menu),
   where the controls it collapses stood. The panel keeps its right-aligned
   anchoring, so it opens leftward and stays on-screen.
2. Burger contents FINAL: Export/Import/ComfyUI rows (≤700) + "View
   options…" (≤600). The Review, Settings, Stats and History rows are
   DELETED; the `:attention` pass-through goes with them.
3. Review-and-fix-tags stays visible at ALL widths — it is the review
   overlay's only visible entry point.
4. Settings/Stats never fold: TbGlobalActions' ≤600 collapse rule is deleted
   and the activity dot stays first-class on the Stats button at every
   width.
5. History: the chevron still hides at ≤480 (UndoControl unchanged), but the
   burger's History row is deleted — below 480 the popover is simply
   unavailable. Documented, accepted loss; the undo/redo buttons and
   shortcuts remain. The dead `.tb-row-480` CSS and the grid's
   `@container toolbar (max-width: 480px)` block go with it.
6. Separator: G-S1 loses `tb-fold-700` — with the burger in the left run,
   the action run at ≤700 is [Search][⋯] (two members to the floor), so
   G-S1 renders at all widths. G-S4 unchanged.
7. Floor:
   `Sort(icons) Filter │G-S1│ Search ⋯ …gap… Review │G-S4│ Undo Settings Stats`.

**Duplicates — the burger DISSOLVES** (REVERSED by amendment #4, which
measured it: the own-group answers left a floor ~100px wider than the burger
does, and the bar never fitted): every foldable found an own-group
answer, so the bar mounts no overflow at all.

1. The TbOverflowMenu mount, all its rows, and the
   `.dq-overflow`/`.dq-row-*`/`.dq-size-row` CSS are deleted; the dq
   `@container toolbar 480` block goes; the 600 block keeps only the
   Auto-stack swap.
2. The Decided toggle compresses instead of folding: label span hides in the
   EXISTING dqbar ≤720 block (icon-only, the Auto-stack pattern), with
   `title`/`aria-label` carrying the name and `aria-pressed` kept.
3. The size slider hides outright at ≤720 (`dq-fold-720`) with no
   replacement row — the value persists in the store.
4. Separator: D-S1 loses `dq-fold-720` — with Decided visible at all widths
   its left flank is always populated, including on an empty queue; D-S1
   renders at all widths. D-S2 unchanged.

TbOverflowMenu itself sheds the attention prop, dot, keyframes and its ≤600
container rule (dead once Stats stopped folding). UndoControl unchanged.

## Amendment #3 (2026-07-30): the verdict key scheme

**The finding:** the owner kept pressing S intending Stack. That is a capture
slip, not ignorance — S's strongest reading IS Stack — and the old binding
punished it with the OPPOSITE verdict (Keep separate). The feature is
unreleased, so there is no habit base to protect; the vocabulary can be fixed
at the root instead of patched with warnings.

**Binding table (before → after):**

| Key | Before | After |
|---|---|---|
| Enter | Stack | Stack — UNCHANGED (primary-action convention, auto-advance rhythm) |
| S | Keep separate | **STACK** (synonym of Enter, queue + Compare). The slip becomes self-healing. |
| K | previous-group nav synonym | **KEEP SEPARATE** (queue + Compare) |
| J | next-group nav synonym | REMOVED, unclaimed |
| Arrows / PageUp/Down / Home / End / C / X / 1-9 / Z / P / Escape / Ctrl+Z / Ctrl+A | — | unchanged |

NO dead-key handling and NO de-training toast: pre-release, explicitly ruled
out.

**Surfaces:** the row's Stack chip stays "Enter" (one chip per button, the
primary key shown; S is taught in copy, not chrome) and the Keep separate
chip becomes "K"; `aria-keyshortcuts` carries the full machine-readable set
("Enter S" / "K") — the chips are aria-hidden, and nothing announced the
keys before, a shipped gap this closes. Compare's footer hint and its Keep
separate `key-hint` follow; the queue's hidden help sentence reads "Enter or
S stacks it. K keeps it separate. Down moves on without deciding."

## Amendment #4 (2026-08-17): the Duplicates bar actually fits (issue #1009)

**The finding, measured rather than argued.** The ladder above was written from
an inventory, never from a measurement, and the Duplicates bar has never fitted
its own container at any width. Rendering the shipped markup and the shipped
`<style>` blocks in Chromium and stepping the container width gives:

| bar width | bar overflow | Settings | Stats |
|---|---|---|---|
| 1400 | 170px | clipped | clipped |
| 860 | 504px | clipped | clipped |
| 720 | 149px | clipped | clipped |
| 600 | 92px | clipped | clipped |
| 480 | 180px | clipped | clipped |

Two causes, both structural:

1. **Nothing in the bar can shrink.** `.dq-tb-left` and `.dq-tb-right` are flex
   items at the default `min-width: auto` holding `white-space: nowrap`
   content, so both keep their intrinsic width whatever the container does.
   The surplus leaves through the right edge, and the last children in DOM
   order are the ones that walk off it: Settings, then Stats, then undo. The
   controls do not fold, they *vanish*, with no signifier that anything is
   missing (Nielsen #1, visibility of system status).
2. **The ladder's floor is wider than the widths it fires at.** After every
   shipped step the bar still measures ~690px at the 600px breakpoint. A
   ladder that ends above its own last rung has no floor at all. Amendment #2's
   "every foldable found an own-group answer" was reasoned, not measured: an
   icon-only Decided plus an icon-only Mixed stacks plus a full-width count
   headline is still ~100px wider than a burger holding both.

**Decision 1 — the shrink chain is the guarantee, the ladder is the design.**
`.dq-tb-left` gets `min-width: 0; flex: 0 1 auto` and its text members
ellipsize; `.dq-tb-right` gets `flex: 0 0 auto` and never shrinks. The
app-wide tail is then unreachable by any content the left group can hold: a
pathological scope name or a seven-digit count eats itself before it can push
Settings off the bar. The ladder still does the visible work; the chain only
catches what the ladder did not foresee. **The bar must not take
`overflow: hidden`** as a cheaper cure: the tier popover and the ⋯ panel are
absolutely positioned inside it and would be clipped away.

**Decision 2 — the burger comes back, for the left group's page toggles only.**
Amendment #2's principle stands (a burger collapses its own visual group and
stands where those controls stood); its conclusion does not. The ⋯ sits at the
end of the toggle run, after the count and before D-S1, and holds exactly the
Decided and Mixed stacks toggles, as labeled rows. This is narrower than the
icon-only pair AND more legible: `mdi-history` and `mdi-alert-outline` with no
label are mystery meat (Norman, signifiers), and an alert glyph on a bare bar
reads as an error rather than as a place to go. The panel opens from its left
edge in this bar (`align="start"`), because a right-anchored 220px panel hung
off a trigger this close to the left edge would open off-screen.

**A toggle that says "Back to review" never folds.** On the Decided and Mixed
pages the toggle is the visible way out of a sub-page (Nielsen #3, user control
and freedom); it stays on the bar and compresses to its `mdi-arrow-left`, which
is the one glyph in this set that needs no label. So the bar button and its
overflow row do not share a v-if verbatim here: one computed
(`pageTogglesFold`) says whether the toggles are the folding kind, and it gates
both the buttons' fold class and the mount of the ⋯ itself. The rows carry no
condition of their own — the menu they sit in only exists while they apply,
which is also what stops the ⋯ appearing with nothing in it on a sub-page.

**Decision 3 — the ladder, revised, and each rung placed where the measurement
puts it.** The old rungs were round numbers picked by eye. These are the widths
at which the previous form stops fitting, measured on the shipped CSS with the
worst content the bar can hold (a scope in force, exact matches to auto-stack,
mixed stacks waiting):

| rung | what gives | content after |
|---|---|---|
| — | everything on the bar | 1577px |
| dqbar ≤1400 | qsub, the size value label | 1372px |
| dqbar ≤1180 | the size control, Auto-stack's sentence → "Auto-stack N" | 1153px |
| dqbar ≤1040 | the page toggles → ⋯, the tier label | 807px |
| dqbar ≤820 | Auto-stack → flash + count, the scope pill → icon + dismiss | 630px |
| toolbar ≤480 | the undo chevron (shared, unchanged) | 598px |
| toolbar ≤420 | redo (shared); the two runs' gaps → `--space-2` | 526px |

Between two rungs the count headline takes the difference: it ellipsizes from
the right, so the number survives and "groups to review" is what goes. A
`flex-shrink: 6` on it buys that order, because a clipped tail on a sentence
reads better than "Mixed stac…" on a button.

Floor: `128 ⋯ │ [filter ▾] [scope ×] …gap… [⚡ 42] │ [undo] [Settings] [Stats]`.
The two numbers in the last row are not in conflict: 526px is what the bar
holds with the count at its full width, and the count is the elastic member, so
the bar goes on fitting below that by spending it. Measured with everything
above present and nothing clipped or overlapping down to **320px of bar
width**, where the count is down to its first digits — well under the shared
ladder's last rung of 420, and under any width this shell is usable at. The
bar's own 36px band and its insets are NOT this ladder's to spend: they are
pinned equal to the grid bar's by a guardrail in `Toolbar.test.js`. (A rule for
them here would be dead anyway — `.dq-toolbar` is the query's container, and a
container query styles descendants, never the container itself.)

**Duplicates ranking, revised:** 1 Undo (never folds or hides) / 2 Settings +
Stats (never fold; the whole point of this amendment) / 3 Tier gate (never
folds, compresses at 1040) / 4 Count (never folds; ellipsizes between rungs,
the number never truncates) / 5 Scope pill (never folds while scoped,
compresses at 820) / 6 Auto-stack (never folds, compresses at 1180 and 820) /
7 Redo + chevron (shared 420/480) / 8 Decided + Mixed stacks (fold to ⋯ rows at
1040, unless showing "Back to review") / 9 Size slider (hides at 1180) /
10 qsub (hides at 1400).

**Acceptance, and it is arithmetic rather than judgement.** At every container
width, with the worst content the bar can hold: the bar does not overflow its
own box (`scrollWidth === clientWidth`), no child of the left group paints past
the right group's left edge, and Settings, Stats and undo all sit inside the
bar.

**What checks it, and what does not.** This has to be measured in a browser:
jsdom evaluates neither container queries nor layout, so no unit test can see
this class of bug at all — and the unit suite passes unchanged with every rung
in this ladder inverted. It is kept for the DOM-level contracts only (each row
mirrors its button, the fold classes, every control's accessible name, the
tail's order, the ⋯ standing in its own group), and it should not be read as
cover for the widths. The browser check is a case in
`frontend/e2e/specs/dedup.spec.js`, which drives the real app from 1600 down to
800px of window and asserts the three invariants above at each step. Its
boundary, stated rather than implied: it stops at 800px because the shell's
sidebar does not respond to width (below that the question is the sidebar's,
not this bar's), and it runs on an unscoped queue, so the scope pill's own rung
is not among what CI exercises. The rows in the rung table were measured by
rendering the shipped `<style>` blocks against the bar's real markup in
Chromium at each width; reproducing that is the way to re-check a rung after
changing what the bar carries.

## (d) Deliberately rejected, and why

- **Left-side standardisation**: no shared anchor exists on the left (the grid
  leads with Sort, Duplicates with its count headline), and it mixes app
  chrome into view-local clusters.
- **Undo in the overflow**: violates Nielsen #3 (user control & freedom) and
  Fitts at exactly the moment of error — recovery must be one visible target.
- **Dropping toolbar undo on narrow widths** ("Ctrl+Z is enough"): fails
  touch, loses the "Changed elsewhere" warning, and kills shortcut
  discoverability.
- **JS priority-plus measurement** (ResizeObserver): jank and complexity when
  a CSS container-query convention already ships in this bar.
- **Teleported v-menu for the overflow**: teleport escapes the container, so
  the rows could not share the bar's container queries.
- **Moving Review-and-fix-tags left**: relocates a second learned control for
  a marginal taxonomy gain.
- **A separator-less tail**: proximity alone cannot separate identical 32px
  icon buttons; the boundary needs the rule.
