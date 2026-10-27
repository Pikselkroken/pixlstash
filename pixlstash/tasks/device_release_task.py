"""Task that releases models and flushes the device cache on the GPU worker.

Unloading ends in ``empty_device_cache()``, which on Metal is
``torch.mps.empty_cache()``: run on any other thread while the worker runs a
model, it frees graphs the worker is using and kills the process (cause 3 in
``docs/apple-metal-thread-safety.md``). The idle sweep, making room for another
tenant and the face finder's end-of-work release used to run it on a request
thread, the event loop, the arbiter's thread or the planner's. Queued here, it
runs on the single GPU worker, between tasks, like everything else that touches
the device.

``URGENT``, so the arbiter admits it while another tenant waits for the card
(that is when making room needs it) and making room never cancels it. It holds
no memory. Being ``URGENT``, it also runs ahead of background batches queued
but not yet started; only the task already running is waited for.
"""

from __future__ import annotations

from typing import Callable, Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

logger = get_logger(__name__)


class DeviceReleaseTask(BaseTask):
    """Run *release* on the GPU worker, if *when* still holds once it is reached.

    Args:
        label: Task type, for the log and the arbiter's snapshot.
        release: What to run: ``Vault.unload_models``,
            ``FaceExtractionTask.release_detection_models``.
        when: Checked on the worker just before *release*; ``False`` skips it.
            For a release whose reason can lapse while it waits in the queue.
    """

    def __init__(
        self,
        label: str,
        release: Callable[[], None],
        when: Optional[Callable[[], bool]] = None,
    ):
        super().__init__(task_type=label)
        self._release = release
        self._when = when

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.URGENT

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def wait(self, timeout_s: float) -> bool:
        """Block until the task has run, been skipped or been cancelled; True if so."""
        return self._done_event.wait(timeout=timeout_s)

    def _run_task(self) -> bool:
        if self._when is not None and not self._when():
            logger.debug("%s skipped: no longer needed", self.type)
            return False
        self._release()
        return True
