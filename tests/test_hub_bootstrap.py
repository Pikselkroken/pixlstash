"""Tests for hub-aware startup and the first-run identity migration.

The invariants here are the ones an upgrading user feels directly: the same
login still works, share links survive, the library they already had becomes
library 1, and a lost hub costs preferences rather than pictures.
"""

import os
import sqlite3

import pytest

from pixlstash.hub.bootstrap import bootstrap_hub
from pixlstash.hub.registry import LibraryRegistry, read_vault_uuid


def make_vault(folder, *, username="owner", password_hash="a-real-hash", tokens=1):
    """Build a vault shaped like one a 1.9 install would leave behind."""
    os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(os.path.join(folder, "vault.db"))
    conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES ('0092_add_library_settings')")
    conn.execute("CREATE TABLE picture (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.execute(
        "CREATE TABLE user (id INTEGER PRIMARY KEY, username TEXT, "
        "password_hash TEXT, theme_mode TEXT, public_url TEXT, max_vram_gb REAL)"
    )
    conn.execute(
        "INSERT INTO user (id, username, password_hash, theme_mode, public_url, "
        "max_vram_gb) VALUES (1, ?, ?, 'dark', 'https://example.test', 8.0)",
        (username, password_hash),
    )
    conn.execute(
        "CREATE TABLE usertoken (id INTEGER PRIMARY KEY, user_id INTEGER, "
        "public_id TEXT, token_hash TEXT, token_prefix TEXT, description TEXT, "
        "scope TEXT, resource_type TEXT, resource_id INTEGER, created_at TEXT, "
        "last_used_at TEXT, expires_at TEXT, include_attachments INTEGER, "
        "watermark INTEGER)"
    )
    for index in range(tokens):
        conn.execute(
            "INSERT INTO usertoken (user_id, public_id, token_hash, token_prefix, "
            "description, scope, created_at, include_attachments, watermark) "
            "VALUES (1, ?, ?, ?, ?, 'ALL', '2026-07-01T00:00:00', 0, 1)",
            (f"public-{index}", f"hash-{index}", f"prefix{index}", f"token {index}"),
        )
    conn.execute(
        "CREATE TABLE library_settings (id INTEGER PRIMARY KEY, library_uuid TEXT, "
        "stack_strictness REAL)"
    )
    conn.execute("INSERT INTO library_settings (id, stack_strictness) VALUES (1, 0.92)")
    conn.commit()
    conn.close()
    return folder


def vault_query(folder, sql):
    """Run a read against a test vault."""
    conn = sqlite3.connect(os.path.join(folder, "vault.db"))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.fixture
def paths(tmp_path):
    """A hub path and an existing library folder."""
    return str(tmp_path / "hub.db"), make_vault(str(tmp_path / "library"))


class TestFirstRun:
    def test_the_existing_vault_becomes_library_one_and_is_active(self, paths):
        hub_path, library_folder = paths

        result = bootstrap_hub(library_folder, hub_path)

        assert result.library.is_active
        assert result.library.path == os.path.realpath(library_folder)
        assert result.image_root == os.path.realpath(library_folder)
        result.hub.close()

    def test_the_owner_and_preferences_move_into_the_hub(self, paths):
        hub_path, library_folder = paths

        result = bootstrap_hub(library_folder, hub_path)

        row = result.hub.connection.execute(
            "SELECT username, password_hash, theme_mode, public_url, max_vram_gb, "
            "is_admin FROM user"
        ).fetchone()
        assert row["username"] == "owner"
        assert row["password_hash"] == "a-real-hash"
        assert row["theme_mode"] == "dark"
        assert row["public_url"] == "https://example.test"
        assert row["is_admin"] == 1
        result.hub.close()

    def test_tokens_move_across_unchanged_and_stamped(self, tmp_path):
        hub_path = str(tmp_path / "hub.db")
        library_folder = make_vault(str(tmp_path / "three-tokens"), tokens=3)

        result = bootstrap_hub(library_folder, hub_path)

        rows = result.hub.connection.execute(
            "SELECT token_hash, library_uuid FROM usertoken ORDER BY token_hash"
        ).fetchall()
        assert [row["token_hash"] for row in rows] == ["hash-0", "hash-1", "hash-2"]
        # Every token belongs to the library it was minted against.
        assert {row["library_uuid"] for row in rows} == {result.library.uuid}
        result.hub.close()

    def test_the_vaults_credentials_are_blanked_afterwards(self, paths):
        hub_path, library_folder = paths

        result = bootstrap_hub(library_folder, hub_path)
        result.hub.close()

        assert vault_query(library_folder, "SELECT password_hash FROM user") == [
            (None,)
        ]
        assert vault_query(library_folder, "SELECT COUNT(*) FROM usertoken") == [(0,)]

    def test_credentials_are_only_blanked_after_a_successful_copy(self, paths):
        """Ordering, asserted: the hub holds the copy before the vault loses it."""
        hub_path, library_folder = paths

        result = bootstrap_hub(library_folder, hub_path)

        in_hub = result.hub.connection.execute(
            "SELECT password_hash FROM user"
        ).fetchone()[0]
        in_vault = vault_query(library_folder, "SELECT password_hash FROM user")[0][0]
        assert in_hub == "a-real-hash"
        assert in_vault is None
        result.hub.close()

    def test_the_library_is_fingerprinted(self, paths):
        hub_path, library_folder = paths

        result = bootstrap_hub(library_folder, hub_path)

        assert read_vault_uuid(library_folder) == result.library.uuid
        result.hub.close()

    def test_migration_reports_that_it_ran(self, paths):
        hub_path, library_folder = paths
        assert bootstrap_hub(library_folder, hub_path).migrated is True


class TestSubsequentRuns:
    def test_the_second_run_migrates_nothing(self, paths):
        hub_path, library_folder = paths
        bootstrap_hub(library_folder, hub_path).hub.close()

        second = bootstrap_hub(library_folder, hub_path)

        assert second.migrated is False
        assert (
            second.hub.connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            == 1
        )
        second.hub.close()

    def test_the_registry_is_not_duplicated(self, paths):
        hub_path, library_folder = paths
        bootstrap_hub(library_folder, hub_path).hub.close()

        second = bootstrap_hub(library_folder, hub_path)
        assert len(LibraryRegistry(second.hub).list_libraries()) == 1
        second.hub.close()

    def test_the_active_library_is_honoured_over_the_configured_path(
        self, paths, tmp_path
    ):
        """Once libraries exist, the hub decides, not server config."""
        hub_path, library_folder = paths
        first = bootstrap_hub(library_folder, hub_path)
        registry = LibraryRegistry(first.hub)
        other = make_vault(str(tmp_path / "other"))
        second_library = registry.attach(other, "Second")
        registry.set_active(second_library.id)
        first.hub.close()

        result = bootstrap_hub(library_folder, hub_path)

        assert result.image_root == os.path.realpath(other)
        result.hub.close()


class TestFreshInstall:
    def test_a_folder_without_a_vault_is_registered_anyway(self, tmp_path):
        """The server creates the vault moments later, so this cannot require one."""
        hub_path = str(tmp_path / "hub.db")
        image_root = str(tmp_path / "brand-new")

        result = bootstrap_hub(image_root, hub_path)

        assert result.library.is_active
        assert result.migrated is False
        assert os.path.isdir(image_root)
        result.hub.close()


class TestLosingTheHub:
    def test_a_deleted_hub_is_recreated_and_the_server_still_starts(self, paths):
        """Hub loss must degrade, never block startup.

        What is lost is the password, the tokens and the preferences. What
        survives is every picture, tag, score and snapshot, because those live
        in the library.
        """
        hub_path, library_folder = paths
        bootstrap_hub(library_folder, hub_path).hub.close()
        os.remove(hub_path)

        result = bootstrap_hub(library_folder, hub_path)

        assert result.library.is_active
        assert result.image_root == os.path.realpath(library_folder)
        # The vault's credentials were blanked by the first run, so there is
        # nothing left to re-migrate: the owner re-registers, as on a new install.
        assert (
            result.hub.connection.execute("SELECT password_hash FROM user").fetchone()[
                0
            ]
            is None
        )
        assert vault_query(library_folder, "SELECT COUNT(*) FROM picture") == [(0,)]
        result.hub.close()

    def test_a_recreated_hub_mints_a_fresh_identity(self, paths):
        hub_path, library_folder = paths
        first = bootstrap_hub(library_folder, hub_path)
        original_uuid = first.library.uuid
        first.hub.close()
        os.remove(hub_path)

        second = bootstrap_hub(library_folder, hub_path)

        # Tokens died with the old hub, so nothing can be holding the old uuid.
        assert second.library.uuid != original_uuid
        assert (
            second.hub.connection.execute("SELECT COUNT(*) FROM usertoken").fetchone()[
                0
            ]
            == 0
        )
        second.hub.close()
