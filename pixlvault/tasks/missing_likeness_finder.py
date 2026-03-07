from sqlmodel import Session

from .base_task_finder import BaseTaskFinder
from .likeness_task import LikenessTask


class MissingLikenessFinder(BaseTaskFinder):
    """Find pending likeness work and create a LikenessTask."""

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingLikenessFinder"

    def find_task(self):
        queue_count, candidate_count, pair_count = self._db.run_immediate_read_task(
            self._likeness_state
        )
        if int(queue_count or 0) > 0:
            return LikenessTask(database=self._db)
        if int(candidate_count or 0) > 0 and int(pair_count or 0) == 0:
            return LikenessTask(database=self._db)
        return None

    @staticmethod
    def _likeness_state(session: Session):
        return (
            LikenessTask.count_queue(session),
            LikenessTask.count_total_candidates(session),
            LikenessTask.count_total_pairs(session),
        )
