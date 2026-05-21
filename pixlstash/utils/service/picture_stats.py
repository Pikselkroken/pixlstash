"""Picture statistics aggregation utilities (/pictures/stats)."""

import dataclasses
import time

from fastapi import HTTPException
from sqlalchemy import Integer, and_, case, cast, desc, exists, func, or_, text
from sqlmodel import Session, select

from pixlstash.db_models import Face, Picture, Tag, TagPrediction
from pixlstash.db_models.tag import TAG_EMPTY_SENTINEL
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.filter_helpers import (
    fetch_set_candidate_ids,
    project_membership_exists_clause,
    project_unassigned_clause,
)

logger = get_logger(__name__)

# How long to cache stats results for identical queries, in seconds.
STATS_TTL = 60.0


@dataclasses.dataclass
class PictureStatsParams:
    """Parsed query parameters for the /pictures/stats endpoint."""

    only_deleted: bool
    set_filter_ids: list[int]
    set_mode: str
    character_id_list: list[int]
    character_mode: str
    character_id_raw: str | None
    project_id_raw: str | None
    format_filter: list[str]
    min_score: int | None
    max_score: int | None
    smart_score_bucket: str | None
    resolution_bucket: str | None
    file_path_prefix: str | None
    import_source_folder: str | None
    tags_filter: list[str]
    rejected_tags: list[str]
    face_filter: str | None
    confidence_tag: str | None
    confidence_above: list[str]
    confidence_below: list[str]
    include: set[str]
    penalised_tag_set: set[str] | None
    penalised_cooc_both: bool


def _empty_stats() -> dict:
    return {
        "total": 0,
        "total_tags": 0,
        "tagged": 0,
        "untagged": 0,
        "avg_tags_per_image": 0.0,
        "top_tags": [],
        "top_cooccurrences": [],
        "confidence_histogram": [],
        "regular_tags": [],
        "score_distribution": [],
        "smart_score_distribution": [],
        "resolution_distribution": [],
    }


def _build_filtered_picture_subquery(session: Session, params: PictureStatsParams):
    """Build the picture-id subquery with all filter predicates applied.

    Args:
        session: Active database session.
        params: All parsed filter parameters from the request.

    Returns:
        A SQLAlchemy subquery of matching picture ids, or ``None`` when the
        filter resolves to an empty candidate set (caller should return early).

    Raises:
        HTTPException: 400 for invalid ``character_id`` or ``project_id``.
    """
    deleted_clause = (
        Picture.deleted.is_(True) if params.only_deleted else Picture.deleted.is_(False)
    )
    pic_q = select(Picture.id).where(deleted_clause)

    if params.set_filter_ids:
        candidate_ids = fetch_set_candidate_ids(
            session,
            set_ids=params.set_filter_ids,
            set_mode=params.set_mode,
            deleted_only=params.only_deleted,
        )
        if not candidate_ids:
            return None
        pic_q = pic_q.where(Picture.id.in_(candidate_ids))
    elif params.character_id_list:
        rows = session.exec(
            select(Face.character_id, Face.picture_id).where(
                Face.character_id.in_(params.character_id_list)
            )
        ).all()
        members_by_char: dict[int, set[int]] = {
            cid: set() for cid in params.character_id_list
        }
        for cid, pid in rows:
            members_by_char.setdefault(int(cid), set()).add(int(pid))

        candidate_ids: set[int]
        if params.character_mode == "intersection":
            intersection: set[int] | None = None
            for cid in params.character_id_list:
                current = members_by_char.get(cid, set())
                intersection = (
                    set(current) if intersection is None else intersection & current
                )
            candidate_ids = intersection or set()
        elif params.character_mode == "difference":
            first = members_by_char.get(params.character_id_list[0], set())
            rest: set[int] = set()
            for cid in params.character_id_list[1:]:
                rest |= members_by_char.get(cid, set())
            candidate_ids = first - rest
        elif params.character_mode == "xor":
            xor_union: set[int] = set()
            for cid in params.character_id_list:
                xor_union |= members_by_char.get(cid, set())
            xor_intersection: set[int] | None = None
            for cid in params.character_id_list:
                current = members_by_char.get(cid, set())
                xor_intersection = (
                    set(current)
                    if xor_intersection is None
                    else xor_intersection & current
                )
            candidate_ids = xor_union - (xor_intersection or set())
        else:
            candidate_ids = set()
            for cid in params.character_id_list:
                candidate_ids |= members_by_char.get(cid, set())

        if not candidate_ids:
            return None
        pic_q = pic_q.where(Picture.id.in_(candidate_ids))
    elif params.character_id_raw == "UNASSIGNED":
        unassigned_conditions = Picture.build_unassigned_conditions(
            enforce_stack_assignment=True,
        )
        pic_q = pic_q.where(*unassigned_conditions)
    elif params.character_id_raw is not None and params.character_id_raw not in (
        "",
        "ALL",
    ):
        try:
            char_id_int = int(params.character_id_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid character_id") from exc
        pic_q = pic_q.where(
            exists(
                select(Face.id).where(
                    Face.picture_id == Picture.id,
                    Face.character_id == char_id_int,
                )
            )
        )

    if params.project_id_raw == "UNASSIGNED":
        pic_q = pic_q.where(project_unassigned_clause(Picture))
    elif params.project_id_raw is not None:
        try:
            pid_int = int(params.project_id_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid project_id") from exc
        pic_q = pic_q.where(project_membership_exists_clause(pid_int, Picture))

    if params.format_filter:
        pic_q = pic_q.where(
            Picture.format.in_([f.upper() for f in params.format_filter])
        )
    if params.min_score is not None:
        pic_q = pic_q.where(Picture.score >= params.min_score)
    if params.max_score is not None:
        pic_q = pic_q.where(Picture.score <= params.max_score)

    if params.smart_score_bucket == "unscored":
        pic_q = pic_q.where(Picture.smart_score.is_(None))
    elif params.smart_score_bucket == "1-2":
        pic_q = pic_q.where(Picture.smart_score.is_not(None), Picture.smart_score < 2.0)
    elif params.smart_score_bucket == "2-3":
        pic_q = pic_q.where(Picture.smart_score >= 2.0, Picture.smart_score < 3.0)
    elif params.smart_score_bucket == "3-4":
        pic_q = pic_q.where(Picture.smart_score >= 3.0, Picture.smart_score < 4.0)
    elif params.smart_score_bucket == "4-5":
        pic_q = pic_q.where(Picture.smart_score >= 4.0)

    if params.resolution_bucket == "unknown":
        pic_q = pic_q.where(or_(Picture.width.is_(None), Picture.height.is_(None)))
    elif params.resolution_bucket == "lt1mp":
        pic_q = pic_q.where(
            Picture.width.is_not(None),
            Picture.height.is_not(None),
            Picture.width * Picture.height < 1_000_000,
        )
    elif params.resolution_bucket == "1-4mp":
        pic_q = pic_q.where(
            Picture.width.is_not(None),
            Picture.height.is_not(None),
            Picture.width * Picture.height >= 1_000_000,
            Picture.width * Picture.height < 4_000_000,
        )
    elif params.resolution_bucket == "4-8mp":
        pic_q = pic_q.where(
            Picture.width.is_not(None),
            Picture.height.is_not(None),
            Picture.width * Picture.height >= 4_000_000,
            Picture.width * Picture.height < 8_000_000,
        )
    elif params.resolution_bucket == "8-16mp":
        pic_q = pic_q.where(
            Picture.width.is_not(None),
            Picture.height.is_not(None),
            Picture.width * Picture.height >= 8_000_000,
            Picture.width * Picture.height < 16_000_000,
        )
    elif params.resolution_bucket == "16plus":
        pic_q = pic_q.where(
            Picture.width.is_not(None),
            Picture.height.is_not(None),
            Picture.width * Picture.height >= 16_000_000,
        )

    if params.file_path_prefix:
        pic_q = pic_q.where(Picture.file_path.startswith(params.file_path_prefix))
    if params.import_source_folder:
        pic_q = pic_q.where(Picture.import_source_folder == params.import_source_folder)
    for tag in params.tags_filter:
        pic_q = pic_q.where(
            exists(
                select(Tag.id).where(
                    Tag.picture_id == Picture.id,
                    Tag.tag == tag,
                )
            )
        )
    for tag in params.rejected_tags:
        pic_q = pic_q.where(
            ~exists(
                select(Tag.id).where(
                    Tag.picture_id == Picture.id,
                    Tag.tag == tag,
                )
            )
        )

    if params.face_filter == "with_face":
        pic_q = pic_q.where(
            exists(
                select(Face.id).where(
                    Face.picture_id == Picture.id,
                    Face.face_index != -1,
                )
            )
        )
    elif params.face_filter == "without_face":
        pic_q = pic_q.where(
            ~exists(
                select(Face.id).where(
                    Face.picture_id == Picture.id,
                    Face.face_index != -1,
                )
            )
        )

    for i, entry in enumerate(params.confidence_above):
        ca_tag, ca_thresh = entry.rsplit(":", 1)
        pic_q = pic_q.where(
            text(
                f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                f" AND tag_prediction.tag = :ca_tag_{i} AND tag_prediction.confidence >= :ca_thresh_{i})"
                f" AND NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ca_tag_{i})"
            ).bindparams(**{f"ca_tag_{i}": ca_tag, f"ca_thresh_{i}": float(ca_thresh)})
        )
    for i, entry in enumerate(params.confidence_below):
        cb_tag, cb_thresh = entry.rsplit(":", 1)
        pic_q = pic_q.where(
            text(
                f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                f" AND tag_prediction.tag = :cb_tag_{i} AND tag_prediction.confidence < :cb_thresh_{i})"
                f" AND EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :cb_tag_{i})"
            ).bindparams(**{f"cb_tag_{i}": cb_tag, f"cb_thresh_{i}": float(cb_thresh)})
        )

    return pic_q.subquery()


def _compute_basic_counts(
    session: Session, pic_subq, params: PictureStatsParams
) -> dict:
    """Compute total, tagged, untagged, avg_tags_per_image, total_tags, and top_tags.

    Args:
        session: Active database session.
        pic_subq: Subquery of filtered picture ids.
        params: Filter params (used for penalised_tag_set).

    Returns:
        Dict with keys: total, tagged, untagged, avg_tags_per_image,
        total_tags, top_tags.
    """
    total = session.exec(select(func.count()).select_from(pic_subq)).one()

    tagged_subq = (
        select(Tag.picture_id)
        .where(
            Tag.picture_id.in_(select(pic_subq.c.id)),
            Tag.tag != TAG_EMPTY_SENTINEL,
            Tag.tag.is_not(None),
        )
        .distinct()
        .subquery()
    )
    tagged = session.exec(select(func.count()).select_from(tagged_subq)).one()

    tag_count_subq = (
        select(
            Tag.picture_id,
            func.count(Tag.id).label("cnt"),
        )
        .where(
            Tag.picture_id.in_(select(pic_subq.c.id)),
            Tag.tag != TAG_EMPTY_SENTINEL,
            Tag.tag.is_not(None),
        )
        .group_by(Tag.picture_id)
        .subquery()
    )
    avg_row = session.exec(select(func.avg(tag_count_subq.c.cnt))).one()
    avg_tags = float(avg_row) if avg_row is not None else 0.0
    total_tags_row = session.exec(select(func.sum(tag_count_subq.c.cnt))).one()
    total_tags = int(total_tags_row) if total_tags_row is not None else 0

    top_tags_q = select(Tag.tag, func.count(Tag.id).label("cnt")).where(
        Tag.picture_id.in_(select(pic_subq.c.id)),
        Tag.tag != TAG_EMPTY_SENTINEL,
        Tag.tag.is_not(None),
    )
    if params.penalised_tag_set:
        top_tags_q = top_tags_q.where(func.lower(Tag.tag).in_(params.penalised_tag_set))
    top_tags_rows = session.exec(
        top_tags_q.group_by(Tag.tag).order_by(desc("cnt")).limit(20)
    ).all()
    top_tags = [{"tag": row[0], "count": row[1]} for row in top_tags_rows]

    return {
        "total": int(total),
        "tagged": int(tagged),
        "untagged": int(total) - int(tagged),
        "avg_tags_per_image": round(avg_tags, 2),
        "total_tags": total_tags,
        "top_tags": top_tags,
    }


def _compute_cooccurrences(
    session: Session, pic_subq, params: PictureStatsParams
) -> list:
    """Compute top tag co-occurrences. Returns ``[]`` if not requested.

    Args:
        session: Active database session.
        pic_subq: Subquery of filtered picture ids.
        params: Filter params (used for include guard and penalised_tag_set).

    Returns:
        List of ``{"tags": [tag_a, tag_b], "count": n}`` dicts, or ``[]``.
    """
    if "cooc" not in params.include:
        return []

    t1 = Tag.__table__.alias("t1")
    t2 = Tag.__table__.alias("t2")
    cooc_base = (
        select(
            t1.c.tag,
            t2.c.tag,
            func.count().label("cnt"),
        )
        .select_from(
            t1.join(
                t2,
                and_(
                    t1.c.picture_id == t2.c.picture_id,
                    t1.c.tag < t2.c.tag,
                ),
            )
        )
        .where(
            t1.c.picture_id.in_(select(pic_subq.c.id)),
            t1.c.tag != TAG_EMPTY_SENTINEL,
            t2.c.tag != TAG_EMPTY_SENTINEL,
        )
    )
    if params.penalised_tag_set:
        if params.penalised_cooc_both:
            cooc_base = cooc_base.where(
                and_(
                    func.lower(t1.c.tag).in_(params.penalised_tag_set),
                    func.lower(t2.c.tag).in_(params.penalised_tag_set),
                )
            )
        else:
            cooc_base = cooc_base.where(
                or_(
                    func.lower(t1.c.tag).in_(params.penalised_tag_set),
                    func.lower(t2.c.tag).in_(params.penalised_tag_set),
                )
            )
    cooc_rows = session.execute(
        cooc_base.group_by(t1.c.tag, t2.c.tag).order_by(desc("cnt")).limit(10)
    ).fetchall()
    return [{"tags": [row[0], row[1]], "count": row[2]} for row in cooc_rows]


def _compute_confidence_stats(
    session: Session, pic_subq, params: PictureStatsParams
) -> tuple[list, list]:
    """Compute the confidence histogram and regular tag list. Returns ``([], [])`` if not requested.

    Args:
        session: Active database session.
        pic_subq: Subquery of filtered picture ids.
        params: Filter params (used for include guard and confidence_tag).

    Returns:
        Tuple of (confidence_histogram, regular_tags).
    """
    if "conf" not in params.include:
        return [], []

    conf_raw_expr = cast(TagPrediction.confidence * 5, Integer)
    conf_bucket_expr = case(
        (conf_raw_expr >= 5, 4),
        else_=conf_raw_expr,
    )
    conf_q = select(conf_bucket_expr.label("bkt"), func.count().label("n")).where(
        TagPrediction.picture_id.in_(select(pic_subq.c.id))
    )
    if params.confidence_tag:
        conf_q = conf_q.where(TagPrediction.tag == params.confidence_tag)
    ch_rows = session.execute(
        conf_q.group_by(conf_bucket_expr).order_by(conf_bucket_expr)
    ).fetchall()
    ch_map = {int(row[0]): int(row[1]) for row in ch_rows}
    if params.confidence_tag:
        # Pictures with the tag label applied but no prediction row are
        # treated as having an implicit confidence of 0.0 -> bucket 0
        labelled_no_pred_count = session.execute(
            select(func.count())
            .select_from(Tag)
            .where(
                Tag.picture_id.in_(select(pic_subq.c.id)),
                Tag.tag == params.confidence_tag,
                ~Tag.picture_id.in_(
                    select(TagPrediction.picture_id).where(
                        TagPrediction.tag == params.confidence_tag
                    )
                ),
            )
        ).scalar_one()
        ch_map[0] = ch_map.get(0, 0) + labelled_no_pred_count
    confidence_histogram = [
        {"label": f"{b * 20}-{b * 20 + 20}%", "count": ch_map.get(b, 0)}
        for b in range(5)
    ]

    reg_tag_rows = session.execute(
        select(Tag.tag)
        .where(
            Tag.picture_id.in_(select(pic_subq.c.id)),
            Tag.tag != TAG_EMPTY_SENTINEL,
            Tag.tag.is_not(None),
        )
        .distinct()
        .order_by(Tag.tag)
    ).fetchall()
    regular_tags = [row[0] for row in reg_tag_rows]

    return confidence_histogram, regular_tags


def _compute_picture_distributions(
    session: Session, pic_subq, params: PictureStatsParams
) -> tuple[list, list, list]:
    """Compute score, smart-score, and resolution distributions. Returns ``([], [], [])`` if not requested.

    Args:
        session: Active database session.
        pic_subq: Subquery of filtered picture ids.
        params: Filter params (used for include guard).

    Returns:
        Tuple of (score_distribution, smart_score_distribution, resolution_distribution).
    """
    if "picture" not in params.include:
        return [], [], []

    score_rows = session.execute(
        select(Picture.score, func.count().label("n"))
        .where(Picture.id.in_(select(pic_subq.c.id)))
        .group_by(Picture.score)
        .order_by(Picture.score)
    ).fetchall()
    score_map = {
        (row[0] if row[0] is not None else -1): int(row[1]) for row in score_rows
    }
    score_distribution = [
        {"label": "Unscored", "count": score_map.get(-1, 0)},
        {"label": "1", "count": score_map.get(1, 0)},
        {"label": "2", "count": score_map.get(2, 0)},
        {"label": "3", "count": score_map.get(3, 0)},
        {"label": "4", "count": score_map.get(4, 0)},
        {"label": "5", "count": score_map.get(5, 0)},
    ]

    ss_bkt = case(
        (Picture.smart_score.is_(None), -1),
        (Picture.smart_score < 2, 0),
        (Picture.smart_score < 3, 1),
        (Picture.smart_score < 4, 2),
        else_=3,
    )
    ss_rows = session.execute(
        select(ss_bkt.label("bkt"), func.count().label("n"))
        .where(Picture.id.in_(select(pic_subq.c.id)))
        .group_by(ss_bkt)
        .order_by(ss_bkt)
    ).fetchall()
    ss_map = {int(row[0]): int(row[1]) for row in ss_rows}
    smart_score_distribution = [
        {"label": "Unscored", "count": ss_map.get(-1, 0)},
        {"label": "1-2", "count": ss_map.get(0, 0)},
        {"label": "2-3", "count": ss_map.get(1, 0)},
        {"label": "3-4", "count": ss_map.get(2, 0)},
        {"label": "4-5", "count": ss_map.get(3, 0)},
    ]

    res_bkt = case(
        (
            or_(Picture.width.is_(None), Picture.height.is_(None)),
            -1,
        ),
        (Picture.width * Picture.height < 1_000_000, 0),
        (Picture.width * Picture.height < 4_000_000, 1),
        (Picture.width * Picture.height < 8_000_000, 2),
        (Picture.width * Picture.height < 16_000_000, 3),
        else_=4,
    )
    res_rows = session.execute(
        select(res_bkt.label("bkt"), func.count().label("n"))
        .where(Picture.id.in_(select(pic_subq.c.id)))
        .group_by(res_bkt)
        .order_by(res_bkt)
    ).fetchall()
    res_map = {int(row[0]): int(row[1]) for row in res_rows}
    resolution_distribution = [
        {"label": "Unknown", "count": res_map.get(-1, 0)},
        {"label": "<1 MP", "count": res_map.get(0, 0)},
        {"label": "1-4 MP", "count": res_map.get(1, 0)},
        {"label": "4-8 MP", "count": res_map.get(2, 0)},
        {"label": "8-16 MP", "count": res_map.get(3, 0)},
        {"label": "16+ MP", "count": res_map.get(4, 0)},
    ]

    return score_distribution, smart_score_distribution, resolution_distribution


def compute_picture_stats(vault, params: PictureStatsParams) -> dict:
    """Run picture statistics aggregation queries and return the result dict.

    Args:
        vault: Application vault, used for DB task dispatch.
        params: All parsed filter parameters from the request.

    Returns:
        A dict with keys: total, total_tags, tagged, untagged,
        avg_tags_per_image, top_tags, top_cooccurrences,
        confidence_histogram, regular_tags, score_distribution,
        smart_score_distribution, resolution_distribution.
    """

    def compute(session: Session) -> dict:
        pic_subq = _build_filtered_picture_subquery(session, params)
        if pic_subq is None:
            return _empty_stats()

        counts = _compute_basic_counts(session, pic_subq, params)
        top_cooccurrences = _compute_cooccurrences(session, pic_subq, params)
        confidence_histogram, regular_tags = _compute_confidence_stats(
            session, pic_subq, params
        )
        score_dist, smart_score_dist, res_dist = _compute_picture_distributions(
            session, pic_subq, params
        )

        return {
            **counts,
            "top_cooccurrences": top_cooccurrences,
            "confidence_histogram": confidence_histogram,
            "regular_tags": regular_tags,
            "score_distribution": score_dist,
            "smart_score_distribution": smart_score_dist,
            "resolution_distribution": res_dist,
        }

    return vault.db.run_immediate_read_task(compute)


# In-memory TTL cache for /pictures/stats responses.
#
# Keyed by an opaque string built by the caller from the request query params.
# Centralised here (next to compute_picture_stats) so callers don't share
# module-level mutable state across routes.
_stats_cache: dict[str, tuple[float, dict]] = {}


def clear_stats_cache() -> None:
    """Discard all cached /pictures/stats results (e.g. after tag mutations)."""
    _stats_cache.clear()


def get_cached_picture_stats(vault, params: PictureStatsParams, cache_key: str) -> dict:
    """Return cached stats for ``cache_key`` or compute and cache them.

    Expired entries (older than ``STATS_TTL`` seconds) are evicted on access.
    """
    now = time.monotonic()
    for expired_key in [
        k for k, (ts, _) in list(_stats_cache.items()) if now - ts >= STATS_TTL
    ]:
        _stats_cache.pop(expired_key, None)

    cached = _stats_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < STATS_TTL:
            return data

    result = compute_picture_stats(vault, params)
    _stats_cache[cache_key] = (time.monotonic(), result)
    return result
