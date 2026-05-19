from pixlstash.worker_config import SMART_SCORE_MAX_INFLIGHT
from .base_task_finder import SimpleMissingFinder
from .smart_score_task import SmartScoreTask


class MissingSmartScoreFinder(SimpleMissingFinder):
    """Find pictures missing a stored smart score and create a SmartScoreTask."""

    def finder_name(self) -> str:
        return "MissingSmartScoreFinder"

    def max_inflight_tasks(self) -> int:
        return SMART_SCORE_MAX_INFLIGHT

    def _batch_size(self) -> int:
        return SmartScoreTask.BATCH_SIZE

    def _fetch_candidates(self, session, limit: int) -> list:
        return SmartScoreTask.find_pictures_missing_smart_score(session, limit)

    def _create_task(self, pictures: list):
        return SmartScoreTask(database=self._db, pictures=pictures)
