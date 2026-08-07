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
