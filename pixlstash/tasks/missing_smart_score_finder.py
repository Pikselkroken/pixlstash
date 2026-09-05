from pixlstash.worker_config import SMART_SCORE_MAX_INFLIGHT
from .base_task_finder import SimpleMissingFinder
from .smart_score_task import SmartScoreTask


class MissingSmartScoreFinder(SimpleMissingFinder):
    """Find pictures missing a stored smart score and create a SmartScoreTask.

    Needs a full ``Vault`` (not just ``database``) because :class:`SmartScoreTask`
    resolves the tagger's per-label acceptance thresholds through it, so it is
    registered in ``vault.py`` rather than ``WorkPlanner.work_finders()`` - the same
    reason ``GFS_SNAPSHOT`` and ``TAG_HEALTH_AUTO_REBUILD`` are.

    Args:
        vault: The vault owning the database and the tagger configuration.
    """

    def __init__(self, vault):
        super().__init__(database=vault.db)
        self._vault = vault

    def finder_name(self) -> str:
        return "MissingSmartScoreFinder"

    def max_inflight_tasks(self) -> int:
        return SMART_SCORE_MAX_INFLIGHT

    def _batch_size(self) -> int:
        return SmartScoreTask.BATCH_SIZE

    def _fetch_candidates(self, session, limit: int) -> list:
        return SmartScoreTask.find_pictures_missing_smart_score(session, limit)

    def _create_task(self, pictures: list):
        return SmartScoreTask(vault=self._vault, pictures=pictures)
