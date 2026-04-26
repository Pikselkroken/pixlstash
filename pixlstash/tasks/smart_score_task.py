import time
from collections import defaultdict

from sqlalchemy import func, desc
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    Picture,
    Quality,
    Tag,
    User,
)
from pixlstash.picture_scoring import (
    _load_builtin_anchors,
    _BUILTIN_MIN_GOOD,
    _BUILTIN_MIN_BAD,
    prepare_smart_score_inputs,
)
from pixlstash.utils.quality.smart_score_utils import (
    SmartScoreUtils,
    _smart_score_penalised_tags,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask


logger = get_logger(__name__)


class SmartScoreTask(BaseTask):
    """Task that pre-computes and stores smart scores for one batch of pictures."""

    BATCH_SIZE = 64

    def __init__(self, database, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="SmartScoreTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._pictures = pictures or []

    def _run_task(self):
        start = time.time()
        pics = self._pictures
        if not pics:
            return {"changed_count": 0}

        picture_ids = [pic.id for pic in pics if getattr(pic, "id", None)]
        if not picture_ids:
            return {"changed_count": 0}

        # Resolve penalised tags from the first (only) user.
        penalised_tags = self._db.run_immediate_read_task(self._fetch_penalised_tags)
        good_anchors, bad_anchors, candidates = self._db.run_immediate_read_task(
            self._fetch_score_data, picture_ids, penalised_tags
        )

        good_list, bad_list, cand_list, cand_ids = prepare_smart_score_inputs(
            good_anchors, bad_anchors, candidates
        )

        if not cand_list:
            logger.debug("SmartScoreTask: no valid candidates in batch, skipping.")
            return {"changed_count": 0}

        scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
            cand_list, good_list, bad_list
        )

        id_to_score = {cand_ids[i]: float(scores[i]) for i in range(len(cand_ids))}

        changed_count = self._db.run_task(
            self._persist_scores,
            id_to_score,
            priority=DBPriority.LOW,
        )

        logger.debug(
            "SmartScoreTask completed in %.2fs with %s updates",
            time.time() - start,
            changed_count,
        )
        return {"changed_count": changed_count}

    @staticmethod
    def _fetch_penalised_tags(session: Session) -> dict:
        """Fetch the first user's penalised tag weights from the database."""
        user = session.exec(select(User)).first()
        if user is None:
            return DEFAULT_SMART_SCORE_PENALIZED_TAGS or {}
        parsed = _smart_score_penalised_tags(
            user.smart_score_penalised_tags,
            DEFAULT_SMART_SCORE_PENALIZED_TAGS,
            default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
        )
        return (
            parsed if parsed is not None else (DEFAULT_SMART_SCORE_PENALIZED_TAGS or {})
        )

    @staticmethod
    def _fetch_score_data(
        session: Session,
        candidate_ids: list,
        penalised_tags: dict,
    ):
        """Fetch anchors and candidates for smart score computation."""
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

        query = (
            select(Picture, Quality)
            .outerjoin(
                Quality,
                (Quality.picture_id == Picture.id) & (Quality.face_id.is_(None)),
            )
            .where(Picture.id.in_(candidate_ids))
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.deleted.is_(False))
        )
        candidate_rows = session.exec(query).all()

        penalised_tag_weights = {
            str(tag).strip().lower(): int(weight)
            for tag, weight in (penalised_tags or {}).items()
            if str(tag).strip()
        }

        candidates = []
        candidate_id_list = []
        for pic, quality in candidate_rows:
            aest = pic.aesthetic_score
            if aest is None and quality is not None:
                try:
                    aest = quality.calculate_quality_score()
                except Exception as exc:
                    logger.warning(
                        "SmartScoreTask: quality score failed for picture %s: %s",
                        pic.id,
                        exc,
                    )
            candidates.append(
                {
                    "id": pic.id,
                    "image_embedding": pic.image_embedding,
                    "aesthetic_score": aest,
                    "width": pic.width,
                    "height": pic.height,
                    "sharpness": quality.sharpness if quality else None,
                    "edge_density": quality.edge_density if quality else None,
                    "luminance_entropy": quality.luminance_entropy if quality else None,
                    "text_score": quality.text_score if quality else None,
                }
            )
            candidate_id_list.append(pic.id)

        penalised_tag_map = defaultdict(int)
        if penalised_tag_weights and candidate_id_list:
            tag_rows = session.exec(
                select(Tag.picture_id, Tag.tag).where(
                    Tag.picture_id.in_(candidate_id_list)
                )
            ).all()
            for pic_id, tag in tag_rows:
                if not tag:
                    continue
                key = tag.strip().lower()
                weight = penalised_tag_weights.get(key)
                if weight is not None:
                    penalised_tag_map[pic_id] += weight
            if penalised_tag_map:
                for candidate in candidates:
                    candidate["penalised_tag_count"] = penalised_tag_map.get(
                        candidate["id"], 0
                    )

        builtin_good, builtin_bad = _load_builtin_anchors()
        if len(good) < _BUILTIN_MIN_GOOD:
            good = list(good) + builtin_good
        if len(bad) < _BUILTIN_MIN_BAD:
            bad = list(bad) + builtin_bad

        return good, bad, candidates

    @staticmethod
    def _persist_scores(session: Session, id_to_score: dict) -> int:
        changed = 0
        for pic_id, score in id_to_score.items():
            pic = session.get(Picture, pic_id)
            if pic is None:
                continue
            pic.smart_score = score
            session.add(pic)
            changed += 1
        session.commit()
        return changed

    @classmethod
    def count_remaining(cls, session: Session) -> int:
        """Count pictures that have an embedding but no stored smart score."""
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.smart_score.is_(None))
            .where(Picture.deleted.is_(False))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def find_pictures_missing_smart_score(session: Session, limit: int) -> list:
        """Fetch pictures that need smart score computation."""
        return session.exec(
            select(Picture)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.smart_score.is_(None))
            .where(Picture.deleted.is_(False))
            .order_by(Picture.id)
            .limit(limit)
        ).all()
