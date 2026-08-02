"""Finder that keeps the mixed-stack cohesion cache current.

Queues one :class:`~pixlstash.tasks.stack_cohesion_task.StackCohesionTask` per
batch of stacks whose cached cohesion row is missing. Database triggers remove
the row in the same transaction whenever live membership or a perceptual hash
moves, so a present row is exact and absence is the only stale state.

Not a :class:`~pixlstash.tasks.base_task_finder.SimpleMissingFinder`: the unit
of work is a **stack**, not a picture, so there are no picture ids to claim.
"""

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.stack_cohesion_task import StackCohesionTask

logger = get_logger(__name__)


class MissingStackCohesionFinder(BaseTaskFinder):
    """Queue cohesion recomputation for stacks whose membership has moved."""

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingStackCohesionFinder"

    def max_inflight_tasks(self) -> int:
        # One at a time. Two concurrent tasks would fight over the same
        # ``stackcohesion`` rows for no gain: the work is a short DB write, not
        # a GPU pipeline, and the batch query is already amortised over 200
        # stacks.
        return 1

    def depends_on(self) -> list:
        # An unhashed member is truthfully cached as not comparable, and the
        # invalidation trigger drops the row as soon as its hash arrives. Waiting
        # for the whole embedding queue left the cache empty during imports.
        return []

    def find_task(self):
        stack_ids = self._db.run_immediate_read_task(
            StackCohesionTask.find_stale_stacks, StackCohesionTask.BATCH_SIZE
        )
        if not stack_ids:
            return None
        logger.debug(
            "[mixed-stacks] queueing cohesion refresh for %d stack(s)", len(stack_ids)
        )
        return StackCohesionTask(database=self._db, stack_ids=list(stack_ids))
