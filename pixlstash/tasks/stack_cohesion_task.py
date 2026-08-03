"""Keep the mixed-stack cohesion cache current as stacks change.

Cohesion is *computed*, never a column on ``picture``
(``docs/design/mixed-stacks-and-stack-units.md``, B5). What this task maintains
is the cache of the threshold-independent half: the within-stack near-pair edge
list keyed on the stack's membership fingerprint, so the Mixed stacks page
folds components out of stored edges instead of re-reading and re-comparing
every member's perceptual hash on every request, and so the flag is already
right the first time a user opens the page after stacking something.

The work unit is a **batch of stacks**, not a batch of pictures, so this is a
plain :class:`~pixlstash.tasks.base_task.BaseTask` rather than one of the
picture-claiming finders' tasks. Cost is O(sum n^2) over stacks and stacks are
small: a library's worth of stacks is a handful of numpy rows plus two queries.
"""

import time

from sqlmodel import Session

from pixlstash.database import DBPriority
from pixlstash.pixl_logging import get_logger
from pixlstash.services import mixed_stack_service
from pixlstash.tasks.base_task import BaseTask, TaskPriority

logger = get_logger(__name__)


class StackCohesionTask(BaseTask):
    """Recompute the cached cohesion edges of a batch of stacks."""

    BATCH_SIZE = 200
    """Stacks per task. The per-stack work is small and the two queries are
    batched over the whole set, so a larger batch is strictly cheaper per stack;
    this is capped only to keep one task's DB write short enough that it never
    holds the serialised writer for long."""

    @property
    def priority(self) -> TaskPriority:
        # Derived, non-blocking and cheap: nothing waits on it, and it must
        # never compete with import, tagging or embedding work.
        return TaskPriority.LOW

    def __init__(self, database, stack_ids: list):
        ordered = sorted({int(sid) for sid in (stack_ids or [])})
        super().__init__(
            task_type="StackCohesionTask",
            params={"stack_ids": ordered, "batch_size": len(ordered)},
        )
        self._db = database
        self._stack_ids = ordered

    @staticmethod
    def find_stale_stacks(session: Session, limit: int) -> list[int]:
        """Stacks whose cached cohesion row is missing or no longer matches.

        Called through ``run_immediate_read_task`` by the finder, so it takes
        the session as its first argument.
        """
        return mixed_stack_service.stale_cohesion_stack_ids_in_session(session, limit)

    def _run_task(self):
        if not self._stack_ids:
            return {"changed_count": 0, "changed": []}
        start = time.time()
        written = self._db.run_task(
            mixed_stack_service.refresh_cohesion_in_session,
            self._stack_ids,
            priority=DBPriority.LOW,
        )
        logger.debug(
            "StackCohesionTask refreshed %d of %d stack(s) in %.2fs",
            len(written or []),
            len(self._stack_ids),
            time.time() - start,
        )
        return {"changed_count": len(written or []), "changed": list(written or [])}
