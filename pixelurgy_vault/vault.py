from typing import Optional

from .logging import get_logger
import os
import sqlite3
import shutil

from .characters import Characters
from .pictures import Pictures
from .picture_iterations import PictureIterations

from .character import Character
from .picture_iteration import PictureIteration
from .picture import Picture
from .vault_upgrade import VaultUpgrade

logger = get_logger(__name__)


class Vault:
    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    """
    Represents a vault for storing images and metadata.

    The vault manages a SQLite database and stores the image root and description in the metadata table.

    Attributes:
        db_path (str): Path to the SQLite database file.
        connection (Optional[sqlite3.Connection]): SQLite connection object.
        pictures (Pictures): Pictures manager.
        iterations (PictureIterations): PictureIterations manager.
        characters (Characters): Characters manager.
        upgrader (VaultUpgrade): VaultUpgrade instance for schema upgrades.
    """

    def __init__(
        self,
        db_path: str,
        image_root: Optional[str] = None,
        description: Optional[str] = None,
    ):
        """
        Initialize a Vault instance.

        Args:
            db_path (str): Path to the SQLite database file.
            image_root (Optional[str]): Path to the image root directory.
            description (Optional[str]): Description of the vault.
        """
        self.logger = get_logger(__name__)
        self.db_path = db_path  # Path to SQLite database file
        self.connection: Optional[sqlite3.Connection] = None
        db_exists = os.path.exists(self.db_path)
        self.logger.debug(f"Vault init, db_path={self.db_path}, db_exists={db_exists}")
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrency
        try:
            self.connection.execute("PRAGMA journal_mode=WAL;")
        except Exception as e:
            self.logger.warning(f"Failed to set WAL mode: {e}")
        if not db_exists:
            self.logger.debug("Creating tables and importing default data...")
            self._create_tables()
        else:
            self.logger.debug("Using existing database, skipping default import.")
        self.upgrader = VaultUpgrade(self.connection)
        self.upgrader.upgrade_if_necessary()
        if image_root is not None:
            self.set_metadata("image_root", image_root)
        if description is not None:
            self.set_metadata("description", description)
        self.iterations = PictureIterations(self.connection, self.db_path)
        self.pictures = Pictures(self.connection, self.iterations, self.db_path)
        self.characters = Characters(self.connection)
        if not db_exists:
            self._import_default_data()
        self.iterations.start_quality_worker()
        self.pictures.start_embeddings_worker()

    def stop_background_workers(self):
        if hasattr(self, "iterations") and hasattr(
            self.iterations, "stop_quality_worker"
        ):
            self.iterations.stop_quality_worker()
        if hasattr(self, "pictures") and hasattr(
            self.pictures, "stop_embeddings_worker"
        ):
            self.pictures.stop_embeddings_worker()

    def start_background_workers(self):
        if hasattr(self, "iterations") and hasattr(
            self.iterations, "start_quality_worker"
        ):
            self.iterations.start_quality_worker()
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
        return f"Vault(db_path='{self.db_path}')"

    def close(self):
        """
        Cleanly close the vault, including stopping background workers and closing DB connection.
        """
        self.stop_background_workers()
        if hasattr(self, "connection") and self.connection:
            self.connection.close()

    def _create_tables(self):
        """
        Create initial tables in the database. Extend as needed.
        """
        cursor = self.connection.cursor()
        # Metadata table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )

        # Characters table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                original_seed INTEGER,
                original_prompt TEXT,
                lora_model TEXT,
                description TEXT
            )
            """
        )

        # Pictures (master assets) table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pictures (
                id TEXT PRIMARY KEY,
                character_id INTEGER,
                description TEXT,
                tags TEXT,
                created_at TEXT,
                is_reference INTEGER DEFAULT 0 CHECK(is_reference BETWEEN 0 AND 1),
                embedding BLOB,
                face_embedding TEXT,
                FOREIGN KEY(character_id) REFERENCES characters(id)
            )
            """
        )

        # Picture iterations (content snapshots) table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS picture_iterations (
                id TEXT PRIMARY KEY,
                picture_id TEXT NOT NULL,
                character_id INTEGER,
                file_path TEXT NOT NULL,
                format TEXT,
                width INTEGER,
                height INTEGER,
                size_bytes INTEGER,
                created_at TEXT,
                is_master INTEGER DEFAULT 0 CHECK(is_master BETWEEN 0 AND 1),
                derived_from TEXT,
                transform_metadata TEXT,
                thumbnail BLOB,
                quality TEXT,
                face_quality TEXT,
                score INTEGER CHECK(score BETWEEN 0 AND 5),
                character_likeness FLOAT CHECK(character_likeness >= 0.0 AND character_likeness <= 1.0),
                pixel_sha TEXT,
                FOREIGN KEY(picture_id) REFERENCES pictures(id),
                FOREIGN KEY(character_id) REFERENCES characters(id)
            )
            """
        )

        # Helpful indexes
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_iterations_picture_id ON picture_iterations(picture_id)"
        )
        self.connection.commit()

    def set_metadata(self, key: str, value: str):
        cursor = self.connection.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)
        """,
            (key, value),
        )
        self.connection.commit()

    def get_image_root(self) -> Optional[str]:
        return self.get_metadata("image_root")

    def get_description(self) -> Optional[str]:
        return self.get_metadata("description")

    def get_metadata(self, key: str) -> Optional[str]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else None

    def _import_default_data(self):
        """
        Import default data into the vault.
        Extend this method to add default pictures or metadata as needed.
        """
        # Add Logo.png to every vault

        logo_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logo.png")
        logo_dest_folder = self.get_image_root()
        self.logger.debug(
            f"logo_dest_folder in _import_default_data: {logo_dest_folder}"
        )
        if not logo_dest_folder:
            # Fallback: use a default images directory next to the DB file
            logo_dest_folder = os.path.join(os.path.dirname(self.db_path), "images")
            self.logger.debug(f"Fallback logo_dest_folder: {logo_dest_folder}")
        os.makedirs(logo_dest_folder, exist_ok=True)
        logo_dest = os.path.join(logo_dest_folder, "Logo.png")
        if not os.path.exists(logo_dest):
            shutil.copy2(logo_src, logo_dest)

        character = Character(
            name="EsmeraldaVault", description="Built-in vault character"
        )
        self.characters.add(character)

        picture = Picture(
            character_id=character.id, description="Vault Logo", tags=["logo"]
        )
        # create_from_file returns (picture_id, PictureIteration)
        _, iteration = PictureIteration.create_from_file(
            picture_id=picture.id,
            image_root_path=logo_dest_folder,
            source_file_path=logo_dest,
            is_master=True,
        )
        # Import iteration (will create master picture row if missing)
        self.pictures.import_pictures([picture])
        self.iterations.import_iterations([iteration])
