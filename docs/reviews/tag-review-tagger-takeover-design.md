# Tag review takeover — architecture design

**Status:** design consult (principal-software-engineer), not implementation. Companion to
`docs/reviews/tag-review-tagger-takeover-plan.md` (the scoping doc this design satisfies).

Grounded in a direct read of the current code (`tag_health_service.py`, `review_service.py`,
`tag_scan_service.py`, `tag_suggestion_service.py`, `tag_prediction_service.py`,
`tagger_run_service.py`, the relevant `db_models/`, `near_neighbor.py`, `detection_task.py`,
`anomaly_penalty.py`, `pictures/_helpers.py`'s `enforce_picture_scope`) and `git log`/`git show`
on `tag-review-rewrite` through commit `7c50a1e4`.

---

## 0. Grounding notes

The four files the original task flagged as "uncommitted" (`ReviewRail.vue`, `TagHealthBoard.vue`,
`tag_scan_service.py`, `test_tag_suggestions_api.py`) are already committed as `7c50a1e4 "Minor tag
review tweaks"`. `tag_scan_service.py`'s change adds `MIN_DISPLAY_TWIN_SIM` — a floor requiring a
displayed dhash-near twin to also clear 0.9 CLIP cosine similarity before the perceptual-hash
override fires (a close Hamming distance alone can be a hash collision). This is directly reused
below as the corroboration rule for gap #2's leakage guard, rather than inventing a new one.

---

## 1. Frozen verified eval slice → real precision/recall/F1

**Problem.** `verified_pct` counts any prediction row with a non-`UNKNOWN` `label_state` — mixed
across all time, never pinned to a fixed picture set or model generation. Can't become a real
accuracy number without a frozen membership + ground truth.

**Data model (new — not a reuse).** `TagPrediction` already *is* the live human-label ledger, but
it's mutable and picture+tag-keyed — a later correction silently changes historical numbers, and
nothing snapshots "the set of pictures this metric was computed over."

- `TagEvalSlice` — one row per freeze event: `id, tag, status (ACTIVE|SUPERSEDED), created_at`.
  Mirror `Review`'s pattern: partial unique index enforcing at most one `ACTIVE` slice per tag;
  re-freezing supersedes.
- `TagEvalSliceItem` — frozen membership: `id, eval_slice_id FK, picture_id FK, label_state (POS|NEG,
  snapshotted), frozen_at`. Label state is **copied, not live-joined** (that's what "frozen" buys).
  The model's prediction is **not** frozen — recompute P/R/F1 at read time by joining live
  `TagPrediction.confidence WHERE model_version = ?` against the frozen `label_state`, so the same
  ground truth re-scores against every new model generation.

**Migration.** Two new tables, additive, no data migration.

**Freeze mechanics.** Freeze for tag X = every picture with `TagPrediction.label_source='human'` for
X, whose split (gap #2) is `EVAL`, copied into a new `TagEvalSliceItem` set; prior `ACTIVE` slice
superseded. This is why #1 can't ship correctly ahead of #2 — freezing before split assignment
exists would freeze train-contaminated pictures.

**Threshold for P/R/F1 — resolved (decision procedure, not a single fallback). Reviewed and
refined by `machine-learning-expert`; see verdict below.** Terminology correction from the ML
review: sweeping a threshold to maximize F1 on the eval slice you then report F1 on is not data
leakage in gap #2's sense (no label information reaches model weights) — it's **threshold
selection bias / test-set tuning**, the same failure as picking a checkpoint by argmax validation
accuracy and reporting that same validation number as the test result. The distinction matters so
this isn't confused with gap #2's picture-identity contamination guard, a genuinely different
mechanism. Severity is real, not theoretical: with `n_pos` in the 5–15 range (expected for rare
anomaly tags) swept over a ~20–50-point threshold grid, expected optimistic F1 bias is 0.1–0.2
absolute — larger than the generation-to-generation deltas this board exists to surface.
`load_label_thresholds` gives a per-tag calibrated threshold from the tagger's meta JSON *when
available*; when it isn't, the fix is a decision order, best to worst:

1. **No predictions at all** → don't score. Existing `has_model` gate — remap children / "no model
   signal" rows have nothing to threshold; show that state, no F1/AP.
2. **Predictions exist, `n_pos ≥ 10`, no calibrated threshold** → report a threshold-free metric
   instead of faking an F1: **Average Precision**, computed via the non-interpolated step-function
   estimator (`AP = Σₙ (Rₙ − Rₙ₋₁)·Pₙ`, i.e. `sklearn.average_precision_score` semantics) —
   **not** trapezoidal PR-AUC (`sklearn.metrics.auc` over PR points), which Davis & Goadrich (2006)
   show is provably over-optimistic since linear interpolation in PR space isn't achievable by any
   real classifier. AP over ROC-AUC specifically because these tags are typically far-more-NEG-
   than-POS — ROC-AUC gets inflated by the large true-negative count on the FPR axis and can look
   strong even when precision at any usable operating point is poor.
   - `10 ≤ n_pos < 25` → AP as a point estimate only, flagged "CI unavailable — n too small."
   - `n_pos ≥ 25` → AP with a bootstrap CI: resample at the **picture level** (mirrors
     pixltagger's own `paired_bootstrap` in `legacy/report.py`/`bootstrap.py` — reuse that
     precedent, not a new methodology), ≥1000 iterations (pixltagger uses 2000), percentile CI.
     Drop degenerate zero-positive resamples from the percentile calculation; if >10% of resamples
     are degenerate, collapse to the no-CI point-estimate state regardless of the raw `n_pos`
     count — that ratio is itself a second signal the slice is too thin.
   - `n_pos < 10` → don't compute AP at all. New explicit state, not silent fallthrough to `none`:
     `eval_metric_kind = insufficient_data`. Falls in the gap between tier 1's `has_model=False`
     and a scoreable slice — must be its own enum value, not conflated with either.
3. **UI needs a P/R/F1 triple** → source the threshold from a slice disjoint from the one being
   scored, in priority:
   - Carry forward the last calibrated threshold for that tag from a **previous** model
     generation's meta, if one exists — not tuned on this eval, leak-free, more meaningful than a
     guess.
   - Re-derive by the tagger's own policy: `label_thresholds_min_precision` (0.75 in current meta)
     — lowest threshold hitting ≥0.75 precision, F1-max fallback, the same policy function
     `legacy/finetune.py::per_class_production_thresholds` already implements on the tagger side —
     applied to a **validation slice disjoint from the eval slice**: that tag's human-labeled
     `TRAIN`-split pictures, disjoint from `EVAL` by construction under `PictureSplit` (gap #2), val
     use only, never gradient training. **Gated by the same `n_pos ≥ 10` floor** applied to that
     TRAIN-split subset specifically — tier 3b is reached only when there's *no* tagger-side
     calibration history for the tag, which is precisely when the TRAIN-split human-labeled subset
     is also least likely to be rich; below the floor, treat as "no disjoint val slice exists" and
     fall straight to tier 4 rather than trusting a threshold derived from a handful of examples.
     Score the val slice's confidence with the **current** `model_version`'s live predictions
     (same live-prediction-against-frozen-label join pattern as the main P/R/F1 computation), not a
     stale snapshot. Best-effort/floor-gated tier, not load-bearing — degrades gracefully to tier 4
     when its precondition isn't met; implement tiers 1/2/4 first.
4. **Last resort** — fixed, tag-independent **0.5**, explicitly labeled `uncalibrated`. Only when
   neither a prior calibrated threshold nor a sufficiently large disjoint labeled val slice exists.
   Surface as a UI chip (`"uncalibrated @0.5"`, same idiom as `"no model signal"`), and **exclude
   from any ranking or sort against calibrated tags**.

**Cross-metric-kind ranking — explicit contract, not just a tier-4 exclusion.** AP (tier 2) and F1
(tier 3a/3b) are different metric kinds, not just different calibration confidence levels — an AP
of 0.82 (integrated across all recall levels) and an F1 of 0.82 (harmonic mean at one operating
point) aren't on a comparable scale even when both are fully trustworthy. The board's sort/rank
must **partition by `eval_metric_kind`**: AP-tags rank among AP-tags, F1-tags among F1-tags,
`insufficient_data`/`none`/tier-4-uncalibrated excluded from ranking entirely. A single global
`ORDER BY` against whichever numeric column happens to be populated is wrong — state this
explicitly so it isn't left implicit for an implementer to get wrong.

**Board surface.** Add `eval_precision, eval_recall, eval_f1, eval_ap, eval_n, eval_n_pos,
eval_slice_frozen_at` to `TagHealth` (`eval_n_pos` alongside `eval_n` because for these imbalanced
tags positive count, not total slice size, is what determines trust — see the `n_pos` floors
above), plus two small enums: `eval_metric_kind (AP | F1 | insufficient_data | none)` and
`eval_threshold_source (calibrated | carried_forward | rederived_disjoint_val |
uncalibrated_fallback | none)`. Populated only for tags with an `ACTIVE` slice. The UI reads
`eval_metric_kind`/`eval_threshold_source` to decide whether to render AP, a flagged-uncalibrated
F1, a trusted F1, or an "insufficient data" state — and whether the row is eligible to sort/compare
against calibrated peers, per the partition rule above. Keep `verified_pct` as a distinct "how much
has anyone looked at this" triage signal. The 10/25 `n_pos` cutoffs are heuristic, not derived from
a closed-form guarantee — flagged as provisional in the doc, to revisit once real eval-slice size
distributions are observed post-launch, but they're the concrete numbers to implement against now.

**Freeze-time floor.** `POST /tag_eval_slices` must not create an `ACTIVE` slice for a tag with
`n_pos < 10` on the `EVAL` side — same floor as above, deliberately identical rather than a fourth
unrelated magic number. Below it, the freeze endpoint returns a "not enough verified examples yet"
state instead of freezing a slice that can only ever produce a noisy, falsely-precise-looking
number.

**API.** `POST /tag_eval_slices {tag}`, `GET /tag_eval_slices?tag=`, `GET
/tag_eval_slices/{id}?model_version=`.

**Authz.** No single resolvable `picture_id` in these routes (vault-wide curation surfaces, like
`/tag_health` and `/reviews`). State (a)-equivalent: follow `_reject_scoped_tokens`/
`fetch_scope_allowed_picture_ids(...) is not None` → 403, the established precedent in
`tag_health.py`/`reviews.py` — deliberate copy of reviewed precedent, still needs named
`chief-security-officer` sign-off since these are new routes.

**Generality.** "Freeze a verified slice, score P/R/F1 against it" is generic per-tag QA — no
hand/foot, no fixed vocabulary, no pixltagger coupling.

---

## 2. Train/eval split + leakage/dedup discipline

The load-bearing gap — most scrutiny here.

**Data model.** One new table, `PictureSplit`: `picture_id (PK/FK, unique), split (TRAIN|EVAL|
NEITHER), component_key, assigned_at, conflict (bool, default False), conflict_detail (text,
nullable)`. Vault-wide and picture-level, not per-tag — a picture's train/eval identity should be
stable across every tag it's ever used to evaluate. No separate conflict-queue table needed —
`SELECT * FROM picture_split WHERE conflict = true` *is* the queue.

**Assignment — component-aware, not per-picture-hash.** Naive `hash(picture_id) % 100 < eval_pct`
lets two near-duplicates land on opposite sides. Instead:

1. Compute the near-dup connected component before assigning split, using the same corroborated
   signal `tag_scan_service.py` just shipped (dhash Hamming ≤ `DEFAULT_MAX_TWIN_HAMMING` **and**
   CLIP cosine ≥ `MIN_DISPLAY_TWIN_SIM`, or a high-confidence `PictureLikeness` row ≥
   `MISMATCH_LIKENESS_THRESHOLD`) as union-find edges. 100% reuse of existing infra.
2. Assign the whole component to the same split by hashing a stable component key (e.g. min
   `picture_id` in the component) — all members land on the same side by construction.

**Split ratio and stratification — resolved (`machine-learning-expert` recommendation).**

- **Ratio: 80/20 train/eval**, applied at the near-dup-component level from step 2 above. The
  ratio can't fix per-tag sparsity on its own — only the `n_pos` floors in §1 catch that — but it
  can maximize the pool available to §1 tier 3b's TRAIN-side threshold rederivation and to human
  labeling generally, so lean toward more TRAIN rather than a 50/50 split that starves both sides
  at once.
- **Stratify by picture set, not by tag or import batch.** Not by tag: would require per-tag split
  assignment, directly contradicting this table's core invariant that a picture's train/eval
  identity is stable across every tag it's ever used to evaluate — a picture carries many tags of
  different rarity simultaneously and can't sit on both sides at once without breaking the
  leakage guard this gap exists to build. Not by import batch: a temporal/operational artifact
  that risks baking tagger-version drift into the split rather than canceling it. By picture set:
  sets correlate with shoot/character/story-arc content and correlated tag co-occurrence — the
  same axis `tag_health_service.py`'s scoped queries already treat as a first-class dimension —
  and a naive global hash risks an entire set landing on one side by chance, biasing eval toward
  whatever's idiosyncratic about that set. Target the 80/20 ratio **within each set's components**,
  not globally.
- **Freezing floor** — see §1's "Freeze-time floor": `n_pos < 10` on the `EVAL` side blocks
  creating an `ACTIVE` slice for that tag, using the same number as the AP/rederivation floors on
  purpose (one magic number across the whole design, not three unrelated ones).

**Where the guard sits — write path (primary) and read path (secondary).**

- **Write path:** the near-dup graph is discovered incrementally; a component can merge *after*
  both halves already have different splits — this is the actual leak vector. Whenever a new
  corroborated edge connects two pictures with **different** splits: mark both `conflict=True`,
  don't auto-resolve. Mirrors the existing `model_disputes` convention (surfaced, never
  auto-applied, human outranks model) — fail closed: pull any `TRAIN`-side picture out of any
  `ACTIVE` `TagEvalSliceItem` membership (never move a picture *into* eval automatically), force
  both sides to `NEITHER` pending human resolution.
- **Read path:** at freeze time (gap #1), re-validate no candidate item has a corroborated near-dup
  on the `TRAIN` side, aborting/flagging rather than freezing silently — catches a race between edge
  discovery and freeze in the same window.

**Detection trigger.** `PictureLikeness` already has a recompute queue
(`PictureLikenessQueue`) — the conflict check is a cheap addition to its consumer, not a new
background system.

**Cross-repo resolution (investigated directly in `../pixltagger`, not assumed).**
`pixltagger/legacy/fetch_pixlstash.py`'s `PixlStashClient` already does a periodic **API pull**
into a local sidecar cache (`pixlstash_cache/{train,eval}/{id}.txt`), driven by `POST
/pictures/tags/bulk_fetch` — this is the live, actively-used ingestion boundary
(`docs/architecture.md` in pixltagger: PixlStash is "the system of record," `pixl fetch` is "the
sole ingestion boundary"). Everything downstream (`finetune.py`, `official_hand_eval.py`,
`decide.py`) reads only that local cache, never the API directly.

Today, split membership on the pixltagger side is **not** driven by anything resembling
`PictureSplit` — it's inferred from which named PixlStash **picture set** (`train_set`/`eval_set`
in `pixlstash.json`) a picture belongs to, and `apply_hand_labels.py`'s eval-guard is a *live*
`/picture_sets/{id}/members` check at write time, not a stored split. This is exactly the fragile
convention gap #2 exists to replace — picture-set membership is curator-maintained and has no
leakage guard of its own.

**Design consequence — no new pixltagger-facing artifact, extend the endpoint it already calls.**
Per direction from the user (avoid coupling pixltagger to anything beyond "it fetches updated tags
through the API"): add an optional `split: Optional[str]` field to `BulkPictureTagsResponse`
(`pixlstash/routes/tags.py:75-81` — already `ConfigDict(extra="allow")`, so this is purely additive
and safe for pixltagger's existing client, which ignores unknown keys) and a matching `split`
filter param + response field on `GET /pictures` (`_listing.py`'s `PictureListFilters`/
`GridPicture`), so pixltagger's `pixl fetch` can eventually route pictures into
`pixlstash_cache/{train,eval}/` by PixlStash's authoritative, leak-guarded split instead of
picture-set naming. **This is a pixltagger-side migration to make later, not something this design
implements** — PixlStash's job is only to make the authoritative signal available on an endpoint
already in its client's call path.

**Migration.** One new table, additive.

**Authz.** Same owner-only vault-wide pattern as #1 for any assign/conflict-resolution endpoints
(`POST /picture_splits/assign`, `GET /picture_splits/conflicts`, `POST
/picture_splits/{picture_id}/resolve`). The `split` field added to `GET /pictures` and
`bulk_fetch_tags` rides those routes' *existing* authz (see note in §6 below — three different
mechanisms are in play across the routes touched by this plan; each new field must be checked
against its own route's existing check, not assumed centralized).

---

## 3. Reliability-aware ranking

**Validated: pure reuse, zero new tables.** `get_latest_tag_precisions` already returns
`{tag: precision}` from the newest `TaggerRun` report; `anomaly_penalty()` already establishes the
discount idiom (`evidence = (p ** CONF_POWER) * precision`, `DEFAULT_TAG_PRECISION` = 0.90
fallback). Apply the same discount to `est_wrong`/`est_missing` in `compute_tag_health_rows`.

**Semantics nuance.** `est_missing_adj = round(est_missing * precision)` is dimensionally correct
(precision = P(true positive | predicted positive), directly calibrates "probably missed" counts).
Applying precision to `est_wrong` is a reasonable first-order noise discount but not as rigorous
(precision isn't P(true negative | predicted negative)) — document as an explicit approximation.
Future refinement: a `get_latest_tag_reliability` sibling exposing per-tag recall too (already in
the `per_tag` report), still zero new tables.

**Data model.** Add `est_wrong_adj: float`, `est_missing_adj: float` to `TagHealth`. Trivial
additive migration.

**Gotcha to verify at implementation time.** `get_latest_tag_precisions` keys are
`.strip().lower()`; confirm `TagPrediction.tag`/`TagHealth.tag` are normalized consistently before
the dict lookup.

---

## 4. Part-level (crop) review for hand/foot

**DEFERRED this implementation pass, per explicit direction.** Kept below as-designed (the
writeback-clobbering bug is real and must not be forgotten whenever this is picked back up), but
not scheduled into Waves A–D (§7). Prerequisite before resuming: a Florence-2 grounding accuracy
spike for hand/foot box quality, routed to `machine-learning-expert`.

**A correctness bug in the naive design, found during this review — not just a modeling choice.**
`TagPrediction` is keyed `UniqueConstraint("picture_id", "tag")` — no crop dimension. If a picture
has two hand crops and the reviewer accepts one (writes POS for `(picture_id, "malformed hand")`)
then dismisses the other (writes NEG for the *same* row), the second call **overwrites** the
first — last-write-wins, silent flip-flop. `_reverse_review`'s `clear_human_label` on reopen has the
identical problem. A design that just adds a `detection_id` filter to `TagSuggestion` and reuses
accept/dismiss unmodified **will ship this bug**.

**Data model.**
- Reuse `Detection` as-is for crop geometry (bbox JSON, open-vocabulary `label`, `source`
  provenance, JSON `attributes_`). Validates "expose an existing mechanism" — no new bbox table;
  open-vocab `label` means any future region detector just adds a label string.
- New `DetectionLabel`: `id, detection_id FK ON DELETE CASCADE, tag, label_state (POS|NEG),
  label_source, labeled_at`, unique on `(detection_id, tag)`. Genuinely new — the crop-granularity
  sibling of `TagPrediction`. Kept separate specifically because retrofitting a crop dimension onto
  `TagPrediction`'s unique constraint would touch the entire tagger pipeline (smart score, sidecar
  sync, every existing reader).
- `TagSuggestion` gains nullable `detection_id FK ON DELETE SET NULL` — NULL for every existing
  suggestion, fully backward compatible.

**Writeback redesign (the actual fix).** For `detection_id IS NOT NULL` suggestions: (1) write the
crop verdict to `DetectionLabel`; (2) recompute the picture-level aggregate over every
`DetectionLabel` for that picture's detections + tag — POS if any crop POS, NEG only once *all*
crops are decided and all NEG, otherwise leave picture-level state untouched; (3) write that
aggregate — once, from the aggregate, never from the individual event — through the existing
`_set_tag`/`record_human_label` chokepoint. Reopening one crop's decision re-runs the aggregate with
that crop's vote removed, not a blind clear.

**Tag→region mapping.** New `DEFAULT_TAG_REGION_LABELS: dict[str, str]` next to
`DEFAULT_TAG_MERGES` in `db_models/tag.py` — same shape, same editability.

**Detection production.** Trigger a `DetectionTask` batch (prompt = region label) over in-scope
pictures lacking a matching `Detection`, when a review's tag has a `DEFAULT_TAG_REGION_LABELS`
entry. Run async with progress, reusing the `tag_health` rebuild's `building`/`progress` polling
convention.

**Crop image delivery.** Render client-side (CSS/canvas crop) from the existing scope-enforced
`/pictures/{id}.{ext}` using `Detection.bbox` already returned with the suggestion — avoids a new
authz-relevant surface entirely. If a server-side crop endpoint later proves necessary, it's a
picture-scoped route and **must** call `enforce_picture_scope` — flag explicitly, easy to
mistake for "just an image resize."

**Open ML question — route to machine-learning-expert, not resolved here.** Florence-2 grounding's
box quality for "hand"/"foot" prompts is unverified. Recommend a small accuracy spike before
committing to the full buildout.

**Generality.** `Detection.label` open vocabulary, `DetectionLabel` has no hand/foot-specific
columns, `DEFAULT_TAG_REGION_LABELS` is the only place hand/foot strings appear — a two-line dict
edit adds any other region.

---

## 5. Data hygiene: version-pinning + apply the remap

**5a. Version-pinning — straightforward bug fix.** `est_wrong`/`est_missing` queries join
`TagPrediction`/`Tag`/`Picture` with **no `model_version` filter**, while the same function already
computes `current_version` and uses it correctly elsewhere (`has_model` "current" column). Fix: add
`TagPrediction.model_version == current_version` to both queries. No schema change — ship first,
independent of everything else.

**5b. Apply `DEFAULT_TAG_MERGES` — reuse, applied uniformly.** Already exists, already used
correctly by `tag_scan_service.scan_tag`'s `equiv` set. `tag_health_service.py` doesn't use it
anywhere. Fix: remap every `.tag` through `DEFAULT_TAG_MERGES.get(tag, tag)` at grouping time,
**consistently across every signal** in `compute_tag_health_rows` — not just
`est_wrong`/`est_missing` (the part the plan doc names) but also `pred_agg`, `disputes`,
`last_reviewed`/`accepted`/`dismissed`, and `_mismatch_counts`'s tag sets. Partial application would
leave a board where the same tag's signals use inconsistent tag-identity definitions — a subtler
version of the bug this gap fixes. Likely means SQL `GROUP BY` → fetch-then-group-in-Python (cache
rebuild is already an offline job, cost is a non-issue). Verify empirically whether any child tag
carries signal the parent lacks before assuming full suppression is lossless.

**Migration.** None — pure query-layer change in an already-rebuildable cache.

---

## 6. Close the loop — no bespoke export, extend the fetch path pixltagger already uses

Hard-blocked on gap #1 for the "which pictures are frozen ground truth" part; otherwise much
lighter than the plan doc assumed. **Revised after investigating `../pixltagger` directly and per
explicit direction: don't build a new integration surface — pixltagger should not be "involved" in
any new way beyond the API fetch it already does.**

**Why the original "export endpoint mimicking `clean_eval.json`" framing was wrong.** The plan doc
assumed `official_hand_eval.py`/`decide.py` "already consume" a `clean_eval.json`-shaped artifact.
They don't: `decide.py::compare()` reads only pixltagger's **local run registry**
(`runs/index.json`), whose `eval_current` snapshot is trusted only if its `eval_fingerprint`
(SHA256 over the local eval-split sidecar contents) matches the live cache. `clean_eval.py`'s own
docstring says as much: "It mutates no metrics and never affects the verdict gate." So there is
**no consumer on the pixltagger side for a bespoke export shape at all** — building one would add
integration surface pixltagger doesn't need.

**What pixltagger actually needs from PixlStash, and already has 90% of it.** `pixl fetch`
(`legacy/fetch_pixlstash.py`, `PixlStashClient`) already calls `GET /pictures?set_id=` to enumerate
candidates and `POST /pictures/tags/bulk_fetch` to pull current tags — and because PixlStash's
human write-back already flows corrections into the `Tag` table (the "what already works" bullet
in the scoping doc), `bulk_fetch`'s response is already, today, the human-corrected ground truth
for any picture a reviewer has touched. What's missing is purely **discovery**: pixltagger has no
way to ask "which picture ids are the frozen, leak-free, human-verified eval set for tag X" — it
currently answers that with the fragile picture-set-membership convention gap #2 replaces.

**Design: one small id-discovery endpoint, zero new artifact shape.**
- `GET /tag_eval_slices/{tag}/picture_ids?limit=&offset=` — returns just the `ACTIVE` slice's
  `picture_id` list (paginated, don't return unbounded). That's the entire new surface.
- pixltagger's fetch flow becomes: call this to get eval-tag picture ids → pass them into the
  **existing, unmodified** `bulk_fetch_tags` call to get current (human-corrected) tags → route into
  its local `eval/` cache. No new response shape for pixltagger's client to learn; it reuses the
  exact call it already makes for everything else.
- Same shape covers the train side using gap #2's `split` field directly (§2): `GET
  /pictures?split=train` replaces the `train_set` named-picture-set convention. Also zero new
  artifact shape — an additive filter on an endpoint already called.
- `decide.py` needs **no changes at all** — once pixltagger's local cache is built from a correctly
  split, correctly labeled source, its existing fingerprint/bootstrap/critical-tag-weighting gate
  keeps working exactly as today. This is the actual "loop closed": not a new export pixltagger has
  to learn to parse, but PixlStash quietly becoming a more trustworthy version of the same fetch
  pixltagger was always going to run.

**Authz.** `GET /tag_eval_slices/{tag}/picture_ids` — same owner-only vault-wide pattern as #1/#2
(`fetch_scope_allowed_picture_ids`-based; no single resolvable `picture_id` in the route). Smaller
blast radius than the original export design (ids only, no label payload — the labels travel over
the already-reviewed `bulk_fetch_tags` path), but still returns membership of a curated set in
bulk, so it still needs a named `chief-security-officer` sign-off, not an assumption that "it's
just ids, so it's low-risk."

**Generality.** This is the strongest form of the generality requirement in the whole plan: PixlStash
doesn't grow any pixltagger-shaped concept at all. `split` and "frozen eval slice picture ids" are
both product-generic ("which pictures are in the training/eval side of the fence," "which pictures
back this tag's accuracy number") — any other downstream consumer, not just pixltagger, can use the
same two calls.

---

## 7. Sequencing — revised from the plan doc's linear order

The plan doc suggests #1+#2 → #3 → #4 → #5/#6 linearly. The actual dependency graph is narrower:

```
Wave A (ship first — lowest risk, zero new tables, same file, bundle into one PR):
  #5a version-pin fix, #5b remap-fold, #3 precision-discount
  → all three touch only tag_health_service.compute_tag_health_rows / TagHealth.

Wave B (start immediately, longest lead time — the load-bearing guard):
  #2 core: PictureSplit table, component-aware assignment, write-path conflict
  detection/fail-closed exclusion, read-path freeze-time re-check.

Wave C (hard-blocked on B):
  #1: TagEvalSlice/TagEvalSliceItem, freeze action, P/R/F1-or-AP compute (§1's
  decision procedure), board columns.

Wave D (hard-blocked on C for the eval-id endpoint; the `split` filter half only
needs B):
  #6: GET /tag_eval_slices/{tag}/picture_ids, GET /pictures?split=, split field
  on bulk_fetch_tags. Small — reuses B/C's tables entirely, no new artifact shape.

#4 (crop review) is deferred this pass per explicit direction — not scheduled into
any wave. Revisit only after the Florence-2 grounding accuracy spike.
```

#1/#2 grouping from the plan doc is confirmed correct (freezing before split assignment exists
freezes contaminated data). #3 and both halves of #5 do **not** need to wait for #1/#2 — pure
query fixes with zero shared state — gating them behind #1/#2 delays near-zero-risk,
immediately-valuable fixes for no technical reason. #6 is now small enough (§6's revision) that it
rides along with Wave D rather than needing its own separate push. #4, deferred per direction, was
also confirmed technically independent of #1/#2/#3/#6 (different tables, different files) — nothing
about deferring it blocks or is blocked by Waves A–D.

---

## 8. Open questions requiring a decision before backend implementation starts

1. ~~P/R/F1 decision threshold~~ **Resolved** — see §1's decision procedure (AP when uncalibrated,
   disjoint-slice threshold derivation before ever falling back to a flagged 0.5). Gap #4 deferred
   this pass (see below); everything else proceeds.
2. ~~Split ratio/stratification policy for `PictureSplit`~~ **Resolved by `machine-learning-expert`**
   — 80/20 train/eval, stratified by picture set, ratio targeted within each set's near-dup
   components (see §2). Freezing floor (`n_pos ≥ 10`) shared with §1's AP/rederivation floors.
3. ~~Cross-repo pixltagger ingestion~~ **Resolved by direct investigation of `../pixltagger`** —
   see the revised §2/§6: pixltagger already pulls via `POST /pictures/tags/bulk_fetch`
   (`fetch_pixlstash.py`); the design extends that existing endpoint (plus `GET /pictures`) with an
   optional `split` field rather than building any new export artifact. `decide.py`'s actual gate
   reads only pixltagger's local run registry, fingerprinted against its local cache — it needs
   nothing new from PixlStash beyond a correctly-split, correctly-labeled cache, which the extended
   fetch endpoints provide.
4. **Deferred this pass, per direction** — gap #4 (crop review) is out of scope for this
   implementation wave. The Florence-2 grounding accuracy spike for hand/foot box quality remains
   the prerequisite whenever it's picked back up; route to `machine-learning-expert` at that time.
5. **Security sign-off required, not optional** — every new endpoint here needs a named
   `chief-security-officer` review and an explicit coverage-matrix cell before merge, including the
   `split` field additions to `GET /pictures`/`bulk_fetch_tags` (§6) — those two routes use
   different, non-shared authz mechanisms today (see §6), so the new field must be verified against
   each independently, not assumed to inherit a common check.
6. **Verify empirically** whether any `TagSuggestion` rows exist directly against
   `DEFAULT_TAG_MERGES` *keys* before assuming full suppression is lossless (§5b).
7. Every new endpoint here uses the current per-handler opt-in pattern (matching existing
   precedent, per `docs/backend_architecture.md` §16.1, still law today). None of it argues against
   the centralized-chokepoint direction (§16.2) — flag each new route's opt-in check as debt against
   that direction in review, per that doc's own instruction.

---

## 9. Verify — what a reviewer must check before signing off any implementation PR

- #1/#2: a freeze-then-refreeze cycle proves the frozen `label_state` snapshot is immutable across
  a later correction to the live `TagPrediction` row; a deliberately-constructed near-dup pair with
  opposite intended splits proves the write-path conflict guard fires and both sides land
  `NEITHER`, not silently on one side.
- #3/#5: `compute_tag_health_rows` output on a fixture with mixed model versions and at least one
  `DEFAULT_TAG_MERGES` child tag shows old-version rows excluded and the child folded into the
  parent's counts, not just in `est_wrong`/`est_missing`.
- #4 (deferred): when resumed, a fixture with two crops on one picture, accepted/dismissed in both
  orders, must prove the picture-level ledger reflects the OR-aggregate and not the last-written
  crop event; reopening one crop's decision must leave the other crop's contribution intact.
- #6: `GET /tag_eval_slices/{tag}/picture_ids` returns only ids (no label payload); feeding those
  ids into the existing `bulk_fetch_tags` returns the same tags a human reviewer would see in the
  product right now — no separate/parallel label representation was introduced.
- Every new/changed route: the coverage-matrix cell (state a/b, mechanism, sign-off) is present in
  the PR description before merge — an empty cell blocks it.
