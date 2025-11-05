import base64
import json
import os
import sqlite3
import shutil

from typing import Optional

from .logging import get_logger
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

        self._db_path = os.path.join(self.image_root, "vault.db")

        self.connection: Optional[sqlite3.Connection] = None
        db_exists = os.path.exists(self._db_path)
        logger.info(f"Vault init, db_path={self._db_path}, db_exists={db_exists}")
        self.connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrency
        try:
            self.connection.execute("PRAGMA journal_mode=WAL;")
        except Exception as e:
            logger.warning(f"Failed to set WAL mode: {e}")
        if not db_exists:
            logger.debug("Creating tables and importing default data...")
            self._create_tables()
        else:
            logger.debug("Using existing database, skipping default import.")
        self._upgrader = VaultUpgrade(self.connection)
        self._upgrader.upgrade_if_necessary()

        if description is not None:
            self.set_metadata("description", description)

        self.iterations = PictureIterations(self.connection, self._db_path)
        self.characters = Characters(self.connection)
        self.pictures = Pictures(
            self.connection, self.iterations, self._db_path, self.characters
        )

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
        return f"Vault(db_path='{self._db_path}')"

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

    def get_description(self) -> Optional[str]:
        return self.get_metadata("description")

    def get_metadata(self, key: str) -> Optional[str]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else None

    def reference_pictures(self, character_id) -> list[dict]:
        """
        Get the reference pictures for a given character ID.

        Args:
            character_id (str): The character ID.
        Returns:
            list[dict]: The reference PictureIterations or an empty list if not found.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT * FROM pictures WHERE character_id = ? AND is_reference = 1",
            (character_id,),
        )
        reference_pics = cursor.fetchall()
        pic_ids = [row["id"] for row in reference_pics]

        iter_map = {}
        if pic_ids:
            qmarks = ",".join(["?"] * len(pic_ids))
            cursor.execute(
                f"SELECT id, picture_id, thumbnail, score FROM picture_iterations WHERE is_master=1 AND picture_id IN ({qmarks})",
                tuple(pic_ids),
            )
            for row in cursor.fetchall():
                iter_map[row["picture_id"]] = row

        pictures = []
        for pic in reference_pics:
            iteration = iter_map.get(pic["id"], None)
            if iteration:
                thumbnail_b64 = (
                    base64.b64encode(iteration["thumbnail"]).decode("ascii")
                    if iteration and iteration["thumbnail"]
                    else None
                )

                pictures.append(
                    {
                        "picture_id": pic["id"],
                        "iteration_id": iteration["id"],
                        "description": pic["description"],
                        "tags": json.loads(pic["tags"]) if pic["tags"] else [],
                        "score": iteration["score"],
                        "thumbnail": thumbnail_b64,
                        "created_at": pic["created_at"],
                    }
                )
        return pictures

    def list_pictures_info(self, pics) -> list[dict]:
        """
        Batch fetch all picture information with scores

        Args:
            pics (list[Picture]): List of Picture objects to fetch info for.
        Returns:
            list[dict]: List of picture info dictionaries.
        """
        pic_ids = [pic.id for pic in pics]
        score_map = {}
        if pic_ids:
            cursor = self.connection.cursor()
            qmarks = ",".join(["?"] * len(pic_ids))
            cursor.execute(
                f"SELECT picture_id, score FROM picture_iterations WHERE is_master=1 AND picture_id IN ({qmarks})",
                tuple(pic_ids),
            )
            for row in cursor.fetchall():
                score_map[row[0]] = row[1]
        result = []
        for pic in pics:
            score = score_map.get(pic.id)
            result.append(
                {
                    "id": pic.id,
                    "character_id": pic.character_id,
                    "description": pic.description,
                    "tags": pic.tags,
                    "created_at": pic.created_at,
                    "score": score,
                    "is_reference": getattr(pic, "is_reference", 0),
                }
            )
        return result

    def delete_character(self, character_id: int):
        """
        Delete a character by ID, and unset character_id in related pictures and iterations.

        Args:
            character_id (int): The ID of the character to delete.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE pictures SET character_id = NULL WHERE character_id = ?",
            (character_id,),
        )
        cursor.execute(
            "UPDATE picture_iterations SET character_id = NULL WHERE character_id = ?",
            (character_id,),
        )
        cursor.execute(
            "DELETE FROM characters WHERE id = ?",
            (character_id,),
        )
        self.connection.commit()

    def import_default_data(self):
        """
        Import default data into the vault.
        Extend this method to add default pictures or metadata as needed.
        """
        # Add Logo.png to every vault

        logo_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logo.png")
        logo_dest_folder = self.image_root
        logger.debug(f"logo_dest_folder in _import_default_data: {logo_dest_folder}")
        if not logo_dest_folder:
            # Fallback: use a default images directory next to the DB file
            logo_dest_folder = os.path.join(os.path.dirname(self._db_path), "images")
            logger.debug(f"Fallback logo_dest_folder: {logo_dest_folder}")
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
