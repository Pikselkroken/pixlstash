# Toolbar decisions: undo/redo placement and responsive collapse (2026-07-30)

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

**Model shelf toolbar** (`ModelShelf.vue`, `.shelf-toolbar`, 36px band since
Amendment #5, container `shelfbar toolbar`) — added 2026-08-15, after this
record was written:

- Left: title → count → Add (accent) → stack sweep → Model folders
- Right: Group → Sort split-button → Show → separator → TbGlobalActions
  (no UndoControl — see Amendment #4)

It was shipped without the tail at all, so both app-wide controls simply
vanished on `/models`; it then followed Decision 1 as written rather than
inventing a third arrangement.

`Model folders` rejoined the left group on 2026-08-16, having been dropped by
the #904 consolidation. **The shelf bar still has no ladder**: it declares
`container-name: shelfbar toolbar` and writes no `@container` rule, and there is
no `TbOverflowMenu` on it, so Decision 2 is unimplemented here and the bar now
carries seven focusable controls plus the tail with nothing to fold. Writing
that ladder is open work, not something this record already decided.

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

**Duplicates — the burger DISSOLVES:** every foldable found an own-group
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

## Amendment #4 (2026-08-16): the model shelf carries no undo

**The finding:** Decision 1 gave every toolbar the same tail, and the shelf bar
took it whole. But the shelf writes nothing to the operation log — adapters,
model folders, model stacks and model moves are not operations — so on
`/models` the pair sat permanently at "Nothing to undo", and the one time it
did not, the step it offered to revert was a library edit made on a screen the
reader had since left. Meanwhile the shelf's own destructive verbs say "There
is no undo for this" in as many words, two icons away from an undo button. An
affordance that never answers for what is in front of it teaches the wrong
thing about the actions beside it, and it is worse when the actions beside it
are the unrecoverable ones.

**The change:** `ModelShelf.vue` drops `UndoControl`; its tail is
`[separator] [TbGlobalActions]`. The separator stays — the rejected-options
list above still holds, the boundary between this view's controls and the
app's needs the rule. The shelf has no ⋯ overflow, so there is no "History…"
row to remove, and undo stays exactly where Decision 1 put it in the grid and
Duplicates bars, which do write the log. One knock-on for whoever writes the
shelf ladder that "Current state" above calls open work: the bar wrote no
`@container` rule of its own before this, and `UndoControl` was the one control
on it that wrote any against the shared `toolbar` name — so `shelfbar toolbar`
now has no consumer at all. The declaration stays for the next shared control
(and `container-type: inline-size` earns its place regardless), but nothing on
this bar responds to width today.

**And the chord goes with the button**, which is the part it would have been
easy to skip. `useGlobalKeydown` is route-agnostic, so `Ctrl+Z` on `/models`
kept calling `operationStore.undo()` — and the shelf mounts no `ActionReceipt`
either, while `UndoControl` is the app's ONLY renderer of the "Changed
elsewhere" warning. Removing the display and leaving the action reachable is
the one combination worse than either alternative: a library mutation with no
button, no state, no history, no receipt and no warning. The handler already
declines behind a modal scrim for exactly this reason ("every undo raises a
receipt"), so the shelf joins that guard on the same kind of DOM signal the
lightbox check uses. What is gone is not only the claim that this screen has
something to take back, but the silent way it had of taking it.

**And it declines out loud.** A chord that does nothing at all teaches nothing,
and the reader's next move is to press it again or go hunting for the buttons.
The notice surface is app-wide and already renders over the shelf, so the
answer costs no new chrome: one `info` notice, one sentence — "Model shelf
changes aren't undoable — undo and redo apply to library actions in the grid."
No action button; it would offer to navigate away mid-task. The push carries a
fixed coalescing key, so a repeated press updates one card rather than building
a wall (notice spec §9.1).

**Rejected: hiding the control but keeping it inert**, the read-only pattern
(`aria-disabled` + an explaining title, "show-but-disable, never hide"). That
rule exists so a demo can see a feature it may not use — the feature is real,
the session is not entitled to it. Here the feature does not exist on this
screen at all, and a permanently inert undo two icons from verbs that say
"There is no undo for this" teaches the opposite of what is true.

## Amendment #5 (2026-08-16): one band, one tail anchor (fine pointers)

**The finding:** Decision 1 unified what the three bars *hold*, and nothing
unified what they *are*. Switching to `/models` moved the whole content area
down 12px and back, because `.shelf-toolbar` shipped at `--bar-height` (48px)
while the grid and Duplicates bars are 36px. The strip was also unpainted, so
`.shelf`'s `background` showed through it — a different hue and value from the
`toolbar` token in both themes — which made the shelf bar read as page rather
than chrome. And its uniform `--space-5` inset put Settings and Stats 8px
further left than in the other two views, so the one pair of controls that
means the same thing everywhere jumped diagonally on every view switch. Three
independent differences, all of them read at once as "this is a different app",
which is exactly the reading a view switch must not produce.

**The change:** `.shelf-toolbar` takes the grid bar's box recipe whole —
`height: 36px` + `box-sizing: border-box` + zero vertical padding — paints
`rgb(var(--v-theme-toolbar))` with `rgb(var(--v-theme-toolbar-text))` ink, and
adopts the queue's split inset, `padding: 0 var(--space-3) 0 var(--space-5)`.
The right token is the one that matters and is now identical in all three:
the app-wide tail is a stable anchor only if it lands at the same distance from
the edge in every view. The left stays at the view's own content gutter, which
on the shelf is `--space-5` (`.shelf-group-btn` and the rows), the same
reasoning the queue's inset already carried. `.shelf-title` drops from
`--text-xl` to `--text-md`, the queue's `.qtitle`: 22px is a view heading, it
does not sit in a 36px band, and the two bars that lead with an identity now
lead with it at one size. `.shelf-sub` re-bases its ink on `toolbar-text` at
the queue's `.qsub` alpha (0.6, not the 0.7 it carried on `on-background`) — at
the old alpha the change would have invented a third strength for one role in a
record whose whole premise is that these bars agree.

`.shelf-viewseg` drops to `height: 30px` in the same pass. It is the only
control on any of the three bars wrapped in a bordered track, so at `.bar-btn`'s
32px the switch measured 34 against every neighbour's 32. The old 48px band hid
that as mere misalignment; a 36px band (35px of content box) also left it 0.5px
of slack, so a focused segment's 3px `--focus-ring` painted onto the first list
row. 30 + 2×1px border is 32, which is what every other control in every bar
already is.

**Not `--bar-height`.** 36px is the shipped majority and the one the grid bar
defines; migrating the whole app's 34/40/48/56 onto the 48px token is the
separate UI/UX-gated item in `visual-language.md` §5, and a bar that jumped
there alone would be drift in the other direction. What this amendment fixes is
the *disagreement between views*, not the token question.

**Kept different, deliberately:** the grid bar paints `rgba(…, 0.95)` rather
than `rgb(…)`. It is the only one of the three that is absolutely positioned
over scrolling content; the other two are flex children of their shells. The
guardrail accepts either form of the same token.

**The guardrail grew a third bar.** `Toolbar.test.js` now reads the CSS block of
all three selectors and asserts the shared recipe, the equal right inset, that
each declares exactly one `background` and that it paints `--v-theme-toolbar`,
and that `.shelf-title`/`.shelf-sub` carry the same size and ink as the queue's
`.qtitle`/`.qsub`. jsdom computes no layout, so the coupling is what gets
pinned, not the rendered pixels. The "exactly one" is the load-bearing part: an
earlier draft asserted only that *a* `background` matched the token, which an
adversarial pass showed still passed with `background: transparent` appended on
the next line — that is, on a reproduction of the very defect being fixed.

### Still open after this amendment

Two things this record should not be read as having settled.

**Coarse pointers are NOT unified, and the gap widened.** `Toolbar.vue` carries
a `@media (hover: none) and (pointer: coarse)` block taking `.selection-bar-
overlay` to 56px with `--space-2` insets and its controls to 46px targets.
Neither the queue nor the shelf has an equivalent, so on touch the three bars
measure 56 / 36 / 36px with 4 / 8 / 8px right insets — every claim above holds
for fine pointers only. The shelf was already wrong here (48px band, 32px
targets: the coarse `.bar-btn { min-height: 46px }` rule is inside
`Toolbar.vue`'s `<style scoped>` and so has never reached any bar but the
grid's, the exact trap `App.css` documents at the `.bar-*` family), and 32px
targets are under the 44px WCAG 2.5.5 floor on all three. The fix is the one
`App.css` already made for the rest of the family — lift the coarse `.bar-*`
rules out of the scoped block, then give the other two bars the band — but it
moves pixels on touch hardware nobody has verified this on, so it is its own
change with its own device pass, not a rider on this one. **The guardrail
cannot see it**: `blockOf` takes the first matching block, so a media-query
override is structurally invisible to it.

**`.bar-btn--open` is a weak affordance and this made it marginally weaker on
the shelf.** The open trigger paints `panel` fill inside a `border` outline;
against `toolbar` that measures 1.01:1 (light) and 1.17:1 (dark) for the fill,
1.28:1 for the outline — under the 3:1 non-text floor (WCAG 1.4.11). The shelf
previously sat on `background`, where the same pairings measure 1.05 / 1.30 /
1.42:1: still failing, slightly less so. This is not a shelf problem — it is how
the open state reads on the grid and queue bars already, and the shelf has now
converged onto it rather than away. Fixing `.bar-btn--open` app-wide (an accent
border, the treatment `.bar-btn--icon.bar-btn--open` already uses) is a
lead-designer item across all three bars.
