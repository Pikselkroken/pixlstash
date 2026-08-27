"""Tests verifying that Alembic migrations run cleanly on a fresh database
and on an upgrade from the pre-snapshots (v1.4.1) schema with real data."""

import contextlib
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from sqlmodel import text


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


def test_alembic_downgrade_and_reupgrade_multi_library_lane():
    """The multi-library lane unwinds off develop's head and comes back cleanly.

    The lane is linear on top of ``0096_add_picture_is_video``, so downgrading
    to that revision unwinds every migration above it at once. Round-tripping
    keeps each ``downgrade()`` honest, and asserting a single stamped revision
    in both directions catches a lane that silently re-merges into more than
    one head.

    The head is read back from the tree rather than named, so adding a
    migration does not break this test.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        up = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert up.returncode == 0, (
            f"alembic upgrade head failed:\nstdout: {up.stdout}\nstderr: {up.stderr}"
        )

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            head = {
                row[0]
                for row in conn.execute("SELECT version_num FROM alembic_version")
            }
        assert len(head) == 1, f"the lane must resolve to exactly one head, got {head}"

        down = _run_alembic(
            ["downgrade", "0096_add_picture_is_video"],
            db_url,
            _MIGRATIONS_DIR,
        )
        assert down.returncode == 0, (
            "alembic downgrade below the multi-library lane failed:\n"
            f"stdout: {down.stdout}\nstderr: {down.stderr}"
        )

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            revisions = {
                row[0]
                for row in conn.execute("SELECT version_num FROM alembic_version")
            }
        assert revisions == {"0096_add_picture_is_video"}

        reup = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert reup.returncode == 0, (
            f"alembic re-upgrade failed:\nstdout: {reup.stdout}\nstderr: {reup.stderr}"
        )

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            revisions = {
                row[0]
                for row in conn.execute("SELECT version_num FROM alembic_version")
            }
        assert revisions == head


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

        # Step 1 - apply every migration, then strip the snapshot
        # infrastructure to make the DB look like a real v1.4.1 install
        # (baseline migration uses SQLModel.metadata.create_all(), so a
        # plain ``upgrade 0048`` would still create snapshot/changelog/
        # metadata_hash from the *current* model graph - defeating the
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

            # Step 2 - insert a couple of real picture rows (fill the
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

        # Step 3 - upgrade to head; 0049 must run against the existing DB.
        up_to_head = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert up_to_head.returncode == 0, (
            f"alembic upgrade head from 0048 failed:\n"
            f"stdout: {up_to_head.stdout}\nstderr: {up_to_head.stderr}"
        )

        # Step 4 - verify data preservation and new schema.
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
    DB ever has ``file_path`` - reproduced here by hand-building it and stamping
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
    rebuild. NULL means "not measured", which the gate leaves alone - absence of
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
                "ground_truth must have no server default - a 0 default is "
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


# ---------------------------------------------------------------------------
# 0086_reissue_api_tokens: API tokens, guest rows and stored addresses cleared
#
# The migration shipped in v1.8.1 and was spliced into the 1.9 chain ahead of
# ``0086_add_operation_log`` when v1.8.1 was merged in (see §12 of
# docs/backend_architecture.md). It keeps its identifier and its parent because
# released v1.8.1 databases are stamped with exactly that id.
# ---------------------------------------------------------------------------

_REVISION_BEFORE_TOKEN_RESET = "0085_recompute_smart_score_restored_builtin_anchors"
# The revision a 1.9 development install predating the splice is stamped at.
# Alembic walks forward only, so such a database is already downstream of the
# spliced-in reissue migration and will never run it - hence the stamp-back
# recovery route exercised below.
_REVISION_AFTER_THE_SPLICE = "0089_add_dedupverdict_reopen_batch_id"


def _insert_minimal_row(conn, table, **overrides):
    """Insert one row into *table*, filling every NOT NULL column it declares.

    Driven off ``PRAGMA table_info`` rather than a hand-written column list, so
    it does not go stale the next time a NOT NULL column is added.
    """
    columns, values = [], []
    for _cid, name, col_type, notnull, default, pk in conn.execute(
        f"PRAGMA table_info({table})"
    ):
        if name in overrides:
            columns.append(name)
            values.append(overrides[name])
        elif notnull and not pk and default is None:
            columns.append(name)
            values.append(0 if "INT" in col_type.upper() else "")
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", values
    )


def _seed_pre_reset_rows(db_path):
    """Populate a 0085 database with tokens, guest rows and stored addresses."""
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        row = conn.execute("SELECT id FROM user ORDER BY id LIMIT 1").fetchone()
        if row is None:
            _insert_minimal_row(conn, "user", id=1)
            row = (1,)
        conn.execute(
            "UPDATE user SET public_url = ?, comfyui_url = ? WHERE id = ?",
            ("https://example.invalid", "http://example.invalid:8188", row[0]),
        )
        _insert_minimal_row(
            conn,
            "usertoken",
            id=1,
            user_id=row[0],
            token_hash="hash-1",
            token_prefix="prefix1",
            scope="ALL",
            created_at="2026-01-01 00:00:00",
        )
        _insert_minimal_row(conn, "picture", id=1, file_path="seed.jpg")
        guest_session_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(guest_session)")
        }
        token_column = (
            "token_id" if "token_id" in guest_session_columns else "token_public_id"
        )
        token_value = 1 if token_column == "token_id" else "seed-public-id"
        conn.execute(
            f"INSERT INTO guest_session (session_id, {token_column}, created_at, "
            "last_active_at, cookie_token) VALUES (?, ?, ?, ?, ?)",
            (
                "sess-1",
                token_value,
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
                "ck-1",
            ),
        )
        conn.execute(
            f"INSERT INTO guest_score (id, session_id, {token_column}, picture_id, "
            "score, scored_at) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "sess-1", token_value, 1, 4, "2026-01-01 00:00:00"),
        )
        conn.commit()


def _counts_and_addresses(db_path):
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return {
            "usertoken": conn.execute("SELECT COUNT(*) FROM usertoken").fetchone()[0],
            "guest_session": conn.execute(
                "SELECT COUNT(*) FROM guest_session"
            ).fetchone()[0],
            "guest_score": conn.execute("SELECT COUNT(*) FROM guest_score").fetchone()[
                0
            ],
            "addresses": conn.execute(
                "SELECT public_url, comfyui_url FROM user ORDER BY id LIMIT 1"
            ).fetchone(),
            "pictures": conn.execute("SELECT COUNT(*) FROM picture").fetchone()[0],
        }


def test_token_reset_clears_tokens_guest_rows_and_stored_addresses():
    """Upgrading past 0086 empties the token and guest tables and the addresses.

    The guest rows are the point of the child-first order: their foreign keys
    declare a cascade, but it does not run on the migration's connection, and a
    reused integer primary key would otherwise re-attach them to whichever
    token is created next.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        stepped = _run_alembic(
            ["upgrade", _REVISION_BEFORE_TOKEN_RESET], db_url, _MIGRATIONS_DIR
        )
        assert stepped.returncode == 0, (
            f"upgrade to {_REVISION_BEFORE_TOKEN_RESET} failed:\n"
            f"stdout: {stepped.stdout}\nstderr: {stepped.stderr}"
        )
        _seed_pre_reset_rows(db_path)

        before = _counts_and_addresses(db_path)
        assert before["usertoken"] == 1
        assert before["guest_session"] == 1
        assert before["guest_score"] == 1
        assert before["addresses"] == (
            "https://example.invalid",
            "http://example.invalid:8188",
        )

        result = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        after = _counts_and_addresses(db_path)
        assert after["usertoken"] == 0, "tokens must be cleared"
        assert after["guest_session"] == 0, "guest sessions must be cleared"
        assert after["guest_score"] == 0, "guest scores must be cleared"
        assert after["addresses"] == (None, None), "stored addresses must be cleared"
        # Nothing beyond those is touched.
        assert after["pictures"] == before["pictures"] == 1


def test_token_reset_runs_on_already_empty_tables():
    """The clear is a no-op on a database with nothing to clear.

    Covers both the empty-table case and re-running the step after a
    downgrade, which is the only way the migration executes twice.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        first = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert first.returncode == 0, (
            f"first upgrade failed:\nstdout: {first.stdout}\nstderr: {first.stderr}"
        )
        down = _run_alembic(
            ["downgrade", _REVISION_BEFORE_TOKEN_RESET], db_url, _MIGRATIONS_DIR
        )
        assert down.returncode == 0, (
            f"downgrade failed:\nstdout: {down.stdout}\nstderr: {down.stderr}"
        )
        second = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert second.returncode == 0, (
            f"re-upgrade failed:\nstdout: {second.stdout}\nstderr: {second.stderr}"
        )

        after = _counts_and_addresses(db_path)
        assert after["usertoken"] == 0
        assert after["guest_session"] == 0
        assert after["guest_score"] == 0


def test_a_pre_splice_dev_database_is_recovered_by_stamping_back_to_0085():
    """The stamp-back recovery route for a 1.9-dev database past the splice.

    Alembic walks forward only. A 1.9 development install took the pre-merge
    path ``0085 -> 0086_add_operation_log -> 0087 -> 0088 -> 0089``, so it is
    already downstream of ``0086_reissue_api_tokens`` and upgrading will never
    run it - the tokens would simply stay. The chain does not carry a second
    reissue migration for this; the fix is operational: stamp the database back
    to 0085 and upgrade.

    That only works because ``0086_add_operation_log`` through ``0089`` are all
    guarded (they inspect existing tables / columns / indexes before creating
    anything), so a second pass over them is a no-op rather than an error. This
    test is the check on that claim, and it is the only thing protecting those
    installs - if it fails, the recovery instruction is wrong.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        stepped = _run_alembic(
            ["upgrade", _REVISION_AFTER_THE_SPLICE], db_url, _MIGRATIONS_DIR
        )
        assert stepped.returncode == 0, (
            f"upgrade to {_REVISION_AFTER_THE_SPLICE} failed:\n"
            f"stdout: {stepped.stdout}\nstderr: {stepped.stderr}"
        )
        # Seed *after* reaching 0089, so the rows sit in the state a running
        # 1.9-dev install would be in: past the splice, tokens intact.
        _seed_pre_reset_rows(db_path)

        before = _counts_and_addresses(db_path)
        assert before["usertoken"] == 1
        assert before["guest_session"] == 1
        assert before["guest_score"] == 1

        # Upgrading alone changes nothing: the database is already at head.
        untouched = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert untouched.returncode == 0, (
            f"upgrade failed:\nstdout: {untouched.stdout}\nstderr: {untouched.stderr}"
        )
        assert _counts_and_addresses(db_path)["usertoken"] == 1, (
            "a plain upgrade must not be expected to fix this database; if it "
            "does, the splice is reaching further than it can and this test's "
            "premise is wrong"
        )

        # The recovery route: stamp back to 0085, then upgrade.
        stamped = _run_alembic(
            ["stamp", _REVISION_BEFORE_TOKEN_RESET], db_url, _MIGRATIONS_DIR
        )
        assert stamped.returncode == 0, (
            f"stamp back failed:\nstdout: {stamped.stdout}\nstderr: {stamped.stderr}"
        )
        replayed = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert replayed.returncode == 0, (
            "re-running 0086..0089 over an already-migrated database must be a "
            f"no-op, not an error:\nstdout: {replayed.stdout}\n"
            f"stderr: {replayed.stderr}"
        )

        after = _counts_and_addresses(db_path)
        assert after["usertoken"] == 0, (
            "the stamp-back recovery route must clear a pre-splice dev "
            "database's tokens"
        )
        assert after["guest_session"] == 0
        assert after["guest_score"] == 0
        assert after["addresses"] == (None, None)
        # The replay is otherwise harmless: application data survives.
        assert after["pictures"] == before["pictures"] == 1


# ---------------------------------------------------------------------------
# 0090 - usertoken.public_id
# ---------------------------------------------------------------------------

_REVISION_BEFORE_PUBLIC_ID = "0089_add_dedupverdict_reopen_batch_id"
_REVISION_PUBLIC_ID = "0090_add_usertoken_public_id"
# The three indexes the table already carried. They are asserted after the
# migration because dropping and re-creating the table (the ``AUTOINCREMENT``
# route that was rejected in favour of this additive column) would have taken
# them with it silently - losing ``ix_usertoken_token_prefix`` in particular
# would deoptimise the token lookup path rather than fail visibly.
_PRE_EXISTING_TOKEN_INDEXES = {
    "ix_usertoken_user_id",
    "ix_usertoken_token_hash",
    "ix_usertoken_token_prefix",
}


def _usertoken_shape(db_path):
    """Return the rows, indexes and foreign keys of ``usertoken``."""
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        guest_session_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(guest_session)")
        }
        guest_token_column = (
            "token_id" if "token_id" in guest_session_columns else "token_public_id"
        )
        return {
            "rows": conn.execute(
                "SELECT id, token_hash, public_id FROM usertoken ORDER BY id"
            ).fetchall(),
            "indexes": {
                row[1]: bool(row[2])
                for row in conn.execute("PRAGMA index_list(usertoken)")
            },
            "foreign_keys": conn.execute(
                "PRAGMA foreign_key_list(usertoken)"
            ).fetchall(),
            "guest_session": conn.execute(
                f"SELECT session_id, {guest_token_column} FROM guest_session"
            ).fetchall(),
            "guest_score": conn.execute(
                f"SELECT id, {guest_token_column} FROM guest_score"
            ).fetchall(),
        }


def test_0090_backfills_public_id_without_disturbing_existing_tokens():
    """Existing tokens gain a distinct ``public_id`` and change in no other way.

    The column exists so a reference that outlives a token row cannot come to
    name a *different* token: SQLite hands out the lowest free integer primary
    key, so a deleted token's id is reissued to the next one created. The
    migration is additive precisely so that fixing that costs nothing else -
    the ids, the hashes, the foreign key and all three pre-existing indexes are
    asserted here to pin that no table rebuild happened.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        stepped = _run_alembic(
            ["upgrade", _REVISION_BEFORE_PUBLIC_ID], db_url, _MIGRATIONS_DIR
        )
        assert stepped.returncode == 0, (
            f"upgrade to {_REVISION_BEFORE_PUBLIC_ID} failed:\n"
            f"stdout: {stepped.stdout}\nstderr: {stepped.stderr}"
        )
        _seed_pre_reset_rows(db_path)
        # A second and third token, so "every row gets its own id" has
        # something to say.
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            for token_id in (2, 5):
                _insert_minimal_row(
                    conn,
                    "usertoken",
                    id=token_id,
                    user_id=1,
                    token_hash=f"hash-{token_id}",
                    token_prefix=f"prefix{token_id}",
                    scope="ALL",
                    created_at="2026-01-01 00:00:00",
                )
            conn.commit()

        result = _run_alembic(["upgrade", _REVISION_PUBLIC_ID], db_url, _MIGRATIONS_DIR)
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        shape = _usertoken_shape(db_path)

        # Ids and hashes are untouched - this is an added column, not a rebuild.
        assert [(row[0], row[1]) for row in shape["rows"]] == [
            (1, "hash-1"),
            (2, "hash-2"),
            (5, "hash-5"),
        ]

        public_ids = [row[2] for row in shape["rows"]]
        assert all(pid for pid in public_ids), (
            f"every existing token must be backfilled, got {public_ids}"
        )
        assert len(set(public_ids)) == len(public_ids), (
            f"each token must get its own public id, got {public_ids}"
        )
        assert all(len(pid) == 32 for pid in public_ids), (
            "the backfill must produce the same 32-hex-character shape as the "
            f"application's new_token_public_id, got {public_ids}"
        )

        # The unique index exists and is unique...
        assert shape["indexes"].get("ix_usertoken_public_id") is True
        # ...and the three pre-existing indexes plus the foreign key survived.
        assert _PRE_EXISTING_TOKEN_INDEXES <= set(shape["indexes"])
        assert shape["foreign_keys"] == [
            (0, 0, "user", "user_id", "id", "NO ACTION", "CASCADE", "NONE")
        ]

        # The guest rows still resolve against their token.
        expected_guest_token = shape["guest_session"][0][1]
        assert expected_guest_token in (1, "seed-public-id")
        assert shape["guest_session"] == [("sess-1", expected_guest_token)]
        assert shape["guest_score"] == [(1, expected_guest_token)]


def test_0090_public_id_uniqueness_is_enforced():
    """Two tokens cannot share a public id - the index is a real constraint."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        result = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            _insert_minimal_row(conn, "user", id=1)
            for token_id in (1, 2):
                _insert_minimal_row(
                    conn,
                    "usertoken",
                    id=token_id,
                    user_id=1,
                    token_hash=f"hash-{token_id}",
                    scope="ALL",
                    created_at="2026-01-01 00:00:00",
                    public_id=f"pub-{token_id}",
                )
            conn.commit()
            try:
                conn.execute("UPDATE usertoken SET public_id = 'pub-1' WHERE id = 2")
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError(
                    "usertoken.public_id must be unique; the duplicate was accepted"
                )


def test_0090_replay_does_not_reissue_existing_public_ids():
    """Re-running the migration leaves every existing public id alone.

    The stamp-back recovery route for a pre-splice development database
    (``test_a_pre_splice_dev_database_is_recovered_by_stamping_back_to_0085``)
    replays every revision from 0086 forward over a database that has already
    run them. If the backfill were unconditional it would hand every token a
    *new* identity on that replay, which is exactly the "an identifier came to
    mean something else" failure the column exists to prevent - and it would
    orphan every session the running server had linked to those tokens.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        assert (
            _run_alembic(
                ["upgrade", _REVISION_PUBLIC_ID], db_url, _MIGRATIONS_DIR
            ).returncode
            == 0
        )
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            _insert_minimal_row(conn, "user", id=1)
            _insert_minimal_row(
                conn,
                "usertoken",
                id=1,
                user_id=1,
                token_hash="hash-1",
                scope="ALL",
                created_at="2026-01-01 00:00:00",
                public_id="a" * 32,
            )
            conn.commit()
        before = _usertoken_shape(db_path)["rows"]
        assert before == [(1, "hash-1", "a" * 32)]

        # Stamp back rather than downgrade: a downgrade drops the column, so it
        # could not show whether the backfill respects an existing value. This
        # is the same recovery route the pre-splice test documents.
        stamped = _run_alembic(
            ["stamp", _REVISION_BEFORE_PUBLIC_ID], db_url, _MIGRATIONS_DIR
        )
        assert stamped.returncode == 0, (
            f"stamp back failed:\nstdout: {stamped.stdout}\nstderr: {stamped.stderr}"
        )
        replayed = _run_alembic(
            ["upgrade", _REVISION_PUBLIC_ID], db_url, _MIGRATIONS_DIR
        )
        assert replayed.returncode == 0, (
            "re-running 0090 over an already-migrated database must be a no-op, "
            f"not an error:\nstdout: {replayed.stdout}\nstderr: {replayed.stderr}"
        )
        assert _usertoken_shape(db_path)["rows"] == before, (
            "a replay must not reissue public ids to tokens that already have one"
        )


def test_0093_rebuilds_guest_tables_onto_token_public_id_and_clears_rows():
    """The hub split removes stale integer token references from each vault."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"
        before = _run_alembic(
            ["upgrade", "0098_add_library_settings"], db_url, _MIGRATIONS_DIR
        )
        assert before.returncode == 0, before.stderr
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DROP TABLE guest_score")
            conn.execute("DROP TABLE guest_session")
            conn.execute(
                "CREATE TABLE guest_session (session_id TEXT PRIMARY KEY, "
                "token_id INTEGER NOT NULL, created_at TEXT NOT NULL, "
                "last_active_at TEXT NOT NULL, cookie_token TEXT)"
            )
            conn.execute(
                "CREATE TABLE guest_score (id INTEGER PRIMARY KEY, session_id TEXT "
                "NOT NULL, token_id INTEGER NOT NULL, picture_id INTEGER NOT NULL, "
                "score INTEGER NOT NULL, scored_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO guest_session VALUES "
                "('legacy', 1, '2026-01-01', '2026-01-01', 'cookie')"
            )
            conn.execute(
                "INSERT INTO guest_score VALUES (1, 'legacy', 1, 1, 4, '2026-01-01')"
            )
            conn.commit()

        result = _run_alembic(
            ["upgrade", "0099_guest_tables_reference_token_public_id"],
            db_url,
            _MIGRATIONS_DIR,
        )
        assert result.returncode == 0, result.stderr
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            for table in ("guest_session", "guest_score"):
                columns = {
                    row[1] for row in conn.execute(f"PRAGMA table_info({table})")
                }
                assert "token_public_id" in columns
                assert "token_id" not in columns
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)


def test_the_migration_chain_has_exactly_one_head():
    """The v1.8.1 merge left two 0086 revisions; only one may be a head.

    ``0086_reissue_api_tokens`` has already run on released v1.8.1 installs, so
    it keeps its identifier and its parent, and ``0086_add_operation_log`` (1.9
    only, unreleased) was re-pointed onto it. A second head here means that
    splice was undone.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_url = f"sqlite:///{os.path.join(tmp, 'test_vault.db')}"
        result = _run_alembic(["heads"], db_url, _MIGRATIONS_DIR)
        assert result.returncode == 0, (
            f"alembic heads failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        heads = [line for line in result.stdout.splitlines() if "(head)" in line]
        assert len(heads) == 1, f"expected exactly one head, got: {heads}"


def test_alembic_0094_adds_telemetry_consent_columns_defaulting_off():
    """An existing install upgrading in must land with every category off.

    The columns are added nullable, so the pre-existing user row reads NULL for
    all five; ``serialize_user_config`` maps NULL to the model default. This
    asserts the DDL half of that: the columns exist, the row survives, and
    nothing was written that could read as consent.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        # A real pre-0094 install: upgrade to the parent revision, then plant a
        # user row the way an existing owner account would look.
        up = _run_alembic(
            ["upgrade", "0093_invalidate_stackcohesion_inputs"],
            db_url,
            _MIGRATIONS_DIR,
        )
        assert up.returncode == 0, (
            f"upgrade to 0093 failed:\nstdout: {up.stdout}\nstderr: {up.stderr}"
        )

        expected = {
            "telemetry_send_install_id",
            "telemetry_send_feature_usage",
            "telemetry_send_error_reports",
            "telemetry_send_hardware_profile",
            "telemetry_consent_prompted",
        }

        conn = sqlite3.connect(db_path)
        try:
            # The 0001 baseline builds `user` with SQLModel.metadata.create_all,
            # so a fresh DB already carries every *current* model column. Drop
            # the five back off to reproduce the shape a real pre-0094 install
            # actually has on disk. Otherwise this asserts nothing.
            for column in sorted(expected):
                conn.execute(f"ALTER TABLE user DROP COLUMN {column}")

            # Fill every remaining NOT NULL column that has no default, derived
            # from the live schema rather than hardcoded, so a future NOT NULL
            # addition does not silently break this test's setup.
            required = [
                row[1]
                for row in conn.execute("PRAGMA table_info(user)")
                if row[3] and row[4] is None and not row[5]
            ]
            names = ["id", "username", "password_hash"] + [
                name
                for name in required
                if name not in {"id", "username", "password_hash"}
            ]
            values = [1, "owner", "x"] + [0] * (len(names) - 3)
            conn.execute(
                "INSERT INTO user ({}) VALUES ({})".format(
                    ", ".join(names), ", ".join("?" * len(names))
                ),
                values,
            )
            conn.commit()
        finally:
            conn.close()

        head = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert head.returncode == 0, (
            f"upgrade to head failed:\nstdout: {head.stdout}\nstderr: {head.stderr}"
        )

        conn = sqlite3.connect(db_path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(user)")}
            assert expected <= columns, (
                f"missing telemetry columns: {expected - columns}"
            )

            row = conn.execute(
                "SELECT {} FROM user WHERE id = 1".format(", ".join(sorted(expected)))
            ).fetchone()
            assert row is not None, "the existing user row did not survive the upgrade"
            # NULL, never 1: nothing in the upgrade may look like an opt-in.
            assert all(value is None for value in row), (
                f"telemetry columns were pre-populated on upgrade: {row}"
            )
        finally:
            conn.close()


_E2E_FIXTURE = os.path.join(_PROJECT_ROOT, "test-data", "images", "vault.db")


def test_a_populated_library_upgrades_through_the_picture_table_rebuild():
    """A vault with rows must survive migration 0080's rebuild of ``picture``.

    ``op.batch_alter_table`` is the only way SQLite can drop a column: it
    copies the table, ``DROP``s the original and renames. The vault engine runs
    with ``PRAGMA foreign_keys=ON`` (``database.init_database``), so that
    ``DROP`` raises "FOREIGN KEY constraint failed" as soon as any row
    references the table - which is every real library, and the committed e2e
    fixture. It brought the whole `e2e` lane down: the server could not boot.

    A fresh database has nothing referencing ``picture``, so `upgrade head` on
    an empty file passes either way; only a populated upgrade catches this.
    The fixture is used because it is the artefact that actually failed, and
    the e2e job already treats its absence as a coverage failure.

    Goes through ``VaultDatabase`` deliberately: the standalone
    ``PIXLSTASH_DB_URL`` path builds its engine from ``alembic.ini`` with no
    connect listener, so FK enforcement is off there and the defect is
    invisible.
    """
    from pixlstash.database import VaultDatabase

    assert os.path.isfile(_E2E_FIXTURE), (
        f"committed e2e fixture missing at {_E2E_FIXTURE}"
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "vault.db")
        shutil.copyfile(_E2E_FIXTURE, db_path)
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            before = conn.execute("SELECT COUNT(*) FROM picture").fetchone()[0]
        assert before > 0, "fixture must carry pictures for this to mean anything"

        db = VaultDatabase(db_path)
        try:
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                after = conn.execute("SELECT COUNT(*) FROM picture").fetchone()[0]
                # The rebuild copies rows; losing them would be the other
                # failure mode of suspending enforcement around it.
                assert after == before
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                assert violations == []

            # Enforcement must be back on for the connection the app then uses;
            # a suspension that leaked would disable it for the whole session.
            enabled = db.run_immediate_read_task(
                lambda session: session.exec(text("PRAGMA foreign_keys")).one()[0]
            )
            assert enabled == 1
        finally:
            db.close()


def test_a_library_carrying_dangling_rows_still_opens():
    """Orphan rows that predate the run must not stop a library opening.

    ``PRAGMA foreign_key_check`` reports the whole database, and
    ``command.upgrade`` runs on every vault open, so a guard that refuses any
    violation refuses one it did not cause - permanently, with no repair path.
    Rows written before their FK existed, or by any path with enforcement off,
    are exactly that. The committed fixture already ships some (``guest_score``
    -> ``usertoken``); migration 0093 clears those, so this adds one that
    survives to head.

    Goes through ``VaultDatabase`` because that is where it bites: the vault
    engine runs with enforcement on, so the guard is active there.
    """
    from pixlstash.database import VaultDatabase

    assert os.path.isfile(_E2E_FIXTURE), (
        f"committed e2e fixture missing at {_E2E_FIXTURE}"
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "vault.db")
        shutil.copyfile(_E2E_FIXTURE, db_path)
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            _insert_minimal_row(conn, "quality", id=999999, picture_id=999999)
            conn.commit()
            assert conn.execute("PRAGMA foreign_key_check").fetchall(), (
                "the row must be an actual violation for this to test anything"
            )

        db = VaultDatabase(db_path)
        try:
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                survived = conn.execute(
                    "SELECT COUNT(*) FROM quality WHERE picture_id = 999999"
                ).fetchone()[0]
                # Left alone, not quietly repaired: deleting rows to satisfy a
                # check the run did not fail is its own kind of data loss.
                assert survived == 1
        finally:
            db.close()


_BREAKING_MIGRATION = '''"""Orphan a character by deleting its project (test fixture only)."""

from alembic import op

revision = "9999_break_a_foreign_key"
down_revision = "{head}"
branch_labels = None
depends_on = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    op.execute("DELETE FROM project WHERE id = 1")


def downgrade() -> None:
    op.execute("INSERT INTO project (id, name) VALUES (1, 'orphan-maker')")
'''


def _database_at_head_with_a_character_in_a_project(db_path):
    """Upgrade *db_path* to head and give it one valid character -> project row."""
    result = _run_alembic(["upgrade", "head"], f"sqlite:///{db_path}", _MIGRATIONS_DIR)
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        _insert_minimal_row(conn, "project", id=1, name="orphan-maker")
        _insert_minimal_row(conn, "character", id=1, name="orphan", project_id=1)
        conn.commit()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == [], (
            "the seed rows must be valid, or the guard has nothing new to catch"
        )


@contextlib.contextmanager
def _migration_tree_that_breaks_a_foreign_key(db_path):
    """A copy of the real migration tree with one FK-breaking migration on top."""
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        head = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    with tempfile.TemporaryDirectory() as tree:
        shutil.copytree(
            os.path.join(_MIGRATIONS_DIR, "migrations"),
            os.path.join(tree, "migrations"),
        )
        shutil.copyfile(
            os.path.join(_MIGRATIONS_DIR, "alembic.ini"),
            os.path.join(tree, "alembic.ini"),
        )
        with open(
            os.path.join(tree, "migrations", "versions", "9999_break_a_foreign_key.py"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(_BREAKING_MIGRATION.format(head=head))
        yield tree


def test_a_migration_that_orphans_a_row_is_refused_and_the_run_rolled_back():
    """Suspending enforcement must not let a migration ship a broken database.

    The vault path is the one that can be prevented rather than merely
    reported: Alembic runs on the caller's open connection, so nothing is
    committed until ``_run_migrations`` says so, and the guard rejects the run
    before that happens.
    """
    from alembic import command
    from alembic.config import Config

    from pixlstash.database import create_configured_engine

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "vault.db")
        _database_at_head_with_a_character_in_a_project(db_path)

        with _migration_tree_that_breaks_a_foreign_key(db_path) as tree:
            config = Config(os.path.join(tree, "alembic.ini"))
            config.set_main_option("script_location", os.path.join(tree, "migrations"))
            config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

            engine = create_configured_engine(db_path)
            try:
                with engine.connect() as conn:
                    # database._run_migrations inspects the schema first, which
                    # leaves the transaction open and hands Alembic an external
                    # one. Reproduce that, or the rollback under test is not
                    # the one production gets.
                    conn.exec_driver_sql("SELECT 1")
                    assert conn.in_transaction()
                    config.attributes["connection"] = conn

                    raised = None
                    try:
                        command.upgrade(config, "head")
                    except RuntimeError as exc:
                        raised = exc
                    assert raised is not None, "the broken migration was accepted"
                    assert "introduced dangling foreign keys" in str(raised)
            finally:
                engine.dispose()

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            assert (
                conn.execute("SELECT COUNT(*) FROM project WHERE id = 1").fetchone()[0]
                == 1
            ), "the rejected migration's delete was committed anyway"
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert (
                conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                != "9999_break_a_foreign_key"
            )


def test_the_standalone_engine_refuses_a_broken_foreign_key_too():
    """The guard must not be gated on enforcement having been on.

    ``alembic.ini`` builds its own engine with no connect listener, so SQLite
    leaves foreign keys off there - and that is the snapshot upgrade path
    (``services/restore/schema_upgrade``), the one migration input that did not
    come from this process. It cannot roll back (each migration commits as it
    goes, with no external transaction), but it must still refuse: its caller
    discards the scratch file it was upgrading.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "vault.db")
        _database_at_head_with_a_character_in_a_project(db_path)

        with _migration_tree_that_breaks_a_foreign_key(db_path) as tree:
            result = _run_alembic(["upgrade", "head"], f"sqlite:///{db_path}", tree)

        assert result.returncode != 0, (
            f"the broken migration was accepted:\nstdout: {result.stdout}"
        )
        assert "introduced dangling foreign keys" in result.stderr, result.stderr
