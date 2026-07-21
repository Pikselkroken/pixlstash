# S3: the 1–5 severity dial only spans a 1.47× range

Status: **open — design decision, not a bug** (raised in the `fix/review-overlay-fixes`
review, 2026-07-19). Deferred to its own task because the fix couples a UI promise to the
scoring calibration and needs a joint `ui-ux-expert` + `machine-learning-expert` call, not
a constant tweak. This note preserves the analysis.

## The finding

`User.smart_score_penalised_tags` lets a user rate each anomaly tag 1–5. The settings UI
(`frontend/src/components/settings/SmartScoreSection.vue:108-113`) labels those
**1 = Mild, 2 = Low, 3 = Moderate, 4 = High, 5 = Severe**, and `clampImportance` bounds
input to 1–5.

Per-tag severity is affine with a floor
(`pixlstash/utils/quality/anomaly_penalty.py`, `_tag_severity`):

```
severity = SEVERITY_GAIN × (SEVERITY_BASE + SEVERITY_WEIGHT_SPAN × weight/5)
         = 1.12 × (0.60 + 0.40 × weight/5)
```

| weight (UI label) | severity | % of weight 5 |
|---|---|---|
| 1 — Mild | 0.762 | **68%** |
| 2 — Low | 0.851 | 76% |
| 3 — Moderate | 0.941 | 84% |
| 4 — High | 1.030 | 92% |
| 5 — Severe | 1.120 | 100% |

The whole dial spans a **1.47× ratio**. "Mild" costs 68% of "Severe". A user who drags a
slider from 5 to 1 expecting "barely penalise this" gets "penalise this about two-thirds as
hard".

This directly undercuts the `watermark 4 → 1` default change (`c8fb57e3`,
"Default watermark to mild severity"): it dropped watermark's severity from 1.030 to 0.762,
a **~26%** cut, not the ~75% the word "mild" implies.

There is a second, related gap: **the only way to actually stop charging a tag is to remove
its row entirely** (absence from the table = not penalised — see `_tag_weight`). The UI can
express 1–5 but has no "off" affordance, so the strongest de-penalisation a user can reach
through the slider is 0.762, and the real "off" is undiscoverable.

## Why the floor exists (the constraint)

`SEVERITY_BASE = 0.60` is not arbitrary. It keeps every weight class inside the calibrated
per-defect-count bands from migration 0076's acceptance criteria — on an otherwise-good
picture: 1 defect → 1.5–2.2, 2 defects → 1.0–1.5, 3+ → floor. Those bands were validated
against the real vault. Lowering the floor to widen the dial's range **re-opens that
calibration**: the count-response curve has to be re-derived and re-checked, because a
lighter single-defect penalty pushes 1- and 2-defect pictures up out of their bands.

So this is a genuine tension, not an oversight: the model was tuned so *count* drives the
score reliably, and a wide, expressive per-tag *weight* dial pulls against that.

## Options

1. **Lower `SEVERITY_BASE`, re-derive the count bands.** Gives the dial real range, but
   couples it to the calibration — needs a fresh vault sweep to keep 1/2/3+ in-band, and
   the bands themselves may have to move. Most faithful to the UI promise, most work.
2. **Document the compression in the UI.** Cheapest and honest: relabel or annotate so "1"
   does not read as "almost off" (e.g. "Mild — still counts", or show the relative weight).
   No model change, no re-calibration. Fixes the expectation mismatch without fixing the
   range.
3. **Remap the slider to a wider severity span in the UI only.** Decouple the displayed
   position from the affine constant — a non-linear mapping from slider position to stored
   weight — so the *felt* range is wider without moving `SEVERITY_BASE`. Still bounded by
   the same floor at the model layer, so it can only redistribute, not extend, the range.
4. **Add an explicit "off / not penalised" control.** Surface the existing "remove the row"
   semantic as a real toggle, so a user can actually zero a tag. Independent of 1–3 and
   worth doing regardless — it closes the "0.762 is as low as I can go" trap.

## Recommendation

Split by layer, and do the cheap honest half first:

- **Now (no calibration risk):** (2) + (4) — stop the UI from over-promising, and give users
  a real "off". This removes the actual user-facing harm (a Mild slider that barely moves
  the score, and no way to fully de-penalise) without touching the model.
- **If genuine per-tag range is wanted:** (1) or (3), owned jointly — `ui-ux-expert` for the
  dial semantics and affordance, `machine-learning-expert` to re-derive and re-validate the
  count bands against the vault if `SEVERITY_BASE` moves. Do not change the constant without
  that re-validation; the 0076 bands are the acceptance test.

Route the decision through both skills before implementing. Until then the dial works as
documented here — it grades severity, it just grades it gently.
