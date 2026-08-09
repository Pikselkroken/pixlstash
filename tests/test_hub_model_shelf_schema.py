"""The model shelf's hub tables (v1.10.0).

These live in the hub rather than in a vault because what they record is a fact
about the machine: a folder of LoRAs is on this disk. Re-registering the same
folder in every library would be absurd. The only vault-side table is
``adapter_attachment``, which is a different change.

The rule under test throughout: **applying the schema twice is a no-op, and a
hub that already exists picks the tables up on its next open.** That is what
lets these land by amending v2 instead of inventing a v3.
"""

import sqlite3

import pytest

from pixlstash.hub.schema import (
    CURRENT_SCHEMA_VERSION,
    apply_migrations,
    read_schema_version,
)

SHELF_TABLES = (
    "model_folder",
    "adapter",
    "adapter_file",
    "checkpoint",
    "adapter_stack",
)


@pytest.fixture
def hub(tmp_path):
    conn = sqlite3.connect(tmp_path / "hub.db")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def table_names(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def ddl_for(conn, name):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row[0] if row else None


class TestFreshHub:
    def test_every_shelf_table_is_created(self, hub):
        apply_migrations(hub)
        assert SHELF_TABLES == tuple(t for t in SHELF_TABLES if t in table_names(hub))

    def test_the_hub_stays_on_version_two(self, hub):
        # The whole point of amending v2: a build that predates this change has
        # CURRENT_SCHEMA_VERSION = 2 and would refuse a v3 hub outright, locking
        # a user out of downgrading.
        apply_migrations(hub)
        assert read_schema_version(hub) == 2
        assert CURRENT_SCHEMA_VERSION == 2

    @pytest.mark.parametrize(
        "table", ("model_folder", "adapter", "checkpoint", "adapter_stack")
    )
    def test_ids_never_recycle(self, hub, table):
        # AUTOINCREMENT, per the library.id precedent. Without it SQLite hands a
        # deleted row's id to the next insert, and a recycled folder id would
        # silently re-point every adapter_file row at a different folder.
        apply_migrations(hub)
        assert "AUTOINCREMENT" in ddl_for(hub, table)

    def test_the_scan_and_stack_indexes_exist(self, hub):
        apply_migrations(hub)
        indexes = {
            row[0]
            for row in hub.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert {
            "ix_adapter_file_sha",
            "ix_adapter_file_folder",
            "ix_adapter_stack_member",
        } <= indexes


class TestReRunIsSafe:
    def test_applying_twice_changes_nothing(self, hub):
        apply_migrations(hub)
        before = sorted(
            hub.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        )
        apply_migrations(hub)
        after = sorted(
            hub.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        )
        assert before == after

    def test_an_existing_v2_hub_gains_the_tables_on_next_open(self, hub):
        """The case that makes amending v2 legitimate.

        A hub created by an earlier build is already at version 2, so the
        migration loop skips every step. It must still end up with the shelf
        tables, which is what the unconditional re-run at the tail of
        apply_migrations is for.
        """
        apply_migrations(hub)
        for table in SHELF_TABLES:
            hub.execute(f"DROP TABLE {table}")
        assert read_schema_version(hub) == 2  # still v2, nothing to upgrade
        assert not (set(SHELF_TABLES) & table_names(hub))

        apply_migrations(hub)
        assert set(SHELF_TABLES) <= table_names(hub)

    def test_existing_rows_survive_a_re_run(self, hub):
        apply_migrations(hub)
        hub.execute(
            "INSERT INTO model_folder (path, kind, movable) VALUES (?, ?, ?)",
            ("/models/loras", "user", "per_item"),
        )
        hub.commit()
        apply_migrations(hub)
        assert hub.execute("SELECT path FROM model_folder").fetchall() == [
            ("/models/loras",)
        ]


class TestConstraints:
    def test_an_adapter_is_identified_by_its_hash(self, hub):
        apply_migrations(hub)
        for _ in range(1):
            hub.execute(
                "INSERT INTO adapter (sha256, kind, provenance) VALUES (?, ?, ?)",
                ("abc", "lora", "external"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            hub.execute(
                "INSERT INTO adapter (sha256, kind, provenance) VALUES (?, ?, ?)",
                ("abc", "lora", "trained"),
            )

    def test_a_checkpoint_may_be_registered_before_it_is_hashed(self, hub):
        # Deliberately different from `adapter`: a checkpoint can be many
        # gigabytes and is registered in place long before anything hashes it.
        apply_migrations(hub)
        hub.execute(
            "INSERT INTO checkpoint (filename, local_path) VALUES (?, ?)",
            ("flux1-dev.safetensors", "/models/flux1-dev.safetensors"),
        )
        assert hub.execute("SELECT sha256 FROM checkpoint").fetchone() == (None,)

    def test_two_unhashed_checkpoints_do_not_collide(self, hub):
        # SQLite treats NULLs as distinct under UNIQUE, which is what allows a
        # whole folder to be registered before any of it is hashed.
        apply_migrations(hub)
        for name in ("a.safetensors", "b.safetensors"):
            hub.execute("INSERT INTO checkpoint (filename) VALUES (?)", (name,))
        assert hub.execute("SELECT COUNT(*) FROM checkpoint").fetchone()[0] == 2

    def test_one_path_per_folder_is_recorded_once(self, hub):
        apply_migrations(hub)
        hub.execute(
            "INSERT INTO model_folder (path, kind, movable) VALUES (?, ?, ?)",
            ("/models", "user", "per_item"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            hub.execute(
                "INSERT INTO model_folder (path, kind, movable) VALUES (?, ?, ?)",
                ("/models", "managed", "root_only"),
            )

    def test_the_same_file_may_sit_in_two_folders(self, hub):
        # One adapter, many paths. That is what a copy into a second registered
        # folder is, and what a duplicate after an interrupted move is.
        apply_migrations(hub)
        hub.execute(
            "INSERT INTO adapter (sha256, kind, provenance) VALUES ('h', 'lora', 'external')"
        )
        for path in ("/a", "/b"):
            hub.execute(
                "INSERT INTO model_folder (path, kind, movable) VALUES (?, 'user', 'per_item')",
                (path,),
            )
        for folder_id in (1, 2):
            hub.execute(
                "INSERT INTO adapter_file "
                "(adapter_sha256, model_folder_id, relpath, state) "
                "VALUES ('h', ?, 'x.safetensors', 'present')",
                (folder_id,),
            )
        assert hub.execute("SELECT COUNT(*) FROM adapter_file").fetchone()[0] == 2


class TestTombstone:
    def test_dropping_a_folders_files_keeps_the_adapter_and_its_curation(self, hub):
        """Why folder removal needs no confirmation prompt.

        Removing a folder drops ``adapter_file`` rows and keeps the ``adapter``
        row, so the display name the user typed survives and re-adding the
        folder re-links by sha256. Nothing a user authored is destroyed, which
        is the whole basis for not interrupting them with a dialog.
        """
        apply_migrations(hub)
        hub.execute(
            "INSERT INTO adapter (sha256, kind, provenance, display_name) "
            "VALUES ('h', 'lora', 'external', 'Clementine v3')"
        )
        hub.execute(
            "INSERT INTO model_folder (path, kind, movable) "
            "VALUES ('/models', 'user', 'per_item')"
        )
        hub.execute(
            "INSERT INTO adapter_file "
            "(adapter_sha256, model_folder_id, relpath, state) "
            "VALUES ('h', 1, 'clem.safetensors', 'present')"
        )
        hub.commit()

        hub.execute("DELETE FROM adapter_file WHERE model_folder_id = 1")
        hub.execute("DELETE FROM model_folder WHERE id = 1")
        hub.commit()

        assert hub.execute("SELECT display_name FROM adapter").fetchone() == (
            "Clementine v3",
        )
