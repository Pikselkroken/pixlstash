from sqlmodel import Session

from .base_task_finder import BaseTaskFinder
from .smart_score_task import SmartScoreTask


class MissingSmartScoreFinder(BaseTaskFinder):
    """Find pictures missing a stored smart score and create a SmartScoreTask."""

    _FETCH_LIMIT = SmartScoreTask.BATCH_SIZE * 4

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingSmartScoreFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        pictures = self._db.run_immediate_read_task(
            SmartScoreTask.find_pictures_missing_smart_score,
            self._FETCH_LIMIT,
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, SmartScoreTask.BATCH_SIZE)
        if not selected:
            return None

        return SmartScoreTask(
            database=self._db,
            pictures=selected,
        )

    @staticmethod
    def _count_remaining(session: Session) -> int:
        return SmartScoreTask.count_remaining(session)
