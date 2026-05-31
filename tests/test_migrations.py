"""Tests verifying that Alembic migrations run cleanly on a fresh database
and on an upgrade from the pre-snapshots (v1.4.1) schema with real data."""

import contextlib
import os
import sqlite3
import subprocess
import sys
import tempfile


_MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "pixlstash")
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_alembic(args, db_url, cwd):
    env = {**os.environ, "PIXLSTASH_DB_URL": db_url, "PYTHONPATH": _PROJECT_ROOT}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini"] + args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    return result


def test_alembic_upgrade_head_fresh_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        result = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert os.path.isfile(db_path), "Database file was not created"


def test_alembic_downgrade_one_step():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        up = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert up.returncode == 0, (
            f"alembic upgrade head failed:\nstdout: {up.stdout}\nstderr: {up.stderr}"
        )

        down = _run_alembic(["downgrade", "-1"], db_url, _MIGRATIONS_DIR)
        assert down.returncode == 0, (
            f"alembic downgrade -1 failed:\nstdout: {down.stdout}\nstderr: {down.stderr}"
        )


def test_alembic_upgrade_from_v1_4_1_preserves_data():
    """Simulate a real v1.4.1 install (alembic head = 0048, no snapshot
    infrastructure) populated with picture rows, then upgrade to head and
    verify the 0049 snapshots migration:

    1. preserves existing picture rows (no data loss);
    2. adds the ``metadata_hash`` column to ``picture`` (NULLable, NULL on
       existing rows so the post-flush hook regenerates them);
    3. creates the ``changelog`` and ``snapshot`` tables.

    Regression test: prior to this test, only fresh-DB upgrades were
    exercised, so a future migration that broke pre-existing rows could
    land green. Every user upgrading from 1.4.1 runs these migrations
    against a non-fresh DB.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        # Step 1 — apply every migration, then strip the snapshot
        # infrastructure to make the DB look like a real v1.4.1 install
        # (baseline migration uses SQLModel.metadata.create_all(), so a
        # plain ``upgrade 0048`` would still create snapshot/changelog/
        # metadata_hash from the *current* model graph — defeating the
        # point of the test).
        up_to_head = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert up_to_head.returncode == 0, (
            f"initial upgrade failed:\nstdout: {up_to_head.stdout}\nstderr: {up_to_head.stderr}"
        )

        # contextlib.closing: sqlite3's own context manager commits/rolls
        # back but does NOT close the connection, leaving the DB file locked
        # so the TemporaryDirectory cleanup fails on Windows (WinError 32).
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute("DROP TABLE IF EXISTS changelog")
            conn.execute("DROP TABLE IF EXISTS snapshot")
            conn.execute("ALTER TABLE picture DROP COLUMN metadata_hash")
            conn.execute(
                "UPDATE alembic_version SET version_num = '0048_normalize_stack_positions'"
            )
            conn.commit()

            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "picture" in tables, "picture table must exist at 0048"
            assert "snapshot" not in tables, "snapshot must NOT exist at 0048"
            picture_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(picture)").fetchall()
            }
            assert "metadata_hash" not in picture_cols, (
                "metadata_hash must NOT exist at 0048"
            )

            # Step 2 — insert a couple of real picture rows (fill the
            # NOT NULL columns the schema requires).
            conn.execute(
                "INSERT INTO picture "
                "(id, file_path, original_file_name, deleted, import_excluded) "
                "VALUES "
                "(1001, 'a/b/c1.jpg', 'c1.jpg', 0, 0), "
                "(1002, 'a/b/c2.jpg', 'c2.jpg', 0, 0)"
            )
            conn.commit()

        # Step 3 — upgrade to head; 0049 must run against the existing DB.
        up_to_head = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert up_to_head.returncode == 0, (
            f"alembic upgrade head from 0048 failed:\n"
            f"stdout: {up_to_head.stdout}\nstderr: {up_to_head.stderr}"
        )

        # Step 4 — verify data preservation and new schema.
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            rows = list(
                conn.execute("SELECT id, original_file_name FROM picture ORDER BY id")
            )
            assert rows == [(1001, "c1.jpg"), (1002, "c2.jpg")], (
                f"Pre-migration picture rows were lost or mutated: {rows}"
            )

            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "snapshot" in tables, "0049 must create snapshot table"
            assert "changelog" not in tables, (
                "0049 must NOT create the now-orphaned changelog table"
            )

            picture_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(picture)").fetchall()
            }
            assert "metadata_hash" in picture_cols, (
                "0049 must add metadata_hash column to picture"
            )

            # metadata_hash should be NULL on pre-existing rows so the
            # post-flush hash hook (in database.py) regenerates them on the
            # next ORM-level update.
            hashes = list(
                conn.execute("SELECT id, metadata_hash FROM picture ORDER BY id")
            )
            assert all(h is None for _, h in hashes), (
                f"metadata_hash must be NULL on pre-existing rows: {hashes}"
            )
