import functools
import pathlib
import struct
import time
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from sqlalchemy import asc, exists, desc, func, or_, text
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    Face,
    Picture,
    PictureSetMember,
    Quality,
    Tag,
    TagPrediction,
    User,
)
from pixlstash.services.tagger_run_service import get_latest_tag_precisions
from pixlstash.utils.quality.anomaly_penalty import ANOMALY_PENALTY_TAGS
from pixlstash.utils.quality.smart_score_utils import (
    SmartScoreUtils,
    smart_score_penalised_tags,
)
from pixlstash.utils.service.label_ledger import HUMAN, NEG, POS
from pixlstash.utils.service.filter_helpers import combine_likeness_scores
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# If the user has fewer than this many rated images in a category, built-in
# anchor embeddings are added to prevent empty-anchor edge cases.
_BUILTIN_MIN_GOOD = 10
_BUILTIN_MIN_BAD = 10


@dataclass
class _BuiltinAnchor:
    """Thin wrapper so built-in numpy embeddings look like DB anchor rows."""

    image_embedding: np.ndarray
    score: int


@functools.lru_cache(maxsize=1)
def _load_builtin_anchors() -> tuple[list["_BuiltinAnchor"], list["_BuiltinAnchor"]]:
    """Load pre-computed built-in CLIP anchor embeddings from package data.

    Returns:
        Tuple of (good_anchors, bad_anchors) where each element is a list of
        _BuiltinAnchor objects compatible with prepare_smart_score_inputs.
    """
    data_dir = pathlib.Path(__file__).parent / "data" / "anchors"
    good_path = data_dir / "builtin_good.npy"
    bad_path = data_dir / "builtin_bad.npy"

    def _load(path: pathlib.Path, score: int) -> list[_BuiltinAnchor]:
        if not path.is_file():
            logger.debug("Built-in anchor file not found: %s", path)
            return []
        try:
            arr = np.load(path)
            if arr.ndim != 2 or arr.shape[1] == 0:
                logger.warning("Unexpected shape in %s: %s", path.name, arr.shape)
                return []
            return [
                _BuiltinAnchor(image_embedding=arr[i], score=score)
                for i in range(len(arr))
            ]
        except Exception as e:
            logger.warning("Failed to load built-in anchor file %s: %s", path.name, e)
            return []

    good = _load(good_path, score=4)
    bad = _load(bad_path, score=1)
    logger.debug(
        "Loaded built-in anchors: %d good, %d bad",
        len(good),
        len(bad),
    )
    return good, bad


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


def get_smart_score_penalised_tags_from_request(server, request):
    user_id = server.auth.get_user_id(request)
    if user_id is None:
        return DEFAULT_SMART_SCORE_PENALIZED_TAGS
    user = server.vault.db.run_task(
        lambda session: session.get(User, user_id),
        priority=DBPriority.IMMEDIATE,
    )
    return smart_score_penalised_tags(
        user.smart_score_penalised_tags if user else None,
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )


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
                or_(Picture.stack_id.is_(None), Picture.stack_position == 0)
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

    Does not require the reference character or likeness scoring — it simply counts
    distinct picture_ids matching the character and candidate filters.

    Args:
        server: The server object.
        character_id: Character id filter (int, None/"ALL", or "UNASSIGNED").
        candidate_ids: Optional list of picture ids to restrict the count to.
        deleted_only: If True, restrict to deleted (scrapheap) pictures only.

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
                or_(Picture.stack_id.is_(None), Picture.stack_position == 0)
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


def fetch_anomaly_confidences(session: Session, picture_ids) -> tuple[dict, dict]:
    """Return per-picture anomaly probabilities and human-verified tags.

    Reads ``TagPrediction`` for the anomaly vocabulary only. A human decision in the
    label ledger overrides the model: a human POS folds to probability 1.0 (and is
    flagged so the penalty bypasses the precision floor), a human NEG folds to 0.0.

    Returns:
        ``(probs_map, human_map)`` where ``probs_map`` is
        ``{picture_id: {tag: probability}}`` and ``human_map`` is
        ``{picture_id: {tag, ...}}`` of human-verified present tags.
    """
    probs_map: dict = defaultdict(dict)
    human_map: dict = defaultdict(set)
    if not picture_ids:
        return probs_map, human_map

    rows = session.exec(
        select(
            TagPrediction.picture_id,
            TagPrediction.tag,
            TagPrediction.confidence,
            TagPrediction.label_state,
            TagPrediction.label_source,
        ).where(
            TagPrediction.picture_id.in_(picture_ids),
            func.lower(TagPrediction.tag).in_(ANOMALY_PENALTY_TAGS),
        )
    ).all()

    for picture_id, tag, confidence, label_state, label_source in rows:
        if not tag:
            continue
        key = tag.strip().lower()
        if label_source == HUMAN and label_state == POS:
            probs_map[picture_id][key] = 1.0
            human_map[picture_id].add(key)
        elif label_source == HUMAN and label_state == NEG:
            probs_map[picture_id][key] = 0.0
        else:
            probs_map[picture_id][key] = (
                float(confidence) if confidence is not None else 0.0
            )

    return probs_map, human_map


def attach_anomaly_inputs(session: Session, candidates) -> dict:
    """Attach calibrated anomaly inputs to candidate dicts; return the precision map.

    Adds ``anomaly_probs`` and ``anomaly_human`` to each candidate (see
    :func:`fetch_anomaly_confidences`) and returns ``{tag: precision}`` from the latest
    evaluated :class:`TaggerRun` for the scorer. Shared by both smart-score fetch paths
    so the on-demand sort and the background task stay in lockstep.
    """
    precisions = get_latest_tag_precisions(session)
    ids = [c.get("id") for c in candidates if c.get("id") is not None]
    if not ids:
        return precisions
    probs_map, human_map = fetch_anomaly_confidences(session, ids)
    for candidate in candidates:
        pid = candidate.get("id")
        candidate["anomaly_probs"] = probs_map.get(pid, {})
        candidate["anomaly_human"] = human_map.get(pid, frozenset())
    return precisions


def fetch_smart_score_data(
    server,
    format,
    candidate_ids=None,
    penalised_tags=None,
    include_deleted: bool = False,
    only_deleted: bool = False,
):
    """Fetch anchors, character references, and candidates for smart score calculation.

    Returns ``(good_anchors, bad_anchors, candidates, tag_precisions)``. ``penalised_tags``
    is retained for signature compatibility but no longer drives the score: the calibrated
    anomaly penalty (see :mod:`pixlstash.utils.quality.anomaly_penalty`) supersedes the old
    per-user integer weights.
    """

    def fetch_data(session: Session):
        # Anchors
        good = session.exec(
            select(Picture.image_embedding, Picture.score)
            .where(Picture.score >= 4)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.deleted.is_(False))
            .order_by(desc(Picture.score), desc(Picture.created_at))
            .limit(200)
        ).all()

        bad = session.exec(
            select(Picture.image_embedding, Picture.score)
            .where(Picture.score <= 1)
            .where(Picture.score > 0)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.deleted.is_(False))
            .order_by(Picture.score, desc(Picture.created_at))
            .limit(200)
        ).all()

        # Candidates — join to picture-level quality rows.
        query = select(Picture, Quality).outerjoin(
            Quality,
            Quality.picture_id == Picture.id,
        )
        if only_deleted:
            query = query.where(Picture.deleted.is_(True))
        elif not include_deleted:
            query = query.where(Picture.deleted.is_(False))

        if candidate_ids is not None:
            if not candidate_ids:
                return good, bad, [], {}
            query = query.where(Picture.id.in_(candidate_ids))

        if format:
            query = query.where(Picture.format.in_(format))

        query = query.where(Picture.image_embedding.is_not(None))

        candidate_rows = session.exec(query).all()

        candidates = []
        for pic, quality in candidate_rows:
            aest = pic.aesthetic_score
            quality_score = None
            if quality is not None:
                try:
                    quality_score = quality.calculate_quality_score()
                except Exception as e:
                    logger.warning(
                        "Failed to compute heuristic quality score for picture %s: %s",
                        pic.id,
                        e,
                    )
            if aest is None:
                aest = quality_score
            candidates.append(
                {
                    "id": pic.id,
                    "image_embedding": pic.image_embedding,
                    "aesthetic_score": aest,
                    "width": pic.width,
                    "height": pic.height,
                    "sharpness": quality.sharpness if quality else None,
                    "edge_density": quality.edge_density if quality else None,
                    "luminance_entropy": (
                        quality.luminance_entropy if quality else None
                    ),
                    "noise_level": quality.noise_level if quality else None,
                    "colorfulness": quality.colorfulness if quality else None,
                    "text_score": pic.text_score,
                }
            )

        # Calibrated anomaly inputs + per-tag precision (replaces the old per-user
        # integer-weight tag sum).
        tag_precisions = attach_anomaly_inputs(session, candidates)

        # Supplement with built-in anchors when the user has few rated images.
        builtin_good, builtin_bad = _load_builtin_anchors()
        if len(good) < _BUILTIN_MIN_GOOD:
            good = list(good) + builtin_good
        if len(bad) < _BUILTIN_MIN_BAD:
            bad = list(bad) + builtin_bad

        return good, bad, candidates, tag_precisions

    return server.vault.db.run_immediate_read_task(fetch_data)


def fetch_smart_score_unscored_ids(
    server,
    format,
    candidate_ids=None,
    descending=True,
    include_deleted: bool = False,
    only_deleted: bool = False,
):
    def fetch_ids(session: Session):
        query = select(Picture.id)
        if only_deleted:
            query = query.where(Picture.deleted.is_(True))
        elif not include_deleted:
            query = query.where(Picture.deleted.is_(False))

        if candidate_ids is not None:
            if not candidate_ids:
                return []
            query = query.where(Picture.id.in_(candidate_ids))

        if format:
            query = query.where(Picture.format.in_(format))

        query = query.where(Picture.image_embedding.is_(None))

        if descending:
            query = query.order_by(desc(Picture.created_at), desc(Picture.id))
        else:
            query = query.order_by(Picture.created_at, Picture.id)

        return [row for row in session.exec(query).all()]

    return server.vault.db.run_task(fetch_ids, priority=DBPriority.IMMEDIATE)


def prepare_smart_score_inputs(good_anchors, bad_anchors, candidates):
    """Decode embeddings and prepare lists of dictionaries for calculation."""

    def get_attr(item, key):
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    def get_vec(blob):
        if blob is None:
            return None
        if isinstance(blob, (memoryview, bytearray)):
            blob = bytes(blob)
        if isinstance(blob, np.ndarray):
            arr = np.asarray(blob, dtype=np.float32)
            return arr if arr.ndim == 1 and arr.size > 0 else None
        if not isinstance(blob, (bytes, bytearray)):
            try:
                blob = bytes(blob)
            except Exception:
                return None
        try:
            arr = np.frombuffer(blob, dtype=np.float32)
            if arr.ndim != 1 or arr.size == 0:
                return None
            return arr.copy()
        except Exception:
            return None

    def process_list(items):
        result = []
        for p in items:
            v = get_vec(p.image_embedding)
            if v is not None:
                result.append({"embedding": v, "score": getattr(p, "score", 0)})
        return result

    good_list = process_list(good_anchors)
    bad_list = process_list(bad_anchors)

    cand_list = []
    cand_ids = []

    for p in candidates:
        pid = get_attr(p, "id")
        v = get_vec(get_attr(p, "image_embedding"))
        if v is not None:
            cand_ids.append(pid)
            cand_list.append(
                {
                    "id": pid,
                    "embedding": v,
                    "aesthetic_score": get_attr(p, "aesthetic_score"),
                    "anomaly_probs": get_attr(p, "anomaly_probs") or {},
                    "anomaly_human": get_attr(p, "anomaly_human") or frozenset(),
                    "width": get_attr(p, "width"),
                    "height": get_attr(p, "height"),
                    "sharpness": get_attr(p, "sharpness"),
                    "edge_density": get_attr(p, "edge_density"),
                    "luminance_entropy": get_attr(p, "luminance_entropy"),
                    "noise_level": get_attr(p, "noise_level"),
                    "colorfulness": get_attr(p, "colorfulness"),
                    "text_score": get_attr(p, "text_score"),
                }
            )

    return good_list, bad_list, cand_list, cand_ids


def find_pictures_by_smart_score(
    server,
    format,
    offset,
    limit,
    descending,
    candidate_ids=None,
    penalised_tags=None,
    include_deleted: bool = False,
    only_deleted: bool = False,
    progress_reporter=None,
):
    def report_progress(status: str, current: int, total: int, message: str):
        if not callable(progress_reporter):
            return
        safe_total = max(0, int(total or 0))
        safe_current = max(0, min(int(current or 0), safe_total))
        progress = (safe_current / safe_total * 100.0) if safe_total else 0.0
        try:
            progress_reporter(
                {
                    "status": status,
                    "current": safe_current,
                    "total": safe_total,
                    "progress": progress,
                    "message": message,
                }
            )
        except Exception:
            # Progress reporting should never break sorting.
            logger.debug("Progress reporting failed during sort.", exc_info=True)

    # 1. Fetch data
    good_anchors, bad_anchors, candidates, tag_precisions = fetch_smart_score_data(
        server,
        format,
        candidate_ids=candidate_ids,
        penalised_tags=penalised_tags,
        include_deleted=include_deleted,
        only_deleted=only_deleted,
    )

    unscored_ids = fetch_smart_score_unscored_ids(
        server,
        format,
        candidate_ids=candidate_ids,
        descending=descending,
        include_deleted=include_deleted,
        only_deleted=only_deleted,
    )

    score_map = {}
    scored_ids = []

    if candidates:
        good_list, bad_list, cand_list, cand_ids = prepare_smart_score_inputs(
            good_anchors, bad_anchors, candidates
        )

        if cand_list:
            total_candidates = len(cand_list)
            report_progress(
                "running",
                0,
                total_candidates,
                f"Calculating smart scores (0/{total_candidates})",
            )

            chunk_size = 1024
            score_chunks = []
            processed = 0
            for start in range(0, total_candidates, chunk_size):
                end = min(start + chunk_size, total_candidates)
                batch = cand_list[start:end]
                batch_scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
                    batch,
                    good_list,
                    bad_list,
                    config={"tag_precisions": tag_precisions},
                )
                score_chunks.append(np.asarray(batch_scores, dtype=np.float32))
                processed = end
                report_progress(
                    "running",
                    processed,
                    total_candidates,
                    f"Calculating smart scores ({processed}/{total_candidates})",
                )

            scores = (
                np.concatenate(score_chunks)
                if len(score_chunks) > 1
                else (score_chunks[0] if score_chunks else np.array([]))
            )

            # Primary sort key is raw smart score so UI labels and ordering
            # always align. Picture ID is a deterministic tiebreaker.
            scores_array = np.asarray(scores, dtype=np.float32)
            ids_array = np.array(cand_ids, dtype=np.int64)
            if descending:
                # lexsort key order: last key is primary.
                # Primary: -score (highest score first)
                # Secondary: id (lowest id first within tied bucket)
                sorted_indices = np.lexsort((ids_array, -scores_array))
            else:
                sorted_indices = np.lexsort((ids_array, scores_array))

            scored_ids = [cand_ids[i] for i in sorted_indices]
            score_map = {cand_ids[i]: float(scores[i]) for i in range(len(scores))}
            report_progress(
                "completed",
                total_candidates,
                total_candidates,
                f"Calculated smart scores ({total_candidates}/{total_candidates})",
            )

    combined_ids = scored_ids + unscored_ids
    if not combined_ids:
        return []

    seen = set()
    unique_ids = []
    for pid in combined_ids:
        if pid is None:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        unique_ids.append(pid)

    final_ids = unique_ids[offset : offset + limit]

    if len(final_ids) == 0:
        return []

    def fetch_final_pics(session, ids):
        return session.exec(select(Picture).where(Picture.id.in_(ids))).all()

    res_pics = server.vault.db.run_task(
        fetch_final_pics, final_ids, priority=DBPriority.IMMEDIATE
    )
    pmap = {p.id: p for p in res_pics}
    metadata_fields = Picture.metadata_fields()

    results = []
    for pid in final_ids:
        if pid in pmap:
            p = pmap[pid]
            d = {field: getattr(p, field) for field in metadata_fields}
            d["smartScore"] = score_map.get(pid)
            results.append(d)

    return results
