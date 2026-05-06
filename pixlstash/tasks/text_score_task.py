"""Task that computes text_score for pictures."""

import time

from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.quality import Quality
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class TextScoreTask(BaseTask):
    """Task that fills in text_score on Picture rows.

    Runs at low priority and is fully independent of QualityTask — it reads
    picture.text_score IS NULL and writes directly to the picture table.
    """

    BATCH_SIZE = 64
    SCAN_LIMIT = 512

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def __init__(self, database, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="TextScoreTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._pictures = pictures or []

    def _run_task(self):
        start = time.time()
        updates: list[tuple[int, float]] = []

        for pic in self._pictures:
            file_path = ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
            img = ImageUtils.load_image_or_video(str(file_path))
            if img is None:
                score = -1.0
            else:
                try:
                    score = float(Quality._calculate_text_score(img))
                except Exception as exc:
                    logger.warning(
                        "text_score calculation failed for picture_id=%s: %s",
                        pic.id,
                        exc,
                    )
                    score = -1.0
            updates.append((int(pic.id), score))

        if not updates:
            return {"changed_count": 0, "changed": []}

        changed = self._db.run_task(
            TextScoreTask._persist_text_scores,
            updates,
            priority=DBPriority.LOW,
        )

        logger.debug(
            "TextScoreTask completed in %.2fs with %s updates",
            time.time() - start,
            len(changed or []),
        )
        return {"changed_count": len(changed or []), "changed": changed or []}

    @staticmethod
    def _persist_text_scores(
        session: Session, updates: list[tuple[int, float]]
    ) -> list:
        """Write text_score values directly to Picture rows in a single transaction."""
        changed = []
        for picture_id, score in updates:
            pic = session.get(Picture, picture_id)
            if pic is None:
                continue
            pic.text_score = score
            session.add(pic)
            changed.append((Picture, picture_id, "text_score", score))
        session.commit()
        return changed

    @staticmethod
    def find_pictures_missing_text_score(session: Session, limit: int) -> list:
        """Return non-deleted pictures whose text_score is NULL."""
        return session.exec(
            select(Picture)
            .where(Picture.text_score.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
            .limit(limit)
        ).all()

    @staticmethod
    def count_missing_text_score(session: Session) -> int:
        """Return the number of pictures still missing text_score."""
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.text_score.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0
