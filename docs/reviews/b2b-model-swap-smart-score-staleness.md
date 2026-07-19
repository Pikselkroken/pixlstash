# Deferred: model-swap smart-score staleness (B2b)

Status: **deferred to its own task** (decided 2026-07-19). Not in scope for
`fix/review-overlay-fixes`. This note preserves the analysis so the follow-up can
start from evidence rather than re-deriving it.

## Why this exists

Smart scores cache an anomaly penalty derived from tagger predictions. The review of
`fix/review-overlay-fixes` closed the *interactive* invalidation holes (B1 resurrection
CAS, B2a `threshold_offset` invalidation, C interactive emit). B2b is the remaining
class: **swapping the tagger model silently stales cached smart scores** and is *not*
covered by any of those fixes.

## What the code actually does (traced, ML review 2026-07-19)

1. **A model swap is not a runtime config operation.** The pixlstash tagger exposes a
   single parameter, `threshold_offset` (`pixlstash_tagger.py:1050-1069`). The model
   path is fixed at construction from `model_dir` (`:124-126`). `set_tagger_settings`
   (`vault.py:502` → `engine.py:231`) only replaces the settings dict; it writes no
   picture/tag/prediction rows and enqueues no work. A genuine model change arrives only
   via the lifecycle path — `needs_download()`/`download()` pulling a new HF revision
   (`pixlstash_tagger.py:184-192`, `:220-246`) — recorded in the revision sidecar
   (`PIXLSTASH_TAGGER_REV_FILENAME`, `:126`).

2. **A model swap does not re-tag existing pictures.** Re-tagging is driven only by
   `MissingTagFinder`, which selects pictures carrying a retag sentinel
   (`missing_tag_finder.py:106-124`). A swap adds no sentinel;
   `MissingTagPredictionFinder` refuses already-tagged pictures
   (`missing_tag_prediction_finder.py:82-85`). So old-model `TagPrediction` rows and
   their `model_version` persist; only new pictures get the new model.

3. **Two staleness channels, neither caught by the per-picture anomaly-signature
   invalidation:**
   - Old-model prediction rows persist, so the cached score computed from them is never
     refreshed (no re-tag → no `invalidate_changed_anomaly_scores`).
   - A new model ships different `label_thresholds` in its meta.json
     (`anomaly_thresholds.py:5-57`), changing *which existing predictions pass the gate* —
     moving the score with **no `TagPrediction` write and no signature movement**. The
     signature reads raw confidences with `apply_thresholds=None`, so it cannot see this.

4. **`model_version` / `_current_model_version` are weak/lagging identity.**
   `model_version = f"v{version_fn()}"` reads the model meta.json `version` int, default
   `0` (`tag_task.py:740-748`; `pixlstash_tagger.py:160-182`). Two checkpoints declaring
   the same (or missing) version collide. `_current_model_version`
   (`tag_health_service.py:135-146`) returns the version of the most recently *written*
   non-`manual` prediction — right after a swap with no re-tag it still returns the OLD
   version until a new row is written. Do not key the trigger on either.

## Recommended shape for the follow-up

- **Trigger location:** the model-lifecycle boundary where a new revision becomes active
  (`download()`/`init()`), comparing the newly-active model identity (meta.json `version`
  **and** `label_thresholds`, most robustly the revision sidecar) against the identity
  that produced the cached data. Not `set_tagger_settings`, not `_current_model_version`.
- **Mechanism (preferred):** enqueue a re-tag (set retag sentinels) of previously-tagged
  pictures, letting the existing self-invalidating re-tag path
  (`tag_task.py:862`/`:994`) NULL the scores for free. This refreshes the actual
  confidences — which a bare `smart_score` NULL cannot, since a recompute from stale
  old-model rows would only pick up new thresholds, not new predictions.
- **Mechanism (cheap fallback):** if a full re-tag is too expensive, targeted
  `invalidate_smart_scores` over pictures holding anomaly predictions, keyed on the
  persisted model revision changing — but this captures only the threshold
  reinterpretation, **not** new-model confidences. Partial correctness; document the gap.

## Cost fork to decide in the follow-up

Full re-tag (complete, most compute) vs. targeted invalidation (cheap, partial). This is
the open decision that caused B2b to be split out rather than built inline.
