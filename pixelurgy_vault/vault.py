from typing import Optional

import os
import sqlite3

from .pictures import Pictures


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
        if image_root:
            self.set_metadata("image_root", image_root)
        if description:
            self.set_metadata("description", description)
        self.pictures = Pictures(self.connection)

        # Add Logo.png to every vault
        import shutil
        from pixelurgy_vault.picture import Picture

        logo_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logo.png")
        logo_dest_folder = self.get_image_root()
        if logo_dest_folder:
            os.makedirs(logo_dest_folder, exist_ok=True)
            logo_dest = os.path.join(logo_dest_folder, "Logo.png")
            if not os.path.exists(logo_dest):
                shutil.copy2(logo_src, logo_dest)
            # Always import the logo as a Picture
            pic = Picture(
                id=Picture.calculate_sha256_from_file_path(logo_dest),
                file_path=logo_dest,
                character_id="logo",
                description="Vault Logo",
                tags=["logo"],
                format="png",
            )
            if not self.pictures.contains(pic):
                self.pictures.import_pictures([pic])

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
        # Pictures table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pictures (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                character_id TEXT,
                description TEXT,
                tags TEXT,
                width INTEGER,
                height INTEGER,
                format TEXT,
                created_at TEXT,
                thumbnail BLOB,
                score INTEGER CHECK(score BETWEEN 0 AND 5),
                quality TEXT
            )
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

    def get_image_root(self) -> Optional[str]:
        return self.get_metadata("image_root")

    def get_description(self) -> Optional[str]:
        return self.get_metadata("description")

    def get_metadata(self, key: str) -> Optional[str]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else None
