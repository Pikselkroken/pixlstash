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
import threading
import time

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

    def test_the_switched_to_vault_gets_its_inference_engine(
        self, server, second_library, monkeypatch
    ):
        """Without this the planner sweeps a library nothing ever queues work for:
        every engine-gated finder (tags, descriptions, faces, embeddings) short-
        circuits on a None engine."""
        from pixlstash.vault import Vault

        calls = []
        monkeypatch.setattr(
            Vault, "ensure_ready", lambda self: calls.append(self.image_root)
        )

        original = server.library_registry.active_library()
        try:
            server.library_switch.switch_to(second_library.uuid)
            assert second_library.path in calls, (
                "the incoming vault was started without an inference engine"
            )
        finally:
            server.library_switch.switch_to(original.uuid)

    def test_an_engine_that_cannot_be_built_does_not_fail_the_switch(
        self, server, second_library, monkeypatch
    ):
        """A library is usable without AI workers; rolling the user back is worse."""
        from pixlstash.vault import Vault

        def _boom(self):
            raise RuntimeError("no models on this machine")

        monkeypatch.setattr(Vault, "ensure_ready", _boom)

        original = server.library_registry.active_library()
        try:
            active = server.library_switch.switch_to(second_library.uuid)
            assert active.uuid == second_library.uuid
            assert server.vault.image_root == second_library.path
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

    def test_switch_clears_every_library_derived_runtime_cache(
        self, server, tmp_path, monkeypatch
    ):
        from pixlstash.routes.pictures import _anomaly
        from pixlstash.utils.service import picture_stats

        library = server.library_registry.create(str(tmp_path / "cache-b"), "Cache B")
        original = server.library_registry.active_library()
        picture_stats._stats_cache["old-library"] = (0.0, {"count": 999})
        _anomaly._anomaly_region_cache[(1, "old", "model")] = {"boxes": []}
        thumbnail_cleared = []
        monkeypatch.setattr(
            server,
            "_clear_thumbnail_runtime_cache",
            lambda: thumbnail_cleared.append(True),
        )
        try:
            server.library_switch.switch_to(library.uuid)
            assert picture_stats._stats_cache == {}
            assert _anomaly._anomaly_region_cache == {}
            assert thumbnail_cleared == [True]
        finally:
            server.library_switch.switch_to(original.uuid)


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

    def test_a_recorded_fingerprint_that_disappears_is_refused_before_open(
        self, server, tmp_path
    ):
        folder = str(tmp_path / "missing-fingerprint")
        target = server.library_registry.create(folder, "Missing fingerprint")
        original = server.library_registry.active_library()
        server.library_switch.switch_to(target.uuid)
        server.library_switch.switch_to(original.uuid)
        target = server.library_registry.by_uuid(target.uuid)
        assert target.vault_uuid is not None
        conn = sqlite3.connect(os.path.join(folder, "vault.db"))
        conn.execute("DROP TABLE library_settings")
        conn.commit()
        conn.close()

        with pytest.raises(
            LibrarySwitchError,
            match="Could not verify the registered library fingerprint",
        ):
            server.library_switch.switch_to(target.uuid)

        conn = sqlite3.connect(os.path.join(folder, "vault.db"))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "library_settings" not in tables
        finally:
            conn.close()
        assert server.library_registry.active_library().uuid == original.uuid

    def test_switch_validates_recorded_vault_uuid_not_fresh_registry_uuid(
        self, server, tmp_path
    ):
        folder = str(tmp_path / "recovered-fingerprint")
        target = server.library_registry.create(folder, "Recovered fingerprint")
        original = server.library_registry.active_library()
        server.library_switch.switch_to(target.uuid)
        server.library_switch.switch_to(original.uuid)
        target = server.library_registry.by_uuid(target.uuid)
        recorded = target.vault_uuid
        recovered_uuid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        with server.hub.transaction() as conn:
            conn.execute(
                "INSERT INTO library_uuid_issued (uuid, issued_at, first_path) "
                "VALUES (?, '2026-08-02T00:00:00+00:00', ?)",
                (recovered_uuid, folder),
            )
            conn.execute(
                "UPDATE library SET uuid=? WHERE id=?", (recovered_uuid, target.id)
            )

        server.library_switch.switch_to(recovered_uuid)
        try:
            assert read_vault_uuid(folder) == recorded
            assert server.library_registry.active_library().uuid == recovered_uuid
        finally:
            server.library_switch.switch_to(original.uuid)

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

        assert "newer PixlStash" in str(excinfo.value)
        assert "releases/latest" in str(excinfo.value)
        assert "revision 9999;" in str(excinfo.value)
        assert server.vault is before_vault
        assert server.library_switch.state is SwitchState.READY

    def test_a_replaced_path_is_refused_before_alembic_touches_it(
        self, server, tmp_path, monkeypatch
    ):
        """A foreign fingerprint is decisive before build_vault can migrate it."""
        folder = str(tmp_path / "replaced-under-registry")
        library = server.library_registry.create(folder, "Replaced")
        vault_path = os.path.join(folder, "vault.db")
        foreign_uuid = "00000000-0000-4000-8000-000000000099"
        conn = sqlite3.connect(vault_path)
        conn.execute("DROP TABLE library_settings")
        conn.execute(
            "CREATE TABLE library_settings (id INTEGER PRIMARY KEY, library_uuid TEXT)"
        )
        conn.execute(
            "INSERT INTO library_settings (id, library_uuid) VALUES (1, ?)",
            (foreign_uuid,),
        )
        conn.execute("CREATE TABLE pre_migration_sentinel (payload TEXT NOT NULL)")
        conn.execute("INSERT INTO pre_migration_sentinel VALUES ('untouched')")
        conn.execute(
            "UPDATE alembic_version SET version_num = "
            "'0100_add_pending_score_invalidation'"
        )
        conn.commit()
        conn.close()

        build_calls = []
        real_build = server.build_vault

        def observed_build(image_root):
            build_calls.append(image_root)
            return real_build(image_root)

        monkeypatch.setattr(server, "build_vault", observed_build)
        with pytest.raises(LibrarySwitchError, match="fingerprint conflict"):
            server.library_switch.switch_to(library.uuid)

        assert len(build_calls) == 1, (
            "the decisive fingerprint check belongs inside the securely bound "
            "VaultDatabase open, immediately before Alembic"
        )
        conn = sqlite3.connect(vault_path)
        try:
            revision = conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(library_settings)")
            }
            sentinel = conn.execute(
                "SELECT payload FROM pre_migration_sentinel"
            ).fetchone()
        finally:
            conn.close()
        assert revision == ("0100_add_pending_score_invalidation",)
        assert columns == {"id", "library_uuid"}
        assert sentinel == ("untouched",)

    def test_failure_after_old_close_reopens_previous_vault(
        self, server, tmp_path, monkeypatch
    ):
        folder = str(tmp_path / "post-close-failure")
        library = server.library_registry.create(folder, "Post-close failure")
        before = server.library_registry.active_library()
        real_build = server.build_vault

        def build_with_failing_candidate(image_root):
            vault = real_build(image_root)
            if image_root == library.path:
                vault.start = lambda: (_ for _ in ()).throw(
                    RuntimeError("candidate start fault")
                )
            return vault

        monkeypatch.setattr(server, "build_vault", build_with_failing_candidate)
        with pytest.raises(LibrarySwitchError, match="still using the previous"):
            server.library_switch.switch_to(library.uuid)

        assert server.library_registry.active_library().uuid == before.uuid
        assert server.vault.image_root == before.path
        assert server.auth.vault_db is server.vault.db
        assert _picture_count(server) >= 0

    def test_close_that_releases_resources_then_raises_rebuilds_previous_vault(
        self, server, tmp_path, monkeypatch
    ):
        library = server.library_registry.create(
            str(tmp_path / "close-side-effect"), "Close side effect"
        )
        before_library = server.library_registry.active_library()
        retired = server.vault
        real_close = retired.close
        raised = False

        def close_then_raise():
            nonlocal raised
            real_close()  # real side effect: db is released and becomes None
            if not raised:
                raised = True
                raise RuntimeError("close fault after resources released")

        monkeypatch.setattr(retired, "close", close_then_raise)
        with pytest.raises(LibrarySwitchError, match="still using the previous"):
            server.library_switch.switch_to(library.uuid)

        assert retired.db is None
        assert server.vault is not retired
        assert server.library_registry.active_library().uuid == before_library.uuid
        assert server.vault.image_root == before_library.path
        assert server.auth.vault_db is server.vault.db
        assert server.handle_vault_event in server.vault._event_listeners
        assert _picture_count(server) >= 0

    def test_switch_waits_for_inflight_request_before_closing_old_vault(
        self, server, tmp_path
    ):
        library = server.library_registry.create(str(tmp_path / "drain"), "Drain")
        original = server.library_registry.active_library()
        finished = threading.Event()
        errors = []
        # Model one already-running active-library request. The switch route is
        # HUB_ONLY/SWITCH_WRITER and therefore does not hold a read lease that
        # would deadlock its own writer admission.
        lease = server.library_coordinator.acquire_read()
        assert lease is not None

        def do_switch():
            try:
                server.library_switch.switch_to(library.uuid)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                finished.set()

        thread = threading.Thread(target=do_switch)
        thread.start()
        deadline = time.monotonic() + 5
        while server.library_switch.state is not SwitchState.SWITCHING:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not finished.is_set()
        assert server.vault.image_root == original.path

        server.library_coordinator.release_read(lease)
        thread.join(timeout=20)
        assert not errors
        assert finished.is_set()
        assert server.vault.image_root == library.path
        server.library_switch.switch_to(original.uuid)

    def test_stale_comfyui_output_is_discarded_after_switch(
        self, server, tmp_path, monkeypatch
    ):
        from pixlstash.services import comfyui_service

        library = server.library_registry.create(
            str(tmp_path / "comfy-stale"), "Comfy stale"
        )
        original = server.library_registry.active_library()
        origin = server.library_coordinator.acquire_read()
        assert origin is not None
        server.library_coordinator.release_read(origin)
        imported = []
        failed = []
        monkeypatch.setattr(
            comfyui_service,
            "_wait_for_comfyui_outputs",
            lambda *_args, **_kwargs: [{"filename": "result.png"}],
        )
        monkeypatch.setattr(
            comfyui_service,
            "_download_comfyui_image",
            lambda *_args, **_kwargs: (b"generated", ".png"),
        )
        monkeypatch.setattr(
            comfyui_service,
            "_import_comfyui_outputs",
            lambda *_args, **_kwargs: imported.append(True) or ([1], []),
        )
        monkeypatch.setattr(
            comfyui_service,
            "_emit_comfyui_failure_progress",
            lambda *_args, **_kwargs: failed.append(True),
        )
        try:
            server.library_switch.switch_to(library.uuid)
            comfyui_service._process_comfyui_outputs(
                server,
                "http://comfy.invalid",
                "old-prompt",
                None,
                None,
                None,
                origin_generation=origin.generation,
                origin_library_uuid=origin.library_uuid,
            )

            assert imported == []
            assert failed == []
            assert _picture_count(server) == 0
        finally:
            server.library_switch.switch_to(original.uuid)

    def test_stale_comfyui_failure_does_not_notify_new_library(
        self, server, tmp_path, monkeypatch
    ):
        from pixlstash.services import comfyui_service

        library = server.library_registry.create(
            str(tmp_path / "comfy-stale-failure"), "Comfy stale failure"
        )
        original = server.library_registry.active_library()
        origin = server.library_coordinator.acquire_read()
        assert origin is not None
        server.library_coordinator.release_read(origin)
        failed = []
        monkeypatch.setattr(
            comfyui_service,
            "_wait_for_comfyui_outputs",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
        )
        monkeypatch.setattr(
            comfyui_service,
            "_emit_comfyui_failure_progress",
            lambda *_args, **_kwargs: failed.append(True),
        )
        try:
            server.library_switch.switch_to(library.uuid)
            comfyui_service._process_comfyui_outputs(
                server,
                "http://comfy.invalid",
                "old-failure",
                None,
                None,
                None,
                origin_generation=origin.generation,
                origin_library_uuid=origin.library_uuid,
            )
            assert failed == []
        finally:
            server.library_switch.switch_to(original.uuid)

    def test_websockets_close_only_after_the_new_runtime_is_published(
        self, server, tmp_path, monkeypatch
    ):
        library = server.library_registry.create(
            str(tmp_path / "socket-publication"), "Socket publication"
        )
        original = server.library_registry.active_library()
        observed = []

        def observe_close(_clients):
            lease = server.library_coordinator.acquire_read()
            assert lease is not None, "a synchronous 1012 reload must be admitted"
            observed.append(
                (
                    server.library_coordinator.state,
                    server.library_registry.active_library().uuid,
                    server.vault.image_root,
                    server.auth.vault_db is server.vault.db,
                    lease.library_uuid,
                )
            )
            server.library_coordinator.release_read(lease)

        monkeypatch.setattr(
            server, "close_websocket_snapshot_for_switch", observe_close
        )
        try:
            server.library_switch.switch_to(library.uuid)
            assert observed[0] == (
                SwitchState.READY,
                library.uuid,
                library.path,
                True,
                library.uuid,
            )
        finally:
            server.library_switch.switch_to(original.uuid)

    def test_old_socket_snapshot_cannot_capture_new_generation_registration(
        self, server
    ):
        old_client = {"ws": object(), "loop": None}
        new_client = {"ws": object(), "loop": None}
        with server._ws_clients_lock:
            assert server._ws_clients == []
            server._ws_clients.append(old_client)

        snapshot = server.claim_websockets_for_switch()
        with server._ws_clients_lock:
            server._ws_clients.append(new_client)
        server.close_websocket_snapshot_for_switch(snapshot)

        with server._ws_clients_lock:
            assert server._ws_clients == [new_client]
            server._ws_clients.clear()


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
            assert observed == library.uuid
        finally:
            server.library_switch.switch_to(original.uuid)


class TestRefusingRequestsMidSwap:
    def test_writer_waits_for_request_paused_inside_authentication(
        self, server, tmp_path, monkeypatch
    ):
        from fastapi.testclient import TestClient

        target = server.library_registry.create(
            str(tmp_path / "auth-pause"), "Auth pause"
        )
        original = server.library_registry.active_library()
        auth_entered = threading.Event()
        release_auth = threading.Event()
        request_done = threading.Event()
        switch_done = threading.Event()
        errors = []

        def paused_token_lookup(_value):
            auth_entered.set()
            assert release_auth.wait(timeout=10)
            return None

        monkeypatch.setattr(server.auth, "_token_from_value", paused_token_lookup)

        def request():
            try:
                TestClient(server.api).get(
                    "/api/v1/pictures", headers={"Authorization": "Bearer paused"}
                )
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                request_done.set()

        def switch():
            try:
                server.library_switch.switch_to(target.uuid)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                switch_done.set()

        request_thread = threading.Thread(target=request)
        switch_thread = threading.Thread(target=switch)
        request_thread.start()
        assert auth_entered.wait(timeout=10)
        switch_thread.start()
        deadline = time.monotonic() + 5
        while server.library_coordinator.state is not SwitchState.SWITCHING:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not switch_done.is_set()
        assert server.vault.image_root == original.path

        release_auth.set()
        request_thread.join(timeout=10)
        switch_thread.join(timeout=20)
        assert request_done.is_set() and switch_done.is_set()
        assert errors == []
        assert server.vault.image_root == target.path
        server.library_switch.switch_to(original.uuid)

    def test_http_admission_refuses_before_authentication(self, server, monkeypatch):
        from fastapi.testclient import TestClient

        auth_calls = []

        def observed_auth(_value):
            auth_calls.append(True)
            return None

        monkeypatch.setattr(server.auth, "_token_from_value", observed_auth)
        server.library_coordinator.state = SwitchState.SWITCHING
        try:
            response = TestClient(server.api).get(
                "/api/v1/pictures",
                headers={"Authorization": "Bearer cannot-run"},
            )
        finally:
            server.library_coordinator.state = SwitchState.READY

        assert response.status_code == 503
        assert auth_calls == []

    def test_the_gate_returns_503_while_switching(self, server):
        """A request mid-swap must not be served against a half-swapped server."""
        from types import SimpleNamespace

        from fastapi import HTTPException

        from pixlstash.authz.gate import AuthzGate
        from pixlstash.authz.policy import AccessPolicy, RoutePolicy

        server.library_coordinator.state = SwitchState.SWITCHING
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
            server.library_coordinator.state = SwitchState.READY

    def test_library_independent_routes_still_answer_mid_swap(self, server):
        """Otherwise a client could not ask what is happening."""
        from types import SimpleNamespace

        from pixlstash.authz.gate import AuthzGate
        from pixlstash.authz.policy import AccessPolicy, RoutePolicy

        server.library_coordinator.state = SwitchState.SWITCHING
        try:
            AuthzGate._refuse_while_switching(
                SimpleNamespace(_server=server),
                SimpleNamespace(state=SimpleNamespace()),
                RoutePolicy(AccessPolicy.OWNER_ONLY, library_independent=True),
            )
        finally:
            server.library_coordinator.state = SwitchState.READY


def test_unrecoverable_post_retirement_failure_is_fatal_and_unavailable(
    tmp_path, monkeypatch
):
    """Never republish READY around a closed/mixed runtime tuple."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    with Server(str(tmp_path / "fatal-server.json")) as isolated:
        original = isolated.library_registry.active_library()
        target = isolated.library_registry.create(
            str(tmp_path / "fatal-target"), "Fatal target"
        )
        real_build = isolated.build_vault
        recovering = False

        def doomed_build(image_root):
            nonlocal recovering
            if image_root == original.path and recovering:
                raise RuntimeError("injected recovery build failure")
            vault = real_build(image_root)
            if image_root == target.path:
                real_start = vault.start

                def fail_after_retirement():
                    nonlocal recovering
                    recovering = True
                    # Keep the real method referenced so this remains clearly a
                    # candidate-start failure injection, not a build failure.
                    assert callable(real_start)
                    raise RuntimeError("injected candidate start failure")

                vault.start = fail_after_retirement
            return vault

        listener = SimpleNamespace(should_exit=False)
        isolated._uvicorn_servers = [listener]
        monkeypatch.setattr(isolated, "build_vault", doomed_build)

        with pytest.raises(LibrarySwitchError, match="Restart the server"):
            isolated.library_switch.switch_to(target.uuid)

        assert isolated.library_coordinator.state is SwitchState.UNAVAILABLE
        assert isolated.vault is None
        assert isolated.auth.vault_db is None
        assert isolated._fatal_shutdown_requested is True
        assert listener.should_exit is True

        def auth_must_not_run(_value):
            raise AssertionError("authentication ran before unavailable admission")

        monkeypatch.setattr(isolated.auth, "_token_from_value", auth_must_not_run)
        response = TestClient(isolated.api).get(
            "/api/v1/pictures", headers={"Authorization": "Bearer irrelevant"}
        )
        assert response.status_code == 503
        assert "no verified open library" in response.text

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
