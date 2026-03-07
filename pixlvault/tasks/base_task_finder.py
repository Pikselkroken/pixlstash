import threading
from abc import ABC, ABCMeta, abstractmethod


class TaskFinderRegistry(ABCMeta):
    registry = {}

    def __new__(cls, name, bases, namespace):
        cls = super().__new__(cls, name, bases, namespace)
        if not name.startswith("Base"):
            TaskFinderRegistry.registry[name] = cls
        return cls


class BaseTaskFinder(ABC, metaclass=TaskFinderRegistry):
    """Base finder that discovers one type of missing work and returns one task.

    Provides a thread-safe picture-ID claim system so that when multiple tasks
    of the same type are in-flight (see ``max_inflight_tasks``), each task
    operates on a disjoint set of pictures.  Subclasses that work on batches
    of pictures should call ``_filter_and_claim`` before constructing a task
    and must call ``super().__init__()`` in their own ``__init__``.
    """

    def __init__(self):
        self._claim_lock = threading.Lock()
        self._claimed_picture_ids: set[int] = set()

    def _filter_and_claim(self, pictures, batch_limit: int) -> list:
        """Return up to *batch_limit* pictures whose IDs are not yet claimed.

        Atomically marks the returned IDs as claimed.  The caller is
        responsible for releasing them (via ``on_task_complete``) once the
        task finishes.

        Args:
            pictures: Candidate picture objects (must expose an ``id`` attr).
            batch_limit: Maximum number of pictures to include in one task.

        Returns:
            A list of pictures selected from *pictures* that were unclaimed.
        """
        selected = []
        with self._claim_lock:
            for picture in pictures:
                picture_id = getattr(picture, "id", None)
                if picture_id is None or picture_id in self._claimed_picture_ids:
                    continue
                self._claimed_picture_ids.add(picture_id)
                selected.append(picture)
                if len(selected) >= batch_limit:
                    break
        return selected

    @abstractmethod
    def finder_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def find_task(self):
        raise NotImplementedError

    def max_inflight_tasks(self) -> int:
        return 1

    def on_task_complete(self, task, error) -> None:
        """Release any picture IDs that were claimed by *task*."""
        picture_ids = (getattr(task, "params", None) or {}).get("picture_ids") or []
        if not picture_ids:
            return
        with self._claim_lock:
            for picture_id in picture_ids:
                self._claimed_picture_ids.discard(picture_id)
