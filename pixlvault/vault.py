import os

from typing import Optional

from .logging import get_logger
from .characters import Characters
from .pictures import Pictures, PictureWorker
from .picture_characters import PictureCharacters
from .picture_sets import PictureSets
from .picture_utils import PictureUtils
from .character import Character
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
        logger.debug(f"Image root: {self.image_root}")
        assert self.image_root is not None, "image_root cannot be None"
        logger.debug(f"Using image_root: {self.image_root}")
        os.makedirs(self.image_root, exist_ok=True)
        assert os.path.exists(self.image_root), (
            f"Image root path does not exist: {self.image_root}"
        )

        self._db_path = os.path.join(self.image_root, "vault.db")
        self.db = VaultDatabase(self._db_path, description=description)

        self.characters = Characters(self.db)
        self.picture_characters = PictureCharacters(self.db)
        self.picture_sets = PictureSets(self.db)
        self.pictures = Pictures(self.db, self.characters)

    def stop_background_workers(self, workers: set[PictureWorker]):
        logger.debug("Stopping background workers...")
        for worker in workers:
            self.pictures.stop_worker(worker)

    def start_background_workers(self, workers: set[PictureWorker]):
        logger.debug("Starting background workers...")
        for worker in workers:
            self.pictures.start_worker(worker)

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
        self.stop_background_workers(PictureWorker.all())

    def _create_tables(self):
        self.db._create_tables()

    def set_metadata(self, key: str, value: str):
        self.db.set_metadata(key, value)

    def get_metadata(self, key: str) -> Optional[str]:
        return self.db.get_metadata(key)

    def get_description(self) -> Optional[str]:
        return self.db.get_description()

    def import_default_data(self, add_tagger_test_images: bool = False):
        """
        Import default data into the vault.
        Extend this method to add default pictures or metadata as needed.
        """
        # Add Logo.png to every vault

        logo_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logo.png")
        logo_dest_folder = self.image_root
        logger.debug(f"logo_dest_folder in _import_default_data: {logo_dest_folder}")

        character = Character(
            name="Esmeralda Vault", description="Built-in vault character"
        )
        self.characters.add(character)

        picture = PictureUtils.create_picture_from_file(
            image_root_path=logo_dest_folder,
            source_file_path=logo_src,
            character_id=character.id,
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
                        character_id=character.id,
                    )
                    assert pic.file_path
                    self.pictures.add(pic)
                    logger.debug(f"Imported default picture: {pic.file_path}")

        assert picture.file_path
        self.pictures.add(picture)
        logger.info("Imported default data into the vault.")
