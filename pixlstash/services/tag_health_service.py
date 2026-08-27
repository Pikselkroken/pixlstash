"""Tag health board cache - per-tag aggregate signals, rebuilt in the background.

Computes one :class:`~pixlstash.db_models.tag_health.TagHealth` row per tag from
indexed SQL over ``tag_prediction`` / ``tag`` / ``tag_suggestion`` / ``picture``
plus the *stored* ``PictureLikeness`` pairs - no embeddings, no kNN, never a
live O(N²) sweep. The board ranks tags by these signals; the expensive
near-neighbour scan stays reserved for review creation.

Signal definitions (thresholds are module constants, deliberately fixed for now
- see the redesign doc's open questions):

* ``est_wrong``    – tagged, *un-reviewed* pictures (no human POS/NEG on the
  ``(tag, picture)`` ledger row - ``label_state == "UNKNOWN"``) whose prediction
  confidence ≤ 0.1, on the current model version only (older generations are
  excluded - see ``_current_model_version``). A tag a human already CONFIRMED is
  excluded even when the model doubts it: that human-vs-model contradiction is
  surfaced via ``model_disputes`` instead, never re-counted here as an estimated
  fix.
* ``est_missing``  – untagged, *un-reviewed* pictures (no human POS/NEG,
  ``label_state == "UNKNOWN"``) whose prediction confidence ≥ 0.9, on the current
  model version only. A tag a human already REJECTED is excluded even though its
  high-confidence prediction survives the rejection: that contradiction is
  surfaced via ``model_disputes`` instead, never re-counted here.
* ``est_wrong_adj`` / ``est_missing_adj`` – ``est_wrong``/``est_missing``
  discounted by the tag's measured precision from the latest
  :class:`~pixlstash.db_models.tagger_run.TaggerRun` report (same discount
  idiom as :func:`pixlstash.utils.quality.anomaly_penalty.anomaly_penalty`),
  so a tag the model argues with a lot but is also unreliable about doesn't
  dominate the board's "estimated fixes" ranking. Falls back to
  ``DEFAULT_TAG_PRECISION`` when no report covers the tag.
* ``verified_pct`` – share of the tag's prediction rows with a non-UNKNOWN
  ledger ``label_state``.
* ``boundary_pct`` – share of predictions in [0.35, 0.65].
* ``overturn_rate``– ACCEPTED / (ACCEPTED + DISMISSED) over the tag's reviewed
  suggestions; ``None`` when the tag has no reviewed history.
* ``model_disputes`` – human-frozen labels the current prediction strongly
  contradicts (POS with conf ≤ 0.1 or NEG with conf ≥ 0.9). Surfaced only -
  never auto-requeued; human outranks model.
* ``mismatch``     – same-stack picture pairs disagreeing on the tag, plus
  stored high-likeness pairs (≥ ``MISMATCH_LIKENESS_THRESHOLD``) disagreeing
  (same-stack pairs are not double counted).
* ``has_model``    – the tag is in the *current tagger's vocabulary*: at least
  one prediction row exists on the current model version (the most recently
  written non-``manual`` prediction's version) somewhere in the vault. This is
  a property of the MODEL, not of the board's scope, so it is computed
  vault-wide even on a scoped board - a scope whose in-scope pictures were last
  tagged by an *earlier* run still reports the tag as in-vocabulary. Tags with
  no current-version prediction anywhere still get a row with ``has_model=False``
  so the board can show a "no model signal" state.
* ``ground_truth`` – how many **pictures** (not ``tag`` rows) carry this tag:
  the count of DISTINCT non-deleted ``picture_id``s with a ``Tag`` row for any
  literal tag in the folded tag's ``DEFAULT_TAG_MERGES`` equivalence class.
  Unlike ``has_model`` this IS scope-restricted - on a scoped board it counts
  only in-scope pictures, because it answers "does a review *of this scope*
  have confirmed examples to vote against?". Its equivalence class is
  deliberately identical to the ``equiv`` set
  :func:`pixlstash.services.tag_scan_service.scan_tag` builds for
  ``has_concept``, and the count is over distinct pictures for the same reason
  ``scan_tag`` builds a *set*: a picture tagged with both a child and its
  parent is one confirmed example, not two. ``ground_truth == 0`` therefore
  implies ``scan_tag``'s ``n_ground_truth == 0`` (the board counts every
  non-deleted in-scope picture; ``scan_tag`` counts the subset of those that
  also have an embedding), which is the direction the board's
  "this review would find nothing" gate depends on. Every rebuild writes a real
  integer; a **NULL** ``ground_truth`` can only appear on a cached row written
  before the column existed and means "not measured", never "zero" - the gate
  must not fire on it (see migration ``0075``).

Every signal above folds child tags into their parent per
:data:`~pixlstash.db_models.tag.DEFAULT_TAG_MERGES` before grouping - the same
``equiv`` idiom :func:`pixlstash.services.tag_scan_service.scan_tag` uses -
so a child ("extra digit") and its parent ("malformed hand") never appear as
separate board rows with inconsistent partial signals. Grouping is done in
Python: the underlying queries still ``GROUP BY`` (or ``DISTINCT``) the
literal tag column in SQL for cheap aggregation, then a second pass merges
same-parent buckets - additive counts sum, ``max(reviewed_at)`` takes the
later timestamp. Set-membership signals (``mismatch``'s per-picture tag sets)
remap at fetch time instead, since disagreement is a per-picture membership
question, not a simple sum.

Rebuilds run on the shared task runner (``vault.submit_task``); progress is
"tags processed / total" and readable via :func:`get_status`. One rebuild per
vault at a time; a second request while building is a no-op returning state.
"""

import threading
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from sqlalchemy import and_, case, func, or_
from sqlmodel import Session, delete, select

from pixlstash.db_models import Picture, PictureLikeness, Tag, TagHealth
from pixlstash.db_models.tag import DEFAULT_TAG_MERGES, is_tag_sentinel
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.db_models.tagger_run import TaggerRun
from pixlstash.pixl_logging import get_logger
from pixlstash.services.tagger_run_service import get_latest_tag_precisions
from pixlstash.utils.quality.anomaly_penalty import DEFAULT_TAG_PRECISION
from pixlstash.utils.service.filter_helpers import fetch_tag_review_scope_picture_ids
from pixlstash.utils.service.scope_table import scope_id_subquery

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

EST_WRONG_MAX_CONF = 0.1
EST_MISSING_MIN_CONF = 0.9
BOUNDARY_LOW = 0.35
BOUNDARY_HIGH = 0.65
# "High threshold" for stored PictureLikeness pairs to count as near-duplicates
# for the mismatch signal (likeness is cosine-like, 1.0 = identical).
MISMATCH_LIKENESS_THRESHOLD = 0.95

# Per-vault rebuild state; keyed by id(vault) so multiple Server instances in
# one process (tests) don't share a progress bar.
_LOCK = threading.Lock()
_STATES: dict[int, dict] = {}


def _state(vault: "Vault") -> dict:
    return _STATES.setdefault(id(vault), {"building": False, "progress": 0.0})


def get_status(vault: "Vault") -> dict:
    """``{"building": bool, "progress": float}`` for this vault's rebuild."""
    with _LOCK:
        state = _state(vault)
        return {"building": state["building"], "progress": state["progress"]}


def _current_model_version(session: Session) -> str | None:
    """The model version of the most recently written real prediction row.

    ``manual`` is the synthetic version ``reject_tag_prediction`` writes for
    pure-human decisions, not a tagger - excluded.
    """
    return session.exec(
        select(TagPrediction.model_version)
        .where(TagPrediction.model_version != "manual")
        .order_by(TagPrediction.predicted_at.desc())
        .limit(1)
    ).first()


def _fold_counts(rows) -> dict[str, int]:
    """Merge ``(literal_tag, count)`` pairs into ``DEFAULT_TAG_MERGES`` buckets.

    The literal ``tag`` values come from a SQL ``GROUP BY`` (cheap); this does
    the second-pass grouping that folds a child tag's count into its parent's
    bucket, summing when both a child and its parent already have counts.
    """
    folded: dict[str, int] = defaultdict(int)
    for tag_value, count in rows:
        folded[DEFAULT_TAG_MERGES.get(tag_value, tag_value)] += int(count)
    return dict(folded)


def _mismatch_counts(
    session: Session, picture_ids: set[int] | None = None
) -> dict[str, int]:
    """Per-tag count of near-duplicate pairs that disagree on the tag.

    Same-stack pairs first, then stored high-likeness pairs; a likeness pair
    whose two pictures share a stack is skipped (already counted). When
    ``picture_ids`` is provided, only pairs whose BOTH pictures are in scope
    count (membership in ``alive``/``stack_of`` enforces this downstream).
    """
    alive = {
        int(r)
        for r in session.exec(select(Picture.id).where(Picture.deleted.is_(False)))
    }
    if picture_ids is not None:
        alive &= picture_ids
    stack_of: dict[int, int] = {
        int(pid): int(sid)
        for pid, sid in session.exec(
            select(Picture.id, Picture.stack_id).where(
                Picture.stack_id.is_not(None), Picture.deleted.is_(False)
            )
        )
        if int(pid) in alive
    }
    tags_of: dict[int, set[str]] = defaultdict(set)
    # Scoped board: read only in-scope tag rows rather than the whole Tag table
    # (``alive`` is already narrowed to the scope above). An empty scope means no
    # rows - skip the query so an empty ``.in_(())`` never runs. Unscoped, ``alive``
    # is ~every non-deleted picture, so the whole-table scan is kept (and the
    # ``pid in alive`` guard still drops tags on deleted pictures).
    # When scoped, materialise ``alive`` into a per-connection temp table once
    # and filter via ``IN (SELECT ...)`` rather than binding one SQL parameter
    # per id - a large scope (tens of thousands of pictures) would otherwise
    # exceed SQLite's bound-parameter ceiling and raise OperationalError. The
    # subquery is result-identical to ``.in_(alive)`` and is reused for the
    # likeness-pairs query below (which binds ``alive`` at both endpoints). A
    # distinct table name keeps this from clobbering the caller's own scope
    # table (see ``compute_tag_health_rows``).
    scope_subq = None
    if picture_ids is not None and alive:
        scope_subq = scope_id_subquery(
            session, alive, name="_pixlstash_scope_ids_mismatch"
        )

    tag_query = select(Tag.picture_id, Tag.tag)
    if picture_ids is not None:
        if not alive:
            tag_rows: list = []
        else:
            tag_rows = session.exec(tag_query.where(Tag.picture_id.in_(scope_subq)))
    else:
        tag_rows = session.exec(tag_query)
    for pid, tag_value in tag_rows:
        if pid in alive and not is_tag_sentinel(tag_value):
            # Remap at the set-membership level (not a post-hoc sum): a
            # picture tagged with a child ("extra digit") must be treated as
            # having the parent ("malformed hand") for disagreement purposes,
            # or two pictures on the same concept but different literal tags
            # would spuriously mismatch against each other.
            tags_of[int(pid)].add(DEFAULT_TAG_MERGES.get(tag_value, tag_value))

    mismatch: dict[str, int] = defaultdict(int)

    # Same-stack pairs: within each stack, disagreeing pairs for tag t are
    # (#members with t) × (#members without t).
    members_by_stack: dict[int, list[int]] = defaultdict(list)
    for pid, sid in stack_of.items():
        members_by_stack[sid].append(pid)
    for members in members_by_stack.values():
        if len(members) < 2:
            continue
        stack_tags: set[str] = set()
        for pid in members:
            stack_tags |= tags_of.get(pid, set())
        for t in stack_tags:
            tagged = sum(1 for pid in members if t in tags_of.get(pid, set()))
            mismatch[t] += tagged * (len(members) - tagged)

    # Stored high-likeness pairs (canonical a < b, so each pair appears once).
    # Scoped board: both endpoints must be in scope (the Python guard below drops
    # any pair with an endpoint outside ``alive``), so push that into SQL to avoid
    # scanning every stored pair. Empty scope → no pairs. Unscoped keeps the full
    # scan (``alive`` is ~the whole table there).
    pair_query = select(
        PictureLikeness.picture_id_a, PictureLikeness.picture_id_b
    ).where(PictureLikeness.likeness >= MISMATCH_LIKENESS_THRESHOLD)
    if picture_ids is not None:
        if not alive:
            pairs: list = []
        else:
            # Both endpoints filtered via the shared temp-table subquery - this
            # pair binds ``alive`` twice, so a plain ``.in_(alive)`` would hit
            # the parameter ceiling at half the scope size.
            pair_query = pair_query.where(
                PictureLikeness.picture_id_a.in_(scope_subq),
                PictureLikeness.picture_id_b.in_(scope_subq),
            )
            pairs = session.exec(pair_query).all()
    else:
        pairs = session.exec(pair_query).all()
    for a, b in pairs:
        a, b = int(a), int(b)
        if a not in alive or b not in alive:
            continue
        sa_, sb_ = stack_of.get(a), stack_of.get(b)
        if sa_ is not None and sa_ == sb_:
            continue  # already counted as a same-stack pair
        for t in tags_of.get(a, set()) ^ tags_of.get(b, set()):
            mismatch[t] += 1

    return dict(mismatch)


def compute_tag_health_rows(
    session: Session,
    progress_cb: Callable[[int, int], None] | None = None,
    picture_ids: set[int] | None = None,
) -> list[dict]:
    """Compute the board's per-tag signal rows (pure read; no writes).

    Every non-sentinel tag that appears in either ``tag`` or ``tag_prediction``
    gets a row; tags with no predictions get zeros and ``has_model=False``.
    ``progress_cb(processed, total)`` is called as tags are assembled.

    When ``picture_ids`` is provided every signal is restricted to those
    pictures (the scoped board), and only tags that appear on in-scope
    pictures get rows. An empty set yields no rows. ``None`` = whole vault
    (the cached path).
    """
    current_version = _current_model_version(session)
    tag_precisions = get_latest_tag_precisions(session)

    # Materialise the scope once into a per-connection temp table and filter
    # every scoped aggregate via ``IN (SELECT ...)`` - binding one SQL parameter
    # per id (``.in_(picture_ids)``) would exceed SQLite's bound-parameter
    # ceiling for a large scope (tens of thousands of pictures) and raise
    # OperationalError. Result-identical to the set ``.in_()``. The default
    # table name is deliberately distinct from ``_mismatch_counts``' table, so
    # the ``_mismatch_counts(session, picture_ids)`` call below (which runs
    # between the early aggregates and the ``ground_truth``/``predicted`` reads)
    # cannot overwrite this table's ids.
    scope_subq = None
    if picture_ids is not None:
        scope_subq = scope_id_subquery(session, picture_ids)

    def _scoped(query, column):
        """Restrict a query to the scope pictures (no-op when unscoped)."""
        if picture_ids is None:
            return query
        return query.where(column.in_(scope_subq))

    # est_wrong: tagged + confidently-negative prediction, current model version only
    # (5a - an unpinned join here previously blended every model generation ever run).
    # label_state == "UNKNOWN" restricts this to pictures no human has ruled on: a tag
    # a human CONFIRMED (POS) keeps its Tag row and would otherwise be counted here even
    # though the human already resolved it - that human-vs-model contradiction belongs to
    # model_disputes, not est_wrong. (Human REJECT drops the Tag row, so those are already
    # excluded by the inner join above; only human POS needs filtering out.) The literal
    # matches the `verified` metric's `label_state != "UNKNOWN"` below.
    est_wrong = _fold_counts(
        session.exec(
            _scoped(
                select(TagPrediction.tag, func.count())
                .join(
                    Tag,
                    and_(
                        Tag.picture_id == TagPrediction.picture_id,
                        Tag.tag == TagPrediction.tag,
                    ),
                )
                .join(Picture, Picture.id == TagPrediction.picture_id)
                .where(
                    Picture.deleted.is_(False),
                    TagPrediction.confidence <= EST_WRONG_MAX_CONF,
                    TagPrediction.model_version == current_version,
                    TagPrediction.label_state == "UNKNOWN",
                ),
                TagPrediction.picture_id,
            ).group_by(TagPrediction.tag)
        ).all()
    )

    # est_missing: confidently-positive prediction with no Tag row, current model
    # version only (5a, same fix as est_wrong above).
    # label_state == "UNKNOWN" restricts this to pictures no human has ruled on: a human
    # REJECT (NEG) deliberately keeps the tagger's original high confidence and leaves no
    # Tag row, so such a row would otherwise be counted here as "missing" even though the
    # human already said no - that contradiction belongs to model_disputes, not est_missing.
    # (Human CONFIRM adds a Tag row, so those are already excluded by `Tag.picture_id IS
    # NULL`; only human NEG needs filtering out.)
    est_missing = _fold_counts(
        session.exec(
            _scoped(
                select(TagPrediction.tag, func.count())
                .join(Picture, Picture.id == TagPrediction.picture_id)
                .outerjoin(
                    Tag,
                    and_(
                        Tag.picture_id == TagPrediction.picture_id,
                        Tag.tag == TagPrediction.tag,
                    ),
                )
                .where(
                    Picture.deleted.is_(False),
                    TagPrediction.confidence >= EST_MISSING_MIN_CONF,
                    Tag.picture_id.is_(None),
                    TagPrediction.model_version == current_version,
                    TagPrediction.label_state == "UNKNOWN",
                ),
                TagPrediction.picture_id,
            ).group_by(TagPrediction.tag)
        ).all()
    )

    # One grouped pass over tag_prediction: totals, verified, boundary (these
    # stay scoped). ``has_model`` is NOT derived here - it is vault-wide
    # vocabulary membership, computed once below (see ``vocab``). Folded into
    # DEFAULT_TAG_MERGES buckets by summing per-literal-tag results - a child and
    # its parent's prediction rows both count toward the parent's row.
    pred_agg: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for tag_value, total, verified, boundary in session.exec(
        _scoped(
            select(
                TagPrediction.tag,
                func.count(),
                func.sum(case((TagPrediction.label_state != "UNKNOWN", 1), else_=0)),
                func.sum(
                    case(
                        (
                            TagPrediction.confidence.between(
                                BOUNDARY_LOW, BOUNDARY_HIGH
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
            # Exclude soft-deleted pictures so verified_pct/boundary_pct
            # match est_wrong/est_missing above (which already join+filter deleted);
            # otherwise a deleted picture's predictions inflate the unscoped board.
            .join(Picture, Picture.id == TagPrediction.picture_id)
            .where(Picture.deleted.is_(False)),
            TagPrediction.picture_id,
        ).group_by(TagPrediction.tag)
    ).all():
        bucket = pred_agg[DEFAULT_TAG_MERGES.get(tag_value, tag_value)]
        bucket[0] += int(total)
        bucket[1] += int(verified or 0)
        bucket[2] += int(boundary or 0)

    # model_disputes: human-frozen label strongly contradicted by the live prediction.
    disputes = _fold_counts(
        session.exec(
            _scoped(
                select(TagPrediction.tag, func.count())
                # Soft-deleted pictures must not inflate model_disputes; join+filter
                # deleted to match est_wrong/est_missing.
                .join(Picture, Picture.id == TagPrediction.picture_id)
                .where(
                    Picture.deleted.is_(False),
                    TagPrediction.label_source == "human",
                    or_(
                        and_(
                            TagPrediction.label_state == "POS",
                            TagPrediction.confidence <= EST_WRONG_MAX_CONF,
                        ),
                        and_(
                            TagPrediction.label_state == "NEG",
                            TagPrediction.confidence >= EST_MISSING_MIN_CONF,
                        ),
                    ),
                ),
                TagPrediction.picture_id,
            ).group_by(TagPrediction.tag)
        ).all()
    )

    # "Last review": the newest reviewed_at over the tag's suggestions, folded by
    # taking the later timestamp when both a child and its parent have history.
    last_reviewed: dict[str, datetime] = {}
    for tag_value, reviewed_at in session.exec(
        _scoped(
            select(TagSuggestion.tag, func.max(TagSuggestion.reviewed_at))
            # Exclude suggestions on soft-deleted pictures so last_reviewed matches
            # the deleted-excluding est_wrong/est_missing signals.
            .join(Picture, Picture.id == TagSuggestion.picture_id)
            .where(
                Picture.deleted.is_(False),
                TagSuggestion.reviewed_at.is_not(None),
            ),
            TagSuggestion.picture_id,
        ).group_by(TagSuggestion.tag)
    ).all():
        if reviewed_at is None:
            continue
        bucket_tag = DEFAULT_TAG_MERGES.get(tag_value, tag_value)
        if bucket_tag not in last_reviewed or reviewed_at > last_reviewed[bucket_tag]:
            last_reviewed[bucket_tag] = reviewed_at

    # Overturn rate over reviewed suggestions.
    accepted: dict[str, int] = defaultdict(int)
    dismissed: dict[str, int] = defaultdict(int)
    for tag_value, status, n in session.exec(
        _scoped(
            select(TagSuggestion.tag, TagSuggestion.status, func.count())
            # Exclude soft-deleted pictures so the overturn_rate numerator/denominator
            # match the deleted-excluding est_wrong/est_missing signals.
            .join(Picture, Picture.id == TagSuggestion.picture_id)
            .where(
                Picture.deleted.is_(False),
                TagSuggestion.status.in_(["ACCEPTED", "DISMISSED"]),
            ),
            TagSuggestion.picture_id,
        ).group_by(TagSuggestion.tag, TagSuggestion.status)
    ).all():
        bucket_tag = DEFAULT_TAG_MERGES.get(tag_value, tag_value)
        if status == "ACCEPTED":
            accepted[bucket_tag] += n
        else:
            dismissed[bucket_tag] += n

    mismatch = _mismatch_counts(session, picture_ids)

    # Exclude soft-deleted pictures from the tag universe so a tag that exists
    # ONLY on deleted pictures does not produce a spurious all-zero board row
    # (every signal already joins+filters deleted). Mirrors est_wrong/est_missing.
    # The scoped path already excludes deleted via its scope helper
    # (fetch_tag_review_scope_picture_ids); the redundant filter there is
    # harmless and keeps this consistent with the other signals' join+_scoped.
    #
    # One grouped pass replaces what used to be a bare `SELECT DISTINCT Tag.tag`:
    # it yields both the ground-truth tag universe (the dict's keys) AND each
    # folded tag's ``ground_truth`` picture count, so the board gains a signal
    # without gaining a query.
    #
    # The DEFAULT_TAG_MERGES fold is pushed into SQL as a CASE over the literal
    # tag and the count is COUNT(DISTINCT picture_id) *over the folded group*.
    # Both details are load-bearing: this count must equal |{pictures carrying
    # any tag in the equivalence class}|, which is exactly the ``concept`` set
    # ``tag_scan_service.scan_tag`` builds (``equiv = {tag} | children-of-tag``,
    # then ``select(Tag.picture_id).where(Tag.tag.in_(equiv))`` - a SET of
    # picture ids). Folding in Python by summing per-literal-tag counts would
    # double-count a picture tagged with BOTH a child ("extra digit") and its
    # parent ("malformed hand"), which the UNIQUE(picture_id, tag) constraint
    # permits, and would no longer match scan_tag's set semantics.
    folded_tag = case(DEFAULT_TAG_MERGES, value=Tag.tag, else_=Tag.tag)
    ground_truth_counts: dict[str, int] = {}
    for tag_value, n in session.exec(
        _scoped(
            select(folded_tag, func.count(Tag.picture_id.distinct()))
            .join(Picture, Picture.id == Tag.picture_id)
            .where(Picture.deleted.is_(False)),
            Tag.picture_id,
        ).group_by(folded_tag)
    ).all():
        # Sentinels never merge, so the folded value is the literal one here.
        if is_tag_sentinel(tag_value):
            continue
        ground_truth_counts[tag_value] = int(n)
    ground_truth_tags = set(ground_truth_counts)
    predicted_tags = {
        DEFAULT_TAG_MERGES.get(t, t)
        for t in session.exec(
            _scoped(
                select(TagPrediction.tag)
                .join(Picture, Picture.id == TagPrediction.picture_id)
                .where(Picture.deleted.is_(False)),
                TagPrediction.picture_id,
            ).distinct()
        )
        if not is_tag_sentinel(t)
    }
    all_tags = sorted(ground_truth_tags | predicted_tags)

    # has_model vocabulary: the folded tags that carry ≥1 prediction on the
    # current model version, computed VAULT-WIDE (deliberately NOT run through
    # ``_scoped``). ``has_model`` asks "is this tag in the current tagger's
    # vocabulary?" - a property of the model, not of the board's scope. On a
    # scoped board whose in-scope pictures were last tagged by an earlier run,
    # a scope-restricted current-version count would be zero and wrongly report
    # every tag as out-of-vocabulary (the R-bug this fixes); the unscoped query
    # asks the model-vocabulary question directly. ``current_version is None``
    # (no non-manual predictions anywhere) → empty vocab → ``has_model=False``
    # everywhere, matching the pre-existing "no model signal" behaviour.
    vocab: set[str] = set()
    if current_version is not None:
        vocab = {
            DEFAULT_TAG_MERGES.get(t, t)
            for t in session.exec(
                select(TagPrediction.tag)
                .join(Picture, Picture.id == TagPrediction.picture_id)
                .where(
                    Picture.deleted.is_(False),
                    TagPrediction.model_version == current_version,
                )
                .distinct()
            )
            if not is_tag_sentinel(t)
        }

    now = datetime.utcnow()
    rows: list[dict] = []
    total_tags = len(all_tags)
    for i, tag_value in enumerate(all_tags):
        total, verified, boundary = pred_agg.get(tag_value, (0, 0, 0))
        acc, dis = accepted.get(tag_value, 0), dismissed.get(tag_value, 0)
        wrong = int(est_wrong.get(tag_value, 0))
        missing = int(est_missing.get(tag_value, 0))
        # tag_precisions' keys are `.strip().lower()` (get_latest_tag_precisions);
        # normalize the lookup the same way so the discount doesn't silently
        # no-op via always missing and falling back to DEFAULT_TAG_PRECISION.
        precision = tag_precisions.get(tag_value.strip().lower(), DEFAULT_TAG_PRECISION)

        rows.append(
            {
                "tag": tag_value,
                "est_wrong": wrong,
                "est_missing": missing,
                "est_wrong_adj": float(round(wrong * precision)),
                "est_missing_adj": float(round(missing * precision)),
                "mismatch": int(mismatch.get(tag_value, 0)),
                "verified_pct": (verified / total) if total else 0.0,
                "boundary_pct": (boundary / total) if total else 0.0,
                "overturn_rate": (acc / (acc + dis)) if (acc + dis) else None,
                "model_disputes": int(disputes.get(tag_value, 0)),
                "has_model": tag_value in vocab,
                "ground_truth": int(ground_truth_counts.get(tag_value, 0)),
                "last_reviewed_at": last_reviewed.get(tag_value),
                "computed_at": now,
            }
        )
        if progress_cb is not None:
            progress_cb(i + 1, total_tags)
    return rows


def rebuild_tag_health(vault: "Vault") -> dict:
    """Recompute and replace the tag_health cache rows (synchronous).

    Progress is published to this vault's state as tags are processed. Returns
    ``{"tags": <row count>}``.
    """
    state = _state(vault)

    def _progress(done: int, total: int) -> None:
        with _LOCK:
            state["progress"] = (done / total) if total else 1.0

    rows = vault.db.run_immediate_read_task(compute_tag_health_rows, _progress)

    def _write(session: Session) -> None:
        # Cache semantics: wholesale replace (this is derived data, not user data).
        session.exec(delete(TagHealth))
        for r in rows:
            session.add(TagHealth(**r))
        session.commit()

    vault.db.run_task(_write)
    return {"tags": len(rows)}


def _run_rebuild_guarded(vault: "Vault") -> dict:
    """Task body: rebuild with the building flag held; always clears it."""
    try:
        return rebuild_tag_health(vault)
    finally:
        with _LOCK:
            state = _state(vault)
            state["building"] = False
            state["progress"] = 1.0


def start_rebuild(vault: "Vault") -> dict:
    """Kick a background rebuild on the shared task runner (idempotent).

    Returns the current ``{"building", "progress"}`` state. If a rebuild is
    already running this is a no-op. If the task runner is unavailable the
    rebuild runs synchronously as a fallback.
    """
    from pixlstash.tasks.tag_health_rebuild_task import TagHealthRebuildTask

    with _LOCK:
        state = _state(vault)
        if state["building"]:
            return {"building": True, "progress": state["progress"]}
        state["building"] = True
        state["progress"] = 0.0

    task = TagHealthRebuildTask(vault)
    if vault.submit_task(task) is None:
        logger.warning(
            "tag_health rebuild: task runner unavailable; rebuilding synchronously"
        )
        _run_rebuild_guarded(vault)
    return get_status(vault)


def _latest_health_relevant_change(session: Session) -> datetime | None:
    """Newest of the signals that make the cached board rows stale.

    Same shape as ``review_service._latest_vault_change`` (latest picture
    creation, latest tagger-run ingest), plus the signal that idiom didn't
    need but the board does: latest ``TagSuggestion.reviewed_at`` - every
    accept/dismiss/swap changes ``est_wrong``/``est_missing``/``mismatch``/
    ``overturn_rate`` for its tag, so a review session that touches zero new
    pictures and triggers zero tagger runs must still be able to mark the
    board stale.

    **Known gap, deliberately not closed here** (flagged as an open item in
    the redesign spec, §11): ``Tag`` has no timestamp column, so a manual tag
    add/remove via ``POST/DELETE /pictures/{id}/tags`` outside the review
    flow - the routes in ``routes/tags.py``, not
    ``tag_suggestion_service`` - is invisible to this staleness check. Adding
    a schema migration + backfill solely to catch that narrower, rarer path
    was judged disproportionate for a staleness *hint* whose escape hatch is
    already one click away (the persistent rebuild button, Spec B frontend);
    the gap means such an edit's board impact surfaces on the next rebuild
    triggered by *any* of the three tracked signals, or a manual rebuild,
    rather than immediately.
    """
    latest_pic = session.exec(
        select(func.max(Picture.created_at)).where(Picture.deleted.is_(False))
    ).one()
    latest_run = session.exec(select(func.max(TaggerRun.created_at))).one()
    latest_review = session.exec(
        select(func.max(TagSuggestion.reviewed_at)).where(
            TagSuggestion.reviewed_at.is_not(None)
        )
    ).one()
    candidates = [t for t in (latest_pic, latest_run, latest_review) if t is not None]
    return max(candidates) if candidates else None


def is_stale(vault: "Vault") -> bool:
    """Whether the cached board is stale relative to the latest health-relevant change.

    Cheap: two scalar aggregate queries (``max(TagHealth.computed_at)``,
    :func:`_latest_health_relevant_change`) - no row hydration, safe to call
    from a periodic finder. ``stale = latest_change > computed_at`` when both
    exist, else ``False`` (never built yet, or nothing has changed).
    """

    def _fetch(session: Session) -> tuple[datetime | None, datetime | None]:
        computed_at = session.exec(select(func.max(TagHealth.computed_at))).one()
        return computed_at, _latest_health_relevant_change(session)

    computed_at, latest_change = vault.db.run_immediate_read_task(_fetch)
    return bool(
        computed_at is not None
        and latest_change is not None
        and latest_change > computed_at
    )


def list_tag_health(vault: "Vault") -> dict:
    """The board payload: cached rows + rebuild state.

    Returns ``{"rows", "building", "progress", "computed_at", "stale"}`` where
    ``computed_at`` is the newest row's timestamp (ISO) or ``None`` when the
    cache has never been built, and ``stale`` is top-level (not per-row) since
    the cache is vault-wide and one rebuild covers every row - see
    :func:`is_stale`.
    """

    def _fetch(session: Session) -> list[TagHealth]:
        return list(session.exec(select(TagHealth).order_by(TagHealth.tag)).all())

    rows = vault.db.run_immediate_read_task(_fetch)
    computed_at = max(
        (r.computed_at for r in rows if r.computed_at is not None), default=None
    )
    status = get_status(vault)
    return {
        "rows": [
            {
                "tag": r.tag,
                "est_wrong": r.est_wrong,
                "est_missing": r.est_missing,
                "est_wrong_adj": r.est_wrong_adj,
                "est_missing_adj": r.est_missing_adj,
                "mismatch": r.mismatch,
                "verified_pct": r.verified_pct,
                "boundary_pct": r.boundary_pct,
                "overturn_rate": r.overturn_rate,
                "model_disputes": r.model_disputes,
                "has_model": r.has_model,
                "ground_truth": r.ground_truth,
                "last_reviewed_at": r.last_reviewed_at.isoformat()
                if r.last_reviewed_at
                else None,
                "computed_at": r.computed_at.isoformat() if r.computed_at else None,
            }
            for r in rows
        ],
        "building": status["building"],
        "progress": status["progress"],
        "computed_at": computed_at.isoformat() if computed_at else None,
        "stale": is_stale(vault),
    }


def list_tag_health_scoped(
    vault: "Vault",
    *,
    project_id: int | None = None,
    set_id: int | None = None,
    character_id: str | None = None,
) -> dict:
    """The board payload restricted to a project/set/character scope.

    Computed live per request (the cache only holds vault-wide rows); the
    grouped aggregates over a scope subset are cheap enough that no cache or
    progress bar is needed. Rows exist only for tags present on in-scope
    pictures. Same payload shape as :func:`list_tag_health`, plus
    ``scoped=True``; the cache is never read or written.
    """

    def _compute(session: Session) -> list[dict]:
        ids = fetch_tag_review_scope_picture_ids(
            session,
            project_id=project_id,
            set_id=set_id,
            character_id=character_id,
        )
        # None = every dimension was "Any"; treat as unscoped-equivalent by
        # computing over the whole vault (callers normally hit the cached
        # path instead, but this keeps the endpoint honest either way).
        return compute_tag_health_rows(session, picture_ids=ids)

    rows = vault.db.run_immediate_read_task(_compute)
    now = datetime.utcnow()
    return {
        "rows": [
            {
                **r,
                "last_reviewed_at": r["last_reviewed_at"].isoformat()
                if r["last_reviewed_at"]
                else None,
                "computed_at": r["computed_at"].isoformat(),
            }
            for r in rows
        ],
        "building": False,
        "progress": 1.0,
        "computed_at": now.isoformat(),
        # Computed live, never cached - nothing for it to be stale relative to.
        "stale": False,
        "scoped": True,
    }
