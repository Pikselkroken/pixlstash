import pickle
import time
from datetime import datetime
from collections import defaultdict

import numpy as np
from sqlalchemy import exists, desc, func
from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.db_models import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    Face,
    Picture,
    PictureSetMember,
    Quality,
    Tag,
    User,
)
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger
from pixlvault.utils import normalize_smart_score_penalized_tags, safe_model_dict

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
            Face.face_index == 0,
            Face.features.is_not(None),
        )
    )

    rows = session.exec(
        base_query.where(Picture.score >= 5)
        .order_by(Picture.created_at.asc(), Picture.id.asc())
        .limit(max_refs)
    ).all()

    logger.info(
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
    logger.info(
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
    logger.info(
        "[reference_faces] character_id=%s remaining_rows=%s selected_pictures=%s",
        character_id,
        len(remaining_rows),
        len(selected_picture_ids),
    )
    if remaining_rows:
        penalized_tags = normalize_smart_score_penalized_tags(
            None,
            DEFAULT_SMART_SCORE_PENALIZED_TAGS,
            default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
        )
        penalized_tag_set = {
            str(tag).strip().lower() for tag in penalized_tags.keys() if tag
        }
        remaining_picture_ids = [picture.id for _, picture in remaining_rows]
        tag_weights = defaultdict(float)
        if penalized_tag_set and remaining_picture_ids:
            tag_rows = session.exec(
                select(Tag.picture_id, Tag.tag)
                .where(Tag.picture_id.in_(remaining_picture_ids))
                .where(Tag.tag.is_not(None))
                .where(func.lower(Tag.tag).in_(penalized_tag_set))
            ).all()
            for pic_id, tag in tag_rows or []:
                if not tag:
                    continue
                tag_weights[pic_id] += penalized_tags.get(tag.strip().lower(), 0.0)

        remaining_rows.sort(
            key=lambda row: (
                tag_weights.get(row[1].id, 0.0),
                row[1].created_at or datetime.max,
                row[1].id,
                row[0].id or 0,
            )
        )
        logger.info(
            "[reference_faces] character_id=%s penalized_tags=%s",
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

    logger.info(
        "[reference_faces] character_id=%s final_faces=%s",
        character_id,
        len(representatives),
    )

    return representatives


def get_smart_score_penalized_tags_from_request(server, request):
    user_id = server.auth.get_user_id(request)
    if user_id is None:
        return DEFAULT_SMART_SCORE_PENALIZED_TAGS
    user = server.vault.db.run_task(
        lambda session: session.get(User, user_id),
        priority=DBPriority.IMMEDIATE,
    )
    return normalize_smart_score_penalized_tags(
        user.smart_score_penalized_tags if user else None,
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )


def compute_character_likeness_for_faces(
    reference_faces: list[Face],
    candidate_faces: list[Face],
) -> dict[int, float]:
    """Compute likeness scores for candidate faces against reference faces.

    Args:
        reference_faces: Reference faces to compare against.
        candidate_faces: Candidate faces to score.

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
    sims = cand_norm @ ref_norm.T
    alpha = 5.0
    sims = np.clip(sims, -1.0, 1.0)
    weights = np.exp(alpha * sims)
    denom = np.sum(weights, axis=1, keepdims=True)
    denom = np.where(denom == 0, 1.0, denom)
    softmax_avg = np.sum(weights * sims, axis=1) / denom.squeeze(1)

    return {
        face_id: float(likeness)
        for face_id, likeness in zip(face_ids, softmax_avg, strict=False)
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
        query = select(Face)
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
        faces = session.exec(query).all()
        return faces

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

    logger.info(
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
        pic_dict["character_likeness"] = picture_likeness_map.get(pic_id, 0.0)
        results.append(pic_dict)

    return results


def fetch_smart_score_data(server, format, candidate_ids=None, penalized_tags=None):
    """Fetch anchors, character references, and candidates for smart score calculation."""

    def fetch_data(session: Session):
        # Anchors
        good = session.exec(
            select(Picture.image_embedding, Picture.score)
            .where(Picture.score >= 4)
            .where(Picture.image_embedding.is_not(None))
            .order_by(desc(Picture.score), desc(Picture.created_at))
            .limit(200)
        ).all()

        bad = session.exec(
            select(Picture.image_embedding, Picture.score)
            .where(Picture.score <= 1)
            .where(Picture.score > 0)
            .where(Picture.image_embedding.is_not(None))
            .order_by(Picture.score, desc(Picture.created_at))
            .limit(200)
        ).all()

        # Candidates
        query = select(Picture, Quality).outerjoin(
            Quality, Quality.picture_id == Picture.id
        )

        if candidate_ids is not None:
            if not candidate_ids:
                return good, bad, [], {}
            query = query.where(Picture.id.in_(candidate_ids))

        if format:
            query = query.where(Picture.format.in_(format))

        query = query.where(Picture.image_embedding.is_not(None))

        candidate_rows = session.exec(query).all()

        penalized_tag_weights = {
            str(tag).strip().lower(): int(weight)
            for tag, weight in (penalized_tags or {}).items()
            if str(tag).strip()
        }

        candidates = []
        candidate_id_list = []
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
                    "noise_level": quality.noise_level if quality else None,
                    "edge_density": quality.edge_density if quality else None,
                }
            )
            candidate_id_list.append(pic.id)

        penalized_tag_map = defaultdict(int)
        if penalized_tag_weights and candidate_id_list:
            tag_rows = session.exec(
                select(Tag.picture_id, Tag.tag).where(
                    Tag.picture_id.in_(candidate_id_list),
                )
            ).all()
            for pic_id, tag in tag_rows:
                if not tag:
                    continue
                key = tag.strip().lower()
                weight = penalized_tag_weights.get(key)
                if weight is not None:
                    penalized_tag_map[pic_id] += weight

            if penalized_tag_map:
                for candidate in candidates:
                    candidate["penalized_tag_count"] = penalized_tag_map.get(
                        candidate["id"], 0
                    )

        return good, bad, candidates

    return server.vault.db.run_task(fetch_data, priority=DBPriority.IMMEDIATE)


def fetch_smart_score_unscored_ids(server, format, candidate_ids=None, descending=True):
    def fetch_ids(session: Session):
        query = select(Picture.id)

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
    """Unpickle embeddings and prepare lists of dictionaries for calculation."""

    def get_attr(item, key):
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    def get_vec(blob):
        if blob is None:
            return None
        if isinstance(blob, memoryview):
            blob = blob.tobytes()
        try:
            obj = pickle.loads(blob)
            if isinstance(obj, np.ndarray):
                if obj.ndim == 1 and obj.size > 0:
                    return obj
            else:
                arr = np.array(obj)
                if arr.ndim == 1 and arr.size > 0:
                    return arr
        except Exception:
            pass
        try:
            arr = np.frombuffer(blob, dtype=np.float32)
            return arr if arr.size else None
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
                    "penalized_tag_count": get_attr(p, "penalized_tag_count") or 0,
                    "width": get_attr(p, "width"),
                    "height": get_attr(p, "height"),
                    "noise_level": get_attr(p, "noise_level"),
                    "edge_density": get_attr(p, "edge_density"),
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
    penalized_tags=None,
):
    # 1. Fetch data
    good_anchors, bad_anchors, candidates = fetch_smart_score_data(
        server,
        format,
        candidate_ids=candidate_ids,
        penalized_tags=penalized_tags,
    )

    unscored_ids = fetch_smart_score_unscored_ids(
        server,
        format,
        candidate_ids=candidate_ids,
        descending=descending,
    )

    score_map = {}
    scored_ids = []

    if candidates:
        # 2. Prepare inputs (unpickling)
        good_list, bad_list, cand_list, cand_ids = prepare_smart_score_inputs(
            good_anchors, bad_anchors, candidates
        )

        if cand_list:
            # 3. Calculate Scores (delegated to PictureUtils)
            scores = PictureUtils.calculate_smart_score_batch_numpy(
                cand_list, good_list, bad_list
            )

            # 4. Sort and build scored id list
            if descending:
                sorted_indices = np.argsort(-scores)
            else:
                sorted_indices = np.argsort(scores)

            scored_ids = [cand_ids[i] for i in sorted_indices]
            score_map = {cand_ids[i]: float(scores[i]) for i in range(len(scores))}

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

    # 5. Fetch Final Objects
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
