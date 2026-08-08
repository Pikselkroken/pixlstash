from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlmodel import SQLModel

# Import models to register SQLModel metadata
from pixlstash import db_models  # noqa: F401

# Alembic Config object
config = context.config

# Configure Python path to import local modules
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        fileConfig(config.config_file_name)

# Target metadata for 'autogenerate'
target_metadata = SQLModel.metadata


def _get_database_url() -> str:
    env_url = os.getenv("PIXLSTASH_DB_URL")
    if env_url:
        return env_url
    return config.get_main_option("sqlalchemy.url")


@contextmanager
def _foreign_keys_suspended(connection):
    """Suspend SQLite FK enforcement for the duration of a migration run.

    ``op.batch_alter_table`` is the only way to drop or alter a column on
    SQLite: it builds a new table, copies the rows across, ``DROP``s the
    original and renames. With ``PRAGMA foreign_keys=ON`` — which the vault
    engine sets (``database.init_database``) — that ``DROP`` raises
    "FOREIGN KEY constraint failed" the moment any row references the table
    being rebuilt, so migration 0080's rebuild of ``picture`` fails on every
    database that actually holds pictures (the committed e2e fixture, and any
    real library upgrading from before 0080). A fresh database has no rows to
    reference, which is why the test suite never saw it.

    Suspending enforcement around the rebuild is SQLite's own documented
    procedure for altering a table (sqlite.org/lang_altertable.html, steps 1
    and 11), not a workaround. Because it is genuinely unsafe to leave a
    migration's output unchecked, ``PRAGMA foreign_key_check`` runs afterwards
    and refuses to let a run that broke referential integrity pass silently.

    The PRAGMA has to be issued outside a transaction to take effect, hence the
    commit on the way in; pysqlite does not emit ``BEGIN`` for DDL or PRAGMA,
    so this is the last statement before Alembic opens its own transaction.
    """
    if connection.dialect.name != "sqlite":
        yield
        return

    connection.commit()
    was_enabled = bool(connection.execute(text("PRAGMA foreign_keys")).scalar())
    connection.execute(text("PRAGMA foreign_keys=OFF"))
    connection.commit()
    try:
        yield
    finally:
        connection.commit()
        if was_enabled:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.commit()
            violations = connection.execute(text("PRAGMA foreign_key_check")).all()
            if violations:
                raise RuntimeError(
                    "Migrations left dangling foreign keys "
                    f"({len(violations)} rows); first: {violations[0]}"
                )


def run_migrations_offline() -> None:
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        context.configure(
            connection=supplied_connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with _foreign_keys_suspended(supplied_connection):
            with context.begin_transaction():
                context.run_migrations()
        return

    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = _get_database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )

        with _foreign_keys_suspended(connection):
            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
