# UI/UX Spec: Tag Health Board — Freeze Eligibility & Accuracy Column Legibility

Follow-up fixes after hands-on testing surfaced real usability problems in the shipped tag
review takeover feature. Companion to `docs/reviews/tag-review-tagger-takeover-design.md` and
`docs/reviews/tag-review-accuracy-freeze-conflicts-ux-spec.md`.

## 1. User problem

1. **Freeze eligibility is invisible until failure.** `POST /tag_eval_slices` requires a tag to
   have `n_pos >= MIN_EVAL_N_POS` (10) verified-positive, EVAL-split human labels. Every
   never-frozen tag renders the identical "Freeze to score" button regardless of whether it has 0
   or 500 eligible examples — the user only learns the real count via a 5-second auto-clearing
   tooltip, after clicking and failing.
2. **Two unrelated numbering systems sit in adjacent, visually similar cells.** The row's numeric
   cells left of Accuracy (ranking score, `est_wrong`, `est_missing`, `mismatch`, `verified_pct`)
   are all live counts over current unscored data. The Accuracy cell computes from a frozen,
   point-in-time `TagEvalSlice`. The only distinguishing signals today are an icon-only header
   (no text) and one sentence buried at the bottom of the table.
3. **`healthLoading` has zero visual representation** during a board-scope refetch — stale rows
   sit unchanged on screen with no "this is refreshing" cue.

## 2. Spec A — Freeze-eligibility row treatment

Backend now exposes `eval_candidate_n_pos: Optional[int]` on every `TagHealth` row (via
`count_eval_slice_candidates_in_session`, sharing the exact candidate-selection query the freeze
action itself uses — so this number can't silently diverge from what a real freeze would do).

Split today's single `unfrozen` state into two, in `acc(r)`:

```js
const FREEZE_MIN_N_POS = 10; // define once; replaces the inlined literal in freezeErrorText()
if (!r.eval_slice_frozen_at || !r.eval_metric_kind) {
  const nPos = r.eval_candidate_n_pos ?? 0;
  return nPos >= FREEZE_MIN_N_POS
    ? { state: "unfrozen_ready", nPos }
    : { state: "unfrozen_pending", nPos };
}
```

- **`unfrozen_ready`** (`nPos >= 10`): keep the existing `rs-acc-freeze-link` button, strengthen
  its tooltip: `` `Not scored yet — ${nPos} confirmed examples ready. Freeze to start tracking
  this tag's accuracy.` ``. No visible count next to the button — its presence vs. the pill *is*
  the signal.
- **`unfrozen_pending`** (`nPos < 10`): **replace the button** with a non-interactive `{n}/10`
  pill (`rs-acc-pill rs-acc-pill--pending`), mirroring the existing post-freeze `insufficient`
  state's pill treatment (its pre-freeze sibling). Do NOT just disable the button — a click below
  the floor is a deterministic, client-computable failure, so don't show a live control that's
  guaranteed to fail.
  ```html
  <span
    v-if="acc(r).state === 'unfrozen_pending'"
    class="rs-acc-pill rs-acc-pill--pending"
    tabindex="0"
    :aria-label="pendingAriaLabel(acc(r).nPos)"
    :title="pendingTip(acc(r).nPos)"
  >{{ acc(r).nPos }}/{{ FREEZE_MIN_N_POS }}</span>
  ```
  ```js
  function pendingTip(nPos) {
    const remaining = FREEZE_MIN_N_POS - nPos;
    return `${nPos}/${FREEZE_MIN_N_POS} confirmed examples — freezing needs at least ${FREEZE_MIN_N_POS} verified-positive labels for this tag. Review ${remaining} more to unlock scoring.`;
  }
  function pendingAriaLabel(nPos) {
    return `${nPos} of ${FREEZE_MIN_N_POS} confirmed examples needed to freeze this tag's accuracy score`;
  }
  ```
  `tabindex="0"` + `aria-label` deliberately added — this pill is now the primary answer to "why
  can't I freeze this," must be keyboard-discoverable, not hover-only.
- Do **not** add a mini progress bar next to the pill — would visually rhyme with the ranking-score
  bar and undercut Spec B's goal of making Accuracy read as a separate system. Bare text is enough.
- Both places `acc(r).state` is switched on (the rank-sunk branch and the main branch) need this
  update — consider extracting the eligibility check into one shared computed so they can't drift.
- Keep `freezeError`/`freezeErrorText`/the 5s auto-clearing tooltip exactly as-is — it becomes a
  rare defensive fallback (e.g. a race), not the primary signal. Do not remove it.

## 3. Spec B — Accuracy column legibility

**(a) Header text.** Replace icon-only with a text label, matching this table's dominant
header idiom (Tag/Est. fixes/Est. wrong/Est. missing/Mismatch are all plain text — icon-only is
the exception for genuinely tight columns, not the rule):

```js
{ label: "Accuracy", center: true, dividerBefore: true,
  tip: "Accuracy — how good the model is at this tag, measured on a separate frozen, scored slice. Not a count of pictures needing review — that's Est. wrong / Est. missing / Ranking score to the left, which update live. This number only changes when the tag is (re)frozen." }
```

**(b) In-row visual distinction: a left border divider, not a background tint.** Reuse an alpha
already used in this file, not a new design value:

```css
.rs-board-acc { border-left: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14); padding-left: var(--space-3); }
.rs-board-hdr--divider { border-left: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14); padding-left: var(--space-3); }
```
Apply `rs-board-hdr--divider` via `h.dividerBefore` in the header `v-for`. Rejected a background
tint: this app's existing vocabulary already uses cell tinting to mean "flagged/problem"
(`.rs-board-anomaly-toggle--on`, `.rs-board-tag--anomaly`), so tinting Accuracy would misread as
an issue rather than "different kind of number." A quiet vertical rule states a boundary without
implying severity.

**(c) Legend:** keep `rs-board-legend`'s existing Accuracy sentence as-is (every other column
also duplicates its tooltip there — removing Accuracy's would make it the inconsistent one). The
actual fix for in-row confusion is the strengthened header tooltip in (a), which now names the
row's other columns by label explicitly.

## 4. Spec C — Loading indicator

Extend the board's existing `rs-board-building` banner (already used for cache-rebuild-in-progress)
to also show when `store.healthLoading` is true: swap its label to "Updating for this scope…" and
its bar from the determinate `healthProgress`-driven fill to an **indeterminate** sliding fill —
port the `@keyframes progress-overlay-indeterminate` / `.fill--indeterminate` technique already
defined in `frontend/src/components/widgets/ProgressOverlay.vue` (a scope refetch has no
processed/total to report). Keep stale rows visible underneath, undimmed — same as the existing
rebuild-banner behavior. Do not reach for `ProgressOverlay` itself (its `position: absolute` +
backdrop-blur contract doesn't fit inline table flow) and do not build a grid-style skeleton
(that pattern is scoped to the image grid, not a dense data table).

## 5. Acceptance criteria

- `candidateNPos < 10`, never frozen → `{n}/10` pill, no clickable freeze control, in both normal
  and rank-sunk row variants.
- `candidateNPos >= 10`, never frozen → freeze button, tooltip states the confirmed count.
- Pill is not clickable; tabbing to it announces count + explanation via `aria-label`.
- Freeze-error tooltip (existing) still fires on a genuine race — demoted to fallback, not deleted.
- Accuracy header renders visible text "Accuracy" (verify via accessible name, not just visually).
- Visible left border separates Accuracy from "Last" in header and every data row, including
  `--nomodel`/opacity-faded rows.
- Header tooltip names the row's other columns by label explicitly.
- `store.healthLoading = true` renders a visible banner (distinct copy from rebuild banner),
  indeterminate bar, existing rows stay on screen.

## 6. Open items for the implementer

- Backend field is confirmed as `eval_candidate_n_pos: Optional[int]` on `TagHealth`/`GET
  /tag_health` rows (already shipped, tested).
- Exact character width of "Accuracy" at the column's font size should get a quick visual check
  at implementation time (expected to fit, not pixel-measured in this spec).
