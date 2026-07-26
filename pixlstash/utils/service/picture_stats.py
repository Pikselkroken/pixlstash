"""Picture statistics aggregation utilities (/pictures/stats)."""

import dataclasses
import time

from fastapi import HTTPException
from sqlalchemy import Integer, and_, case, cast, desc, exists, func, or_
from sqlmodel import Session, select

from pixlstash.db_models import Face, Picture, Tag, TagPrediction
from pixlstash.db_models.tag import TAG_SENTINEL_LIKE_PATTERN, TAG_SENTINEL_ESCAPE_CHAR
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.filter_helpers import (
    fetch_set_candidate_ids,
    project_membership_exists_clause,
    project_unassigned_clause,
)
from pixlstash.utils.query.predicate_filter import PredicateFilter

logger = get_logger(__name__)

# How long to cache stats results for identical queries, in seconds.
STATS_TTL = 60.0

# Display buckets for the agreement matrix's smart-score axis. Identical to the
# `smart_score_distribution` bucketing so the matrix reads as the cross-product of
# the two histograms the sidebar already shows.
AGREEMENT_BUCKET_LABELS = ("1-2", "2-3", "3-4", "4-5")

# Smart score is aggregated at 0.01 resolution for the rank statistic, then summed
# into the four display buckets. One query serves both, so the coefficient and the
# cells can never disagree, and tau-b keeps almost all of the continuous variable's
# resolution instead of being attenuated by four coarse bins.
_AGREEMENT_CENTS_PER_UNIT = 100

# Below this many plottable pairs a rank coefficient is noise dressed up as a
# finding, so the API returns null and the UI says why instead of printing it.
AGREEMENT_MIN_PAIRS = 20


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
    scoped_picture_ids: list[int] | None = None


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
        "score_agreement": {},
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

    # Hard-limit to token-scoped pictures when the request is authorised via
    # a scoped token (picture_set, character, or project).
    if params.scoped_picture_ids is not None:
        if not params.scoped_picture_ids:
            return None
        pic_q = pic_q.where(Picture.id.in_(params.scoped_picture_ids))

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

    # Intrinsic-attribute predicates via the shared compiler.  The stats sidebar
    # uppercases formats and matches a file-path prefix as a whole sub-tree (not just
    # direct children).  The deleted lifecycle clause was already applied above, and
    # this site never filters on import-excluded / unimported / comfyui / hidden tags.
    pic_q = PredicateFilter(
        format=[f.upper() for f in params.format_filter] or None,
        min_score=params.min_score,
        max_score=params.max_score,
        smart_score_bucket=params.smart_score_bucket,
        resolution_bucket=params.resolution_bucket,
        file_path_prefix=params.file_path_prefix or None,
        file_path_prefix_children_only=False,
        import_source_folder=params.import_source_folder or None,
        tags_filter=params.tags_filter or None,
        tags_rejected_filter=params.rejected_tags or None,
        tags_confidence_above_filter=params.confidence_above or None,
        tags_confidence_below_filter=params.confidence_below or None,
        face_filter=params.face_filter,
        apply_deleted_filter=False,
    ).apply(pic_q)

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
            ~Tag.tag.like(TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR),
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
            ~Tag.tag.like(TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR),
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
        ~Tag.tag.like(TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR),
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
            t1.c.tag.notlike(
                TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
            ),
            t2.c.tag.notlike(
                TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
            ),
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
            ~Tag.tag.like(TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR),
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


def _agreement_scope(session, pic_subq, params: PictureStatsParams):
    """Return the picture scope the agreement matrix is computed over.

    A filter widget must not filter itself. Clicking a cell sets the score and
    smart-score-bucket filters, and if the matrix honoured them it would collapse
    to the single cell that was clicked, leaving no way to reach a neighbouring
    one. So those three predicates are dropped here while every other scope and
    filter still applies, and the clicked cell is rendered as selected instead.

    Args:
        session: Active database session.
        pic_subq: The already-built, fully-filtered scope.
        params: All parsed filter parameters from the request.

    Returns:
        ``pic_subq`` unchanged when none of the three predicates is active (the
        common case, so the second subquery build is skipped), otherwise a scope
        rebuilt without them. Falls back to ``pic_subq`` if the rebuild resolves
        to an empty candidate set.
    """
    if (
        params.min_score is None
        and params.max_score is None
        and params.smart_score_bucket is None
    ):
        return pic_subq
    unfiltered = dataclasses.replace(
        params, min_score=None, max_score=None, smart_score_bucket=None
    )
    # A subquery has no defined truthiness, so test for None explicitly.
    rebuilt = _build_filtered_picture_subquery(session, unfiltered)
    return pic_subq if rebuilt is None else rebuilt


def _weighted_pearson(points: list[tuple[float, float, int]]) -> float | None:
    """Pearson's r over ``(x, y, weight)`` triples.

    Weighted because the rows arrive pre-aggregated: one entry per distinct
    (rating, smart score) pair with its observation count, rather than one entry
    per picture.

    Args:
        points: Distinct (x, y) pairs with the number of observations of each.

    Returns:
        r in [-1, 1], or ``None`` when either variable is constant. A vanishing
        denominator means "no variance", which is not the same as "no
        relationship", so it must not be reported as 0.
    """
    n = sum(w for _, _, w in points)
    if n < 2:
        return None
    sum_x = sum(x * w for x, _, w in points)
    sum_y = sum(y * w for _, y, w in points)
    sum_xx = sum(x * x * w for x, _, w in points)
    sum_yy = sum(y * y * w for _, y, w in points)
    sum_xy = sum(x * y * w for x, y, w in points)

    covariance = n * sum_xy - sum_x * sum_y
    var_x = n * sum_xx - sum_x * sum_x
    var_y = n * sum_yy - sum_y * sum_y
    if var_x <= 0 or var_y <= 0:
        return None
    return covariance / ((var_x * var_y) ** 0.5)


def _mid_ranks(counts: dict[float, int]) -> dict[float, float]:
    """Map each distinct value to its tie-corrected (mid) rank.

    Args:
        counts: Observation count per distinct value.

    Returns:
        Value -> the average of the ranks that value's observations occupy.
    """
    ranks: dict[float, float] = {}
    seen = 0
    for value in sorted(counts):
        count = counts[value]
        # Ranks seen+1 .. seen+count all collapse to their average.
        ranks[value] = seen + (count + 1) / 2
        seen += count
    return ranks


def _weighted_spearman(points: list[tuple[float, float, int]]) -> float | None:
    """Spearman's rho: Pearson over mid-ranks, so tied values share a rank.

    Mid-ranks matter a lot here. The rating axis has five levels, so a naive
    ranking would impose an arbitrary order inside each star level and invent a
    relationship that isn't in the data.

    Args:
        points: Distinct (x, y) pairs with the number of observations of each.

    Returns:
        rho in [-1, 1], or ``None`` when it is undefined.
    """
    x_counts: dict[float, int] = {}
    y_counts: dict[float, int] = {}
    for x, y, w in points:
        x_counts[x] = x_counts.get(x, 0) + w
        y_counts[y] = y_counts.get(y, 0) + w
    x_ranks = _mid_ranks(x_counts)
    y_ranks = _mid_ranks(y_counts)
    return _weighted_pearson([(x_ranks[x], y_ranks[y], w) for x, y, w in points])


def _kendall_tau_b(matrix: list[list[int]]) -> float | None:
    """Kendall's tau-b for an ordered contingency table.

    Kept alongside Pearson and Spearman because the user score is an ordinal
    1-5 rating: almost every pair ties on that axis, and tau-b is the coefficient
    whose denominator corrects for ties in *both* variables.

    Args:
        matrix: Row-major counts, rows and columns both in increasing rank order.

    Returns:
        Tau-b in [-1, 1], or ``None`` when it is undefined (fewer than two
        observations, or one variable is constant so its tie correction consumes
        the whole denominator).
    """
    n_rows = len(matrix)
    n_cols = len(matrix[0]) if n_rows else 0
    if not n_rows or not n_cols:
        return None

    # Per row, running counts of the observations to the left of / to the right of
    # each column, so the concordant / discordant sums stay O(rows * cols).
    suffix = [[0] * n_cols for _ in range(n_rows)]
    prefix = [[0] * n_cols for _ in range(n_rows)]
    for i in range(n_rows):
        running = 0
        for j in range(n_cols - 1, -1, -1):
            suffix[i][j] = running
            running += matrix[i][j]
        running = 0
        for j in range(n_cols):
            prefix[i][j] = running
            running += matrix[i][j]

    concordant = 0
    discordant = 0
    for i in range(n_rows):
        for j in range(n_cols):
            cell = matrix[i][j]
            if not cell:
                continue
            greater = sum(suffix[k][j] for k in range(i + 1, n_rows))
            lesser = sum(prefix[k][j] for k in range(i + 1, n_rows))
            concordant += cell * greater
            discordant += cell * lesser

    row_totals = [sum(row) for row in matrix]
    col_totals = [sum(matrix[i][j] for i in range(n_rows)) for j in range(n_cols)]
    n = sum(row_totals)
    if n < 2:
        return None

    all_pairs = n * (n - 1) / 2
    row_ties = sum(t * (t - 1) / 2 for t in row_totals)
    col_ties = sum(t * (t - 1) / 2 for t in col_totals)
    denominator = (all_pairs - row_ties) * (all_pairs - col_ties)
    if denominator <= 0:
        # Every observation shares one rating, or one smart-score value: there is
        # no pair the coefficient could be computed from.
        return None
    return (concordant - discordant) / (denominator**0.5)


def _compute_agreement(session, pic_subq, params: PictureStatsParams) -> dict:
    """Cross-tabulate the user's star rating against the smart score.

    Both ``NULL`` and ``0`` mean "unrated" here, matching how
    ``score_distribution`` labels NULL "Unscored" and omits 0, and how the
    smart-score anchor query treats ``score > 0``.

    Args:
        session: Active database session.
        pic_subq: Subquery of filtered picture ids. The caller is responsible for
            passing a scope that does NOT apply the score / smart-score-bucket
            filters, so clicking a cell cannot collapse the matrix to itself.
        params: Filter params (used for the include guard).

    Returns:
        ``{}`` when not requested, else a dict with ``cells`` (all 20, dense and
        ordered), ``rated``, ``pairs``, ``total`` and the three coefficients
        ``pearson``, ``spearman`` and ``tau_b``.
    """
    if "picture" not in params.include:
        return {}

    cents = cast(Picture.smart_score * _AGREEMENT_CENTS_PER_UNIT, Integer)
    rows = session.execute(
        select(Picture.score, cents.label("cents"), func.count().label("n"))
        .where(
            Picture.id.in_(select(pic_subq.c.id)),
            Picture.score.is_not(None),
            Picture.score > 0,
        )
        .group_by(Picture.score, cents)
    ).fetchall()

    total = session.exec(select(func.count()).select_from(pic_subq)).one()

    # Rated-but-not-yet-scored pictures can't be placed on the smart-score axis.
    # They still count as rated for the coverage line; they are not plotted and
    # they do not enter the coefficient.
    rated = 0
    fine: dict[int, dict[int, int]] = {}
    for score, cent, count in rows:
        rated += int(count)
        if cent is None:
            continue
        fine.setdefault(int(score), {})[int(cent)] = int(count)

    columns = sorted({cent for row in fine.values() for cent in row})
    col_index = {cent: idx for idx, cent in enumerate(columns)}
    matrix = [[0] * len(columns) for _ in range(5)]
    for score, by_cent in fine.items():
        for cent, count in by_cent.items():
            matrix[score - 1][col_index[cent]] = count

    def bucket_of(cent: int) -> int:
        if cent < 2 * _AGREEMENT_CENTS_PER_UNIT:
            return 0
        if cent < 3 * _AGREEMENT_CENTS_PER_UNIT:
            return 1
        if cent < 4 * _AGREEMENT_CENTS_PER_UNIT:
            return 2
        return 3

    display = [[0] * len(AGREEMENT_BUCKET_LABELS) for _ in range(5)]
    for score, by_cent in fine.items():
        for cent, count in by_cent.items():
            display[score - 1][bucket_of(cent)] += count

    pairs = sum(sum(row) for row in display)
    cells = [
        {"score": score + 1, "bucket": label, "count": display[score][bucket]}
        for score in range(5)
        for bucket, label in enumerate(AGREEMENT_BUCKET_LABELS)
    ]

    # Pearson and Spearman run on the same 0.01-resolution rows, so all three
    # coefficients describe exactly the grid that is drawn.
    points = [
        (float(score), cent / _AGREEMENT_CENTS_PER_UNIT, count)
        for score, by_cent in fine.items()
        for cent, count in by_cent.items()
    ]
    enough = pairs >= AGREEMENT_MIN_PAIRS
    return {
        "cells": cells,
        "rated": rated,
        "pairs": pairs,
        "total": int(total),
        "tau_b": _kendall_tau_b(matrix) if enough else None,
        "pearson": _weighted_pearson(points) if enough else None,
        "spearman": _weighted_spearman(points) if enough else None,
    }


def compute_picture_stats(vault, params: PictureStatsParams) -> dict:
    """Run picture statistics aggregation queries and return the result dict.

    Args:
        vault: Application vault, used for DB task dispatch.
        params: All parsed filter parameters from the request.

    Returns:
        A dict with keys: total, total_tags, tagged, untagged,
        avg_tags_per_image, top_tags, top_cooccurrences,
        confidence_histogram, regular_tags, score_distribution,
        smart_score_distribution, resolution_distribution, score_agreement.
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
        agreement = _compute_agreement(
            session, _agreement_scope(session, pic_subq, params), params
        )

        return {
            **counts,
            "top_cooccurrences": top_cooccurrences,
            "confidence_histogram": confidence_histogram,
            "regular_tags": regular_tags,
            "score_distribution": score_dist,
            "smart_score_distribution": smart_score_dist,
            "resolution_distribution": res_dist,
            "score_agreement": agreement,
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
