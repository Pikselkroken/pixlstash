from __future__ import annotations

import logging
import os
import sys
from collections import Counter
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

logger = logging.getLogger("alembic.env")


def _get_database_url() -> str:
    env_url = os.getenv("PIXLSTASH_DB_URL")
    if env_url:
        return env_url
    return config.get_main_option("sqlalchemy.url")


def _database_is_at_script_head() -> bool:
    """True when the database already holds every revision in the tree.

    ``command.upgrade(config, "head")`` runs on every vault open, so the
    integrity scans below would otherwise cost two whole-database
    ``foreign_key_check`` passes on every start. Nothing can run, so nothing
    can be broken: skip them.

    A ``downgrade`` also starts from head and so skips the scans. That is
    deliberate - downgrades only ever run through the standalone Alembic CLI
    (``tests/test_migrations.py``), never against a live vault. The
    suspension itself is not skipped, so the rebuild still works.

    Must be called after ``context.configure()``.
    """
    from alembic.script import ScriptDirectory

    script_heads = set(ScriptDirectory.from_config(config).get_heads())
    return set(context.get_context().get_current_heads()) == script_heads


def _foreign_key_violations(connection) -> Counter:
    """Count dangling foreign keys per (child table, parent table, FK id).

    ``PRAGMA foreign_key_check`` reports one row per violation as
    ``(table, rowid, parent, fkid)``. The rowid is dropped: a batch rebuild
    renumbers rowids of any table without an ``INTEGER PRIMARY KEY``, so the
    same pre-existing orphan would otherwise look like a new one.
    """
    rows = connection.execute(text("PRAGMA foreign_key_check")).all()
    return Counter((row[0], row[2], row[3]) for row in rows)


@contextmanager
def _foreign_keys_suspended(connection):
    """Suspend SQLite FK enforcement for the duration of a migration run.

    ``op.batch_alter_table`` is the only way to drop or alter a column on
    SQLite: it builds a new table, copies the rows across, ``DROP``s the
    original and renames. With ``PRAGMA foreign_keys=ON`` - which the vault
    engine sets (``database.init_database``) - that ``DROP`` raises
    "FOREIGN KEY constraint failed" the moment any row references the table
    being rebuilt, so migration 0080's rebuild of ``picture`` fails on every
    database that actually holds pictures (the committed e2e fixture, and any
    real library upgrading from before 0080). A fresh database has no rows to
    reference, which is why the test suite never saw it.

    Suspending enforcement around the rebuild is SQLite's own documented
    procedure for altering a table (sqlite.org/lang_altertable.html, steps 1
    and 11), not a workaround. Because it is genuinely unsafe to leave a
    migration's output unchecked, ``PRAGMA foreign_key_check`` runs before and
    after, and the run is refused if *it* broke referential integrity.

    Refused, not merely reported, on the path that matters: a vault opens
    Alembic on an already-open connection, so Alembic treats the transaction
    as external and leaves the commit to ``database._run_migrations``. The
    check therefore runs while the whole run is still uncommitted - SQLite's
    procedure checks at step 10, before the commit at step 11 - and the
    rollback below discards every migration in the run. The standalone engine
    has no external transaction, so SQLite's non-transactional DDL commits
    each migration as it goes and the guard can only refuse the result; its
    caller is the snapshot upgrader, which throws its scratch file away when
    the upgrade fails.

    Only violations the run *introduced* are refused. A database that already
    holds orphan rows (written before its FKs existed, or by any path with
    enforcement off) is logged and left alone - failing on those would brick
    an existing library on every open, with no way back.

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

    # Deliberately not gated on ``was_enabled``: the standalone engine leaves
    # SQLite's default (off), and that is the snapshot-restore path - the one
    # migration input that did not come from this process.
    check = not _database_is_at_script_head()
    pre_existing = _foreign_key_violations(connection) if check else Counter()
    if pre_existing:
        logger.warning(
            "Database holds %d dangling foreign key(s) before migrating: %s. "
            "These predate this run and are left untouched.",
            sum(pre_existing.values()),
            dict(pre_existing),
        )
    connection.commit()

    completed = False
    try:
        yield
        if check:
            introduced = _foreign_key_violations(connection) - pre_existing
            if introduced:
                raise RuntimeError(
                    "Migrations introduced dangling foreign keys "
                    f"({sum(introduced.values())} rows): {dict(introduced)}"
                )
        completed = True
    finally:
        if completed:
            connection.commit()
        else:
            connection.rollback()
        if was_enabled:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.commit()


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
