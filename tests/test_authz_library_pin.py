"""The library pin: a token authenticates only while its library is active.

Every token belongs to exactly one library (multi-library plan §4). Without the
pin, switching library would silently change what an existing token grants: a
share link would start serving somebody else's pictures, and an automation
holding an ALL token would write into a library the owner never pointed it at.

Both directions are asserted throughout, because over-blocking is its own
regression: a token for the *active* library must keep working, and a cookie
session must keep following the switch, which is the whole point of switching.
"""

import sqlite3
import tempfile
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from pixlstash.authz.policy import AccessPolicy, LibraryAccessMode, RoutePolicy
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.db_models import User, UserToken
from pixlstash.server import Server
from pixlstash.services.library_switch_service import SwitchState

API = "/api/v1"


@pytest.fixture(scope="module")
def server():
    """One Server for the module; building it runs migrations and vault startup."""
    with tempfile.TemporaryDirectory() as temp_dir:
        with Server(f"{temp_dir}/server-config.json") as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean_auth(server):
    """Start each test from a claimed owner with no tokens."""

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


def _owner_client(server) -> TestClient:
    """A client logged in as the owner."""
    client = TestClient(server.api)
    response = client.post(
        "/login", json={"username": "pinowner", "password": "example-pinowner-password"}
    )
    assert response.status_code == 200, response.text
    return client


def _mint(owner_client, scope="ALL") -> str:
    """Mint a token and return its raw value."""
    response = owner_client.post(
        "/users/me/token", json={"description": "pin test", "scope": scope}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.fixture(scope="module")
def other_library(server, tmp_path_factory):
    """A second registered library, so a token can be stamped for a real one.

    Stamping with an invented uuid is impossible by design: the hub's foreign
    key refuses a token that names a library which does not exist.
    """
    folder = tmp_path_factory.mktemp("other-library")
    conn = sqlite3.connect(str(folder / "vault.db"))
    conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES ('0093_guest_tables')")
    conn.execute("CREATE TABLE picture (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return server.library_registry.attach(str(folder), "Other").uuid


def _restamp_tokens(server, library_uuid: str) -> None:
    """Point every token at *library_uuid*, simulating a different library."""

    def _update(session: Session):
        for token in session.exec(select(UserToken)).all():
            token.library_uuid = library_uuid
            session.add(token)
        session.commit()

    server.hub_engine.run_task(_update)
    server.auth._flush_token_cache()


class TestTheStamp:
    def test_a_minted_token_carries_the_active_library(self, server):
        owner = _owner_client(server)
        _mint(owner)

        stamped = server.hub_engine.run_immediate_read_task(
            lambda session: session.exec(select(UserToken)).first().library_uuid
        )
        assert stamped == server.auth.active_library_uuid()


class TestPinnedRoutes:
    def test_a_token_for_the_active_library_works(self, server):
        """The positive direction. Over-blocking here would be the regression."""
        owner = _owner_client(server)
        token = _mint(owner)

        response = TestClient(server.api).get(
            f"{API}/pictures", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, response.text

    def test_a_token_for_another_library_is_refused(self, server, other_library):
        owner = _owner_client(server)
        token = _mint(owner)
        _restamp_tokens(server, other_library)

        response = TestClient(server.api).get(
            f"{API}/pictures", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
        assert "different library" in response.json()["detail"]

    def test_library_mismatch_is_refused_before_any_guest_vault_lookup(
        self, server, other_library, monkeypatch
    ):
        owner = _owner_client(server)
        token = _mint(owner, scope="READ")
        _restamp_tokens(server, other_library)
        with TestClient(server.api) as client:
            vault_reads = []
            real_read = server.vault.db.run_immediate_read_task

            # Record WHAT was read, not merely that something was. The vault
            # has other readers: the WorkPlanner thread probes it for pending
            # work every few seconds (QualityTask.count_missing_quality,
            # MissingLikenessFinder._likeness_state, and siblings), and those
            # probes have nothing to do with this request. A bare
            # "nothing was read" assertion therefore fails on any machine slow
            # enough for one to land inside the request - which is what CI is:
            # this call took 0.63 s there and collected 15 unrelated reads.
            #
            # The claim under test is the one in the name: the guest lookup
            # never ran. `_lookup_by_token` is the same sentinel
            # test_writer_waits_for_request_paused_in_guest_lookup keys on, and
            # that test waits for it, so a rename cannot quietly defang this.
            def observed_read(callback, *args, **kwargs):
                vault_reads.append(
                    (
                        getattr(callback, "__module__", ""),
                        getattr(callback, "__name__", repr(callback)),
                    )
                )
                return real_read(callback, *args, **kwargs)

            monkeypatch.setattr(
                server.vault.db, "run_immediate_read_task", observed_read
            )
            response = client.get(
                f"{API}/pictures",
                headers={"Authorization": f"Bearer {token}"},
                cookies={"guest_session": "plausible-cookie"},
            )
            assert response.status_code == 403
            # Every read the authentication path makes, not just the guest
            # lookup: naming one callable would still pass if the pin were
            # moved behind some *other* vault read in the same module. The
            # background probes stay excluded because they come from
            # ``pixlstash.tasks``, so this cannot go timing-dependent again.
            auth_reads = [read for read in vault_reads if read[0] == "pixlstash.auth"]
            assert auth_reads == [], (
                f"authentication read the vault before the pin refused: {vault_reads}"
            )

    def test_writer_waits_for_request_paused_in_guest_lookup(
        self, server, tmp_path, monkeypatch
    ):
        owner = _owner_client(server)
        token = _mint(owner, scope="READ")
        original = server.library_registry.active_library()
        target = server.library_registry.create(
            str(tmp_path / "guest-pause"), "Guest pause"
        )
        lookup_entered = threading.Event()
        release_lookup = threading.Event()
        request_done = threading.Event()
        switch_done = threading.Event()
        errors = []
        real_read = server.vault.db.run_immediate_read_task

        def paused_guest_read(callback, *args, **kwargs):
            if getattr(callback, "__name__", "") == "_lookup_by_token":
                lookup_entered.set()
                assert release_lookup.wait(timeout=10)
            return real_read(callback, *args, **kwargs)

        monkeypatch.setattr(
            server.vault.db, "run_immediate_read_task", paused_guest_read
        )

        def request():
            try:
                TestClient(server.api).get(
                    f"{API}/pictures",
                    headers={"Authorization": f"Bearer {token}"},
                    cookies={"guest_session": "plausible-cookie"},
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
        assert lookup_entered.wait(timeout=10)
        switch_thread.start()
        deadline = time.monotonic() + 5
        while server.library_coordinator.state is not SwitchState.SWITCHING:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not switch_done.is_set()
        assert server.vault.image_root == original.path

        release_lookup.set()
        request_thread.join(timeout=10)
        switch_thread.join(timeout=20)
        assert request_done.is_set() and switch_done.is_set()
        assert errors == []
        assert server.vault.image_root == target.path
        server.library_switch.switch_to(original.uuid)

    def test_a_token_with_no_stamp_at_all_is_refused(self, server):
        """Fails closed: an unstamped token is not treated as universal.

        The database makes an unstamped token impossible (the hub column is NOT
        NULL), so this exercises the gate's own defensive branch directly rather
        than through a row that cannot exist.
        """
        from types import SimpleNamespace

        from fastapi import HTTPException

        from pixlstash.authz.gate import AuthzGate

        request = SimpleNamespace(
            state=SimpleNamespace(matched_token=SimpleNamespace(library_uuid=None))
        )
        with pytest.raises(HTTPException) as excinfo:
            AuthzGate._enforce_library_pin(
                SimpleNamespace(_auth=server.auth),
                request,
                RoutePolicy(AccessPolicy.OWNER_ONLY),
            )
        assert excinfo.value.status_code == 403

    def test_the_owner_cookie_session_is_unaffected(self, server, other_library):
        """A session follows the active library, which is why switching exists."""
        owner = _owner_client(server)
        _mint(owner)
        _restamp_tokens(server, other_library)

        assert owner.get(f"{API}/pictures").status_code == 200

    def test_session_created_from_token_keeps_its_library_pin(self, server, tmp_path):
        """Exchanging a token for a cookie must not launder away its pin."""
        password_owner = _owner_client(server)
        token = _mint(password_owner)
        token_session = TestClient(server.api)
        response = token_session.post("/login", json={"token": token})
        assert response.status_code == 200, response.text

        original = server.library_registry.active_library()
        other = server.library_registry.create(str(tmp_path / "session-pin"), "Pin B")
        try:
            server.library_switch.switch_to(other.uuid)
            refused = token_session.get(f"{API}/pictures")
            assert refused.status_code == 403
            assert "different library" in refused.json()["detail"]
            # A password-derived browser session follows the same switch.
            assert password_owner.get(f"{API}/pictures").status_code == 200
        finally:
            server.library_switch.switch_to(original.uuid)
            server.library_registry.detach(other.id)


class TestLibraryIndependentRoutes:
    def test_auth_info_answers_even_for_a_non_active_library_token(
        self, server, other_library
    ):
        """Otherwise a refused token could not discover why it was refused."""
        owner = _owner_client(server)
        token = _mint(owner)
        _restamp_tokens(server, other_library)

        response = TestClient(server.api).get(
            f"{API}/users/me/auth", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, response.text

    def test_hub_only_auth_during_switch_never_enriches_from_guest_vault(
        self, server, monkeypatch
    ):
        owner = _owner_client(server)
        token = _mint(owner, scope="READ")
        vault_reads = []
        real_read = server.vault.db.run_immediate_read_task

        # Record WHAT was read, for the reason spelled out in
        # TestPinnedRoutes.test_guest_enrichment_never_runs_for_a_pinned_route:
        # the WorkPlanner thread probes the vault on its own schedule, so a
        # bare "nothing was read" assertion fails whenever one of those probes
        # lands inside the request.
        def observed_read(callback, *args, **kwargs):
            vault_reads.append(
                (
                    getattr(callback, "__module__", ""),
                    getattr(callback, "__name__", repr(callback)),
                )
            )
            return real_read(callback, *args, **kwargs)

        monkeypatch.setattr(server.vault.db, "run_immediate_read_task", observed_read)
        server.library_coordinator.state = SwitchState.SWITCHING
        try:
            response = TestClient(server.api).get(
                f"{API}/libraries",
                headers={"Authorization": f"Bearer {token}"},
                cookies={"guest_session": "plausible-cookie"},
            )
        finally:
            server.library_coordinator.state = SwitchState.READY

        assert response.status_code == 403
        # Enrichment lives in ``pixlstash.auth``; the background probes come
        # from ``pixlstash.tasks`` and are none of this test's business.
        auth_reads = [read for read in vault_reads if read[0] == "pixlstash.auth"]
        assert auth_reads == [], (
            f"authentication read the guest vault during a switch: {vault_reads}"
        )


class TestTheDeclarationContract:
    def test_pinned_is_the_default(self):
        """Safe by omission: a new route is pinned unless it opts out."""
        assert RoutePolicy(AccessPolicy.OWNER_ONLY).library_independent is False

    def test_the_independent_set_is_small_and_deliberate(self):
        """Every exemption is a decision, so the set is asserted, not counted.

        Growing this list is exactly the change that should require a reviewer
        to think, so a new entry fails here until it is added deliberately.
        """
        independent = {
            route
            for route, policy in ROUTE_POLICIES.items()
            if policy.library_independent
        }
        assert independent == {
            ("GET", "/api/v1/users/me/auth"),
            ("GET", "/api/v1/libraries"),
            ("POST", "/api/v1/libraries/active"),
        }

    def test_every_route_has_a_typed_generation_access_mode(self):
        assert ROUTE_POLICIES
        for route, policy in ROUTE_POLICIES.items():
            assert isinstance(policy.library_access, LibraryAccessMode), route

    def test_only_the_switch_endpoint_has_writer_admission(self):
        writers = {
            route
            for route, policy in ROUTE_POLICIES.items()
            if policy.library_access is LibraryAccessMode.SWITCH_WRITER
        }
        assert writers == {("POST", "/api/v1/libraries/active")}

    def test_token_management_is_never_library_independent(self):
        """The second clause of the rule, pinned down.

        A token stamped for library A that could mint while B is active would
        hand itself a B-stamped token, reopening the pivot the pin closes.
        """
        for route, policy in ROUTE_POLICIES.items():
            if "token" in route[1]:
                assert not policy.library_independent, route
