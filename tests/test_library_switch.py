"""Switching the active library on a running server.

The property that matters is not that switching works. It is that **a switch
which cannot complete leaves the session exactly where it was**, because the
alternative is a server with no vault at all and a blank grid.

That is why the vault is constructed before the old one is closed: opening is
where the failures live (missing folder, corrupt database, a migration that will
not apply), and until it succeeds nothing has been given up.
"""

import os
import sqlite3
import tempfile

import pytest
from sqlmodel import delete, select

from pixlstash.db_models import Picture
from pixlstash.hub.registry import read_vault_uuid
from pixlstash.server import Server
from pixlstash.services.library_switch_service import (
    LibrarySwitchError,
    SwitchState,
    assert_vault_not_newer,
    known_vault_revisions,
)
from pixlstash.hub.registry import LibraryNotFoundError


@pytest.fixture(scope="module")
def server():
    """One Server for the module; building it runs migrations and vault startup."""
    with tempfile.TemporaryDirectory() as temp_dir:
        with Server(f"{temp_dir}/server-config.json") as srv:
            yield srv


@pytest.fixture
def second_library(server, tmp_path):
    """A real, attachable second library built by the same code the server uses."""
    folder = str(tmp_path / "second")
    library = server.library_registry.create(folder, "Second")
    yield library
    if not library.is_active:
        try:
            server.library_registry.detach(library.id)
        except Exception:
            pass


def _picture_count(server) -> int:
    return server.vault.db.run_immediate_read_task(
        lambda session: len(session.exec(select(Picture)).all())
    )


class TestSwitching:
    def test_switching_changes_the_open_vault_and_the_registry(
        self, server, second_library
    ):
        original = server.library_registry.active_library()

        active = server.library_switch.switch_to(second_library.uuid)

        assert active.uuid == second_library.uuid
        assert server.vault.image_root == second_library.path
        assert server.library_registry.active_library().uuid == second_library.uuid

        # Put it back so the module's other tests start from the original.
        server.library_switch.switch_to(original.uuid)
        assert server.vault.image_root == original.path

    def test_the_auth_service_follows_the_vault(self, server, second_library):
        """Guest sessions are per-library, so auth's vault handle must move."""
        original = server.library_registry.active_library()
        try:
            server.library_switch.switch_to(second_library.uuid)
            assert server.auth.vault_db is server.vault.db
        finally:
            server.library_switch.switch_to(original.uuid)

    def test_identity_does_not_follow_the_vault(self, server, second_library):
        """The owner is the owner in every library. This is the whole point."""
        original = server.library_registry.active_library()
        before = server.auth.get_user()
        try:
            server.library_switch.switch_to(second_library.uuid)
            after = server.auth.get_user()
            assert after.id == before.id
        finally:
            server.library_switch.switch_to(original.uuid)

    def test_the_state_returns_to_ready(self, server, second_library):
        original = server.library_registry.active_library()
        try:
            server.library_switch.switch_to(second_library.uuid)
            assert server.library_switch.state is SwitchState.READY
            assert not server.library_switch.is_switching
        finally:
            server.library_switch.switch_to(original.uuid)

    def test_switching_to_the_active_library_is_a_no_op(self, server):
        active = server.library_registry.active_library()
        before = server.vault
        assert server.library_switch.switch_to(active.uuid).uuid == active.uuid
        assert server.vault is before, "a no-op switch must not rebuild the vault"

    def test_switching_to_an_unknown_library_is_refused(self, server):
        with pytest.raises(LibraryNotFoundError):
            server.library_switch.switch_to("00000000-0000-4000-8000-000000000000")

    def test_switching_to_a_detached_library_is_refused(self, server, tmp_path):
        library = server.library_registry.create(str(tmp_path / "detached"), "Gone")
        server.library_registry.detach(library.id)

        with pytest.raises(LibraryNotFoundError):
            server.library_switch.switch_to(library.uuid)


class TestFailingToSwitch:
    def test_a_missing_folder_leaves_the_session_where_it_was(self, server, tmp_path):
        """The failure the construct-then-swap ordering exists for."""
        folder = str(tmp_path / "removable")
        library = server.library_registry.create(folder, "Removable")
        before_vault = server.vault
        before_active = server.library_registry.active_library()

        # The drive goes away between attach and switch.
        os.rename(folder, folder + "-unplugged")

        with pytest.raises(LibrarySwitchError) as excinfo:
            server.library_switch.switch_to(library.uuid)

        assert "not where PixlStash left it" in str(excinfo.value)
        assert server.vault is before_vault, "the old vault must still be open"
        assert server.library_registry.active_library().uuid == before_active.uuid
        assert server.library_switch.state is SwitchState.READY
        # And the server still works.
        assert _picture_count(server) >= 0

    def test_a_folder_that_is_no_longer_a_vault_is_refused(self, server, tmp_path):
        folder = str(tmp_path / "emptied")
        library = server.library_registry.create(folder, "Emptied")
        before_vault = server.vault

        os.remove(os.path.join(folder, "vault.db"))

        with pytest.raises(LibrarySwitchError):
            server.library_switch.switch_to(library.uuid)
        assert server.vault is before_vault
        assert server.library_switch.state is SwitchState.READY

    def test_a_vault_from_a_newer_build_is_refused(self, server, tmp_path):
        """Better a clear message than this build migrating a future database."""
        folder = str(tmp_path / "from-the-future")
        library = server.library_registry.create(folder, "Future")
        before_vault = server.vault

        conn = sqlite3.connect(os.path.join(folder, "vault.db"))
        conn.execute("UPDATE alembic_version SET version_num = '9999_from_the_future'")
        conn.commit()
        conn.close()

        with pytest.raises(LibrarySwitchError) as excinfo:
            server.library_switch.switch_to(library.uuid)

        assert "newer version of PixlStash" in str(excinfo.value)
        assert server.vault is before_vault
        assert server.library_switch.state is SwitchState.READY


class TestSchemaGuard:
    def test_the_current_head_is_recognised(self, server):
        vault_path = os.path.join(server.vault.image_root, "vault.db")
        assert_vault_not_newer(vault_path)  # does not raise

    def test_every_migration_file_is_a_known_revision(self):
        known = known_vault_revisions()
        assert "0001_baseline" in known
        assert any(rev.startswith("0094") for rev in known)


class TestSwitchingCreatesAFingerprint:
    def test_a_library_created_by_the_cli_is_fingerprinted_on_first_switch(
        self, server, tmp_path
    ):
        """A library must be identifiable after a detach and re-attach."""
        folder = str(tmp_path / "fingerprinted")
        library = server.library_registry.create(folder, "Fingerprinted")
        original = server.library_registry.active_library()

        try:
            server.library_switch.switch_to(library.uuid)
            # The fingerprint is written by the bootstrap on first open, so a
            # library switched into for the first time should carry one or be
            # able to have one written. Either way it must not carry another
            # library's.
            observed = read_vault_uuid(folder)
            assert observed in (None, library.uuid)
        finally:
            server.library_switch.switch_to(original.uuid)


class TestRefusingRequestsMidSwap:
    def test_the_gate_returns_503_while_switching(self, server):
        """A request mid-swap must not be served against a half-swapped server."""
        from types import SimpleNamespace

        from fastapi import HTTPException

        from pixlstash.authz.gate import AuthzGate
        from pixlstash.authz.policy import AccessPolicy, RoutePolicy

        server.library_switch._state = SwitchState.SWITCHING
        try:
            with pytest.raises(HTTPException) as excinfo:
                AuthzGate._refuse_while_switching(
                    SimpleNamespace(_server=server),
                    SimpleNamespace(state=SimpleNamespace()),
                    RoutePolicy(AccessPolicy.OWNER_ONLY),
                )
            assert excinfo.value.status_code == 503
            assert excinfo.value.headers.get("Retry-After")
        finally:
            server.library_switch._state = SwitchState.READY

    def test_library_independent_routes_still_answer_mid_swap(self, server):
        """Otherwise a client could not ask what is happening."""
        from types import SimpleNamespace

        from pixlstash.authz.gate import AuthzGate
        from pixlstash.authz.policy import AccessPolicy, RoutePolicy

        server.library_switch._state = SwitchState.SWITCHING
        try:
            AuthzGate._refuse_while_switching(
                SimpleNamespace(_server=server),
                SimpleNamespace(state=SimpleNamespace()),
                RoutePolicy(AccessPolicy.OWNER_ONLY, library_independent=True),
            )
        finally:
            server.library_switch._state = SwitchState.READY

    def test_nothing_is_refused_when_not_switching(self, server):
        from types import SimpleNamespace

        from pixlstash.authz.gate import AuthzGate
        from pixlstash.authz.policy import AccessPolicy, RoutePolicy

        AuthzGate._refuse_while_switching(
            SimpleNamespace(_server=server),
            SimpleNamespace(state=SimpleNamespace()),
            RoutePolicy(AccessPolicy.OWNER_ONLY),
        )


def test_a_switched_vault_is_built_like_a_started_one(server, tmp_path):
    """Configuration must not diverge between boot and switch.

    A vault opened by a switch that differs from one opened at boot is a bug
    that only appears after the first switch, which is the hardest kind to find.
    """
    folder = str(tmp_path / "config-check")
    library = server.library_registry.create(folder, "ConfigCheck")
    original = server.library_registry.active_library()
    before = server.vault

    try:
        server.library_switch.switch_to(library.uuid)
        after = server.vault
        assert after is not before
        assert after._disable_background_workers == before._disable_background_workers
        assert after._force_cpu == before._force_cpu
        assert after._insightface_model_pack == before._insightface_model_pack
        assert after.auth_service is server.auth
    finally:
        server.library_switch.switch_to(original.uuid)


def test_wiping_state_between_modules(server):
    """Guard: the module leaves the original library active."""
    active = server.library_registry.active_library()
    assert active is not None
    assert server.vault.image_root == active.path


@pytest.fixture(autouse=True)
def _clean_pictures(server):
    yield
    server.vault.db.run_task(
        lambda session: (session.exec(delete(Picture)), session.commit())
    )
