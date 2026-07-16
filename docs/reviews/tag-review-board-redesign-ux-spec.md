# UI/UX Spec: Tag Health Board Rescue — Split Automation, Rebuild, Honest Ranking

Follow-up to a hands-on test that found the board's headline promises broken: "Reviewing
tags does NOTHING to change the Accuracy… it is completely impossible to know what to do
about it and why you can't freeze the set" and "Verified is always at 0% and the other
columns also seem utterly static." Two investigations (`senior-backend-developer` root
cause, `machine-learning-expert` statistical review) confirmed systemic causes, both
summarized in full in the brief and re-verified against `pixlstash/services/tag_health_service.py`,
`pixlstash/services/picture_split_service.py`, `pixlstash/services/tag_scan_service.py`,
`pixlstash/services/tag_eval_slice_service.py`, `pixlstash/routes/tag_health.py`,
`pixlstash/routes/picture_splits.py`, `frontend/src/stores/useReviewSessionsStore.js`,
`frontend/src/components/reviews/TagHealthBoard.vue`, `ReviewRail.vue`,
`ReviewSessionView.vue`, and `frontend/src/stores/useTasksStore.js` for this pass. Companion
to `docs/reviews/tag-review-tagger-takeover-design.md`, `-plan.md`, and the two prior
`tag-review-*-ux-spec.md` docs — this spec follows their section/acceptance-criteria format.

Design-only: no code in this document is a diff, it is the spec an implementer builds
from. Every change below is tagged **[FRONTEND]**, **[BACKEND]**, or
**[FRONTEND, gated on BACKEND]** so the two implementation lanes can split cleanly.

## 1. User problem

**Task:** a curator uses the board to decide which tag to review next, reviews it, and
expects the numbers that justified that choice to visibly respond — both the ranking
signal that put the tag at the top, and the accuracy score that tells them whether the
model actually got better.

**Evidence, not hypothesis** (reproduced against the running server, cited in the brief):
a full review pass on a tag left `eval_candidate_n_pos` at 0 until `POST
/picture_splits/assign` was called by hand — an endpoint with zero callers anywhere in the
app. Separately, `TagHealthBoard.vue`'s only rebuild trigger is gated behind
`v-else-if="!store.healthRows.length"` (line 128 in the pre-change file), so it disappears
the moment the board has ever had one row, permanently. Both are root causes, not symptoms:
the first makes the accuracy half of the board structurally frozen; the second makes the
ranking half look frozen even though the underlying ledger *is* updating live.

## 2. Priority findings (ranked by how directly each explains the reported breakage)

1. **Split assignment has no trigger anywhere** → freeze eligibility (`eval_candidate_n_pos`)
   can never move, however much a tag is reviewed. Root cause of "can't freeze."
2. **The only rebuild control is a one-time-only affordance** → the board's counts look
   static even when the underlying data changed. Root cause of "Verified is always 0% /
   nothing moves."
3. **"Est. fixes" and "Start review" run different computations with no shared contract** →
   the board's headline number cannot predict what a review session contains. This is a
   calibration-of-expectations problem, not a bug in either pipeline.
4. **The freeze-eligibility copy misstates the real cost** — "review 5 more" reads as "5
   more reviews," but only ~1 in 5 reviewed pictures lands on the EVAL side that counts.
5. **A shipped column (`Why it ranks here`) is permanently blank** on every row — actively
   worse than not having the column, because a promised-but-empty explanation erodes trust
   in the columns that *do* work.
6. **`verified_pct` duplicates a stronger signal already on the row**; `boundary_pct` /
   `overturn_rate` are computed and never surfaced.

## 3. Spec A — Split assignment: fully automatic, zero bespoke UI

**Decision: make it invisible background maintenance, matching every other derived-state
task in this app (quality score, embeddings, likeness) — with one narrow, temporary
exception in the freeze-pill copy (Spec D).**

`frontend/src/stores/useTasksStore.js` and `docs/frontend_architecture.md` §4.4 confirm the
precedent: background workers need **no bespoke frontend code** to surface progress — they
appear automatically in `GET /workers/progress` → `useTasksStore.activeEntries` → the Tasks
tab + the Toolbar's pulsing activity dot, generically, by task type. `PictureSplit`
assignment should be exactly this: a fact the app maintains about itself, never a concept
the user is asked to hold in their head. This matches how `docs/backend_architecture.md` §7
already runs `REFERENCE_FOLDER_SCAN` — a periodic bulk finder, not a per-picture
`NULL`-column backfill (`assign_splits_in_session` is naturally idempotent: pictures that
already have a `PictureSplit` row are excluded from `target_ids` and the call is a fast
no-op when nothing changed).

**[BACKEND]**
- Register a new periodic task (e.g. `PICTURE_SPLIT_ASSIGNMENT`, CPU queue) whose finder
  does a cheap existence check (`SELECT 1 FROM picture WHERE NOT deleted AND id NOT IN
  (SELECT picture_id FROM picture_split) LIMIT 1`) and, if any exist, calls
  `picture_split_service.assign_splits(vault)`. Same `WorkPlanner` adaptive-backoff shape as
  every other finder in the table in §7 of `docs/backend_architecture.md`.
- Order it after `IMAGE_EMBEDDING` / `LIKENESS` in whatever dependency mechanism
  `TAG_PREDICTION_BACKFILL` already uses (it "depends on FACE_EXTRACTION + TAGGER") — not a
  hard requirement (union-find handles missing embeddings as singleton components; the
  write-path guard in `check_split_conflicts_for_new_edges` catches any resulting
  same-split-mismatch later), but running after likeness data has mostly landed minimizes
  churn and future conflicts.
- No new route, no new frontend polling loop, no new indicator component. The existing
  generic Tasks-tab pipeline is the entire UI for this.

**[FRONTEND]** none. Confirm during implementation that the new task type doesn't need an
allowlist entry anywhere the way `services/` DB-access guardrail files do (§10.1) — it's a
task, not a service, so it shouldn't.

**Why not a visible "setting up…" banner:** the app already has a strict precedent (§4.4:
"the only always-on background poll") for keeping this kind of maintenance silent. Adding
bespoke chrome for one specific background task, when eleven others (§7's table) get none,
would be inconsistent and would train users to expect a banner for every future
maintenance task. The one place this *does* leak into UI copy is Spec D, because it's the
one place a stale split assignment produces an outright misleading number, not just a
slower-than-expected one.

## 4. Spec B — Board rebuild: persistent control now, auto-rebuild-when-stale next

**Decision: ship the persistent manual control immediately (frontend-only, no backend
dependency); land backend staleness detection and auto-rebuild as the closing piece so the
manual button becomes an escape hatch, not the only path — mirroring Spec A's shape.**

Reuse, don't reinvent: `pixlstash/services/review_service.py` already has this exact idiom
for review sessions — `_latest_vault_change()` (max of latest picture creation, latest
`TaggerRun`) compared against an anchor (`review.refreshed_at or review.created_at`) via
`_is_stale()`, surfaced in `ReviewSessionView.vue` as a `mdi-clock-alert-outline` chip
+ inline "Refresh" button (lines 15–26). Apply the same shape to the board cache instead of
inventing a new one.

**[FRONTEND] — ships now, no backend change required:**
1. Move the rebuild control out of the `v-else-if="!store.healthRows.length"` empty-state
   branch and into the board header (`.rs-board-heading`/`.rs-board-controls` row), always
   rendered, not conditional on row count.
   ```html
   <button
     class="rs-board-rebuild-persistent"
     type="button"
     :disabled="store.healthBuilding"
     :title="rebuildTitle"
     @click="store.rebuildHealth()"
   >
     <v-icon size="14" :class="{ 'mdi-spin': store.healthBuilding }">mdi-refresh</v-icon>
     {{ store.healthComputedAt ? `Updated ${relativeComputedAt}` : "Never built" }}
   </button>
   ```
   ```js
   const relativeComputedAt = computed(() => shortRelative(store.healthComputedAt));
   const rebuildTitle = computed(() =>
     store.healthBuilding
       ? "Rebuilding…"
       : "Recompute tag health signals from the current data",
   );
   ```
   `shortRelative` can reuse whatever relative-time helper `utils/utils.js` already exports
   for dates elsewhere in the app (check before adding a new one — `formatUserDate` handles
   absolute dates only, so a small `"3m ago"`/`"2h ago"` formatter may be new; keep it a pure
   function in `utils/utils.js`, not inline in the component, per that module's existing
   contract).
2. Keep the existing empty-state's "Build now" button as-is for the true first-ever-build
   case (clearer call-to-action copy is appropriate there; the persistent header control
   uses quieter, ambient copy since it's visible at all times).

**[BACKEND] — closes the loop, lands after or alongside the frontend piece:**
1. Add a `_latest_health_relevant_change()` helper in `tag_health_service.py`, same shape as
   `review_service._latest_vault_change()` but covering the broader signal set that actually
   invalidates board rows: latest `Picture.created_at`, latest `TaggerRun.created_at`
   (both already used by the review one), **plus** latest `TagSuggestion.reviewed_at`
   (every accept/dismiss/swap changes `est_wrong`/`est_missing`/`mismatch`/`overturn_rate`
   for its tag) and, if timestamped, the newest `Tag` row creation/deletion. The exact
   complete signal set is a backend design call — flagged as an open item below, not guessed
   here.
2. Add `stale: bool` to `TagHealthResponse` (top-level — the cache is vault-wide and one
   rebuild covers every row, so this is not a per-row field), `stale = latest_change >
   computed_at` when both exist, else `False`.
3. Register a second periodic finder (same shape as Spec A's), gated on `stale and not
   building`, calling `tag_health_service.start_rebuild(vault)`. Debounce so a burst of
   review decisions doesn't retrigger a rebuild every few seconds — a fixed minimum interval
   between auto-rebuilds (e.g. every few minutes while stale) is enough; exact cadence is a
   backend tuning call.

**[FRONTEND, gated on BACKEND]** once `stale` ships: tint the persistent control with the
`warning` token and swap its icon to `mdi-clock-alert-outline` (same icon already used for
review-session staleness — visual consistency across the two features) when `store.healthStale`
is true, tooltip: *"Tag health hasn't been recomputed since new activity — rebuild now, or
it'll catch up automatically shortly."* Add `healthStale` to the store, populated from the
new response field in `fetchHealth()`.

## 5. Spec C — "Est. fixes": honest relabeling, not reconciliation

**Decision: (b), relabel. Reconciliation is rejected, not deferred.**

The two pipelines exist as different tools by explicit original design, documented in
`tag_health_service.py`'s own module docstring: "indexed SQL… no embeddings, no kNN, never
a live O(N²) sweep… the expensive near-neighbour scan stays reserved for review creation."
Forcing them to share a candidate query means either making the board's landing view
expensive (it exists specifically to be cheap enough to compute vault-wide on every rebuild)
or making review creation a confidence-threshold count instead of the twin-corroborated kNN
scan that gives it materially better precision. Both trade away the reason the two paths
exist. More importantly, the user's actual complaint isn't "the two numbers should match" —
a kNN vote count and a confidence-threshold count are different statistics even in
principle, matching N would not mean "the same N pictures" — the complaint is "I had no way
to know they wouldn't." That is a labeling problem, fully solvable without touching either
pipeline.

**[FRONTEND] — copy and one column rename, no backend change:**

1. Rename the header from **"Est. fixes"** to **"Priority"**. Keep the same field
   (`corrections(r)`), same heat bar, same default sort key (`score`) — only the label and
   its framing change.
   ```js
   {
     label: "Priority",
     key: "score",
     tip: "A fast ranking estimate (est. wrong + est. missing + mismatches), used to sort tags by how worth reviewing they look. Not a forecast of how many cards a review session will contain — Start review runs a separate, slower scan (nearest-neighbour comparison) that usually finds a smaller, different set of pictures.",
   }
   ```
2. Rewrite the `score` sort's subtitle:
   `"Sorted by how worth reviewing each tag looks — a fast estimate, not a review-session size."`
3. Rewrite the legend line (drop the "Est. fixes" phrasing entirely):
   `"Priority" = a fast ranking estimate (est. wrong + est. missing + mismatches) for sorting tags — not the number of cards a review session will contain, which comes from a separate, slower scan.`
4. **[FRONTEND]** In `ReviewSessionView.vue`'s existing `emptyScan` state (lines 61–87,
   already reads "Scanned N pictures · M handled in earlier reviews"), add one clarifying
   sentence so a 0-found result right after a high-Priority tag doesn't read as broken:
   ```html
   <p class="rs-state-sub">
     Scanned {{ scanned.toLocaleString() }} pictures ·
     {{ session.stats?.prev_reviewed ?? 0 }} handled in earlier reviews.
   </p>
   <p class="rs-state-sub rs-state-sub--muted">
     The board's Priority number is a fast estimate — the review scan is more selective, so
     finding fewer (or none) here doesn't mean that number was wrong.
   </p>
   ```
   Anchor only the `emptyScan` state, not every session open — a small nonzero `found` count
   is self-evidently "some cards did appear" and doesn't need the same reassurance; zero is
   the one state that reads as an error without it.

Reconciliation ((a)) is explicitly out of scope for this pass; if a future pass wants a
cheap preview of expected session size before clicking "Start review," that is a new,
separately-scoped backend feature (e.g. a fast upper-bound heuristic), not a retrofit onto
either existing pipeline.

## 6. Spec D — The 50-not-10 expectation: fix the freeze-pill copy, not just add a note

**Decision: rewrite the existing `unfrozen_pending` pill's tooltip (the UI's own documented
"primary answer to why can't I freeze this," per `tag-review-board-legibility-ux-spec.md`
§2) rather than bolt on a separate explainer — this is the surface the confused user already
reaches for.**

`eval_candidate_n_pos` only counts EVAL-side verified positives
(`tag_eval_slice_service.count_eval_slice_candidates_in_session` joins on
`PictureSplit.split == SplitValue.EVAL.value`), and `picture_split_service.py`'s
`TRAIN_RATIO = 0.8` means roughly 1 in 5 reviewed pictures lands there. The current copy —
*"Review {remaining} more to unlock scoring"* — reads as "N more reviews," which is off by
roughly 5×. This is exactly finding 4's predicted failure mode ("I reviewed 20, why does it
still say 4/10").

**[FRONTEND] — no backend change; uses fields already shipped:**
```js
function pendingTip(nPos) {
  const remaining = FREEZE_MIN_N_POS - nPos;
  return `${nPos}/${FREEZE_MIN_N_POS} confirmed EVAL-side examples — freezing needs at least ${FREEZE_MIN_N_POS}. PixlStash reserves most reviewed pictures for training and only keeps a fifth for scoring, so this climbs slower than your review count — reviewing more of this tag is still the way to unlock it, just not 1-for-1. Review ${remaining} more EVAL-side examples (roughly ${remaining * 5} reviews of this tag) to unlock scoring.`;
}
function pendingAriaLabel(nPos) {
  return `${nPos} of ${FREEZE_MIN_N_POS} EVAL-side confirmed examples needed to freeze this tag's accuracy score. Only about one in five reviewed pictures counts toward this number.`;
}
```
The `remaining * 5` figure is a copy-level approximation of `1 / (1 - TRAIN_RATIO)`, not a
computed guarantee — flag this in code as a comment tied to `TRAIN_RATIO = 0.8` in
`picture_split_service.py` so it doesn't silently drift if that ratio ever changes. If a
backend implementer wants a fully robust version, the durable fix is exposing the ratio (or
a directly-computed "reviews needed" estimate) via the API instead of hardcoding 5× in the
frontend — noted as an open item, not required for this pass.

**[FRONTEND]** Extend the board's closing legend paragraph similarly (currently: `"Accuracy"
= how good the model is on a frozen, scored slice, once you've frozen one.`) to:
`"Accuracy" = how good the model is on a frozen, scored slice (only about 1 in 5 reviewed
pictures counts toward it — most are reserved for training), once you've frozen one.`

Both changes are pure copy edits to functions/templates that already exist — zero new state,
zero new API surface, ships independently of Spec A/B.

## 7. Spec E — Column cuts: drop Verified, give overturn_rate a home via a real "Why", leave boundary_pct alone

### 7a. `verified_pct` ("Verified" column) — **cut it**

Agree with the ML finding. `verified_pct` is vault-wide, all-time, and not model-version- or
split-pinned; `eval_candidate_n_pos` (via the freeze-eligibility pill, already on the same
row per the prior UX pass) answers the same underlying question — "how much of this tag have
I verified" — with the *more actionable* framing: distance to unlocking scoring, in the
slice that actually counts. Showing a stale, broader duplicate of a sharper number already on
the row is worse than not showing it. (A tag that's heavily verified on the TRAIN side only
would show high `verified_pct` but a low freeze pill — that gap is real, but `verified_pct`
doesn't explain it either; Spec D's rewritten pill tooltip is what actually resolves that
confusion, by naming the TRAIN/EVAL split reservation directly.)

**[FRONTEND]** Remove: the `{ icon: "mdi-check-decagram-outline", key: "verified", … }`
header entry; the `<span class="rs-board-num rs-board-num--muted">{{ Math.round(r.verified_pct ?? 0) }}%</span>`
cell; the `"verified"` case in `keyval()`; the `{ label: "Least verified", key: "verified", dir: "asc" }`
entry in `SORT_OPTS`; the `verified` line in `SUBTITLE`; `defaultDir()`'s `key === "verified"`
branch. Update `grid-template-columns` from
`172px 116px 98px 106px 84px 44px 56px 92px 1fr 116px` (10 columns) to
`172px 116px 98px 106px 84px 56px 92px 1fr 116px` (9 columns).

**[BACKEND]** No urgency — `verified_pct` can keep being computed/served harmlessly. If a
later cleanup pass wants to drop it from `compute_tag_health_rows`/`TagHealth`/the response
model too, that's backend's call, not blocking this frontend change (`TagHealthRowResponse`
already declares `model_config = ConfigDict(extra="allow")`, so an unused field costs
nothing to leave in place).

### 7b. `boundary_pct` — **no UI home this pass; recommend dropping from the response**

`boundary_pct` flags a fuzzy tag *definition* (predictions clustering in the ambiguous
middle), which is a real signal but not one this board's UI has any next action for — there
is no rename/split/merge-tag flow reachable from here. Wiring it into the table would repeat
exactly finding 5's failure mode: a column with nothing to do about it. Recommend
**[BACKEND]** stop shipping it in `TagHealthRowResponse` (or keep it computed internally for
a future tag-hygiene feature that does have an action attached to it) rather than adding a
UI affordance to justify data that already exists.

### 7c. `overturn_rate` — **wire it into a real "Why" column instead of a new grid column**

This one *is* decision-relevant (a low overturn rate says "this tag's suggestions are mostly
noise, review it at your own risk"; a high one validates the tag as worth the time) but the
table is already at 9–10 columns wide — adding an 11th for one number is the wrong shape.
Fold it into fixing finding 5 instead: the currently-blank "Why it ranks here" column.

**[FRONTEND]** — `r.why` doesn't exist anywhere (confirmed: absent from
`TagHealthRowResponse` and from every store/service file read for this spec). Rather than
adding a new backend field, compute a short justification client-side from fields the row
*already* carries — deterministic, unit-testable (this directory already has
`reviewCards.test.js`, so board-logic unit tests are an established pattern here), zero new
API surface:
```js
function whyText(r) {
  if (r.has_model === false)
    return "not in the tagger's vocabulary — similarity review still works";
  const wrong = r.est_wrong_adj ?? r.est_wrong ?? 0;
  const missing = r.est_missing_adj ?? r.est_missing ?? 0;
  const mismatch = r.mismatch ?? 0;
  const disputes = r.model_disputes ?? 0;
  if (disputes > 0)
    return `model disputes ${disputes} of your past call${disputes === 1 ? "" : "s"}`;
  if (wrong === 0 && missing === 0 && mismatch === 0) {
    if (r.overturn_rate != null) {
      const pct = Math.round(r.overturn_rate * 100);
      if (r.overturn_rate >= 0.66) return `past suggestions mostly confirmed (${pct}%)`;
      if (r.overturn_rate <= 0.33) return `past suggestions mostly dismissed (${pct}%) — low signal`;
    }
    return "";
  }
  return [
    { label: "mostly missing — model is confident but untagged", v: missing },
    { label: "mostly wrong — tagged but model disagrees", v: wrong },
    { label: "near-identical shots disagree on this tag", v: mismatch },
  ].sort((a, b) => b.v - a.v)[0].label;
}
```
Priority order and rationale: a human-vs-model dispute (`model_disputes`) is the rarest and
most specific story on the row (per the module docstring, "surfaced, never auto-requeued —
human outranks model"), so it wins when present. Otherwise the dominant est_wrong/missing/
mismatch signal explains the ranking directly. Only when none of those fired (a tag that's
merely low-priority, not flagged) does `overturn_rate` get a look-in, as a secondary trust
signal — and only when it's strongly one-sided (≥66% or ≤33%); a middling overturn rate isn't
worth a sentence. Also fix the missing `:title` binding on the cell (currently truncates with
`text-overflow: ellipsis` and no way to read the rest):
```html
<span class="rs-board-why" :title="whyText(r)">{{ whyText(r) }}</span>
```

**[BACKEND]** none required for this — `overturn_rate` and `model_disputes` are already
shipped on every row.

## 8. Spec F — Accuracy tie-breaker: adopt, tightly scoped, capped, and badge-visible

**Decision: adopt a modified version — continuous and capped (not a discrete "comparable"
check), scoped to the default "Suggested (health)" sort only, and badged only on rows it
actually moved.**

Rejected as proposed: a discrete "when two tags have comparable Est. fixes" rule needs
"comparable" defined precisely or it becomes unpredictable, and applying it to the explicit
`eval_ap`/`eval_f1` sorts would violate the hard partition rule those two sorts already
enforce (AP-rows only rank against AP-rows, F1 only against F1 — documented in
`TagHealth`'s own docstring as a ranking *contract*). Applying it to "Most wrong"/"Most
missing"/etc. would also break the promise those explicit, single-number sorts make. Scoping
it to the one sort that already exists specifically to blend signals (`score`, aka the new
"Priority" sort from Spec C) keeps every other sort's guarantee intact.

**[FRONTEND] — uses `eval_f1`/`eval_metric_kind`/`eval_threshold_source`, already shipped:**
```js
const F1_BOOST_THRESHOLD = 0.7; // eval_f1 at/above this: no boost
const F1_BOOST_MAX = 1.3;       // eval_f1 = 0: full 1.3x cap

function isBoostEligible(r) {
  return (
    r.eval_metric_kind === "F1" &&
    r.eval_threshold_source != null &&
    r.eval_threshold_source !== "uncalibrated_fallback" &&
    (r.eval_f1 ?? 1) < F1_BOOST_THRESHOLD
  );
}

function boostFactor(r) {
  if (!isBoostEligible(r)) return 1;
  const deficit = (F1_BOOST_THRESHOLD - r.eval_f1) / F1_BOOST_THRESHOLD; // (0,1]
  return 1 + (F1_BOOST_MAX - 1) * Math.min(1, deficit);
}

function boostedScore(r) {
  return corrections(r) * boostFactor(r); // sort key only — never the displayed number
}
```
Continuous and capped means the boost only flips ordering between tags that were already
close — a small multiplier on a small `corrections()` value can't leapfrog a much larger one
— which is exactly the "comparable" behavior the ML recommendation wanted, without needing a
brittle band definition. Never for `eval_metric_kind === "AP"` or unfrozen rows (no
comparable scale, per the existing docstring contract); never for `uncalibrated_fallback`
(already flagged untrustworthy elsewhere via the `~`-prefixed/dashed-underline treatment).

**Visible surfacing (required, not optional per the brief):** the displayed **Priority**
number stays the honest, unboosted `corrections(r)` — only the sort order changes, and only
when it actually changes. Compute both orderings and badge only rows where the boost moved
them up:
```js
// inside the `sorted` computed, only when sort.value.key === "score":
const rawOrder = base.slice().sort((a, b) => keyval(b, "score") - keyval(a, "score"));
const boostedOrder = base.slice().sort((a, b) => boostedScore(b) - boostedScore(a));
const rawIndex = new Map(rawOrder.map((r, i) => [r.tag, i]));
const boostedIndex = new Map(boostedOrder.map((r, i) => [r.tag, i]));
function wasBoosted(r) {
  return (boostedIndex.get(r.tag) ?? 0) < (rawIndex.get(r.tag) ?? 0);
}
```
Badge: a small chip beside the Priority number (`.rs-board-health-num`), reusing the
existing warning-tone vocabulary already defined for low-F1 cells (`.rs-acc-f1--warn`/
`--bad`) rather than inventing a new color:
```html
<span
  v-if="sort.key === 'score' && wasBoosted(r)"
  class="rs-board-boost-chip"
  tabindex="0"
  :aria-label="`Ranked higher than its raw priority — weak accuracy, ${Math.round((r.eval_f1 ?? 0) * 100)} percent F1`"
  :title="`Ranked above its raw Priority score — this tag's measured accuracy is low (${Math.round((r.eval_f1 ?? 0) * 100)}% F1), so fixing it is worth more per review.`"
>
  <v-icon size="11">mdi-arrow-up-bold</v-icon>
</span>
```
```css
.rs-board-boost-chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 3px;
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-warning));
  background: color-mix(in srgb, rgb(var(--v-theme-warning)) 16%, transparent);
}
.rs-board-boost-chip:focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
}
```
`tabindex="0"` + `aria-label` for the same reason the `unfrozen_pending` pill has them (Spec
A of the prior legibility UX spec): a non-`<button>` element carrying a decision-relevant
explanation must be keyboard-discoverable, not hover-only.

**Default-on, no toggle.** A user who wants the unblended, literal-count order already has
"Most wrong" / "Most missing" (Spec F leaves those untouched); adding a fourth control just
to opt out of a capped, badge-explained, single-sort effect is over-engineering for the value
it returns. The visible badge *is* the escape hatch to understanding, per the brief's "never
a silent reordering a user can't explain."

**[BACKEND]** none — every field this reads is already on `TagHealth`/`GET /tag_health`.

## 9. Recommended flow (states, feedback, recovery)

| State | What the user sees | Recovery / next action |
|---|---|---|
| Fresh vault, board never built | Empty state, "Build now" button (unchanged) | Click → building banner (existing) |
| Board built, no reviews yet | Rows with Priority/Est.wrong/Est.missing populated; Accuracy column shows `unfrozen_pending 0/10` pill everywhere; persistent header rebuild control shows "Updated just now" | None needed — expected state |
| User reviews a tag (Spec A running silently) | Pending pill climbs slower than review count; hovering/focusing it (Spec D) explains why in one sentence, no dead end | Keep reviewing; pill crosses 10/10 → becomes clickable "Freeze to score" |
| User starts a review from a high-Priority tag, scan finds few/none | `emptyScan` state (Spec C) explicitly says the board's number is an estimate, not a promise — not framed as an error | Archive or refresh, same as today |
| Time passes, new pictures/reviews land (Spec B) | Persistent rebuild control (always visible now) eventually tints warning + "Rebuild now, or it'll catch up automatically" once `stale` ships; auto-rebuild finder closes the loop without a click | Click to force-refresh now, or do nothing |
| Two tags are close on Priority, one has a frozen low F1 | The weaker-accuracy tag ranks slightly higher in the default "Suggested" sort with a visible up-arrow chip explaining why (Spec F) | Hover/focus the chip for the one-sentence reason; switch to "Most wrong" for the unblended order |

Keyboard model: every new interactive element (persistent rebuild button, boost chip) is
either a real `<button>` or a `tabindex="0"` element with `aria-label`, matching the existing
`rs-acc-pill--pending` precedent already in this file — no new keyboard trap, no new
hover-only information.

## 10. Acceptance criteria

- `POST /picture_splits/assign` is called by a periodic backend task with zero user action,
  verified by seeding a fresh vault, reviewing pictures, and observing
  `eval_candidate_n_pos` climb without any manual endpoint call.
- The tag-health rebuild control is visible in the board header regardless of
  `store.healthRows.length` (row count 0, 1, or many) — no state hides it.
- `GET /tag_health` after N review decisions and no rebuild eventually goes `stale: true`
  (once Spec B backend ships) or the board's own periodic finder rebuilds it within its
  configured cadence without a user click.
- The board's default-sort column header reads "Priority", never "Est. fixes"; its tooltip
  and the legend both explicitly disclaim it as a review-session-size forecast.
- Hovering or focusing the `{n}/10` pending pill states the ~5× EVAL-reservation ratio, not
  just "review N more."
- The "Why it ranks here" cell is never blank-but-present for a scored row: it shows a
  reason, or is legitimately empty only when the row has no wrong/missing/mismatch signal
  and no lopsided `overturn_rate` to report. Truncated text is readable via `title`.
- No "Verified" column, header, sort option, or cell renders anywhere in the table.
- `boundary_pct` renders nowhere in the frontend.
- In the default "Suggested (health)" sort only, a tag with a frozen, calibrated F1 < 0.7
  can outrank a tag with a numerically higher raw Priority score, and does so with a visible,
  keyboard-reachable badge stating why. The **displayed** Priority number is never altered by
  the boost. "Most wrong" / "Most missing" / "Ranking score" / "Accuracy" sorts are bit-for-bit
  unaffected by the boost.

## 11. Validation / open items for the implementer

- **Exact staleness signal set (Spec B, backend).** This spec names pictures + tagger runs +
  reviewed suggestions as the minimum; whether `Tag` row creation/deletion needs its own
  timestamp tracked (it may not currently have one) is a real backend design question, not
  resolved here — check before implementing `_latest_health_relevant_change()`.
- **Auto-rebuild cadence (Spec B, backend).** A concrete debounce interval (e.g. "at most
  once every 5 minutes while stale") needs picking; this spec only requires that it exist and
  not thrash on every single review decision.
- **`remaining * 5` approximation (Spec D, frontend).** Tied to `TRAIN_RATIO = 0.8` by
  convention, not by shared constant — flag it in a code comment so it doesn't silently drift
  if that ratio changes; a follow-up could expose the ratio via the API instead.
- **Relative-time formatter (Spec B, frontend).** Confirm whether `utils/utils.js` already
  has a short relative-time helper before adding one — none was found in the files read for
  this spec, but the module wasn't read in full.
- **`F1_BOOST_THRESHOLD`/`F1_BOOST_MAX` values (Spec F, frontend).** `0.7`/`1.3x` are carried
  over directly from the ML recommendation's stated range; if a curator's actual usage shows
  the boost is too aggressive or too subtle once shipped, these are the two constants to
  retune — no other code changes needed.
- **Smallest test to resolve remaining uncertainty:** ship Spec A + B first (they're the
  literal root causes) and watch one full vault's `eval_candidate_n_pos` and `computed_at`
  over a normal review session before investing further design time in Spec F's boost
  tuning — if freeze eligibility and cache freshness alone resolve the "everything is static"
  complaint, the tie-breaker (a smaller, secondary trust improvement) can safely land a beat
  later without blocking the rest.
