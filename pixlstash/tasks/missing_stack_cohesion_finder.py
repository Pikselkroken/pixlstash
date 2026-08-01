"""Finder that keeps the mixed-stack cohesion cache current.

Queues one :class:`~pixlstash.tasks.stack_cohesion_task.StackCohesionTask` per
batch of stacks whose cached cohesion row is missing or stale, stale meaning
the stack's live membership fingerprint no longer matches the cached one, which
is the only staleness that exists here (a row whose fingerprint still matches is
exact however old it is, because its inputs have not moved).

Not a :class:`~pixlstash.tasks.base_task_finder.SimpleMissingFinder`: the unit
of work is a **stack**, not a picture, so there are no picture ids to claim.
"""

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.stack_cohesion_task import StackCohesionTask
from pixlstash.tasks.task_type import TaskType

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
        # Cohesion is measured on ``picture.perceptual_hash``, which the image
        # embedding worker writes. Running ahead of it would cache an edge list
        # derived from members that simply have no hash yet, and every one of
        # them would look stranded: the exact false positive the flag must not
        # produce. Waiting means the first answer is the honest one.
        return [TaskType.IMAGE_EMBEDDING]

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
