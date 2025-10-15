from typing import Optional

import os
import sqlite3


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
        self.connection = sqlite3.connect(self.db_path)
        if not db_exists:
            self._create_tables()
        if image_root:
            self.set_metadata("image_root", image_root)
        if description:
            self.set_metadata("description", description)

    def __repr__(self):
        return f"Vault(db_path='{self.db_path}')"

    def _create_tables(self):
        """
        Create initial tables in the database. Extend as needed.
        """
        cursor = self.connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
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
