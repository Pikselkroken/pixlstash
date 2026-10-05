import threading
import time
from abc import ABC, ABCMeta, abstractmethod
from typing import TYPE_CHECKING, Callable, Optional

from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.tasks.task_type import TaskType

logger = get_logger(__name__)


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
        # Monotonic timestamp of the last check `_due` let through.
        self._last_check_at: Optional[float] = None

    def _due(self, interval_s: float) -> bool:
        """Whether *interval_s* has passed since the last check it let through.

        The first call is always due. The sentinel is ``None``, NOT 0.0:
        ``time.monotonic()``'s reference point is undefined (on Linux it is
        seconds since BOOT), so 0.0 is an absolute instant, and on a host that
        booted less than the interval ago ``now - 0.0`` reads as "checked
        moments ago" and silently suppresses the first run - which is how it
        surfaced, on a CI runner with under 15 minutes of uptime.
        """
        now = time.monotonic()
        if self._last_check_at is not None and now - self._last_check_at < interval_s:
            return False
        self._last_check_at = now
        return True

    def _filter_and_claim(self, pictures, batch_limit: int) -> list:
        """Return up to *batch_limit* pictures whose IDs are not yet claimed.

        Atomically marks the returned IDs as claimed.  The caller is
        responsible for releasing them (via ``on_task_complete``) once the
        task finishes.

        Args:
            pictures: Candidate picture objects (must expose an ``id`` attr).
            batch_limit: Maximum number of pictures to include in one task.

        Pictures whose image file cannot be decoded (issue #585) are excluded
        here, at the one point every batch finder claims through, so a corrupt
        image is skipped uniformly instead of being re-selected on every sweep.
        Suppression is looked up by picture id against the registry's own stored
        path (never ``picture.file_path``, which is often deferred and the
        candidate pictures are detached here); a rewritten file lifts it
        automatically (see :class:`UnprocessableImageRegistry`).

        Returns:
            A list of pictures selected from *pictures* that were unclaimed.
        """
        database = getattr(self, "_db", None)
        registry = getattr(database, "unprocessable_images", None)
        selected = []
        with self._claim_lock:
            for picture in pictures:
                picture_id = getattr(picture, "id", None)
                if picture_id is None or picture_id in self._claimed_picture_ids:
                    continue
                if registry is not None and registry.is_suppressed(picture_id):
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

    def depends_on(self) -> "list[TaskType]":
        """Return TaskType values of finders whose in-flight tasks must reach zero before this finder runs.

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

    - ``_batch_size() -> int`` - number of pictures per task.
    - ``_fetch_candidates(session, limit: int) -> list`` - DB query; called as
      a bound method so it receives ``session`` as the first argument when
      invoked through ``run_immediate_read_task``.
    - ``_create_task(pictures: list)`` - construct and return the task.
    """

    def __init__(self, database):
        super().__init__()
        self._db = database

    @abstractmethod
    def _batch_size(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def _fetch_candidates(self, session, limit: int) -> list:
        raise NotImplementedError

    @abstractmethod
    def _create_task(self, pictures: list):
        raise NotImplementedError

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


class BaseDeferringFinder(BaseTaskFinder):
    """A finder handing out batches of ids, deferring the ones that fail.

    ``_handed_out`` holds a batch from the moment its task is built until its
    result arrives. One task at a time is not enough on its own:
    ``WorkPlanner.on_task_complete`` frees the inflight slot under its lock and
    only then calls the finder's callback, so the planner can run ``find_task``
    in between and re-issue the identical batch.

    ``_deferred`` holds what failed, or what the task reported as
    ``deferred``, for the life of the process. The planner sweeps
    continuously, so without it one row that cannot succeed would make the
    finder return a task on every cycle forever; a restart retries it.

    Subclasses set ``_IDS_PARAM`` (the task param holding the batch),
    ``_TRANSIENT`` (errors that teach nothing about the rows, such as
    ``TaskCancelledError``: the batch stays eligible) and ``_WHAT`` (the work,
    for the log).
    """

    _IDS_PARAM: str
    _TRANSIENT: tuple
    _WHAT: str

    def __init__(self):
        super().__init__()
        self._deferred: set = set()
        self._handed_out: set = set()

    def _take(self, fetch: Callable[[int], list], size: int, key=lambda item: item):
        """Up to *size* of ``fetch(limit)`` neither deferred nor out, now out.

        *limit* is *size* plus every id skipped, so a page full of skipped rows
        still yields a batch; *key* is an item's id.
        """
        skip = self._deferred | self._handed_out
        batch = [item for item in fetch(size + len(skip)) if key(item) not in skip]
        batch = batch[:size]
        self._handed_out.update(key(item) for item in batch)
        return batch

    def on_task_complete(self, task, error) -> None:
        """Release the batch, and defer what must not be handed out again."""
        ids = (getattr(task, "params", None) or {}).get(self._IDS_PARAM) or []
        self._handed_out.difference_update(ids)
        if isinstance(error, self._TRANSIENT):
            logger.info(
                "%s did not run for %d item(s): %s. They stay eligible.",
                self._WHAT,
                len(ids),
                error,
            )
            return
        if error is not None:
            logger.warning(
                "%s failed for %s: %s. Deferring them for the rest of this session.",
                self._WHAT,
                ids,
                error,
            )
            self._deferred.update(ids)
            return
        self._deferred.update((getattr(task, "result", None) or {}).get("deferred", []))
