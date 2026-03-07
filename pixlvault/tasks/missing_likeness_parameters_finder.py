from sqlmodel import Session

from pixlvault.utils.likeness.likeness_parameter_utils import (
    LikenessParameterUtils,
)

from .base_task_finder import BaseTaskFinder
from .likeness_parameters_task import LikenessParametersTask


class MissingLikenessParametersFinder(BaseTaskFinder):
    """Find pending likeness-parameter work and create a task."""

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingLikenessParametersFinder"

    def find_task(self):
        missing = int(
            self._db.run_immediate_read_task(self._count_pending_parameters) or 0
        )
        if missing <= 0:
            return None
        return LikenessParametersTask(database=self._db)

    @staticmethod
    def _count_pending_parameters(session: Session) -> int:
        return LikenessParameterUtils.count_pending_parameters(session)
