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

    def depends_on(self) -> list[str]:
        """Return names of finders whose inflight tasks must reach zero before this finder runs.

        When any listed finder has in-flight tasks the WorkPlanner skips this
        finder for that planning cycle, so heavyweight upstream work is never
        interleaved with this finder's tasks.
        """
        return []

    def on_all_tasks_complete(self) -> None:
        """Called once when the finder is exhausted and all its in-flight tasks finish.

        Override to release GPU resources (e.g. ONNX session CUDA arenas) that
        are no longer needed until the next work sweep.
        """

    def on_task_complete(self, task, error) -> None:
        """Release any picture IDs that were claimed by *task*."""
        picture_ids = (getattr(task, "params", None) or {}).get("picture_ids") or []
        if not picture_ids:
            return
        with self._claim_lock:
            for picture_id in picture_ids:
                self._claimed_picture_ids.discard(picture_id)


class SimpleMissingFinder(BaseTaskFinder, ABC):
    """Base for finders that follow the fetch-claim-create pattern.

    Subclasses implement three small methods and get a correct ``find_task``
    for free.  The fetch multiplier is ``max_inflight_tasks() + 1`` so that
    ``_filter_and_claim`` can always fill one full task even when all in-flight
    slots are already claimed.

    Subclasses must implement (in addition to ``finder_name``):

    - ``_batch_size() -> int`` — number of pictures per task.
    - ``_fetch_candidates(session, limit: int) -> list`` — DB query; called as
      a bound method so it receives ``session`` as the first argument when
      invoked through ``run_immediate_read_task``.
    - ``_create_task(pictures: list)`` — construct and return the task.
    """

    def __init__(self, database):
        super().__init__()
        self._db = database

    @abstractmethod
    def _batch_size(self) -> int: ...

    @abstractmethod
    def _fetch_candidates(self, session, limit: int) -> list: ...

    @abstractmethod
    def _create_task(self, pictures: list): ...

    def _guard(self) -> bool:
        """Return False to skip this planning cycle. Override to gate on external state."""
        return True

    def find_task(self):
        if not self._guard():
            return None
        batch = self._batch_size()
        limit = batch * (max(1, self.max_inflight_tasks()) + 1)
        pictures = self._db.run_immediate_read_task(self._fetch_candidates, limit)
        if not pictures:
            return None
        selected = self._filter_and_claim(pictures, batch)
        if not selected:
            return None
        return self._create_task(selected)
