import time

from sqlmodel import Session, select
from sqlalchemy import func

from pixlstash.database import DBPriority
from pixlstash.db_models import Face, Picture, Quality
from pixlstash.utils.quality.quality_utils import QualityUtils
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask


logger = get_logger(__name__)


class FaceQualityTask(BaseTask):
    """Task that calculates face-level quality metrics for one batch."""

    BATCH_SIZE = 32

    def __init__(self, database):
        super().__init__(
            task_type="FaceQualityTask",
            params={
                "batch_size": self.BATCH_SIZE,
            },
        )
        self._db = database

    def _run_task(self):
        start = time.time()
        face_quality_helper = QualityUtils(self._db)

        faces_missing_quality = self._db.run_task(self._find_faces_missing_quality)
        if not faces_missing_quality:
            return {"changed_count": 0, "changed": []}

        grouped_faces, invalid_faces = (
            face_quality_helper.group_faces_by_format_and_size(faces_missing_quality)
        )

        changed = []

        if invalid_faces:
            sentinel_qualities = [
                Quality(
                    sharpness=-1.0,
                    edge_density=-1.0,
                    contrast=-1.0,
                    brightness=-1.0,
                    noise_level=-1.0,
                    color_histogram=None,
                )
                for _ in invalid_faces
            ]
            result = self._db.run_task(
                face_quality_helper.update_face_quality,
                invalid_faces,
                sentinel_qualities,
                priority=DBPriority.LOW,
            )
            changed.extend(result or [])

        for group in grouped_faces.values():
            batch = group[: self.BATCH_SIZE]
            if not batch:
                continue
            qualities = face_quality_helper.calculate_face_quality(batch)
            if qualities:
                faces = [face for _, face in batch]
                result = self._db.run_task(
                    face_quality_helper.update_face_quality,
                    faces,
                    qualities,
                    priority=DBPriority.LOW,
                )
                changed.extend(result or [])

        logger.debug(
            "FaceQualityTask completed in %.2fs with %s updates",
            time.time() - start,
            len(changed),
        )
        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    @staticmethod
    def _find_faces_missing_quality(session: Session):
        statement = (
            select(Face, Picture)
            .join(Picture, Face.picture_id == Picture.id)
            .outerjoin(Quality, Quality.face_id == Face.id)
            .where(Quality.id.is_(None))
            .where(Face.bbox_.is_not(None))
            .order_by(Picture.format, Picture.width, Picture.height)
            .limit(FaceQualityTask.BATCH_SIZE * 8)
        )
        return session.exec(statement).all()

    @staticmethod
    def count_total_faces(session: Session) -> int:
        result = session.exec(
            select(func.count()).select_from(Face).where(Face.bbox_.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def count_missing_face_quality(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Face)
            .outerjoin(Quality, Quality.face_id == Face.id)
            .where(Quality.id.is_(None))
            .where(Face.bbox_.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0
