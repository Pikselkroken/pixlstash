"""Character-likeness scoring: face↔reference similarity via InsightFace embeddings.

Split out of the former ``pixlstash.picture_scoring`` module (Backend Refactor
Phase 2 §4.6). Smart-score computation lives in the sibling
:mod:`pixlstash.scoring.smart_score`. Import the public names from
:mod:`pixlstash.scoring`.
"""

import struct
import time
from collections import defaultdict
from datetime import datetime

import numpy as np
from sqlalchemy import and_, asc, desc, exists, func, or_, text
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    Face,
    Picture,
    PictureSetMember,
    Tag,
)
from pixlstash.utils.quality.smart_score_utils import smart_score_penalised_tags
from pixlstash.utils.service.filter_helpers import combine_likeness_scores
from pixlstash.utils.serialization_utils import safe_model_dict
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def select_reference_faces_for_character(
    session: Session,
    character_id: int,
    max_refs: int = 10,
) -> list[Face]:
    """Select reference faces for a character using simple, deterministic rules.

    Args:
        session: Database session to query faces and pictures.
        character_id: Character id to select reference faces for.
        max_refs: Maximum number of reference faces to return.

    Returns:
        A list of Face objects to use as reference faces.
    """

    min_refs = min(5, max_refs)

    base_query = (
        select(Face, Picture)
        .join(Picture, Face.picture_id == Picture.id)
        .where(
            Face.character_id == character_id,
            Face.features.is_not(None),
            Picture.deleted.is_(False),
        )
    )

    rows = session.exec(
        base_query.where(Picture.score >= 5)
        .order_by(Picture.created_at.asc(), Picture.id.asc())
        .limit(max_refs)
    ).all()

    logger.debug(
        "[reference_faces] character_id=%s target_count=%s five_star_rows=%s",
        character_id,
        max_refs,
        len(rows),
    )

    representatives = [face for face, _ in rows]
    if len(representatives) >= max_refs:
        return representatives

    selected_face_ids = {face.id for face in representatives if face is not None}
    selected_picture_ids = {
        face.picture_id for face in representatives if face is not None
    }

    remaining_rows = session.exec(
        base_query.where(Picture.score >= 4)
        .where(~Picture.id.in_(selected_picture_ids))
        .order_by(Picture.created_at.asc(), Picture.id.asc())
        .limit(max_refs - len(representatives))
    ).all()
    logger.debug(
        "[reference_faces] character_id=%s four_five_rows=%s selected_pictures=%s",
        character_id,
        len(remaining_rows),
        len(selected_picture_ids),
    )
    if remaining_rows:
        for face, _ in remaining_rows:
            if len(representatives) >= max_refs:
                break
            if face.id in selected_face_ids:
                continue
            selected_face_ids.add(face.id)
            representatives.append(face)

    if len(representatives) >= min_refs:
        return representatives

    remaining_rows = session.exec(
        base_query.where(~Picture.id.in_(selected_picture_ids))
    ).all()
    logger.debug(
        "[reference_faces] character_id=%s remaining_rows=%s selected_pictures=%s",
        character_id,
        len(remaining_rows),
        len(selected_picture_ids),
    )
    if remaining_rows:
        penalised_tags = smart_score_penalised_tags(
            None,
            DEFAULT_SMART_SCORE_PENALIZED_TAGS,
            default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
        )
        penalised_tag_set = {
            str(tag).strip().lower() for tag in penalised_tags.keys() if tag
        }
        remaining_picture_ids = [picture.id for _, picture in remaining_rows]
        tag_weights = defaultdict(float)
        if penalised_tag_set and remaining_picture_ids:
            tag_rows = session.exec(
                select(Tag.picture_id, Tag.tag)
                .where(Tag.picture_id.in_(remaining_picture_ids))
                .where(Tag.tag.is_not(None))
                .where(func.lower(Tag.tag).in_(penalised_tag_set))
            ).all()
            for pic_id, tag in tag_rows or []:
                if not tag:
                    continue
                tag_weights[pic_id] += penalised_tags.get(tag.strip().lower(), 0.0)

        remaining_rows.sort(
            key=lambda row: (
                tag_weights.get(row[1].id, 0.0),
                row[1].created_at or datetime.max,
                row[1].id,
                row[0].id or 0,
            )
        )
        logger.debug(
            "[reference_faces] character_id=%s penalised_tags=%s",
            character_id,
            len(tag_weights),
        )
        for face, _ in remaining_rows:
            if len(representatives) >= min_refs:
                break
            if face.id in selected_face_ids:
                continue
            selected_face_ids.add(face.id)
            representatives.append(face)

    if len(representatives) >= min_refs:
        return representatives

    fallback_row = session.exec(
        base_query.order_by(desc(Picture.score), Picture.created_at.asc(), Picture.id)
    ).first()
    if fallback_row:
        fallback_face = fallback_row[0]
        if fallback_face and fallback_face.id not in selected_face_ids:
            representatives.append(fallback_face)

    logger.debug(
        "[reference_faces] character_id=%s final_faces=%s",
        character_id,
        len(representatives),
    )

    return representatives


def compute_character_likeness_for_faces(
    reference_faces: list[Face],
    candidate_faces: list[Face],
    combine: str = "softmax",
) -> dict[int, float]:
    """Compute likeness scores for candidate faces against reference faces.

    Args:
        reference_faces: Reference faces to compare against.
        candidate_faces: Candidate faces to score.
        combine: How to aggregate each candidate face's cosine similarity
            across the character's multiple reference faces.

            - ``"softmax"`` (default): the legacy softmax-weighted average
              (alpha=5), which leans toward the best-matching reference faces.
              This is the behaviour every existing caller relied on, so it stays
              the default and their scores are unchanged.
            - ``"mean"`` / ``"max"`` / ``"min"`` / ``"harmonic_mean"`` /
              ``"geometric_mean"``: reduce across reference faces via
              :func:`combine_likeness_scores`. ``"max"`` scores a face on its
              single best-matching reference (lenient), ``"min"`` requires
              matching every reference (strict), ``"mean"`` is the plain average.

    Returns:
        A mapping of face_id to likeness score.
    """

    if not reference_faces or not candidate_faces:
        return {}

    ref_arrs = []
    for ref_face in reference_faces:
        if ref_face.features is None:
            continue
        ref_arr = np.frombuffer(ref_face.features, dtype=np.float32)
        if ref_arr.size == 0:
            continue
        ref_arrs.append(ref_arr)

    if not ref_arrs:
        return {}

    face_vectors = []
    face_ids = []
    for face in candidate_faces:
        if face.features is None:
            continue
        arr_face = np.frombuffer(face.features, dtype=np.float32)
        if arr_face.size == 0:
            continue
        face_vectors.append(arr_face)
        face_ids.append(face.id)

    if not face_vectors:
        return {}

    cand = np.stack(face_vectors)
    ref = np.stack(ref_arrs)
    cand_norm = cand / np.maximum(np.linalg.norm(cand, axis=1, keepdims=True), 1e-8)
    ref_norm = ref / np.maximum(np.linalg.norm(ref, axis=1, keepdims=True), 1e-8)
    # (N_cand, N_ref) per-candidate, per-reference cosine similarity.
    sims = np.clip(cand_norm @ ref_norm.T, -1.0, 1.0)

    if combine == "softmax":
        alpha = 5.0
        weights = np.exp(alpha * sims)
        denom = np.sum(weights, axis=1, keepdims=True)
        denom = np.where(denom == 0, 1.0, denom)
        per_candidate = np.sum(weights * sims, axis=1) / denom.squeeze(1)
    else:
        # combine_likeness_scores reduces across axis 0; we want to reduce across
        # reference faces (axis 1), so transpose to (N_ref, N_cand) and it
        # returns one score per candidate face.
        per_candidate = combine_likeness_scores(sims.T, combine)

    return {
        face_id: float(likeness)
        for face_id, likeness in zip(face_ids, per_candidate, strict=False)
    }


def find_pictures_by_character_likeness(
    server,
    character_id,
    reference_character_id,
    offset,
    limit,
    descending,
    candidate_ids=None,
):
    """List pictures by likeness to a character.

    Args:
        server: The server object.
        character_id: Character id to filter pictures by (or "ALL" or "UNASSIGNED").
        reference_character_id: Character id to use as reference for likeness scoring.
        offset: The number of items to skip before starting to collect the result set.
        limit: The maximum number of items to return.
        descending: Whether to sort in descending order.
        candidate_ids: Optional list of candidate picture ids to filter by.
    """
    reference_character_id = int(reference_character_id)

    timing_start = time.perf_counter()

    reference_faces = server.vault.db.run_task(
        select_reference_faces_for_character,
        reference_character_id,
        10,
        priority=DBPriority.IMMEDIATE,
    )
    timing_after_refs = time.perf_counter()

    if not reference_faces:
        logger.warning("No reference faces found for character id=%s", character_id)
        return []

    def get_all_faces(session, character_id, candidate_ids=None):
        query = select(Face).join(Picture, Face.picture_id == Picture.id)
        if character_id == "ALL" or character_id is None:
            pass
        elif character_id == "UNASSIGNED":
            query = query.where(Face.character_id.is_(None))
        else:
            query = query.where(Face.character_id == int(character_id))
        if candidate_ids is not None:
            if not candidate_ids:
                return []
            query = query.where(Face.picture_id.in_(candidate_ids))
        return session.exec(query).all()

    candidate_faces = server.vault.db.run_task(
        get_all_faces, character_id, candidate_ids
    )
    timing_after_candidates = time.perf_counter()
    if not candidate_faces:
        logger.warning("No unassigned faces found")
        return []

    character_likeness_map = compute_character_likeness_for_faces(
        reference_faces,
        candidate_faces,
    )
    if not character_likeness_map:
        logger.warning(
            "No reference face features found for character id=%s", character_id
        )
        return []
    timing_after_likeness = time.perf_counter()

    picture_likeness_map = {}
    for face in candidate_faces:
        pic_id = face.picture_id
        likeness = character_likeness_map.get(face.id, 0.0)
        if pic_id not in picture_likeness_map:
            picture_likeness_map[pic_id] = likeness
        else:
            picture_likeness_map[pic_id] = max(picture_likeness_map[pic_id], likeness)

    sorted_ids = sorted(
        picture_likeness_map.items(),
        key=lambda item: item[1],
        reverse=descending,
    )
    sorted_ids = [pid for pid, _ in sorted_ids]

    if character_id == "UNASSIGNED" and sorted_ids:

        def filter_unassigned_ids(session: Session, picture_ids: list[int]):
            if not picture_ids:
                return []
            assigned_faces = exists(
                select(Face.id).where(
                    Face.picture_id == Picture.id,
                    Face.character_id.is_not(None),
                )
            )
            in_set = exists(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.picture_id == Picture.id
                )
            )
            rows = session.exec(
                select(Picture.id)
                .where(Picture.id.in_(picture_ids))
                .where(~assigned_faces)
                .where(~in_set)
                .where(Picture.deleted.is_(False))
            ).all()
            return [row for row in rows]

        eligible_ids = set(server.vault.db.run_task(filter_unassigned_ids, sorted_ids))
        sorted_ids = [pid for pid in sorted_ids if pid in eligible_ids]

    selected_ids = sorted_ids[offset : offset + limit]
    if not selected_ids:
        return []

    candidate_pics = server.vault.db.run_task(
        Picture.find,
        id=selected_ids,
        select_fields=Picture.metadata_fields(),
    )
    timing_after_fetch = time.perf_counter()

    logger.debug(
        "[LIKELINESS TIMING] refs=%.3fms candidates=%.3fms likeness=%.3fms fetch=%.3fms total=%.3fms",
        (timing_after_refs - timing_start) * 1000.0,
        (timing_after_candidates - timing_after_refs) * 1000.0,
        (timing_after_likeness - timing_after_candidates) * 1000.0,
        (timing_after_fetch - timing_after_likeness) * 1000.0,
        (timing_after_fetch - timing_start) * 1000.0,
    )

    pic_map = {pic.id: pic for pic in candidate_pics}
    results = []
    for pic_id in selected_ids:
        pic = pic_map.get(pic_id)
        if not pic:
            continue
        pic_dict = safe_model_dict(pic)
        pic_dict["character_likeness"] = max(0.0, picture_likeness_map.get(pic_id, 0.0))
        results.append(pic_dict)

    return results


def pack_reference_blobs(reference_faces: list) -> bytes | None:
    """Pack reference face feature vectors into a binary blob for the character_face_likeness SQL function.

    Args:
        reference_faces: List of Face objects whose features will be packed.

    Returns:
        Bytes object with header (n_refs, vec_size as little-endian int32) followed by
        pre-normalised float32 vectors concatenated, or None if no valid features found.
    """
    vecs = []
    for face in reference_faces:
        if face.features is None:
            continue
        arr = np.frombuffer(face.features, dtype=np.float32)
        if arr.size == 0:
            continue
        vecs.append(arr)
    if not vecs:
        return None
    vec_size = vecs[0].size
    vecs = [v for v in vecs if v.size == vec_size]
    if not vecs:
        return None
    matrix = np.stack(vecs).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms < 1e-8, 1.0, norms)
    matrix_norm = (matrix / norms).astype(np.float32)
    header = struct.pack("<ii", len(vecs), vec_size)
    return header + matrix_norm.tobytes()


def _scoped_stack_leader_clause(character_id, candidate_ids: list[int] | None):
    """Build the scoped-leader stack-collapse clause for CHARACTER_LIKENESS queries.

    Mirrors the ``id_scope`` branch of ``Picture.find`` (picture.py): a stack is
    represented by its lowest-positioned live member *inside the filtered scope*,
    instead of being dropped whenever its global position-0 leader is out of
    scope (e.g. the leader has no face assigned to the viewed character while a
    child does). Shared by the list and count queries so the two can never
    drift.

    The sibling scope is: not deleted, has a Face row satisfying the same
    character filter the outer query applies, and (when ``candidate_ids`` is
    given) is itself in the candidate set - the candidate list carries
    token-scope narrowing, so omitting it would let a share-scoped stack be
    represented by an out-of-scope sibling. Tie-break is identical to
    ``Picture.find``: ``coalesce(stack_position, 999999)`` ascending, then
    lower id wins.

    Args:
        character_id: Character filter (int, None/""/"ALL", or "UNASSIGNED").
        candidate_ids: Optional picture-id restriction (token scope / filters).

    Returns:
        A SQLAlchemy boolean clause to apply with ``.where()``.
    """
    sibling = aliased(Picture)
    sib_face = aliased(Face)
    if character_id == "UNASSIGNED":
        other_sib_face = aliased(Face)
        sibling_in_scope = and_(
            exists(
                select(sib_face.id).where(
                    sib_face.picture_id == sibling.id,
                    sib_face.character_id.is_(None),
                )
            ),
            ~exists(
                select(other_sib_face.id).where(
                    other_sib_face.picture_id == sibling.id,
                    other_sib_face.character_id.is_not(None),
                )
            ),
        )
    elif character_id is not None and character_id != "" and character_id != "ALL":
        sibling_in_scope = exists(
            select(sib_face.id).where(
                sib_face.picture_id == sibling.id,
                sib_face.character_id == int(character_id),
            )
        )
    else:
        sibling_in_scope = exists(
            select(sib_face.id).where(sib_face.picture_id == sibling.id)
        )

    # ``deleted IS NOT 1`` rather than ``deleted IS 0`` on purpose. As an
    # equality term ``deleted`` lets the planner serve the sibling lookup from
    # ``ix_picture_deleted``, which ties on cost with ``ix_picture_stack_id``
    # (no ``sqlite_stat1``, see ``Picture.__table_args__``) and wins or loses
    # on index-creation order. When it won, every face row walked every live
    # picture: 6.5 s for one page on a 12k-picture library, 0.13 s the other
    # way. ``IS NOT`` is not an indexable term, so only the stack_id indexes
    # can answer the correlated lookup, whichever order the indexes were built
    # in. (``~sibling.deleted`` does not do this: SQLAlchemy compiles it to
    # ``deleted = 0`` for SQLite, which is indexable again.)
    conditions = [
        sibling.stack_id == Picture.stack_id,
        sibling.deleted.is_not(True),
        sibling_in_scope,
    ]
    if candidate_ids is not None:
        conditions.append(sibling.id.in_(candidate_ids))
    cur_pos = func.coalesce(Picture.stack_position, 999999)
    sib_pos = func.coalesce(sibling.stack_position, 999999)
    conditions.append(
        or_(
            sib_pos < cur_pos,
            and_(sib_pos == cur_pos, sibling.id < Picture.id),
        )
    )
    has_higher_ranked_sibling = exists(select(sibling.id).where(*conditions))
    return or_(Picture.stack_id.is_(None), ~has_higher_ranked_sibling)


def find_pictures_by_character_likeness_sql(
    server,
    character_id,
    reference_character_id,
    offset: int,
    limit: int,
    descending: bool,
    candidate_ids: list[int] | None = None,
    deleted_only: bool = False,
    stack_leaders_only: bool = False,
) -> list[dict]:
    """List pictures by character likeness using SQL ORDER BY with LIMIT/OFFSET.

    Uses the character_face_likeness SQLite scalar function so sorting and pagination
    happen entirely at the SQL layer, enabling the fast grid streaming path.

    Args:
        server: The server object.
        character_id: Character id to filter candidate faces by (int, None/"ALL", or "UNASSIGNED").
        reference_character_id: Character whose reference faces define the likeness target.
        offset: Number of rows to skip.
        limit: Maximum number of rows to return.
        descending: If True, highest-likeness pictures come first.
        candidate_ids: Optional list of picture ids to restrict the search to.
        deleted_only: If True, restrict to deleted (scrapheap) pictures only.
        stack_leaders_only: If True, collapse each stack to one representative
            using scoped-leader semantics (see _scoped_stack_leader_clause).

    Returns:
        List of picture metadata dicts with a "character_likeness" field added.
    """
    reference_character_id = int(reference_character_id)
    timing_start = time.perf_counter()

    reference_faces = server.vault.db.run_task(
        select_reference_faces_for_character,
        reference_character_id,
        10,
        priority=DBPriority.IMMEDIATE,
    )
    if not reference_faces:
        logger.warning(
            "No reference faces found for character id=%s", reference_character_id
        )
        return []

    refs_blob = pack_reference_blobs(reference_faces)
    if refs_blob is None:
        logger.warning(
            "No valid reference face features for character id=%s",
            reference_character_id,
        )
        return []

    timing_after_refs = time.perf_counter()

    def run_query(session: Session):
        max_likeness = func.max(
            func.character_face_likeness(Face.features, refs_blob)
        ).label("likeness_score")
        order_expr = (
            desc(text("likeness_score")) if descending else asc(text("likeness_score"))
        )
        deleted_filter = (
            Picture.deleted.is_(True) if deleted_only else Picture.deleted.is_(False)
        )
        query = (
            select(Face.picture_id, max_likeness)
            .join(Picture, Face.picture_id == Picture.id)
            .where(deleted_filter)
            .group_by(Face.picture_id)
            .order_by(order_expr)
            .limit(limit)
            .offset(offset)
        )
        if stack_leaders_only:
            query = query.where(
                _scoped_stack_leader_clause(character_id, candidate_ids)
            )
        if character_id == "UNASSIGNED":
            other_face = aliased(Face)
            query = query.where(Face.character_id.is_(None))
            query = query.where(
                ~exists(
                    select(other_face.id)
                    .where(
                        other_face.picture_id == Face.picture_id,
                        other_face.character_id.is_not(None),
                    )
                    .correlate(Face)
                )
            )
        elif character_id is not None and character_id != "" and character_id != "ALL":
            query = query.where(Face.character_id == int(character_id))
        if candidate_ids is not None:
            query = query.where(Face.picture_id.in_(candidate_ids))
        return session.exec(query).all()

    rows = server.vault.db.run_task(run_query, priority=DBPriority.IMMEDIATE)
    timing_after_query = time.perf_counter()

    if not rows:
        return []

    selected_ids = [row[0] for row in rows]
    likeness_by_pic = {row[0]: float(row[1]) for row in rows}

    candidate_pics = server.vault.db.run_task(
        Picture.find,
        id=selected_ids,
        select_fields=Picture.metadata_fields(),
        only_deleted=deleted_only,
    )
    timing_after_fetch = time.perf_counter()

    logger.debug(
        "[LIKENESS SQL TIMING] refs=%.3fms query=%.3fms fetch=%.3fms total=%.3fms",
        (timing_after_refs - timing_start) * 1000.0,
        (timing_after_query - timing_after_refs) * 1000.0,
        (timing_after_fetch - timing_after_query) * 1000.0,
        (timing_after_fetch - timing_start) * 1000.0,
    )

    pic_map = {pic.id: pic for pic in candidate_pics}
    results = []
    for pic_id in selected_ids:
        pic = pic_map.get(pic_id)
        if not pic:
            continue
        pic_dict = safe_model_dict(pic)
        pic_dict["character_likeness"] = max(0.0, likeness_by_pic.get(pic_id, 0.0))
        results.append(pic_dict)

    return results


def count_pictures_by_character_likeness(
    server,
    character_id,
    candidate_ids: list[int] | None = None,
    deleted_only: bool = False,
    stack_leaders_only: bool = False,
) -> int:
    """Count pictures that would be returned by a CHARACTER_LIKENESS sort query.

    Does not require the reference character or likeness scoring - it simply counts
    distinct picture_ids matching the character and candidate filters.

    Args:
        server: The server object.
        character_id: Character id filter (int, None/"ALL", or "UNASSIGNED").
        candidate_ids: Optional list of picture ids to restrict the count to.
        deleted_only: If True, restrict to deleted (scrapheap) pictures only.
        stack_leaders_only: If True, collapse each stack to one representative
            using scoped-leader semantics (see _scoped_stack_leader_clause), so
            the count matches what find_pictures_by_character_likeness_sql yields.

    Returns:
        Total number of distinct matching pictures.
    """

    def run_count(session: Session) -> int:
        deleted_filter = (
            Picture.deleted.is_(True) if deleted_only else Picture.deleted.is_(False)
        )
        query = (
            select(func.count(func.distinct(Face.picture_id)))
            .join(Picture, Face.picture_id == Picture.id)
            .where(deleted_filter)
        )
        if stack_leaders_only:
            query = query.where(
                _scoped_stack_leader_clause(character_id, candidate_ids)
            )
        if character_id == "UNASSIGNED":
            inner_face = aliased(Face)
            query = query.where(Face.character_id.is_(None))
            query = query.where(
                ~exists(
                    select(inner_face.id)
                    .where(
                        inner_face.picture_id == Face.picture_id,
                        inner_face.character_id.is_not(None),
                    )
                    .correlate(Face)
                )
            )
        elif character_id is not None and character_id != "" and character_id != "ALL":
            query = query.where(Face.character_id == int(character_id))
        if candidate_ids is not None:
            query = query.where(Face.picture_id.in_(candidate_ids))
        result = session.exec(query).first()
        return int(result) if result is not None else 0

    return server.vault.db.run_task(run_count, priority=DBPriority.IMMEDIATE)
