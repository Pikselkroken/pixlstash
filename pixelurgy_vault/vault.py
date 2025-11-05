import os

from typing import Optional

from .logging import get_logger
from .characters import Characters
from .pictures import Pictures
from .character import Character
from .picture import Picture
from .database import VaultDatabase

logger = get_logger(__name__)


class Vault:
    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
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
        assert self.image_root is not None, "image_root cannot be None"
        logger.info(f"Using image_root: {self.image_root}")
        os.makedirs(self.image_root, exist_ok=True)

        db_path = os.path.join(self.image_root, "vault.db")
        self.db = VaultDatabase(db_path, description=description)

        self.characters = Characters(self.db)
        self.pictures = Pictures(self.db)

        self.start_background_workers()

    def stop_background_workers(self):
        logger.info("Stopping background workers...")
        if hasattr(self, "pictures") and hasattr(self.pictures, "stop_quality_worker"):
            self.pictures.stop_quality_worker()
        if hasattr(self, "pictures") and hasattr(
            self.pictures, "stop_embeddings_worker"
        ):
            self.pictures.stop_embeddings_worker()

    def start_background_workers(self):
        logger.info("Starting background workers...")
        if hasattr(self, "pictures") and hasattr(self.pictures, "start_quality_worker"):
            self.pictures.start_quality_worker()
        if hasattr(self, "pictures") and hasattr(
            self.pictures, "start_embeddings_worker"
        ):
            self.pictures.start_embeddings_worker()

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
        self.stop_background_workers()
        if hasattr(self, "db") and self.db:
            self.db.close()

    def _create_tables(self):
        self.db._create_tables()

    def set_metadata(self, key: str, value: str):
        self.db.set_metadata(key, value)

    def get_metadata(self, key: str) -> Optional[str]:
        return self.db.get_metadata(key)

    def get_description(self) -> Optional[str]:
        return self.db.get_description()

    def get_pictures_from_ids(self, picture_ids: list[str]) -> list[Picture]:
        return self.db.fetch_pictures_by_ids(picture_ids)

    def get_picture_info(self, filters: dict) -> list[Picture]:
        return self.db.fetch_pictures(filters)

    def delete_pictures(self, picture_ids: list[str]):
        self.db.delete_pictures(picture_ids)

    def insert_pictures(self, pictures: list[Picture]):
        self.db.insert_pictures(pictures)

    def update_pictures(self, pictures: list[Picture]):
        self.db.update_pictures(pictures)

    def delete_character(self, character_id: int):
        self.db.delete_character(character_id)

    def update_character(self, character: Character):
        self.db.update_character(character)

    def get_character(self, character_id: int) -> Optional[Character]:
        return self.db.fetch_character_by_id(character_id)

    def get_pictures_matching_shas(self, shas: list[str]) -> list[Picture]:
        return self.db.fetch_pictures_by_shas(shas)
