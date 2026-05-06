"""Finder for pictures missing a text_score value."""

from sqlmodel import Session

from pixlstash.worker_config import TEXT_SCORE_MAX_INFLIGHT
from .base_task_finder import BaseTaskFinder
from .text_score_task import TextScoreTask


class MissingTextScoreFinder(BaseTaskFinder):
    """Find quality rows missing text_score and create TextScoreTasks.

    Runs at low priority after QualityTask has populated the main metrics.
    Two tasks may be in-flight so that one can load images while the other
    runs the MSER computation.
    """

    _FETCH_LIMIT = TextScoreTask.BATCH_SIZE * 4
    _CLAIM_LIMIT = TextScoreTask.BATCH_SIZE

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingTextScoreFinder"

    def max_inflight_tasks(self) -> int:
        return TEXT_SCORE_MAX_INFLIGHT

    def find_task(self):
        pictures = self._db.run_immediate_read_task(
            TextScoreTask.find_pictures_missing_text_score,
            self._FETCH_LIMIT,
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, self._CLAIM_LIMIT)
        if not selected:
            return None

        return TextScoreTask(database=self._db, pictures=selected)

    @staticmethod
    def _count_missing_text_score(session: Session) -> int:
        return TextScoreTask.count_missing_text_score(session)
