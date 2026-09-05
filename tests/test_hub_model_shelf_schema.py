"""The model shelf's hub tables (v1.10.0).

These live in the hub rather than in a vault because what they record is a fact
about the machine: a folder of LoRAs is on this disk. Re-registering the same
folder in every library would be absurd. The only vault-side table is
``adapter_attachment``, which is a different change.

Two rules under test throughout:

1. **Applying the schema twice is a no-op, and a hub that already exists picks
   the tables up on its next open.** That is what lets these land by amending v2
   instead of inventing a v3.
2. **``model`` is what a file is; ``model_file`` is where a copy of it sits.**
   One content table and one location table, for adapters and checkpoints
   alike (integration plan §3). A checkpoint therefore tombstones exactly the
   way an adapter does, which the superseded inline ``checkpoint.local_path``
   could not do.
"""

import re
import sqlite3
import threading
import time

import pytest

from pixlstash.hub.schema import (
    CURRENT_DATA_VERSION,
    CURRENT_SCHEMA_VERSION,
    _V2_MODEL,
    _V2_MODEL_FILE,
    apply_migrations,
    read_schema_version,
)

SHELF_TABLES = (
    "model_folder",
    "model",
    "model_file",
    "adapter_stack",
)

# The shape this replaced. Nothing ever wrote them (the scan is unmerged and no
# released build shipped them), so `_apply_v2` drops rather than migrates.
SUPERSEDED_TABLES = ("adapter", "adapter_file", "checkpoint")


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


def column_names(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ddl_for(conn, name):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row[0] if row else None


def add_model(conn, *, file_kind="adapter", sha256="h", **columns):
    """Insert one content row and return its id.

    ``kind`` defaults the way the scanner writes it, an algorithm for an
    adapter and NULL for anything else, so the suite's rows are shaped the way
    real ones are. A helper that left it NULL on an adapter would be the one
    thing that lets a constraint or query regression pass unnoticed.
    """
    columns = {
        "file_kind": file_kind,
        "sha256": sha256,
        "kind": "lora" if file_kind == "adapter" else None,
        "provenance": "external",
        **columns,
    }
    placeholders = ", ".join("?" for _ in columns)
    cursor = conn.execute(
        f"INSERT INTO model ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(columns.values()),
    )
    return int(cursor.lastrowid)


def _install_pre_check_model_table(conn):
    """Put back the `model` shape a hub opened on an earlier develop still has.

    Derived from the shipped DDL with the adapter-kind CHECK cut out, so it
    stays the real pre-CHECK shape column for column instead of drifting from
    it. Both tables go, because `model_file`'s foreign key would block the drop.
    """
    ddl = re.sub(
        r",\s*(--[^\n]*\n\s*)*CHECK \(file_kind <> 'adapter' OR kind IS NOT NULL\)",
        "",
        _V2_MODEL,
    )
    assert "kind IS NOT NULL" not in ddl, "the pre-CHECK DDL still carries the CHECK"
    conn.execute("DROP TABLE model_file")
    conn.execute("DROP TABLE model")
    conn.execute(ddl)
    conn.execute(_V2_MODEL_FILE)


def add_folder(conn, path):
    cursor = conn.execute(
        "INSERT INTO model_folder (path, kind, movable) VALUES (?, 'user', 'per_item')",
        (path,),
    )
    return int(cursor.lastrowid)


def add_file(conn, model_id, folder_id, relpath, state="present"):
    conn.execute(
        "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
        "VALUES (?, ?, ?, ?)",
        (model_id, folder_id, relpath, state),
    )


class TestFreshHub:
    def test_every_shelf_table_is_created(self, hub):
        apply_migrations(hub)
        assert set(SHELF_TABLES) <= table_names(hub)

    def test_the_superseded_tables_are_gone(self, hub):
        apply_migrations(hub)
        assert not (set(SUPERSEDED_TABLES) & table_names(hub))

    def test_the_hub_stays_on_version_two(self, hub):
        # The whole point of amending v2: a build that predates this change has
        # CURRENT_SCHEMA_VERSION = 2 and would refuse a v3 hub outright, locking
        # a user out of downgrading.
        apply_migrations(hub)
        assert read_schema_version(hub) == 2
        assert CURRENT_SCHEMA_VERSION == 2

    @pytest.mark.parametrize("table", ("model_folder", "model", "adapter_stack"))
    def test_ids_never_recycle(self, hub, table):
        # AUTOINCREMENT, per the library.id precedent. Without it SQLite hands a
        # deleted row's id to the next insert, and a recycled id would silently
        # re-point every model_file row at a different folder or model.
        apply_migrations(hub)
        assert "AUTOINCREMENT" in ddl_for(hub, table)

    def test_the_scan_stack_and_hash_queue_indexes_exist(self, hub):
        apply_migrations(hub)
        indexes = {
            row[0]
            for row in hub.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert {
            "ix_model_file_model",
            "ix_model_file_folder",
            "ix_model_stack_member",
            "ix_model_hash_queue",
        } <= indexes

    def test_the_hash_queue_index_is_partial(self, hub):
        # A full index on sha256 would be almost entirely rows the finder never
        # wants: the queue is a handful of rows in a table of thousands.
        apply_migrations(hub)
        sql = hub.execute(
            "SELECT sql FROM sqlite_master WHERE name='ix_model_hash_queue'"
        ).fetchone()[0]
        assert "WHERE sha256 IS NULL" in sql


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
        for table in ("model_file", "model", "model_folder", "adapter_stack"):
            hub.execute(f"DROP TABLE {table}")
        assert read_schema_version(hub) == 2  # still v2, nothing to upgrade
        assert not (set(SHELF_TABLES) & table_names(hub))

        apply_migrations(hub)
        assert set(SHELF_TABLES) <= table_names(hub)

    def test_an_existing_v2_hub_gains_the_icon_column_on_next_open(self, hub):
        """The icon verb's column, added the same way and for the same reason.

        A build shipped before it has `CURRENT_SCHEMA_VERSION = 2` and would
        refuse a v3 hub with `HubSchemaTooNewError`, locking that user out of a
        downgrade - so the column amends v2 in place rather than bumping it.
        The rows already in `model` must survive the amend, which is what makes
        this different from the table case above: those could be dropped and
        recreated because nothing had ever written them.
        """
        apply_migrations(hub)
        ddl = hub.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='model'"
        ).fetchone()[0]
        assert "icon_sha256" in ddl

        # Rebuild `model` as the pre-icon build had it, with a row in it.
        hub.execute("DROP TABLE model")
        hub.execute(re.sub(r"\n\s*icon_sha256\s+TEXT,", "", ddl))
        hub.execute(
            "INSERT INTO model (file_kind, kind, sha256, filename, provenance, "
            "created_at) VALUES ('adapter', 'lora', ?, 'keep.safetensors', "
            "'scanned', '2026-08-11T00:00:00Z')",
            ("a" * 64,),
        )
        # Committed explicitly, and the reason is the same pysqlite behaviour the
        # stack detector's UPDATE guard exists for: with `isolation_level=""` a
        # transaction opens on DML, so this INSERT leaves one open while the
        # DROP/CREATE above did not. `apply_migrations` then fails with "cannot
        # start a transaction within a transaction" - which is how this test
        # first failed.
        hub.commit()
        assert "icon_sha256" not in column_names(hub, "model")
        assert read_schema_version(hub) == 2

        apply_migrations(hub)

        assert "icon_sha256" in column_names(hub, "model")
        kept = hub.execute("SELECT filename, icon_sha256 FROM model").fetchone()
        assert kept == ("keep.safetensors", None)
        assert read_schema_version(hub) == 2, "the amend must not bump to v3"

    def test_the_re_run_takes_the_write_lock_before_it_reads_the_schema(self, tmp_path):
        """The tail is the same read-then-ALTER logic the versioned path guards.

        A server and a ``pixlstash libraries`` CLI can open the same
        pre-model-shelf v2 hub at once. Under sqlite3's default DEFERRED
        transaction both read "column absent", both ALTER, and the loser raises
        ``OperationalError`` ("duplicate column name") straight out of
        ``HubDatabase.__init__``. ``BEGIN IMMEDIATE`` before the read means the
        loser waits and then sees the column the winner added.

        Deterministic, not timing-hopeful: the competitor holds the write lock
        across the whole re-run and only commits once the re-run is provably
        parked on it.
        """
        hub_path = tmp_path / "hub.db"
        conn = sqlite3.connect(hub_path, timeout=30, check_same_thread=False)
        competitor = sqlite3.connect(hub_path, timeout=30)
        errors: list[Exception] = []
        try:
            apply_migrations(conn)
            conn.commit()
            # Put the hub back in the state a pre-telemetry v2 developer hub is
            # in: still version 2, so the migration loop skips every step and
            # only the tail runs, and missing a column the tail wants to add.
            conn.execute("ALTER TABLE user DROP COLUMN telemetry_consent_prompted")
            conn.commit()
            assert read_schema_version(conn) == 2

            competitor.execute("BEGIN IMMEDIATE")

            def rerun():
                try:
                    apply_migrations(conn)
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            thread = threading.Thread(target=rerun)
            thread.start()
            # The competitor is the other process winning the race: it adds the
            # very column the parked re-run intends to add.
            time.sleep(0.3)
            competitor.execute(
                "ALTER TABLE user ADD COLUMN telemetry_consent_prompted INTEGER"
            )
            competitor.commit()
            thread.join(timeout=30)

            assert not thread.is_alive(), "the re-run never finished"
            assert not errors, f"the losing re-run raised: {errors}"
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(user)").fetchall()
            }
            assert "telemetry_consent_prompted" in columns
        finally:
            conn.close()
            competitor.close()

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

    def test_a_pre_reshape_hub_is_reshaped_on_its_next_open(self, hub):
        """A developer hub opened on an earlier develop still gets the new shape.

        ``CREATE TABLE IF NOT EXISTS`` cannot reshape a table, so without the
        drop guard such a hub would keep the superseded three tables and gain
        the new two, with the scan writing to neither.
        """
        apply_migrations(hub)
        hub.execute("DROP TABLE model_file")
        hub.execute("DROP TABLE model")
        hub.execute("CREATE TABLE adapter (sha256 TEXT)")
        hub.execute("CREATE TABLE adapter_file (relpath TEXT)")
        hub.execute("CREATE TABLE checkpoint (local_path TEXT)")
        hub.commit()

        apply_migrations(hub)

        assert not (set(SUPERSEDED_TABLES) & table_names(hub))
        assert {"model", "model_file"} <= table_names(hub)

    def test_a_hub_with_the_pre_check_model_table_keeps_its_rows(self, hub):
        """The adapter-kind CHECK reaches a populated hub without costing it data.

        SQLite has no ``ALTER TABLE ADD CONSTRAINT``, so the table is rebuilt:
        new shape, copy, drop, rename. The copy is the point: ``model.id`` and
        ``sha256`` come across unchanged, so the shelf is not re-hashed and no
        checkpoint is re-queued at 24 GB to recover a digest the hub had.
        """
        apply_migrations(hub)
        _install_pre_check_model_table(hub)
        folder_id = add_folder(hub, "/models")
        adapter_id = add_model(hub, sha256="abc", kind="lora", display_name="Clem v3")
        checkpoint_id = add_model(hub, file_kind="checkpoint", sha256=None, kind=None)
        add_file(hub, adapter_id, folder_id, "clem.safetensors")
        add_file(hub, checkpoint_id, folder_id, "flux.safetensors")
        hub.commit()

        apply_migrations(hub)

        assert hub.execute(
            "SELECT id, sha256, display_name FROM model ORDER BY id"
        ).fetchall() == [(adapter_id, "abc", "Clem v3"), (checkpoint_id, None, None)]
        assert hub.execute(
            "SELECT model_id, relpath, state FROM model_file ORDER BY relpath"
        ).fetchall() == [
            (adapter_id, "clem.safetensors", "present"),
            (checkpoint_id, "flux.safetensors", "present"),
        ]
        assert hub.execute("SELECT id FROM model_folder").fetchall() == [(folder_id,)]
        assert hub.execute("PRAGMA foreign_key_check").fetchall() == []
        # And the constraint the rebuild was for is now live.
        with pytest.raises(sqlite3.IntegrityError):
            add_model(hub, file_kind="adapter", sha256="new", kind=None)

    def test_the_rebuild_leaves_the_indexes_where_they_belong(self, hub):
        # Dropping `model` takes its indexes with it and the rename carries
        # nothing stale over, so the CREATE INDEX block is what puts them back.
        apply_migrations(hub)
        _install_pre_check_model_table(hub)
        hub.commit()

        apply_migrations(hub)

        indexes = {
            row[0]: (row[1], row[2])
            for row in hub.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert indexes["ix_model_stack_member"][0] == "model"
        assert indexes["ix_model_file_model"][0] == "model_file"
        assert indexes["ix_model_file_folder"][0] == "model_file"
        # The partial hash-queue index in particular: a full one would be almost
        # entirely rows MissingCheckpointHashFinder never wants.
        table, sql = indexes["ix_model_hash_queue"]
        assert table == "model"
        assert "WHERE sha256 IS NULL" in sql

    def test_an_adapter_with_no_kind_stops_the_rebuild_loudly(self, hub):
        # Nothing emits that row. If one exists anyway the copy must fail rather
        # than fall back to dropping the table, which is how the rows would be
        # lost quietly.
        apply_migrations(hub)
        _install_pre_check_model_table(hub)
        folder_id = add_folder(hub, "/models")
        model_id = add_model(hub, sha256="abc", kind=None)
        add_file(hub, model_id, folder_id, "clem.safetensors")
        hub.commit()

        with pytest.raises(sqlite3.IntegrityError):
            apply_migrations(hub)

        assert hub.execute("SELECT id, sha256 FROM model").fetchall() == [
            (model_id, "abc")
        ]
        assert hub.execute("SELECT COUNT(*) FROM model_file").fetchone() == (1,)

    def test_the_drop_guard_is_idempotent_and_loses_nothing_after_the_first_open(
        self, hub
    ):
        """Three consecutive opens; only the first may drop anything.

        The guard keys on ``adapter``, which does not exist after the reshape,
        so it must be false on a fresh hub and false on every re-run. If it
        ever fired again it would take a populated ``model``/``model_file`` pair
        with it - the guard drops tables, and a drop cannot be undone.
        """
        apply_migrations(hub)
        folder_id = add_folder(hub, "/models")
        model_id = add_model(hub, display_name="Clementine v3")
        add_file(hub, model_id, folder_id, "clem.safetensors")
        hub.commit()

        for _ in range(2):
            apply_migrations(hub)
            assert hub.execute("SELECT display_name FROM model").fetchone() == (
                "Clementine v3",
            )
            assert hub.execute("SELECT COUNT(*) FROM model_file").fetchone()[0] == 1
            assert hub.execute("SELECT COUNT(*) FROM model_folder").fetchone()[0] == 1


class TestConstraints:
    def test_a_model_is_identified_by_its_hash(self, hub):
        apply_migrations(hub)
        add_model(hub, sha256="abc")
        with pytest.raises(sqlite3.IntegrityError):
            add_model(hub, sha256="abc", provenance="trained")

    def test_an_adapter_may_not_be_stored_without_a_hash(self, hub):
        # The CHECK keeps `adapter`'s old NOT NULL exactly where it was
        # load-bearing: an adapter is hashed on sight, and its sha256 is the
        # interop identity Civitai lookup and `{sha256}/file` both resolve on.
        apply_migrations(hub)
        with pytest.raises(sqlite3.IntegrityError):
            add_model(hub, file_kind="adapter", sha256=None)

    def test_an_adapter_may_not_be_stored_without_a_kind(self, hub):
        # No producer emits it: AdapterInfo.kind is typed str and
        # detect_adapter_kind returns 'unknown' rather than None on every path.
        # The CHECK is what keeps it that way once something else writes rows.
        apply_migrations(hub)
        with pytest.raises(sqlite3.IntegrityError):
            add_model(hub, file_kind="adapter", kind=None)

    def test_a_checkpoint_needs_no_kind(self, hub):
        # The other direction: `kind` names an adapter algorithm, so demanding
        # one of a checkpoint would be its own bug.
        apply_migrations(hub)
        add_model(hub, file_kind="checkpoint", sha256=None, kind=None)
        assert hub.execute("SELECT kind FROM model").fetchone() == (None,)

    def test_a_checkpoint_may_be_registered_before_it_is_hashed(self, hub):
        # Deliberately different from an adapter: a checkpoint can be many
        # gigabytes and is registered in place long before anything hashes it.
        apply_migrations(hub)
        add_model(hub, file_kind="checkpoint", sha256=None, filename="flux1-dev")
        assert hub.execute("SELECT sha256 FROM model").fetchone() == (None,)

    def test_two_unhashed_checkpoints_do_not_collide(self, hub):
        # SQLite treats NULLs as distinct under UNIQUE, which is what allows a
        # whole folder to be registered before any of it is hashed.
        apply_migrations(hub)
        for name in ("a.safetensors", "b.safetensors"):
            add_model(hub, file_kind="checkpoint", sha256=None, filename=name)
        assert hub.execute("SELECT COUNT(*) FROM model").fetchone()[0] == 2

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
        # One model, many paths. That is what a copy into a second registered
        # folder is, and what a duplicate after an interrupted move is.
        apply_migrations(hub)
        model_id = add_model(hub)
        for path in ("/a", "/b"):
            add_file(hub, model_id, add_folder(hub, path), "x.safetensors")
        assert hub.execute("SELECT COUNT(*) FROM model_file").fetchone()[0] == 2

    def test_a_location_cannot_name_a_model_that_does_not_exist(self, hub):
        # The FK is what keeps `model_file` from outliving its content row.
        apply_migrations(hub)
        folder_id = add_folder(hub, "/models")
        with pytest.raises(sqlite3.IntegrityError):
            add_file(hub, 9999, folder_id, "ghost.safetensors")


class TestUnknownIsFirstClass:
    """``unknown`` is shown, never promoted, and correctable in one statement."""

    def test_an_unknown_file_stores_as_unknown_and_is_not_an_adapter(self, hub):
        apply_migrations(hub)
        add_model(hub, file_kind="unknown", sha256="u", kind=None)
        add_model(hub, file_kind="adapter", sha256="a", kind="lora")

        assert hub.execute(
            "SELECT file_kind FROM model WHERE sha256 = 'u'"
        ).fetchone() == ("unknown",)
        adapters = hub.execute(
            "SELECT sha256 FROM model WHERE file_kind = 'adapter'"
        ).fetchall()
        assert adapters == [("a",)]

    def test_correcting_an_unknown_to_checkpoint_keeps_its_locations(self, hub):
        # The correction is the point of storing `unknown` rather than guessing.
        # Under the superseded shape it was a cross-table move; here it is one
        # UPDATE and the location rows never move.
        apply_migrations(hub)
        model_id = add_model(hub, file_kind="unknown", sha256="u", kind=None)
        add_file(hub, model_id, add_folder(hub, "/a"), "vae.safetensors")
        add_file(hub, model_id, add_folder(hub, "/b"), "vae.safetensors")
        hub.commit()

        hub.execute(
            "UPDATE model SET file_kind = 'checkpoint' WHERE id = ?", (model_id,)
        )

        assert hub.execute("SELECT file_kind FROM model").fetchall() == [
            ("checkpoint",)
        ]
        assert hub.execute(
            "SELECT COUNT(*) FROM model_file WHERE model_id = ?", (model_id,)
        ).fetchone() == (2,)


class TestComponentRoleBackfill:
    """Re-filing support files that were registered before they had kinds.

    Every row on an existing shelf was classified by tensor markers and a
    parameter count, and neither can see a VAE or a text encoder. The folder
    each file sits in can, and it is already in the hub - so this is a data
    backfill over stored columns, not a rescan.

    It runs exactly once per hub, tracked in ``PRAGMA user_version`` rather than
    in ``schema_version``: the schema steps are re-applied on every open by
    design, and a step that rewrote an owner-correctable value on every restart
    would undo the correction each time.
    """

    def test_a_small_vae_stored_as_unknown_is_refiled(self, hub):
        apply_migrations(hub)
        hub.execute("PRAGMA user_version = 0")
        model_id = add_model(hub, file_kind="unknown", sha256="v", kind=None)
        add_file(hub, model_id, add_folder(hub, "/models"), "VAE/sdxl_vae.safetensors")
        hub.commit()

        apply_migrations(hub)

        assert hub.execute(
            "SELECT file_kind FROM model WHERE id = ?", (model_id,)
        ).fetchone() == ("vae",)

    def test_a_large_text_encoder_stored_as_checkpoint_is_refiled(self, hub):
        # The 131 GB case: a T5-class encoder clears the checkpoint threshold,
        # so it was filed as a base model and counted as one.
        apply_migrations(hub)
        hub.execute("PRAGMA user_version = 0")
        model_id = add_model(hub, file_kind="checkpoint", sha256="t", kind=None)
        add_file(
            hub,
            model_id,
            add_folder(hub, "/models"),
            "TextEncoders/t5xxl_fp16.safetensors",
        )
        hub.commit()

        apply_migrations(hub)

        assert hub.execute(
            "SELECT file_kind FROM model WHERE id = ?", (model_id,)
        ).fetchone() == ("text_encoder",)

    def test_an_adapter_is_never_touched(self, hub):
        # Markers are positive evidence. A LoRA kept beside the VAEs is a
        # misfiled LoRA, and the backfill must not "fix" it into a VAE.
        apply_migrations(hub)
        hub.execute("PRAGMA user_version = 0")
        model_id = add_model(hub, file_kind="adapter", sha256="a", kind="lora")
        add_file(hub, model_id, add_folder(hub, "/models"), "VAE/mislaid.safetensors")
        hub.commit()

        apply_migrations(hub)

        assert hub.execute(
            "SELECT file_kind FROM model WHERE id = ?", (model_id,)
        ).fetchone() == ("adapter",)

    def test_copies_that_disagree_leave_the_row_alone(self, hub):
        # One copy under `VAE/` and one loose in a mixed folder is not evidence.
        # Resolving it by picking a side would be a guess wearing a fact's face.
        apply_migrations(hub)
        hub.execute("PRAGMA user_version = 0")
        model_id = add_model(hub, file_kind="unknown", sha256="d", kind=None)
        add_file(hub, model_id, add_folder(hub, "/a"), "VAE/thing.safetensors")
        add_file(hub, model_id, add_folder(hub, "/b"), "Downloads/thing.safetensors")
        hub.commit()

        apply_migrations(hub)

        assert hub.execute(
            "SELECT file_kind FROM model WHERE id = ?", (model_id,)
        ).fetchone() == ("unknown",)

    def test_a_tombstone_cannot_veto_what_the_live_copies_agree_on(self, hub):
        # `model_file` is also the tombstone, so a copy deleted months ago leaves
        # its row behind. Counting that dead path as a dissenting voice would
        # block the re-filing every present copy agrees on.
        apply_migrations(hub)
        hub.execute("PRAGMA user_version = 0")
        model_id = add_model(hub, file_kind="unknown", sha256="t", kind=None)
        add_file(hub, model_id, add_folder(hub, "/a"), "VAE/thing.safetensors")
        add_file(
            hub,
            model_id,
            add_folder(hub, "/b"),
            "Downloads/thing.safetensors",
            state="missing",
        )
        hub.commit()

        apply_migrations(hub)

        assert hub.execute(
            "SELECT file_kind FROM model WHERE id = ?", (model_id,)
        ).fetchone() == ("vae",)

    def test_present_copies_that_disagree_still_veto(self, hub):
        # Preferring the live copies is not the same as ignoring disagreement:
        # two folders we can currently see, naming different roles, is exactly
        # the case that must be left alone.
        apply_migrations(hub)
        hub.execute("PRAGMA user_version = 0")
        model_id = add_model(hub, file_kind="unknown", sha256="p", kind=None)
        add_file(hub, model_id, add_folder(hub, "/a"), "VAE/thing.safetensors")
        add_file(hub, model_id, add_folder(hub, "/b"), "Downloads/thing.safetensors")
        hub.commit()

        apply_migrations(hub)

        assert hub.execute(
            "SELECT file_kind FROM model WHERE id = ?", (model_id,)
        ).fetchone() == ("unknown",)

    def test_a_model_with_no_present_copy_is_still_refiled(self, hub):
        # An unplugged drive. This runs once, so skipping it here would leave
        # that drive's support files mislabelled for good.
        apply_migrations(hub)
        hub.execute("PRAGMA user_version = 0")
        model_id = add_model(hub, file_kind="checkpoint", sha256="o", kind=None)
        add_file(
            hub,
            model_id,
            add_folder(hub, "/detached"),
            "TextEncoders/t5xxl.safetensors",
            state="unreachable",
        )
        hub.commit()

        apply_migrations(hub)

        assert hub.execute(
            "SELECT file_kind FROM model WHERE id = ?", (model_id,)
        ).fetchone() == ("text_encoder",)

    def test_it_runs_once_and_never_undoes_a_correction(self, hub):
        # The reason this is not a schema step. `_apply_v2` re-runs on every
        # open; if this did too, an owner who corrected a row would find it
        # reverted on their next restart, silently, forever.
        apply_migrations(hub)
        hub.execute("PRAGMA user_version = 0")
        model_id = add_model(hub, file_kind="unknown", sha256="c", kind=None)
        add_file(hub, model_id, add_folder(hub, "/models"), "VAE/odd.safetensors")
        hub.commit()

        apply_migrations(hub)
        assert hub.execute(
            "SELECT file_kind FROM model WHERE id = ?", (model_id,)
        ).fetchone() == ("vae",)

        # The owner disagrees: it is really a checkpoint someone filed oddly.
        hub.execute(
            "UPDATE model SET file_kind = 'checkpoint' WHERE id = ?", (model_id,)
        )
        hub.commit()

        apply_migrations(hub)

        assert hub.execute(
            "SELECT file_kind FROM model WHERE id = ?", (model_id,)
        ).fetchone() == ("checkpoint",)

    def test_a_fresh_hub_records_the_backfill_as_done(self, hub):
        apply_migrations(hub)

        assert hub.execute("PRAGMA user_version").fetchone()[0] == CURRENT_DATA_VERSION


class TestTombstone:
    """Why folder removal needs no confirmation prompt.

    Removing a folder drops ``model_file`` rows and keeps the ``model`` row, so
    the display name the user typed survives and re-adding the folder re-links.
    Nothing a user authored is destroyed, which is the whole basis for not
    interrupting them with a dialog.
    """

    @pytest.mark.parametrize(
        "file_kind, sha256",
        (
            ("adapter", "h"),
            # The case the superseded shape could not do at all: a checkpoint
            # carried its path inline, so removing the folder either destroyed
            # the row or left it pointing at a folder that was gone.
            ("checkpoint", None),
        ),
    )
    def test_dropping_a_folders_files_keeps_the_model_and_its_curation(
        self, hub, file_kind, sha256
    ):
        apply_migrations(hub)
        model_id = add_model(
            hub, file_kind=file_kind, sha256=sha256, display_name="Clementine v3"
        )
        folder_id = add_folder(hub, "/models")
        add_file(hub, model_id, folder_id, "clem.safetensors")
        hub.commit()

        hub.execute("DELETE FROM model_file WHERE model_folder_id = ?", (folder_id,))
        hub.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))
        hub.commit()

        assert hub.execute("SELECT display_name FROM model").fetchone() == (
            "Clementine v3",
        )
        assert hub.execute("SELECT COUNT(*) FROM model_file").fetchone()[0] == 0
