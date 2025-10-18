from typing import Optional

import os
import sqlite3
import shutil

from .pictures import Pictures
from .picture_iterations import PictureIterations
from .picture_iteration import PictureIteration
from .picture import Picture


class Vault:
    """
    Represents a vault for storing images and metadata.
    Specifies the path to a SQLite database and stores the image root and description in the metadata table.
    """

    def __init__(
        self,
        db_path: str,
        image_root: Optional[str] = None,
        description: Optional[str] = None,
    ):
        self.db_path = db_path  # Path to SQLite database file
        self.connection: Optional[sqlite3.Connection] = None
        db_exists = os.path.exists(self.db_path)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        if not db_exists:
            self._create_tables()
            self._import_default_data()
        if image_root:
            self.set_metadata("image_root", image_root)
        if description:
            self.set_metadata("description", description)
        self.pictures = Pictures(self.connection)
        self.iterations = PictureIterations(self.connection)

    def __repr__(self):
        return f"Vault(db_path='{self.db_path}')"

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
        # Pictures (master assets) table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pictures (
                id TEXT PRIMARY KEY,
                character_id TEXT,
                title TEXT,
                description TEXT,
                tags TEXT,
                created_at TEXT
            )
            """
        )

        # Picture iterations (content snapshots) table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS picture_iterations (
                id TEXT PRIMARY KEY,
                picture_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                format TEXT,
                width INTEGER,
                height INTEGER,
                size_bytes INTEGER,
                created_at TEXT,
                is_master INTEGER DEFAULT 0,
                derived_from TEXT,
                transform_metadata TEXT,
                thumbnail BLOB,
                quality TEXT,
                score INTEGER CHECK(score BETWEEN 0 AND 5),
                pixel_sha TEXT,
                FOREIGN KEY(picture_id) REFERENCES pictures(id)
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
        return row[0] if row else None

    def _import_default_data(self):
        """
        Import default data into the vault.
        Extend this method to add default pictures or metadata as needed.
        """
        # Add Logo.png to every vault

        logo_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logo.png")
        logo_dest_folder = self.get_image_root()
        if logo_dest_folder:
            os.makedirs(logo_dest_folder, exist_ok=True)
            logo_dest = os.path.join(logo_dest_folder, "Logo.png")
            if not os.path.exists(logo_dest):
                shutil.copy2(logo_src, logo_dest)

            picture = Picture(
                character_id="logo", description="Vault Logo", tags=["logo"]
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
            self.pictures.import_iterations([iteration])
