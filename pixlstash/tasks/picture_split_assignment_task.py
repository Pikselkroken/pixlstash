"""Periodic maintenance: assign TRAIN/EVAL splits to pictures that have none.

Dispatched by :class:`~pixlstash.tasks.picture_split_assignment_finder.
PictureSplitAssignmentFinder` — see that module and
:mod:`pixlstash.services.picture_split_service` for the component-aware
assignment algorithm this delegates to.

This is the automatic replacement for the previously-uncalled ``POST
/picture_splits/assign`` (Spec A,
``docs/reviews/tag-review-board-redesign-ux-spec.md`` §3): without something
calling ``assign_splits`` on a schedule, freeze-eligibility
(``eval_candidate_n_pos``) could never move no matter how much a tag was
reviewed, because nothing ever wrote a picture's first ``PictureSplit`` row in
the first place.
"""

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

logger = get_logger(__name__)


class PictureSplitAssignmentTask(BaseTask):
    """Run ``assign_splits`` for every picture currently lacking a split.

    Pure SQL + in-memory union-find over already-computed ``PictureLikeness``
    rows (no models, no embeddings computed here) — CPU queue. LOW priority:
    background maintenance that should never preempt interactive or
    live-import work.

    Attributes:
        _vault: The owning Vault, used to reach the DB worker.
    """

    def __init__(self, vault) -> None:
        """Initialise the task.

        Args:
            vault: The owning Vault instance.
        """
        super().__init__(task_type="PictureSplitAssignmentTask", params={})
        self._vault = vault

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def queue_type(self) -> QueueType:
        return QueueType.CPU

    def _run_task(self) -> dict:
        # Late import: picture_split_service pulls in tag_health_service /
        # tag_scan_service at module load, avoid that cost for every task
        # module import at startup.
        from pixlstash.services import picture_split_service

        result = picture_split_service.assign_splits(self._vault)
        logger.info(
            "PictureSplitAssignmentTask: assigned=%s conflicted=%s",
            result.get("assigned"),
            result.get("conflicted"),
        )
        return result
