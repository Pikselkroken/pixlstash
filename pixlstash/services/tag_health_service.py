"""Tag health board cache — per-tag aggregate signals, rebuilt in the background.

Computes one :class:`~pixlstash.db_models.tag_health.TagHealth` row per tag from
indexed SQL over ``tag_prediction`` / ``tag`` / ``tag_suggestion`` / ``picture``
plus the *stored* ``PictureLikeness`` pairs — no embeddings, no kNN, never a
live O(N²) sweep. The board ranks tags by these signals; the expensive
near-neighbour scan stays reserved for review creation.

Signal definitions (thresholds are module constants, deliberately fixed for now
— see the redesign doc's open questions):

* ``est_wrong``    – tagged pictures whose prediction confidence ≤ 0.1.
* ``est_missing``  – untagged pictures whose prediction confidence ≥ 0.9.
* ``verified_pct`` – share of the tag's prediction rows with a non-UNKNOWN
  ledger ``label_state``.
* ``boundary_pct`` – share of predictions in [0.35, 0.65].
* ``overturn_rate``– ACCEPTED / (ACCEPTED + DISMISSED) over the tag's reviewed
  suggestions; ``None`` when the tag has no reviewed history.
* ``model_disputes`` – human-frozen labels the current prediction strongly
  contradicts (POS with conf ≤ 0.1 or NEG with conf ≥ 0.9). Surfaced only —
  never auto-requeued; human outranks model.
* ``mismatch``     – same-stack picture pairs disagreeing on the tag, plus
  stored high-likeness pairs (≥ ``MISMATCH_LIKENESS_THRESHOLD``) disagreeing
  (same-stack pairs are not double counted).
* ``has_model``    – the tag has prediction rows for the current model version
  (the most recently written non-``manual`` prediction's version). Tags with
  no predictions at all still get a row with ``has_model=False`` so the board
  can show a "no model signal" state.

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
from pixlstash.db_models.tag import is_tag_sentinel
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.filter_helpers import fetch_tag_review_scope_picture_ids

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
    pure-human decisions, not a tagger — excluded.
    """
    return session.exec(
        select(TagPrediction.model_version)
        .where(TagPrediction.model_version != "manual")
        .order_by(TagPrediction.predicted_at.desc())
        .limit(1)
    ).first()


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
    for pid, tag_value in session.exec(select(Tag.picture_id, Tag.tag)):
        if pid in alive and not is_tag_sentinel(tag_value):
            tags_of[int(pid)].add(tag_value)

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
    pairs = session.exec(
        select(PictureLikeness.picture_id_a, PictureLikeness.picture_id_b).where(
            PictureLikeness.likeness >= MISMATCH_LIKENESS_THRESHOLD
        )
    ).all()
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

    def _scoped(query, column):
        """Restrict a query to the scope pictures (no-op when unscoped)."""
        if picture_ids is None:
            return query
        return query.where(column.in_(picture_ids))

    # est_wrong: tagged + confidently-negative prediction.
    est_wrong = dict(
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
                ),
                TagPrediction.picture_id,
            ).group_by(TagPrediction.tag)
        ).all()
    )

    # est_missing: confidently-positive prediction with no Tag row.
    est_missing = dict(
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
                ),
                TagPrediction.picture_id,
            ).group_by(TagPrediction.tag)
        ).all()
    )

    # One grouped pass over tag_prediction: totals, verified, boundary, has_model.
    pred_agg: dict[str, tuple[int, int, int, int]] = {}
    for tag_value, total, verified, boundary, current in session.exec(
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
                # current_version None renders as IS NULL → matches nothing → 0.
                func.sum(
                    case((TagPrediction.model_version == current_version, 1), else_=0)
                ),
            ),
            TagPrediction.picture_id,
        ).group_by(TagPrediction.tag)
    ).all():
        pred_agg[tag_value] = (
            int(total),
            int(verified or 0),
            int(boundary or 0),
            int(current or 0),
        )

    # model_disputes: human-frozen label strongly contradicted by the live prediction.
    disputes = dict(
        session.exec(
            _scoped(
                select(TagPrediction.tag, func.count()).where(
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

    # "Last review": the newest reviewed_at over the tag's suggestions.
    last_reviewed: dict[str, datetime] = dict(
        session.exec(
            _scoped(
                select(TagSuggestion.tag, func.max(TagSuggestion.reviewed_at)).where(
                    TagSuggestion.reviewed_at.is_not(None)
                ),
                TagSuggestion.picture_id,
            ).group_by(TagSuggestion.tag)
        ).all()
    )

    # Overturn rate over reviewed suggestions.
    accepted: dict[str, int] = defaultdict(int)
    dismissed: dict[str, int] = defaultdict(int)
    for tag_value, status, n in session.exec(
        _scoped(
            select(TagSuggestion.tag, TagSuggestion.status, func.count()).where(
                TagSuggestion.status.in_(["ACCEPTED", "DISMISSED"])
            ),
            TagSuggestion.picture_id,
        ).group_by(TagSuggestion.tag, TagSuggestion.status)
    ).all():
        if status == "ACCEPTED":
            accepted[tag_value] += n
        else:
            dismissed[tag_value] += n

    mismatch = _mismatch_counts(session, picture_ids)

    ground_truth_tags = {
        t
        for t in session.exec(_scoped(select(Tag.tag), Tag.picture_id).distinct())
        if not is_tag_sentinel(t)
    }
    predicted_tags = {
        t
        for t in session.exec(
            _scoped(select(TagPrediction.tag), TagPrediction.picture_id).distinct()
        )
        if not is_tag_sentinel(t)
    }
    all_tags = sorted(ground_truth_tags | predicted_tags)

    now = datetime.utcnow()
    rows: list[dict] = []
    total_tags = len(all_tags)
    for i, tag_value in enumerate(all_tags):
        total, verified, boundary, current = pred_agg.get(tag_value, (0, 0, 0, 0))
        acc, dis = accepted.get(tag_value, 0), dismissed.get(tag_value, 0)
        rows.append(
            {
                "tag": tag_value,
                "est_wrong": int(est_wrong.get(tag_value, 0)),
                "est_missing": int(est_missing.get(tag_value, 0)),
                "mismatch": int(mismatch.get(tag_value, 0)),
                "verified_pct": (verified / total) if total else 0.0,
                "boundary_pct": (boundary / total) if total else 0.0,
                "overturn_rate": (acc / (acc + dis)) if (acc + dis) else None,
                "model_disputes": int(disputes.get(tag_value, 0)),
                "has_model": current > 0,
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


def list_tag_health(vault: "Vault") -> dict:
    """The board payload: cached rows + rebuild state.

    Returns ``{"rows", "building", "progress", "computed_at"}`` where
    ``computed_at`` is the newest row's timestamp (ISO) or ``None`` when the
    cache has never been built.
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
                "mismatch": r.mismatch,
                "verified_pct": r.verified_pct,
                "boundary_pct": r.boundary_pct,
                "overturn_rate": r.overturn_rate,
                "model_disputes": r.model_disputes,
                "has_model": r.has_model,
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
        "scoped": True,
    }
