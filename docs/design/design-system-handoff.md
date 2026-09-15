# PixlStash Design System — open decisions and carried findings

What is still **open** in the visual system: decisions that need a UI/UX call, and
findings that were measured and decided but not yet implemented. Nothing here is
settled-and-shipped; that lives in `visual-language.md` (the spec) and
`design-tokens.css` (the values).

This file used to carry a full paste-able mirror of the token vocabulary, both color
themes and the component patterns, for designing in Claude Design without the repo
open. That mirror went stale (it never picked up `6e14c32c`'s deepened status hues) and
is deleted: paste `visual-language.md` + `design-tokens.css` + the themes in
`frontend/src/main.js` into such a session instead, and they cannot drift.

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

## 9. Carried findings — measured, decided, implemented

These were decisions, not questions, recorded with their numbers so the implementation
lane did not have to re-measure anything. Each item now carries a **Status** line saying
where it landed; the record under it is kept for why. Grouped by how expensive they are
to reverse.

### 9.1 The action-fill change set (one commit; every value is in `visual-language.md` §4)

> **Status: done.** The table landed in `4cc29048` (2026-07-23). Its hex values were
> then superseded by the unified amber palette (`3dd7287b`, 2026-07-24), which moved
> `primary` / `secondary` / `tertiary` to warm-white `#f7f1ea` labels and `accent` to
> `#c47a1e` under pure white; read `frontend/src/main.js` for the live values, not the
> table. The focus and wash rows were superseded again on 2026-09-13 (ink focus ring,
> ink hover wash; `visual-language.md` §11). `dark-surface-primary` and the two tally
> spans are live as specified. The foreground sweep below is done too (#1300): the
> small-text sites were moved onto their surface's ink.

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

**Sweep, as done (#1300).** Against the live palette the failure is two-sided: `accent`
is 2.93 – 3.41:1 as a foreground on every light chrome surface, and `primary` /
`secondary` / `tertiary` are 2.30 – 3.04:1 on every dark one, so any small text in one
of the four fails in one theme or the other. The pass took every rule that sets one of
the four as `color` on text at `--text-sm` (13px) or below, whether the size is declared
in that rule or on the base class a modifier extends, and moved the words onto the ink
of the surface they sit on: `.titlebar-update-link`
(`on-background`), `.sidebar-update-available` (`sidebar-text`), `.editor-copy-status`,
`.editor-sync-detected`, `.relocate-result`, `.account-success`, `.pp-more` (which now
inherits its row's `on-surface`), `.pf-url-meta` (`on-surface` at
`--opacity-text-secondary`), `.layout-tree__badge` (words to `on-surface`, the olive
stays on its border), and `.rs-xp-points` / `.rs-xp-streak` (`on-dark-surface`; tertiary
measures 2.79:1 on `dark-surface`), `.rs-bin-new` / `.rs-pair-new` (`on-dark-surface`,
the amber stays in the wash), `.kind-pill--DAILY` / `--MANUAL` / `--WEEKLY` (words to
`on-surface`, the kind's hue moves into the wash through `--kind-hue`),
`.layout-tree__delta--in` / `--out`, `.smart-score-status--success`, and
`.sidebar-move-menu-group-header--current` (`sidebar-text`, set apart from its
siblings by full opacity; now `.ctx-label--current` on the shared menu's heading,
which takes `--selected-ink` rather than full opacity). A success message is therefore ink in every surface that had
one in amber; the words say "saved", and status colour was never meant to be the only
cue. Two sites were found and left: `.star-number-label` sits
over a photo or the dark lightbox, not a canvas (amber 4.45 – 5.06:1 there), and
`.titlebar-bc-crumb.is-link`, whose olive is the only thing marking a crumb as
clickable. The two update links had the same property and were re-pointed anyway,
leaning on their hover underline and the dismiss control beside them; if they read as
lost in situ, the fix is an underline at rest, not the amber back.

**Reversal cost.** Cheap and total: every row above is a one-line value swap with no
structural dependency. The one irreversible-ish part is perceptual, not technical — the
dark accent drops 21 points of HSL lightness and people will notice. If it reads muddy in
situ, the lever is the invariant itself (restore `#f28f3b` and put `on-accent` back to
`#1b1b1b`), not a compromise value: there is no fill that is both brighter and legal
under a white label.

### 9.2 `on-<x>` used on a surface that is not `<x>` — the recurring trap, four more sites

> **Status: done (#1300), and guarded.** The three live rows below had already gone:
> the project menu was rebuilt without `on-tertiary`, the media-type toggle was deleted,
> and SideBar's CSS has since moved to `SideBar.css`, so the line numbers are dead. A
> sweep of every `on-accent` / `on-primary` / `on-secondary` / `on-tertiary` use found
> the same bug elsewhere, all fixed: `.sidebar-inline-notice` (the old `8522` row, moved;
> `rgba(secondary, .75)` → solid, 3.35 → 4.91:1 over the light sidebar), `.sidebar-new-tag`
> (`rgba(primary, .7)` → solid, 2.86 → 4.86:1 light), `.overlay-close` and
> `.overlay-comfy-run` (`.7` / `.8` → solid), the move-to-project menu
> (`on-tertiary` on a 38% teal mix, 1.72:1 light → `sidebar-text`, 8.15:1), the chart
> counts in `StatsHistogram` and `StatsSidebar` (`on-primary` at .85 on a 50 – 85%
> fill, under 2:1 light at rest → `on-surface`), and `.tbm-toggle-end`, which set `on-primary`
> with no fill at all and now inherits it from `.tbm-toggle--on`, the only state it
> renders in (since removed with the sort dot it drew, when sort moved to `OptionRows`, #1296). One residual: the **active** histogram bar (fill `.85` over the light
> sidebar) gives its count 3.87:1 in ink, against 3.14:1 before; neither ink nor white
> reaches 4:1 on that fill, so closing it means lowering the active fill, a visual call.
> `frontend/src/styles/on-fill-pairing.test.js` now fails the build on any rule that
> pairs one of those four `on-*` colours with an `rgba(...)` or `color-mix(...)`
> background. It is deliberately that one shape: an `on-*` with no background in its
> rule, an `on-*` made translucent itself, and an `on-*` in a child rule under a
> see-through parent (the toggle, chart and move-menu shapes above) are all legal
> somewhere in the tree, so they stay review catches rather than a noisy allowlist.

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

### 9.3 Carried from earlier passes

> **Status (#1300).** The `error`-on-`dark-surface` migration is **done** (`4cc29048`:
> 49 declarations moved; the two `v-theme-error` uses left in the lightbox are fills,
> not foregrounds, which is correct). `SelectionBar`'s `6px` is **gone**: the bar was merged
> into the grid action pill (`35c9f519`) and the old rule with it; the action-bar height
> reconciliation it was waiting on is still the §8 decision. The z-index retrofit
> **stays opportunistic by decision** (`visual-language.md` §14): 91 raw numeric values remain,
> and moving them wholesale is exactly the unreviewable stacking change §14 rules out.

- **~40 `error`-on-`dark-surface` declarations** in the review overlay measure 3.12:1 and
  want `dark-surface-error` (4.12:1). Pre-existing, mechanical, large enough to want its
  own eyeball pass.
- **`SelectionBar`'s `6px` block padding** stays off-grid until the action-bar height
  reconciliation (34 / 36 / 40 / 48 / 56px → `--bar-height`), which is UI/UX-gated
  because it moves pixels on the app's most-used control. The three **view toolbars**
  are out of that list: they were reconciled with each other at 36px rather than onto
  the token (`toolbar-responsive-decisions.md` Amendment #5).
- **The 40+ raw z-index call sites.** The ladder is shipped; retrofitting is
  opportunistic (touch a rule, move it onto the ladder). The ladder's own values and the
  migration of the remaining squatters are owned by the concurrent layering lane — read
  `frontend/src/styles/design-tokens.css` for the current rungs rather than any copy of
  them here.
### 9.4 There are two focus languages, and the second one is not documented anywhere

> **Status: done (`82a8f22c`, 2026-08-06, and `3b8b1035`, 2026-09-13), by a different route.** `--focus-ring` is
> gone rather than adopted. Focus is now one global ink outline (2px `--focus-stroke`
> with a 2px gap) drawn from `style.css` and documented in `visual-language.md` §11.
> The `focus` theme key is retired and nothing reads `rgb(var(--v-theme-focus))` any
> more (verified for #1300). The record below is kept for why.

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
