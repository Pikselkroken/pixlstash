"""Errors that routes and services catch from the inference engine.

Imports nothing from pixlstash. Search, export and the vault all import these,
and ``pixlstash.utils.service`` imports ``export_utils`` on its way to modules
the engine needs, so a module here that imported back into pixlstash could
close an import cycle.
"""

#: The 503 detail for a search, a likeness search or an export by query when no
#: GPU worker is running to load the models it needs, so waiting will not help.
NO_GPU_WORKER_DETAIL = (
    "Search cannot load its models: the GPU worker is not running. If this "
    "persists, restart PixlStash."
)


class CpuQueryEncodersNotReadyError(RuntimeError):
    """The CPU query encoders are not loaded, so a search cannot encode its query.

    Their load did not finish in time, failed, was cancelled, or could not be
    queued or run because the task runner has no running GPU worker. The next
    search queues the load again (``Vault.query_encoders``).

    Args:
        message: What went wrong.
        worker_running: ``False`` when there was no running GPU worker to load
            them on.

    Attributes:
        worker_running: Whether a GPU worker was running. When none was, a
            retry fails the same way until the runner runs again.
    """

    def __init__(self, message: str, *, worker_running: bool = True) -> None:
        super().__init__(message)
        self.worker_running = worker_running
