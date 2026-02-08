from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import desc, exists, or_
from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.db_models import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    Picture,
    Quality,
    Tag,
    TAG_EMPTY_SENTINEL,
    User,
)
from pixlvault.event_types import EventType
from pixlvault.picture_scoring import prepare_smart_score_inputs
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger
from pixlvault.utils import normalize_smart_score_penalized_tags
from pixlvault.worker_registry import BaseWorker, WorkerType

logger = get_logger(__name__)

DEFAULT_SMART_SCORE_SCRAPHEAP_THRESHOLD = 1.25
DEFAULT_SMART_SCORE_SCRAPHEAP_LOOKBACK_MINUTES = 30


class SmartScoreScrapheapWorker(BaseWorker):
    """
    Automatically move newly imported, tagged pictures into the scrapheap
    when their smart score is below a configured threshold.
    """

    INTERVAL = 30

    def worker_type(self) -> WorkerType:
        return WorkerType.SMART_SCORE_SCRAPHEAP

    def _run(self):
        logger.info("SmartScoreScrapheapWorker: Started.")
        while not self._stop.is_set():
            try:
                start = datetime.utcnow()
                moved = self._scrapheap_low_scoring_recent()
                elapsed = (datetime.utcnow() - start).total_seconds()
                if moved:
                    logger.debug(
                        "SmartScoreScrapheapWorker: Moved %d picture(s) after %.2fs.",
                        moved,
                        elapsed,
                    )
                else:
                    logger.debug(
                        "SmartScoreScrapheapWorker: Sleeping after %.2fs. No candidates.",
                        elapsed,
                    )
                    self._wait()
            except Exception as exc:
                import traceback

                logger.error(
                    "SmartScoreScrapheapWorker exiting due to error: %s\n%s",
                    exc,
                    traceback.format_exc(),
                )
                break
        logger.info("SmartScoreScrapheapWorker: Stopped.")

    def _get_config(self):
        def fetch_config(session: Session):
            user = session.exec(select(User).order_by(User.id)).first()
            threshold = (
                user.auto_scrapheap_smart_score_threshold
                if user and user.auto_scrapheap_smart_score_threshold is not None
                else DEFAULT_SMART_SCORE_SCRAPHEAP_THRESHOLD
            )
            lookback = (
                int(user.auto_scrapheap_lookback_minutes)
                if user and user.auto_scrapheap_lookback_minutes is not None
                else DEFAULT_SMART_SCORE_SCRAPHEAP_LOOKBACK_MINUTES
            )
            penalized_tags = normalize_smart_score_penalized_tags(
                getattr(user, "smart_score_penalized_tags", None) if user else None,
                DEFAULT_SMART_SCORE_PENALIZED_TAGS,
                default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
            )
            return threshold, lookback, penalized_tags

        return self._db.run_task(fetch_config, priority=DBPriority.IMMEDIATE)

    def _fetch_anchors(self, session: Session):
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

        return good, bad

    def _fetch_candidates(
        self,
        session: Session,
        cutoff: datetime,
        penalized_tag_weights: dict | None,
    ):
        tag_exists = exists(
            select(Tag.id).where(
                Tag.picture_id == Picture.id,
                Tag.tag.is_not(None),
                Tag.tag != TAG_EMPTY_SENTINEL,
            )
        )
        query = (
            select(Picture, Quality)
            .outerjoin(Quality, Quality.picture_id == Picture.id)
            .where(Picture.deleted.is_(False))
            .where(Picture.imported_at.is_(None))
            .where(Picture.image_embedding.is_not(None))
            .where(tag_exists)
            .where(
                or_(Picture.aesthetic_score.is_not(None), Quality.id.is_not(None))
            )
        )

        candidate_rows = session.exec(query).all()

        candidates = []
        candidate_ids = []
        for pic, quality in candidate_rows:
            aest = pic.aesthetic_score
            quality_score = None
            if quality is not None:
                try:
                    quality_score = quality.calculate_quality_score()
                except Exception as exc:
                    logger.warning(
                        "SmartScoreScrapheapWorker: Failed quality score for picture %s: %s",
                        pic.id,
                        exc,
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
            candidate_ids.append(pic.id)

        penalized_tag_map = {}
        if penalized_tag_weights and candidate_ids:
            tag_rows = session.exec(
                select(Tag.picture_id, Tag.tag).where(
                    Tag.picture_id.in_(candidate_ids),
                )
            ).all()
            for pic_id, tag in tag_rows:
                if not tag:
                    continue
                key = str(tag).strip().lower()
                weight = penalized_tag_weights.get(key)
                if weight is not None:
                    penalized_tag_map[pic_id] = penalized_tag_map.get(pic_id, 0) + weight

            if penalized_tag_map:
                for candidate in candidates:
                    candidate["penalized_tag_count"] = penalized_tag_map.get(
                        candidate["id"], 0
                    )

        return candidates

    def _scrapheap_low_scoring_recent(self) -> int:
        threshold, lookback_minutes, penalized_tags = self._get_config()
        if threshold is None:
            return 0
        try:
            threshold_value = float(threshold)
        except (TypeError, ValueError):
            threshold_value = DEFAULT_SMART_SCORE_SCRAPHEAP_THRESHOLD
        if threshold_value <= 0:
            return 0

        try:
            lookback_value = int(lookback_minutes)
        except (TypeError, ValueError):
            lookback_value = DEFAULT_SMART_SCORE_SCRAPHEAP_LOOKBACK_MINUTES
        lookback_value = max(1, lookback_value)

        cutoff = datetime.utcnow() - timedelta(minutes=lookback_value)

        def fetch_data(session: Session):
            good, bad = self._fetch_anchors(session)
            candidates = self._fetch_candidates(session, cutoff, penalized_tags)
            return good, bad, candidates

        good, bad, candidates = self._db.run_task(
            fetch_data, priority=DBPriority.IMMEDIATE
        )

        if not candidates:
            return 0

        good_list, bad_list, cand_list, cand_ids = prepare_smart_score_inputs(
            good, bad, candidates
        )
        if not cand_list:
            return 0

        scores = PictureUtils.calculate_smart_score_batch_numpy(
            cand_list, good_list, bad_list
        )

        low_ids = [
            cand_ids[idx]
            for idx, score in enumerate(scores)
            if score is not None and float(score) < threshold_value
        ]

        now = datetime.utcnow()

        def mark_processed(session: Session, candidate_ids: list[int], low_ids: list[int]):
            if not candidate_ids:
                return 0, 0
            low_set = set(low_ids)
            pics = session.exec(
                select(Picture).where(Picture.id.in_(candidate_ids))
            ).all()
            for pic in pics:
                if pic.imported_at is None:
                    pic.imported_at = now
                if pic.id in low_set:
                    pic.deleted = True
                session.add(pic)
            session.commit()
            return len(low_set), len(pics)

        deleted_count, processed_count = self._db.run_task(
            mark_processed, cand_ids, low_ids, priority=DBPriority.IMMEDIATE
        )

        if processed_count:
            logger.info(
                "SmartScoreScrapheapWorker: Processed %d picture(s) (threshold=%.3f, window=%dm).",
                processed_count,
                threshold_value,
                lookback_value,
            )
            self._notify_others(EventType.CHANGED_PICTURES, cand_ids)
        if deleted_count:
            logger.info(
                "SmartScoreScrapheapWorker: Moved %d picture(s) to scrapheap (threshold=%.3f, window=%dm).",
                deleted_count,
                threshold_value,
                lookback_value,
            )
            self._notify_others(EventType.CHANGED_PICTURES, low_ids)

        return deleted_count
