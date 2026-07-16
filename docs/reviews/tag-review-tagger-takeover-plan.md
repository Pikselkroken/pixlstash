# Tag review: full takeover of the pixltagger ad-hoc tools

**Goal:** let the Tag Health board + Review Sessions completely replace the hand-rolled
labeling/eval scripts in the `pixltagger` repo, so all tag-quality work lives where the data
does. This is a scoping plan, not an implementation.

## What already works (don't rebuild)

- **Human write-back** — accept/dismiss on a review item writes a POS/NEG ledger `label_state`
  (the same signal the smart score reads). This is the labeling half of pixltagger's
  `hand_label_game.py` + `apply_hand_labels.py`.
- **Frozen scope + twin/near-dup detection** (hamming) on review creation.
- **Human-outranks-model** — `model_disputes` are surfaced, never auto-applied.

## What's missing for a complete takeover

The board today ranks by *model-vs-label disagreement* and attributes all of it to label error
(`verified_pct` is 0% everywhere). That's a good discovery signal but it is **not accuracy**, and
for a weak tag it inflates: `malformed hand` shows `est_missing` 6888, but the model's own
precision there is ~0.5–0.7, so a large share of those are model false positives, not missed
labels. Six gaps, ranked by what unblocks takeover.

### 1. Frozen verified eval slice → real precision/recall/F1  *(the unlock)*
Every accept/dismiss already produces ground truth. Let a user **freeze a per-tag eval slice**
(fixed pictures, verified POS/NEG, pinned to a model generation) and compute the model's
**precision/recall/F1 against it** — shown as a real column, replacing the 0%. This is
`official_hand_eval.py` / `clean_eval.json`, living in PixlStash. Without it the board can never
replace the source-of-truth number.

### 2. Train/eval split + leakage/dedup discipline  *(makes #1 valid)*
The honest number is only leak-free if eval is disjoint from training **and** deduped across the
split — what `dedup_prodeval.py` and `apply_hand_labels.py`'s eval-guard enforce today.
- First-class **split assignment** per picture (train / eval / neither), frozen per model gen.
- Guard: reviewing an eval image never feeds training; **near-dups can't straddle the split**
  (reuse the existing phash/hamming twin infra).

### 3. Reliability-aware ranking  *(kill the inflated 6888)*
PixlStash already ingests per-tag precision (`get_latest_tag_precisions`, used by the smart
score). Use it on the board too: **discount `est_missing`/`est_wrong` by the tag's precision** so
"EST. FIXES" reflects *expected real fixes*, not raw disagreement. Weak, noisy tags stop
dominating purely because the model argues a lot. Longer term: per-tag confidence calibration so
conf ≥ 0.9 means the same thing across tags.

### 4. Part-level (crop) review for hand/foot
Whole-image malformed-hand was stuck at ~0.50 because the hand is a tiny fraction of a 768px
frame — the whole reason `build_hand_crops.py` exists. Detect hand/foot regions (extend the
existing Face-detection pattern) and **show the crop** as the review item, aggregating crop
verdicts to the image (any crop malformed → image malformed), exactly like `apply_hand_labels`.
Without this, hand review here is slower and less accurate than the crop game.

### 5. Data hygiene: version-pinning + apply the remap
- `est_wrong`/`est_missing` don't filter by `model_version` — they blend model generations.
  **Pin every signal to the current version** (already computed for `has_model`).
- Apply pixltagger's `tag_remap` (`extra digit`/`missing digit` → `malformed hand`, etc.). The
  board shows `extra digit` as its own "no model signal" row (est_wrong 953), but in training
  those images *are* malformed hand — the board's label space diverges from what the model learns.
  Fold remapped children into their parent.

### 6. Close the loop: export the verified eval to the gate
Don't reimplement pixltagger's accept/reject gate (`decide.py`: bootstrap CIs, precision floor,
critical-tag weighting). Just **export the frozen verified eval slice** (image → human label) in
the shape `official_hand_eval.py` / `decide.py` already consume. One export endpoint retires the
last hand-off.

## Suggested order

1. **#1 + #2** — frozen verified eval + split/dedup discipline (honest, leak-free accuracy).
2. **#3** — reliability-aware ranking (fixes the trust problem the 6888 exposed).
3. **#4** — crop-level hand/foot review (parity with the crop game).
4. **#5 / #6** — hygiene and loop-closing.

After #1–#3 the board stops being "which tags does the model argue with" and becomes "which tags
are actually wrong, and how good is the model really" — at which point it replaces the ad-hoc
stack, with the frozen clean eval finally living where the data does.

## Acceptance (how we know it took over)

- A per-tag **F1 on a frozen verified slice** matches, within noise, what `official_hand_eval.py`
  reports on the same images (validates #1–#2 are leak-free).
- Board ranking is **precision-discounted**: `malformed hand`'s expected-fixes figure lands near
  the clean-eval-calibrated rate, not the raw 6888 (#3).
- Hand/foot reviews operate on **crops** and write image-level tags (#4).
- pixltagger can drive a full **retrain → judge** cycle using only a PixlStash eval export, with no
  `hand_label_game` / `apply_hand_labels` / `clean_eval` scripts in the loop (#6).

## Cross-repo notes

- Per-tag precision + tag_remap already exist in this repo (`get_latest_tag_precisions`,
  `DEFAULT_TAG_MERGES`); prefer reusing them over new tables.
- The pixltagger contract to preserve: leak-free splits, near-dup dedup across splits, and
  human-outranks-model. These are load-bearing — a subtle leak here silently inflates every F1.
