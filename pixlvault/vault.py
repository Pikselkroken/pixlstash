import concurrent

import os
import time
import threading
import numpy as np

from typing import Optional

from sqlmodel import Session, select


from .database import DBPriority, VaultDatabase
from .db_models import MetaData, Character, Picture, PictureSet
from .pixl_logging import get_logger
from .picture_tagger import PictureTagger
from .picture_utils import PictureUtils
from .worker_registry import WorkerRegistry, WorkerType

# These import lines are all necessary to register the workers with the WorkerRegistry
from pixlvault.event_types import EventType
from pixlvault.tag_worker import TagWorker, DescriptionWorker, EmbeddingWorker  # noqa: F401
from pixlvault.feature_extraction_worker import FeatureExtractionWorker  # noqa: F401
from pixlvault.likeness_worker import LikenessWorker  # noqa: F401
from pixlvault.likeness_parameter_worker import LikenessParameterWorker  # noqa: F401
from pixlvault.image_embedding_worker import ImageEmbeddingWorker  # noqa: F401
from pixlvault.quality_worker import FaceQualityWorker, QualityWorker  # noqa: F401
from pixlvault.smart_score_scrapheap_worker import (  # noqa: F401
    SmartScoreScrapheapWorker,
)
from pixlvault.watch_folder_worker import WatchFolderWorker  # noqa: F401


logger = get_logger(__name__)


class Vault:
    AGGRESSIVE_UNLOAD_INTERVAL = 300
    # Map event type to list of worker types
    _event_worker_map = {
        EventType.CHANGED_PICTURES: [
            WorkerType.FACE,
            WorkerType.TAGGER,
            WorkerType.QUALITY,
            WorkerType.DESCRIPTION,
            WorkerType.IMAGE_EMBEDDING,
            WorkerType.LIKENESS_PARAMETERS,
            WorkerType.SMART_SCORE_SCRAPHEAP,
        ],
        EventType.CHANGED_TAGS: [WorkerType.SMART_SCORE_SCRAPHEAP],
        EventType.CHANGED_FACES: [
            WorkerType.FACE_QUALITY,
            WorkerType.TAGGER,
        ],
        EventType.CHANGED_CHARACTERS: [
            WorkerType.DESCRIPTION,
        ],
        EventType.CHANGED_DESCRIPTIONS: [WorkerType.TEXT_EMBEDDING],
        EventType.QUALITY_UPDATED: [
            WorkerType.LIKENESS,
            WorkerType.LIKENESS_PARAMETERS,
            WorkerType.SMART_SCORE_SCRAPHEAP,
        ],
        EventType.CLEARED_TAGS: [WorkerType.TAGGER, WorkerType.TEXT_EMBEDDING],
    }

    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def __exit__(self, _, __, ___):
        self.close()

    """
    Represents a vault for storing images and metadata.

    The vault contains a database that manages a SQLite database and stores the vault description in the metadata table.
    """

    def __init__(
        self,
        image_root: str,
        description: Optional[str] = None,
    ):
        """
        Initialize a Vault instance.

        Args:
            db_path (str): Path to the SQLite database file.
            image_root (Optional[str]): Path to the image root directory.
            description (Optional[str]): Description of the vault.
        """
        self.image_root = image_root
        logger.debug(f"Image root: {self.image_root}")
        assert self.image_root is not None, "image_root cannot be None"
        logger.debug(f"Using image_root: {self.image_root}")
        os.makedirs(self.image_root, exist_ok=True)
        assert os.path.exists(self.image_root), (
            f"Image root path does not exist: {self.image_root}"
        )

        self._db_path = os.path.join(self.image_root, "vault.db")
        self.db = VaultDatabase(self._db_path)
        self.set_description(description or "")

        self._picture_tagger = None
        self._last_aggressive_unload_at = 0.0
        self._keep_models_in_memory = True

        self._workers = {}
        self._event_listeners = []
        self._event_listeners_lock = threading.Lock()

    def stop_workers(self, workers_to_stop: set[WorkerType] = WorkerType.all()):
        logger.debug("Stopping background workers...")
        for worker in self._workers.values():
            if worker.worker_type() in workers_to_stop:
                logger.debug(f"Stopping worker: {worker.worker_type()}")
                worker.stop()

    def start_workers(self, workers_to_start: set[WorkerType] = WorkerType.all()):
        # Initialize all workers
        logger.debug("Initialise background workers...")
        for worker_type in workers_to_start:
            if worker_type not in self._workers:
                self.initialise_worker_if_necessary(worker_type)

        logger.debug("Starting background workers...")
        for worker_type in workers_to_start:
            worker = self._workers.get(worker_type)
            if worker:
                logger.info(f"Starting worker: {worker_type}")
                worker.start()
            else:
                logger.warning(f"Worker {worker_type} not found in vault workers.")

    def notify(self, event_type: EventType, data=None):
        """
        Notify all relevant workers for a given event type.

        Example:
            vault.notify(Vault.VaultEventType.NEW_PICTURE)
        """
        worker_types = self._event_worker_map.get(event_type, [])
        for worker_type in worker_types:
            worker = self._workers.get(worker_type)
            if worker:
                logger.debug(f"Notifying worker {worker_type} for event {event_type}")
                worker.notify(event_type=event_type, data=data)
            else:
                logger.debug(f"Worker {worker_type} not found for event {event_type}")
        with self._event_listeners_lock:
            listeners = list(self._event_listeners)
        for listener in listeners:
            try:
                listener(event_type, data)
            except Exception as exc:
                logger.warning("Event listener failed for %s: %s", event_type, exc)

    def add_event_listener(self, listener):
        """Register a callback to be invoked when vault events occur."""
        if not callable(listener):
            raise ValueError("listener must be callable")
        with self._event_listeners_lock:
            if listener not in self._event_listeners:
                self._event_listeners.append(listener)

    def __repr__(self):
        """
        Return a string representation of the Vault instance.

        Returns:
            str: String representation.
        """
        return f"Vault(db_path='{self._db_path}')"

    def close(self):
        """
        Cleanly close the vault, including stopping background workers and closing DB connection.
        """
        self.stop_workers(WorkerType.all())
        for worker in self._workers.values():
            worker.close()

        if self._picture_tagger:
            self._picture_tagger.close()
            del self._picture_tagger
            self._picture_tagger = None
        if self.db:
            self.db.close()
            del self.db
            self.db = None

    def set_keep_models_in_memory(self, keep_models_in_memory: bool):
        self._keep_models_in_memory = bool(keep_models_in_memory)

    def generate_text_embedding(self, query: str) -> Optional[np.ndarray]:
        """
        Generate a text embedding using the EmbeddingWorker.

        Args:
            text (str): Input text to generate embedding for.

        Returns:
            Optional[np.ndarray]: Generated text embedding or None if failed.
        """
        embedding = self._picture_tagger.generate_text_embedding(query=query)
        return embedding[0] if embedding is not None and len(embedding) > 0 else None

    def generate_clip_text_embedding(self, query: str) -> Optional[np.ndarray]:
        """
        Generate a CLIP text embedding for the provided query text.
        """
        return self._picture_tagger.generate_clip_text_embedding(query=query)

    def preprocess_query_words(self, words: list[str]) -> list[str]:
        """
        Preprocess a list of words using the PictureTagger.

        Args:
            words (list[str]): List of input words to preprocess.

        Returns:
            list[str]: Preprocessed list of words.
        """
        preprocessed_words = self._picture_tagger.preprocess_query_words(words=words)
        return preprocessed_words

    def set_description(self, description: str):
        def op(session: Session):
            metadata = session.exec(
                select(MetaData).where(
                    MetaData.schema_version == MetaData.CURRENT_SCHEMA_VERSION
                )
            ).first()
            if metadata is None:
                metadata = MetaData(
                    schema_version=MetaData.CURRENT_SCHEMA_VERSION,
                    description=description,
                )
            else:
                metadata.description = description
            session.add(metadata)
            session.commit()

        self.db.submit_task(op, priority=DBPriority.IMMEDIATE)

    def get_description(self) -> Optional[str]:
        return self.db.submit_task(
            lambda session: session.exec(
                select(MetaData).where(
                    MetaData.schema_version == MetaData.CURRENT_SCHEMA_VERSION
                )
            )
            .first()
            .description
        ).result()

    def initialise_worker_if_necessary(self, worker_type: WorkerType):
        """
        Initialize and start a specific worker type.

        Args:
            worker_type (WorkerType): The type of worker to initialize.
        """
        if not self._picture_tagger:
            self._picture_tagger = PictureTagger(image_root=self.image_root)

        if worker_type not in self._workers:
            worker_instance = WorkerRegistry.create_worker(
                worker_type,
                self.db,
                self._picture_tagger,
                event_callback=self.notify,
            )
            self._workers[worker_type] = worker_instance

    def get_worker_future(
        self, worker_type: WorkerType, cls: type, object_id: int, attr: str
    ) -> "concurrent.futures.Future":
        """
        Returns a Future that will be set when the specified worker has processed the given object ID.
        Args:
            worker_type (WorkerType): The type of worker to wait for.
        Returns:
            concurrent.futures.Future: Future set to True when completed.
        """
        self.initialise_worker_if_necessary(worker_type)

        worker = self._workers.get(worker_type)
        if worker is None:
            raise ValueError(f"Worker {worker_type} not found in vault.")

        return worker.watch_id(cls, object_id, attr)

    def is_worker_running(self, worker_type: WorkerType) -> bool:
        """
        Check if a specific worker is running.
        """
        worker = self._workers.get(worker_type)
        return worker is not None and worker.is_alive()

    def get_worker_progress(self) -> dict:
        progress = {}
        for worker_type in WorkerType.all():
            worker = self._workers.get(worker_type)
            if worker:
                snapshot = worker.get_progress()
                snapshot["running"] = worker.is_alive()
            else:
                snapshot = {
                    "label": "uninitialized",
                    "current": 0,
                    "total": 0,
                    "remaining": 0,
                    "updated_at": None,
                    "status": "uninitialized",
                    "running": False,
                }
            progress[worker_type.value] = snapshot
        self._maybe_aggressive_unload(progress)
        return progress

    def _maybe_aggressive_unload(self, progress: dict):
        if self._keep_models_in_memory:
            return
        if not self._picture_tagger:
            return
        now = time.time()
        if now - self._last_aggressive_unload_at < self.AGGRESSIVE_UNLOAD_INTERVAL:
            return

        any_busy = False
        for snapshot in progress.values():
            status = snapshot.get("status")
            running = bool(snapshot.get("running"))
            if running and status not in ("idle", "stopped", "uninitialized"):
                any_busy = True
                break
        if any_busy:
            return

        logger.warning("All workers idle; aggressively unloading models.")
        try:
            self._picture_tagger.aggressive_unload()
        except Exception as exc:
            logger.warning("Aggressive unload failed for PictureTagger: %s", exc)
        for worker in self._workers.values():
            try:
                worker.close()
            except Exception as exc:
                logger.warning("Aggressive unload failed for %s: %s", worker.name(), exc)
        self._last_aggressive_unload_at = now

    def import_default_data(self, add_tagger_test_images: bool = False):
        """
        Import default data into the vault.
        Extend this method to add default pictures or metadata as needed.
        """
        # Add Logo.png to every vault

        logo_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logo.png")
        logo_dest_folder = self.image_root
        logger.debug(f"logo_dest_folder in _import_default_data: {logo_dest_folder}")

        characters = [
            "Esmeralda Vault",
            "Barbara Vault",
            "Barry Vault",
            "Cassandra Vault",
        ]

        def add_character(session: Session, character: Character):
            session.add(character)
            session.commit()
            session.refresh(character)
            char_id = character.id
            char_name = character.name
            # Create reference picture set for this character, using character name
            reference_set = PictureSet(
                name="reference_pictures", description=str(char_name)
            )
            session.add(reference_set)
            session.commit()
            session.refresh(reference_set)
            return char_id, char_name

        for character_name in characters:
            self.db.run_task(
                lambda session: add_character(
                    session,
                    Character(
                        name=character_name, description="Built-in vault character"
                    ),
                ),
                priority=DBPriority.IMMEDIATE,
            )

        picture = PictureUtils.create_picture_from_file(
            image_root_path=logo_dest_folder,
            source_file_path=logo_src,
        )
        picture.description = "PixlVault Logo"

        assert picture.file_path

        def add_picture(session: Session, picture: Picture):
            session.add(picture)
            session.commit()
            session.refresh(picture)
            return picture

        picture = self.db.run_task(
            lambda session: add_picture(session, picture),
            priority=DBPriority.IMMEDIATE,
        )

        if add_tagger_test_images:
            # Add all pictures/TaggerTest*.png
            for file in os.listdir(
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "pictures")
            ):
                if file.startswith("TaggerTest") and file.endswith(".png"):
                    src_path = os.path.join(
                        os.path.dirname(os.path.dirname(__file__)),
                        "pictures",
                        file,
                    )
                    pic = PictureUtils.create_picture_from_file(
                        image_root_path=logo_dest_folder,
                        source_file_path=src_path,
                    )
                    pic.description = os.path.basename(src_path)
                    assert pic.file_path
                    self.db.run_task(
                        add_picture,
                        pic,
                        priority=DBPriority.IMMEDIATE,
                    )
                    logger.debug(f"Imported default picture: {pic.file_path}")
        logger.info("Imported default data into the vault.")
