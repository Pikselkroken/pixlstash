import random
import threading
import time
from typing import List, Tuple, Type
from concurrent.futures import Future
from abc import ABC, ABCMeta, abstractmethod
from enum import Enum

from .logging import get_logger


class WorkerType(str, Enum):
    FACIAL_FEATURES = "FacialFeaturesWorker"
    TAGGER = "TagWorker"
    QUALITY = "QualityWorker"
    FACE_QUALITY = "FaceQualityWorker"
    #    LIKENESS = "LikenessWorker"
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

    INTERVAL = 2.5  # Default interval between worker runs in seconds

    def __init__(self, database, picture_tagger):
        self._db = database
        self._picture_tagger = picture_tagger

        self._stop = threading.Event()
        self._thread = None

        self._watched_ids = {}
        self._watched_ids_lock = threading.Lock()

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
        return future

    def _notify_ids_processed(self, object_ids: List[Tuple[Type, object, str]]):
        """
        Notify that an object ID has been processed.
        """
        with self._watched_ids_lock:
            for cls, object_id, attr in object_ids:
                logger.info(
                    f"Trying to notify processed ID: {cls.__name__} id={object_id} attr={attr}"
                )

                future = self._watched_ids.pop((cls, object_id, attr), None)
                if future:
                    logger.info(
                        f"Worker {self.name()} processed {cls.__name__} id={object_id} attr={attr}"
                    )
                    future.set_result(object_id)

    def _wait(self):
        """
        Wait for a random short duration to stagger working time
        """
        wait_time = random.uniform(self.INTERVAL - 1.0, self.INTERVAL + 1.0)
        time.sleep(wait_time)

    @abstractmethod
    def _run(self):
        """
        The main logic of the worker.
        """
        pass
