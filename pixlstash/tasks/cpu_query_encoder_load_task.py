"""Load the CPU query-encoder copies, on the GPU worker.

On the **GPU** queue although it loads onto the CPU, which looks wrong and is
the whole point: the queue is what serialises this load against the worker's
own model loads. transformers and accelerate are not safe to import from two
threads at once - loading a model on a request thread beside a worker load
failed with ``ImportError: cannot import name 'AcceleratorState' from partially
initialized module 'accelerate.state'`` - and the GPU worker is the one thread
every other model load already goes through.

``URGENT`` so it lands ahead of the tagging and embedding batches the planner
queues at start-up: a search that arrives in the first seconds waits for this
task, and everything it would otherwise queue behind takes minutes.
"""

from __future__ import annotations

from typing import Any

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

logger = get_logger(__name__)


class CpuQueryEncoderLoadTask(BaseTask):
    """Load both CPU copies so no request thread ever has to.

    Args:
        encoders: The :class:`~pixlstash.inference.cpu_query_encoders.CpuQueryEncoders`
            to load.
    """

    TASK_TYPE = "cpu_query_encoder_load"

    def __init__(self, encoders) -> None:
        super().__init__(self.TASK_TYPE)
        self._encoders = encoders

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.URGENT

    @property
    def queue_type(self) -> QueueType:
        """The GPU queue - see the module docstring; this is deliberate."""
        return QueueType.GPU

    def _run_task(self) -> Any:
        """Load the copies and report whether both arrived.

        Returns:
            ``True`` when both copies loaded. A partial load returns ``False``
            rather than raising: the pair refuses to serve either way, and a
            failed task would be retried three times for a GPU out-of-memory
            error this cannot have.
        """
        self._encoders.load()
        return self._encoders.is_loaded()
