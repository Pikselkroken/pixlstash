import concurrent

import os
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
from pixlvault.face_extraction_worker import FaceExtractionWorker  # noqa: F401
from pixlvault.facial_features_worker import FacialFeaturesWorker  # noqa: F401
from pixlvault.face_likeness_worker import FaceLikenessWorker  # noqa: F401
from pixlvault.likeness_worker import LikenessWorker  # noqa: F401
from pixlvault.quality_worker import FaceQualityWorker, QualityWorker  # noqa: F401


logger = get_logger(__name__)


class Vault:
    # Map event type to list of worker types
    _event_worker_map = {
        EventType.CHANGED_PICTURES: [
            WorkerType.FACE,
            WorkerType.TAGGER,
            WorkerType.QUALITY,
            WorkerType.DESCRIPTION,
        ],
        EventType.CHANGED_TAGS: [],
        EventType.CHANGED_FACES: [WorkerType.FACIAL_FEATURES, WorkerType.FACE_QUALITY],
        EventType.CHANGED_CHARACTERS: [WorkerType.DESCRIPTION],
        EventType.CHANGED_DESCRIPTIONS: [WorkerType.TEXT_EMBEDDING],
    }

    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def __exit__(self, _, __, ___):
        self.close()

    """
    Represents a vault for storing images and metadata.

    The vault contains a database that manages a SQLite database and stores the image root and description in the metadata table.
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

        self._picture_tagger = PictureTagger()

        self._workers = {}

        for worker_type in WorkerType.all():
            logger.debug(f"Creating worker of type: {worker_type}")
            self._workers[worker_type] = WorkerRegistry.create_worker(
                worker_type,
                self.db,
                self._picture_tagger,
                event_callback=lambda et: self.notify(et),
            )

    def stop_workers(self, workers: set[WorkerType] = WorkerType.all()):
        logger.debug("Stopping background workers...")
        for worker in workers:
            if worker in self._workers:
                logger.debug(f"Stopping worker: {worker}")
                self._workers[worker].stop()
            else:
                logger.warning(f"Worker {worker} not found in vault workers.")

    def start_workers(self, workers: set[WorkerType] = WorkerType.all()):
        logger.debug("Starting background workers...")
        for worker in workers:
            if worker in self._workers:
                logger.info(f"Starting worker: {worker}")
                self._workers[worker].start()
            else:
                logger.warning(f"Worker {worker} not found in vault workers.")

    def notify(self, event_type: EventType):
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
                worker.notify()
            else:
                logger.warning(f"Worker {worker_type} not found for event {event_type}")

    def queue_likeness_pair_calculation(self, picture_id_a: str, picture_id_b: str):
        """
        Queue a pair of pictures for likeness calculation.

        Args:
            picture_id_a (str): ID of the first picture.
            picture_id_b (str): ID of the second picture.
        """
        likeness_worker: LikenessWorker = self._workers.get(WorkerType.LIKENESS)
        if likeness_worker is None:
            raise ValueError("LikenessWorker is not available in this vault.")
        likeness_worker.queue_pair(picture_id_a, picture_id_b)

    def queue_face_likeness_pair_calculation(self, face_id_a: int, face_id_b: int):
        """
        Queue a pair of faces for likeness calculation.

        Args:
            face_id_a (int): ID of the first face.
            face_id_b (int): ID of the second face.
        """
        likeness_worker: FaceLikenessWorker = self._workers.get(
            WorkerType.FACE_LIKENESS
        )
        if likeness_worker is None:
            raise ValueError("FaceLikenessWorker is not available in this vault.")
        likeness_worker.queue_pair(face_id_a, face_id_b)

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

    def generate_text_embedding(self, query: str) -> Optional[np.ndarray]:
        """
        Generate a text embedding using the EmbeddingWorker.

        Args:
            text (str): Input text to generate embedding for.

        Returns:
            Optional[np.ndarray]: Generated text embedding or None if failed.
        """
        embedding, _ = self._picture_tagger.generate_text_embedding(query=query)
        return embedding

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

        worker = self._workers.get(worker_type)
        if worker is None:
            raise ValueError(f"Worker {worker_type} not found in vault.")

        return worker.watch_id(cls, object_id, attr)

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
                    self.db.submit_task(
                        lambda session: (session.add(pic), session.commit()),
                        priority=DBPriority.IMMEDIATE,
                    )
                    logger.debug(f"Imported default picture: {pic.file_path}")
        logger.info("Imported default data into the vault.")
