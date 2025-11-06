import json
import os
import sqlite3

from typing import Optional

from .logging import get_logger
from .characters import Characters
from .pictures import Pictures

from .character import Character
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

        self.characters = Characters(self.connection)
        self.pictures = Pictures(self.connection, self._db_path, self.characters)

        self.pictures.start_quality_worker()
        self.pictures.start_embeddings_worker()

    def stop_background_workers(self):
        if hasattr(self, "pictures") and hasattr(self.pictures, "stop_quality_worker"):
            self.pictures.stop_quality_worker()
        if hasattr(self, "pictures") and hasattr(
            self.pictures, "stop_embeddings_worker"
        ):
            self.pictures.stop_embeddings_worker()

    def start_background_workers(self):
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

        cursor.execute(
            """
            CREATE TABLE picture_tags (
                picture_id TEXT,
                tag TEXT,
                PRIMARY KEY (picture_id, tag),
                FOREIGN KEY (picture_id) REFERENCES pictures(id) ON DELETE CASCADE
            )
            """
        )

        # Pictures (master assets) table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pictures (
                id TEXT PRIMARY KEY,
                character_id INTEGER,
                file_path TEXT NOT NULL,
                description TEXT,
                format TEXT,
                width INTEGER,
                height INTEGER,
                size_bytes INTEGER,
                created_at TEXT,
                is_reference INTEGER DEFAULT 0 CHECK(is_reference BETWEEN 0 AND 1),
                embedding BLOB,
                face_bbox TEXT,
                thumbnail BLOB,
                quality TEXT,
                face_quality TEXT,
                score INTEGER CHECK(score BETWEEN 0 AND 5),
                character_likeness FLOAT CHECK(character_likeness >= 0.0 AND character_likeness <= 1.0),
                pixel_sha TEXT,
                FOREIGN KEY(character_id) REFERENCES characters(id)
            )
            """
        )

        # Index for fast lookup by pixel_sha
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pictures_pixel_sha ON pictures(pixel_sha)
            """
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

    def get_pictures_from_ids(self, picture_ids: list[str]) -> list[Picture]:
        """
        Fetch picture objects for the given list of picture IDs.

        Args:
            picture_ids (list[str]): List of picture IDs to fetch.
        Returns:
            list[Picture]: The matching Pictures or an empty list if not found.
        """
        cursor = self.connection.cursor()

        if not picture_ids:
            return []

        placeholders = ",".join(["?"] * len(picture_ids))
        sql = f"SELECT * FROM pictures WHERE id IN ({placeholders})"
        rows = cursor.execute(sql, picture_ids)

        return [Picture.from_dict(row) for row in rows] if rows else []

    def get_picture_info(self, filters: dict) -> list[Picture]:
        """
        Fetch picture objects for all matching pictures based on filters.

        Args:
            filters (dict): A dictionary of filters to apply.
        Returns:
            list[Picture]: The matching Pictures or an empty list if not found.
        """
        cursor = self.connection.cursor()

        if not filters:
            cursor.execute("SELECT * FROM pictures")
            rows = cursor.fetchall()
        else:
            where_clause = " AND ".join([f"{k}=?" for k in filters.keys()])
            sql = f"SELECT * FROM pictures WHERE {where_clause}"
            params = list(filters.values())
            rows = cursor.execute(sql, params)

        return [Picture.from_dict(row) for row in rows] if rows else []

    def delete_pictures(self, picture_id: list[str]):
        """
        Delete pictures by their IDs.

        Args:
            picture_id (list[str]): List of picture IDs to delete.
        """
        cursor = self.connection.cursor()

        # Delete the pictures themselves
        cursor.executemany(
            "DELETE FROM pictures WHERE id = ?",
            [(pid,) for pid in picture_id],
        )

        self.connection.commit()

    def insert_pictures(self, pictures: list[Picture]):
        """
        Insert multiple pictures into the database.

        Args:
            pictures (list[Picture]): List of Picture instances to insert.
        """
        cursor = self.connection.cursor()
        for picture in pictures:
            dict = picture.to_dict()
            # Remove tags from dict if present
            dict.pop("tags", None)
            columns = ", ".join(dict.keys())
            placeholders = ", ".join([f":{k}" for k in dict.keys()])
            sql = f"INSERT INTO pictures ({columns}) VALUES ({placeholders})"
            cursor.execute(sql, dict)
            # Insert tags into picture_tags table
            if hasattr(picture, "tags") and picture.tags:
                tag_cursor = self.connection.cursor()
                tag_cursor.executemany(
                    "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                    [(picture.id, tag) for tag in picture.tags],
                )
        self.connection.commit()

    def update_pictures(self, pictures: list[Picture]):
        """
        Update multiple pictures in the database.

        Args:
            pictures (list[Picture]): List of Picture instances to update.
        """
        cursor = self.connection.cursor()
        for picture in pictures:
            dict = picture.to_dict()
            dict.pop("tags", None)
            columns = ", ".join(dict.keys())
            placeholders = ", ".join([f":{k}" for k in dict.keys()])
            sql = f"UPDATE pictures SET ({columns}) = ({placeholders}) WHERE id = :id"
            cursor.execute(sql, dict)
            # Update tags in picture_tags table
            tag_cursor = self.connection.cursor()
            tag_cursor.execute(
                "DELETE FROM picture_tags WHERE picture_id = ?", (picture.id,)
            )
            if hasattr(picture, "tags") and picture.tags:
                tag_cursor.executemany(
                    "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                    [(picture.id, tag) for tag in picture.tags],
                )
        self.connection.commit()

    def delete_character(self, character_id: int):
        """
        Delete a character by ID, and unset character_id in related pictures.

        Args:
            character_id (int): The ID of the character to delete.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE pictures SET character_id = NULL WHERE character_id = ?",
            (character_id,),
        )
        cursor.execute(
            "DELETE FROM characters WHERE id = ?",
            (character_id,),
        )
        self.connection.commit()

    def update_character(self, character: Character):
        """
        Update a character's information in the database.

        Args:
            character (Character): The Character instance with updated information.
        """
        cursor = self.connection.cursor()
        dict = character.to_dict()
        cursor.execute(
            """
            UPDATE characters
            SET name = ?, original_seed = ?, original_prompt = ?, lora_model = ?, description = ?
            WHERE id = ?
            """,
            (
                dict["name"],
                dict["original_seed"],
                dict["original_prompt"],
                json.dumps(dict["loras"]) if dict["loras"] else None,
                dict["description"],
                dict["id"],
            ),
        )

        self.connection.commit()

    def get_character(self, character_id: int) -> Optional[Character]:
        """
        Retrieve a character by ID.

        Args:
            character_id (int): The ID of the character to retrieve.
        Returns:
            Optional[Character]: The Character instance if found, else None.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT * FROM characters WHERE id = ?",
            (character_id,),
        )
        row = cursor.fetchone()
        return Character.from_dict(row) if row else None

    def get_pictures_matching_shas(self, shas: list[str]) -> list[Picture]:
        """
        Fetch all pictures whose pixel_sha is in the provided list of shas, using a single query.
        Args:
            shas (list[str]): List of SHA strings to match.
        Returns:
            list[Picture]: List of matching Picture objects.
        """
        if not shas:
            return []
        cursor = self.connection.cursor()
        placeholders = ",".join(["?"] * len(shas))
        sql = f"SELECT * FROM pictures WHERE pixel_sha IN ({placeholders})"
        rows = cursor.execute(sql, shas)
        return [Picture.from_dict(row) for row in rows] if rows else []

    def import_default_data(self):
        """
        Import default data into the vault.
        Extend this method to add default pictures or metadata as needed.
        """
        # Add Logo.png to every vault

        logo_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logo.png")
        logo_dest_folder = self.image_root
        logger.debug(f"logo_dest_folder in _import_default_data: {logo_dest_folder}")

        character = Character(
            name="EsmeraldaVault", description="Built-in vault character"
        )
        self.characters.add(character)

        picture = Picture.create_from_file(
            image_root_path=logo_dest_folder,
            source_file_path=logo_src,
            character_id=character.id,
            description="Vault Logo",
        )
        assert picture.file_path
        self.insert_pictures([picture])
