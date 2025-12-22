import random
import threading

from typing import List, Tuple, Type
from concurrent.futures import Future
from abc import ABC, ABCMeta, abstractmethod
from enum import Enum

from .event_types import EventType
from .pixl_logging import get_logger


class WorkerType(str, Enum):
    FACE = "FaceExtractionWorker"
    TAGGER = "TagWorker"
    QUALITY = "QualityWorker"
    FACE_QUALITY = "FaceQualityWorker"
    FACE_LIKENESS = "FaceLikenessWorker"
    FACE_CHARACTER_LIKENESS = "FaceCharacterLikenessWorker"
    LIKENESS = "LikenessWorker"
    DESCRIPTION = "DescriptionWorker"
    TEXT_EMBEDDING = "EmbeddingWorker"

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
        if worker_name not in cls.registry:
            raise ValueError(f"Worker '{worker_name}' is not registered.")
        return cls.registry[worker_name.value](*args, **kwargs)


class BaseWorker(ABC, metaclass=WorkerRegistry):
    """
    Class representing different types of picture processing workers.
    """

    INTERVAL = 10  # Default interval between worker runs in seconds

    def __init__(self, database, picture_tagger, event_callback):
        self._db = database
        self._picture_tagger = picture_tagger

        self._stop = threading.Event()
        self._event = threading.Event()
        self._thread = None

        self._watched_ids = {}
        self._watched_ids_lock = threading.Lock()

        self._event_callback = event_callback

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
            self._thread.join()

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

    def notify(self):
        """
        Notify the worker that it needs to wake.
        """
        logger.info("Worker {} woken up.".format(self.name()))
        self._event.set()

    def name(self):
        """
        Return the name of the worker.
        """
        return self.worker_type().value

    def watch_id(self, cls: type, object_id, attr: str):
        """
        Add an object ID to the watch list.
        """
        future = Future()
        with self._watched_ids_lock:
            self._watched_ids[(cls, object_id, attr)] = future
        logger.debug(f"Future created for {cls.__name__} id={object_id} attr={attr}")
        return future

    def _notify_others(self, event_type: EventType):
        """
        Notify other components of an event.
        """
        if self._event_callback:
            self._event_callback(event_type)

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
                    logger.debug(
                        f"Future result set for {cls.__name__} id={object_id} attr={attr} with payload={payload}"
                    )

    def _wait(self):
        """
        Wait for a random short duration to stagger working time
        """
        if self._stop.is_set():
            return

        wait_time = random.uniform(self.INTERVAL - 1.0, self.INTERVAL + 1.0)
        self._event.wait(wait_time)
        self._event.clear()

    @abstractmethod
    def _run(self):
        """
        The main logic of the worker.
        """
        pass
