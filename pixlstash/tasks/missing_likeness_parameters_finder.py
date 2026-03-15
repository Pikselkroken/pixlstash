from sqlmodel import Session

from pixlstash.utils.likeness.likeness_parameter_utils import (
    LikenessParameterUtils,
)

from .base_task_finder import BaseTaskFinder
from .likeness_parameters_task import LikenessParametersTask
from .quality_task import QualityTask


class MissingLikenessParametersFinder(BaseTaskFinder):
    """Find pending likeness-parameter work and create a task.

    While quality calculation is running it continuously resets
    ``likeness_parameters`` on processed pictures.  To avoid scheduling
    tiny likeness batches that are immediately invalidated again, we require
    at least MIN_PENDING_WHILE_QUALITY pictures to be waiting before creating
    a task.  Once quality finishes the threshold drops to 1 so no work is
    left lingering.
    """

    # Require at least half a full likeness batch before scheduling while quality
    # is still running.  This avoids churning on 1-2 pictures per quality batch.
    MIN_PENDING_WHILE_QUALITY = LikenessParametersTask.BATCH_SIZE // 2

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingLikenessParametersFinder"

    def find_task(self):
        pending_quality = int(
            self._db.run_immediate_read_task(QualityTask.count_missing_quality) or 0
        )
        missing = int(
            self._db.run_immediate_read_task(self._count_pending_parameters) or 0
        )
        if pending_quality > 0:
            # Quality is still running and constantly resetting likeness_parameters.
            # Only schedule once a full batch has accumulated to avoid churn.
            if missing < self.MIN_PENDING_WHILE_QUALITY:
                return None
        elif missing <= 0:
            return None
        return LikenessParametersTask(database=self._db)

    @staticmethod
    def _count_pending_parameters(session: Session) -> int:
        return LikenessParameterUtils.count_pending_parameters(session)
