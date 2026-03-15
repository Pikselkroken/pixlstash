from sqlmodel import Session

from .base_task_finder import BaseTaskFinder
from .quality_task import QualityTask


class MissingQualityFinder(BaseTaskFinder):
    """Find missing full-image quality work and create a QualityTask.

    Allows two tasks to be in-flight simultaneously so that one task can
    preload images from disk while the other is running compute (ping-pong).
    """

    # Fetch 2× the per-task limit so that when two tasks are in-flight the
    # second call to find_task still finds enough unclaimed pictures.
    _FETCH_LIMIT = QualityTask.BATCH_SIZE * 8
    _CLAIM_LIMIT = QualityTask.BATCH_SIZE * 8

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingQualityFinder"

    def max_inflight_tasks(self) -> int:
        return 2

    def find_task(self):
        pictures = self._db.run_immediate_read_task(
            QualityTask._find_pictures_missing_quality,
            self._FETCH_LIMIT * 2,
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, self._CLAIM_LIMIT)
        if not selected:
            return None

        return QualityTask(database=self._db, pictures=selected)

    @staticmethod
    def _count_missing_quality(session: Session) -> int:
        return QualityTask.count_missing_quality(session)
