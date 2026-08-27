"""Finder for pictures owing a layout check (v1.11 Phase 4b)."""

import time

from pixlstash.tasks.base_task_finder import SimpleMissingFinder
from pixlstash.tasks.layout_move_task import LayoutMoveTask


class LayoutMoveFinder(SimpleMissingFinder):
    """Queue :class:`LayoutMoveTask` for pictures whose check has come due.

    The candidate query is one indexed predicate against a column that is NULL
    for every picture nobody has just reassigned, so a library where nothing is
    happening pays a single empty read per cycle.

    One task in flight. The unit of work is renaming files on the owner's disk,
    and two passes racing each other over the same tree is not worth the
    parallelism - a batch of 200 file renames is fast, and the debounce means
    the work arrives in clumps rather than continuously.
    """

    def __init__(self, database, notifier=None) -> None:
        super().__init__(database)
        self._notifier = notifier

    def finder_name(self) -> str:
        return "LayoutMoveFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def _batch_size(self) -> int:
        return LayoutMoveTask.BATCH_SIZE

    def _fetch_candidates(self, session, limit: int) -> list:
        return LayoutMoveTask.find_due_pictures(session, limit, time.time())

    def _create_task(self, pictures: list):
        return LayoutMoveTask(
            database=self._db, pictures=pictures, notifier=self._notifier
        )
