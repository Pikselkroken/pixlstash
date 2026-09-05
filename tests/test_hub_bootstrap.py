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
from types import SimpleNamespace

import pytest

from sqlalchemy.exc import NoSuchTableError, OperationalError

from pixlstash.hub.bootstrap import (
    HubBootstrapError,
    UnusableVaultError,
    bootstrap_hub,
    finalize_opened_library,
    prepare_legacy_identity,
    prevalidate_library_fingerprint,
    registered_vault_path,
    unusable_vault_from_open_failure,
)
from pixlstash.hub.db import HubDatabase
from pixlstash.server import Server
from pixlstash.hub.registry import LibraryRegistry, read_vault_uuid
from pixlstash.trusted_sqlite import TrustedSQLiteLocation
from pixlstash.vault import Vault


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

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_fresh_image_root_and_vault_db_are_private_under_a_group_umask(
        self, tmp_path
    ):
        """A fresh unregistered vault must be private under umask 002.

        Two creations under one umask: ``Vault.__init__`` makes the image_root
        (0775 with the default makedirs mode), and - via the unguarded
        no-hub branch - a ``VaultDatabase`` whose file SQLite would otherwise
        create at 0664. Both must come out owner-only.
        """
        old_umask = os.umask(0o002)
        try:
            image_root = tmp_path / "fresh-root"
            with Vault(str(image_root), disable_background_workers=True):
                pass
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(os.lstat(image_root).st_mode) == 0o700
        assert stat.S_IMODE(os.lstat(image_root / "vault.db").st_mode) == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_an_existing_loose_image_root_keeps_its_mode(self, tmp_path):
        """Creation-only: the user's own shared folder is never tightened.

        A deliberately group-accessible image_root (shared with a media server
        or sync agent) must keep its mode across ``Vault.__init__``; only
        directories the vault actually creates are 0700.
        """
        image_root = tmp_path / "shared-root"
        image_root.mkdir()
        os.chmod(image_root, 0o775)

        with Vault(str(image_root), disable_background_workers=True):
            pass

        assert stat.S_IMODE(os.lstat(image_root).st_mode) == 0o775
        # The database inside it is still ours alone.
        assert stat.S_IMODE(os.lstat(image_root / "vault.db").st_mode) == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_a_deep_fresh_image_root_is_private_at_every_created_component(
        self, tmp_path
    ):
        """W21: ``makedirs(mode=)`` reaches only the leaf since Python 3.7.

        A two-level-deep new image_root under umask 002 used to leave the
        intermediate at 0775, and ``_validate_namespace`` then refused the
        namespace the app itself had just created. Every created component
        must be 0700 and the guarded open must accept the result.
        """
        old_umask = os.umask(0o002)
        try:
            os.chmod(tmp_path, 0o700)
            image_root = tmp_path / "deep" / "nested"
            with Vault(str(image_root), disable_background_workers=True):
                pass
        finally:
            os.umask(old_umask)

        for directory in (image_root.parent, image_root):
            mode = stat.S_IMODE(os.lstat(directory).st_mode)
            assert mode == 0o700, f"{directory} is {oct(mode)}, expected 0o700"
        TrustedSQLiteLocation.open(str(image_root / "vault.db")).close()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_a_loose_existing_ancestor_is_kept_and_still_reported(
        self, tmp_path, caplog
    ):
        """Creation only: the fix never chmods a directory the owner already had.

        The loose ancestor keeps its mode (owner's folder, owner's business),
        and the guarded open goes on warning about the namespace, asserted here
        so the fix cannot drift into repairing directories it did not create.

        World-writable, not the 0775 this asserted originally: group-write alone
        is no longer an exposure when the group is the owner's own one-member
        group, which is exactly what a developer box and a GitHub runner both
        have, so 0775 stopped being refused and this asserted the opposite of
        the behaviour it names.
        """
        os.chmod(tmp_path, 0o700)
        loose = tmp_path / "loose"
        loose.mkdir()
        os.chmod(loose, 0o777)

        old_umask = os.umask(0o002)
        try:
            image_root = loose / "new-library"
            with Vault(str(image_root), disable_background_workers=True):
                pass
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(os.lstat(loose).st_mode) == 0o777
        assert stat.S_IMODE(os.lstat(image_root).st_mode) == 0o700
        TrustedSQLiteLocation.open(str(image_root / "vault.db")).close()
        assert any(
            "group/world-writable" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        )

    def test_existing_loose_hub_directory_is_reported_not_silently_repaired(
        self, tmp_path, caplog
    ):
        """A permission someone else loosened is an event the operator must see.

        Same rule ``check_file_mode`` follows for the hub file: the server
        warns rather than tightening in place, because silently fixing it hides
        that it ever happened.
        """
        os.chmod(tmp_path, 0o700)
        parent = tmp_path / "loose"
        parent.mkdir()
        # chmod, not mkdir(mode=): the mode argument is masked by the process
        # umask, so the mode lands short wherever the umask is 022 - which is
        # the GitHub runner default. The directory then is not group-writable,
        # nothing is loose, and the test failed with "DID NOT RAISE" for the
        # one reason that is not a defect in the code under test.
        #
        # World-writable, where this used to say 0o775: group-write alone is no
        # longer an exposure when the group is the owner's own one-member group
        # (``_is_private_group``), which is what a developer box has, so 0o775
        # would make this pass there and fail nothing.
        os.chmod(parent, 0o777)

        HubDatabase(str(parent / "hub.db")).close()

        assert any(
            "group/world-writable" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        )
        assert stat.S_IMODE(parent.stat().st_mode) == 0o777

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


class TestInteractiveLegacyPreparation:
    """Baking `prepare-legacy-identity` into startup via a Y/n callback."""

    def test_an_accepted_prompt_authorizes_and_migrates_in_one_call(self, paths):
        hub_path, library_folder = paths
        asked = []

        def prompt(library):
            asked.append(library.path)
            return True

        result = bootstrap_hub(library_folder, hub_path, legacy_identity_prompt=prompt)
        finalize_opened_library(result)

        assert asked == [os.path.realpath(library_folder)]
        assert result.migrated is True
        row = result.hub.connection.execute(
            "SELECT username, password_hash FROM user"
        ).fetchone()
        assert (row["username"], row["password_hash"]) == ("owner", "a-real-hash")
        assert vault_query(library_folder, "SELECT COUNT(*) FROM user") == [(0,)]
        result.hub.close()

    def test_a_declined_prompt_leaves_the_vault_untouched(self, paths):
        hub_path, library_folder = paths

        result = bootstrap_hub(
            library_folder, hub_path, legacy_identity_prompt=lambda library: False
        )

        assert result.migrated is False
        assert (
            result.hub.connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            == 0
        )
        assert vault_query(library_folder, "SELECT username FROM user") == [("owner",)]
        assert result.library.identity_migration_state == "not_required"
        result.hub.close()

    def test_no_prompt_is_offered_without_a_legacy_owner(self, tmp_path):
        """A fresh install's empty vault must never trigger the callback."""
        hub_path = str(tmp_path / "hub.db")
        image_root = str(tmp_path / "brand-new")
        calls = []

        result = bootstrap_hub(
            image_root, hub_path, legacy_identity_prompt=lambda library: calls.append(1)
        )

        assert calls == []
        assert result.migrated is False
        result.hub.close()

    def test_omitting_the_prompt_keeps_the_prior_default_behaviour(self, paths):
        """No callback at all must behave exactly like before this feature."""
        hub_path, library_folder = paths

        result = bootstrap_hub(library_folder, hub_path)

        assert result.migrated is False
        assert (
            result.hub.connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            == 0
        )
        result.hub.close()

    def test_a_concurrent_preparation_during_the_prompt_is_not_reattempted(self, paths):
        """The callback can block on a human for as long as it likes, so
        another process - e.g. someone running the CLI command in a second
        terminal while this prompt is still awaiting an answer - can record
        the same preparation first. The belated "yes" must not try to record
        it again, only pick up the state that is already there.
        """
        hub_path, library_folder = paths

        def prompt(library):
            concurrent_hub = HubDatabase(hub_path)
            prepare_legacy_identity(concurrent_hub, library_folder)
            concurrent_hub.close()
            return True

        result = bootstrap_hub(library_folder, hub_path, legacy_identity_prompt=prompt)
        finalize_opened_library(result)

        assert result.migrated is True
        assert result.library.identity_migration_state == "complete"
        result.hub.close()

    def test_a_concurrent_full_migration_during_the_prompt_does_not_abort_startup(
        self, paths
    ):
        """The tighter case: another process finishes the *entire* migration
        (prepare, copy and finalize) before the belated "yes" comes back.
        `prepare_legacy_identity` then refuses ("hub already has an owner"),
        and that refusal must be swallowed rather than raised out of
        `bootstrap_hub`, since the only thing wrong is that this startup asked
        for something already done.
        """
        hub_path, library_folder = paths

        def prompt(library):
            concurrent = bootstrap_hub(
                library_folder, hub_path, legacy_identity_prompt=lambda lib: True
            )
            finalize_opened_library(concurrent)
            concurrent.hub.close()
            return True

        result = bootstrap_hub(library_folder, hub_path, legacy_identity_prompt=prompt)

        assert result.migrated is False
        assert result.library.identity_migration_state == "complete"
        result.hub.close()


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

    def test_a_never_opened_registration_adopts_the_fingerprint_it_finds(self, paths):
        """Two installations sharing one folder must not wedge each other.

        The desktop build keeps its hub in a different config directory from
        the terminal build. Whichever registers the folder while it is still
        unstamped records no fingerprint, and if the other one stamps it first
        the loser used to fail every later startup with no way back.
        """
        hub_path, library_folder = paths
        foreign_uuid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        conn = sqlite3.connect(os.path.join(library_folder, "vault.db"))
        conn.execute("UPDATE library_settings SET library_uuid = ?", (foreign_uuid,))
        conn.commit()
        conn.close()

        result = bootstrap_hub(library_folder, hub_path)
        assert result.library.vault_uuid == foreign_uuid
        # The library keeps its own identity; only the fingerprint is adopted.
        assert result.library.uuid != foreign_uuid

        finalize_opened_library(result)

        assert read_vault_uuid(library_folder) == foreign_uuid
        result.engine.close()
        result.hub.close()

    def test_a_library_holding_tokens_never_adopts_a_foreign_fingerprint(self, paths):
        """A share link must not come back pointing at content it never saw."""
        hub_path, library_folder = paths
        result = bootstrap_upgrade(library_folder, hub_path)
        library_uuid = result.library.uuid
        assert result.hub.fetchone(
            "SELECT COUNT(*) FROM usertoken WHERE library_uuid = ?", (library_uuid,)
        )[0]
        result.engine.close()
        result.hub.close()

        # Forget the fingerprint the way a registration that never opened the
        # vault would have left it, then let something else stamp the folder.
        hub = HubDatabase(hub_path)
        with hub.transaction() as conn:
            conn.execute("UPDATE library SET vault_uuid = NULL")
        hub.close()
        foreign_uuid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        conn = sqlite3.connect(os.path.join(library_folder, "vault.db"))
        conn.execute("UPDATE library_settings SET library_uuid = ?", (foreign_uuid,))
        conn.commit()
        conn.close()

        result = bootstrap_hub(library_folder, hub_path)
        assert result.library.vault_uuid is None
        with pytest.raises(HubBootstrapError, match="fingerprint conflict"):
            finalize_opened_library(result)
        result.engine.close()
        result.hub.close()

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


class TestAVanishedVault:
    """The active library's vault deleted from under a still-stamped hub.

    A desktop install and a source install keep separate hubs but share one
    library folder, so removing a vault in one leaves the other's registration
    pointing at nothing.
    """

    @staticmethod
    def _stamped(folder, fingerprint):
        make_vault(folder)
        conn = sqlite3.connect(os.path.join(folder, "vault.db"))
        conn.execute("UPDATE library_settings SET library_uuid = ?", (fingerprint,))
        conn.commit()
        conn.close()
        return folder

    @pytest.fixture
    def two_libraries(self, tmp_path):
        """A hub whose active library's vault has just been deleted."""
        hub_path = str(tmp_path / "hub.db")
        kept = self._stamped(
            str(tmp_path / "kept"), "00000000-0000-4000-8000-0000000000aa"
        )
        gone = self._stamped(
            str(tmp_path / "gone"), "00000000-0000-4000-8000-0000000000bb"
        )
        hub = HubDatabase(hub_path)
        registry = LibraryRegistry(hub)
        registry.attach(kept, "Kept")
        registry.attach(gone, "Gone")
        registry.set_active("Gone")
        hub.close()
        os.remove(os.path.join(gone, "vault.db"))
        return hub_path, kept, gone

    def test_the_deleted_vault_is_not_recreated_and_the_alternative_is_named(
        self, two_libraries
    ):
        hub_path, kept, gone = two_libraries

        with pytest.raises(HubBootstrapError) as raised:
            bootstrap_hub(kept, hub_path)

        assert "Kept" in str(raised.value) and kept in str(raised.value)
        # The whole point of validating read-only first: an empty vault.db here
        # would turn a missing database into an unrecognisable one for good.
        assert not os.path.exists(os.path.join(gone, "vault.db"))

    def test_the_offered_library_becomes_active(self, two_libraries):
        hub_path, kept, gone = two_libraries
        offered = []

        def prompt(library, reason, alternatives):
            offered.append((library.name, [lib.name for lib in alternatives]))
            return alternatives[0]

        result = bootstrap_hub(kept, hub_path, library_switch_prompt=prompt)
        try:
            assert offered == [("Gone", ["Kept"])]
            assert result.library.path == kept
            assert result.library.is_active
        finally:
            result.engine.close()
            result.hub.close()

    def test_declining_the_offer_keeps_the_active_library(self, two_libraries):
        hub_path, kept, gone = two_libraries

        with pytest.raises(HubBootstrapError):
            bootstrap_hub(kept, hub_path, library_switch_prompt=lambda *_: None)

        hub = HubDatabase(hub_path)
        try:
            assert LibraryRegistry(hub).active_library().path == gone
        finally:
            hub.close()

    def test_a_populated_folder_without_its_vault_starts_fresh(self, two_libraries):
        """A restored picture folder is an import folder, not a dead end.

        The desktop has no terminal to offer alternatives in, and the folder
        the owner restored their pictures into is the library they want: start
        a fresh vault there, so the app opens on an empty library whose folder
        is full of pictures to import. Contrast the empty folder above, which
        is what an unmounted drive looks like and stays refused.
        """
        hub_path, kept, gone = two_libraries
        with open(os.path.join(gone, "holiday.jpg"), "wb") as picture:
            picture.write(b"not really a jpeg")

        result = bootstrap_hub(kept, hub_path, library_switch_prompt=lambda *_: None)
        try:
            assert result.library.path == gone
            assert result.library.vault_uuid is None
            with Vault(
                registered_vault_path(result.hub, result.library, result),
                disable_background_workers=True,
            ):
                pass
            assert read_vault_uuid(gone) == result.library.uuid
            assert LibraryRegistry(result.hub).by_uuid(
                result.library.uuid
            ).vault_uuid == (result.library.uuid)
        finally:
            result.engine.close()
            result.hub.close()

    def test_an_unreadable_alternative_is_never_offered(self, two_libraries):
        hub_path, kept, gone = two_libraries
        os.remove(os.path.join(kept, "vault.db"))

        with pytest.raises(HubBootstrapError) as raised:
            bootstrap_hub(kept, hub_path, library_switch_prompt=lambda *_: "no")

        assert "Kept" not in str(raised.value)


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
    def test_every_fsynced_fd_is_opened_with_write_access(self, tmp_path, monkeypatch):
        """Windows refuses to fsync a read-only handle (EBADF).

        CommitFileBuffers requires write access, so an ``O_RDONLY`` fd that is
        later fsynced works on Linux and fails on Windows - which is how the
        read-only opens in this module took down every test in backend-windows
        shard 2, through ``finalize_library_connection`` on every registered
        vault open. Linux cannot reproduce the EBADF, but it can assert the
        invariant that prevents it: any fd this module fsyncs must have been
        opened with write access. (Directory fds are exempt; those helpers
        already return early on ``nt``.)
        """
        from pixlstash.services import portable_identity

        marker = "FSYNC-FLAGS-PROBE"
        path = tmp_path / "vault.db"
        connection = _make_portable_identity_db(path, marker)

        readonly_fds = set()
        real_open = os.open

        def observing_open(target, flags, *args, **kwargs):
            fd = real_open(target, flags, *args, **kwargs)
            if not os.path.isdir(target) and (flags & os.O_ACCMODE) == os.O_RDONLY:
                readonly_fds.add(fd)
            return fd

        fsynced_readonly = []
        real_fsync = os.fsync

        def observing_fsync(fd):
            if fd in readonly_fds:
                fsynced_readonly.append(fd)
            return real_fsync(fd)

        monkeypatch.setattr(os, "open", observing_open)
        monkeypatch.setattr(os, "fsync", observing_fsync)

        portable_identity.sanitize_vault_connection(connection, str(path))
        connection.close()

        assert fsynced_readonly == [], "fsync on a read-only fd raises EBADF on Windows"
        # The scrub itself must still have done its job (the other direction).
        _assert_portable_identity_absent(path, marker)

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

    def test_attached_library_scrubs_the_vault_and_defers_its_archives(self, tmp_path):
        """Startup scrubs the live vault; archives are left to the background.

        Rewriting every archive inline is minutes of work ahead of the listening
        socket, and serving does not depend on it: restore paths scrub whatever
        they materialize. So finalize must clear the vault's own identity rows
        and mark the library complete, while leaving the archive untouched and
        still claimed by ``identity_scrubbed_at IS NULL``.
        """
        folder = make_vault(str(tmp_path / "attached"), username="secondary")
        snapshots = tmp_path / "attached" / "snapshots"
        snapshots.mkdir()
        marker = "SECONDARY-LIBRARY-HISTORY-51ce"
        archive = snapshots / "legacy.sqlite"
        _make_portable_identity_db(archive, marker).close()
        before = archive.read_bytes()
        connection = sqlite3.connect(os.path.join(folder, "vault.db"))
        connection.execute(
            "CREATE TABLE snapshot (id INTEGER PRIMARY KEY, relative_path TEXT, "
            "byte_size INTEGER, identity_scrubbed_at TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO snapshot (id, relative_path, byte_size) "
            "VALUES (1, 'snapshots/legacy.sqlite', 0)"
        )
        connection.commit()
        connection.close()

        result = bootstrap_hub(folder, str(tmp_path / "hub.db"))
        assert result.library.identity_migration_state == "not_required"
        finalize_opened_library(result)
        assert result.library.identity_migration_state == "complete"

        # The live vault is clean...
        assert vault_query(folder, "SELECT COUNT(*) FROM user") == [(0,)]
        assert vault_query(folder, "SELECT COUNT(*) FROM usertoken") == [(0,)]
        # ...and the archive is untouched, still owed to the background finder.
        assert archive.read_bytes() == before
        assert vault_query(
            folder, "SELECT COUNT(*) FROM snapshot WHERE identity_scrubbed_at IS NULL"
        ) == [(1,)]
        result.engine.close()
        result.hub.close()


def make_unopenable_vault(folder):
    """A ``vault.db`` that is present and is not a database we can open.

    The shape that ended a first run on a traceback: the file exists, so
    ``register_pending`` is not used, and it does not validate, so ``attach``
    refuses. Bytes rather than SQLite, because "unopenable" has to cover a
    truncated or foreign file and not only an old schema.
    """
    os.makedirs(folder, exist_ok=True)
    vault = os.path.join(folder, "vault.db")
    with open(vault, "wb") as handle:
        handle.write(b"not a database, not even close")
    return vault


def make_pre_alembic_vault(folder):
    """A real vault from before Alembic: openable, upgradable, no version table."""
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
    ):
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return os.path.join(folder, "vault.db")


class TestAVaultThatWillNotOpen:
    """Start-up asks a question about it rather than dying on it."""

    def test_bootstrap_raises_a_typed_error_and_touches_nothing(self, tmp_path):
        folder = str(tmp_path / "library")
        vault = make_unopenable_vault(folder)
        before = open(vault, "rb").read()

        with pytest.raises(UnusableVaultError) as caught:
            bootstrap_hub(folder, str(tmp_path / "hub.db"))

        assert caught.value.folder == os.path.realpath(folder)
        assert caught.value.vault_path == vault
        assert "vault.db" in caught.value.reason
        assert open(vault, "rb").read() == before, "refusing must not edit the file"

    def test_it_is_a_hub_bootstrap_error_so_existing_callers_report_it(self, tmp_path):
        """`app.main` already prints HubBootstrapError concisely; inherit that."""
        folder = str(tmp_path / "library")
        make_unopenable_vault(folder)

        with pytest.raises(HubBootstrapError):
            bootstrap_hub(folder, str(tmp_path / "hub.db"))

    def test_recreation_moves_the_old_file_aside_rather_than_deleting_it(
        self, tmp_path, monkeypatch
    ):
        folder = str(tmp_path / "library")
        vault = make_unopenable_vault(folder)
        before = open(vault, "rb").read()
        # Sidecars travel with the database; a stale -wal beside a new vault is
        # its own bug report.
        for suffix in ("-wal", "-shm"):
            with open(f"{vault}{suffix}", "wb") as handle:
                handle.write(b"stale")
        monkeypatch.setenv("PIXLSTASH_RECREATE_VAULT", "1")

        result = bootstrap_hub(folder, str(tmp_path / "hub.db"))
        try:
            assert result.library.path == os.path.realpath(folder)
            assert not os.path.exists(vault), "the new vault is created on open"

            moved = [
                name
                for name in os.listdir(folder)
                if name.startswith("vault.db.unusable-")
            ]
            assert len(moved) == 3, f"database and both sidecars, got {sorted(moved)}"
            kept = next(name for name in moved if not name.endswith(("-wal", "-shm")))
            assert open(os.path.join(folder, kept), "rb").read() == before
        finally:
            result.engine.close()
            result.hub.close()

    def test_nothing_is_moved_without_the_explicit_authorisation(self, tmp_path):
        """An inherited '0' - or no variable at all - is not a yes."""
        folder = str(tmp_path / "library")
        make_unopenable_vault(folder)

        with pytest.raises(UnusableVaultError):
            bootstrap_hub(folder, str(tmp_path / "hub.db"))

        assert os.listdir(folder) == ["vault.db"]

    def test_a_pre_alembic_vault_is_opened_rather_than_recreated(self, tmp_path):
        """The regression itself: this vault upgrades, so it must never be offered
        the recovery that abandons it."""
        folder = str(tmp_path / "library")
        vault = make_pre_alembic_vault(folder)
        before = open(vault, "rb").read()

        result = bootstrap_hub(folder, str(tmp_path / "hub.db"))
        try:
            assert result.library.path == os.path.realpath(folder)
            assert open(vault, "rb").read() == before, "registration must not migrate"
            assert not any(
                name.startswith("vault.db.unusable-") for name in os.listdir(folder)
            )
        finally:
            result.engine.close()
            result.hub.close()


def sqlalchemy_operational_error(message):
    """An OperationalError shaped the way SQLAlchemy actually raises one."""
    return OperationalError("SELECT 1", {}, sqlite3.OperationalError(message))


class TestAVaultThatRegistersButWillNotMigrate:
    """Validation reads `sqlite_master`; the migration chain reads much more."""

    def _library(self, tmp_path):
        folder = str(tmp_path / "library")
        make_pre_alembic_vault(folder)
        registry = LibraryRegistry(HubDatabase(str(tmp_path / "hub.db")))
        return registry.attach(folder, "Library 1")

    def test_a_schema_failure_becomes_the_same_offer(self, tmp_path):
        library = self._library(tmp_path)

        unusable = unusable_vault_from_open_failure(library, NoSuchTableError("user"))

        assert unusable is not None
        assert unusable.vault_path == library.vault_path
        # "NoSuchTableError: user" alone reads as the word "user".
        assert "could not be upgraded" in unusable.reason
        assert "NoSuchTableError" in unusable.reason

    @pytest.mark.parametrize(
        "message",
        [
            "database is locked",
            "unable to open database file",
            "attempt to write a readonly database",
            "disk I/O error",
            "database disk image is malformed",
            # SQLITE_FULL says it in SQLite's words, ENOSPC in the OS's. A
            # full disk answered by abandoning the library is the worst
            # outcome this classification exists to prevent.
            "database or disk is full",
            "no space left on device",
            "out of memory",
        ],
    )
    def test_a_failure_about_the_machine_is_never_offered_a_recreate(
        self, tmp_path, message
    ):
        """A full disk must not be answered by abandoning a good library."""
        library = self._library(tmp_path)

        assert (
            unusable_vault_from_open_failure(
                library, sqlalchemy_operational_error(message)
            )
            is None
        )

    def test_a_file_that_is_not_a_database_is_still_offered_the_recovery(
        self, tmp_path
    ):
        """SQLITE_NOTADB is a statement about the file, not about the machine."""
        library = self._library(tmp_path)

        unusable = unusable_vault_from_open_failure(
            library, sqlalchemy_operational_error("file is not a database")
        )

        assert unusable is not None


class TestOpeningTheRegisteredVault:
    """`Server._open_registered_vault`'s branches, without standing up a Server."""

    def _server_double(self, tmp_path, failure, *, opened=None):
        folder = str(tmp_path / "library")
        make_pre_alembic_vault(folder)
        hub = HubDatabase(str(tmp_path / "hub.db"))
        registry = LibraryRegistry(hub)
        library = registry.attach(folder, "Library 1")
        attempts = []

        def build_vault(image_root):
            attempts.append(image_root)
            if len(attempts) == 1:
                raise failure
            return opened

        return SimpleNamespace(
            hub=hub,
            library_registry=registry,
            _hub_bootstrap=SimpleNamespace(library=library),
            build_vault=build_vault,
        ), attempts

    def test_a_schema_failure_raises_the_typed_error(self, tmp_path):
        double, attempts = self._server_double(tmp_path, NoSuchTableError("user"))

        with pytest.raises(UnusableVaultError):
            Server._open_registered_vault(double)

        assert len(attempts) == 1, "nothing is retried without authorisation"

    def test_an_environmental_failure_is_re_raised_untouched(self, tmp_path):
        double, _ = self._server_double(
            tmp_path, sqlalchemy_operational_error("database is locked")
        )

        with pytest.raises(OperationalError):
            Server._open_registered_vault(double)

    def test_an_authorised_retry_sets_the_old_file_aside_and_reopens(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PIXLSTASH_RECREATE_VAULT", "1")
        sentinel = object()
        double, attempts = self._server_double(
            tmp_path, NoSuchTableError("user"), opened=sentinel
        )
        vault_path = double._hub_bootstrap.library.vault_path
        before = open(vault_path, "rb").read()

        assert Server._open_registered_vault(double) is sentinel
        assert len(attempts) == 2

        folder = os.path.dirname(vault_path)
        moved = [n for n in os.listdir(folder) if n.startswith("vault.db.unusable-")]
        assert len(moved) == 1
        assert open(os.path.join(folder, moved[0]), "rb").read() == before
        assert not os.path.exists(vault_path), "the replacement is made by the open"
        # Without this the new vault would be refused for carrying no fingerprint.
        assert double._hub_bootstrap.library.vault_uuid is None
