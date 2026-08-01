"""Tests for the hub database, the library registry, and the library CLI.

Covers the invariants the multi-library plan makes load-bearing: one active
library, one registration per path, credentials in an attached vault stay
inert, ``detach`` never touches files, and the hub file is not group- or
world-readable.
"""

import os
import sqlite3
import stat

import pytest

from pixlstash.hub.db import HUB_FILE_MODE, HubDatabase, HubPermissionError
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
from pixlstash.libraries import main as libraries_main


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

    def test_server_refuses_a_group_readable_hub(self, tmp_path):
        path = str(tmp_path / "hub.db")
        HubDatabase(path).close()
        os.chmod(path, 0o640)

        with pytest.raises(HubPermissionError):
            HubDatabase(path)

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
        code = libraries_main(["--hub", str(tmp_path / "hub.db"), "list"])

        assert code == 0
        assert "attach" in capsys.readouterr().out

    def test_attach_then_list_shows_the_active_marker(self, tmp_path, capsys):
        hub_path = str(tmp_path / "hub.db")
        folder = make_vault_folder(tmp_path, "family")

        assert libraries_main(["--hub", hub_path, "attach", folder]) == 0
        assert libraries_main(["--hub", hub_path, "list"]) == 0

        out = capsys.readouterr().out
        assert "family" in out
        assert "* " in out

    def test_attach_of_a_non_vault_fails_with_a_usable_message(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()

        code = libraries_main(["--hub", str(tmp_path / "hub.db"), "attach", str(empty)])

        assert code == 1
        assert "vault.db" in capsys.readouterr().err

    def test_detach_of_the_active_library_is_refused_and_says_so(
        self, tmp_path, capsys
    ):
        hub_path = str(tmp_path / "hub.db")
        folder = make_vault_folder(tmp_path, "only")
        libraries_main(["--hub", hub_path, "attach", folder])
        capsys.readouterr()

        code = libraries_main(["--hub", hub_path, "detach", "only"])

        err = capsys.readouterr().err
        assert code == 1
        assert "active library" in err
        assert "No files have been changed." in err

    def test_detach_reassures_that_files_are_kept(self, tmp_path, capsys):
        hub_path = str(tmp_path / "hub.db")
        libraries_main(["--hub", hub_path, "attach", make_vault_folder(tmp_path, "a")])
        folder = make_vault_folder(tmp_path, "b")
        libraries_main(["--hub", hub_path, "attach", folder])
        capsys.readouterr()

        code = libraries_main(["--hub", hub_path, "detach", "b"])

        out = capsys.readouterr().out
        assert code == 0
        assert "No files were removed." in out
        assert os.path.isfile(os.path.join(folder, "vault.db"))

    def test_overlap_is_a_warning_not_a_refusal(self, tmp_path, capsys):
        hub_path = str(tmp_path / "hub.db")
        outer = make_vault_folder(tmp_path, "outer")
        libraries_main(["--hub", hub_path, "attach", outer])
        inner = make_vault_folder(outer, "inner")
        capsys.readouterr()

        code = libraries_main(["--hub", hub_path, "attach", inner])

        captured = capsys.readouterr()
        assert code == 0
        assert "overlaps" in captured.err
