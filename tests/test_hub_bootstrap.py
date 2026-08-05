"""Tests for hub-aware startup and the first-run identity migration.

The invariants here are the ones an upgrading user feels directly: the same
login still works, share links survive, the library they already had becomes
library 1, and a lost hub costs preferences rather than pictures.
"""

import os
import sqlite3
import stat
import threading
import time

import pytest

from pixlstash.hub.bootstrap import (
    HubBootstrapError,
    bootstrap_hub,
    finalize_opened_library,
    prepare_legacy_identity,
    prevalidate_library_fingerprint,
)
from pixlstash.hub.db import HubDatabase, HubPermissionError
from pixlstash.hub.registry import LibraryRegistry, read_vault_uuid


def make_vault(folder, *, username="owner", password_hash="a-real-hash", tokens=1):
    """Build a vault shaped like one a 1.9 install would leave behind."""
    os.makedirs(folder, exist_ok=True)
    os.chmod(folder, 0o700)
    conn = sqlite3.connect(os.path.join(folder, "vault.db"))
    conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES ('0098_add_library_settings')")
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


def bootstrap_upgrade(library_folder, hub_path, *, finalize=True):
    preparer_hub = HubDatabase(hub_path)
    prepare_legacy_identity(preparer_hub, library_folder)
    preparer_hub.close()
    result = bootstrap_hub(
        library_folder,
        hub_path,
    )
    if finalize:
        finalize_opened_library(result)
    return result


@pytest.fixture
def paths(tmp_path):
    """A hub path and an existing library folder."""
    return str(tmp_path / "hub.db"), make_vault(str(tmp_path / "library"))


class TestFirstRun:
    def test_the_existing_vault_becomes_library_one_and_is_active(self, paths):
        hub_path, library_folder = paths

        result = bootstrap_upgrade(library_folder, hub_path)

        assert result.library.is_active
        assert result.library.path == os.path.realpath(library_folder)
        assert result.image_root == os.path.realpath(library_folder)
        result.hub.close()

    def test_the_owner_and_preferences_move_into_the_hub(self, paths):
        hub_path, library_folder = paths

        result = bootstrap_upgrade(library_folder, hub_path)

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

        result = bootstrap_upgrade(library_folder, hub_path)

        rows = result.hub.connection.execute(
            "SELECT token_hash, library_uuid FROM usertoken ORDER BY token_hash"
        ).fetchall()
        assert [row["token_hash"] for row in rows] == ["hash-0", "hash-1", "hash-2"]
        # Every token belongs to the library it was minted against.
        assert {row["library_uuid"] for row in rows} == {result.library.uuid}
        result.hub.close()

    def test_the_vaults_credentials_are_blanked_afterwards(self, paths):
        hub_path, library_folder = paths

        result = bootstrap_upgrade(library_folder, hub_path)
        result.hub.close()

        assert vault_query(library_folder, "SELECT COUNT(*) FROM user") == [(0,)]
        assert vault_query(library_folder, "SELECT COUNT(*) FROM usertoken") == [(0,)]

    def test_credentials_are_only_blanked_after_a_successful_copy(self, paths):
        """Ordering, asserted: the hub holds the copy before the vault loses it."""
        hub_path, library_folder = paths

        result = bootstrap_upgrade(library_folder, hub_path)

        in_hub = result.hub.connection.execute(
            "SELECT password_hash FROM user"
        ).fetchone()[0]
        in_vault = vault_query(library_folder, "SELECT COUNT(*) FROM user")[0][0]
        assert in_hub == "a-real-hash"
        assert in_vault == 0
        result.hub.close()

    def test_the_library_is_fingerprinted(self, paths):
        hub_path, library_folder = paths

        result = bootstrap_upgrade(library_folder, hub_path)

        assert read_vault_uuid(library_folder) == result.library.uuid
        result.hub.close()

    def test_migration_reports_that_it_ran(self, paths):
        hub_path, library_folder = paths
        assert bootstrap_upgrade(library_folder, hub_path).migrated is True


class TestSubsequentRuns:
    def test_the_second_run_migrates_nothing(self, paths):
        hub_path, library_folder = paths
        bootstrap_upgrade(library_folder, hub_path).hub.close()

        second = bootstrap_hub(library_folder, hub_path)

        assert second.migrated is False
        assert (
            second.hub.connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            == 1
        )
        second.hub.close()

    def test_the_registry_is_not_duplicated(self, paths):
        hub_path, library_folder = paths
        bootstrap_upgrade(library_folder, hub_path).hub.close()

        second = bootstrap_hub(library_folder, hub_path)
        assert len(LibraryRegistry(second.hub).list_libraries()) == 1
        second.hub.close()

    def test_the_active_library_is_honoured_over_the_configured_path(
        self, paths, tmp_path
    ):
        """Once libraries exist, the hub decides, not server config."""
        hub_path, library_folder = paths
        first = bootstrap_upgrade(library_folder, hub_path)
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

    def test_hub_directory_is_created_private_under_a_group_writable_umask(
        self, tmp_path, monkeypatch
    ):
        """A stock Ubuntu umask must not lock the very first run out of its hub.

        umask 002 is the Debian/Ubuntu default (every user has their own group).
        Creating the config directory under it yields 0775, and
        ``TrustedSQLiteLocation`` refuses a group-writable ancestor, so the hub
        would refuse to open the directory it had just created. Every missing
        component must be 0700, not only the leaf: the guard walks the whole
        chain, so an intermediate created by ``parents=True`` fails it too.
        """
        monkeypatch.setattr(os, "umask", lambda _mask: 0o002)
        os.umask(0o002)
        try:
            os.chmod(tmp_path, 0o700)
            # Two missing components, so the leaf-only mode of
            # Path.mkdir(parents=True, mode=...) would leave "fresh" at 0775.
            hub_path = tmp_path / "fresh" / "nested" / "hub.db"

            hub = HubDatabase(str(hub_path))

            for directory in (hub_path.parent.parent, hub_path.parent):
                mode = stat.S_IMODE(directory.stat().st_mode)
                assert mode == 0o700, f"{directory} is {oct(mode)}, expected 0o700"
            hub.close()
        finally:
            os.umask(0o022)

    def test_existing_loose_hub_directory_is_reported_not_silently_repaired(
        self, tmp_path
    ):
        """A permission someone else loosened is an event the operator must see.

        Same rule ``check_file_mode`` follows for the hub file: the server
        raises rather than tightening in place, because silently fixing it hides
        that it ever happened.
        """
        os.chmod(tmp_path, 0o700)
        parent = tmp_path / "loose"
        parent.mkdir(mode=0o775)

        with pytest.raises(HubPermissionError, match="group/world-writable"):
            HubDatabase(str(parent / "hub.db"))

        assert stat.S_IMODE(parent.stat().st_mode) == 0o775

    def test_existing_foreign_vault_is_not_an_implicit_identity_source(self, tmp_path):
        hub_path = str(tmp_path / "hub.db")
        folder = make_vault(str(tmp_path / "foreign"), username="donor", tokens=2)

        result = bootstrap_hub(folder, hub_path)

        assert (
            result.hub.connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            == 0
        )
        assert vault_query(folder, "SELECT username, password_hash FROM user") == [
            ("donor", "a-real-hash")
        ]
        assert vault_query(folder, "SELECT COUNT(*) FROM usertoken") == [(2,)]
        result.engine.close()
        result.hub.close()


class TestExplicitLegacyPreparation:
    def test_preparation_records_path_digest_and_paired_pending_state(self, paths):
        from pixlstash.hub.db import HubDatabase

        hub_path, folder = paths
        hub = HubDatabase(hub_path)
        library = prepare_legacy_identity(hub, folder)

        operation = hub.fetchone(
            "SELECT source_path, length(payload_digest), state "
            "FROM identity_migration_operation WHERE library_uuid=?",
            (library.uuid,),
        )
        assert tuple(operation) == (os.path.realpath(folder), 64, "pending")
        assert (
            hub.fetchone(
                "SELECT identity_migration_state FROM library WHERE uuid=?",
                (library.uuid,),
            )[0]
            == "pending"
        )
        hub.close()

    def test_changed_identity_payload_is_not_imported(self, paths):
        from pixlstash.hub.db import HubDatabase

        hub_path, folder = paths
        hub = HubDatabase(hub_path)
        library = prepare_legacy_identity(hub, folder)
        hub.close()
        conn = sqlite3.connect(os.path.join(folder, "vault.db"))
        conn.execute("UPDATE user SET password_hash='substituted'")
        conn.commit()
        conn.close()

        with pytest.raises(HubBootstrapError, match="changed after approval"):
            bootstrap_hub(folder, hub_path)

        reopened = HubDatabase(hub_path)
        assert reopened.fetchone("SELECT COUNT(*) FROM user")[0] == 0
        assert (
            reopened.fetchone(
                "SELECT identity_migration_state FROM library WHERE uuid=?",
                (library.uuid,),
            )[0]
            == "pending"
        )
        assert (
            reopened.fetchone(
                "SELECT state FROM identity_migration_operation WHERE library_uuid=?",
                (library.uuid,),
            )[0]
            == "pending"
        )
        reopened.close()

    def test_missing_approved_source_remains_pending(self, paths):
        from pixlstash.hub.db import HubDatabase

        hub_path, folder = paths
        hub = HubDatabase(hub_path)
        library = prepare_legacy_identity(hub, folder)
        hub.close()
        os.remove(os.path.join(folder, "vault.db"))

        with pytest.raises(HubBootstrapError, match="missing.*remains pending"):
            bootstrap_hub(folder, hub_path)

        reopened = HubDatabase(hub_path)
        assert (
            reopened.fetchone(
                "SELECT identity_migration_state FROM library WHERE uuid=?",
                (library.uuid,),
            )[0]
            == "pending"
        )
        assert (
            reopened.fetchone(
                "SELECT state FROM identity_migration_operation WHERE library_uuid=?",
                (library.uuid,),
            )[0]
            == "pending"
        )
        reopened.close()

    def test_completed_operation_cannot_be_reauthorized(self, paths):
        hub_path, folder = paths
        result = bootstrap_upgrade(folder, hub_path)
        library_uuid = result.library.uuid
        result.engine.close()
        result.hub.close()

        from pixlstash.hub.db import HubDatabase

        hub = HubDatabase(hub_path)
        with pytest.raises(HubBootstrapError, match="already complete"):
            prepare_legacy_identity(hub, folder)
        assert (
            hub.fetchone(
                "SELECT state FROM identity_migration_operation WHERE library_uuid=?",
                (library_uuid,),
            )[0]
            == "complete"
        )
        assert (
            hub.fetchone(
                "SELECT identity_migration_state FROM library WHERE uuid=?",
                (library_uuid,),
            )[0]
            == "complete"
        )
        hub.close()

    def test_invalid_preparation_does_not_create_a_registry_row(self, tmp_path):
        from pixlstash.hub.db import HubDatabase

        hub = HubDatabase(str(tmp_path / "hub.db"))
        folder = make_vault(str(tmp_path / "ownerless"), username=None)
        conn = sqlite3.connect(os.path.join(folder, "vault.db"))
        conn.execute("DELETE FROM user")
        conn.commit()
        conn.close()

        with pytest.raises(HubBootstrapError, match="no legacy owner"):
            prepare_legacy_identity(hub, folder)
        assert LibraryRegistry(hub).list_libraries() == []
        hub.close()

    def test_existing_hub_owner_refusal_does_not_attach_source(self, tmp_path):
        from pixlstash.hub.db import HubDatabase

        hub = HubDatabase(str(tmp_path / "hub.db"))
        with hub.transaction() as conn:
            conn.execute("INSERT INTO user (username, is_admin) VALUES ('owner', 1)")
        folder = make_vault(str(tmp_path / "legacy"))

        with pytest.raises(HubBootstrapError, match="already has an owner"):
            prepare_legacy_identity(hub, folder)
        assert LibraryRegistry(hub).list_libraries() == []
        hub.close()

    def test_preparation_read_failure_does_not_attach_source(
        self, tmp_path, monkeypatch
    ):
        import pixlstash.hub.bootstrap as bootstrap_module
        from pixlstash.hub.db import HubDatabase

        hub = HubDatabase(str(tmp_path / "hub.db"))
        folder = make_vault(str(tmp_path / "unreadable-identity"))

        def fail_payload(_vault):
            raise HubBootstrapError("injected preparation read failure")

        monkeypatch.setattr(bootstrap_module, "_identity_payload", fail_payload)
        with pytest.raises(HubBootstrapError, match="preparation read failure"):
            prepare_legacy_identity(hub, folder)
        assert LibraryRegistry(hub).list_libraries() == []
        hub.close()

    def test_token_digest_input_is_stably_ordered_by_id(self, tmp_path):
        import pixlstash.hub.bootstrap as bootstrap_module

        folder = make_vault(str(tmp_path / "ordered"), tokens=0)
        conn = sqlite3.connect(os.path.join(folder, "vault.db"))
        conn.row_factory = sqlite3.Row
        for token_id in (10, 2):
            conn.execute(
                "INSERT INTO usertoken (id, user_id, public_id, token_hash, scope, "
                "created_at, include_attachments, watermark) VALUES (?, 1, ?, ?, "
                "'ALL', '2026-07-01T00:00:00', 0, 1)",
                (token_id, f"public-{token_id}", f"hash-{token_id}"),
            )
        conn.commit()
        _user, tokens, _digest = bootstrap_module._identity_payload(conn)
        conn.close()
        assert [token["id"] for token in tokens] == [2, 10]


class TestCrashSafeOrdering:
    def test_legacy_select_helpers_fail_closed_on_sqlite_errors(self):
        import pixlstash.hub.bootstrap as bootstrap_module

        class BrokenConnection:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("injected SELECT error")

        with pytest.raises(HubBootstrapError, match="legacy user"):
            bootstrap_module._read_user(BrokenConnection())
        with pytest.raises(HubBootstrapError, match="legacy tokens"):
            bootstrap_module._read_tokens(BrokenConnection(), 1)

    def test_copy_commits_before_any_vault_credential_is_blanked(self, paths):
        hub_path, library_folder = paths

        first = bootstrap_upgrade(library_folder, hub_path, finalize=False)

        assert tuple(
            first.hub.connection.execute(
                "SELECT username, password_hash FROM user"
            ).fetchone()
        ) == ("owner", "a-real-hash")
        assert vault_query(
            library_folder, "SELECT username, password_hash FROM user"
        ) == [("owner", "a-real-hash")]
        assert first.library.identity_migration_state == "copied"
        assert (
            first.hub.fetchone(
                "SELECT state FROM identity_migration_operation WHERE library_uuid=?",
                (first.library.uuid,),
            )[0]
            == "copied"
        )
        first.engine.close()
        first.hub.close()

        # Simulate restart after copy and before blank. No copy is repeated;
        # finalization resumes from the durable state and is idempotent.
        second = bootstrap_hub(library_folder, hub_path)
        finalize_opened_library(second)
        finalize_opened_library(second)
        assert (
            vault_query(library_folder, "SELECT username, password_hash FROM user")
            == []
        )
        assert second.library.identity_migration_state == "complete"
        assert (
            second.hub.fetchone(
                "SELECT state FROM identity_migration_operation WHERE library_uuid=?",
                (second.library.uuid,),
            )[0]
            == "complete"
        )
        second.engine.close()
        second.hub.close()

    def test_copied_resume_rejects_a_replaced_vault_before_stamp_or_blank(
        self, paths, tmp_path
    ):
        hub_path, library_folder = paths
        result = bootstrap_upgrade(library_folder, hub_path, finalize=False)
        original = str(tmp_path / "approved-original")
        os.rename(library_folder, original)
        make_vault(library_folder, username="foreign", password_hash="foreign-hash")

        with pytest.raises(HubBootstrapError, match="changed after it was copied"):
            finalize_opened_library(result)

        assert read_vault_uuid(library_folder) is None
        assert vault_query(
            library_folder, "SELECT username, password_hash FROM user"
        ) == [("foreign", "foreign-hash")]
        result.engine.close()
        result.hub.close()

    def test_copied_resume_accepts_already_blanked_matching_vault(self, paths):
        hub_path, library_folder = paths
        result = bootstrap_upgrade(library_folder, hub_path, finalize=False)
        conn = sqlite3.connect(os.path.join(library_folder, "vault.db"))
        conn.execute(
            "UPDATE library_settings SET library_uuid=?", (result.library.uuid,)
        )
        conn.execute("UPDATE user SET username=NULL, password_hash=NULL")
        conn.execute("DELETE FROM usertoken")
        conn.commit()
        conn.close()

        finalize_opened_library(result)

        assert result.library.identity_migration_state == "complete"
        result.engine.close()
        result.hub.close()

    def test_owner_winning_prepare_race_leaves_no_library_or_operation(self, tmp_path):
        from pixlstash.hub.db import HubDatabase

        hub_path = str(tmp_path / "hub.db")
        folder = make_vault(str(tmp_path / "legacy"))
        preparer = HubDatabase(hub_path)
        owner = HubDatabase(hub_path)
        errors = []

        owner.connection.execute("BEGIN IMMEDIATE")
        owner.connection.execute(
            "INSERT INTO user (username, is_admin) VALUES ('winner', 1)"
        )

        def attempt_prepare():
            try:
                prepare_legacy_identity(preparer, folder)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        thread = threading.Thread(target=attempt_prepare)
        thread.start()
        time.sleep(0.1)
        owner.connection.commit()
        thread.join(timeout=10)

        assert not thread.is_alive()
        assert len(errors) == 1
        assert "already has an owner" in str(errors[0])
        assert preparer.fetchone("SELECT COUNT(*) FROM library")[0] == 0
        assert (
            preparer.fetchone("SELECT COUNT(*) FROM identity_migration_operation")[0]
            == 0
        )
        owner.close()
        preparer.close()

    def test_fingerprint_conflict_stops_before_blanking(self, paths):
        hub_path, library_folder = paths
        result = bootstrap_upgrade(library_folder, hub_path, finalize=False)
        foreign_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        conn = sqlite3.connect(os.path.join(library_folder, "vault.db"))
        conn.execute("UPDATE library_settings SET library_uuid = ?", (foreign_uuid,))
        conn.commit()
        conn.close()

        with pytest.raises(HubBootstrapError, match="fingerprint conflict"):
            finalize_opened_library(result)

        assert read_vault_uuid(library_folder) == foreign_uuid
        assert vault_query(
            library_folder, "SELECT username, password_hash FROM user"
        ) == [("owner", "a-real-hash")]
        result.engine.close()
        result.hub.close()

    def test_user_select_error_keeps_migration_pending_and_vault_untouched(
        self, paths, monkeypatch
    ):
        import pixlstash.hub.bootstrap as bootstrap_module
        from pixlstash.hub.db import HubDatabase

        hub_path, library_folder = paths
        preparer = HubDatabase(hub_path)
        prepare_legacy_identity(preparer, library_folder)
        preparer.close()

        def fail_user_read(_vault):
            raise HubBootstrapError("injected user SELECT failure")

        monkeypatch.setattr(bootstrap_module, "_read_user", fail_user_read)
        with pytest.raises(HubBootstrapError, match="injected user SELECT"):
            bootstrap_hub(
                library_folder,
                hub_path,
            )

        hub = HubDatabase(hub_path)
        try:
            assert (
                hub.fetchone("SELECT identity_migration_state FROM library")[0]
                == "pending"
            )
            assert hub.fetchone("SELECT COUNT(*) FROM user")[0] == 0
        finally:
            hub.close()
        assert vault_query(
            library_folder, "SELECT username, password_hash FROM user"
        ) == [("owner", "a-real-hash")]
        assert vault_query(library_folder, "SELECT COUNT(*) FROM usertoken") == [(1,)]

    def test_token_select_error_keeps_migration_pending_and_vault_untouched(
        self, paths, monkeypatch
    ):
        import pixlstash.hub.bootstrap as bootstrap_module
        from pixlstash.hub.db import HubDatabase

        hub_path, library_folder = paths
        preparer = HubDatabase(hub_path)
        prepare_legacy_identity(preparer, library_folder)
        preparer.close()

        def fail_token_read(_vault, _user_id):
            raise HubBootstrapError("injected token SELECT failure")

        monkeypatch.setattr(bootstrap_module, "_read_tokens", fail_token_read)
        with pytest.raises(HubBootstrapError, match="injected token SELECT"):
            bootstrap_hub(
                library_folder,
                hub_path,
            )

        hub = HubDatabase(hub_path)
        try:
            assert (
                hub.fetchone("SELECT identity_migration_state FROM library")[0]
                == "pending"
            )
            assert hub.fetchone("SELECT COUNT(*) FROM user")[0] == 0
        finally:
            hub.close()
        assert vault_query(
            library_folder, "SELECT username, password_hash FROM user"
        ) == [("owner", "a-real-hash")]
        assert vault_query(library_folder, "SELECT COUNT(*) FROM usertoken") == [(1,)]

    def test_startup_refuses_a_foreign_stamped_vault_before_alembic(self, tmp_path):
        import json

        from pixlstash.server import Server

        config_path = tmp_path / "server-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "image_root": str(tmp_path / "library"),
                    "disable_background_workers": True,
                }
            )
        )
        with Server(str(config_path)) as server:
            vault_path = server.library_registry.active_library().vault_path

        conn = sqlite3.connect(vault_path)
        conn.execute("DROP TABLE library_settings")
        conn.execute(
            "CREATE TABLE library_settings (id INTEGER PRIMARY KEY, library_uuid TEXT)"
        )
        conn.execute(
            "INSERT INTO library_settings VALUES "
            "(1, '00000000-0000-4000-8000-000000000099')"
        )
        conn.execute("CREATE TABLE startup_sentinel (payload TEXT)")
        conn.execute("INSERT INTO startup_sentinel VALUES ('untouched')")
        conn.execute(
            "UPDATE alembic_version SET version_num = "
            "'0100_add_pending_score_invalidation'"
        )
        conn.commit()
        conn.close()

        with pytest.raises(HubBootstrapError, match="not opened or migrated"):
            Server(str(config_path))

        conn = sqlite3.connect(vault_path)
        try:
            assert conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0100_add_pending_score_invalidation",)
            assert {
                row[1] for row in conn.execute("PRAGMA table_info(library_settings)")
            } == {"id", "library_uuid"}
            assert conn.execute("SELECT payload FROM startup_sentinel").fetchone() == (
                "untouched",
            )
        finally:
            conn.close()

    def test_recorded_fingerprint_read_errors_fail_closed(self, tmp_path):
        hub_path = str(tmp_path / "hub.db")
        library_folder = make_vault(str(tmp_path / "library"))
        result = bootstrap_upgrade(library_folder, hub_path)
        library = result.library
        result.engine.close()
        result.hub.close()
        conn = sqlite3.connect(library.vault_path)
        conn.execute("DROP TABLE library_settings")
        conn.commit()
        conn.close()

        with pytest.raises(HubBootstrapError, match="Could not verify"):
            prevalidate_library_fingerprint(library)

    def test_unstamped_malformed_fingerprint_table_fails_before_alembic(self, tmp_path):
        import json

        from pixlstash.server import Server

        folder = str(tmp_path / "library")
        config_path = tmp_path / "server-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "image_root": folder,
                    "disable_background_workers": True,
                }
            )
        )
        with Server(str(config_path)):
            pass

        # Simulate hub loss: the replacement registry has no recorded
        # fingerprint and must not therefore treat a malformed *present* table
        # as an old vault whose table is genuinely absent.
        hub_path = tmp_path / "hub.db"
        for suffix in ("-wal", "-shm", "-journal", ""):
            candidate = str(hub_path) + suffix
            if os.path.exists(candidate):
                os.remove(candidate)
        vault_path = os.path.join(folder, "vault.db")
        conn = sqlite3.connect(vault_path)
        conn.execute("DROP TABLE library_settings")
        conn.execute("CREATE TABLE library_settings (id INTEGER, wrong TEXT)")
        conn.execute("INSERT INTO library_settings VALUES (1, 'do-not-migrate')")
        conn.execute("CREATE TABLE malformed_fingerprint_sentinel (payload TEXT)")
        conn.execute("INSERT INTO malformed_fingerprint_sentinel VALUES ('untouched')")
        conn.execute(
            "UPDATE alembic_version SET version_num = "
            "'0100_add_pending_score_invalidation'"
        )
        conn.commit()
        conn.close()

        with pytest.raises(HubBootstrapError, match="Could not verify"):
            Server(str(config_path))

        conn = sqlite3.connect(vault_path)
        try:
            assert conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0100_add_pending_score_invalidation",)
            assert {
                row[1] for row in conn.execute("PRAGMA table_info(library_settings)")
            } == {"id", "wrong"}
            assert conn.execute(
                "SELECT payload FROM malformed_fingerprint_sentinel"
            ).fetchone() == ("untouched",)
        finally:
            conn.close()

    def test_vault_open_runs_alembic_before_direct_finalization(self, tmp_path):
        from pixlstash.database import VaultDatabase
        from pixlstash.db_models import User

        folder = str(tmp_path / "real-ordering")
        os.makedirs(folder)
        os.chmod(folder, 0o700)
        vault_path = os.path.join(folder, "vault.db")
        db = VaultDatabase(vault_path)
        db.run_task(
            lambda session: (
                session.add(User(username="owner", password_hash="hash-before-open")),
                session.commit(),
            )
        )
        db.close()
        conn = sqlite3.connect(vault_path)
        conn.execute("DROP TABLE library_settings")
        conn.execute(
            "UPDATE alembic_version SET version_num = '0097_add_usertoken_library_uuid'"
        )
        conn.commit()
        conn.close()

        result = bootstrap_upgrade(folder, str(tmp_path / "hub.db"), finalize=False)
        assert read_vault_uuid(folder) is None
        assert vault_query(folder, "SELECT password_hash FROM user") == [
            ("hash-before-open",)
        ]

        opened = VaultDatabase(vault_path)
        finalize_opened_library(result, opened)
        assert read_vault_uuid(folder) == result.library.uuid
        assert vault_query(folder, "SELECT username, password_hash FROM user") == []
        opened.close()
        result.engine.close()
        result.hub.close()

    def test_server_startup_wires_upgrade_copy_migrate_stamp_and_blank(self, tmp_path):
        import json

        from fastapi.testclient import TestClient
        from passlib.hash import bcrypt

        from pixlstash.database import VaultDatabase
        from pixlstash.db_models import User, UserToken
        from pixlstash.server import Server

        folder = str(tmp_path / "production-ordering")
        os.makedirs(folder)
        os.chmod(folder, 0o700)
        vault_path = os.path.join(folder, "vault.db")
        raw_token = "legacy-token-secret"
        password = "legacy-password"
        db = VaultDatabase(vault_path)

        def seed_legacy_identity(session):
            user = User(
                username="legacy-owner",
                password_hash=bcrypt.hash(password),
                stack_strictness=0.81,
                smart_score_penalised_tags='["watermark"]',
                hidden_tags='["private"]',
                apply_tag_filter=False,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            session.add(
                UserToken(
                    user_id=user.id,
                    token_hash=bcrypt.hash(raw_token),
                    token_prefix=raw_token[:8],
                    description="legacy token",
                    scope="ALL",
                )
            )
            session.commit()

        db.run_task(seed_legacy_identity)
        db.close()
        conn = sqlite3.connect(vault_path)
        conn.execute("DROP TABLE library_settings")
        conn.execute("DROP INDEX IF EXISTS ix_usertoken_library_uuid")
        conn.execute("ALTER TABLE usertoken DROP COLUMN library_uuid")
        conn.execute(
            "UPDATE alembic_version SET version_num = '0090_add_usertoken_public_id'"
        )
        conn.commit()
        conn.close()

        config_path = tmp_path / "server-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "image_root": folder,
                    "disable_background_workers": True,
                }
            )
        )

        from pixlstash.hub.db import HubDatabase

        prepared_hub = HubDatabase(str(tmp_path / "hub.db"))
        prepare_legacy_identity(prepared_hub, folder)
        prepared_hub.close()

        def assert_upgraded_server(server):
            library = server.library_registry.active_library()
            assert read_vault_uuid(folder) == library.uuid
            assert vault_query(folder, "SELECT username, password_hash FROM user") == []
            assert vault_query(folder, "SELECT COUNT(*) FROM usertoken") == [(0,)]
            row = server.hub.connection.execute(
                "SELECT username, password_hash, stack_strictness, "
                "smart_score_penalised_tags, hidden_tags, apply_tag_filter "
                "FROM user"
            ).fetchone()
            assert row["username"] == "legacy-owner"
            assert bcrypt.verify(password, row["password_hash"])
            assert row["stack_strictness"] == pytest.approx(0.81)
            assert row["smart_score_penalised_tags"] == '["watermark"]'
            assert row["hidden_tags"] == '["private"]'
            assert row["apply_tag_filter"] == 0
            token_row = server.hub.connection.execute(
                "SELECT token_hash, library_uuid FROM usertoken"
            ).fetchone()
            assert bcrypt.verify(raw_token, token_row["token_hash"])
            assert token_row["library_uuid"] == library.uuid
            assert (
                server.hub.connection.execute(
                    "SELECT identity_migration_state FROM library WHERE uuid = ?",
                    (library.uuid,),
                ).fetchone()[0]
                == "complete"
            )
            client = TestClient(server.api)
            assert (
                client.post(
                    "/login",
                    json={"username": "legacy-owner", "password": password},
                ).status_code
                == 200
            )
            token_client = TestClient(server.api)
            assert (
                token_client.post("/login", json={"token": raw_token}).status_code
                == 200
            )
            assert token_client.get("/api/v1/pictures").status_code == 200

        with Server(str(config_path)) as first:
            original_uuid = first.library_registry.active_library().uuid
            assert_upgraded_server(first)

        # Production constructor rerun: no duplicate copy, token, user or UUID.
        with Server(str(config_path)) as second:
            assert second.library_registry.active_library().uuid == original_uuid
            assert_upgraded_server(second)
            assert (
                second.hub.connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
                == 1
            )
            assert (
                second.hub.connection.execute(
                    "SELECT COUNT(*) FROM usertoken"
                ).fetchone()[0]
                == 1
            )


class TestLosingTheHub:
    def test_a_deleted_hub_is_recreated_and_the_server_still_starts(self, paths):
        """Hub loss must degrade, never block startup.

        What is lost is the password, the tokens and the preferences. What
        survives is every picture, tag, score and snapshot, because those live
        in the library.
        """
        hub_path, library_folder = paths
        bootstrap_upgrade(library_folder, hub_path).hub.close()
        os.remove(hub_path)

        result = bootstrap_hub(
            library_folder,
            hub_path,
        )

        assert result.library.is_active
        assert result.image_root == os.path.realpath(library_folder)
        # The vault's credentials were blanked by the first run, so there is
        # nothing left to re-migrate: the owner re-registers, as on a new install.
        assert (
            result.hub.connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            == 0
        )
        assert vault_query(library_folder, "SELECT COUNT(*) FROM picture") == [(0,)]
        result.hub.close()

    def test_a_recreated_hub_mints_a_fresh_identity(self, paths):
        hub_path, library_folder = paths
        first = bootstrap_upgrade(library_folder, hub_path)
        original_uuid = first.library.uuid
        first.hub.close()
        os.remove(hub_path)

        second = bootstrap_hub(
            library_folder,
            hub_path,
        )

        # Hub loss is not import or recovery authority. The stamped value is
        # recorded only as an advisory vault fingerprint; a fresh hub mints a
        # fresh registry identity and never reads credentials from the vault.
        assert second.library.uuid != original_uuid
        assert second.library.vault_uuid == original_uuid
        assert (
            second.hub.connection.execute("SELECT COUNT(*) FROM usertoken").fetchone()[
                0
            ]
            == 0
        )
        second.hub.close()


def _make_portable_identity_db(path, marker):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE user (
            id INTEGER PRIMARY KEY,
            username TEXT,
            password_hash TEXT,
            hidden_tags TEXT
        );
        CREATE TABLE usertoken (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            token_hash TEXT
        );
        CREATE TABLE guest_session (
            id INTEGER PRIMARY KEY,
            cookie_token TEXT,
            user_token_id INTEGER
        );
        CREATE TABLE guest_score (
            id INTEGER PRIMARY KEY,
            guest_session_id INTEGER,
            score REAL
        );
        """
    )
    connection.execute(
        "INSERT INTO user VALUES (1, ?, ?, ?)",
        (f"user-{marker}", f"password-{marker}", f'tags-["{marker}"]'),
    )
    connection.execute("INSERT INTO usertoken VALUES (1, 1, ?)", (f"token-{marker}",))
    connection.execute(
        "INSERT INTO guest_session VALUES (1, ?, 1)", (f"cookie-{marker}",)
    )
    connection.execute("INSERT INTO guest_score VALUES (1, 1, 0.7)")
    connection.commit()
    return connection


def _assert_portable_identity_absent(path, marker):
    connection = sqlite3.connect(path)
    try:
        for table in ("user", "usertoken", "guest_session", "guest_score"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (
                0,
            )
    finally:
        connection.close()
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = str(path) + suffix
        if os.path.exists(candidate):
            assert marker.encode() not in open(candidate, "rb").read()


class TestPortableIdentityScrub:
    def test_live_scrub_erases_rows_wal_and_plaintext_markers(self, tmp_path):
        from pixlstash.services.portable_identity import sanitize_vault_connection

        marker = "LIVE-PORTABLE-SECRET-7f39"
        path = tmp_path / "vault.db"
        connection = _make_portable_identity_db(path, marker)
        sanitize_vault_connection(connection, str(path))
        connection.close()

        _assert_portable_identity_absent(path, marker)

    @pytest.mark.parametrize("compressed", [False, True])
    def test_historical_plain_and_compressed_0664_archives_are_scrubbed(
        self, tmp_path, compressed
    ):
        from pixlstash.services.portable_identity import sanitize_historical_snapshots
        from pixlstash.utils.snapshot_compression import (
            compress_snapshot,
            materialize_snapshot,
        )

        marker = f"HISTORICAL-SECRET-{compressed}-8a12"
        root = tmp_path / "library"
        snapshots = root / "snapshots"
        snapshots.mkdir(parents=True)
        source = tmp_path / "source.sqlite"
        _make_portable_identity_db(source, marker).close()
        suffix = ".sqlite.zst" if compressed else ".sqlite"
        archive = snapshots / f"archive{suffix}"
        if compressed:
            compress_snapshot(str(source), str(archive))
        else:
            archive.write_bytes(source.read_bytes())
        archive.chmod(0o664)

        live = sqlite3.connect(root / "vault.db")
        live.execute(
            "CREATE TABLE snapshot (id INTEGER PRIMARY KEY, relative_path TEXT, "
            "byte_size INTEGER)"
        )
        live.execute(
            "INSERT INTO snapshot VALUES (1, ?, 0)",
            (os.path.join("snapshots", archive.name),),
        )
        live.commit()
        sanitize_historical_snapshots(live, str(root))
        recorded_size = live.execute(
            "SELECT byte_size FROM snapshot WHERE id=1"
        ).fetchone()[0]
        live.close()

        assert recorded_size == archive.stat().st_size
        assert archive.stat().st_mode & 0o777 == 0o600
        materialized = tmp_path / "verified.sqlite"
        materialize_snapshot(str(archive), str(materialized))
        _assert_portable_identity_absent(materialized, marker)

    @pytest.mark.parametrize("registered", ["../../outside.sqlite", "ABSOLUTE"])
    def test_registered_snapshot_escape_is_refused_and_external_file_preserved(
        self, tmp_path, registered
    ):
        from pixlstash.services.portable_identity import (
            PortableIdentityScrubError,
            sanitize_historical_snapshots,
        )

        root = tmp_path / "library"
        (root / "snapshots").mkdir(parents=True)
        outside = tmp_path / "outside.sqlite"
        original = b"external-target-must-survive"
        outside.write_bytes(original)
        value = str(outside) if registered == "ABSOLUTE" else registered
        live = sqlite3.connect(root / "vault.db")
        live.execute(
            "CREATE TABLE snapshot (id INTEGER PRIMARY KEY, relative_path TEXT, "
            "byte_size INTEGER)"
        )
        live.execute("INSERT INTO snapshot VALUES (1, ?, 0)", (value,))
        live.commit()

        with pytest.raises(PortableIdentityScrubError, match="unsafe|escapes"):
            sanitize_historical_snapshots(live, str(root))
        live.close()
        assert outside.read_bytes() == original

    def test_archive_and_stale_scrub_symlinks_are_refused(self, tmp_path):
        from pixlstash.services.portable_identity import (
            PortableIdentityScrubError,
            sanitize_historical_snapshots,
        )

        root = tmp_path / "library"
        snapshots = root / "snapshots"
        snapshots.mkdir(parents=True)
        outside = tmp_path / "outside.sqlite"
        original = b"external-symlink-target"
        outside.write_bytes(original)
        (snapshots / "linked.sqlite").symlink_to(outside)
        live = sqlite3.connect(root / "vault.db")
        live.execute(
            "CREATE TABLE snapshot (id INTEGER PRIMARY KEY, relative_path TEXT, "
            "byte_size INTEGER)"
        )
        live.execute("INSERT INTO snapshot VALUES (1, 'snapshots/linked.sqlite', 0)")
        live.commit()
        with pytest.raises(PortableIdentityScrubError, match="symlink"):
            sanitize_historical_snapshots(live, str(root))

        (snapshots / "linked.sqlite").unlink()
        (snapshots / ".pixlstash_identity_scrub_evil").symlink_to(tmp_path)
        with pytest.raises(PortableIdentityScrubError, match="symlinked stale"):
            sanitize_historical_snapshots(live, str(root))
        live.close()
        assert outside.read_bytes() == original

    def test_corrupt_recompression_preserves_original_archive(
        self, tmp_path, monkeypatch
    ):
        import pixlstash.services.portable_identity as portable
        from pixlstash.utils.snapshot_compression import compress_snapshot

        source = tmp_path / "source.sqlite"
        _make_portable_identity_db(source, "CORRUPT-REWRITE-SECRET").close()
        archive = tmp_path / "archive.sqlite.zst"
        compress_snapshot(str(source), str(archive))
        original = archive.read_bytes()

        def corrupt_compression(_source, destination):
            with open(destination, "wb") as handle:
                handle.write(b"not-a-zstd-or-sqlite-file")
            return os.path.getsize(destination)

        monkeypatch.setattr(portable, "compress_snapshot", corrupt_compression)
        with pytest.raises(portable.PortableIdentityScrubError):
            portable.sanitize_snapshot_archive(str(archive))
        assert archive.read_bytes() == original

    def test_an_interrupted_scrub_resumes_instead_of_restarting(
        self, tmp_path, monkeypatch
    ):
        """Each archive records its own completion, so a restart skips it.

        Without this the whole loop was all-or-nothing: interrupting it (which
        is what a user does when a silent multi-minute startup looks like a
        hang) discarded every finished archive, so a large snapshot history
        could never get through the migration.
        """
        import pixlstash.services.portable_identity as portable

        root = tmp_path / "library"
        snapshots = root / "snapshots"
        snapshots.mkdir(parents=True)
        live = sqlite3.connect(root / "vault.db")
        live.execute(
            "CREATE TABLE snapshot (id INTEGER PRIMARY KEY, relative_path TEXT, "
            "byte_size INTEGER, identity_scrubbed_at TIMESTAMP)"
        )
        for index in range(3):
            archive = snapshots / f"archive{index}.sqlite"
            _make_portable_identity_db(archive, f"RESUME-SECRET-{index}").close()
            live.execute(
                "INSERT INTO snapshot (id, relative_path, byte_size) VALUES (?, ?, 0)",
                (index + 1, os.path.join("snapshots", archive.name)),
            )
        live.commit()

        real = portable.sanitize_snapshot_archive
        calls = []

        def fail_on_the_third(path):
            calls.append(path)
            if len(calls) == 3:
                raise portable.PortableIdentityScrubError("injected interruption")
            return real(path)

        monkeypatch.setattr(portable, "sanitize_snapshot_archive", fail_on_the_third)
        with pytest.raises(portable.PortableIdentityScrubError, match="injected"):
            portable.sanitize_historical_snapshots(live, str(root))

        marks = dict(
            live.execute("SELECT id, identity_scrubbed_at FROM snapshot").fetchall()
        )
        assert marks[1] is not None and marks[2] is not None, (
            "finished archives must record their own completion"
        )
        assert marks[3] is None, "the interrupted archive must stay unmarked"

        # Second run: only the unfinished archive is touched.
        resumed = []

        def track(path):
            resumed.append(path)
            return real(path)

        monkeypatch.setattr(portable, "sanitize_snapshot_archive", track)
        portable.sanitize_historical_snapshots(live, str(root))

        assert len(resumed) == 1, f"expected only the leftover archive, got {resumed}"
        assert resumed[0].endswith("archive2.sqlite")
        assert all(
            value is not None
            for value in dict(
                live.execute("SELECT id, identity_scrubbed_at FROM snapshot").fetchall()
            ).values()
        )
        live.close()

    def test_attached_library_scrubs_once_then_uses_durable_complete_marker(
        self, tmp_path, monkeypatch
    ):
        import pixlstash.hub.bootstrap as bootstrap_module
        from pixlstash.utils.snapshot_compression import materialize_snapshot

        folder = make_vault(str(tmp_path / "attached"), username="secondary")
        snapshots = tmp_path / "attached" / "snapshots"
        snapshots.mkdir()
        marker = "SECONDARY-LIBRARY-HISTORY-51ce"
        archive = snapshots / "legacy.sqlite"
        _make_portable_identity_db(archive, marker).close()
        connection = sqlite3.connect(os.path.join(folder, "vault.db"))
        connection.execute(
            "CREATE TABLE snapshot (id INTEGER PRIMARY KEY, relative_path TEXT, "
            "byte_size INTEGER)"
        )
        connection.execute(
            "INSERT INTO snapshot VALUES (1, 'snapshots/legacy.sqlite', 0)"
        )
        connection.commit()
        connection.close()

        result = bootstrap_hub(folder, str(tmp_path / "hub.db"))
        assert result.library.identity_migration_state == "not_required"
        finalize_opened_library(result)
        assert result.library.identity_migration_state == "complete"
        materialized = tmp_path / "secondary-verified.sqlite"
        materialize_snapshot(str(archive), str(materialized))
        _assert_portable_identity_absent(materialized, marker)

        def must_not_repeat(*_args, **_kwargs):
            raise AssertionError("historical archives were rewritten a second time")

        monkeypatch.setattr(
            bootstrap_module, "sanitize_historical_snapshots", must_not_repeat
        )
        finalize_opened_library(result)
        result.engine.close()
        result.hub.close()
