"""Tests for the hub database, the library registry, and the library CLI.

Covers the invariants the multi-library plan makes load-bearing: one active
library, one registration per path, credentials in an attached vault stay
inert, ``detach`` never touches files, and the hub file is not group- or
world-readable.
"""

import os
import shutil
import sqlite3
import stat
import uuid

import pytest

from pixlstash.hub.db import HUB_FILE_MODE, HubDatabase
from pixlstash.hub.registry import (
    ActiveLibraryError,
    LibraryError,
    LibraryExistsError,
    LibraryNotFoundError,
    LibraryRegistry,
    NotAVaultError,
    validate_vault_folder,
)
from pixlstash.hub.schema import CURRENT_SCHEMA_VERSION, read_schema_version
from pixlstash.cli import main as cli_main
from pixlstash.trusted_sqlite import TrustedSQLiteLocation


def make_vault_folder(root, name="library", *, with_credentials=False):
    """Create a folder holding a minimal but valid-looking vault.db.

    Builds the marker tables by hand rather than running the real vault
    initialisation: these tests are about the registry, and a real vault costs
    an Alembic run per test.
    """
    folder = os.path.join(str(root), name)
    os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(os.path.join(folder, "vault.db"))
    conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES ('0001_baseline')")
    conn.execute("CREATE TABLE picture (id INTEGER PRIMARY KEY, file_path TEXT)")
    if with_credentials:
        # A vault copied in from another installation still carries the old
        # identity tables; attaching it must not import them.
        conn.execute(
            "CREATE TABLE user (id INTEGER PRIMARY KEY, username TEXT, "
            "password_hash TEXT)"
        )
        conn.execute("INSERT INTO user VALUES (1, 'someone-else', 'a-real-hash')")
        conn.execute("CREATE TABLE usertoken (id INTEGER PRIMARY KEY, token_hash TEXT)")
        conn.execute("INSERT INTO usertoken VALUES (1, 'another-real-hash')")
    conn.commit()
    conn.close()
    return folder


def make_legacy_vault_folder(root, name="legacy"):
    """A vault from before PixlStash adopted Alembic: no ``alembic_version``.

    This is the ``0001_baseline`` table set as a December-2025 install left it.
    ``VaultDatabase`` opens exactly this by stamping the baseline and upgrading,
    so the registry has to let it through - refusing it is what exited the
    backend during first-run setup instead.
    """
    folder = os.path.join(str(root), name)
    os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(os.path.join(folder, "vault.db"))
    for table in (
        "picture",
        "character",
        "face",
        "tag",
        "quality",
        "metadata",
        "pictureset",
        "picturesetmember",
        "conversation",
        "message",
    ):
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return folder


def set_vault_uuid(folder, value):
    """Give a test vault the ``library_settings`` fingerprint row."""
    conn = sqlite3.connect(os.path.join(folder, "vault.db"))
    conn.execute("CREATE TABLE IF NOT EXISTS library_settings (library_uuid TEXT)")
    conn.execute("DELETE FROM library_settings")
    conn.execute("INSERT INTO library_settings (library_uuid) VALUES (?)", (value,))
    conn.commit()
    conn.close()


def _add_token(hub, library_uuid, *, username="owner"):
    """Insert a user and a token stamped for *library_uuid*."""
    with hub.transaction() as conn:
        row = conn.execute("SELECT id FROM user").fetchone()
        if row is None:
            cursor = conn.execute("INSERT INTO user (username) VALUES (?)", (username,))
            user_id = cursor.lastrowid
        else:
            user_id = row[0]
        conn.execute(
            "INSERT INTO usertoken (user_id, library_uuid, token_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, library_uuid, "a-hash", "2026-08-01T00:00:00+00:00"),
        )


def _token_rows(hub, library_uuid):
    """Count the tokens stamped for a library."""
    return hub.connection.execute(
        "SELECT COUNT(*) FROM usertoken WHERE library_uuid = ?", (library_uuid,)
    ).fetchone()[0]


@pytest.fixture
def hub(tmp_path):
    """An open hub database in a temporary directory."""
    database = HubDatabase(str(tmp_path / "hub.db"))
    yield database
    database.close()


@pytest.fixture
def registry(hub):
    """A registry over the temporary hub."""
    return LibraryRegistry(hub)


class TestHubDatabase:
    def test_created_at_current_schema_version(self, hub):
        assert read_schema_version(hub.connection) == CURRENT_SCHEMA_VERSION

    def test_created_owner_only(self, tmp_path):
        path = str(tmp_path / "hub.db")
        HubDatabase(path).close()
        assert stat.S_IMODE(os.stat(path).st_mode) == HUB_FILE_MODE

    def test_server_warns_about_a_group_readable_hub(self, tmp_path, caplog):
        path = str(tmp_path / "hub.db")
        HubDatabase(path).close()
        os.chmod(path, 0o640)

        HubDatabase(path).close()
        assert any(
            "should be 600" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        )
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o640

    def test_cli_repairs_a_group_readable_hub(self, tmp_path):
        path = str(tmp_path / "hub.db")
        HubDatabase(path).close()
        os.chmod(path, 0o640)

        HubDatabase(path, repair_permissions=True).close()
        assert stat.S_IMODE(os.stat(path).st_mode) == HUB_FILE_MODE

    def test_reopening_is_idempotent(self, tmp_path):
        path = str(tmp_path / "hub.db")
        HubDatabase(path).close()
        second = HubDatabase(path)
        assert read_schema_version(second.connection) == CURRENT_SCHEMA_VERSION
        second.close()

    def test_wal_is_enabled_for_multi_process_access(self, hub):
        mode = hub.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    @pytest.mark.parametrize("earlier_lane_had_salt", [False, True])
    def test_v1_hub_migrates_to_v2_idempotently(self, tmp_path, earlier_lane_had_salt):
        path = str(tmp_path / "hub.db")
        first = HubDatabase(path)
        with first.transaction() as conn:
            conn.execute(
                "INSERT INTO library_uuid_issued VALUES "
                "('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'now', '/library')"
            )
            conn.execute(
                "INSERT INTO library (uuid, settings_salt, name, path, created_at, "
                "attached_at) VALUES "
                "('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'kept-salt', 'One', "
                "'/library', 'now', 'now')"
            )
            conn.execute("ALTER TABLE library DROP COLUMN identity_migration_state")
            if not earlier_lane_had_salt:
                conn.execute("ALTER TABLE library DROP COLUMN settings_salt")
            conn.execute("UPDATE schema_version SET version = 1")
        first.close()

        upgraded = HubDatabase(path)
        columns = {
            row[1] for row in upgraded.connection.execute("PRAGMA table_info(library)")
        }
        assert {"settings_salt", "identity_migration_state"} <= columns
        row = upgraded.connection.execute(
            "SELECT settings_salt, identity_migration_state FROM library"
        ).fetchone()
        assert row["identity_migration_state"] == "not_required"
        if earlier_lane_had_salt:
            assert row["settings_salt"] == "kept-salt"
        else:
            assert len(row["settings_salt"]) == 32
        assert read_schema_version(upgraded.connection) == 2
        upgraded.close()


class TestVaultValidation:
    def test_accepts_a_vault_folder(self, tmp_path):
        folder = make_vault_folder(tmp_path)
        assert validate_vault_folder(folder).endswith("vault.db")

    def test_rejects_a_folder_without_a_vault(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(NotAVaultError):
            validate_vault_folder(str(empty))

    def test_rejects_a_database_that_is_not_a_vault(self, tmp_path):
        folder = tmp_path / "impostor"
        folder.mkdir()
        conn = sqlite3.connect(str(folder / "vault.db"))
        conn.execute("CREATE TABLE something_else (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        with pytest.raises(NotAVaultError):
            validate_vault_folder(str(folder))

    def test_accepts_a_vault_from_before_alembic(self, tmp_path):
        """The opener stamps and upgrades these; the registry must not refuse them."""
        folder = make_legacy_vault_folder(tmp_path)

        assert validate_vault_folder(folder).endswith("vault.db")

    def test_a_lone_picture_table_is_still_not_a_vault(self, tmp_path):
        """The pre-Alembic allowance is a whole schema, not one familiar name."""
        folder = tmp_path / "impostor-with-pictures"
        folder.mkdir()
        conn = sqlite3.connect(str(folder / "vault.db"))
        conn.execute("CREATE TABLE picture (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        with pytest.raises(NotAVaultError) as caught:
            validate_vault_folder(str(folder))

        # "missing alembic_version" on its own reads as an old vault we could
        # upgrade, and the recovery dialog shows this text verbatim.
        assert "not a pre-Alembic one either" in str(caught.value)
        assert "character" in str(caught.value)

    def test_rejects_a_missing_folder(self, tmp_path):
        with pytest.raises(NotAVaultError):
            validate_vault_folder(str(tmp_path / "nope"))


class TestAttachAndList:
    def test_first_attached_library_becomes_active(self, registry, tmp_path):
        library = registry.attach(make_vault_folder(tmp_path, "first"))
        assert library.is_active
        assert registry.active_library().id == library.id

    def test_second_library_does_not_steal_active(self, registry, tmp_path):
        first = registry.attach(make_vault_folder(tmp_path, "first"))
        second = registry.attach(make_vault_folder(tmp_path, "second"))

        assert registry.active_library().id == first.id
        assert not second.is_active

    def test_name_defaults_to_the_folder_name(self, registry, tmp_path):
        library = registry.attach(make_vault_folder(tmp_path, "holiday-pics"))
        assert library.name == "holiday-pics"

    def test_explicit_name_wins(self, registry, tmp_path):
        library = registry.attach(
            make_vault_folder(tmp_path, "holiday-pics"), "Holiday 2026"
        )
        assert library.name == "Holiday 2026"

    def test_attaching_the_same_path_twice_is_refused(self, registry, tmp_path):
        folder = make_vault_folder(tmp_path)
        registry.attach(folder)
        with pytest.raises(LibraryExistsError):
            registry.attach(folder)

    def test_attaching_through_a_symlink_is_refused_as_a_duplicate(
        self, registry, tmp_path
    ):
        folder = make_vault_folder(tmp_path, "real")
        link = str(tmp_path / "link")
        os.symlink(folder, link)

        registry.attach(folder)
        # Same folder reached by another name: the resolved path collides, so
        # this must not become a second library over one vault.
        with pytest.raises(LibraryExistsError):
            registry.attach(link)

    def test_attach_ignores_credentials_embedded_in_the_vault(
        self, registry, hub, tmp_path
    ):
        folder = make_vault_folder(tmp_path, "foreign", with_credentials=True)
        registry.attach(folder)

        users = hub.connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
        tokens = hub.connection.execute("SELECT COUNT(*) FROM usertoken").fetchone()[0]
        assert (users, tokens) == (0, 0)

    def test_attach_does_not_write_to_the_foreign_vault(self, registry, tmp_path):
        folder = make_vault_folder(tmp_path, "foreign", with_credentials=True)
        vault = os.path.join(folder, "vault.db")
        before = os.stat(vault).st_mtime_ns
        digest_before = open(vault, "rb").read()

        registry.attach(folder)

        assert os.stat(vault).st_mtime_ns == before
        assert open(vault, "rb").read() == digest_before

    def test_list_puts_the_active_library_first(self, registry, tmp_path):
        registry.attach(make_vault_folder(tmp_path, "zebra"))
        second = registry.attach(make_vault_folder(tmp_path, "alpha"))
        registry.set_active(second.id)

        names = [library.name for library in registry.list_libraries()]
        assert names[0] == "alpha"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_create_makes_the_vault_db_owner_only_under_a_group_umask(
        self, registry, tmp_path
    ):
        """``registry.create`` is the second unguarded ``VaultDatabase`` site.

        Left to SQLite, the fresh ``vault.db`` would be 0664 under the
        Debian/Ubuntu umask 002; the pre-create inside ``VaultDatabase`` must
        cover this call site too, not only ``Vault.__init__``.
        """
        old_umask = os.umask(0o002)
        try:
            library = registry.create(str(tmp_path / "brand-new"))
        finally:
            os.umask(old_umask)

        vault_db = os.path.join(library.path, "vault.db")
        assert stat.S_IMODE(os.lstat(vault_db).st_mode) == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_create_makes_every_missing_component_private_under_a_group_umask(
        self, registry, tmp_path
    ):
        """W21 at the registry's own makedirs: intermediates too, not the leaf.

        ``pixlstash libraries create <deep new path>`` on stock Ubuntu (umask
        002) left the intermediate at 0775 and yielded a library the guarded
        open refused.
        """
        old_umask = os.umask(0o002)
        try:
            os.chmod(tmp_path, 0o700)
            library = registry.create(str(tmp_path / "deep" / "brand-new"))
        finally:
            os.umask(old_umask)

        for directory in (tmp_path / "deep", tmp_path / "deep" / "brand-new"):
            mode = stat.S_IMODE(os.lstat(directory).st_mode)
            assert mode == 0o700, f"{directory} is {oct(mode)}, expected 0o700"
        TrustedSQLiteLocation.open(os.path.join(library.path, "vault.db")).close()

    def test_unreachable_library_is_listed_and_flagged(self, registry, tmp_path):
        folder = make_vault_folder(tmp_path, "removable")
        library = registry.attach(folder)
        os.remove(os.path.join(folder, "vault.db"))

        listed = {entry.id: entry for entry in registry.list_libraries()}
        assert library.id in listed
        assert not listed[library.id].is_reachable


class TestActiveLibrary:
    def test_exactly_one_library_is_active_after_switching(self, registry, tmp_path):
        first = registry.attach(make_vault_folder(tmp_path, "first"))
        second = registry.attach(make_vault_folder(tmp_path, "second"))

        registry.set_active(second.id)

        active = [entry for entry in registry.list_libraries() if entry.is_active]
        assert [entry.id for entry in active] == [second.id]
        assert not registry.get(first.id).is_active

    def test_the_single_active_invariant_is_enforced_by_the_database(
        self, registry, hub, tmp_path
    ):
        registry.attach(make_vault_folder(tmp_path, "first"))
        second = registry.attach(make_vault_folder(tmp_path, "second"))

        # Bypass the registry entirely: the partial unique index, not the
        # application code, is what makes two active rows impossible.
        with pytest.raises(sqlite3.IntegrityError):
            hub.connection.execute(
                "UPDATE library SET is_active = 1 WHERE id = ?", (second.id,)
            )

    def test_switching_to_an_unknown_library_is_refused(self, registry, tmp_path):
        registry.attach(make_vault_folder(tmp_path))
        with pytest.raises(LibraryNotFoundError):
            registry.set_active("does-not-exist")


class TestDetach:
    def test_detach_refuses_the_active_library(self, registry, tmp_path):
        library = registry.attach(make_vault_folder(tmp_path))
        with pytest.raises(ActiveLibraryError):
            registry.detach(library.id)
        assert len(registry.list_libraries()) == 1

    def test_detach_removes_the_row_but_no_files(self, registry, tmp_path):
        registry.attach(make_vault_folder(tmp_path, "first"))
        folder = make_vault_folder(tmp_path, "second")
        second = registry.attach(folder)

        registry.detach(second.id)

        assert [entry.name for entry in registry.list_libraries()] == ["first"]
        assert os.path.isfile(os.path.join(folder, "vault.db"))

    def test_a_detached_library_can_be_attached_again(self, registry, tmp_path):
        registry.attach(make_vault_folder(tmp_path, "first"))
        folder = make_vault_folder(tmp_path, "second")
        registry.detach(registry.attach(folder).id)

        reattached = registry.attach(folder)
        assert reattached.name == "second"


class TestLibraryIdentity:
    """The uuid contract: stable, never reused, and never taken from a vault."""

    def test_every_library_gets_a_uuid(self, registry, tmp_path):
        library = registry.attach(make_vault_folder(tmp_path))
        assert uuid.UUID(library.uuid).version == 4

    def test_uuids_are_distinct(self, registry, tmp_path):
        first = registry.attach(make_vault_folder(tmp_path, "one"))
        second = registry.attach(make_vault_folder(tmp_path, "two"))
        assert first.uuid != second.uuid

    def test_uuid_survives_rename_and_switch(self, registry, tmp_path):
        library = registry.attach(make_vault_folder(tmp_path, "before"))
        registry.attach(make_vault_folder(tmp_path, "other"))

        renamed = registry.rename(library.id, "after")
        switched = registry.set_active(library.id)

        assert renamed.uuid == library.uuid
        assert switched.uuid == library.uuid

    def test_every_issued_uuid_is_recorded_in_the_ledger(self, registry, hub, tmp_path):
        first = registry.attach(make_vault_folder(tmp_path, "one"))
        second = registry.attach(make_vault_folder(tmp_path, "two"))

        issued = {
            row[0]
            for row in hub.connection.execute("SELECT uuid FROM library_uuid_issued")
        }
        assert {first.uuid, second.uuid} <= issued

    def test_an_issued_uuid_can_never_be_issued_again(self, registry, hub, tmp_path):
        library = registry.attach(make_vault_folder(tmp_path))

        # The ledger is what enforces this after a row is gone, so assert on the
        # ledger rather than on the live table.
        with pytest.raises(sqlite3.IntegrityError):
            hub.connection.execute(
                "INSERT INTO library_uuid_issued (uuid, issued_at) VALUES (?, ?)",
                (library.uuid, "2026-08-01T00:00:00+00:00"),
            )

    def test_an_integer_id_may_be_reused_but_a_uuid_may_not(
        self, registry, hub, tmp_path
    ):
        """The hazard the uuid exists for, demonstrated end to end."""
        registry.attach(make_vault_folder(tmp_path, "keeper"))
        second = registry.attach(make_vault_folder(tmp_path, "goes-away"))
        original_uuid = second.uuid
        registry.detach(second.id)

        third = registry.attach(make_vault_folder(tmp_path, "brand-new"))

        # Whatever happens to integer ids, the identity a token would carry is
        # never handed to a different library.
        assert third.uuid != original_uuid


class TestDetachRetainsTokens:
    def test_detach_keeps_tokens_and_they_come_back_on_reattach(
        self, registry, hub, tmp_path
    ):
        registry.attach(make_vault_folder(tmp_path, "first"))
        folder = make_vault_folder(tmp_path, "second")
        library = registry.attach(folder)
        _add_token(hub, library.uuid)

        registry.detach(library.id)
        assert _token_rows(hub, library.uuid) == 1, "detach must not revoke tokens"

        revived = registry.attach(folder)
        assert revived.uuid == library.uuid
        assert _token_rows(hub, library.uuid) == 1

    def test_a_library_with_tokens_cannot_be_deleted(self, registry, hub, tmp_path):
        registry.attach(make_vault_folder(tmp_path, "first"))
        library = registry.attach(make_vault_folder(tmp_path, "second"))
        _add_token(hub, library.uuid)

        # The foreign key is the second guard behind "a uuid is never reused":
        # the row cannot go away while anything still references it.
        with pytest.raises(sqlite3.IntegrityError):
            hub.connection.execute("DELETE FROM library WHERE id = ?", (library.id,))

    def test_a_detached_library_is_not_listed(self, registry, tmp_path):
        registry.attach(make_vault_folder(tmp_path, "first"))
        library = registry.attach(make_vault_folder(tmp_path, "second"))
        registry.detach(library.id)

        assert [entry.name for entry in registry.list_libraries()] == ["first"]
        assert library.uuid in {
            entry.uuid for entry in registry.list_libraries(include_detached=True)
        }


class TestFingerprintGuardsRevival:
    def test_same_library_back_at_the_same_path_revives(self, registry, hub, tmp_path):
        registry.attach(make_vault_folder(tmp_path, "first"))
        folder = make_vault_folder(tmp_path, "second")
        set_vault_uuid(folder, "11111111-1111-4111-8111-111111111111")
        library = registry.attach(folder)
        _add_token(hub, library.uuid)
        registry.detach(library.id)

        revived = registry.attach(folder)

        assert revived.uuid == library.uuid
        assert _token_rows(hub, library.uuid) == 1

    def test_a_different_library_at_the_same_path_does_not_revive(
        self, registry, hub, tmp_path
    ):
        """The reason the fingerprint exists: share links must not come back
        pointing at content they were never issued for."""
        registry.attach(make_vault_folder(tmp_path, "first"))
        folder = make_vault_folder(tmp_path, "second")
        set_vault_uuid(folder, "11111111-1111-4111-8111-111111111111")
        library = registry.attach(folder)
        _add_token(hub, library.uuid)
        registry.detach(library.id)

        # Same path, different library.
        shutil.rmtree(folder)
        make_vault_folder(tmp_path, "second")
        set_vault_uuid(folder, "22222222-2222-4222-8222-222222222222")

        replacement = registry.attach(folder)

        assert replacement.uuid != library.uuid
        # The old tokens survive, still stamped for the old library, and inert.
        assert _token_rows(hub, library.uuid) == 1
        assert _token_rows(hub, replacement.uuid) == 0

    def test_a_fingerprintless_library_still_revives_on_path(
        self, registry, hub, tmp_path
    ):
        """Libraries that predate fingerprints keep the old path-only behaviour."""
        registry.attach(make_vault_folder(tmp_path, "first"))
        folder = make_vault_folder(tmp_path, "second")
        library = registry.attach(folder)
        registry.detach(library.id)

        assert registry.attach(folder).uuid == library.uuid

    def test_the_fingerprint_is_never_used_as_the_token_binding(
        self, registry, tmp_path
    ):
        """A folder cannot claim an identity tokens are already stamped with."""
        registry.attach(make_vault_folder(tmp_path, "first"))
        existing = registry.attach(make_vault_folder(tmp_path, "second"))

        # A hostile library arrives claiming the other library's hub uuid.
        hostile = make_vault_folder(tmp_path, "hostile")
        set_vault_uuid(hostile, existing.uuid)

        attached = registry.attach(hostile)
        assert attached.uuid != existing.uuid


class TestRelocate:
    def test_relocate_keeps_the_uuid_and_its_tokens(self, registry, hub, tmp_path):
        folder = make_vault_folder(tmp_path, "original")
        library = registry.attach(folder)
        _add_token(hub, library.uuid)

        moved_to = os.path.join(str(tmp_path), "moved")
        shutil.move(folder, moved_to)

        relocated = registry.relocate(library.id, moved_to)

        assert relocated.uuid == library.uuid
        assert relocated.path == os.path.realpath(moved_to)
        assert _token_rows(hub, library.uuid) == 1

    def test_relocate_to_an_occupied_path_is_refused(self, registry, tmp_path):
        first = registry.attach(make_vault_folder(tmp_path, "first"))
        second_folder = make_vault_folder(tmp_path, "second")
        registry.attach(second_folder)

        with pytest.raises(LibraryExistsError):
            registry.relocate(first.id, second_folder)

    def test_relocate_to_a_non_vault_is_refused(self, registry, tmp_path):
        library = registry.attach(make_vault_folder(tmp_path))
        empty = tmp_path / "empty"
        empty.mkdir()

        with pytest.raises(NotAVaultError):
            registry.relocate(library.id, str(empty))


class TestOverlapDetection:
    def test_nested_library_is_reported_as_overlapping(self, registry, tmp_path):
        outer = make_vault_folder(tmp_path, "outer")
        registry.attach(outer)
        inner = make_vault_folder(outer, "inner")

        assert [entry.name for entry in registry.overlapping(inner)] == ["outer"]

    def test_unrelated_library_does_not_overlap(self, registry, tmp_path):
        registry.attach(make_vault_folder(tmp_path, "one"))
        other = make_vault_folder(tmp_path, "two")

        assert registry.overlapping(other) == []


class TestRename:
    def test_rename_changes_the_label(self, registry, tmp_path):
        library = registry.attach(make_vault_folder(tmp_path, "typo"))
        assert registry.rename(library.id, "Client work").name == "Client work"

    def test_rename_to_an_empty_name_is_refused(self, registry, tmp_path):
        library = registry.attach(make_vault_folder(tmp_path))
        with pytest.raises(LibraryError):
            registry.rename(library.id, "   ")


class TestCli:
    def test_list_on_an_empty_hub_explains_how_to_add_one(self, tmp_path, capsys):
        code = cli_main(["--hub", str(tmp_path / "hub.db"), "libraries", "list"])

        assert code == 0
        assert "attach" in capsys.readouterr().out

    def test_attach_then_list_shows_the_active_marker(self, tmp_path, capsys):
        hub_path = str(tmp_path / "hub.db")
        folder = make_vault_folder(tmp_path, "family")

        assert cli_main(["--hub", hub_path, "libraries", "attach", folder]) == 0
        assert cli_main(["--hub", hub_path, "libraries", "list"]) == 0

        out = capsys.readouterr().out
        assert "family" in out
        assert "* " in out

    def test_attach_of_a_non_vault_fails_with_a_usable_message(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()

        code = cli_main(
            ["--hub", str(tmp_path / "hub.db"), "libraries", "attach", str(empty)]
        )

        assert code == 1
        assert "vault.db" in capsys.readouterr().err

    def test_detach_of_the_active_library_is_refused_and_says_so(
        self, tmp_path, capsys
    ):
        hub_path = str(tmp_path / "hub.db")
        folder = make_vault_folder(tmp_path, "only")
        cli_main(["--hub", hub_path, "libraries", "attach", folder])
        capsys.readouterr()

        code = cli_main(["--hub", hub_path, "libraries", "detach", "only"])

        err = capsys.readouterr().err
        assert code == 1
        assert "active library" in err
        assert "No files have been changed." in err

    def test_detach_reassures_that_files_are_kept(self, tmp_path, capsys):
        hub_path = str(tmp_path / "hub.db")
        cli_main(
            ["--hub", hub_path, "libraries", "attach", make_vault_folder(tmp_path, "a")]
        )
        folder = make_vault_folder(tmp_path, "b")
        cli_main(["--hub", hub_path, "libraries", "attach", folder])
        capsys.readouterr()

        code = cli_main(["--hub", hub_path, "libraries", "detach", "b"])

        out = capsys.readouterr().out
        assert code == 0
        assert "No files were removed." in out
        assert os.path.isfile(os.path.join(folder, "vault.db"))

    def test_overlap_is_a_warning_not_a_refusal(self, tmp_path, capsys):
        hub_path = str(tmp_path / "hub.db")
        outer = make_vault_folder(tmp_path, "outer")
        cli_main(["--hub", hub_path, "libraries", "attach", outer])
        inner = make_vault_folder(outer, "inner")
        capsys.readouterr()

        code = cli_main(["--hub", hub_path, "libraries", "attach", inner])

        captured = capsys.readouterr()
        assert code == 0
        assert "overlaps" in captured.err
