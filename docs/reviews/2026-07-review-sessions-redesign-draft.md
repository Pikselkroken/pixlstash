# Review sessions redesign — draft

**Status:** draft for discussion · 2026-07-15
**Replaces (conceptually):** the current "Review tags" overlay (`ReviewFixesOverlay.vue`, `useReviewFixesStore.js`) and its hidden queue.
**Design mock:** `ui_kits/app/review-sessions.html` in the Claude Design project (next to the earlier `review-tags.html` proposal, which this supersedes).

## Goal

Help users review **one tag at a time, very quickly**, in service of **correct labels** — not label
consistency. Consistency between similar images is a signal the *detector* uses to find suspects;
it is not something the user should be asked to produce by hand.

## Why the current system feels illogical

Diagnosis of the shipped overlay (see `tag_scan_service.py`, `tag_suggestion_service.py`,
`useReviewFixesStore.js`):

1. **The workflow has no noun.** There is a scan (invisible), a queue (invisible, destroyed and
   rebuilt every time a tag is picked — `selectOrScan` → `POST /tag_suggestions/scan` deletes all
   PENDING rows for the tag), and a one-card-at-a-time stack (the only visible thing). Users
   cannot reason about a workflow with no object to point at.
2. **"All caught up" conflates three different situations** — the scan found nothing, everything
   eligible was already reviewed once, or the scope filters hid everything — behind one message
   whose trigger is simply "the client-side items array is empty".
3. **Reviewed-once = suppressed forever.** The rescan skips any picture that has *any* prior row
   for the tag (`tag_scan_service.py:201-211`). No decay, no re-surfacing, no visible history.
4. **Two systems wear one UI.** The near-neighbour scan picks *which pairs appear*; the tagger's
   `TagPrediction` confidences produce the four-corner verdict and "likely" recommendation,
   computed client-side. The user is never told these are independent signals.
5. **The pair is a detection mechanism that leaked into the UI.** `min_twin_sim = 0.85` in CLIP
   space is "another beach photo", not "near-duplicate" — yet the never-flagged right-hand image
   gets a co-equal verdict, and the four-corner question (L/B/N/R) is several times slower than a
   binary one. The "swap" corner is only meaningful for actual copies.

## The new model

Three questions, three surfaces, no hidden state:

| Question | Surface |
|---|---|
| What should I review? | **Tag health board** (landing view) |
| What am I reviewing? | **Review sessions** in a vertical rail |
| Decide fast | **Binary cards** (+ pair cards for true versions only) |

### 1. Tag health board (landing view)

Instead of opening to a tag picker, the overlay opens to a ranked table of tags. All signals are
cheap SQL over `tag_prediction` + `tag` — no embeddings, no kNN (that stays reserved for review
creation):

| Signal | Definition (per tag) | Notes |
|---|---|---|
| **Est. missing** | count of pictures with prediction confidence ≥ ~0.9 and no `Tag` row | primary workhorse |
| **Est. wrong** | count of tagged pictures with prediction confidence ≤ ~0.1 | primary workhorse |
| **Historical overturn rate** | fraction of past `TagSuggestion` outcomes that were ACCEPTED (vs DISMISSED) | makes ranking honest |
| **Expected corrections** | (est. wrong + est. missing) × overturn rate | **the ranking key**; cold start: raw disagreement count |
| **Verification coverage** | fraction of the tag's pictures with `label_state = UNKNOWN` | "nobody ever looked" |
| **Model-disputes-human** | `label_source = 'human'` but current model version strongly disagrees with the frozen `label_confidence` snapshot | surface as a separate small count; never auto-requeue (human outranks model) |
| **Boundary mass** | fraction of predictions in ~0.35–0.65 | flags ambiguous tag *definitions*, where the fix is "clarify the tag", not "review more" |
| **Duplicate conflicts** | same-`PictureStack` (or dhash-near) pairs with differing tags | small counts, near-100 % precision; feeds the pair-card queue |

Each row shows a plain-language reason ("41 high-confidence predictions lack this tag") and a
**Start review** button. Smart-score penalised tags keep their red flag. A tag with an open
review shows **Open →** instead (greyed for creation — one open review per tag).

### 2. Reviews are first-class sessions

A **Review** = one tag + optional scope + one scan's results, **frozen at creation**.

- **Creation is explicit.** "New review" → pick tag (open tags greyed) → optional scope → the
  near-neighbour scan runs **once**, and its report becomes the review's cover sheet: *"Scanned
  4,812 pictures · 23 suspects · 61 handled in earlier reviews."*
- **Prior suppression becomes a visible choice.** The creation dialog shows the count of pairs
  handled in earlier reviews with an "include previously reviewed" toggle (default off). Same
  protection as today's permanent suppression, zero mystery, and a re-surfacing path at last.
- **Vertical rail** (like claude.ai sessions): each open review is a tab with tag name, progress
  (9/23), scope hint. Switching tabs just switches — nothing rescans, nothing is destroyed.
- **Scope is frozen at creation.** Want a different scope? That's a different review. In
  exchange, a review can always be **completed or aborted** — abort discards the session (already
  -made decisions stand; they were written through on each card).
- **Refresh appends, never rebuilds.** An explicit refresh (or the passive *"vault changed since
  this scan — refresh?"* staleness hint) adds newly-found pairs with a "new" badge; decided pairs
  are never resurrected within a review.
- **Done is a real state:** "23/23 reviewed — 9 removed, 5 added, 9 kept" → Archive or Re-scan.

### 3. The review unit is a single picture with a binary question

- One image, one question: **"Is this 'sunset'?"** Yes / No / Skip / Undo. Sub-second decisions;
  the L/B/N/R four-corner apparatus is deleted.
- **The twin is demoted to evidence — and widened.** Instead of one co-equal "twin", show the
  neighbourhood vote itself: a zoomable strip of the k nearest neighbours, each with a small
  tag/no-tag badge — *"Similar images — 2 of 12 have 'sunset'"*. The opaque `pos_frac` becomes
  something you can see, and no thumbnail in the strip ever demands its own verdict.
- If an answer implies a neighbour is now suspect, that neighbour **enqueues as its own card**
  (the two-for-one survives as detection, not as a forced joint decision).
- The tagger signal finally fits: one image, one confidence — "tagger: 86 % sure it is".
- **Detail tags** (watermark, blurry, malformed hands…) don't survive thumbnailing — so no
  grid-triage default. The existing Grad-CAM endpoint (`/pictures/{id}/anomaly_region?tag=`) lets
  the card show the *evidence region* the model saw. A denser grid view is at most a per-review
  opt-in for scene-level tags, later.

### 4. Pair cards only for true versions

A pair card appears **only** when two images are versions of one shot: same `PictureStack`, or
dhash-Hamming within the existing threshold (today this silently swaps the *displayed* twin;
here it becomes an explicit card type). Copy: *"These are versions of the same shot with
different labels."* Verbs: Both / Neither / Swap — the only context where "swap" means anything.

## Backend changes (modest)

- New `review` table: id, tag, scope JSON, status (OPEN/ARCHIVED/ABORTED), scan stats
  (scanned/found/prev_reviewed), created_at, refreshed_at.
- `TagSuggestion` gains `review_id` FK; suggestions belong to the review whose scan created them.
- Scan writes into a review instead of deleting global PENDING rows; the destructive
  delete-and-rebuild in `tag_scan_service._write` goes away, as does the permanent
  `reviewed_pids` suppression (replaced by the per-review include toggle).
- Endpoints: create/list/get/refresh/archive/abort review; list suggestions per review. The
  per-item actions (accept/dismiss/fix-twin/swap/reopen) and human-label-ledger writes are
  untouched.
- Health board endpoint: per-tag aggregate of the signal table above (indexed queries on
  `tag_prediction`; consider a nightly cache if vaults are large).
- Bulk auto-resolve becomes part of the creation receipt: "14 obvious pairs — auto-resolve?"
  using the existing dry-run preview.

## What gets deleted

- The four-corner decision UI and bucket-grouping logic (`decision()`, `BUCKET_ORDER`,
  `bucketRank` in `useReviewFixesStore.js`).
- `selectOrScan`'s scan-on-every-pick and the delete-and-rebuild in the scan service.
- The permanent reviewed-picture suppression.
- The hidden `direction` plumbing in the UI (direction becomes visible card framing:
  "probably wrong" vs "probably missing").
- The ambiguous "All caught up" empty state (replaced by explicit completion/empty-scan states).

## Decisions — 2026-07-15

- **Health board cost:** cache the per-tag aggregates and show a **progress bar while (re)building**
  the cache; a bounded build time is acceptable. Signals refresh after tagger runs / imports.
- **Board design locked** (from the tweaks panel in the mock): **compact** density, **icon** anomaly
  marker, **heat** health bar, **"Why it ranks here" shown**, **wide** table width.
- **Feasibility findings accepted** (see mock + session notes): neighbour ids captured at scan time
  into a JSON column on `tag_suggestion`; "include previously reviewed" re-parents existing rows
  into the new review (keeps `UNIQUE(picture_id, tag, source)`); "Mismatch" = same-stack pairs +
  stored `PictureLikeness` pairs, never a live O(N²) sweep; tags outside the tagger vocabulary get
  an explicit **"no model signal"** row state (kNN review still available).
- **Gamified review ("Pretend this is fun")**: opt-in checkbox in the decision row. While on,
  decisions earn celebration effects **and varied sticker rewards** on a variable-ratio schedule
  (first decision after enabling always awards; then every 2–5). Stickers **reuse the Picture Set
  icon + colour palette** (`frontend/src/utils/setAppearance.js` → `SET_ICON_CATEGORIES` /
  `SET_COLORS`; the implementation imports that module so sets and stickers never drift), restyled
  as die-cut stickers (white edge, gloss, tilt). Award animation: pop over the card → fly to the
  rail → land with a bounce in a **sticker shelf** at the bottom of the sidebar, which is capped
  (~1/3 height, scrolls, stickers shrink as the collection grows) so it always yields space to
  navigation. Rewards are never clawed back by Undo.

## Rulings — 2026-07-15 (post UX audit)

The ui-ux-expert audit (3 blockers, 12 should-fixes) was applied with these overrides:

- **Skip = no decision, out of the queue.** A skip removes the item from the review permanently
  with nothing written to tags or the ledger (new `SKIPPED` suggestion status +
  `POST /tag_suggestions/{id}/skip`; `reopen` works on skipped rows). Progress and receipts count
  skipped separately: `{removed, added, kept, skipped}`. No rotation, no "revisit" loop.
- **Full keyboard/focus model** (ported from the old overlay) — confirmed.
- **Decision bar keeps constant positions and always shows an Undo button.**
- **XP/streak DO decrement on undo** (net counters — the audit's monotonic-counter recommendation
  is rejected). Celebrations still never fire on undo; stickers are never clawed back.
- **Evidence region:** H toggle (heatmap + boxes, persisted preference) — confirmed.
- **Manual tagging:** visible overlay button bottom-left on the card image + `T` shortcut
  (TbTagPanel escape hatch).
- **"Abort" keeps its name** ("Discard remaining" rejected). Aborting shows a dialog:
  *"You made N changes in this review"* → **Keep N changes** / **Undo N changes** (review-scoped
  bulk-reopen) / Cancel.
- **Board column renamed "Est. fixes"** (estimated fixable labels = wrong + missing + mismatches),
  number beside an absolutely-scaled heat bar, color legend in the footnote.
- **Health board cache** may take visible time as long as a progress bar shows while it builds.

## Open questions

- Threshold choices for the board signals (0.9/0.1 disagreement band, 0.35–0.65 boundary band)
  — start fixed, calibrate later against overturn rates?
- Staleness detection: compare review.created_at/refreshed_at against last import + last tagger
  run, or a vault-level change counter?
- Are reviews per-user or per-vault? (Multi-user vaults: probably per-vault with a creator field.)
- Retention: keep archived reviews forever (they're the audit trail the health board's overturn
  rate feeds on) or prune?
- Does `impossible_tag` (second suggestion source, currently unwired in the UI) become just
  another review type/source on the same session model?
