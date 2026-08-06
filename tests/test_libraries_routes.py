"""The two library routes, and the locality rule that shapes their responses.

Both directions are asserted throughout. A local caller must keep seeing paths
and a working Switch, because over-blocking the owner on their own machine is as
much a regression as leaking host layout to a remote one.
"""

import importlib
import io
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, delete, select

from pixlstash.db_models import Picture, User, UserToken
from pixlstash.server import Server

API = "/api/v1"

# A genuinely globally-routable address, so the locality predicate says "not
# local". Deliberately NOT a documentation range like 203.0.113.x: Python's
# ``ipaddress`` reports those as ``is_private`` (it folds IANA special-purpose
# ranges in), so ``is_local_ip`` counts them as LOCAL and a test using one would
# silently assert the opposite of what it reads like. The rest of the suite uses
# 8.8.8.8 for the same reason.
REMOTE_IP = "8.8.8.8"
# Tailscale CGNAT, which the §16.3 predicate deliberately counts as local.
TAILSCALE_IP = "100.101.102.103"


@pytest.fixture(scope="module")
def server():
    """A server that trusts the test client as a proxy.

    Without ``trusted_proxies`` the ``X-Forwarded-For`` header is ignored, which
    is the correct production behaviour (a client must not be able to spoof its
    way to "local"). Naming the test client as a trusted proxy is what lets these
    tests present a chosen client IP at all.
    """
    import json

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/server-config.json"
        with open(config_path, "w") as handle:
            json.dump({"trusted_proxies": ["testclient"]}, handle)
        with Server(config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean_auth(server):
    def _wipe(session: Session):
        session.exec(delete(UserToken))
        session.exec(delete(User))
        session.commit()

    server.hub_engine.run_task(_wipe)
    server.auth.password_hash = None
    server.auth.username = None
    server.auth.user = None
    server.auth._clear_all_sessions()
    server.auth._flush_token_cache()
    server.auth._failed_login_attempts = 0
    server.auth._login_lockout_until = 0.0
    server.auth.ensure_user()
    yield


def _owner(server, client_ip: str | None = None) -> TestClient:
    """An owner client, optionally presenting a spoofed forwarded address.

    The header is attached per request rather than to the client, because the
    login itself must arrive from the default (local) address: a first-owner
    claim is loopback-gated, and spoofing that too would be testing the wrong
    thing.
    """
    client = TestClient(server.api)
    response = client.post(
        "/login", json={"username": "libowner", "password": "libownerpass1"}
    )
    assert response.status_code == 200, response.text
    if client_ip:
        client.headers.update({"X-Forwarded-For": client_ip})
    return client


@pytest.fixture
def spare_library(server, tmp_path):
    """A second library, with the original restored afterwards.

    Restores the library that was active when the test started, rather than
    "any other one": the module also creates deliberately broken libraries, and
    switching into one of those during teardown would fail the test that had
    already passed.
    """
    original = server.library_registry.active_library()
    library = server.library_registry.create(str(tmp_path / "spare"), "Spare")
    yield library
    active = server.library_registry.active_library()
    if original and active and active.uuid != original.uuid:
        server.library_switch.switch_to(original.uuid)


class TestListing:
    def test_a_local_owner_sees_paths_and_the_cli_hint(self, server):
        body = _owner(server).get(f"{API}/libraries").json()

        assert body["libraries"], "at least one library is always registered"
        assert body["libraries"][0]["path"], "a local caller sees the folder"
        assert body["cli_hint"], "a local caller is told the exact command"
        assert body["can_manage"] is True

    def test_a_tailscale_owner_counts_as_local(self, server):
        """The §16.3 predicate includes Tailscale, so a phone behaves like the desktop."""
        body = _owner(server, TAILSCALE_IP).get(f"{API}/libraries").json()

        assert body["can_manage"] is True
        assert body["libraries"][0]["path"]

    def test_a_remote_owner_sees_neither_path_nor_hint(self, server):
        body = _owner(server, REMOTE_IP).get(f"{API}/libraries").json()

        assert body["libraries"], "the list still renders; it is not a dead end"
        assert body["libraries"][0]["path"] is None
        assert body["cli_hint"] is None
        assert body["can_manage"] is False

    def test_a_remote_owner_still_learns_names_and_which_is_active(self, server):
        """Enough to render the tab, which is why this route is not local-only."""
        body = _owner(server, REMOTE_IP).get(f"{API}/libraries").json()

        entry = body["libraries"][0]
        assert entry["name"]
        assert entry["uuid"]
        assert "is_active" in entry

    def test_the_response_names_libraries_by_uuid_not_row_id(self, server):
        entry = _owner(server).get(f"{API}/libraries").json()["libraries"][0]
        assert "id" not in entry, "a row id must never be the client's handle"

    def test_share_link_counts_are_available_before_switch_without_path_locality(
        self, server, spare_library
    ):
        client = _owner(server)
        active = server.library_registry.active_library()
        created = client.post(
            f"{API}/users/me/token",
            json={
                "description": "one shared set",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 1,
            },
        )
        assert created.status_code == 200, created.text
        expired = client.post(
            f"{API}/users/me/token",
            json={
                "description": "expired shared set",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 2,
                "expires_at": "2020-01-01T00:00:00",
            },
        )
        assert expired.status_code == 200, expired.text

        local_entries = {
            entry["uuid"]: entry
            for entry in client.get(f"{API}/libraries").json()["libraries"]
        }
        remote_entries = {
            entry["uuid"]: entry
            for entry in _owner(server, REMOTE_IP)
            .get(f"{API}/libraries")
            .json()["libraries"]
        }
        assert local_entries[active.uuid]["active_share_links"] == 1
        assert local_entries[spare_library.uuid]["active_share_links"] == 0
        assert remote_entries[active.uuid]["active_share_links"] == 1
        assert remote_entries[active.uuid]["path"] is None

    def test_an_anonymous_caller_is_refused(self, server):
        assert TestClient(server.api).get(f"{API}/libraries").status_code in (401, 403)


class TestSwitching:
    def test_staging_session_from_previous_generation_is_inaccessible_and_removed(
        self, server, spare_library
    ):
        client = _owner(server)
        original = server.library_registry.active_library()
        opened = client.post(f"{API}/pictures/import/staging", json={})
        assert opened.status_code == 200, opened.text
        staging_id = opened.json()["staging_id"]
        staging_dir = server.staging_sessions[staging_id]["staging_dir"]
        assert os.path.isdir(staging_dir)
        try:
            server.library_switch.switch_to(spare_library.uuid)
            for method, suffix in (
                ("get", "/status"),
                ("post", "/commit"),
                ("delete", ""),
            ):
                response = getattr(client, method)(
                    f"{API}/pictures/import/staging/{staging_id}{suffix}"
                )
                assert response.status_code == 404
            staged = client.post(
                f"{API}/pictures/import/staging/{staging_id}/files",
                files={"file": ("stale.png", b"stale", "image/png")},
            )
            assert staged.status_code == 404
            assert not os.path.exists(staging_dir)
        finally:
            if server.library_registry.active_library().uuid != original.uuid:
                server.library_switch.switch_to(original.uuid)

    def test_switch_discards_private_export_artifact_and_stale_status(
        self, server, spare_library, tmp_path
    ):
        client = _owner(server)
        original = server.library_registry.active_library()
        lease = server.library_coordinator.acquire_read()
        assert lease is not None
        server.library_coordinator.release_read(lease)
        private_dir = Path(
            tempfile.mkdtemp(prefix="pixlstash_export_test_", dir=tmp_path)
        )
        artifact = private_dir / "old.zip"
        artifact.write_bytes(b"old library")
        os.chmod(artifact, 0o600)
        task_id = "old-generation-export"
        server.export_tasks[task_id] = {
            "status": "completed",
            "file_path": str(artifact),
            "filename": "old.zip",
            "total": 1,
            "processed": 1,
            "private_dir": str(private_dir),
            "library_uuid": lease.library_uuid,
            "generation": lease.generation,
        }
        try:
            server.library_switch.switch_to(spare_library.uuid)
            assert (
                client.get(
                    f"{API}/pictures/export/status", params={"task_id": task_id}
                ).status_code
                == 404
            )
            assert not private_dir.exists()
            assert task_id not in server.export_tasks
        finally:
            if server.library_registry.active_library().uuid != original.uuid:
                server.library_switch.switch_to(original.uuid)

    def test_switch_waits_for_detached_import_and_never_cross_writes(
        self, server, spare_library, monkeypatch
    ):
        from pixlstash.routes.pictures import _import as import_routes

        client = _owner(server)
        original = server.library_registry.active_library()
        old_count = server.vault.db.run_immediate_read_task(
            lambda session: len(session.exec(select(Picture)).all())
        )
        entered = threading.Event()
        release = threading.Event()
        switched = threading.Event()
        errors = []
        real_create = import_routes._create_picture_imports

        def paused_create(*args, **kwargs):
            entered.set()
            assert release.wait(timeout=10)
            return real_create(*args, **kwargs)

        monkeypatch.setattr(import_routes, "_create_picture_imports", paused_create)
        image = Image.new("RGB", (8, 8), (12, 34, 56))
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
        response = client.post(
            f"{API}/pictures/import",
            files={"file": ("lease.png", encoded.getvalue(), "image/png")},
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["task_id"]
        assert entered.wait(timeout=10)

        def do_switch():
            try:
                server.library_switch.switch_to(spare_library.uuid)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                switched.set()

        switch_thread = threading.Thread(target=do_switch)
        switch_thread.start()
        deadline = time.monotonic() + 5
        while server.library_coordinator.state.value != "switching":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not switched.is_set()

        release.set()
        switch_thread.join(timeout=20)
        try:
            assert not errors
            assert switched.is_set()
            assert server.library_registry.active_library().uuid == spare_library.uuid
            assert (
                server.vault.db.run_immediate_read_task(
                    lambda session: len(session.exec(select(Picture)).all())
                )
                == 0
            )
            assert (
                client.get(
                    f"{API}/pictures/import/status", params={"task_id": task_id}
                ).status_code
                == 404
            )
        finally:
            if server.library_registry.active_library().uuid != original.uuid:
                server.library_switch.switch_to(original.uuid)
        assert (
            server.vault.db.run_immediate_read_task(
                lambda session: len(session.exec(select(Picture)).all())
            )
            == old_count + 1
        )

    def test_a_local_owner_can_switch(self, server, spare_library):
        client = _owner(server)

        response = client.post(
            f"{API}/libraries/active", json={"uuid": spare_library.uuid}
        )

        assert response.status_code == 200, response.text
        assert response.json()["library"]["uuid"] == spare_library.uuid
        assert server.vault.image_root == spare_library.path

    def test_a_remote_owner_is_refused_by_the_gate(self, server, spare_library):
        """Local-only, and the message names the setting that would allow it."""
        client = _owner(server, REMOTE_IP)

        response = client.post(
            f"{API}/libraries/active", json={"uuid": spare_library.uuid}
        )

        assert response.status_code == 403
        assert "allow_remote_host_ops" in response.text

    def test_switching_to_an_unknown_library_is_404(self, server):
        client = _owner(server)
        response = client.post(
            f"{API}/libraries/active",
            json={"uuid": "00000000-0000-4000-8000-000000000000"},
        )
        assert response.status_code == 404

    def test_a_library_that_cannot_be_opened_is_409_and_changes_nothing(
        self, server, tmp_path
    ):
        """Well-formed request, permitted caller, unopenable library."""
        import os

        library = server.library_registry.create(str(tmp_path / "broken"), "Broken")
        os.remove(os.path.join(library.path, "vault.db"))
        before = server.vault
        client = _owner(server)

        response = client.post(f"{API}/libraries/active", json={"uuid": library.uuid})

        assert response.status_code == 409
        assert server.vault is before, "the session stays on its library"

    def test_overlapping_switch_posts_finish_without_deadlock(
        self, server, tmp_path, monkeypatch
    ):
        original = server.library_registry.active_library()
        first_target = server.library_registry.create(
            str(tmp_path / "concurrent-first"), "Concurrent first"
        )
        second_target = server.library_registry.create(
            str(tmp_path / "concurrent-second"), "Concurrent second"
        )
        first_client = _owner(server)
        second_client = _owner(server)
        build_entered = threading.Event()
        release_build = threading.Event()
        real_build = server.build_vault
        blocked_once = False

        def blocking_build(image_root):
            nonlocal blocked_once
            if image_root == first_target.path and not blocked_once:
                blocked_once = True
                build_entered.set()
                assert release_build.wait(timeout=10)
            return real_build(image_root)

        monkeypatch.setattr(server, "build_vault", blocking_build)
        responses = {}

        def post(name, client, target):
            responses[name] = client.post(
                f"{API}/libraries/active", json={"uuid": target.uuid}
            )

        first_thread = threading.Thread(
            target=post, args=("first", first_client, first_target)
        )
        second_thread = threading.Thread(
            target=post, args=("second", second_client, second_target)
        )
        first_thread.start()
        try:
            assert build_entered.wait(timeout=10)
            second_thread.start()
            second_thread.join(timeout=5)
            assert not second_thread.is_alive(), "second switch must fail promptly"
        finally:
            release_build.set()
        first_thread.join(timeout=20)
        second_thread.join(timeout=20)

        try:
            assert not first_thread.is_alive()
            assert not second_thread.is_alive()
            assert responses["first"].status_code == 200
            assert responses["second"].status_code == 409
            assert "already in progress" in responses["second"].text
            assert server.library_registry.active_library().uuid == first_target.uuid
            assert server.vault.image_root == first_target.path
        finally:
            if server.library_registry.active_library().uuid != original.uuid:
                server.library_switch.switch_to(original.uuid)

    def test_the_response_reports_share_links_left_behind(self, server, spare_library):
        """The owner is the only person who can see their links go dark."""
        client = _owner(server)

        response = client.post(
            f"{API}/libraries/active", json={"uuid": spare_library.uuid}
        )

        assert response.status_code == 200
        assert "active_share_links" in response.json()

    def test_a_switch_tells_every_client_to_reload(self, server, spare_library):
        """Picture ids do not carry across libraries, so a reload is the only
        honest instruction."""
        from pixlstash.event_types import EventType

        seen = []
        original = server.handle_vault_event
        server.handle_vault_event = lambda event, data=None: seen.append(event)
        try:
            _owner(server).post(
                f"{API}/libraries/active", json={"uuid": spare_library.uuid}
            )
        finally:
            server.handle_vault_event = original

        assert EventType.LIBRARY_SWITCHED in seen

    def test_switching_to_the_already_active_library_broadcasts_nothing(self, server):
        from pixlstash.event_types import EventType

        active = server.library_registry.active_library()
        seen = []
        original = server.handle_vault_event
        server.handle_vault_event = lambda event, data=None: seen.append(event)
        try:
            response = _owner(server).post(
                f"{API}/libraries/active", json={"uuid": active.uuid}
            )
        finally:
            server.handle_vault_event = original

        assert response.status_code == 200
        assert EventType.LIBRARY_SWITCHED not in seen


class TestTheRequestContract:
    def test_an_unknown_field_is_rejected(self, server):
        response = _owner(server).post(
            f"{API}/libraries/active", json={"uuid": "x", "path": "/etc"}
        )
        assert response.status_code == 422, "the body must not accept a host path"

    def test_the_route_takes_no_path_at_all(self):
        """The MVP's whole HTTP surface accepts no filesystem path."""
        from pixlstash.routes.libraries import SwitchLibraryRequest

        assert set(SwitchLibraryRequest.model_fields) == {"uuid"}


class TestCliHintLength:
    """The hint is copy-pasted, so it has to stay both short and runnable.

    A venv install that is not on PATH prints an absolute interpreter path. Most
    of that is the reader's own home directory, and the full string wraps the
    settings panel.
    """

    def test_home_is_abbreviated_and_stays_expandable(self, monkeypatch, tmp_path):
        # pixlstash.hub re-exports the cli_hint FUNCTION, which shadows the
        # submodule name, so a plain `import ... as` would bind the function.
        cli_hint_module = importlib.import_module("pixlstash.hub.cli_hint")

        home = tmp_path / "home" / "someone"
        venv_bin = home / "Projects" / "app" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        interpreter = venv_bin / "python3"
        interpreter.write_text("")

        monkeypatch.setattr(cli_hint_module.os.path, "expanduser", lambda _p: str(home))
        monkeypatch.setattr(cli_hint_module.sys, "executable", str(interpreter))
        monkeypatch.setattr(cli_hint_module.shutil, "which", lambda _n: None)
        monkeypatch.setattr(cli_hint_module, "running_in_docker", lambda: False)

        hint = cli_hint_module.cli_hint()

        assert hint.startswith("~/"), f"home should collapse to ~, got {hint}"
        assert str(home) not in hint
        assert hint.endswith("libraries list")

    def test_a_quoted_path_keeps_the_tilde_outside_the_quotes(self):
        """``shlex.quote('~/x')`` yields ``'~/x'``, which no shell expands."""
        _quote = importlib.import_module("pixlstash.hub.cli_hint")._quote

        quoted = _quote("~/Projects/my dir/bin/python")

        assert quoted.startswith("~/"), "a quoted tilde stops being the home dir"
        assert "my dir" in quoted

    def test_the_console_script_beside_the_interpreter_wins(
        self, monkeypatch, tmp_path
    ):
        """Shorter than spelling out the module invocation, and the same env."""
        # pixlstash.hub re-exports the cli_hint FUNCTION, which shadows the
        # submodule name, so a plain `import ... as` would bind the function.
        cli_hint_module = importlib.import_module("pixlstash.hub.cli_hint")

        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python3").write_text("")
        (venv_bin / cli_hint_module.CONSOLE_SCRIPT).write_text("")

        monkeypatch.setattr(
            cli_hint_module.sys, "executable", str(venv_bin / "python3")
        )
        monkeypatch.setattr(cli_hint_module.shutil, "which", lambda _n: None)
        monkeypatch.setattr(cli_hint_module, "running_in_docker", lambda: False)

        hint = cli_hint_module.cli_hint()

        assert cli_hint_module.CONSOLE_SCRIPT in hint
        assert cli_hint_module.MODULE_INVOCATION not in hint
