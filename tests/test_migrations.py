"""Tests verifying that Alembic migrations run cleanly on a fresh database
and on an upgrade from the pre-snapshots (v1.4.1) schema with real data."""

import contextlib
import hashlib
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
            # Note: import_excluded was dropped by migration 0052; the table
            # built above (current model via baseline create_all) no longer has
            # it, so the INSERT must not reference it.
            conn.execute(
                "INSERT INTO picture "
                "(id, file_path, original_file_name, deleted) "
                "VALUES "
                "(1001, 'a/b/c1.jpg', 'c1.jpg', 0), "
                "(1002, 'a/b/c2.jpg', 'c2.jpg', 0)"
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


def test_alembic_0051_migrates_deleted_file_log_to_path_sha():
    """A deployed DB whose ``deleted_file_log`` still has the old cleartext
    ``file_path`` column is migrated to the opaque ``path_sha``: existing paths
    are hashed (so already-logged deletions stay matchable) and the cleartext
    column is dropped (privacy).

    Fresh DBs build the table from the current model, so only a real deployed
    DB ever has ``file_path`` — reproduced here by hand-building it and stamping
    the alembic version to the revision just before 0051.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "vault.db")
        db_url = f"sqlite:///{db_path}"

        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE deleted_file_log (
                    id INTEGER NOT NULL PRIMARY KEY,
                    file_path VARCHAR NOT NULL,
                    pixel_sha VARCHAR,
                    deleted_at DATETIME NOT NULL
                );
                CREATE INDEX ix_deleted_file_log_file_path
                    ON deleted_file_log (file_path);
                CREATE TABLE alembic_version (version_num VARCHAR NOT NULL);
                INSERT INTO alembic_version (version_num)
                    VALUES ('0050_reset_metadata_hash_membership');
                INSERT INTO deleted_file_log (file_path, pixel_sha, deleted_at)
                    VALUES ('abcd-1234.jpg', 'pix1', '2026-01-01 00:00:00');
                INSERT INTO deleted_file_log (file_path, pixel_sha, deleted_at)
                    VALUES ('/private/ref/secret.png', NULL, '2026-01-02 00:00:00');
                """
            )
            conn.commit()
        finally:
            conn.close()

        result = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert result.returncode == 0, (
            f"upgrade failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        conn = sqlite3.connect(db_path)
        try:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(deleted_file_log)").fetchall()
            }
            assert "path_sha" in cols, "0051 must add path_sha"
            assert "file_path" not in cols, "0051 must drop the cleartext file_path"

            rows = conn.execute(
                "SELECT path_sha, pixel_sha FROM deleted_file_log ORDER BY id"
            ).fetchall()
            assert rows[0][0] == hashlib.sha256(b"abcd-1234.jpg").hexdigest()
            assert rows[0][1] == "pix1", "pixel_sha must be preserved"
            # Sensitive reference-folder path is now a one-way hash, not cleartext.
            assert rows[1][0] == hashlib.sha256(b"/private/ref/secret.png").hexdigest()
            assert rows[1][0] != "/private/ref/secret.png"
        finally:
            conn.close()


def test_alembic_0075_leaves_existing_ground_truth_null():
    """A deployed tag_health cache upgraded across 0075 gets NULL, never 0.

    The board's zero-yield gate treats ``ground_truth === 0 && est_missing === 0``
    as *proof* that a review would yield nothing and disables "Start review". A
    ``server_default="0"`` backfill would make every pre-existing row assert that
    proof it does not have, disabling the button vault-wide until the next
    rebuild. NULL means "not measured", which the gate leaves alone — absence of
    evidence is not evidence of emptiness.

    Only a real deployed DB ever hits this path: a fresh DB builds tag_health
    from the current model, so the column already exists and 0075 is a no-op.
    Reproduced by hand-building the pre-0075 table and stamping 0074.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "vault.db")
        db_url = f"sqlite:///{db_path}"

        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE tag_health (
                    id INTEGER NOT NULL PRIMARY KEY,
                    tag VARCHAR NOT NULL,
                    est_wrong INTEGER NOT NULL,
                    est_missing INTEGER NOT NULL,
                    est_wrong_adj FLOAT,
                    est_missing_adj FLOAT,
                    mismatch INTEGER NOT NULL,
                    verified_pct FLOAT NOT NULL,
                    boundary_pct FLOAT NOT NULL,
                    overturn_rate FLOAT,
                    model_disputes INTEGER NOT NULL,
                    has_model BOOLEAN NOT NULL,
                    last_reviewed_at DATETIME,
                    computed_at DATETIME
                );
                CREATE UNIQUE INDEX ix_tag_health_tag ON tag_health (tag);
                CREATE TABLE alembic_version (version_num VARCHAR NOT NULL);
                INSERT INTO alembic_version (version_num)
                    VALUES ('0074_recompute_tag_health_exclude_human_decisions');
                INSERT INTO tag_health
                    (tag, est_wrong, est_missing, mismatch, verified_pct,
                     boundary_pct, model_disputes, has_model, computed_at)
                    VALUES ('malformed hand', 3, 0, 0, 0.5, 0.1, 0, 1,
                            '2026-07-01 00:00:00');
                """
            )
            conn.commit()
        finally:
            conn.close()

        result = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert result.returncode == 0, (
            f"upgrade failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        conn = sqlite3.connect(db_path)
        try:
            info = {
                r[1]: r
                for r in conn.execute("PRAGMA table_info(tag_health)").fetchall()
            }
            assert "ground_truth" in info, "0075 must add ground_truth"
            # notnull flag (index 3) must be 0 and there must be no default (4).
            assert info["ground_truth"][3] == 0, "ground_truth must be nullable"
            assert info["ground_truth"][4] is None, (
                "ground_truth must have no server default — a 0 default is "
                "indistinguishable from a measured zero"
            )
            row = conn.execute(
                "SELECT ground_truth, est_missing, computed_at FROM tag_health"
            ).fetchone()
            assert row[0] is None, (
                "a pre-0075 row must read NULL, not 0: with est_missing == 0 a "
                "backfilled 0 would disable 'Start review' for this tag"
            )
            assert row[1] == 0
            # …and the cache is marked stale so a rebuild fills in the real count.
            assert row[2].startswith("1970-01-01")
        finally:
            conn.close()


def test_alembic_0087_backfills_entity_project_membership():
    """A deployed DB whose characters / picture sets carry the legacy single
    ``project_id`` FK gains one join row per assignment (issue #125).

    The migration is additive: the scalar FKs must survive untouched, because
    they stay the "primary project" pointer until a post-1.12 cleanup drops them.
    A dangling FK (a project row that no longer exists) must be skipped rather
    than inserted, or the new foreign key would be violated.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "vault.db")
        db_url = f"sqlite:///{db_path}"

        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE project (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description VARCHAR,
                    cover_image_path VARCHAR,
                    extra_metadata VARCHAR,
                    created_at DATETIME NOT NULL
                );
                CREATE TABLE character (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description VARCHAR,
                    extra_metadata VARCHAR,
                    reference_picture_set_id INTEGER,
                    project_id INTEGER
                );
                CREATE TABLE pictureset (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description VARCHAR,
                    project_id INTEGER,
                    set_icon VARCHAR,
                    set_color VARCHAR,
                    locked BOOLEAN NOT NULL DEFAULT 0
                );
                CREATE TABLE alembic_version (version_num VARCHAR NOT NULL);
                INSERT INTO alembic_version (version_num)
                    VALUES ('0085_recompute_smart_score_restored_builtin_anchors');
                INSERT INTO project (id, name, created_at)
                    VALUES (1, 'Alpha', '2026-01-01 00:00:00');
                INSERT INTO project (id, name, created_at)
                    VALUES (2, 'Beta', '2026-01-01 00:00:00');
                INSERT INTO character (id, name, project_id) VALUES (10, 'C1', 1);
                INSERT INTO character (id, name, project_id) VALUES (11, 'C2', 2);
                INSERT INTO character (id, name, project_id) VALUES (12, 'C3', NULL);
                -- Dangling pointer at a project that no longer exists.
                INSERT INTO character (id, name, project_id) VALUES (13, 'C4', 999);
                INSERT INTO pictureset (id, name, project_id) VALUES (20, 'S1', 1);
                INSERT INTO pictureset (id, name, project_id) VALUES (21, 'S2', NULL);
                """
            )
            conn.commit()
        finally:
            conn.close()

        result = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert result.returncode == 0, (
            f"upgrade failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        conn = sqlite3.connect(db_path)
        try:
            char_rows = set(
                conn.execute(
                    "SELECT character_id, project_id FROM characterprojectmember"
                ).fetchall()
            )
            assert char_rows == {(10, 1), (11, 2)}, (
                f"unexpected character membership backfill: {sorted(char_rows)}"
            )

            set_rows = set(
                conn.execute(
                    "SELECT set_id, project_id FROM picturesetprojectmember"
                ).fetchall()
            )
            assert set_rows == {(20, 1)}, (
                f"unexpected set membership backfill: {sorted(set_rows)}"
            )

            # Additive: the legacy pointers are untouched, including the dangling
            # one (the migration never rewrites application data).
            assert conn.execute(
                "SELECT project_id FROM character ORDER BY id"
            ).fetchall() == [(1,), (2,), (None,), (999,)]
            assert conn.execute(
                "SELECT project_id FROM pictureset ORDER BY id"
            ).fetchall() == [(1,), (None,)]
        finally:
            conn.close()
