# The Tagger Flywheel — PixlStash × PixlTagger implementation plan

**Status:** Implementation plan (actionable) · **Date:** 2026-06-15 · **Owner:** Gaute (solo)
**Companion to:** the [data-centric refinement thinking note](2026-06-data-centric-tagger-refinement.md) (the *why* and the *what*) and the [Next-Big-Thing options note](2026-06-next-big-thing-lora-vs-pipelines.md). This doc is the *how* — grounded in what the two codebases actually do today.

This turns the thinking note into a build sequence. It is opinionated about ordering and about what **not** to build. Read the TL;DR, then "the one finding that reframes everything," then the build sequence.

---

## TL;DR

1. **You are not building a pipeline framework. You are connecting two systems that each already do ~70% of the job.** PixlStash already has the task/plugin engine, the per-`(image,tag)` prediction store with confidences and a review status, CLIP embeddings with cosine similarity, and a confirm/reject UI. PixlTagger already has the train→eval→gate harness, per-tag F1, and two label-error miners. The work is the **connective tissue and the writeback path**, not the engine.
2. **The flywheel is one orchestrated loop that crosses both apps.** PixlStash is the system of record + the review surface; PixlTagger is the trainer + the error-detector. Each turn, the *new, better* model mines the suspects for the next turn — that's the self-improvement you asked for.
3. **Two new pieces unlock everything:** (a) a **writeback path** so PixlTagger's label-error suspects land back in PixlStash as a review queue, and (b) a **golden eval set** marked in PixlStash so the gate measures signal, not noise. Build these two first; almost everything else already exists.
4. **Dogfooding is nearly free here** because the review surface is just the existing tag-prediction confirm/reject UI pointed at a new source of suggestions. The internal tool *is* the product feature.
5. **The loop runs in two modes on the same machinery: *refine* a tag and *grow* the tag space.** The bigger Smart Score win isn't only cleaner versions of today's 12 tags — it's *more* tags (more anomaly types, and positive/quality tags too). A richer label space makes the score continuous instead of binary, and **dilutes every individual label error** (one tag's mistake is a smaller fraction of the aggregate), which directly relaxes the rare-class noise problem. Expanding the vocabulary is itself a robustness strategy, not just a feature.
6. **Sequence:** golden set → cold-start near-neighbor scan (PixlStash-native, model-independent) → one gated *refine* loop by hand → wire the orchestrator (`pixl refine`) → use the same machinery in *expand* mode to add tags → evolve Smart Score from a few penalties to a calibrated multi-tag blend. Concrete-first, generalize last.

---

## The one finding that reframes everything

The thinking note worried about "how pipelines work with ComfyUI" and about building a plugin system. **Both worries are already solved in the codebase:**

- **PixlStash's plugin/pipeline system exists.** [`work_planner.py`](../../pixlstash/pixlstash/work_planner.py) continuously polls registered **task finders** ([`tasks/`](../../pixlstash/pixlstash/tasks/)); each finder (`BaseTaskFinder.find_task()`) looks for work and emits a batched task; [`task_runner.py`](../../pixlstash/pixlstash/task_runner.py) runs them on a CPU queue + a single-threaded GPU queue with VRAM budgeting. **A "plugin" in PixlStash is a `BaseTaskFinder` + a `Task`.** `MissingTagFinder → TagTask`, `MissingImageEmbeddingFinder → ImageEmbeddingTask`, `MissingSmartScoreFinder → SmartScoreTask` are the existing examples. You add a pipeline stage by adding a finder. That's it.
- **The tagger already runs inside PixlStash**, inline (not subprocess/HTTP): [`tagger_plugins/pixlstash_tagger.py`](../../pixlstash/pixlstash/tagger_plugins/pixlstash_tagger.py) loads the ConvNeXt anomaly model; [`inference/workflows/tagging.py`](../../pixlstash/pixlstash/inference/workflows/tagging.py) runs it; [`tasks/tag_task.py`](../../pixlstash/pixlstash/tasks/tag_task.py) persists raw sigmoid outputs as `TagPrediction` rows.
- **The prediction store is already a review queue substrate.** [`db_models/tag_prediction.py`](../../pixlstash/pixlstash/db_models/tag_prediction.py): `(picture_id, tag, confidence, model_version, status ∈ {PENDING,CONFIRMED,REJECTED}, predicted_at)`. The confirm/reject endpoints and UI already exist ([`services/tag_prediction_service.py`](../../pixlstash/pixlstash/services/tag_prediction_service.py), [`OverlayTagsPanel.vue`](../../pixlstash/frontend/src/components/views/OverlayTagsPanel.vue), bulk patterns in [`TbTagPanel.vue`](../../pixlstash/frontend/src/components/panels/TbTagPanel.vue)).

So strip ComfyUI and "framework" out of your head entirely. The internal refinement loop is: **finders that produce review suggestions → a human reviews them in the existing UI → fixes write to the labels (system of record) → PixlTagger re-fetches and retrains → the gate keeps or reverts → repeat.** Most of those arrows already exist in code.

---

## The architecture in one picture

```
        ┌──────────────────────────── PixlStash (system of record + review) ───────────────────────────┐
        │  Pictures · Tags · TagPrediction(conf, model_version, status) · CLIP image_embedding          │
        │  WorkPlanner + TaskFinders (the "plugin/pipeline" engine) · TaskRunner (CPU/GPU queues)        │
        │                                                                                                │
        │   [NEW] NearNeighborDisagreementFinder ──┐                                                     │
        │        (model-independent cold-start)    ├──▶  TagSuggestion queue  ──▶  Review UI  ──▶ Tags    │
        │   [NEW] ingest endpoint  ◀───────────────┘        (reuse confirm/reject confirm flow)          │
        └───────────────▲─────────────────────────────────────────────────────────────────┬────────────┘
                        │  POST suspects (writeback)                  GET labels (fetch)    │
                        │                                                                   ▼
        ┌───────────────┴──────────────────────────── PixlTagger (trainer + error detector) ────────────┐
        │  fetch_pixlstash.py → cache → finetune.py (ConvNeXt, per-tag F1, thresholds)                   │
        │  decide.py (accept/reject gate vs GOLDEN eval set) · state.py (run registry)                   │
        │  mine_weak_label_candidates.py (confident-learning-lite) · build_active_learning_queue.py      │
        │  [NEW] pixl refine (the orchestrator) · [NEW] push-suspects client · [NEW] temp-scaling calib  │
        └────────────────────────────────────────────────────────────────────────────────────────────────┘
```

PixlStash owns the *truth* and the *human attention surface*. PixlTagger owns *training* and *finding what's probably wrong*. The two new arrows (writeback POST, golden-set fetch) plus one new finder are the spine of the flywheel.

---

## What exists vs. what to build (the honest gap table)

| Capability | Status | Where |
|---|---|---|
| Plugin/pipeline engine (finders + tasks + runner) | ✅ exists | `work_planner.py`, `tasks/`, `task_runner.py` |
| Tagger runs inline, dumps raw sigmoids per (image,tag) | ✅ exists | `tag_task.py`, `tagger_plugins/pixlstash_tagger.py`, `tag_prediction.py` |
| Confirm/reject review UI + endpoints | ✅ exists | `tag_prediction_service.py`, `OverlayTagsPanel.vue` |
| Bulk tag fetch / multi-image review patterns | ✅ exists | `routes/tags.py` `bulk_fetch`, `TbTagPanel.vue` |
| CLIP image embeddings + cosine similarity in SQL | ✅ exists | `picture.py` (`image_embedding`, `semantic_search`, `cosine_similarity` UDF) |
| Near-duplicate / likeness pairs | ✅ exists | `PictureLikeness`, `LikenessTask` |
| Train→eval→accept/reject gate, per-tag F1, run registry | ✅ exists | `decide.py`, `metrics.py`, `state.py`, `finetune.py` |
| Label-error miner (confident-learning-lite, both directions) | ✅ exists | `mine_weak_label_candidates.py` |
| Active-learning ranker (uncertainty/missed/extra/rare) | ✅ exists | `build_active_learning_queue.py` |
| Dataset fetch from PixlStash → cache | ✅ exists | `fetch_pixlstash.py` |
| **Golden eval set (hand-verified, marked in PixlStash)** | ❌ **build** | new flag/collection in PixlStash + fetch support |
| **Near-neighbor disagreement finder (cold-start signal)** | ❌ **build** | new `BaseTaskFinder` over `image_embedding` |
| **`TagSuggestion` queue (suspects, with direction + source + reason)** | ❌ **build** | new model + endpoints, or extend `TagPrediction` |
| **Writeback: PixlTagger suspects → PixlStash queue** | ❌ **build** | new REST endpoint + push client |
| **Orchestrator: one `pixl refine` session** | ❌ **build** | new CLI command wrapping the loop |
| **Temperature-scaling calibration on golden set** | ❌ **build** | small module; improves Smart Score for free |
| Per-image embedding export / OOF k-fold predictions | ⚠️ optional | not needed for cold-start; add when graduating to Cleanlab |

**The ❌ rows are the whole project.** Six new pieces, three of them small. Everything else is reuse.

---

## The self-improving flywheel (your follow-up, made concrete)

You want *one streamlined session* to produce a ConvNeXt iteration that helps produce an even better one. Here is the exact mechanism and why it compounds.

**One turn of the flywheel — `pixl refine --tag <worst-tag>`:**

1. **Pull truth.** `pixl fetch` → current labels from PixlStash into the cache (already exists).
2. **Train candidate.** `pixl train` → `run-N+1` (ConvNeXt). Produces per-tag F1, thresholds, sigmoids (already exists).
3. **Gate against the golden set.** `pixl evaluate` + `decide.compare` ([`decide.py`](../../pixltagger/src/pixl/decide.py)) vs the *hand-verified golden eval set* (the one new dependency). Verdict ∈ {improved→promote, mixed→investigate, regressed/no-change→hold}. **This guarantees monotonic improvement** — bad data batches get reverted, not shipped.
4. **Mine suspects with the best model available.** Run three rankers for the target tag:
   - **Confident-learning-lite** — `mine_weak_label_candidates.py`: confident false-positives and false-negatives, both directions.
   - **Version-disagreement** — diff `run-N+1` vs `run-N` predictions; flips relative to the label are high-value (a signal you already generate for free).
   - **Near-neighbor disagreement** — *model-independent*, runs in PixlStash over CLIP embeddings (the cold-start; see below). Catches the rare-class **false negatives** the model alone misses.
5. **Push suspects back into PixlStash** as `TagSuggestion` rows (image, tag, direction add/remove, reason, score, source, model_version) via the new ingest endpoint.
6. **Human reviews** the ranked queue in PixlStash — one tag, worst-first, batched, fast ("missing?" vs "wrong?"). Accept/reject writes to the labels (system of record). This is the only irreducibly human step, and it gets faster every round as definitions firm up.
7. **Loop.** Back to step 1. The labels are now cleaner; the next model is better; the next mine is sharper.

**Why it compounds (the actual self-improvement):**
- **The detector improves with the model.** Step 4 uses `run-N+1`. A better model produces a better-ordered suspect queue → more real errors fixed per minute of your attention next round. Model v_{n+1} surfaces errors v_n literally could not see.
- **The cold-start signal breaks the circularity.** The near-neighbor scan doesn't depend on the model, so it keeps feeding rare-class false negatives even when the model is still too weak to trust — exactly the recall hole the note flags.
- **The gate makes it safe to be aggressive.** Because every batch is gated against the golden set, you can apply big batches of fixes and let the gate revert the ones that hurt. No fear of poisoning the model.
- **Churn-vs-gain tells you when to stop a tag.** Track labels-changed vs golden-F1-gain per turn; when churn stays high but F1 flattens, lock the tag, move to the next worst. That's your dashboard.

The "streamlined session" is the orchestrator collapsing steps 1–5 into one command, then a focused review session (step 6), then re-running it. Over a week that's several turns on one tag; over a month it's the worst several tags cleaned and a measurably better tagger — built by the tagger.

---

## Beyond cleaning: growing the tag space (and making Smart Score less binary)

This is the bigger prize, and it uses the same loop with one extra bootstrap step.

### Two modes of the same machinery

- **Refine mode** — clean an *existing* tag (the loop above). Improves the fidelity of tags you already have. Model-based mining works because the tag already has labels.
- **Expand mode** — add a *new* tag (anomaly **or** positive/quality). Grows the resolution of the score. Model-based mining can't help at first (zero labels), so the cold-start is different — you bootstrap from embeddings.

You want both because they attack the Smart Score from opposite ends: refine makes each existing signal truer; expand makes the score a blend of many signals instead of a few loud ones.

### Why "more tags" is a label-noise strategy, not just a feature

Today the score leans on a handful of penalised anomaly tags (`DEFAULT_SMART_SCORE_PENALIZED_TAGS` in [`db_models/tag.py`](../../pixlstash/pixlstash/db_models/tag.py)), so it behaves close to binary: one strong anomaly tag dominates, and one mislabel swings the result hard. Make the score a weighted blend over *K* tags and the math changes:

> With 12 tags, a single bad-anatomy flip is ~1/12 ≈ **8%** of the signal. With ~60 tags it's ~**1.5%**. The aggregate stays stable even when individual rare tags are imperfect.

So **expanding the vocabulary dilutes every individual label error** — it's the cheapest mitigation for the exact rare-class-recall hole the thinking note flags. It also makes auto-reject graded instead of a cliff, and gives users a richer, more legible score ("why was this rejected?" → a profile across many tags, not one verdict).

### Positive tags, not just anomalies

Add quality-positive tags ("sharp focus", "natural skin texture", "coherent background", "good hands", "correct lighting") alongside more anomaly types. Positives are harder to label (presence-of-good vs absence-of-bad), so start with the few that are visually unambiguous and high-signal. They give the score something to reward, not only punish — which is what turns a defect detector into a quality score.

### Cold-start for a brand-new tag (the expand-mode bootstrap)

A new tag has ~0 labels, so the model-based miners are useless until it has some. Bootstrap with embeddings (this is label *propagation* — technique #2 in the thinking note — used for *creation*, not correction):

1. **Seed** — hand-label 20–50 clear positives (and a few clear negatives). Find candidates fast via PixlStash's existing semantic/text search for the concept.
2. **Propagate** — kNN over CLIP `image_embedding` from the seeds → ranked candidate positives → `TagSuggestion` queue (the same queue, `source=propagation`).
3. **Review** — accept/reject the propagated candidates until the tag has enough labels to train (and enough golden examples to gate).
4. **Train** — `finetune.py` builds the classifier head from the label vocabulary automatically and already handles imbalance (iterative stratification + capped `pos_weight`), so *adding a tag is mechanically just adding labels to the data*. No architecture work.
5. **Gate** — does adding the tag improve the score / not regress the others? Add the new tag's golden examples to the eval set so it's measured honestly from birth.
6. **Graduate** — it's now an existing tag; switch to refine mode.

The expensive part of a new tag is getting its first labels cheaply — which is exactly what embedding propagation buys you.

### Evolving Smart Score: from a few penalties to a calibrated multi-tag blend

Today [`smart_score_task.py`](../../pixlstash/pixlstash/tasks/smart_score_task.py) computes cosine similarity to good/bad embedding anchors, then penalises a few quality tags. Target state:

- Add a **tag-driven quality term** = weighted sum of *calibrated* tag confidences — anomaly tags negative, positive tags positive — over the whole vocabulary.
- **Blend** it with the existing embedding-anchor term, and shift weight toward the tag term as the vocabulary grows and cleans. (Keep the anchor term: it's your model-independent backstop, same role as near-neighbor in the cleaning loop.)
- This makes **calibration load-bearing, not optional**: you're now summing soft scores across many tags, so they must be comparable. Temperature-scaling on the golden set (cheap, no retrain) is what makes a blend of 60 tag confidences meaningful. This is why calibration moves up in priority the moment you commit to the tag-driven score.

### Sequencing: refine first, then expand

Prove **refine mode** on the single worst existing tag first — it validates golden set + gate + queue + writeback end-to-end at the lowest risk (the tag already has labels, so every piece is exercised). *Then* point the same machinery at **expand mode**, because that's where the Smart Score payoff is but it carries more unknowns (new taxonomy, seed quality, propagation trust). Don't expand into an unproven pipeline.

---

## Build sequence

### Phase 0 — Golden eval set (do this first; nothing is honest without it)
- In PixlStash, mark a hand-verified set: simplest is a dedicated **project/collection** (Picture already has `project_id`) or a reserved tag like `__golden`. Hand-clean ~300 images for the single worst anomaly tag.
- Teach `fetch_pixlstash.py` to pull that set as the frozen `eval/` folder (it already supports an `eval_set` list in [`pixlstash.json`](../../pixltagger/pixlstash.json)). Point `decide.py`'s eval at it.
- **Output:** the gate now measures signal. This is the highest-leverage, most-skipped step.

### Phase 1 — Cold-start near-neighbor scan (PixlStash-native, model-independent)
- New `NearNeighborDisagreementFinder(BaseTaskFinder)` + `NearNeighborTask`: for the target tag, use existing `image_embedding` + `cosine_similarity` (and/or `PictureLikeness`) to find visually near-identical pairs/clusters that **disagree** on that tag. Emit `TagSuggestion` rows (direction inferred from which twin has the tag).
- This is the cheapest real win and proves the queue end-to-end without touching training. Borrow fastdup's *idea* (embedding-based near-dup label disagreement); you already have the embeddings, so don't take the dependency.
- **Output:** a populated suggestion queue from a signal you can trust on day one.

### Phase 2 — `TagSuggestion` queue + minimal review surface
- New `TagSuggestion` model (or extend `TagPrediction` with `source` + `direction` columns — decide based on whether you want suspects mixed with live predictions; a separate table is cleaner). Endpoints: list (ranked, filter by tag), accept (writes Tag), reject (marks dismissed).
- First review surface can reuse the existing confirm/reject affordances; a dedicated "Review Suggested Fixes" view comes in Phase 5.
- **Output:** you can review and apply fixes in-app; closes the human half of the loop.

### Phase 3 — One gated loop, by hand (end-to-end once)
- Review the Phase-1 queue, apply a batch, `pixl fetch && pixl train && pixl evaluate`, run `decide.compare` vs golden. Record the F1 delta. Keep or revert.
- **Output:** proof the whole loop works once. This is the "Day 2" of the thinking note, now grounded in real commands.

### Phase 4 — Writeback + the `pixl refine` orchestrator (the flywheel)
- New push client in PixlTagger: POST `mine_weak_label_candidates.py` output (and version-disagreement diffs) to the PixlStash ingest endpoint as `TagSuggestion` rows with `source=model`.
- New `pixl refine --tag <t>` command wrapping fetch→train→evaluate→decide→mine→push, with the churn-vs-gain numbers printed at the end.
- **Output:** one command per turn. The self-improving loop is now real; each run uses the freshly-trained model to mine the next round.

### Phase 5 — Promote to a product surface + calibration
- Build the dedicated "Review Suggested Fixes" UI (reuse `TbTagPanel` layout: queue, one-tag filter, batch accept/reject, direction-labelled). This is the PixlStash dataset-curation feature users asked for — same build, internal + product.
- Add temperature-scaling calibration on the golden set (cheap, no retrain) so tag confidences are comparable across tags. Optional while the score is still anchor-based; **load-bearing once Phase 7 lands.**
- **Output:** the dogfood tool becomes a shippable differentiator; calibrated confidences ready for the tag-driven score.

### Phase 6 — Expand mode: add tags via embedding propagation
- Add `source=propagation` to `TagSuggestion`; add a "seed → propagate" path: hand-label 20–50 seeds for a new tag, kNN-propagate over `image_embedding`, review the candidates in the same queue. Re-use everything from Phases 1–5.
- Pick the first new tags for high signal and easy labelling (a couple of new anomaly types + one or two unambiguous positives). Add their golden examples.
- **Output:** the vocabulary grows through the same loop; each new tag is gated on arrival.

### Phase 7 — Tag-driven Smart Score
- Add a calibrated multi-tag quality term to [`smart_score_task.py`](../../pixlstash/pixlstash/tasks/smart_score_task.py) (weighted sum of calibrated confidences; anomaly negative, positive positive) and blend it with the existing anchor term, shifting weight toward tags as the vocabulary cleans. Reuse/extend the existing weight vocabulary (`DEFAULT_SMART_SCORE_PENALIZED_TAGS`).
- **Output:** Smart Score is continuous, robust to single-tag noise, and explainable — the binary feel is gone.

---

## What to deliberately NOT build (yet)

- **No general pipeline/DAG framework.** The finder pattern is your pipeline. Add finders, not abstractions. Generalize only after the concrete loop has earned it.
- **No ComfyUI in this loop.** Distribution concern; out of scope until the internal loop works.
- **No Cleanlab / FiftyOne dependency on day one.** Your near-neighbor + `mine_weak_label_candidates.py` cover cold-start and model-based both-direction mining. Graduate to Cleanlab (needs OOF k-fold prediction export, a new piece) only when you want the rigorous version and the model is clean enough to trust. FiftyOne is worth it for a one-time big visual cleanup, not as permanent infra — and dogfooding argues for building the review surface in PixlStash anyway.
- **No influence functions / TracIn.** Park it, per the note.
- **No per-flip retraining.** Always batch; always gate.

---

## Decisions to make before Phase 2

1. **`TagSuggestion` as a new table vs. extending `TagPrediction`.** New table keeps model-suspects separate from live predictions and avoids overloading the `status` field; extending reuses the existing UI faster. *Lean: new table, because direction + source + reason don't fit `TagPrediction` cleanly and you'll want suspects to outlive any single model_version.*
2. **Golden set marker: project vs. reserved tag vs. collection.** *Lean: a project/collection, so it's visible and curatable in-app and trivially fetchable.*
3. **Review-first surface: reuse confirm/reject vs. build the dedicated view now.** *Lean: reuse for Phases 1–4 to validate the loop; build the dedicated view in Phase 5 once you know the review ergonomics you actually want.*

## Open questions (carried from the thinking note, now pointed at code)

- How is **Smart Score** derived today, and how far do we shift it toward tags? It lives in PixlStash ([`tasks/smart_score_task.py`](../../pixlstash/pixlstash/tasks/smart_score_task.py)) and currently rides on embedding anchors + a few penalised tags, *not* raw tagger confidences. Phase 7 deliberately reverses that — adding a calibrated multi-tag term and shifting weight toward it as the vocabulary grows. Open question is the *blend ratio over time* and whether to keep the anchor term as a permanent backstop (lean: yes). This is also what determines how many new tags are "enough" to feel non-binary — probably a few dozen, not 12.
- Which **single anomaly tag is worst** right now? `decide.py`/`metrics.py` read per-tag F1 from `test_per_class.tsv` — but on the *current* (noisy) eval set. The golden set produces the first honest read; pick the worst tag from it, not from the old split.
- Are the **CLIP embeddings good enough** for near-neighbor on anomaly tags specifically? They're tuned for semantic/visual similarity, not anomaly discrimination — sanity-check the first near-neighbor batch before trusting the ranking. If weak, the tagger's own penultimate features (a small export from `finetune.py`) are the fallback.

---

## Why this is worth doing now

- **It's pure execution you own** — no Ostris, no ComfyUI, no upstream merge (the things parking half the [portfolio](../portfolio/README.md)). Zero external blockers.
- **It reuses ~70% of two existing codebases** — the riskiest parts (training harness, gate, embeddings, review UI, task engine) already exist and are in production.
- **It is the foundation under both Next-Big-Thing paths** — better tags → better tagger → better Smart Score → better auto-reject → better pipelines *and* better LoRA quality-gates.
- **The internal tool and the product feature are the same build** — you dogfood your own tagger and ship the dataset-curation differentiator users asked for, in one effort. Immich/Eagle can't surface a model's own errors for review; you can.
