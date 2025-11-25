import random
import threading
import time

from abc import ABC, ABCMeta, abstractmethod
from enum import Enum

from pixlvault.database import DBPriority


class WorkerType(str, Enum):
    FACIAL_FEATURES = "FacialFeaturesWorker"
    TAGGER = "TagWorker"
    QUALITY = "QualityWorker"
    LIKENESS = "LikenessWorker"
    DESCRIPTION = "DescriptionWorker"
    TEXT_EMBEDDING = "EmbeddingWorker"

    @staticmethod
    def all():
        return set(item for item in WorkerType)


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

    def __init__(self, db_connection, picture_tagger, characters):
        self._db = db_connection
        self._picture_tagger = picture_tagger
        self._characters = characters
        self._stop = threading.Event()
        self._thread = None

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

    def _update_attributes(self, pictures, attributes):
        """Update specified attributes for a list of Picture instances in the database using executemany for efficiency."""

        values = []
        for picture in pictures:
            row = picture.to_dict()
            attr_values = [row[attr] for attr in attributes]
            attr_values.append(picture.id)
            values.append(tuple(attr_values))
            # logger.info(f"Updating picture {picture.id} with attributes: {row}")
        set_clause = ", ".join([f"{attr}=?" for attr in attributes])
        query = f"UPDATE pictures SET {set_clause} WHERE id=?"
        return self._db.submit_task(
            lambda conn: conn.executemany(query, values), priority=DBPriority.LOW
        ).result()
