from sqlmodel import Session

from .base_task_finder import BaseTaskFinder
from .quality_task import QualityTask


class MissingQualityFinder(BaseTaskFinder):
    """Find missing full-image quality work and create a QualityTask."""

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingQualityFinder"

    def find_task(self):
        missing = int(
            self._db.run_immediate_read_task(self._count_missing_quality) or 0
        )
        if missing <= 0:
            return None
        return QualityTask(database=self._db)

    @staticmethod
    def _count_missing_quality(session: Session) -> int:
        return QualityTask.count_missing_quality(session)
