import dataclasses

from .character import CharacterModel
from .picture import PictureModel, PictureTagModel
import contextlib
import os
import sqlite3
import threading
from typing import Optional, List

from .character import Character
from .logging import get_logger
from .picture import Picture
from .vault_upgrade import VaultUpgrade

logger = get_logger(__name__)


class VaultDatabase:
    """
    Centralized database access for Pixelurgy Vault.
    All direct SQLite operations should be performed here.
    """

    def __init__(self, db_path: str, description: Optional[str] = None):
        self._db_path = db_path

        db_exists = os.path.exists(self._db_path)
        logger.info(f"Vault init, db_path={self._db_path}, db_exists={db_exists}")

        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

        self._ensure_connection()

        if not db_exists:
            logger.debug("Creating tables and importing default data...")
            # Create tables from dataclasses
            self._ensure_connection()
            for model in [CharacterModel, PictureModel, PictureTagModel]:
                sql = self.create_table_sql(model)
                self._conn.execute(sql)
            self._conn.commit()
        else:
            logger.debug("Using existing database, skipping default import.")
            upgrader = VaultUpgrade(self._conn)
            upgrader.upgrade_if_necessary()

        if description is not None:
            self.set_metadata("description", description)

    @staticmethod
    def python_type_to_sql(py_type):
        # Handle Optional and List types
        origin = getattr(py_type, "__origin__", None)
        if origin is list:
            return "TEXT"  # Store lists as JSON strings
        if origin is not None and hasattr(py_type, "__args__"):
            py_type = py_type.__args__[0]
        if py_type in (int,):
            return "INTEGER"
        if py_type in (float,):
            return "REAL"
        if py_type in (str,):
            return "TEXT"
        if py_type in (bytes,):
            return "BLOB"
        return "TEXT"  # fallback

    @classmethod
    def create_table_sql(cls, model_cls):
        table_name = getattr(model_cls, "__tablename__", model_cls.__name__.lower())
        fields = []
        primary_keys = []
        composite_keys = []
        foreign_keys = []
        for f in dataclasses.fields(model_cls):
            sql_type = cls.python_type_to_sql(f.type)
            col_def = f"{f.name} {sql_type}"
            meta = f.metadata if hasattr(f, "metadata") else {}
            if meta.get("primary_key"):
                primary_keys.append(f.name)
            if meta.get("composite_key"):
                composite_keys.append(f.name)
            if meta.get("foreign_key"):
                foreign_keys.append((f.name, meta["foreign_key"]))
            fields.append(col_def)
        constraints = []
        # Add primary key constraint
        if composite_keys:
            constraints.append(f"PRIMARY KEY ({', '.join(composite_keys)})")
        elif primary_keys:
            constraints.append(f"PRIMARY KEY ({', '.join(primary_keys)})")
        # Add foreign key constraints
        for col, ref in foreign_keys:
            constraints.append(f"FOREIGN KEY ({col}) REFERENCES {ref}")
        all_defs = fields + constraints
        fields_sql = ", ".join(all_defs)
        return f"CREATE TABLE IF NOT EXISTS {table_name} ({fields_sql});"

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    @property
    def threaded_connection(self):
        """
        Context manager for a new SQLite connection for use in a thread.
        Handles PRAGMA journal_mode=WAL and retries on failure.
        Usage:
            with self.threaded_connection as conn:
                ...
        """
        import time

        db_path = self._db_path
        max_retries = 5
        delay = 0.2

        @contextlib.contextmanager
        def _conn_ctx():
            last_exception = None
            for attempt in range(max_retries):
                try:
                    conn = sqlite3.connect(db_path, check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    try:
                        conn.execute("PRAGMA journal_mode=WAL;")
                        yield conn
                        return
                    finally:
                        conn.close()
                except sqlite3.OperationalError as e:
                    last_exception = e
                    if "database is locked" in str(e) and attempt < max_retries - 1:
                        time.sleep(delay)
                        continue
                    else:
                        break
            raise (
                last_exception
                if last_exception
                else RuntimeError("Failed to open SQLite connection after retries.")
            )

        return _conn_ctx()

    def set_metadata(self, key: str, value: str):
        self._execute(
            """
            INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)
            """,
            (key, value),
            commit=True,
        )

    def get_metadata(self, key: str) -> Optional[str]:
        row = self._execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def get_description(self) -> Optional[str]:
        return self.get_metadata("description")

    def _ensure_connection(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    def _execute(
        self, sql: str, params: tuple = (), commit: bool = False
    ) -> sqlite3.Cursor:
        with self._lock:
            self._ensure_connection()
            curs = self._conn.execute(sql, params)
            if commit:
                self._conn.commit()
            return curs

    def _executemany(
        self, sql: str, seq_of_params: list, commit: bool = False
    ) -> sqlite3.Cursor:
        with self._lock:
            self._ensure_connection()
            curs = self._conn.executemany(sql, seq_of_params)
            if commit:
                self._conn.commit()
            return curs

    def _query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            self._ensure_connection()
            curs = self._conn.execute(sql, params)
            return curs.fetchall()

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
