# Smart Score: calibrated, precision-aware anomaly scoring

**Status:** Phase 1 implemented · **Date:** 2026-06-20 · **Owner:** Gaute (solo)
**Companion to:** [`2026-06-human-label-ledger-plan.md`](2026-06-human-label-ledger-plan.md)
(this delivers its step 7 — the "calibrated `Σ wₜ·pₜ` Smart Score blend with noisy-OR
families") and [`2026-06-tagger-flywheel-implementation-plan.md`](2026-06-tagger-flywheel-implementation-plan.md).

## Why

PixlStash auto-assigns a 1–5 "smart score" so the grid ranks images without the user
rating everything. The goal is to land that score as close as possible to **how most
people would score the image**. Two input changes prompted a rethink:

- **4 new full-image anomaly tags** — `compression artifacts`, `noise`, `oversaturation`,
  `watermark` — global quality defects (not body-part defects) a human downscores
  regardless of subject.
- **Several anomaly tags raised to ~0.90 precision**: a broader and more accurate set of
  defects usable to push scores down.

The old scorer could not exploit either, because its anomaly term was **binary and
precision-blind**: `smart_score_task` summed *integer* weights of *present* `Tag` rows, so
a `pixelated` at 0.51 confidence penalised exactly like one at 0.99, and a flaky
0.60-precision classifier penalised as hard as a 0.95 one.

**Decided direction (with the user):** target a *tiered* result — an objective/consensus
default that personalises to the user's own ratings later — and ship now the *no-training*
penalty rework plus three extra objective signals (calibrated tags, OpenCV corroboration,
a CLIP-IQA probe). Making star ratings the training target is a later tier, kept here as
design only.

## How smart score works (study result)

`SmartScoreUtils.calculate_smart_score_batch_numpy`
([`utils/quality/smart_score_utils.py`](../../pixlstash/utils/quality/smart_score_utils.py))
computes `score = clip(Σ weighted components, 0, 1)`, mapped to `[1, 5]`. Positive terms:
good-anchor similarity, aesthetic (LAION predictor), sharpness, focus presence, resolution,
detail richness. Negative terms: bad-anchor similarity, text clutter, and the penalised-tag
term. The positive weights already summed to 1.25 (clipped at 1.0), so a new positive term
needs the others trimmed or it is clipped away.

**The five defects of the old anomaly term:** (1) binary, ignores the stored probability;
(2) precision-blind; (3) double-counts correlated defects (`bad anatomy` + `malformed hand`
+ `malformed foot` stacked additively); (4) arbitrary `/5`-then-cap-3.5 scale; (5) no
objective cross-check, while `Quality.noise_level` / `colorfulness` / `contrast` /
`brightness` sit computed-but-unused.

## What shipped (Phase 1)

### Calibrated anomaly penalty — [`utils/quality/anomaly_penalty.py`](../../pixlstash/utils/quality/anomaly_penalty.py)

For each picture and anomaly tag `t`:

- `p_t` = the tagger's stored probability (`TagPrediction.confidence`, kept down to 0.05, so
  the penalty sees graded evidence — not just accepted tags). A human ledger decision
  overrides the model: human POS → 1.0, human NEG → 0.0.
- `prec_t` = the tag's precision from the latest evaluated `TaggerRun`
  (`report.payload.per_tag[].precision`), else `DEFAULT_TAG_PRECISION = 0.90`.
- **Precision floor (`0.70`)** — answers *what to do with less-accurate tags*. Below it, a
  tag does **not** downscore (it only feeds the review queue); a false-positive-prone
  classifier wrongly tanking good images is worse for "scores like a human" than ignoring
  it. A **human confirmation bypasses the floor** and counts as certain — closing the loop
  with the human-label ledger.
- `e_t = p_tᶜ · prec_t` with **confidence power `CONF_POWER = 1.5`**, optionally damped by
  **objective corroboration** (below). The power makes punishment rise *super-linearly* with
  the tagger's confidence: a near-certain defect is punished disproportionately harder than a
  borderline one, and an uncertain (possibly false) detection stays gentle.

Tags group into **independent families**, combined **within** by noisy-OR (no
double-counting) and summed **across**:

```
noisyOR_f = 1 − Πₜ (1 − e_t)
penalty   = w_penalised · min( Σ_f severity_f · noisyOR_f , CAP=3.5 )
```

**Severity is derived, not hand-maintained.** `severity_f = (max member weight / 5) ×
SEVERITY_GAIN`, reading the per-tag weights from `DEFAULT_SMART_SCORE_PENALIZED_TAGS` (the
single editable source of truth — also where the 4 new IQA tags are registered). With
`SEVERITY_GAIN = 1.5` and `w_penalised = 0.50`: anatomy 1.5, watermark 1.2, compression 0.9,
noise/oversaturation/skin 0.6. The gain + power let a confident catastrophic defect drive
the score to the floor while *reliability stays a separate axis* (the precision discount):
"malformed hand" is as severe as "bad anatomy" but harder to predict, so same severity,
lower precision, less punishment per detection. Worked behaviour on a clean reference image
(score 4.34):

| Defect | Score | Why |
|---|---|---|
| confident `bad anatomy` (p 0.95, prec 0.92) | **1.79** | reliable disaster → floored |
| uncertain `bad anatomy` (p 0.60) | 3.06 | same defect, not sure → gentler |
| confident `malformed hand` (p 0.95, prec 0.74) | 2.29 | severe, but less reliable → less |
| uncertain `malformed hand` (p 0.60) | 3.31 | unreliable + unsure → protected |
| confident `watermark` (p 0.90) | 2.50 | |
| confident `noise` (p 0.90, weak corroboration) | 3.88 | objective metric disagrees → soft |

The 4 new full-image tags are their own families and **add** (a noisy *and* watermarked
image is penalised for both); the final `[0,1]` clip bounds the total.

### Objective corroboration (reuse the unused OpenCV metrics)

Where a family declares a corroborator, `e_t` is multiplied by a bounded factor in
`[0.5, 1.0]` from an objective metric, so two independent signals agreeing raises effective
precision and disagreement damps (never zeroes — a noisy metric cannot dominate the model):

- **oversaturation ↔ `colorfulness`** (Hasler–Süsstrunk) — clean corroborator.
- **noise ↔ `noise_level`**, *disambiguated by sharpness*: `noise_level` (mean |Laplacian|)
  is confounded with edge detail, so agreement = `noise_norm · (1 − sharpness)` (noisy but
  not sharp). `compression` has no defensible objective metric and is left uncorroborated.

### CLIP-IQA objective quality probe — [`utils/quality/smart_score_utils.py`](../../pixlstash/utils/quality/smart_score_utils.py)

An opinion-unaware perceptual-quality term from the CLIP embedding already stored on every
picture: `q = softmax(scale · [cos(img, good), cos(img, bad)])[0]`. The two prompt vectors
are precomputed by [`scripts/generate_clipiqa_prompts.py`](../../scripts/generate_clipiqa_prompts.py)
and bundled as `pixlstash/data/anchors/clipiqa_{good,bad}.npy` (mirroring `builtin_good.npy`),
so the scorer stays engine-free — two dot products per batch. Positive weights were
rebalanced (aesthetic/sharpness 0.35 → 0.30, CLIP-IQA 0.12); **if the prompt files are
absent the term is skipped and the legacy weights restored**, so scores never silently
drift.

### Wiring

`TagPrediction` confidences + per-tag precision are fetched once per batch via the shared
`attach_anomaly_inputs` / `fetch_anomaly_confidences` in
[`picture_scoring.py`](../../pixlstash/picture_scoring.py), called by **both** score paths
(the background `SmartScoreTask` and the on-demand `find_pictures_by_smart_score`, plus the
stack/grid callers) so they score identically.
`tagger_run_service.get_latest_tag_precisions` reads the precision map.
Migration `0061` NULL-resets `Picture.smart_score` so `MissingSmartScoreFinder` recomputes
the vault under the new formula.

**Note:** the per-tag weights in `smart_score_penalised_tags` now drive *relative* severity
(family severity = max member weight / 5 × gain); they are the single source of truth.
Per-tag detection thresholds and the global penalty magnitude are separate knobs.

## Tuning knobs (seeds — calibrate on real data)

`SEVERITY_GAIN` (1.5) and `w_penalised` (0.50) set how hard defects punish overall;
`CONF_POWER` (1.5) sets how steeply punishment tracks the tagger's confidence; relative
severities come from the per-tag weights in `DEFAULT_SMART_SCORE_PENALIZED_TAGS`.
`PRECISION_FLOOR`/`DEFAULT_TAG_PRECISION`, the corroboration ranges (`NOISE_LEVEL_*`,
`COLORFULNESS_*`, `CORRO_FLOOR`), and `clipiqa_scale` (50) / `w_clipiqa` round out the set.
All are editorial seeds, not fitted values; validate against a labelled sample, and let
Tier 3 learn the severities and the confidence curve.

## Future tiers (design only)

- **Tier 2 — per-tag temperature calibration.** Fit `T_t` on the golden/human-labelled
  slice (1-D NLL min) and use `p_t = sigmoid(logit_t / T_t)`. Temperature scaling is the
  standard one-parameter post-hoc calibration ([Guo et al. 2017]; scikit-learn calibration
  docs). Store `T_t` in `TaggerRun.report`.
- **Tier 3 — learned monotonic blend (the "personalise when data allows" half).** Fit the
  blend to the user's actual `Picture.score` (+ pooled `GuestScore` as a crowd label) with
  a **monotonic** model — ordinal logistic or LightGBM with monotone constraints (anomaly↓,
  sharpness↑, resolution↑) — interpretable and immune to perverse fits. Tabular regression:
  gradient boosting, not a neural net. Evaluate by Spearman/Kendall vs held-out human scores
  + per-class MAE, not accuracy. Personalise per-user above a few thousand ratings; fall
  back to the global blend otherwise.
- **Stronger objective backbone, later.** LAION-aesthetic v2 (today's `aesthetic_score`) is
  dated and rewards prompt-ignoring images; CLIP-IQA / QualiCLIP (opinion-unaware,
  CLIP-based) and learned IQA (Q-Align, Q-SiT, DeQA-Score) are stronger. Start with the
  near-free CLIP-IQA probe; revisit heavier models only if it underperforms.

## Verification

- Unit tests in [`tests/test_anomaly_penalty.py`](../../tests/test_anomaly_penalty.py):
  monotonic in probability and precision; precision floor gates; human bypass; noisy-OR
  within a family stays ≤ severity (no triple-count); independent families add; cap honoured;
  corroboration tracks colorfulness and is disambiguated by sharpness; merge children group
  with their parent; the precision reader parses/falls back; the scorer penalises defects in
  range, ignores sub-floor tags, and the CLIP-IQA term rewards a quality-aligned embedding.
- `test_smart_score_consistency` (on-demand vs stored parity) and `test_tagger_runs_api`
  still green; affected stack / tag-prediction / batch-score suites green.
- Behavioural check on real pictures (watermarked/noisy drops, clean high-aesthetic
  unaffected) and a Spearman no-regression vs existing star ratings are recommended before
  wide rollout — stars used as held-out eval only, not training, consistent with this phase.
- Activate CLIP-IQA in production by running `python -m scripts.generate_clipiqa_prompts`
  (the model is not bundled); scoring works without it, just without that signal.

## References

- Temperature scaling: Guo et al., *On Calibration of Modern Neural Networks*, arXiv:1706.04599;
  scikit-learn probability-calibration docs.
- CLIP-IQA: Wang et al., *Exploring CLIP for Assessing the Look and Feel of Images*,
  arXiv:2207.12396; QualiCLIP (opinion-unaware, CLIP-based).
- NR-IQA state of the art for the later backbone decision: Q-Align, Q-SiT, DeQA-Score, MUSIQ.
