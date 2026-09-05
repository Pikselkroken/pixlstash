"""Snapshot schema-upgrade and Alembic helpers for the restore package.

Materialises a snapshot archive into a scratch SQLite file and runs
``alembic upgrade head`` on it, plus the revision-comparison helpers used to
decide whether a snapshot needs upgrading before it can be read.
"""

import os
import shutil
import sqlite3
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from pixlstash.database import create_configured_engine
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.pixl_logging import get_logger
from pixlstash.services.portable_identity import sanitize_vault_file
from pixlstash.utils.snapshot_compression import (
    materialize_snapshot,
    snapshot_scratch_dir,
)

logger = get_logger(__name__)


# Alembic's EnvironmentContext is not thread-safe (uses module globals).
# Serialise all snapshot schema upgrades with this lock.
_ALEMBIC_UPGRADE_LOCK = threading.Lock()


def snapshot_engine(db_path: str):
    """Return the engine every restore path uses for a snapshot ``.sqlite``.

    The single entry point for opening a snapshot file, so the busy timeout,
    the page cache and the custom SQL functions match the live vault engine
    (§13) instead of each call site silently inheriting SQLite's defaults
    (issue #709). It differs from the vault engine in exactly one deliberate
    way:

    **``wal=False``.** A snapshot must remain a *self-contained single file*.
    ``journal_mode`` is a persistent property of the database header, so an
    engine that set WAL would rewrite the snapshot's header and start writing
    a ``-wal`` sidecar next to it. Every path that handles a snapshot
    copies, replaces or compresses the **main file by name**
    (``shutil.copy2(upgraded, abs_snapshot)`` in ``_backfill_snapshot``,
    ``compress_snapshot``, ``materialize_snapshot``). Any of those would drop
    the sidecar and silently lose the pages it held. The two
    ``wal_checkpoint(TRUNCATE)`` + ``journal_mode=DELETE`` conversions in this
    package (``_upgrade_snapshot_schema`` below and
    ``preview._fill_snapshot_hashes_at``) exist precisely to force snapshots
    *out* of WAL, and could not reliably do so against a live pooled WAL
    connection, because SQLite refuses to leave WAL while another connection
    has the database open. ``synchronous`` therefore also stays at SQLite's ``FULL``
    default, since ``NORMAL`` is only crash-safe under WAL.

    Foreign-key enforcement is left **on** (the shared default): all but one
    of these engines are read-only, where FK enforcement has no effect at all,
    and the one writer (``_fill_snapshot_hashes_at``) only ever updates
    ``picture.metadata_hash``, which is neither a child key nor a parent key,
    so SQLite runs no FK check for it. Pre-existing violations inside a
    snapshot are never scanned for, so a snapshot restored "as a unit" from an
    intermediate state still opens fine.

    Args:
        db_path: Absolute path to a snapshot ``.sqlite`` file (the archive
            materialised into scratch, or a legacy uncompressed snapshot).

    Returns:
        A configured SQLAlchemy ``Engine``. Callers own ``dispose()``.
    """
    return create_configured_engine(db_path, wal=False)


@lru_cache(maxsize=1)
def _alembic_paths() -> tuple[Path, Path]:
    """Locate the Alembic ini + migrations directory for snapshot upgrades.

    Returns:
        ``(alembic_ini, migrations_dir)`` as absolute paths.

    Raises:
        RuntimeError: If neither the repo-root nor the packaged layout has
            both an ``alembic.ini`` and a ``migrations`` directory.
    """
    module_dir = Path(__file__).resolve().parent.parent
    repo_root = module_dir.parent
    for candidate_ini, candidate_migrations in (
        (repo_root / "alembic.ini", repo_root / "migrations"),
        (module_dir / "alembic.ini", module_dir / "migrations"),
    ):
        if candidate_ini.exists() and candidate_migrations.exists():
            return candidate_ini, candidate_migrations
    raise RuntimeError("Alembic config not found for snapshot upgrade.")


def _alembic_config(sqlalchemy_url: str = "") -> Config:
    """Build an Alembic ``Config`` pointed at this package's migrations.

    Args:
        sqlalchemy_url: Database URL to operate on. Only needed for commands
            that actually connect (e.g. ``upgrade``); revision-graph lookups
            can pass the empty default.

    Returns:
        A configured Alembic ``Config``.
    """
    alembic_ini, migrations_dir = _alembic_paths()
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(migrations_dir))
    if sqlalchemy_url:
        config.set_main_option("sqlalchemy.url", sqlalchemy_url)
    return config


@lru_cache(maxsize=1)
def _alembic_head_revisions() -> frozenset[str]:
    """Return the current head revision(s) of the migration graph.

    Cached: the migration scripts are read-only for the life of the process.

    Returns:
        Frozen set of head revision identifiers (normally exactly one).
    """
    return frozenset(ScriptDirectory.from_config(_alembic_config()).get_heads())


def _snapshot_schema_revision(db_path: str) -> Optional[str]:
    """Return the Alembic revision stamped in the SQLite file at *db_path*.

    Args:
        db_path: Absolute path to a snapshot ``.sqlite`` file.

    Returns:
        The ``alembic_version.version_num`` value, or None when the file has
        no ``alembic_version`` table (a very old snapshot), the table is
        empty, or it could not be read.
    """
    if not os.path.isfile(db_path):
        logger.warning(
            "RestoreService: cannot read schema revision, no such file: %s", db_path
        )
        return None
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # Most commonly "no such table: alembic_version" on a pre-Alembic
        # snapshot; treated as "unknown revision" so the caller upgrades.
        logger.info(
            "RestoreService: no readable alembic_version in snapshot %s (%s); "
            "treating it as out of date.",
            db_path,
            exc,
        )
        return None
    return row[0] if row and row[0] else None


def _snapshot_schema_is_current(db_path: str) -> bool:
    """Return True only if the snapshot's schema is at the migration head.

    This replaces the old "does column X exist?" sniff. Probing a single
    column cannot tell a current snapshot from an intermediate one - a file
    can carry ``metadata_hash`` and still predate ``tags_file`` - and any ORM
    entity load against such a file selects columns that do not exist, which
    fails at query time. Comparing the stamped revision against the head is
    the only check that stays correct as new migrations land.

    Args:
        db_path: Absolute path to a snapshot ``.sqlite`` file.

    Returns:
        True when the stamped revision is one of the current heads. False for
        an unstamped, behind, or unrecognised revision - i.e. fail towards
        "needs upgrading", never towards reading a stale schema.
    """
    revision = _snapshot_schema_revision(db_path)
    if revision is None:
        return False
    try:
        heads = _alembic_head_revisions()
    except Exception as exc:
        logger.error(
            "RestoreService: could not resolve Alembic head revisions while "
            "checking snapshot %s: %s",
            db_path,
            exc,
            exc_info=True,
        )
        return False
    if revision in heads:
        return True
    logger.info(
        "RestoreService: snapshot %s is at schema revision %s, head is %s; "
        "it will be upgraded before use.",
        db_path,
        revision,
        sorted(heads),
    )
    return False


class SchemaUpgradeMixin:
    """Snapshot materialisation + Alembic upgrade behaviour.

    Mixed into :class:`~pixlstash.services.restore.RestoreService`; relies on
    the facade's ``_vault`` and per-path file-lock attributes.
    """

    def _snapshot_file_lock(self, abs_snapshot: str) -> threading.RLock:
        """Return the per-path lock for *abs_snapshot*, creating it if needed.

        Acquire this around any direct read/write of the snapshot file on
        disk to keep compare/preview/restore from racing the in-place
        backfill that ``compare_hashes`` performs on old snapshots.
        Reentrant - safe to nest across helpers in one thread.
        """
        with self._snapshot_file_locks_meta:
            lock = self._snapshot_file_locks.get(abs_snapshot)
            if lock is None:
                lock = threading.RLock()
                self._snapshot_file_locks[abs_snapshot] = lock
            return lock

    def _get_snapshot_or_raise(self, snapshot_id: int) -> Snapshot:
        cp = self._vault.snapshot_service.get_snapshot(snapshot_id)
        if cp is None:
            raise ValueError(f"Snapshot id={snapshot_id} not found.")
        return cp

    def _upgrade_snapshot_schema(self, abs_snapshot: str) -> Optional[str]:
        """Materialize the snapshot to a temp file and alembic-upgrade it.

        Decompresses the zstd archive (or copies a legacy plain ``.sqlite``)
        into a scratch ``.sqlite`` and runs ``alembic upgrade head`` on it.
        The read of the original file is held under the per-path file lock
        (reentrant) so it cannot race with a concurrent in-place backfill of a
        legacy snapshot.

        Args:
            abs_snapshot: Absolute path to the read-only snapshot archive
                (``.sqlite.zst`` for new snapshots, ``.sqlite`` for legacy).

        Returns:
            Path to the upgraded temp file, or None if the upgrade failed.
        """
        tmp_dir = tempfile.mkdtemp(
            prefix="pixlstash_restore_",
            dir=snapshot_scratch_dir(self._vault.image_root),
        )
        tmp_snapshot = os.path.join(tmp_dir, "snapshot.sqlite")
        try:
            with self._snapshot_file_lock(abs_snapshot):
                materialize_snapshot(abs_snapshot, tmp_snapshot)
        except Exception as exc:
            logger.error(
                "RestoreService: failed to materialize snapshot to temp dir: %s",
                exc,
                exc_info=True,
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

        try:
            config = _alembic_config(f"sqlite:///{tmp_snapshot}")
            with _ALEMBIC_UPGRADE_LOCK:
                command.upgrade(config, "head")
            # The upgraded scratch is the only file eligible for a live swap.
            # Scrub portable identity, checkpoint/delete its journals, VACUUM,
            # and verify it before any restore/preview path can consume it.
            sanitize_vault_file(tmp_snapshot)
            logger.info(
                "RestoreService: snapshot schema upgraded to head at %s",
                tmp_snapshot,
            )
            return tmp_snapshot
        except Exception as exc:
            logger.error(
                "RestoreService: snapshot schema upgrade failed: %s",
                exc,
                exc_info=True,
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None
