"""The library pin: a token authenticates only while its library is active.

A token belongs to exactly one library (multi-library plan §4) unless the owner
set it to cover them all (#1787, ``TestATokenThatCoversEveryLibrary``). Without the
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
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
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
        try:
            try:
                assert lookup_entered.wait(timeout=10)
                switch_thread.start()
                deadline = time.monotonic() + 5
                while server.library_coordinator.state is not SwitchState.SWITCHING:
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                assert not switch_done.is_set()
                assert server.vault.image_root == original.path
            finally:
                # Inside the try, so an assertion that fires still releases and
                # joins the paused handler instead of leaving it parked on
                # release_lookup.wait(10) holding the read task.
                release_lookup.set()
                request_thread.join(timeout=10)
                if switch_thread.ident is not None:  # unstarted if the pause failed
                    switch_thread.join(timeout=20)
            assert request_done.is_set() and switch_done.is_set()
            assert errors == []
            assert server.vault.image_root == target.path
        finally:
            # Likewise for the library itself: a failure here used to skip the
            # restore and leave the rest of the module on the wrong library.
            if server.library_registry.active_library().uuid != original.uuid:
                server.library_switch.switch_to(original.uuid)

    def test_a_token_with_no_stamp_at_all_is_refused(self, server):
        """Fails closed: an unstamped token is not treated as universal.

        The database makes an unstamped token impossible (the hub column is NOT
        NULL), so this exercises the gate's own defensive branch directly rather
        than through a row that cannot exist.
        """
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


class TestTheTokenList:
    def test_a_share_link_is_named_from_its_own_library_only(
        self, server, other_library
    ):
        """The list is read from the hub and the names from the library.

        Looking the name up in the hub, which has no sets, answered 500 for
        the whole list as soon as one share link existed. And a link for a
        library that is not open gets no name: its id is another set here.
        """
        owner = _owner_client(server)
        made = owner.post(f"{API}/picture_sets", json={"name": "Pinned set"})
        assert made.status_code == 200, made.text
        set_id = made.json()["picture_set"]["id"]
        try:
            minted = owner.post(
                "/users/me/token",
                json={
                    "description": "pin test",
                    "scope": "READ",
                    "resource_type": "picture_set",
                    "resource_id": set_id,
                },
            )
            assert minted.status_code == 200, minted.text

            listed = owner.get(f"{API}/users/me/token")
            assert listed.status_code == 200, listed.text
            assert [row["resource_name"] for row in listed.json()] == ["Pinned set"]

            _restamp_tokens(server, other_library)
            listed = owner.get(f"{API}/users/me/token")
            assert listed.status_code == 200, listed.text
            assert [row["resource_name"] for row in listed.json()] == [None]
        finally:
            owner.delete(f"{API}/picture_sets/{set_id}")

    def test_with_no_library_database_the_list_still_answers_without_names(
        self, server
    ):
        """The names are a courtesy; the list is not. A request that reached
        the handler with no library lease and no vault to read (nothing served
        does, but the service can be built that way) must not go looking for
        sets in the hub."""
        owner = _owner_client(server)
        minted = owner.post(
            "/users/me/token",
            json={
                "description": "pin test",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 1,
            },
        )
        assert minted.status_code == 200, minted.text
        request = SimpleNamespace(
            state=SimpleNamespace(auth_user_id=server.auth.get_user().id),
            cookies={},
        )
        vault_db = server.auth.vault_db
        server.auth.vault_db = None
        try:
            listed = server.auth.list_tokens(request)
        finally:
            server.auth.vault_db = vault_db
        assert [(row["resource_id"], row["resource_name"]) for row in listed] == [
            (1, None)
        ]


def _token_row(server, description="pin test") -> UserToken:
    """The one token a test minted, as the hub has it now."""
    return server.hub_engine.run_immediate_read_task(
        lambda session: session.exec(
            select(UserToken).where(UserToken.description == description)
        ).one()
    )


def _cover(client, token_id: int, all_libraries: bool, **kwargs):
    """Ask for a token to cover every library, or only the active one."""
    return client.put(
        f"{API}/users/me/token/{token_id}/libraries",
        json={"all_libraries": all_libraries},
        **kwargs,
    )


class TestATokenThatCoversEveryLibrary:
    """The owner's opt-out from the pin (#1787), and who may take it.

    Every refusal here sits beside the same request succeeding, so a negative
    cannot pass because the credential or the route was simply missing.
    """

    def test_it_works_in_a_library_it_was_not_minted_in(self, server, other_library):
        owner = _owner_client(server)
        token = _mint(owner)
        bearer = {"Authorization": f"Bearer {token}"}
        _restamp_tokens(server, other_library)
        # The control: pinned, it is refused, and told the way out.
        refused = TestClient(server.api).get(f"{API}/pictures", headers=bearer)
        assert refused.status_code == 403
        assert "cover every library" in refused.json()["detail"]

        _restamp_tokens(server, server.auth.active_library_uuid())
        widened = _cover(owner, _token_row(server).id, True)
        assert widened.status_code == 200, widened.text
        assert widened.json()["all_libraries"] is True
        _restamp_tokens(server, other_library)

        response = TestClient(server.api).get(f"{API}/pictures", headers=bearer)
        assert response.status_code == 200, response.text
        # Signing in with it works from here too, not only from its own library.
        signed_in = TestClient(server.api).post("/login", json={"token": token})
        assert signed_in.status_code == 200, signed_in.text
        listed = owner.get(f"{API}/users/me/token").json()
        assert [row["all_libraries"] for row in listed] == [True]

    def test_it_follows_a_real_switch_and_so_does_its_session(self, server, tmp_path):
        owner = _owner_client(server)
        token = _mint(owner)
        assert _cover(owner, _token_row(server).id, True).status_code == 200
        token_session = TestClient(server.api)
        assert token_session.post("/login", json={"token": token}).status_code == 200

        original = server.library_registry.active_library()
        other = server.library_registry.create(str(tmp_path / "covered"), "Covered")
        try:
            server.library_switch.switch_to(other.uuid)
            bearer = TestClient(server.api).get(
                f"{API}/pictures", headers={"Authorization": f"Bearer {token}"}
            )
            assert bearer.status_code == 200, bearer.text
            assert token_session.get(f"{API}/pictures").status_code == 200
        finally:
            server.library_switch.switch_to(original.uuid)
            server.library_registry.detach(other.id)

    def test_pinning_it_again_pins_it_to_the_active_library(
        self, server, other_library
    ):
        owner = _owner_client(server)
        token = _mint(owner)
        bearer = {"Authorization": f"Bearer {token}"}
        token_id = _token_row(server).id
        assert _cover(owner, token_id, True).status_code == 200
        token_session = TestClient(server.api)
        assert token_session.post("/login", json={"token": token}).status_code == 200
        assert token_session.get(f"{API}/pictures").status_code == 200
        _restamp_tokens(server, other_library)
        # Used once more while it covers everything, so the token cache holds
        # the row as it is now, going into the change.
        assert (
            TestClient(server.api).get(f"{API}/pictures", headers=bearer).status_code
            == 200
        )

        narrowed = _cover(owner, token_id, False)
        assert narrowed.status_code == 200, narrowed.text
        row = _token_row(server)
        assert row.all_libraries is False
        assert row.library_uuid == server.auth.active_library_uuid()
        # The session it made while it covered everything does not outlive that.
        assert token_session.get(f"{API}/pictures").status_code == 401
        assert (
            TestClient(server.api).get(f"{API}/pictures", headers=bearer).status_code
            == 200
        )
        # Nor does the verified copy in the token cache: with the registry
        # naming some other library, the pinned row is refused at the gate. A
        # cached row from before the change would still cover it. Nothing has
        # flushed the cache since the bearer request above but the change.
        provider = server.auth.library_uuid_provider
        server.auth.library_uuid_provider = lambda: "example-elsewhere"
        try:
            stale = TestClient(server.api).get(f"{API}/pictures", headers=bearer)
        finally:
            server.auth.library_uuid_provider = provider
        assert stale.status_code == 403, stale.text
        _restamp_tokens(server, other_library)
        assert (
            TestClient(server.api).get(f"{API}/pictures", headers=bearer).status_code
            == 403
        )

    def test_pinning_with_no_registry_keeps_the_library_it_was_stamped_with(
        self, server
    ):
        """With nothing to say which library is active there is none to pin to.

        That is an ``AuthService`` built without a registry; a served request
        always has an active library, because without one the admission
        middleware answers 503 before any handler runs. The token goes back to
        the library its stamp names, which the hub never lets be empty, so it
        is never left covering nothing.
        """
        owner = _owner_client(server)
        token = _mint(owner)
        before = _token_row(server)
        assert _cover(owner, before.id, True).status_code == 200

        provider = server.auth.library_uuid_provider
        server.auth.library_uuid_provider = lambda: None
        try:
            narrowed = _cover(owner, before.id, False)
        finally:
            server.auth.library_uuid_provider = provider
        assert narrowed.status_code == 200, narrowed.text

        after = _token_row(server)
        assert after.all_libraries is False
        assert after.library_uuid == before.library_uuid
        response = TestClient(server.api).get(
            f"{API}/pictures", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, response.text

    def test_a_read_only_token_can_cover_every_library_and_stays_read_only(
        self, server, other_library
    ):
        owner = _owner_client(server)
        token = _mint(owner, scope="READ")
        bearer = {"Authorization": f"Bearer {token}"}
        assert _cover(owner, _token_row(server).id, True).status_code == 200
        _restamp_tokens(server, other_library)

        client = TestClient(server.api)
        assert client.get(f"{API}/pictures", headers=bearer).status_code == 200
        write = client.post(
            f"{API}/users/me/token", json={"scope": "ALL"}, headers=bearer
        )
        assert write.status_code == 403

    def test_a_share_link_cannot_be_widened(self, server, other_library):
        owner = _owner_client(server)
        minted = owner.post(
            "/users/me/token",
            json={
                "description": "pin test",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 1,
            },
        )
        assert minted.status_code == 200, minted.text
        bearer = {"Authorization": f"Bearer {minted.json()['token']}"}
        token_id = _token_row(server).id

        refused = _cover(owner, token_id, True)
        assert refused.status_code == 400, refused.text
        assert _token_row(server).all_libraries is False

        # And a row that carries the flag anyway (a hand-edited hub) stays
        # pinned: the flag is only ever read on a token naming no resource.
        def _forge(session: Session):
            token = session.get(UserToken, token_id)
            token.all_libraries = True
            session.add(token)
            session.commit()

        server.hub_engine.run_task(_forge)
        server.auth._flush_token_cache()
        client = TestClient(server.api)
        assert client.get(f"{API}/picture_sets", headers=bearer).status_code == 200
        _restamp_tokens(server, other_library)
        assert _token_row(server).all_libraries is True
        refused = client.get(f"{API}/picture_sets", headers=bearer)
        assert refused.status_code == 403
        # And is told nothing about how the owner could change that.
        assert refused.json()["detail"] == "Token belongs to a different library"

    def test_a_token_bound_to_one_address_cannot_be_widened(
        self, server, other_library
    ):
        """The ComfyUI link key names no resource, but it is not the owner's
        to spread: it was handed to one integration for one library."""
        owner = _owner_client(server)
        token = _mint(owner)
        bearer = {"Authorization": f"Bearer {token}"}
        token_id = _token_row(server).id

        def _write(**values):
            def _apply(session: Session):
                row = session.get(UserToken, token_id)
                for name, value in values.items():
                    setattr(row, name, value)
                session.add(row)
                session.commit()

            server.hub_engine.run_task(_apply)
            server.auth._flush_token_cache()

        # Bound to the address these requests come from, so it still works.
        _write(bound_address="testclient")
        client = TestClient(server.api)
        assert client.get(f"{API}/pictures", headers=bearer).status_code == 200

        refused = _cover(owner, token_id, True)
        assert refused.status_code == 400, refused.text
        assert "ComfyUI link key" in refused.json()["detail"]
        assert _token_row(server).all_libraries is False
        listed = owner.get(f"{API}/users/me/token").json()
        assert [row["source_bound"] for row in listed] == [True]

        # A row carrying the flag anyway stays pinned, and is not told how the
        # owner could change that.
        _write(all_libraries=True)
        assert client.get(f"{API}/pictures", headers=bearer).status_code == 200
        _restamp_tokens(server, other_library)
        elsewhere = client.get(f"{API}/pictures", headers=bearer)
        assert elsewhere.status_code == 403
        assert elsewhere.json()["detail"] == "Token belongs to a different library"

        # The same row with no address is an ordinary token: the flag counts.
        _write(bound_address=None)
        assert client.get(f"{API}/pictures", headers=bearer).status_code == 200

    def test_only_the_owner_on_the_local_network_may_set_it(self, server):
        owner = _owner_client(server)
        token_id = owner.post(
            "/users/me/token", json={"description": "pin test", "scope": "ALL"}
        ).json()["token_id"]
        read_token = owner.post(
            "/users/me/token", json={"description": "reader", "scope": "READ"}
        ).json()["token"]
        route = ("PUT", f"{API}/users/me/token/{{token_id}}/libraries")
        assert ROUTE_POLICIES[route].policy is AccessPolicy.LOCAL_OWNER_ONLY

        # A read-only token is not the owner.
        by_reader = _cover(
            TestClient(server.api),
            token_id,
            True,
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert by_reader.status_code == 403
        # Nor is nobody at all.
        assert _cover(TestClient(server.api), token_id, True).status_code == 401

        # The owner's own session, from somewhere else, is refused too.
        config = server.auth._server_config
        previous = config.get("trusted_proxies")
        config["trusted_proxies"] = ["testclient"]
        try:
            remote = _cover(
                owner, token_id, True, headers={"X-Forwarded-For": "8.8.8.8"}
            )
            assert remote.status_code == 403
            assert "allow_remote_host_ops" in remote.json()["detail"]
            assert _token_row(server).all_libraries is False
            # The same session, the same proxy, from a local address: allowed.
            local = _cover(
                owner, token_id, True, headers={"X-Forwarded-For": "127.0.0.1"}
            )
            assert local.status_code == 200, local.text
        finally:
            if previous is None:
                config.pop("trusted_proxies", None)
            else:
                config["trusted_proxies"] = previous
        assert _token_row(server).all_libraries is True

    def test_a_token_refused_in_this_library_cannot_lift_its_own_pin(
        self, server, other_library
    ):
        owner = _owner_client(server)
        token = _mint(owner)
        bearer = {"Authorization": f"Bearer {token}"}
        token_id = _token_row(server).id
        _restamp_tokens(server, other_library)

        refused = _cover(TestClient(server.api), token_id, True, headers=bearer)
        assert refused.status_code == 403
        assert _token_row(server).all_libraries is False

    def test_no_token_may_set_it_not_even_a_full_access_one_on_itself(self, server):
        """An agent told "this library only" must not be able to lift that.

        Its token is an owner to every other route, and the next thing it could
        do is switch library. The token is in its own library here and reaches
        an owner route, so the refusals are about what it is, not where.
        """
        owner = _owner_client(server)
        token = _mint(owner)
        bearer = {"Authorization": f"Bearer {token}"}
        token_id = _token_row(server).id
        client = TestClient(server.api)
        assert client.get(f"{API}/users/me/token", headers=bearer).status_code == 200

        refused = _cover(client, token_id, True, headers=bearer)
        assert refused.status_code == 403
        assert "a token cannot" in refused.json()["detail"]

        # Nor by signing in with it first and asking as a session.
        token_session = TestClient(server.api)
        assert token_session.post("/login", json={"token": token}).status_code == 200
        assert token_session.get(f"{API}/users/me/token").status_code == 200
        laundered = _cover(token_session, token_id, True)
        assert laundered.status_code == 403
        assert "a token cannot" in laundered.json()["detail"]
        assert _token_row(server).all_libraries is False

        # The owner's own session may.
        assert _cover(owner, token_id, True).status_code == 200

    def test_a_login_that_raced_a_pinning_does_not_keep_the_wider_session(self, server):
        """The session a token login registers carries the reach the token had
        when it was read. If the owner pinned the token in between, the
        re-check after registering ends that session; a token that still covers
        every library, and a session that is pinned itself, are left alone."""
        owner = _owner_client(server)
        _mint(owner)
        row = _token_row(server)
        user_id = server.auth.get_user().id

        server.auth._register_session("example-raced", user_id, row.public_id)
        with pytest.raises(HTTPException) as excinfo:
            server.auth._confirm_session_token_still_exists(
                "example-raced", row.public_id
            )
        assert excinfo.value.status_code == 401
        assert "example-raced" not in server.auth.active_session_ids

        server.auth._register_session(
            "example-pinned", user_id, row.public_id, row.library_uuid
        )
        server.auth._confirm_session_token_still_exists("example-pinned", row.public_id)
        assert _cover(owner, row.id, True).status_code == 200
        server.auth._register_session("example-follows", user_id, row.public_id)
        server.auth._confirm_session_token_still_exists(
            "example-follows", row.public_id
        )
        assert "example-follows" in server.auth.active_session_ids

    def test_an_unknown_token_is_not_found(self, server):
        owner = _owner_client(server)
        assert _cover(owner, 987654321, True).status_code == 404


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
        # discard closes and reopens the active vault, as the switch does, so
        # it cannot wait behind a read lease either.
        assert writers == {
            ("POST", "/api/v1/libraries/active"),
            ("POST", "/api/v1/libraries/{library_uuid}/discard"),
        }

    def test_token_management_is_never_library_independent(self):
        """The second clause of the rule, pinned down.

        A token stamped for library A that could mint while B is active would
        hand itself a B-stamped token, reopening the pivot the pin closes.
        """
        for route, policy in ROUTE_POLICIES.items():
            if "token" in route[1]:
                assert not policy.library_independent, route
