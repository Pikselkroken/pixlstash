# Data-Centric Tagger Refinement — thinking it through

**Status:** Thinking note (for Gaute, to read later) · **Date:** 2026-06-15 · **Owner:** Gaute (solo)
**Context:** companion to the [Next-Big-Thing options note](2026-06-next-big-thing-lora-vs-pipelines.md) and the in-flight PixlTagger training-pipeline CLI ([portfolio register](../portfolio/README.md)). Prompted by the real bottleneck: ~10k images with mediocre anomaly tags, where better labels → better tagger → better Smart Score → better everything downstream.

This is me thinking the problem through properly so you have something concrete to act on. It's opinionated. Read the TL;DR, then the "first 3 days" plan; the rest is the reasoning and the deeper angles.

---

## TL;DR

1. **You don't fix labels by retraining per tag-flip.** You let the model *rank* which labels are probably wrong, review the top of the list, fix in batches, and re-gate. The whole game is **ranking under a human-time budget.**
2. **Cold-start the ranking with a model-independent signal** (near-neighbor disagreement in your existing embedding space) so you're not trusting a model that was itself trained on noise. Then bring in model-based signals (confident learning, margin tracking) as the model gets cleaner.
3. **Triage one tag at a time**, worst-performing first. "Refine 10k images × N tags" is paralysing; "clean the single worst anomaly tag this week" is a Tuesday.
4. **The accept/reject gate you already built is the loop's engine** — but it's only trustworthy if you gate against a small **hand-cleaned golden eval set**. Build that first or the whole loop measures noise.
5. **This is plausibly the real foundation** under both the pipeline and LoRA paths, *and* it converges with the product: a "review suggested tag fixes" queue is a PixlStash feature, not just internal tooling. Dogfood it on your own tagger; ship it to users doing dataset curation.

---

## The reframe that unblocks you

You said you can't envision how pipelines work with ComfyUI. **So don't.** The thing you actually need is an *internal dataset-refinement loop*. Its "stages" are model passes over your own data (tag, detect anomalies, detect label errors); a "plugin" is just a model pass. ComfyUI is a downstream distribution concern that can wait. Stripping ComfyUI out of the picture is most of why this suddenly feels tractable — you were stuck on the wrong half of the problem.

## The right mental model: ranking under a budget

You have finite review time. Every label-cleaning method below is really answering one question: *in what order should I look at (image, tag) pairs to get the most model improvement per minute of attention?* The deliverable isn't a clever algorithm, it's a **prioritized review queue**. Everything else is in service of ordering that queue well.

## Two error types, found by different signals

Don't lump these — anomaly tags are rare, so the asymmetry matters:

- **False negatives (missing tags)** — the anomaly is there, nobody tagged it. *This is usually your bigger problem with rare classes*, and it's the one uncertainty-sampling alone misses (the model also predicts low). Caught by: near-neighbor propagation, and "model confidently says YES, label says no."
- **False positives (wrong tags)** — tag applied where it shouldn't be. Caught by: "model confidently says NO, label says yes," high-loss/low-margin tracking.

A good queue surfaces both, labelled by direction, so review is fast ("is this missing?" vs "is this wrong?").

---

## The techniques, cheapest first

### 1. Near-neighbor label disagreement — model-independent, nearly free, do first
You already compute semantic/face embeddings for similarity search. Reuse them: for each tag, find pairs/small clusters of **visually near-identical images that disagree on that tag**. Near-twins with different labels are almost always an error or a genuine boundary case. Because this doesn't depend on the (noisy) model, it's the right **cold-start** — it breaks the circularity of "trust the model to clean the data it was trained on."
- Off-the-shelf: **fastdup** (free, embedding-based, finds near-dups + label issues fast) or roll your own kNN over the embeddings you already have.

### 2. Label propagation in embedding space — attacks the false-negative problem at scale
Build a kNN graph over embeddings, propagate tags through it, flag where the *propagated* label disagrees with the given one. This **suggests tags for under-labelled images**, which is exactly the rare-anomaly recall hole. Semi-supervised, cheap if embeddings exist.

### 3. Confident learning (Cleanlab) — the standard tool, once the model is decent
Train k-fold cross-val so every image gets a prediction from a model that didn't see it; feed out-of-fold probabilities + labels to `cleanlab` (it has multi-label support). It ranks every (image, tag) pair by mislabel likelihood, both directions. 10k images / 5 folds of a ConvNeXt is very affordable.
- Alternative app-style tool: **FiftyOne** (`compute_mistakenness`, `compute_uniqueness`, `compute_hardness` + embedding visualization). Heavier dependency, but purpose-built for "find label mistakes and look at them." Worth it for a big one-time cleanup; maybe not worth standing up as permanent infra.

### 4. Margin / loss tracking with canary calibration — nearly free, rides along training
Track each example's margin (assigned-class logit vs. decision boundary) across epochs in a single run; persistently, confidently-wrong examples are mislabel suspects (this is the AUM idea). **Calibrate the suspicion threshold with canaries:** deliberately flip a small known set of labels, see what margin range *they* land in, and use that as your data-driven cutoff — instead of an arbitrary threshold. Elegant, principled, costs almost nothing.

### 5. Version-disagreement — a signal you already generate for free
Your gate compares model v_n to v_{n-1}. Examples where the two versions disagree, or where a model flipped relative to the label, are high-value review candidates. You're already producing these outputs; just mine them.

> **What to skip for now:** influence functions / TracIn (estimating one example's effect without retraining). It's the rigorous version of your "try flipping it" instinct, but it's not easy and not where the marginal hour pays off yet. Park it.

---

## Triage: one tag at a time, worst first

The reason this feels like "*massive* work" is that you're imagining all tags at once. Don't. Rank your anomaly tags by per-tag F1 (on the golden set, below) or by uncertainty mass, pick the **single worst-performing tag**, clean *it* thoroughly across the 10k, lock it, retrain, measure, move to the next. This:
- bounds the work into shippable chunks with visible wins,
- lets you stop on a tag when returns diminish,
- and surfaces taxonomy problems one concept at a time (see below) instead of all at once.

## The loop — batches, not per-flip, gated by what you already built

1. Build/refresh the ranked review queue (techniques above) for the target tag.
2. Review the top-N; apply fixes **in a batch**.
3. Retrain (or fine-tune).
4. Run your **existing accept/reject eval gate** vs. the previous model. Keep the batch if golden-set F1 improves; revert if not.
5. Loop until churn-vs-gain flattens (below).

This is literally your "add/remove the tag and see if it converges better" — at batch granularity, which is affordable, instead of per-tag, which is not. The harness exists; you're feeding it a better-ordered stream of edits.

---

## The thing that quietly decides success: a golden eval set

Your gate is only as honest as its labels. If the validation set carries the same anomaly-tag noise as training, the gate measures against noise and will reject good fixes. **Hand-clean a few hundred images into a golden eval set, gate against that.** This is the highest-leverage, most-skipped step in the whole plan. Do it before the loop, not after. (Bonus: the act of carefully labelling the golden set is where you'll discover your taxonomy problems.)

## A free, separate win for Smart Score specifically

Smart Score rides on the tagger's confidences, so **calibration** matters independently of relabelling. Temperature-scale the model on the golden set — a few lines, no retraining — and the *scores* get more honest even before labels improve. Cheap, decoupled, worth doing early because it improves the downstream consumer (auto-reject) directly.

## The hidden real bottleneck: tag definitions, not tag values

A chunk of your "errors" won't be mistakes — they'll be **genuine ambiguity** about what the tag *means*. The refinement process will keep surfacing boundary cases that force the question "what even counts as this anomaly?" That's not noise, that's signal: **refining the taxonomy/definitions may matter as much as fixing individual labels.** Keep a running `tag-definitions.md` as you review; every boundary call you make is a rule that makes the next 100 labels faster and more consistent. Don't discover halfway through that two tags overlap or one means three different things.

## Knowing when to stop

Per iteration, track **label churn (how many labels changed) against golden-set F1 gain.** Early iterations: big churn, big gain. When churn stays high but F1 stops moving, that tag is clean enough (or the remaining disagreements are real ambiguity → a taxonomy problem, not a labelling one). Stop, lock, next tag. Plot it; it's your dashboard for the whole effort.

---

## Why this might be the actual Next Big Thing

- **It's the foundation under everything.** Better tags → better tagger → better Smart Score → better auto-reject → better pipelines and better LoRA quality-gates. It's at the top of the stack in the options note; it unblocks both paths rather than competing with them.
- **Zero external dependency.** No Ostris, no ComfyUI, no upstream merge. Pure execution you own and are *excited* about — which, solo, is half the battle.
- **It reuses everything you have:** the train→eval→accept/reject harness (the loop engine), the embeddings (the cold-start ranking), and the PixlStash tagging UI (the review frontend).
- **It converges with the product.** A "review suggested tag fixes" queue *is* a PixlStash feature — the dataset-curation value users already told you they want (the FaceLikeness thread: "people already hand-roll this," "lead with curation"). So you'd **dogfood your own tooling and ship a differentiator in the same build.** That's rare and worth a lot. Immich/Eagle don't have a model whose errors they can surface for review; you do.

The honest caveat: kept purely internal, this improves *your* models and is "just" infra. The leverage multiplies when you make the review queue a product surface. Build it internal-first to fix your tagger, but design the review UX as if users will see it — because they should.

---

## First 3 days (resist building a framework)

- **Day 1 — golden set + cold-start scan.** Hand-clean ~300 images for the worst-performing tag into a golden eval set. Run the near-neighbor disagreement scan (technique 1) using existing embeddings → a CSV of suspicious (image, tag) pairs for that one tag.
- **Day 2 — review + batch fix + first gated loop.** Review the top of the CSV (in a notebook or the PixlStash UI), apply fixes in a batch, retrain, run the gate against the golden set. Record the F1 delta. This is the whole loop, end to end, once.
- **Day 3 — add the model-based ranker.** Wire Cleanlab on out-of-fold predictions (technique 3) for the same tag, compare its top suspects to the near-neighbor ones (overlap = high confidence), do a second gated loop. Now you have two complementary rankers and a working iteration rhythm.

Then decide whether to (a) grind tag-by-tag with this loop, and/or (b) promote the review queue into a real PixlStash feature.

## Risks / what not to do

- **Don't build the general "pipeline framework" first.** Build the concrete loop for one tag; generalize only once it's earned. (Same principle as the LoRA recommendation — concrete special case before general engine.)
- **Don't trust the model's ranking before it's earned trust.** Cold-start model-independent (embeddings), then graduate to model-based signals.
- **Don't gate against noisy validation.** The golden set is non-negotiable.
- **Don't over-clean.** Watch churn-vs-gain; stop when it flattens. Chasing genuine-ambiguity cases as if they're errors is wasted effort — fix the definition instead.
- **Don't conflate the two error directions.** Rare-class recall (missing tags) is probably your real problem; make sure the queue surfaces false negatives, not just confident false positives.

## Open questions to resolve when you start

- How is Smart Score actually derived from the tagger today? (Determines how much calibration vs. relabelling moves it.)
- Which single anomaly tag is worst right now — do you already have per-tag F1 to pick from, or does the golden set produce the first honest read?
- Are the existing embeddings good enough to trust for near-neighbor, or do they need their own sanity check first?
- Where does review happen — notebook now, PixlStash UI later? (Affects how fast you can iterate this week vs. the product payoff.)
