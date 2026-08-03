"""Finder that picks up requested duplicate scans.

A scoped scan is *requested* by writing a
:class:`~pixlstash.db_models.dedup.DedupScan` row with status ``pending`` (the
``POST /dedup/scan`` route does exactly that and returns immediately, so the
context-menu entry point never blocks). This finder turns that row into a
:class:`~pixlstash.tasks.dedup_scan_task.DedupScanTask`.

It is not a :class:`SimpleMissingFinder`: the unit of work is a scan row, not a
batch of pictures, so there is nothing to claim.
"""

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.dedup_scan_task import DedupScanTask
from pixlstash.tasks.task_type import TaskType

logger = get_logger(__name__)


class DedupScanFinder(BaseTaskFinder):
    """Create one :class:`DedupScanTask` per pending scan request."""

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "DedupScanFinder"

    def max_inflight_tasks(self) -> int:
        # One scan at a time. Two concurrent scans would fight over the same
        # upserted group rows for no gain: the work is IO-bound on one table.
        return 1

    def depends_on(self) -> list:
        # Every tier needs the sampled content hash, but an exact-only request
        # must not sit behind an unrelated embedding backlog. Only the near and
        # embedding policies consume the perceptual hash produced by that
        # worker. With no pending request we keep the conservative exact
        # dependency; ``find_task`` will immediately report no work anyway.
        scan = self._db.run_immediate_read_task(DedupScanTask.find_pending_scan)
        if scan is None:
            return [TaskType.PIXEL_SHA]
        policy = DedupScanTask._policy_for(scan)
        dependencies = [TaskType.PIXEL_SHA]
        if policy.near_enabled or policy.embedding_enabled:
            dependencies.append(TaskType.IMAGE_EMBEDDING)
        return dependencies

    def find_task(self):
        scan = self._db.run_immediate_read_task(DedupScanTask.find_pending_scan)
        if scan is None:
            return None
        logger.info(
            "[dedup-scan] queueing scan %s for scope %s", scan.id, scan.scope_key
        )
        return DedupScanTask(database=self._db, scan_id=int(scan.id))
