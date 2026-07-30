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
