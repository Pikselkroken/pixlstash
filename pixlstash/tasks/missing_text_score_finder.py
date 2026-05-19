"""Finder for pictures missing a text_score value."""

from pixlstash.worker_config import TEXT_SCORE_MAX_INFLIGHT
from .base_task_finder import SimpleMissingFinder
from .text_score_task import TextScoreTask


class MissingTextScoreFinder(SimpleMissingFinder):
    """Find quality rows missing text_score and create TextScoreTasks.

    Runs at low priority after QualityTask has populated the main metrics.
    Two tasks may be in-flight so that one can load images while the other
    runs the MSER computation.
    """

    def finder_name(self) -> str:
        return "MissingTextScoreFinder"

    def max_inflight_tasks(self) -> int:
        return TEXT_SCORE_MAX_INFLIGHT

    def _batch_size(self) -> int:
        return TextScoreTask.BATCH_SIZE

    def _fetch_candidates(self, session, limit: int) -> list:
        return TextScoreTask.find_pictures_missing_text_score(session, limit)

    def _create_task(self, pictures: list):
        return TextScoreTask(database=self._db, pictures=pictures)
