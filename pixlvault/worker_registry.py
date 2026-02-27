import queue
import random
import threading
import time

from typing import List, Tuple, Type
from concurrent.futures import Future
from abc import ABC, ABCMeta, abstractmethod
from enum import Enum

from .event_types import EventType
from .pixl_logging import get_logger


class WorkerType(str, Enum):
    FACE = "FeatureExtractionWorker"
    TAGGER = "TagWorker"
    QUALITY = "QualityWorker"
    FACE_QUALITY = "FaceQualityWorker"
    LIKENESS = "LikenessWorker"
    LIKENESS_PARAMETERS = "LikenessParameterWorker"
    DESCRIPTION = "DescriptionWorker"
    TEXT_EMBEDDING = "EmbeddingWorker"
    IMAGE_EMBEDDING = "ImageEmbeddingWorker"
    WATCH_FOLDERS = "WatchFolderWorker"

    @staticmethod
    def all():
        return set(item for item in WorkerType)


logger = get_logger(__name__)


class WorkerRegistry(ABCMeta):
    """
    Metaclass for registering worker classes.
    """

    registry = {}

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        if not name.startswith("Base"):
            WorkerRegistry.registry[name] = cls
        return cls

    @classmethod
    def create_worker(cls, worker_name, *args, **kwargs):
        """
        Create an instance of a registered worker by name.
        """
        class_name_by_worker_type = {
            WorkerType.IMAGE_EMBEDDING: "ImageEmbeddingWorker",
        }
        class_name = class_name_by_worker_type.get(worker_name)
        if class_name is None:
            raise ValueError(
                f"Worker type '{worker_name}' is not configured as thread-backed."
            )
        if class_name not in cls.registry:
            raise ValueError(f"Worker class '{class_name}' is not registered.")
        return cls.registry[class_name](*args, **kwargs)


class BaseWorker(ABC, metaclass=WorkerRegistry):
    """
    Class representing different types of picture processing workers.
    """

    INTERVAL = 10  # Default interval between worker runs in seconds
    IDLE_CLEANUP_INTERVAL = 60  # Seconds between idle cleanup attempts

    def __init__(self, database, picture_tagger, event_callback):
        self._db = database
        self._picture_tagger = picture_tagger

        self._stop = threading.Event()
        self._event = threading.Event()
        self._thread = None

        self._watched_ids = {}
        self._watched_ids_lock = threading.Lock()

        self._event_callback = event_callback
        self._queue = queue.Queue()
        self._task_submitter = None

        self._progress_lock = threading.Lock()
        self._progress = {
            "label": "idle",
            "current": 0,
            "total": 0,
            "remaining": 0,
            "updated_at": None,
            "status": "idle",
        }
        self._last_idle_cleanup_at = 0.0

    def set_task_submitter(self, task_submitter):
        """Register a callback used to submit tasks to the shared task runner."""
        self._task_submitter = task_submitter

    def _submit_task(self, task):
        """Submit a task through the configured task submitter.

        If no submitter is configured, the task runs synchronously as a fallback.
        """
        if self._task_submitter is not None:
            return self._task_submitter(task)
        logger.warning(
            "No task submitter configured for %s; running task synchronously.",
            self.name(),
        )
        task.run()
        return getattr(task, "id", None)

    @abstractmethod
    def worker_type(self) -> WorkerType:
        """
        Return the type of the worker.
        """
        pass

    def start(self):
        """
        Start the worker process.
        """
        self._event.clear()
        self._stop.clear()
        self._set_progress(status="running")
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """
        Stop the worker process.
        """
        self._stop.set()
        self._event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning(
                    "Worker %s did not shut down within timeout.",
                    self.name(),
                )
        self._set_progress(status="stopped")

    def close(self):
        """
        Clean up resources held by the worker.
        """
        pass

    def is_alive(self):
        """
        Check if the worker thread is alive.
        """
        return self._thread is not None and self._thread.is_alive()

    def is_stopped(self):
        """
        Check if the worker has been stopped.
        """
        return self._stop.is_set()

    def notify(self, event_type: EventType = None, data=None):
        """
        Notify the worker that it needs to wake.
        """
        logger.debug("Worker {} woken up.".format(self.name()))
        self._queue.put(data)
        self._event.set()

    def name(self):
        """
        Return the name of the worker.
        """
        return self.worker_type().value

    def get_progress(self) -> dict:
        """
        Return a snapshot of the worker progress.
        """
        with self._progress_lock:
            return dict(self._progress)

    def _set_progress(
        self,
        label: str | None = None,
        current: int | None = None,
        total: int | None = None,
        status: str | None = None,
    ):
        with self._progress_lock:
            if status is None and self._progress.get("status") == "idle":
                status = "running"
            if label is not None:
                self._progress["label"] = label
            if current is not None:
                self._progress["current"] = max(0, int(current))
            if total is not None:
                self._progress["total"] = max(0, int(total))
            remaining = self._progress["total"] - self._progress["current"]
            self._progress["remaining"] = max(0, int(remaining))
            if status is not None:
                self._progress["status"] = status
            self._progress["updated_at"] = time.time()

    def _maybe_release_idle_memory(self):
        now = time.time()
        if now - self._last_idle_cleanup_at < self.IDLE_CLEANUP_INTERVAL:
            return
        self._last_idle_cleanup_at = now

        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def watch_id(self, cls: type, object_id, attr: str):
        """
        Add an object ID to the watch list.
        """
        future = Future()
        with self._watched_ids_lock:
            self._watched_ids[(cls, object_id, attr)] = future
        return future

    def _notify_others(self, event_type: EventType, data=None):
        """
        Notify other components of an event.
        """
        if self._event_callback:
            self._event_callback(event_type, data)

    def _notify_ids_processed(
        self, notification: List[Tuple[Type, object, str, object]]
    ):
        """
        Notify that an object ID has been processed.
        """
        with self._watched_ids_lock:
            for cls, object_id, attr, payload in notification:
                future = self._watched_ids.pop((cls, object_id, attr), None)
                if future:
                    logger.debug(
                        f"Worker {self.name()} processed {cls.__name__} id={object_id} attr={attr}"
                    )
                    future.set_result((object_id, payload))

    def _wait(self):
        """
        Wait for a random short duration to stagger working time
        """
        self._set_progress(status="idle")
        self._maybe_release_idle_memory()
        wait_time = random.uniform(self.INTERVAL - 1.0, self.INTERVAL + 1.0)
        self._event.wait(wait_time)
        self._event.clear()

    @abstractmethod
    def _run(self):
        """
        The main logic of the worker.
        """
        pass
