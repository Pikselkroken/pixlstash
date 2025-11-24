import dataclasses

from .character import CharacterModel
from .picture import PictureModel, PictureTagModel
import os
import sqlite3
import threading
from typing import Optional

from .logging import get_logger
from .picture_character import PictureCharacterModel
from .picture_set import PictureSetModel, PictureSetMemberModel
from .picture_likeness import PictureLikenessModel
from .vault_upgrade import VaultUpgrade

import queue
from concurrent.futures import Future
from enum import IntEnum


# Priority enum for DB operations
class DBPriority(IntEnum):
    LOW = 30
    MEDIUM = 20
    HIGH = 10
    IMMEDIATE = 0


# Database task for the queue
class DatabaseTask:
    def __init__(self, priority, func, args=(), kwargs=None):
        self.priority = priority
        self.func = func
        self.args = args
        self.kwargs = kwargs or {}
        self.future = Future()

    def __lt__(self, other):
        return self.priority < other.priority


logger = get_logger(__name__)


def _assert_no_bytes(params):
    if isinstance(params, dict):
        for v in params.values():
            assert not isinstance(v, bytes), (
                f"Attempted to insert raw bytes into DB: {v!r}"
            )
    elif isinstance(params, (list, tuple)):
        for item in params:
            if isinstance(item, (list, tuple, dict)):
                _assert_no_bytes(item)
            else:
                assert not isinstance(item, bytes), (
                    f"Attempted to insert raw bytes into DB: {item!r}"
                )
    else:
        assert not isinstance(params, bytes), (
            f"Attempted to insert raw bytes into DB: {params!r}"
        )


class VaultDatabase:
    """
    Centralized database access for Pixelurgy Vault.
    All direct SQLite operations should be performed here.
    """

    def __init__(self, db_path: str, description: Optional[str] = None):
        self._db_path = db_path
        db_exists = os.path.exists(self._db_path)
        logger.info(f"Vault init, db_path={self._db_path}, db_exists={db_exists}")

        models = [
            CharacterModel,
            PictureModel,
            PictureTagModel,
            PictureCharacterModel,
            PictureSetModel,
            PictureSetMemberModel,
            PictureLikenessModel,
        ]
        if not db_exists:
            with sqlite3.connect(self._db_path, check_same_thread=False) as conn:
                logger.info("Creating tables and importing default data...")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    );
                    """
                )
                for model in models:
                    sql = self._create_table_sql(model)
                    logger.info(
                        f"CREATE TABLE SQL for {getattr(model, '__tablename__', model.__name__)}: {sql}"
                    )
                    conn.execute(sql)
                    self._create_indexes_for_model(model, conn)
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS likeness_work_queue (
                        picture_id_a TEXT NOT NULL,
                        picture_id_b TEXT NOT NULL,
                        UNIQUE (picture_id_a, picture_id_b)
                    );
                    """
                )
                conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lwq_pair ON likeness_work_queue(picture_id_a, picture_id_b)
            """)
                conn.commit()
        else:
            logger.debug("Using existing database, skipping default import.")
            with sqlite3.connect(self._db_path, check_same_thread=False) as conn:
                upgrader = VaultUpgrade(conn)
                upgrader.upgrade_if_necessary()
                for model in models:
                    self._create_indexes_for_model(model, conn)

        # Write queue and worker
        self._write_queue = queue.PriorityQueue()
        self._write_worker = threading.Thread(
            target=self._write_worker_loop, daemon=True
        )
        self._write_worker.start()

        if description is not None:
            self.set_metadata("description", description)

    # --- Queued API ---
    def submit_task(self, func, *args, priority=DBPriority.MEDIUM, **kwargs):
        """
        Submit a database operation (INSERT/UPDATE/DELETE) to be executed serially.
        Returns a Future you can .result(timeout) on.

        Examples:

        # Using a lambda for a simple write
        future = db.submit_task(lambda conn: conn.execute(
            "UPDATE pictures SET quality = ? WHERE id = ?", (0.95, "pic123")
        ))
        result = future.result()

        # Using a full function for more complex logic
        def update_picture_quality(conn, pic_id, new_quality):
            sql = "UPDATE pictures SET quality = ? WHERE id = ?"
            return conn.execute(sql, (new_quality, pic_id))

        future = db.submit_task(update_picture_quality, "pic123", 0.95)
        result = future.result()
        """
        task = DatabaseTask(priority, func, args, kwargs)
        self._write_queue.put(task)
        return task.future

    # --- Read API ---
    def execute_read(self, func, *args, **kwargs):
        """
        Execute a read operation (SELECT) directly, using a new connection.
        Raises if func is a write operation.

        Examples:

        # Using a lambda for a simple query
        rows = db.execute_read(lambda conn: conn.execute(
            "SELECT * FROM pictures WHERE quality > ?", (0.8,)
        ).fetchall())

        # Using a full function for more complex logic
        def get_high_quality_pictures(conn, min_quality):
            sql = "SELECT * FROM pictures WHERE quality > ?"
            return conn.execute(sql, (min_quality,)).fetchall()

        rows = db.execute_read(get_high_quality_pictures, 0.8)
        """
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            # Guard: check if func is a write operation by inspecting its name or SQL
            # If func is a function, check its name
            if hasattr(func, "__name__") and func.__name__.lower().startswith(
                ("insert", "update", "delete", "write", "commit")
            ):
                raise RuntimeError(
                    f"execute_read called with write operation: {func.__name__}"
                )
            # If func is a string (SQL), check for write keywords
            if isinstance(func, str) and func.strip().lower().startswith(
                ("insert", "update", "delete", "create", "drop", "alter", "replace")
            ):
                raise RuntimeError(f"execute_read called with write SQL: {func}")
            return func(conn, *args, **kwargs)
        finally:
            conn.close()

    def bulk_read(self, sql: str, params: tuple = ()):
        """Perform a bulk read operation using execute_read."""

        def op(conn, sql, params):
            return conn.execute(sql, params).fetchall()

        return self.execute_read(op, sql, params)

    def set_metadata(self, key: str, value: str):
        self.submit_task(
            lambda conn: conn.execute(
                """
            INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)
            """,
                (key, value),
            )
        ).result()

    def get_metadata(self, key: str) -> Optional[str]:
        row = self.execute_read(
            lambda conn: conn.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
        )
        return row["value"] if row else None

    def get_description(self) -> Optional[str]:
        return self.get_metadata("description")

    @staticmethod
    def _python_type_to_sql(py_type):
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
    def _create_table_sql(cls, model_cls):
        table_name = getattr(model_cls, "__tablename__", model_cls.__name__.lower())
        fields = []
        primary_keys = []
        composite_keys = []
        foreign_keys = []
        for f in dataclasses.fields(model_cls):
            sql_type = cls._python_type_to_sql(f.type)
            col_def = f"{f.name} {sql_type}"
            meta = f.metadata if hasattr(f, "metadata") else {}
            if meta.get("db_ignore", False):
                continue
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

    @classmethod
    def _get_index_definitions(cls, model_cls):
        table_name = getattr(model_cls, "__tablename__", model_cls.__name__.lower())
        indexes = []
        for f in dataclasses.fields(model_cls):
            meta = f.metadata if hasattr(f, "metadata") else {}
            if not meta:
                continue
            index_spec = meta.get("index")
            if index_spec:
                if isinstance(index_spec, str):
                    index_name = index_spec
                else:
                    index_name = f"idx_{table_name}_{f.name}"
                unique = bool(meta.get("unique_index", False))
                where_clause = meta.get("index_where")
                indexes.append(
                    {
                        "name": index_name,
                        "fields": [f.name],
                        "unique": unique,
                        "where": where_clause,
                    }
                )

        composite_indexes = getattr(model_cls, "__indexes__", [])
        for idx in composite_indexes:
            if not idx:
                continue
            fields = idx.get("fields") or []
            if not fields:
                continue
            name = idx.get("name") or f"idx_{table_name}_{'_'.join(fields)}"
            unique = bool(idx.get("unique", False))
            where_clause = idx.get("where")
            indexes.append(
                {
                    "name": name,
                    "fields": fields,
                    "unique": unique,
                    "where": where_clause,
                }
            )
        return indexes

    def _create_indexes_for_model(self, model_cls, conn):
        indexes = self._get_index_definitions(model_cls)
        if not indexes:
            return
        table_name = getattr(model_cls, "__tablename__", model_cls.__name__.lower())
        for idx in indexes:
            fields_sql = ", ".join(idx["fields"])
            unique = "UNIQUE " if idx.get("unique") else ""
            sql = f"CREATE {unique}INDEX IF NOT EXISTS {idx['name']} ON {table_name} ({fields_sql})"
            if idx.get("where"):
                sql += f" WHERE {idx['where']}"
            logger.debug(f"Ensuring index with SQL: {sql}")
            conn.execute(sql)
        conn.commit()

    def _write_worker_loop(self):
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        while True:
            task = self._write_queue.get()
            try:
                result = task.func(conn, *task.args, **task.kwargs)
                conn.commit()
                task.future.set_result(result)
            except Exception as e:
                conn.rollback()
                task.future.set_exception(e)
