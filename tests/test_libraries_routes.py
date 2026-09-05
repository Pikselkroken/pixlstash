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
from pixlstash.hub.registry import VAULT_FILENAME, LibraryExistsError
from pixlstash.utils.media_files import count_media_files, is_supported_media_file

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
        "/login", json={"username": "libowner", "password": "example-libowner-password"}
    )
    assert response.status_code == 200, response.text
    if client_ip:
        client.headers.update({"X-Forwarded-For": client_ip})
    return client


def _write_picture(path: Path) -> Path:
    """A real 1x1 image, so a count that reads bytes and one that reads names agree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1, 1)).save(path)
    return path


def _tree(folder: Path) -> set[tuple[str, int]]:
    """Every file under *folder* as (relative path, size).

    The shape "moves, renames and copies zero files" is asserted against: a move
    changes the relative path, a rename changes the name in it, and a copy adds
    a member. Sizes catch a rewrite in place.
    """
    return {
        (str(item.relative_to(folder)), item.stat().st_size)
        for item in folder.rglob("*")
        if item.is_file() and not item.name.startswith(f"{VAULT_FILENAME}-")
    }


def _make_vault(server, folder: Path) -> Path:
    """A folder holding a real vault that no library is registered against.

    Built through the registry so it is indistinguishable from one the server
    made, then deregistered - the row is dropped outright rather than detached,
    because a detached row would revive on attach and these tests are about the
    folder, not the registration.
    """
    library = server.library_registry.create(str(folder))
    with server.hub.transaction() as conn:
        conn.execute("DELETE FROM library WHERE id = ?", (library.id,))
    return folder


def _inspect(client: TestClient, folder: Path) -> dict:
    response = client.get(f"{API}/libraries/inspect", params={"path": str(folder)})
    assert response.status_code == 200, response.text
    return response.json()


def _add(client: TestClient, folder: Path, name: str | None = None) -> dict:
    """Add *folder*, creating it first the way the picker's `New folder` does.

    The route registers a library in a folder that is already there; it never
    creates one. Mirroring that here keeps the helper honest about the flow.
    """
    folder.mkdir(parents=True, exist_ok=True)
    body = {"path": str(folder)}
    if name is not None:
        body["name"] = name
    response = client.post(f"{API}/libraries", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def added_libraries(server):
    """Drop whatever a test registered, so the shared module does not accumulate.

    The rows are deleted rather than detached: a detached row revives when the
    same path is added again, and ``tmp_path`` is per-test, so leaving them would
    grow the registry a row per test and leave the listing tests describing a
    library that no longer exists on disk.
    """
    registered: list[dict] = []

    def _track(library: dict) -> dict:
        registered.append(library)
        return library

    yield _track

    with server.hub.transaction() as conn:
        for library in registered:
            # usertoken.library_uuid references library(uuid) with no ON DELETE
            # action, so a token stamped for one of these rows would make the
            # delete fail. These are the module's own fixture tokens.
            conn.execute(
                "DELETE FROM usertoken WHERE library_uuid = ?", (library["uuid"],)
            )
            conn.execute("DELETE FROM library WHERE uuid = ?", (library["uuid"],))


@pytest.fixture
def spare_library(server, tmp_path):
    """A second library, with the original restored afterwards.

    Restores the library that was active when the test started, rather than
    "any other one": the module also creates deliberately broken libraries, and
    switching into one of those during teardown would fail the test that had
    already passed.
    """
    original = server.library_registry.active_library()
    # Unique per test: the module keeps one registry for its whole run and a
    # library name has to be unique across it, so a fixed "Spare" would be
    # refused the second time this fixture is used.
    library = server.library_registry.create(
        str(tmp_path / "spare"), f"Spare {tmp_path.name}"
    )
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


class TestInspect:
    """One picker, five answers, and no mode for the owner to choose first."""

    def test_an_empty_folder_offers_a_fresh_library(self, server, tmp_path):
        folder = tmp_path / "nothing-here"
        folder.mkdir()

        body = _inspect(_owner(server), folder)

        assert body["verdict"] == "empty"
        assert body["can_add"] is True
        assert body["picture_count"] == 0
        assert body["suggested_name"] == "nothing-here"

    def test_a_folder_of_pictures_is_counted_through_its_subfolders(
        self, server, tmp_path
    ):
        folder = tmp_path / "shoots"
        (folder / "2024" / "mira").mkdir(parents=True)
        _write_picture(folder / "cover.jpg")
        _write_picture(folder / "2024" / "mira" / "01.png")
        _write_picture(folder / "2024" / "mira" / "02.webp")
        (folder / "2024" / "notes.txt").write_text("not a picture")

        body = _inspect(_owner(server), folder)

        assert body["verdict"] == "pictures"
        assert body["can_add"] is True
        assert body["picture_count"] == 3
        assert body["picture_count_capped"] is False
        assert "3 pictures" in body["headline"]

    def test_a_hidden_folder_is_not_counted(self, server, tmp_path):
        """``.pixlstash`` sidecars and thumbnail caches are not the owner's pictures."""
        folder = tmp_path / "with-sidecars"
        (folder / ".pixlstash" / "thumbnails").mkdir(parents=True)
        _write_picture(folder / ".pixlstash" / "thumbnails" / "cached.jpg")
        _write_picture(folder / "real.jpg")

        body = _inspect(_owner(server), folder)

        assert body["picture_count"] == 1

    def test_an_unregistered_vault_is_offered_as_it_is(self, server, tmp_path):
        folder = tmp_path / "made-earlier"
        _make_vault(server, folder)

        body = _inspect(_owner(server), folder)

        assert body["verdict"] == "vault"
        assert body["can_add"] is True

    def test_the_active_library_is_named_rather_than_offered(self, server):
        active = server.library_registry.active_library()

        body = _inspect(_owner(server), Path(active.path))

        assert body["verdict"] == "attached"
        assert body["can_add"] is False
        assert body["library"]["uuid"] == active.uuid
        assert active.name in body["detail"]

    def test_the_active_library_still_counts_what_is_on_disk(self, server):
        # A desktop first run puts the vault in a folder the owner already
        # kept pictures in, so the empty library needs to know they are there.
        active = server.library_registry.active_library()
        before = _inspect(_owner(server), Path(active.path))["picture_count"]
        _write_picture(Path(active.path) / "loose.jpg")
        try:
            body = _inspect(_owner(server), Path(active.path))
        finally:
            (Path(active.path) / "loose.jpg").unlink()

        assert body["verdict"] == "attached"
        assert body["picture_count"] == before + 1

    def test_a_folder_inside_an_attached_library_is_refused_by_name(
        self, server, added_libraries, tmp_path
    ):
        outer = tmp_path / "outer"
        (outer / "inner").mkdir(parents=True)
        added_libraries(_add(_owner(server), outer, name="Outer"))

        body = _inspect(_owner(server), outer / "inner")

        assert body["verdict"] == "overlaps"
        assert body["can_add"] is False
        assert "Outer" in body["detail"]

    def test_a_folder_containing_an_attached_library_is_refused_too(
        self, server, added_libraries, tmp_path
    ):
        """The overlap is symmetric; only one direction has design copy for it."""
        outer = tmp_path / "parent"
        inner = outer / "child"
        inner.mkdir(parents=True)
        added_libraries(_add(_owner(server), inner, name="Child"))

        body = _inspect(_owner(server), outer)

        assert body["verdict"] == "overlaps"
        assert body["can_add"] is False
        assert "Child" in body["detail"]

    def test_the_library_that_contains_the_folder_is_the_one_named(
        self, server, added_libraries, tmp_path
    ):
        """With a parent AND a child overlapping, the copy says "covers".

        `overlapping` returns registry order - active first, then name - which
        says nothing about the direction, so taking the first would sometimes
        name the child under a sentence claiming it contains the folder.
        """
        parent = tmp_path / "aaa-parent"
        middle = parent / "middle"
        child = middle / "zzz-child"
        child.mkdir(parents=True)
        # Registered through the registry, not the route: nesting is legal in
        # the registry and refused by the route, which is the whole reason this
        # message exists. Sorted so registry order (name) would pick the child.
        for folder, name in ((parent, "Zzz outer"), (child, "Aaa inner")):
            added_libraries(
                {"uuid": server.library_registry.create(str(folder), name).uuid}
            )

        body = _inspect(_owner(server), middle)

        assert body["verdict"] == "overlaps"
        assert body["detail"].startswith('"Zzz outer" covers this folder.')
        assert body["library"]["name"] == "Zzz outer"

    def test_a_system_directory_is_refused_by_the_shared_chokepoint(self, server):
        blocked = "C:\\Windows" if os.name == "nt" else "/etc"
        response = _owner(server).get(
            f"{API}/libraries/inspect", params={"path": blocked}
        )

        assert response.status_code == 400
        assert "restricted system directory" in response.json()["detail"]

    def test_a_symlink_cannot_smuggle_a_system_directory_past_the_blocklist(
        self, server, tmp_path
    ):
        """The blocklist compares literals, so it has to see the resolved path.

        Checking the string the caller sent let `~/link-to-etc` through, and
        `POST /libraries` on the result chmods the folder 0700 and writes a
        database into it. Reproduced before the fix: a 200 reading
        `{"verdict": "empty", "can_add": true}` for `/etc`.
        """
        if os.name == "nt":
            pytest.skip("the blocklist is drive-rooted on Windows")
        link = tmp_path / "innocent-looking"
        link.symlink_to("/etc")

        response = _owner(server).get(
            f"{API}/libraries/inspect", params={"path": str(link)}
        )

        assert response.status_code == 400
        assert "restricted system directory" in response.json()["detail"]
        assert (
            _owner(server)
            .post(f"{API}/libraries", json={"path": str(link)})
            .status_code
            == 400
        )

    def test_a_folder_outside_the_configured_roots_is_refused(
        self, server, tmp_path, monkeypatch
    ):
        """`filesystem_roots` confines this route as it confines the picker.

        An operator who fenced the folder browser did not mean "except for the
        route that can write a vault anywhere".
        """
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.setitem(server._server_config, "filesystem_roots", [str(allowed)])

        refused = _owner(server).get(
            f"{API}/libraries/inspect", params={"path": str(outside)}
        )
        assert refused.status_code == 403
        assert (
            _owner(server)
            .post(f"{API}/libraries", json={"path": str(outside)})
            .status_code
            == 403
        )
        # The positive control: inside a root still works, or the refusal above
        # would be indistinguishable from the feature being switched off.
        assert (
            _owner(server)
            .get(f"{API}/libraries/inspect", params={"path": str(allowed)})
            .status_code
            == 200
        )

    def test_a_relative_path_is_refused(self, server):
        response = _owner(server).get(
            f"{API}/libraries/inspect", params={"path": "Pictures"}
        )

        assert response.status_code == 400

    def test_a_folder_that_is_not_there_is_a_404(self, server, tmp_path):
        response = _owner(server).get(
            f"{API}/libraries/inspect", params={"path": str(tmp_path / "absent")}
        )

        assert response.status_code == 404

    def test_a_remote_owner_is_refused_and_a_local_one_is_not(self, server, tmp_path):
        """Both directions: over-blocking the owner on their own machine is a regression too."""
        folder = tmp_path / "locality"
        folder.mkdir()

        assert (
            _owner(server, REMOTE_IP)
            .get(f"{API}/libraries/inspect", params={"path": str(folder)})
            .status_code
            == 403
        )
        assert (
            _owner(server, TAILSCALE_IP)
            .get(f"{API}/libraries/inspect", params={"path": str(folder)})
            .status_code
            == 200
        )

    def test_an_anonymous_caller_is_refused(self, server, tmp_path):
        """With the positive control on the same path string.

        The READ-token middleware runs ahead of routing, so a request to a
        renamed or nonexistent route returns the same 403 as a genuine refusal
        - which would make this pass against a dead route. The owner's 200 on
        the identical URL is what proves the route is alive and the refusal is
        the gate's.
        """
        url = f"{API}/libraries/inspect"
        params = {"path": str(tmp_path)}

        assert _owner(server).get(url, params=params).status_code == 200, (
            "the route must be alive, or the refusal below proves nothing"
        )
        assert TestClient(server.api).get(url, params=params).status_code in (401, 403)


class TestAdding:
    def test_an_empty_folder_becomes_a_library_that_can_be_opened(
        self, server, added_libraries, tmp_path
    ):
        """`create`, not `register_pending`: an unreachable row cannot be switched to."""
        library = added_libraries(_add(_owner(server), tmp_path / "fresh"))

        assert library["is_reachable"] is True, (
            "a library the owner just added must not render as Not found"
        )
        assert os.path.isfile(
            os.path.join(
                server.library_registry.by_uuid(library["uuid"]).path, "vault.db"
            )
        )

    def test_attaching_moves_renames_and_copies_no_file(
        self, server, added_libraries, tmp_path
    ):
        """The release's headline, asserted rather than eyeballed."""
        folder = tmp_path / "curated"
        (folder / "2024 Shoots" / "Mira" / "final").mkdir(parents=True)
        _write_picture(folder / "2024 Shoots" / "Mira" / "final" / "01.jpg")
        _write_picture(folder / "2024 Shoots" / "loose.png")
        _make_vault(server, folder)
        before = _tree(folder)

        added_libraries(_add(_owner(server), folder))

        assert _tree(folder) == before

    def test_a_name_is_taken_from_the_folder_when_none_is_given(
        self, server, added_libraries, tmp_path
    ):
        library = added_libraries(_add(_owner(server), tmp_path / "Client work"))

        assert library["name"] == "Client work"

    def test_a_covered_folder_is_refused_in_the_registrys_own_words(
        self, server, added_libraries, tmp_path
    ):
        outer = tmp_path / "covered-outer"
        (outer / "inner").mkdir(parents=True)
        added_libraries(_add(_owner(server), outer, name="Covered outer"))

        response = _owner(server).post(
            f"{API}/libraries", json={"path": str(outer / "inner")}
        )

        assert response.status_code == 409
        assert "Covered outer" in response.json()["detail"]
        assert len(server.library_registry.overlapping(str(outer / "inner"))) == 1

    def test_a_folder_already_on_the_list_is_refused(
        self, server, added_libraries, tmp_path
    ):
        folder = tmp_path / "twice"
        added_libraries(_add(_owner(server), folder))

        response = _owner(server).post(f"{API}/libraries", json={"path": str(folder)})

        assert response.status_code == 409

    def test_a_name_already_in_use_is_refused_without_creating_anything(
        self, server, added_libraries, tmp_path
    ):
        added_libraries(_add(_owner(server), tmp_path / "first", name="Shared name"))
        second = tmp_path / "second"
        second.mkdir()

        response = _owner(server).post(
            f"{API}/libraries", json={"path": str(second), "name": "Shared name"}
        )

        assert response.status_code == 409
        assert not any(
            library.path == str(second.resolve())
            for library in server.library_registry.list_libraries()
        )
        assert not (second / VAULT_FILENAME).exists(), (
            "a refused add must not leave a vault behind, which would turn the "
            "folder into an attach case the owner never asked for"
        )

    def test_a_system_directory_is_refused(self, server):
        blocked = "C:\\Windows" if os.name == "nt" else "/etc"
        response = _owner(server).post(f"{API}/libraries", json={"path": blocked})

        assert response.status_code == 400

    def test_a_folder_that_is_not_there_is_a_404(self, server, tmp_path):
        response = _owner(server).post(
            f"{API}/libraries", json={"path": str(tmp_path / "never-made")}
        )

        assert response.status_code == 404

    def test_a_remote_owner_is_refused_and_a_local_one_is_not(
        self, server, added_libraries, tmp_path
    ):
        refused = tmp_path / "remote-refused"
        refused.mkdir()
        allowed = tmp_path / "tailscale-allowed"

        assert (
            _owner(server, REMOTE_IP)
            .post(f"{API}/libraries", json={"path": str(refused)})
            .status_code
            == 403
        )
        assert not os.path.isfile(refused / "vault.db"), (
            "a refused call must not have written a vault first"
        )
        added_libraries(_add(_owner(server, TAILSCALE_IP), allowed))

    def test_the_body_accepts_no_other_field(self, server, tmp_path):
        response = _owner(server).post(
            f"{API}/libraries",
            json={"path": str(tmp_path), "name": "x", "is_active": True},
        )
        assert response.status_code == 422


class TestRenaming:
    def test_a_rename_changes_the_label_and_nothing_on_disk(
        self, server, added_libraries, tmp_path
    ):
        folder = tmp_path / "before-rename"
        library = added_libraries(_add(_owner(server), folder, name="Old name"))
        before = _tree(folder)

        response = _owner(server).patch(
            f"{API}/libraries/{library['uuid']}", json={"name": "New name"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "New name"
        assert _tree(folder) == before
        assert folder.is_dir(), "renaming a library renames no folder"

    def test_a_name_already_in_use_is_refused(self, server, added_libraries, tmp_path):
        added_libraries(_add(_owner(server), tmp_path / "taken", name="Taken"))
        other = added_libraries(_add(_owner(server), tmp_path / "other", name="Other"))

        response = _owner(server).patch(
            f"{API}/libraries/{other['uuid']}", json={"name": "Taken"}
        )

        assert response.status_code == 409

    def test_an_empty_name_is_refused(self, server, added_libraries, tmp_path):
        library = added_libraries(_add(_owner(server), tmp_path / "keeps-its-name"))

        response = _owner(server).patch(
            f"{API}/libraries/{library['uuid']}", json={"name": "   "}
        )

        assert response.status_code == 400

    def test_a_row_id_is_not_a_handle(self, server, added_libraries, tmp_path):
        """The registry's CLI fallbacks must not be reachable over HTTP."""
        library = added_libraries(
            _add(_owner(server), tmp_path / "by-id", name="By id")
        )
        row_id = server.library_registry.by_uuid(library["uuid"]).id

        by_id = _owner(server).patch(
            f"{API}/libraries/{row_id}", json={"name": "renamed by row id"}
        )
        by_name = _owner(server).patch(
            f"{API}/libraries/By id", json={"name": "renamed by name"}
        )

        assert by_id.status_code == 404
        assert by_name.status_code == 404
        assert server.library_registry.by_uuid(library["uuid"]).name == "By id"


class TestDetaching:
    def test_detaching_removes_no_file_and_keeps_the_row(
        self, server, added_libraries, tmp_path
    ):
        folder = tmp_path / "forget-me"
        library = added_libraries(_add(_owner(server), folder))
        _write_picture(folder / "kept.jpg")
        before = _tree(folder)

        response = _owner(server).delete(f"{API}/libraries/{library['uuid']}")

        assert response.status_code == 200, response.text
        assert _tree(folder) == before
        kept = server.library_registry.by_uuid(library["uuid"])
        assert kept is not None and kept.attached is False, (
            "the row is kept so the tokens stamped with it survive"
        )
        assert library["uuid"] not in {
            entry["uuid"]
            for entry in _owner(server).get(f"{API}/libraries").json()["libraries"]
        }

    def test_adding_the_same_folder_again_revives_the_same_identity(
        self, server, added_libraries, tmp_path
    ):
        """Re-attaching must restore the share links, which means the same uuid."""
        folder = tmp_path / "comes-back"
        library = added_libraries(_add(_owner(server), folder))
        _owner(server).delete(f"{API}/libraries/{library['uuid']}")

        revived = added_libraries(_add(_owner(server), folder))

        assert revived["uuid"] == library["uuid"]

    def test_the_active_library_is_refused(self, server):
        active = server.library_registry.active_library()

        response = _owner(server).delete(f"{API}/libraries/{active.uuid}")

        assert response.status_code == 409
        assert "active library" in response.json()["detail"]
        assert server.library_registry.active_library().uuid == active.uuid

    def test_the_answer_says_how_many_share_links_go_inert(
        self, server, added_libraries, tmp_path
    ):
        library = added_libraries(_add(_owner(server), tmp_path / "shared"))
        client = _owner(server)
        minted = client.post(
            f"{API}/users/me/token",
            json={
                "description": "one shared set",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 1,
            },
        )
        assert minted.status_code == 200, minted.text
        # Minting is pinned to the active library, and this library is not it.
        # Repointing the one column is the whole fixture: what is under test is
        # the count the answer carries, not how a token comes to be stamped.
        with server.hub.transaction() as conn:
            conn.execute(
                "UPDATE usertoken SET library_uuid = ? WHERE description = ?",
                (library["uuid"], "one shared set"),
            )

        body = client.delete(f"{API}/libraries/{library['uuid']}").json()

        assert body["inert_share_links"] == 1

    def test_a_library_already_detached_is_a_404_rather_than_a_second_ok(
        self, server, added_libraries, tmp_path
    ):
        """`by_uuid` returns detached rows on purpose; these routes want the
        attached set, the one `GET /libraries` shows. Without the filter, a
        second DELETE answered 200 "ok" for a no-op and re-stamped
        `detached_at`, and PATCH renamed a row nobody can see onto the name of
        one they can - the duplicate check only inspects attached rows.
        """
        library = added_libraries(_add(_owner(server), tmp_path / "twice-forgotten"))
        assert (
            _owner(server).delete(f"{API}/libraries/{library['uuid']}").status_code
            == 200
        )

        again = _owner(server).delete(f"{API}/libraries/{library['uuid']}")
        renamed = _owner(server).patch(
            f"{API}/libraries/{library['uuid']}", json={"name": "Ghost"}
        )

        assert again.status_code == 404
        assert renamed.status_code == 404
        assert server.library_registry.by_uuid(library["uuid"]).name != "Ghost"

    def test_a_switch_in_flight_defers_the_detach_rather_than_racing_it(
        self, server, added_libraries, tmp_path, monkeypatch
    ):
        """These routes are HUB_ONLY, so the gate's switch 503 does not cover
        them - deliberately, so the registry answers when no vault is open.
        Detach is the one that cannot take the exemption: it reads `is_active`,
        and mid-swap that flag is moving.
        """
        library = added_libraries(_add(_owner(server), tmp_path / "mid-switch"))
        monkeypatch.setattr(
            type(server.library_switch), "is_switching", property(lambda _self: True)
        )

        response = _owner(server).delete(f"{API}/libraries/{library['uuid']}")

        assert response.status_code == 503
        assert server.library_registry.by_uuid(library["uuid"]).attached is True
        # `Retry-After` is set on the exception and deliberately not asserted
        # here: `Server._add_cors_exception_handler` rebuilds every HTTPException
        # as a JSONResponse and drops `exc.headers`, so no route's headers reach
        # a client today - the authz gate's own switch 503 loses the same one.
        # Pre-existing and worth its own change; the header stays so it works
        # the day that is fixed.

    def test_an_unknown_uuid_is_a_404(self, server):
        response = _owner(server).delete(f"{API}/libraries/not-a-library")
        assert response.status_code == 404

    def test_a_remote_owner_is_refused_and_a_local_one_is_not(
        self, server, added_libraries, tmp_path
    ):
        library = added_libraries(_add(_owner(server), tmp_path / "locality-detach"))

        assert (
            _owner(server, REMOTE_IP)
            .delete(f"{API}/libraries/{library['uuid']}")
            .status_code
            == 403
        )
        assert server.library_registry.by_uuid(library["uuid"]).attached is True
        assert (
            _owner(server, TAILSCALE_IP)
            .delete(f"{API}/libraries/{library['uuid']}")
            .status_code
            == 200
        )


class TestTheNameRuleDoesNotStrandARow:
    """The duplicate-name check must never fire after a committed write.

    `_register` frees a detached row's path - by rewriting it to
    `<path>#detached-<uuid>` - when a *different* library now sits there. That
    UPDATE commits. A name refusal after it would leave the old row at a path
    `_find_by_path` can never match again, so its uuid, and every share token
    stamped with it, would be stranded forever. That is precisely what
    `detach`'s docstring promises cannot happen: "Deleting the row instead would
    silently revoke share links the owner had handed out."
    """

    def test_a_refused_name_leaves_the_detached_rows_path_intact(
        self, server, added_libraries, tmp_path
    ):
        registry = server.library_registry
        folder = tmp_path / "swapped"
        original = added_libraries(_add(_owner(server), folder, name="Swapped"))
        resolved = registry.by_uuid(original["uuid"]).path
        _owner(server).delete(f"{API}/libraries/{original['uuid']}")

        # Record a fingerprint the folder does not carry, so re-registering this
        # path takes the "a different library now sits here" branch - the one
        # that frees the path - instead of reviving.
        with server.hub.transaction() as conn:
            conn.execute(
                "UPDATE library SET vault_uuid = ? WHERE uuid = ?",
                ("fingerprint-that-is-not-there", original["uuid"]),
            )
        added_libraries(_add(_owner(server), tmp_path / "holder", name="Taken"))

        with pytest.raises(LibraryExistsError):
            registry.attach(str(folder), "Taken")

        assert registry.by_uuid(original["uuid"]).path == resolved, (
            "the refusal must come before the commit, or this row's tokens are "
            "unreachable for the life of the installation"
        )

    def test_start_up_records_its_name_rather_than_refusing_to_boot(
        self, server, added_libraries, tmp_path
    ):
        """`register_pending` opts out, and it has to.

        `bootstrap._register_first_library` passes the hardcoded "Library 1"
        and does not catch `LibraryExistsError`, so refusing there would turn a
        duplicate label - a nuisance - into a server that will not start.
        """
        # Not the literal "Library 1": this module's own first library already
        # answers to it. The property under test is that `register_pending`
        # does not consult the rule, whatever the name is.
        added_libraries(_add(_owner(server), tmp_path / "first", name="Booted"))

        pending = server.library_registry.register_pending(
            str(tmp_path / "startup"), "Booted"
        )
        added_libraries({"uuid": pending.uuid})

        assert pending.name == "Booted"

    def test_start_up_survives_the_name_on_every_branch_it_can_take(
        self, server, added_libraries, tmp_path
    ):
        """All three, not just the fresh-registration one.

        `_register` reaches its write by one of three routes, and the flag has
        to hold on each: a fresh row, a revive (the path's detached row is
        provably the same library), and the path-mismatch branch that frees the
        path first. The #1096 review caught the third ignoring the flag; the
        revive had the identical bug one branch above it, on the same start-up
        path.
        """
        registry = server.library_registry
        added_libraries(_add(_owner(server), tmp_path / "holder", name="Contested"))

        revives = tmp_path / "revives"
        revives.mkdir()
        first = registry.register_pending(str(revives), "Contested one")
        added_libraries({"uuid": first.uuid})
        with server.hub.transaction() as conn:
            conn.execute(
                "UPDATE library SET attached = 0 WHERE uuid = ?", (first.uuid,)
            )

        # Revive branch: same path, fingerprints agree (neither carries one).
        revived = registry.register_pending(str(revives), "Contested")
        assert revived.uuid == first.uuid, "this must be the revive branch"
        assert revived.name == "Contested"

        # Path-mismatch branch: a fingerprint the folder does not carry.
        mismatched = tmp_path / "mismatched"
        mismatched.mkdir()
        stale = registry.register_pending(str(mismatched), "Contested two")
        added_libraries({"uuid": stale.uuid})
        with server.hub.transaction() as conn:
            conn.execute(
                "UPDATE library SET attached = 0, vault_uuid = ? WHERE uuid = ?",
                ("fingerprint-that-is-not-there", stale.uuid),
            )

        fresh = registry.register_pending(str(mismatched), "Contested")
        added_libraries({"uuid": fresh.uuid})
        assert fresh.uuid != stale.uuid, "this must be the path-mismatch branch"
        assert fresh.name == "Contested"


class TestCountingMediaFiles:
    def test_the_walk_stops_at_its_cap_and_says_so(self, tmp_path):
        """A picker has to answer while somebody is looking at it.

        The count must be **short of the total**, not merely capped-flagged: the
        first version of this counted every file in a directory before testing
        the cap, so a flat folder of half a million images - the exact shape
        this release is about - was walked in full while reporting `capped`.
        `0 < count <= 12` passed against that. `count < 12` does not.
        """
        for index in range(12):
            _write_picture(tmp_path / f"{index}.jpg")

        count, capped = count_media_files(str(tmp_path), entry_cap=4)

        assert capped is True
        assert count < 12, "the cap must bound work inside one directory too"

    def test_a_finished_walk_reports_an_exact_total(self, tmp_path):
        for index in range(3):
            _write_picture(tmp_path / f"{index}.jpg")

        assert count_media_files(str(tmp_path)) == (3, False)

    def test_our_own_thumbnails_are_never_counted_as_pictures(self, tmp_path):
        """One indexed thumbnail earns a thumbnail, and that one earns another.

        Found four generations deep in a real library (``x_thumb_thumb_thumb_
        thumb.webp``) after its folder was re-indexed in place.
        """
        _write_picture(tmp_path / "IMG_1231.PNG")
        _write_picture(tmp_path / "IMG_1231_thumb.webp")
        _write_picture(tmp_path / "IMG_1231_thumb_thumb.webp")
        _write_picture(tmp_path / "holiday.webp")

        assert is_supported_media_file("IMG_1231.PNG")
        assert is_supported_media_file("holiday.webp"), "a real .webp still counts"
        assert not is_supported_media_file("IMG_1231_thumb.webp")
        assert not is_supported_media_file("a/b/IMG_1231_THUMB.WEBP")
        assert count_media_files(str(tmp_path)) == (2, False)


class TestTheRequestContract:
    def test_an_unknown_field_is_rejected(self, server):
        response = _owner(server).post(
            f"{API}/libraries/active", json={"uuid": "x", "path": "/etc"}
        )
        assert response.status_code == 422, "the body must not accept a host path"

    def test_the_switch_route_takes_no_path_at_all(self):
        """Switching names a registry uuid, never a folder.

        ``POST /libraries`` and ``GET /libraries/inspect`` do accept a host path
        - that is the whole point of the picker - and are on the locality tier
        for it. This route is not, and its body must stay closed so it cannot
        drift onto that tier by accident.
        """
        from pixlstash.routes.libraries import SwitchLibraryRequest

        assert set(SwitchLibraryRequest.model_fields) == {"uuid"}


class TestCliHintIsShort:
    """The hint is read at a glance in a settings panel, so it stays short.

    It used to print the absolute interpreter path of the environment the server
    was started from. That is the most precise answer and the least useful one:
    on a venv install it wrapped the panel, and the verb (the part being taught)
    ended up behind boilerplate the reader already knows.
    """

    def _module(self):
        # pixlstash.hub re-exports the cli_hint FUNCTION, which shadows the
        # submodule name, so a plain `import ... as` would bind the function.
        return importlib.import_module("pixlstash.hub.cli_hint")

    def test_no_interpreter_path_when_a_console_script_exists(
        self, monkeypatch, tmp_path
    ):
        mod = self._module()
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python3").write_text("")
        (venv_bin / mod.CONSOLE_SCRIPT).write_text("")

        monkeypatch.setattr(mod.sys, "executable", str(venv_bin / "python3"))
        monkeypatch.setattr(mod.shutil, "which", lambda _n: None)
        monkeypatch.setattr(mod, "running_in_docker", lambda: False)

        hint = mod.cli_hint()

        assert hint == f"{mod.CONSOLE_SCRIPT} libraries list"
        assert str(tmp_path) not in hint, "no absolute path may survive"

    def test_a_source_checkout_gets_the_module_invocation_not_a_path(
        self, monkeypatch, tmp_path
    ):
        mod = self._module()
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python3").write_text("")

        monkeypatch.setattr(mod.sys, "executable", str(venv_bin / "python3"))
        monkeypatch.setattr(mod.shutil, "which", lambda _n: None)
        monkeypatch.setattr(mod, "running_in_docker", lambda: False)

        hint = mod.cli_hint()

        assert hint == f"python {mod.MODULE_INVOCATION} libraries list"
        assert str(tmp_path) not in hint

    def test_a_frozen_desktop_build_still_needs_its_bundled_executable(
        self, monkeypatch, tmp_path
    ):
        """The one case a path is right: no console script, and no ``python``."""
        mod = self._module()
        bundled = tmp_path / "PixlStash" / "pixlstash-backend"
        bundled.parent.mkdir(parents=True)
        bundled.write_text("")

        monkeypatch.setattr(mod.sys, "executable", str(bundled))
        monkeypatch.setattr(mod.sys, "frozen", True, raising=False)
        monkeypatch.setattr(mod, "running_in_docker", lambda: False)

        hint = mod.cli_hint()

        assert mod.MODULE_INVOCATION in hint
        assert "pixlstash-backend" in hint

    def test_a_launcher_that_declares_its_command_wins_over_every_guess(
        self, monkeypatch
    ):
        """The desktop app knows the answer; this module cannot work it out.

        Its console script is sealed inside the app image at a path that changes
        every launch, and its hub is not the platform default, so both the bare
        name and any ``sys.executable`` derivation would print a command that
        edits the wrong registry or does not run at all.
        """
        mod = self._module()
        # Docker detection is deliberately not consulted first: a declared
        # command is the launcher speaking for itself.
        monkeypatch.setattr(mod, "running_in_docker", lambda: True)
        monkeypatch.setenv("PIXLSTASH_CLI_COMMAND", "pixlstash")

        assert mod.cli_hint() == "pixlstash libraries list"

    def test_a_declared_command_is_printed_exactly_as_given(self, monkeypatch):
        """A launcher's own quoting must survive untouched.

        Re-quoting or stripping any of it would break the one instruction the
        Libraries panel gives (multi-library plan §3.6 acceptance 8).
        """
        mod = self._module()
        declared = "& 'C:\\Program Files\\PixlStash\\PixlStash.exe' cli"
        monkeypatch.setenv("PIXLSTASH_CLI_COMMAND", declared)

        assert mod.cli_hint() == f"{declared} libraries list"

    def _windows_desktop(self, monkeypatch, tmp_path, mod):
        """Pose as the bundled Windows desktop runtime.

        The layout is the packaged one: ``<resources>/python/python.exe`` with
        ``runtime.json`` beside the ``python`` directory, which is the marker
        only that build writes.
        """
        resources = tmp_path / "resources"
        (resources / "python").mkdir(parents=True)
        interpreter = resources / "python" / "python.exe"
        interpreter.write_text("")
        (resources / "runtime.json").write_text("{}")
        monkeypatch.setattr(mod.os, "name", "nt")
        monkeypatch.setattr(mod.sys, "executable", str(interpreter))
        monkeypatch.delenv("PIXLSTASH_CLI_COMMAND", raising=False)
        return str(interpreter)

    def test_the_windows_desktop_names_its_console_interpreter_not_the_launcher(
        self, monkeypatch, tmp_path
    ):
        """Issue #1058, the half that made the output overlap the prompt.

        ``PixlStash.exe`` is linked for the Windows GUI subsystem, so no shell
        waits for it: the prompt returns and the CLI's output then lands on top
        of it. The bundled ``python.exe`` is console-subsystem and at a durable
        path, so naming it is what makes the shell wait.
        """
        mod = self._module()
        interpreter = self._windows_desktop(monkeypatch, tmp_path, mod)
        hub = "C:\\Users\\me\\AppData\\Roaming\\PixlStash\\hub.db"

        hint = mod.cli_hint(hub_path=hub)

        assert hint == (
            f"& '{interpreter}' {mod.MODULE_INVOCATION} --hub '{hub}' libraries list"
        )
        assert "PixlStash.exe" not in hint, "the GUI launcher is what broke this"
        assert hint.startswith("& "), "PowerShell needs the call operator"

    def test_the_windows_hub_is_named_because_it_is_not_the_platform_default(
        self, monkeypatch, tmp_path
    ):
        """Omitting --hub would edit the wrong registry, silently."""
        mod = self._module()
        self._windows_desktop(monkeypatch, tmp_path, mod)

        assert "--hub" in mod.cli_hint(hub_path="C:\\hub.db")

    def test_a_windows_path_with_a_quote_is_doubled_not_backslash_escaped(
        self, monkeypatch, tmp_path
    ):
        """PowerShell's single-quoted string is literal; ``''`` is its escape."""
        mod = self._module()
        self._windows_desktop(monkeypatch, tmp_path, mod)

        hint = mod.cli_hint(hub_path="C:\\Users\\o'brien\\hub.db")

        assert "o''brien" in hint
        assert "o\\'brien" not in hint

    def test_a_system_python_on_windows_is_not_mistaken_for_the_desktop(
        self, monkeypatch, tmp_path
    ):
        """Without the bundle's runtime.json this is somebody's own Python."""
        mod = self._module()
        self._windows_desktop(monkeypatch, tmp_path, mod)
        (tmp_path / "resources" / "runtime.json").unlink()

        assert mod.desktop_windows_command("C:\\hub.db") is None

    def test_the_desktop_branch_never_fires_off_windows(self, monkeypatch, tmp_path):
        """POSIX keeps the console script; the AppImage keeps its launcher."""
        mod = self._module()
        self._windows_desktop(monkeypatch, tmp_path, mod)
        monkeypatch.setattr(mod.os, "name", "posix")

        assert mod.desktop_windows_command("/home/me/hub.db") is None

    def test_a_declaration_still_outranks_the_derived_windows_command(
        self, monkeypatch, tmp_path
    ):
        """A launcher that speaks for itself is still the more specific answer."""
        mod = self._module()
        self._windows_desktop(monkeypatch, tmp_path, mod)
        monkeypatch.setenv("PIXLSTASH_CLI_COMMAND", "pixlstash")

        assert mod.cli_hint(hub_path="C:\\hub.db") == "pixlstash libraries list"

    def test_an_empty_declaration_falls_through_to_the_normal_rules(
        self, monkeypatch, tmp_path
    ):
        """An env var set to nothing must not print a hint that starts with a space."""
        mod = self._module()
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python3").write_text("")
        (venv_bin / mod.CONSOLE_SCRIPT).write_text("")

        monkeypatch.setenv("PIXLSTASH_CLI_COMMAND", "   ")
        monkeypatch.setattr(mod.sys, "executable", str(venv_bin / "python3"))
        monkeypatch.setattr(mod.shutil, "which", lambda _n: None)
        monkeypatch.setattr(mod, "running_in_docker", lambda: False)

        assert mod.cli_hint() == f"{mod.CONSOLE_SCRIPT} libraries list"

    def test_docker_keeps_the_container_name_it_cannot_infer(self, monkeypatch):
        mod = self._module()
        monkeypatch.setattr(mod, "running_in_docker", lambda: True)
        monkeypatch.setenv("HOSTNAME", "pixlstash-1")

        hint = mod.cli_hint()

        assert (
            hint == f"docker exec -it pixlstash-1 {mod.CONSOLE_SCRIPT} libraries list"
        )
