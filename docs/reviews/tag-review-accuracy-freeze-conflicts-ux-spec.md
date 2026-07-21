# Tag Health accuracy / freeze / split-conflicts — interaction spec

**Author:** UI/UX expert (design consult, no code). Companion to
`docs/reviews/tag-review-tagger-takeover-design.md` (backend architecture, already
implemented across Waves A–D) and `docs/reviews/tag-review-tagger-takeover-plan.md`.

**Status:** ready for `senior-frontend-developer` (behaviour/flow) and `lead-designer`
(visual pass). Per CLAUDE.md: this document is the required sign-off for the flow/state
changes described below before implementation starts.

**Scope covered:** the `TagHealth` accuracy fields (`eval_metric_kind`,
`eval_threshold_source`, `eval_precision/recall/f1/ap/...`), the `POST /tag_eval_slices`
freeze action, and the `PictureSplit` conflict queue (`GET /picture_splits/conflicts`,
`POST /picture_splits/{picture_id}/resolve`). All backend-implemented; nothing here
changes the API.

---

## 1. User problem

**Primary user:** a self-hosting individual curating their own personal photo library
(confirmed against `docs/design/visual-language.md` §1: "PixlStash is a self-hosted
image library," "the photos are the color and the chrome stays quiet" — this is a
consumer/prosumer tool, not an MLOps dashboard, and its own board copy already proves
it: "Est. wrong," "Verified," "no model signal" are plain-English translations of
tagger-internal concepts, never raw ML terms). This person is not an ML practitioner.

**Job:** periodically decide which tags are worth reviewing (Tag Health board), work
through a review session, and — new in this spec — occasionally trust that a tag's
reported "accuracy" number means something, without needing to know what AP, F1, a
train/eval split, or "leakage" are. A conflict in the split system is something that
happens *to* them, unprompted, and must be resolvable without any of that vocabulary
either.

**Frequency:** the board and review sessions are used often (that's the product's core
loop, per `docs/reviews/tag-review-tagger-takeover-design.md`). Freezing a tag's eval
slice is *occasional and deliberate* — a curator action, not a per-review default. Split
conflicts are *rare and unplanned* — most users will see the count sit at zero
indefinitely.

**Costly failure:** the ranking-partition rule in the design doc exists precisely
because comparing an AP of 0.82 to an F1 of 0.82 as if they were the same scale is
actively misleading — a user could conclude a tag is "doing great" or "doing badly"
relative to peers when the numbers aren't comparable at all. That is the single biggest
risk this spec has to close off, structurally, not just with a caveat nobody reads.

---

## 2. Priority findings

1. **The ranking-partition constraint needs a structural answer, not a footnote.**
   AP and F1 rows sitting in one numeric column, sortable together, will get compared
   by users no matter what a tooltip says. The fix has to make the two kinds
   *look* different, not just be *labeled* different.
2. **Raw enum values must never reach the UI.** `eval_metric_kind=AP`,
   `eval_threshold_source=carried_forward`, etc. are backend vocabulary. The board's
   existing copy register (plain English, translated jargon, chips like "no model
   signal") is the bar every new string here has to clear.
3. **Freeze needs a fail path that doesn't dead-end.** "Not enough verified examples
   yet" is an expected, frequent response early in a tag's life, not an edge case —
   it needs to read as "come back after a few more reviews," not as an error.
4. **Split conflicts are the one surface with no existing precedent in this app** —
   and also the one place a non-technical user has to make a real judgment call. This
   is where the most translation work has to happen.
5. **The board is already dense** (9 columns, compact grid). Adding surface area for
   three fairly deep features without violating "quiet chrome" requires hiding two of
   the three by default (freeze: low-emphasis; conflicts: fully absent at zero) and
   compressing the third (accuracy) into the existing column rather than adding new
   ones.
6. **One keyboard ladder, not two.** `ReviewSessionsOverlay.vue` owns a single
   capture-phase `keydown` handler with a defined Esc-unwind order (cheat-sheet →
   dialog → zoom → tag panel → session → close). Every new keyboard-driven surface
   here must plug into that ladder and its `?` cheat-sheet, not add a second listener.

---

## 3. Board column: Accuracy

### 3.1 What the column replaces / where it sits

Add a 10th column, **Accuracy**, immediately after the existing "Last review" column
and before "Why it ranks here" (grid-template-columns gets one more track; exact width
is a `lead-designer` call, but budget it similarly to "Mismatch," ~80–90px). The
existing icon-header pattern (`mdi-check-decagram-outline` for Verified,
`mdi-clock-outline` for Last) is available — use a small icon
(e.g. `mdi-target-variant` or `mdi-bullseye-arrow`) plus a `title` tip, matching every
other header in the row.

**Do not add this as a second thing bolted onto "Verified."** `verified_pct` answers
"how much have I looked at this" (triage); accuracy answers "how good is the model at
this, per a frozen, scored slice" (trust). The design doc is explicit these stay
distinct signals — keep them as two columns, not one merged one.

### 3.2 State inventory (every combination a real user will see)

| Case | Visual form | Copy |
|---|---|---|
| No `ACTIVE` eval slice exists yet | Muted text-link "Freeze to score" (see §4) | tooltip: "Not scored yet. Freeze your confirmed examples to start tracking this tag's accuracy." |
| `eval_metric_kind = none` (no model signal at all) | A single muted dash `–`, nothing else | No new copy — the Tag column's existing "no model signal" chip already explains this row; don't duplicate the explanation in a second column. |
| `eval_metric_kind = insufficient_data` | Small muted pill, same shape/weight as the existing `.rs-board-nomodel-chip` | Pill text: **"not enough data yet"**. Tooltip: "Fewer than 10 confirmed examples for this tag — review a few more to unlock scoring." |
| `eval_metric_kind = F1`, source `calibrated` | Percentage + tri-color tint (reuse the board's existing error/warning/tertiary heat logic, just inverted sense — high % = good = tertiary/green-ish, low % = error) | Cell: **"91%"**. Tooltip: "91% accurate, based on 42 pictures you've confirmed. This tag has a cutoff tuned specifically for it." |
| `eval_metric_kind = F1`, source `carried_forward` | Same percentage form, small superscript/suffix marker (e.g. a tiny clock glyph) | Tooltip: "88% accurate, using the cutoff from an earlier version of the tagger — this tag hasn't been retuned since the model last changed." |
| `eval_metric_kind = F1`, source `rederived_disjoint_val` | Same percentage form, same marker as carried_forward (both mean "not fresh-tuned, but not a guess either") | Tooltip: "88% accurate, using a cutoff estimated from your training examples (no tuned cutoff exists for this tag yet)." |
| `eval_metric_kind = F1`, source `uncalibrated_fallback` | Percentage rendered **faded/dashed** (lower opacity, dashed bottom-border) with a leading `~`, **excluded from any F1 sort** | Cell: **"~76%"**. Tooltip: "Rough estimate only — this tag has no tuned cutoff yet, so this uses a generic 50/50 guess boundary. Not included when sorting by accuracy." |
| `eval_metric_kind = AP`, `n_pos ≥ 25` | A 5-segment dot/notch meter (**not** a percentage — see 3.3 for why), value 0–100 mapped to fill, plus the number | Cell: **"●●●●○ 82"**. Tooltip: "Ranking score: 82 out of 100, from 31 confirmed examples (likely range 76–88). Measures how well the model sorts probably-correct pictures ahead of probably-wrong ones — this tag doesn't have a tuned yes/no cutoff yet, so there's no single accuracy percentage for it." |
| `eval_metric_kind = AP`, `10 ≤ n_pos < 25` | Same meter form, trailing `*` | Tooltip: same as above minus the range, plus: "Only 14 confirmed examples — too few yet to show a confidence range." |

Two visually distinct *shapes* (percentage-with-tint vs. dot-meter) is the load-bearing
decision: even a user who ignores every tooltip cannot mentally line up "91%" against
"●●●●○ 82" as the same kind of number. That's a second, non-verbal backstop on top of
the sort partition below.

### 3.3 Why a meter instead of a percentage for AP

Rendering AP as "82%" would read as "82% accurate" to any user, which is false — it's
an integral over every possible cutoff, not a hit rate. A meter (discrete notches, not
a continuous percent bar) doesn't invite that reading and doesn't visually compete with
the F1 percentage next to it. Precedent for a non-percentage numeric widget already
exists on this board (the "Est. fixes" heat bar), so this isn't a new visual paradigm —
it's the same "bar/meter + number" idiom in a new spot.

### 3.4 The ranking-partition contract, concretely

The existing `SORT_OPTS` dropdown (`TagHealthBoard.vue`, currently 7 entries:
Suggested/Tag name/Most wrong/Most missing/Most conflicts/Least verified/Recently
reviewed) gains **two new entries**, not one:

- `"Ranking score"` (sorts by `eval_ap`, only meaningful for `kind = AP`)
- `"Accuracy"` (sorts by `eval_f1`, only meaningful for `kind = F1` and
  `threshold_source != uncalibrated_fallback`)

**The Accuracy column header itself is not click-to-sort.** Every other sortable
header in this table is a `<button>` because it maps to exactly one sort key
(`h.key && toggleSort(h.key)`); Accuracy maps to two different, non-comparable keys, so
making it clickable would silently reorder by a scale the user didn't choose. Render it
as a plain `<span>` header (the table already has this variant — see the "Why it ranks
here" header, which has no `key`). Sorting the new dimension is dropdown-only, which is
also the more forgiving interaction for a two-key situation (you pick the intent by
name, "Ranking score" vs. "Accuracy," instead of guessing what a third header-click does).

**When "Ranking score" or "Accuracy" is the active sort:** rows in the *other* kind, and
rows in the excluded set (`insufficient_data`, `none`, and F1-`uncalibrated_fallback`),
sink to the bottom of the list, stay visible (never hidden — hiding rows on a sort
change violates "user control," someone might be looking for exactly one of them), and
get the same `--nomodel`-style dimming already used for out-of-vocabulary tags
(`rs-board-row--nomodel`, opacity 0.55). Their Accuracy cell in that state shows a short
inline note instead of competing for rank: **"scored differently"** (with a tooltip
pointing at the other sort option) for off-kind rows, and the usual "not enough data
yet" pill for excluded rows. A one-line legend addition (matching the existing
`.rs-board-legend` paragraph style) documents this once: *"'Ranking score' and
'Accuracy' are two different kinds of numbers and are never sorted against each other."*

For every **other** active sort (Suggested/Tag name/etc.), the Accuracy column just
renders whichever state applies per row — no partition concern, since it isn't the sort
key.

### 3.5 Acceptance criteria

- No `AP`/`F1`/`calibrated`/`carried_forward`/`rederived_disjoint_val`/
  `uncalibrated_fallback` string ever renders outside a `title`/tooltip attribute.
- Selecting "Ranking score" never places an F1-kind row above an AP-kind row (or vice
  versa for "Accuracy") anywhere in the visible order, including ties.
- `insufficient_data` and `uncalibrated_fallback` rows are excluded from both new sorts
  but remain visible and filterable/searchable exactly like any other row.
- The meter (AP) and the percentage (F1) are visually distinguishable at a glance
  without reading either tooltip (shape test, not just color test — color alone isn't
  sufficient for colorblind users).

---

## 4. Freeze action

### 4.1 Where it lives, and why not automatic

**Manual, per-tag, not folded into "Start review."** Three reasons, weighed against
this app's existing patterns:

1. Freezing is consequential and durable — the new snapshot becomes what this tag's
   accuracy number means from now on, and (per the design doc's §6) is also what
   pixltagger's own eval gate will eventually read. Silently doing that as a side
   effect of "Start review" fails "visibility of system status": the user asked to
   review tags, not to redefine what "accurate" means for one of them.
2. The freeze floor (`n_pos ≥ 10` on the eval side) means most reviews-in-progress
   *can't* usefully freeze yet. Auto-attempting it on every "Start review" click would
   mostly produce the failure state (§4.3) as background noise, which is worse than not
   trying.
3. The board already has exactly this shape of action — **"Build now"** (rebuild the
   health cache) is a low-emphasis, manual, single-purpose button that appears only in
   the state where it's relevant (`rs-board-rebuild`, shown only in the "no health data
   yet" empty state). Freeze should read as the same *class* of control: an occasional
   curator action, not a primary flow.

**Placement:**
- **Board row, Accuracy cell**, when no `ACTIVE` slice exists: a low-emphasis text
  affordance, always visible (not hover-gated — see §7 on why first-time actions must
  not be hover-only), styled like a text link rather than the heavier `rs-board-btn`
  used for "Start review" — this is a secondary action next to a primary one in the
  same row. Label: **"Freeze to score"**.
- **Board row, Accuracy cell**, once an `ACTIVE` slice exists: a ghost "↻ Refreeze"
  control, revealed on hover/focus-within of the cell — this reuses the exact technique
  already shipped for `ReviewRail`'s abort button (`.rs-rail-abort`: `visibility:
  hidden` by default, `visibility: visible` on `:hover`/`:focus-within`, `visibility`
  not `display` so the row doesn't reflow when it appears). This is the right
  precedent specifically *because* re-freezing is a secondary action on an
  already-established state, unlike the first-time freeze.
- **Inside an open review session** (near the tally/receipt area in
  `ReviewSessionView.vue`), a soft nudge once the session's confirmed-count for this
  tag crosses the freeze floor: not a dialog, just a small inline line near the
  existing tally display — flagged here as a recommendation for the implementer to
  place next to the removed/added/kept counts, since that's the moment the user has
  just produced fresh eligible examples.

### 4.2 Interaction shape: no dialog

Freeze has zero configurable options (it always operates on "this tag's current
human-verified eval-side pictures") — unlike "New review," which genuinely has choices
(scope, include-reviewed) and earns its modal. Match "Build now" instead: **a single
click**, no confirmation dialog, no options.

### 4.3 Success and failure feedback

**Success:** the button/link is replaced in place by a brief transient state — reuse
the board's `mdi-spin` loading-icon idiom already used for "Building tag health
signals…" — label **"Freezing…"** for the moment the request is in flight, then the
cell repopulates with the real number once the next `/tag_health` fetch lands (the
board already refetches on this kind of action, per `rebuildHealth()`). No modal, no
toast — the number appearing *is* the confirmation, consistent with how the rest of
this board communicates state (numbers changing in place, not pop-ups).

**Failure ("not enough verified examples yet"):** render inline, in the same cell,
for a few seconds, then revert to the button — never a silent no-op and never a
blocking dialog for an expected, common outcome. Copy (fill in the live deficit if the
response includes it, else the generic form):

> Not enough confirmed examples yet — needs at least 10, this tag has **{n}**. Review a
> few more to unlock freezing.

(Generic fallback if the count isn't available: *"Not enough confirmed examples yet —
review a few more of this tag to unlock freezing."*)

### 4.4 Re-freeze / supersession

**Not silent, but not a blocking confirm either.** A blocking "Are you sure?" dialog
(the weight of `ReviewRail`'s abort dialog, with its keep/undo choice) is wrong here —
that pattern exists for a destructive, session-scoped action with real undo cost.
Refreezing is expected to recur (the whole point of the feature is tracking accuracy
across model generations over time) and the old snapshot is never deleted, only marked
`SUPERSEDED` — there's nothing to "lose."

Resolution: **disclose before commit, not after.** Hovering "↻ Refreeze" shows a
tooltip surfacing the freeze history before the click:

> Last frozen {date}, {n_pos} confirmed examples. Refreezing replaces this with today's
> confirmed set — the old snapshot is kept in history, not deleted.

The click still fires immediately on click (no modal) — the tooltip *is* the warning,
delivered ahead of commitment, matching the freeze action's overall low-ceremony shape.

**History disclosure** (`GET /tag_eval_slices?tag=`): a click-to-expand inline
disclosure, not a hover tooltip (history is multi-line and needs to stay open to read
— `title` tooltips fail that and aren't reliably keyboard-openable). Reuse the sticker
shelf's exact toggle pattern from `ReviewRail.vue` (`rs-shelf-toggle`,
`aria-expanded`, chevron icon): a small "History (3 freezes)" toggle near the Refreeze
control, expanding to a short list — date, `n_pos`/`n` at freeze time, and a status
chip (**Active** / **Superseded**).

### 4.5 Acceptance criteria

- No modal appears for either freeze or refreeze; both are single-click actions.
- The failure state never leaves the row in a dead-end — the same "Freeze to score"
  control is clickable again immediately after the deficit message clears.
- Hovering (or focusing) "Refreeze" always shows the last-frozen date and count before
  any click is possible.
- The freeze-history disclosure is reachable and operable via keyboard (a real
  `aria-expanded` button, not hover-only content).

---

## 5. Split conflicts

This is the most novel surface — no existing precedent in this app — and also the one
place a non-technical user makes a real judgment call. Every design choice below
prioritizes reusing an existing idiom over inventing one, per the task's own framing.

### 5.1 Where it surfaces

**A count-gated nav row in `ReviewRail.vue`, hidden entirely at zero.** The rail
already models exactly this shape for a different rare-but-actionable signal — the
board's "The current model disputes N of your earlier calls" toggle is
`v-if="totalDisputes > 0"` (`TagHealthBoard.vue`). Copying that pattern verbatim (not
inventing a badge system, not reusing the Tasks-tab pulsing-dot idiom, which signals
*ongoing* background work rather than a *pending decision*) is the better fit here.

Add a second top-level nav destination in the rail, alongside the existing "Tag
health" row (`rs-rail-board`), appearing only when the conflict count is nonzero:

```
[mdi-heart-pulse]  Tag health
[mdi-shuffle-variant] Needs a decision · 2      ← only rendered when count > 0
─── Open reviews ───
...
```

Icon: something distinct from the existing alert iconography already in use
(`mdi-alert-octagon-outline` = anomaly, `mdi-account-alert-outline` = model dispute) —
avoid a third near-identical "alert" glyph; suggest `mdi-shuffle-variant` or
`mdi-image-multiple-outline` (two-pictures framing), final choice is a `lead-designer`
call. Color: warning-tone (this is data hygiene, not data loss — don't use `error`).

Clicking it opens a **new `view.type: 'conflicts'`** in the same main pane the board /
session / archived receipt already occupy — no new overlay, no new route, matching
exactly how `store.showBoard()` / `store.openSession(id)` / `store.openArchived(id)`
already swap the main pane's content today.

### 5.2 What the resolution card asks, in plain language

**Never say** "near-duplicate," "train/eval split," "leakage," "component," or
"picture split" anywhere a user reads. Frame it as two pictures with two *jobs*:

> **Two pictures, two jobs**
> PixlStash found that these two look like the same shot. It keeps separate pictures
> for teaching the tagger and for checking its work, so it never grades the model on a
> picture it already studied. These two are currently stuck in the middle because
> they'd normally end up on opposite sides.

Layout: reuse `ReviewPairCard.vue`'s existing side-by-side picture layout wholesale —
this conflict *is* structurally a pair-decision card, just deciding a different
question than tag-correctness ("does this pair belong together on one side of the
fence" instead of "does this pair share a tag"). This is the single best reuse
opportunity in the whole feature: same visual shape, same click-to-zoom wiring
(`rs-open-zoom`, already `provide`d at the overlay level), same card chrome — a new
*kind* of pair card, not a new UI paradigm.

**Two-step decision, to keep the first screen simple** (progressive disclosure inside
the card itself, not a wizard):

```
Step 1 — always shown:
  "Are these actually the same shot?"      [ Yes ]   [ No ]

Step 2 — only shown after "Yes":
  "Keep both together for:"
     [ Teaching the tagger ]   [ Checking its work ]   [ Leave both out for now ]
                                                          ^ marked "recommended"
```

- **"No"** resolves immediately on click — single decision, no further screen. Copy on
  hover/tooltip: "Treats them as different pictures — each one gets sorted normally
  again."
- **"Yes" → "Leave both out for now"** is the visually recommended option (small
  "recommended" tag next to it, not a different button style) because it mirrors the
  backend's own fail-closed default (`NEITHER`) — the UI should nudge toward the same
  safe choice the system defaults to when it can't decide on its own, not away from it.
- **"Yes" → "Teaching"/"Checking"** are the two committing choices when the user is
  confident and wants the pair to count somewhere.

**Open item, not guessed here:** the exact resolve-endpoint payload/enum values for
these three "Yes" branches aren't specified in the architecture doc at the level of
"what does `POST /picture_splits/{picture_id}/resolve` actually accept." Flag this
explicitly for `senior-backend-developer`/`senior-frontend-developer` to confirm before
wiring the buttons — the three-choice framing above is the UX contract; the request
shape underneath it needs a quick contract check, not an assumption.

### 5.3 Undo / recovery

Not fully specified backend-side in the reviewed design doc (no mention of whether a
resolved conflict can be reopened). Recommend, and flag for backend confirmation: a
transient inline **"Resolved · Undo"** affordance for ~5 seconds after a decision,
matching the general expectation this app sets elsewhere (Review sessions' own
`undo()` reverses the last queue decision) — if the backend can't support reopening a
resolved conflict, downgrade this to a plain "Resolved" confirmation with no undo
affordance rather than showing an undo control that silently fails.

### 5.4 Discoverability of the *first* conflict ever

Because the nav row's default state is total absence (§5.1), a user's first-ever
conflict is a "new thing appeared" moment with no prior visual context. Recommend a
one-time subtle entrance treatment (brief pulse/fade-in on first render, honouring
`prefers-reduced-motion`) rather than the row just silently existing on next
render — flagged as a testable question in §8, not asserted as settled.

### 5.5 Acceptance criteria

- The "Needs a decision" nav row is entirely absent from the DOM at count 0 (not
  present-but-hidden — matches the `v-if` precedent, keeps it out of tab order too).
- No jargon term (near-duplicate, split, leakage, train, eval, component) appears in
  any user-visible string on this surface.
- The "No" branch resolves in one click; the "Yes" branch never resolves in fewer than
  two (prevents an accidental single fat-fingered click from committing a picture to a
  bucket).
- "Leave both out for now" is visually distinguishable as the recommended choice
  without being the only clickable one (no dark-pattern default).

---

## 6. Discoverability vs. clutter — recommendation

Audience is confirmed non-ML (visual-language.md §1; the board's own existing copy
register is the proof). Recommendation, per surface, is **not** a blanket "advanced
mode" toggle — this codebase has no such pattern anywhere today, and inventing one
would be a larger, more novel UI addition than any single piece of this feature.
Instead, three narrower mechanisms, each already precedented:

1. **Accuracy column:** default-visible (it's core to the board's purpose), but every
   ML-internal term stays behind a `title` tooltip — same mechanism every other header
   tip on this board already uses.
2. **Freeze:** default-visible but low-emphasis (text link, not a prominent button) —
   same visual weight class as "Build now."
3. **Split conflicts:** default-*invisible*, `v-if="count > 0"` — same mechanism as the
   existing "model disputes" toggle. This is the strongest lever available and it's
   already in the codebase.

---

## 7. Accessibility / keyboard model

- **Freeze** (first-time): a real, always-visible `<button>` in normal tab order,
  positioned after "Last review" and before "Why it ranks here" in each row's DOM
  order — a first-time action must not be hover-gated (hover-reveal is only
  appropriate for a *secondary* action on an already-established state, which is why
  "Refreeze" gets it and initial "Freeze" doesn't).
- **Refreeze / history disclosure:** `visibility: hidden` + `:hover`/`:focus-within`
  reveal, copying `.rs-rail-abort`'s technique exactly (`visibility` not `display`, so
  keyboard-focused elements are reachable via Tab even before hover, and the row
  doesn't reflow when the control appears).
- **History disclosure** must be a real `<button aria-expanded="...">` (matching
  `rs-shelf-toggle`), not a hover-only tooltip — rich, multi-line content that needs to
  stay open for reading is not appropriate for the `title` attribute pattern used for
  the board's single-line tips.
- **Conflicts nav row:** focusable, in the rail's existing tab sequence next to
  "Tag health." Give it an `aria-label` that includes the live count (e.g. "Needs a
  decision, 2 pending") so screen-reader users get the count without relying on the
  visual pill alone.
- **Conflict-card shortcuts:** integrate into the *one* capture-phase handler in
  `ReviewSessionsOverlay.vue` (`handleKeyDown`) — add a delegation branch parallel to
  the existing `sessionRef.value?.handleKey(key)` (e.g. a `conflictsRef` ref exposing
  its own `handleKey`), never a second `keydown` listener. Suggested bindings,
  consistent with the existing Y/N and B/N/L/R idiom already documented in the
  cheat-sheet: `Y`/`N` for "are these the same shot," then a second-tier binding (e.g.
  `1`/`2`/`3` or `T`/`C`/`O` for Teach/Check/leave Out) for the follow-up choice.
  **Add every new binding to the `SHORTCUTS` array** in `ReviewSessionsOverlay.vue` so
  the `?` cheat-sheet stays the single source of truth — it already documents every
  other keyboard surface in the overlay this way.
- **Esc ladder:** a conflict card mid-decision (Step 2 revealed) should collapse back
  to Step 1 on Esc before falling through to closing the conflicts view, matching the
  existing pattern where `sessionRef.value?.handleKey("escape")` can consume Esc for a
  "pending consistency confirm" before the overlay itself closes.
- **Focus return:** resolving the last conflict in the list (count → 0, which also
  removes the nav row per §5.1) must move focus somewhere sane — return it to "Tag
  health," mirroring how `abortSession`/`archiveSession` already call `showBoard()`
  when the currently-viewed session disappears out from under the user.
- **Reduced motion:** the freeze "Freezing…" spinner, the conflict "Resolved" flash,
  and any first-appearance entrance treatment for the conflicts nav row (§5.4) must all
  honour `prefers-reduced-motion`, per the app-wide rule already stated in
  `docs/frontend_architecture.md` §4.4.

---

## 8. Validation — what's still open

1. **Backend contract check, not guessed:** confirm the exact accepted values/payload
   for `POST /picture_splits/{picture_id}/resolve` against the three-branch decision in
   §5.2 before wiring buttons. This is the one place this spec had to describe the UX
   contract without a fully specified backend shape underneath it.
2. **Smallest test for the core bet:** a short read-only check (5 users, no other
   context) showing only the Accuracy column's two cell shapes (percentage vs. meter)
   plus their tooltips — the question is whether "these are two different kinds of
   numbers" registers without further explanation, since the whole partition design
   rests on that landing non-verbally.
3. **First-appearance treatment for the conflicts nav row (§5.4):** worth an A/B
   between "silent appearance" and "one-time pulse/highlight" once real usage exists —
   flagged, not resolved, since it's a low-stakes cosmetic question that doesn't block
   implementation either way.
4. **Undo semantics for conflict resolution (§5.3):** needs a backend confirmation on
   whether a resolved conflict can be reopened before committing to showing an "Undo"
   affordance in the UI.
