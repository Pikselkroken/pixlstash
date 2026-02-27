from sqlmodel import Session

from .base_task_finder import BaseTaskFinder
from .face_quality_task import FaceQualityTask


class MissingFaceQualityFinder(BaseTaskFinder):
    """Find missing face-level quality work and create a FaceQualityTask."""

    def __init__(self, database):
        self._db = database

    def finder_name(self) -> str:
        return "MissingFaceQualityFinder"

    def find_task(self):
        missing = int(
            self._db.run_immediate_read_task(self._count_missing_face_quality) or 0
        )
        if missing <= 0:
            return None
        return FaceQualityTask(database=self._db)

    @staticmethod
    def _count_missing_face_quality(session: Session) -> int:
        return FaceQualityTask.count_missing_face_quality(session)
