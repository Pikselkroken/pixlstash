import pickle
from collections import defaultdict

import numpy as np
from sqlalchemy import exists, desc, func
from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.db_models import (
    Character,
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    Face,
    FaceCharacterLikeness,
    Picture,
    PictureSet,
    PictureSetMember,
    Quality,
    Tag,
    User,
)
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger
from pixlvault.utils import normalize_smart_score_penalized_tags, safe_model_dict

logger = get_logger(__name__)


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


def find_pictures_by_character_likeness(
    server, character_id, reference_character_id, offset, limit, descending
):
    reference_character_id = int(reference_character_id)

    # List pictures by likeness to character
    # 1. Fetch reference faces from the reference pictures set for character
    # 2. Fetch all faces
    # 3. Order them by average likeness to reference faces
    # 4. Return pictures containing those faces in the same order as the faces
    def get_character_reference_faces(session, reference_character_id):
        # Need to get pictures in the reference set for this character
        character = Character.find(session, id=reference_character_id)
        reference_set = session.get(PictureSet, character[0].reference_picture_set_id)
        if not reference_set:
            return []
        members = session.exec(
            select(PictureSetMember).where(PictureSetMember.set_id == reference_set.id)
        ).all()
        picture_ids = [m.picture_id for m in members]
        if not picture_ids:
            logger.debug(
                "No pictures in reference set id=%s for character id=%s",
                reference_set.id,
                character_id,
            )
            return []
        faces = Face.find(session, picture_id=picture_ids)
        return faces

    # 1. Get reference faces (use set of face IDs for uniqueness)
    reference_faces = server.vault.db.run_task(
        get_character_reference_faces,
        reference_character_id,
        priority=DBPriority.IMMEDIATE,
    )

    if not reference_faces:
        logger.warning("No reference faces found for character id=%s", character_id)
        return []

    # 2. Get all faces
    def get_all_faces(session, character_id):
        query = select(Face)
        if character_id == "ALL" or character_id is None:
            pass
        elif character_id == "UNASSIGNED":
            query = query.where(Face.character_id.is_(None))
        else:
            query = query.where(Face.character_id == int(character_id))
        faces = session.exec(query).all()
        return faces

    candidate_faces = server.vault.db.run_task(get_all_faces, character_id)
    if not candidate_faces:
        logger.warning("No unassigned faces found")
        return []

    # Fetch likeness scores directly from FaceCharacterLikeness
    def fetch_character_likeness(session, reference_character_id):
        rows = session.exec(
            select(FaceCharacterLikeness.face_id, FaceCharacterLikeness.likeness).where(
                FaceCharacterLikeness.character_id == reference_character_id
            )
        ).all()
        return {row.face_id: row.likeness for row in rows}

    character_likeness_map = server.vault.db.run_task(
        fetch_character_likeness, reference_character_id
    )

    # Debug logging for character likeness map
    logger.debug("Character likeness map: %s", character_likeness_map)

    # 3. Get unique picture IDs in that order
    # For each picture, use the maximum character_likeness among all its unassigned faces
    picture_likeness_map = {}
    for face in candidate_faces:
        pic_id = face.picture_id
        likeness = character_likeness_map.get(face.id, 0.0)
        if pic_id not in picture_likeness_map:
            picture_likeness_map[pic_id] = likeness
        else:
            picture_likeness_map[pic_id] = max(picture_likeness_map[pic_id], likeness)

    # Debug logging for picture likeness map
    logger.debug("Picture likeness map: %s", picture_likeness_map)

    # Fetch Picture objects
    candidate_pics = server.vault.db.run_task(
        Picture.find,
        id=list(picture_likeness_map.keys()),
        select_fields=Picture.metadata_fields() | {"characters", "picture_sets"},
    )

    # Assign character_likeness to pictures
    dicts = []
    for pic in candidate_pics:
        if character_id == "UNASSIGNED":
            character_ids = [c.id for c in pic.characters]
            if reference_character_id in character_ids or character_ids:
                # Skip pictures that already have any characters assigned
                continue
            if getattr(pic, "picture_sets", None):
                if pic.picture_sets:
                    # Skip pictures that are already in a picture set
                    continue
        pic_dict = safe_model_dict(pic)
        pic_id = pic_dict["id"]
        pic_dict["character_likeness"] = picture_likeness_map.get(pic_id, 0.0)
        dicts.append(pic_dict)

    # Sort by character_likeness honoring descending flag
    dicts.sort(key=lambda x: x["character_likeness"], reverse=descending)

    # Apply offset and limit
    selected_pics = dicts[offset : offset + limit]
    return selected_pics


def fetch_smart_score_data(
    server, character_id, format, candidate_ids=None, penalized_tags=None
):
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

        # Apply Filter Logic Matches list_pictures
        if character_id == "UNASSIGNED":
            query = query.where(
                ~exists(
                    select(Face.id).where(
                        Face.picture_id == Picture.id,
                        Face.character_id.is_not(None),
                    )
                ),
                ~exists(
                    select(PictureSetMember.picture_id).where(
                        PictureSetMember.picture_id == Picture.id
                    )
                ),
            )
        elif character_id and character_id != "ALL":
            try:
                cid = int(character_id)
                character_picture_ids = session.exec(
                    select(Face.picture_id).where(Face.character_id == cid)
                ).all()
                if not character_picture_ids:
                    return good, bad, [], {}
                query = query.where(Picture.id.in_(character_picture_ids))
            except ValueError:
                pass

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

        # Pre-fetch Max Face-Character Likeness Map for Candidates
        pic_likeness_map = {}
        char_id = None
        if character_id is not None:
            try:
                char_id = int(character_id)
            except (TypeError, ValueError):
                char_id = None
        if candidate_id_list and char_id is not None:
            try:
                stmt = (
                    select(Face.picture_id, func.max(FaceCharacterLikeness.likeness))
                    .join(
                        FaceCharacterLikeness,
                        Face.id == FaceCharacterLikeness.face_id,
                    )
                    .where(Face.picture_id.in_(candidate_id_list))
                    .where(Face.character_id == char_id)
                    .where(FaceCharacterLikeness.character_id == char_id)
                    .group_by(Face.picture_id)
                )
                rows = session.exec(stmt).all()
                pic_likeness_map = {r[0]: r[1] for r in rows}
            except Exception as e:
                logger.warning("Failed to fetch likeness map: %s", e)

        return good, bad, candidates, pic_likeness_map

    return server.vault.db.run_task(fetch_data, priority=DBPriority.IMMEDIATE)


def fetch_smart_score_unscored_ids(
    server, character_id, format, candidate_ids=None, descending=True
):
    def fetch_ids(session: Session):
        query = select(Picture.id)

        if candidate_ids is not None:
            if not candidate_ids:
                return []
            query = query.where(Picture.id.in_(candidate_ids))

        if character_id == "UNASSIGNED":
            query = query.where(
                ~exists(
                    select(Face.id).where(
                        Face.picture_id == Picture.id,
                        Face.character_id.is_not(None),
                    )
                ),
                ~exists(
                    select(PictureSetMember.picture_id).where(
                        PictureSetMember.picture_id == Picture.id
                    )
                ),
            )
        elif character_id and character_id != "ALL":
            try:
                cid = int(character_id)
                character_picture_ids = session.exec(
                    select(Face.picture_id).where(Face.character_id == cid)
                ).all()
                if not character_picture_ids:
                    return []
                query = query.where(Picture.id.in_(character_picture_ids))
            except ValueError:
                pass

        if format:
            query = query.where(Picture.format.in_(format))

        query = query.where(Picture.image_embedding.is_(None))

        if descending:
            query = query.order_by(desc(Picture.created_at), desc(Picture.id))
        else:
            query = query.order_by(Picture.created_at, Picture.id)

        return [row for row in session.exec(query).all()]

    return server.vault.db.run_task(fetch_ids, priority=DBPriority.IMMEDIATE)


def prepare_smart_score_inputs(good_anchors, bad_anchors, candidates, pic_likeness_map):
    """Unpickle embeddings and prepare lists of dictionaries for calculation."""

    def get_attr(item, key):
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    def get_vec(blob):
        try:
            obj = pickle.loads(blob)
            if isinstance(obj, np.ndarray):
                return obj
            return np.array(obj)
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
                    "character_likeness": pic_likeness_map.get(pid),
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
    character_id,
    format,
    offset,
    limit,
    descending,
    candidate_ids=None,
    penalized_tags=None,
):
    # 1. Fetch data
    good_anchors, bad_anchors, candidates, pic_likeness_map = fetch_smart_score_data(
        server,
        character_id,
        format,
        candidate_ids=candidate_ids,
        penalized_tags=penalized_tags,
    )

    unscored_ids = fetch_smart_score_unscored_ids(
        server,
        character_id,
        format,
        candidate_ids=candidate_ids,
        descending=descending,
    )

    score_map = {}
    scored_ids = []

    if candidates:
        # 2. Prepare inputs (unpickling)
        good_list, bad_list, cand_list, cand_ids = prepare_smart_score_inputs(
            good_anchors, bad_anchors, candidates, pic_likeness_map
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

    final_ids = combined_ids[offset : offset + limit]

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
