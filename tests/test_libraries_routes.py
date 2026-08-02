"""The two library routes, and the locality rule that shapes their responses.

Both directions are asserted throughout. A local caller must keep seeing paths
and a working Switch, because over-blocking the owner on their own machine is as
much a regression as leaking host layout to a remote one.
"""

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from pixlstash.db_models import User, UserToken
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

    def test_an_anonymous_caller_is_refused(self, server):
        assert TestClient(server.api).get(f"{API}/libraries").status_code in (401, 403)


class TestSwitching:
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
