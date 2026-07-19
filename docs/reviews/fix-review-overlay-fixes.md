# Review: `fix/review-overlay-fixes` (HEAD~5..HEAD)

Reviewed commits:

- `5d391b4a` Invalidate smart score when a picture's anomaly-tag state changes
- `6b7fa267` Grade the anomaly penalty by defect count and stop clipping at the floor
- `8771e65c` Score anomalies from the user's penalised-tag table and the apply threshold
- `c8fb57e3` Default watermark to mild severity
- `590a132d` Grade anomaly severity by confidence relative to each tag's apply threshold

Method: read the diff plus the full text of the new/rewritten modules, the task and
finder wiring, the four migrations, and the touched routes/services. An outside
second opinion (Kimi K3, via `senior-backend-consultant`) was taken on the
concurrency and invalidation questions; its two headline claims were independently
re-verified against the code and are reproduced below with that verification noted.
Its answer truncated before covering migrations and penalty math, so those sections
are my own analysis.

Overall this is careful, well-documented work — the docstrings genuinely explain the
*why*, the scoping of the config-change invalidation is a real throughput win over the
previous library-wide reset, and test coverage is unusually good (61 tests across the
two files). Two blocking defects, both in the invalidation's coverage rather than in
its mechanics.

---

## Blocking

### B1. `SmartScoreTask._persist_scores` resurrects invalidated scores (permanent stale)

`pixlstash/tasks/smart_score_task.py:90-96` and `:193-203`

The task reads anomaly inputs in one transaction, computes for seconds outside any
session, then persists in a **separate** transaction:

```python
changed_count = self._db.run_task(
    self._persist_scores,
    id_to_score,
    priority=DBPriority.LOW,
)
```

`_persist_scores` then writes unconditionally:

```python
pic.smart_score = score
```

Interleaving:

1. Finder claims picture 7 (`smart_score IS NULL`); `_fetch_score_data` reads its
   anomaly state at T0.
2. Scoring runs (seconds).
3. At T1 a route handler or `TagTask` NULLs `smart_score` for picture 7 and commits,
   atomically with the tag mutation.
4. At T2 `_persist_scores` writes the score computed from the **pre-T1** state.
5. The claim is released. The row is now non-NULL, so
   `find_pictures_missing_smart_score` never picks it again, and nothing else will
   ever NULL it.

The stale score is permanent. The finder's claim only serialises `SmartScoreTask`s
against each other — it does not exclude route handlers or the tagger.

`DBPriority.LOW` on the persist widens the window materially: the write can sit in
the queue behind other work well after the compute finished.

**The obvious guard does not work.** `UPDATE ... WHERE smart_score IS NULL` cannot
fix this, because the row is NULL in *both* relevant states — "claimed but not yet
scored" and "invalidated since being claimed". The predicate cannot distinguish them,
so the guarded write still succeeds at T2.

**Fix:** compare-and-swap on the *inputs*. `anomaly_state_signature` already exists
for exactly this. Capture the signature in `_fetch_score_data` alongside the
candidates, carry it to `_persist_scores`, and inside the write transaction
re-snapshot and skip any picture whose signature moved:

```python
@staticmethod
def _persist_scores(session, id_to_score: dict, before: dict) -> int:
    after = anomaly_state_signature(session, list(id_to_score))
    changed = 0
    for pic_id, score in id_to_score.items():
        if before.get(pic_id) != after.get(pic_id):
            logger.info(
                "SmartScoreTask: anomaly state for picture %s moved during scoring; "
                "discarding the stale score so the finder re-picks it.", pic_id
            )
            continue
        ...
```

Because SQLite serialises writers this closes the race in both orderings: either the
invalidation committed first (signature moved, write skipped, row stays NULL, finder
re-picks) or it commits after (write lands, then the invalidation NULLs it, finder
re-picks). Cost is one chunked read per 64-picture batch.

A `score_version` column bumped by `invalidate_smart_scores` and checked in the
persist's `WHERE` is the alternative, but it adds schema and migration churn for the
same guarantee.

### B2. Changing the tagger's `threshold_offset` invalidates nothing

`pixlstash/utils/service/anomaly_thresholds.py:108-124`,
`pixlstash/routes/config.py:305-341`

`resolve_anomaly_apply_thresholds` folds `vault.get_pixlstash_tagger_threshold_offset()`
into every anomaly tag's gate. That offset changes **both**:

- which predictions survive the gate in `fetch_anomaly_confidences`
  (`picture_scoring.py:762-767`), and
- the evidence of every surviving prediction, via `u = (p - t) / (1 - t)` in
  `_confidence_evidence` (`anomaly_penalty.py:329-333`).

So moving the offset moves every cached score that has any anomaly prediction.
Nothing invalidates. I traced the setter: `threshold_offset` is a **tagger plugin
parameter** (`pixlstash/tagger_plugins/pixlstash_tagger.py:1054`, stored in the
`tagger_settings` JSON), not part of `smart_score_penalised_tags` — and
`routes/config.py` only diffs the penalised-tag table. The signature-diff context
manager is doubly blind here: it only wraps tag mutations, and its signature is
ungated anyway (see N1).

The same hole applies to a **tagger model swap**: new `meta.json` `label_thresholds`
shift every gate identically, with no invalidation.

**Fix:** where `threshold_offset` (and the tagger model/meta path) is persisted, diff
old vs new and, on change, call
`invalidate_for_penalised_tag_change(session, ANOMALY_PENALTY_TAGS)` in the same
transaction. That over-invalidates pictures whose predictions sit far from any gate,
but it is one bulk UPDATE and computing the exact affected set is not worth the
complexity. Note this is not fixable by changing the signature — a settings edit is
not a wrapped tag mutation.

---

## Should fix

### S1. Collapse the four migrations into one

`pixlstash/migrations/versions/0076_*.py` … `0079_*.py`

All four are unmerged on this branch and all four run the identical statement:

```python
op.execute(sa.text("UPDATE picture SET smart_score = NULL"))
```

CLAUDE.md is explicit that a feature branch should squash/merge migrations for the
same change rather than stack them, and this is one change (the smart-score formula
moved) split across four commits. A fresh deploy currently rewrites the entire
`picture` table four times for one net effect.

The pattern itself is correct: this is exactly the NULL-reset-to-trigger-reprocessing
that CLAUDE.md prescribes, the table/column existence guards are right, the
`__all__` export is present, and the conditional-`add_column` rule doesn't apply since
no columns are added. `downgrade()` is a documented no-op with a reason, not a bare
`pass` — compliant.

**Fix:** squash 0076–0079 into a single `0076_recompute_smart_score_calibrated_anomaly_v2`
(or similar) whose docstring lists all four reasons. Keep the guards as they are.

### S2. Config-route invalidation is non-atomic with the config write

`pixlstash/routes/config.py:329-341`

The user update commits in one DB task, then the invalidation runs in a **separate**
`DBPriority.LOW` task. If the process dies in between, the weight change is durable
but the invalidation is lost, and nothing records that it is owed — scores stay stale
indefinitely, which is the exact failure this branch exists to eliminate.

The stated reason for deferring doesn't hold: `invalidate_for_penalised_tag_change`
is one bulk UPDATE per tag chunk with no id round-trip into Python. It is cheap
enough to run inline.

This is also the one caller that breaks the invariant `invalidate_smart_scores`'
own docstring states — *"the invalidation lands atomically with the tag mutation that
caused it."*

**Fix:** run `changed_penalised_tags` and `invalidate_for_penalised_tag_change` inside
the same transaction as the user update, before its commit.

### S3. The 1–5 weight dial only spans a 1.47× severity range

`pixlstash/utils/quality/anomaly_penalty.py:231-248`

`_tag_severity` is affine with a 0.60 floor:

| weight | severity |
|---|---|
| 1 | 0.762 |
| 2 | 0.851 |
| 3 | 0.941 |
| 4 | 1.030 |
| 5 | 1.120 |

Weight 1 costs **68% of weight 5**. The whole user-facing dial spans a 1.47× ratio.

This directly undercuts `c8fb57e3` ("Default watermark to mild severity"): dropping
`watermark` from 4 to 1 reduces its severity by only ~26%, not the ~75% the change
implies. A user who drags a slider from 5 to 1 expecting "barely penalise this" gets
"penalise this 68% as hard".

The docstring justifies the floor as keeping every weight class inside the calibrated
per-count bands, which is a real constraint — but it means the settings UI is
promising far more control than the model delivers, and the only way to actually
de-penalise a tag is to delete its row entirely (a discoverability problem the UI
does not signal).

**Fix:** either lower `SEVERITY_BASE` and re-derive the count bands, or document the
compression in the settings UI so "1" doesn't read as "almost off". Worth a decision
rather than leaving it implicit — route the UI half past `ui-ux-expert` if the
constant stays.

### S4. `_persist_scores` is 128 statements per 64-picture batch

`pixlstash/tasks/smart_score_task.py:193-203`

`session.get(Picture, pic_id)` in a loop against a fresh session is 64 SELECTs plus
64 UPDATEs per batch, on the single writer queue, for what is a pure scalar write.
With four migrations each forcing a full-library rescore this runs across the whole
vault four times.

**Fix:** a single bulk `update(Picture)` with a CASE, or `session.execute` with
`executemany` bindings. Fold this into the B1 rewrite since that restructures the same
function.

### S5. `resolve_anomaly_apply_thresholds` re-reads `meta.json` per batch

`pixlstash/utils/service/anomaly_thresholds.py:88-124`, called from
`smart_score_task.py:67` and `picture_scoring.py:871`

`open()` + `json.load()` on every `SmartScoreTask` (batch of 64) and on every
on-demand smart-score sort. A 100k-picture rescore is ~1,560 file reads of the same
unchanged file.

**Fix:** cache the resolved map on the vault, keyed on `(meta_path, offset,
default_threshold)`, and invalidate it wherever the offset or tagger model changes —
which is the same hook B2 needs, so the two fixes share plumbing.

---

## Nits

### N1. The change signature is taken over *ungated* confidences

`pixlstash/utils/service/smart_score_invalidation.py:90`

`anomaly_state_signature` calls `fetch_anomaly_confidences(session, chunk)` with no
thresholds, so it reflects raw prediction state while the scorer consumes the gated
map. In principle a sub-threshold confidence drifting 0.30 → 0.31 against a 0.85 gate
invalidates a score that provably cannot move.

The consultant rated this close to blocking. **I checked `TagTask` and it is not.**
`tag_task.py:929-935` only assigns `existing.confidence = confidence` when the value
actually differs, so a same-version re-tag of an unchanged image writes nothing and
the signature is stable. The case where confidences do move en masse is a model
version change — where scores genuinely should be recomputed anyway. The module
docstring's "faithful by construction" claim is slightly overstated (the scorer's real
input is the gated map), but the practical over-invalidation is small.

Optional: thread the thresholds through so the signature matches the scorer exactly.
Callers already have vault access. Low priority.

### N2. `_ID_CHUNK` reused for tag chunking, with two `IN` clauses per statement

`pixlstash/utils/service/smart_score_invalidation.py:257-270`

`_chunks(tags)` uses `_ID_CHUNK = 900`, and each chunk is bound into **two** `IN`
clauses in one statement — 1,800 parameters, above the ~999 the constant's comment
cites. In practice harmless: SQLite's limit has been 32,766 since 3.32 (local build is
3.49.1, verified), and the penalised-tag table is ~15 entries so a chunk is never
close to 900. Still, the comment now documents a bound the code would violate.

**Fix:** a separate `_TAG_CHUNK` constant at, say, 400, or a comment noting the 2×
multiplier.

### N3. Dead initialisation

`pixlstash/services/impossible_tag_clear_service.py:76`

`removed: list[tuple[int, str]] = []` is immediately reassigned by
`removed = _clear_tags_in_session(...)`. Drop it.

---

## Compliance checks

**Endpoint scope enforcement (HARD REQUIREMENT):** ✅ Verified. All four modified
handlers in `routes/tags.py` call `enforce_picture_scope` after parsing the id and
before the mutation — `add_tag_to_picture` (:168), `remove_tag_from_picture` (:296),
`remove_tag_from_picture_everywhere` (:373), `clear_all_tags_on_picture` (:437). The
`routes/tag_predictions.py` changes are import-only refactors (moving
`load_label_thresholds` to the new module) with no handler-signature or return-path
change. `routes/config.py` is owner-only user config. **No new endpoints were added**,
so no new coverage-matrix cells are opened.

**Exception handling:** ✅ No bare `except: pass`. The two `except Exception` blocks
in `anomaly_thresholds.py` log with `exc_info=True` and the offending path. Note they
were moved verbatim from `tag_prediction_service.py` — not introduced here. They are
silent-fallback-shaped (a corrupt `meta.json` silently shifts every gate to the global
threshold with only a warning), which is worth a follow-up but is pre-existing.

**Imports:** ✅ Top-of-file throughout. The one local import
(`MissingSmartScoreFinder` in `vault.py:178`) is a documented circular-dependency
avoidance consistent with the two neighbouring finders.

**Test coverage:** Strong. 61 tests. Invalidation covers both directions (penalised
tag invalidates / content tag does not) across add, remove, confirm, reject, bulk
tagger rewrite, and config patch. Penalty covers monotonicity in probability,
precision, confidence and defect count, the calibrated count bands, the
divide-by-zero guard, and `compress_raw_score` bounds/monotonicity.

Gaps, both corresponding to blocking findings above:

- No test for the B1 persist race. Add one that NULLs a picture's score between
  `_fetch_score_data` and `_persist_scores` and asserts the row stays NULL.
- No test for B2 — the behaviour does not exist yet. Add both directions once it does.

**Penalty math** (checked directly, since the consultant truncated before this):

- `_confidence_evidence` divide-by-zero is guarded — `MAX_APPLY_THRESHOLD = 0.99`
  keeps `1 - t >= 0.01`, and non-positive thresholds take the raw-probability branch.
  `test_absurd_threshold_offset_cannot_divide_by_zero` covers it. ✅
- Output bounded by `cap` (default 3.5). ✅
- `compress_raw_score` is continuous at 0 (both branches yield `SCORE_FLOOR_BAND`),
  strictly monotonic, bounded to [0,1]; both `np.where` branches are evaluated but the
  exponent is clipped at −50 so there is no underflow warning. ✅
- `_tag_weight` family-max inheritance behaves as documented, and
  `changed_penalised_tags` correctly propagates a family-ceiling move to unweighted
  aliases and merge children — that subtlety is handled well and is the kind of thing
  that usually gets missed. ✅
