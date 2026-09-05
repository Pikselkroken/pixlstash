import logging
import tempfile
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from pixlstash import auth as auth_module
from pixlstash.db_models import User, UserToken
from pixlstash.server import Server
from tests.network_vectors import LAN_IPV4, PRIVATE_172_IPV4

API_PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def server():
    """Shared Server instance for all auth tests in this module.

    Building a Server (DB migrations, vault start-up, FastAPI route
    registration, etc.) takes a couple of seconds, so we pay that cost once
    per module and reset auth state between tests with the ``reset_auth``
    fixture instead of re-instantiating the Server.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"
        with Server(server_config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def reset_auth(server):
    """Clear stored user credentials and tokens before each test.

    Each test in this module starts from a clean auth state: no user row,
    no tokens and no cached/active sessions. This mirrors the behaviour of
    the original per-test ``with Server(...)`` blocks where every test got
    a fresh database.
    """

    def _wipe(session: Session):
        session.exec(delete(UserToken))
        session.exec(delete(User))
        session.commit()

    server.hub_engine.run_task(_wipe)

    # Reset in-memory auth caches that mirror the on-disk state.
    server.auth.password_hash = None
    server.auth.username = None
    server.auth.user = None
    # _clear_all_sessions also drops the session→token maps, which a bare
    # active_session_ids = {} would leave behind.
    server.auth._clear_all_sessions()
    # Go through the flush helper so the revocation epoch is bumped too - a
    # bare _token_cache.clear() skips it (see AuthService._flush_token_cache).
    server.auth._flush_token_cache()
    # The login lockout counter is process-wide, so a test that exercises
    # rejected logins would otherwise lock out the tests that follow it.
    server.auth._failed_login_attempts = 0
    server.auth._login_lockout_until = 0.0

    # Re-create the User row so the rest of the server behaves as on first
    # startup (no password set yet).
    server.auth.ensure_user()

    yield


def test_authentication_without_login(server):
    """Test accessing a protected endpoint without logging in."""
    client = TestClient(server.api)

    # Access without a session cookie
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_authentication_with_password_setup(server):
    """Test setting up the password on first login."""
    client = TestClient(server.api)

    # First login to set the password
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "testuser", "password": "testpassword"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Username and password set successfully."


def test_authentication_with_valid_password(server):
    """Test logging in with the correct password after setup."""
    with TestClient(server.api) as client1:
        # First login to set the password
        response = client1.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Username and password set successfully."

    with TestClient(server.api) as client2:
        # Login with the correct password
        response = client2.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Login successful."

        # Access a protected endpoint
        response = client2.get(f"{API_PREFIX}/protected")
        assert response.status_code == 200
        assert response.json()["message"] == "You are authenticated!"


def test_authentication_with_invalid_password(server):
    """Test logging in with an incorrect password."""
    with TestClient(server.api) as client1:
        # First login to set the password
        response = client1.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Username and password set successfully."

    with TestClient(server.api) as client2:
        # Attempt login with an incorrect password
        response = client2.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "wrongpassword"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid password"

        # Access a protected endpoint
        response = client2.get(f"{API_PREFIX}/protected")
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"


def test_authentication_with_token_login(server):
    """Test creating a token and logging in with it."""
    with TestClient(server.api) as client1:
        # First login to set the password
        response = client1.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Username and password set successfully."

        # Create a token
        response = client1.post(
            f"{API_PREFIX}/users/me/token", json={"description": "Test token"}
        )
        assert response.status_code == 200
        token = response.json().get("token")
        assert token

    with TestClient(server.api) as client2:
        # Login with token
        response = client2.post(f"{API_PREFIX}/login", json={"token": token})
        assert response.status_code == 200
        assert response.json()["message"] == "Login successful."

        # Access a protected endpoint
        response = client2.get(f"{API_PREFIX}/protected")
        assert response.status_code == 200
        assert response.json()["message"] == "You are authenticated!"

    with TestClient(server.api) as client3:
        # Login with wrong token
        response = client3.post(f"{API_PREFIX}/login", json={"token": "bad-token"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"


# --- Which credentials may be exchanged for a session -----------------------
#
# Signing in requires a credential with full, unscoped owner authority: an
# unexpired ALL-scope token with no resource restriction. These tests pin both
# directions: narrower and expired credentials are refused, and a genuine owner
# token still works.


def _claim_owner(client) -> None:
    """Claim the empty owner account so token endpoints become reachable."""
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "testuser", "password": "testpassword"},
    )
    assert response.status_code == 200


def _create_token(client, **payload) -> dict:
    """Create a token via the owner API and return the response body."""
    response = client.post(f"{API_PREFIX}/users/me/token", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_login_refused(server, token: str) -> None:
    """A /login with *token* is refused, and hands out no working session."""
    with TestClient(server.api) as client:
        response = client.post(f"{API_PREFIX}/login", json={"token": token})
        assert response.status_code == 401, response.text
        # Same body as an unrecognised token, so the response does not
        # distinguish "unknown token" from "not an owner token".
        assert response.json()["detail"] == "Invalid token"
        assert "session_id" not in response.cookies
        assert client.get(f"{API_PREFIX}/protected").status_code == 401


def test_a_token_is_expired_at_exactly_its_expiry_moment():
    """The expiry comparison is inclusive, so the boundary itself is expired.

    The two checks this rule replaced disagreed here, one accepting and one
    rejecting an ``expires_at`` equal to the moment of the check. Pin the
    stricter reading so the boundary cannot drift back.
    """
    moment = datetime(2026, 1, 1, 12, 0, 0)
    at_expiry = SimpleNamespace(expires_at=moment)
    assert auth_module.is_token_expired(at_expiry, moment) is True
    assert (
        auth_module.is_token_expired(at_expiry, moment - timedelta(microseconds=1))
        is False
    )
    assert (
        auth_module.is_token_expired(SimpleNamespace(expires_at=None), moment) is False
    )


def test_login_refuses_a_read_scoped_token(server):
    """A READ token is narrower than a session and cannot be exchanged for one."""
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        created = _create_token(owner, description="read", scope="READ")
    _assert_login_refused(server, created["token"])


def test_login_refuses_a_resource_scoped_token(server):
    """A token restricted to one resource cannot be exchanged for a session."""
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        created = _create_token(
            owner,
            description="one set",
            scope="READ",
            resource_type="picture_set",
            resource_id=1,
        )
    assert created["resource_type"] == "picture_set"
    _assert_login_refused(server, created["token"])


def test_login_refuses_an_expired_token(server):
    """A token past its expiry cannot be exchanged for a session."""
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        created = _create_token(
            owner,
            description="expired",
            scope="ALL",
            expires_at="2020-01-01T12:00:00",
        )
    _assert_login_refused(server, created["token"])


def test_login_accepts_an_unscoped_owner_token(server):
    """An unexpired, unrestricted owner token still logs in (no over-blocking)."""
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        created = _create_token(owner, description="owner", scope="ALL")

    with TestClient(server.api) as client:
        response = client.post(f"{API_PREFIX}/login", json={"token": created["token"]})
        assert response.status_code == 200
        assert response.json()["message"] == "Login successful."
        assert client.get(f"{API_PREFIX}/protected").status_code == 200
        # The session really is owner-level: an owner-only endpoint answers.
        assert client.get(f"{API_PREFIX}/users/me/token").status_code == 200


# --- A session does not outlive the token that created it -------------------


def test_deleting_a_token_ends_the_session_it_created(server):
    """Removing a token also ends any session that was created from it."""
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        created = _create_token(owner, description="owner", scope="ALL")

        with TestClient(server.api) as client:
            response = client.post(
                f"{API_PREFIX}/login", json={"token": created["token"]}
            )
            assert response.status_code == 200
            assert client.get(f"{API_PREFIX}/protected").status_code == 200

            # Remove the token from a separate, password-authenticated session.
            deleted = owner.delete(f"{API_PREFIX}/users/me/token/{created['token_id']}")
            assert deleted.status_code == 200

            # The next request on the already-issued session is refused.
            assert client.get(f"{API_PREFIX}/protected").status_code == 401


def test_deleting_a_token_leaves_unrelated_sessions_alone(server):
    """Only the removed token's own sessions end; other sessions keep working."""
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        doomed = _create_token(owner, description="doomed", scope="ALL")
        kept = _create_token(owner, description="kept", scope="ALL")

        with (
            TestClient(server.api) as doomed_client,
            TestClient(server.api) as kept_client,
        ):
            assert (
                doomed_client.post(
                    f"{API_PREFIX}/login", json={"token": doomed["token"]}
                ).status_code
                == 200
            )
            assert (
                kept_client.post(
                    f"{API_PREFIX}/login", json={"token": kept["token"]}
                ).status_code
                == 200
            )

            deleted = owner.delete(f"{API_PREFIX}/users/me/token/{doomed['token_id']}")
            assert deleted.status_code == 200

            assert doomed_client.get(f"{API_PREFIX}/protected").status_code == 401
            # The other token's session and the password session are untouched.
            assert kept_client.get(f"{API_PREFIX}/protected").status_code == 200
            assert owner.get(f"{API_PREFIX}/protected").status_code == 200


def test_revoking_resource_tokens_leaves_unrelated_sessions_alone(server):
    """Revoking a resource's share tokens does not end other sessions."""
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        _create_token(
            owner,
            description="shared set",
            scope="READ",
            resource_type="picture_set",
            resource_id=7,
        )
        kept = _create_token(owner, description="kept", scope="ALL")

        with TestClient(server.api) as kept_client:
            assert (
                kept_client.post(
                    f"{API_PREFIX}/login", json={"token": kept["token"]}
                ).status_code
                == 200
            )

            revoked = owner.delete(
                f"{API_PREFIX}/users/me/tokens/by-resource",
                params={"resource_type": "picture_set", "resource_id": 7},
            )
            assert revoked.status_code == 200
            assert revoked.json()["deleted_count"] == 1

            assert kept_client.get(f"{API_PREFIX}/protected").status_code == 200
            assert owner.get(f"{API_PREFIX}/protected").status_code == 200


def test_a_token_removed_mid_login_leaves_no_usable_session(server, monkeypatch):
    """A removal that lands inside the sign-in window still ends the session.

    Matching a token costs a bcrypt call per candidate row plus a database
    round trip, so a removal can land between the read that matched the token
    and the moment the session is registered, which is before the removal's
    sweep has a session to find. This test pins that interleaving exactly: the
    removal is fired from inside the verification step, so it completes before
    the login registers anything.
    """
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        created = _create_token(owner, description="removed mid-login", scope="ALL")
        token_id = created["token_id"]

        real_verify = auth_module.bcrypt.verify
        revoked_during_login = []

        def verify_then_remove(secret, hashed):
            matched = real_verify(secret, hashed)
            # Fire once, on the match, so the removal lands after the token
            # rows have been read and before the session is registered. The
            # owner client authenticates with a password session cookie, so
            # this call does not re-enter bcrypt verification.
            if matched and not revoked_during_login:
                revoked_during_login.append(token_id)
                deleted = owner.delete(f"{API_PREFIX}/users/me/token/{token_id}")
                assert deleted.status_code == 200
            return matched

        monkeypatch.setattr(auth_module.bcrypt, "verify", verify_then_remove)

        with TestClient(server.api) as racer:
            response = racer.post(
                f"{API_PREFIX}/login", json={"token": created["token"]}
            )
            assert revoked_during_login, (
                "the removal did not land inside the sign-in window, so this "
                "test is not exercising what it claims to"
            )
            assert response.status_code == 401, response.text
            assert response.json()["detail"] == "Invalid token"
            assert "session_id" not in response.cookies
            assert racer.get(f"{API_PREFIX}/protected").status_code == 401

    # No session is left pointing at a token that no removal path could reach
    # again. (The owner's own password session is still live and is correctly
    # not linked to any token.)
    assert server.auth._sessions_by_token_public_id == {}
    assert server.auth._token_public_id_by_session == {}


# --- A removed token stops authenticating straight away ---------------------
#
# Verified tokens are cached for five minutes so bcrypt does not run on every
# request. Removal empties that cache, but a lookup already in flight has
# already read the row and is about to write it back. These tests pin that a
# removal which lands mid-lookup still takes effect on the next request.


def _remove_token_during_verification(monkeypatch, owner, token_id):
    """Make the next successful bcrypt match fire a removal of *token_id*.

    Places the removal inside the lookup window: after the row has been read
    and matched, before the result is cached.
    """
    real_verify = auth_module.bcrypt.verify
    removed = []

    def verify_then_remove(secret, hashed):
        matched = real_verify(secret, hashed)
        # The owner client authenticates with a password session cookie, so
        # this call does not re-enter bcrypt verification.
        if matched and not removed:
            removed.append(token_id)
            deleted = owner.delete(f"{API_PREFIX}/users/me/token/{token_id}")
            assert deleted.status_code == 200
        return matched

    monkeypatch.setattr(auth_module.bcrypt, "verify", verify_then_remove)
    return removed


def test_a_token_removed_mid_lookup_stops_working_on_the_next_request(
    server, monkeypatch
):
    """A removal landing inside a lookup is not undone by that lookup's caching."""
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        created = _create_token(owner, description="removed mid-lookup", scope="ALL")
        headers = {"Authorization": f"Bearer {created['token']}"}

        removed = _remove_token_during_verification(
            monkeypatch, owner, created["token_id"]
        )

        with TestClient(server.api) as client:
            # The request that races the removal may still be served; it had
            # already matched the token before the removal committed.
            client.get(f"{API_PREFIX}/protected", headers=headers)
            assert removed, (
                "the removal did not land inside the lookup window, so this "
                "test is not exercising what it claims to"
            )

            # The next request must not be served from a cache entry written
            # after the removal.
            assert (
                client.get(f"{API_PREFIX}/protected", headers=headers).status_code
                == 401
            )


def test_a_token_removed_mid_lookup_cannot_mint_a_replacement(server, monkeypatch):
    """A removed token cannot be used to create another one."""
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        created = _create_token(owner, description="removed mid-lookup", scope="ALL")
        headers = {"Authorization": f"Bearer {created['token']}"}

        removed = _remove_token_during_verification(
            monkeypatch, owner, created["token_id"]
        )

        with TestClient(server.api) as client:
            client.get(f"{API_PREFIX}/protected", headers=headers)
            assert removed, "the removal did not land inside the lookup window"

            minted = client.post(
                f"{API_PREFIX}/users/me/token",
                json={"description": "replacement"},
                headers=headers,
            )
            assert minted.status_code == 401, minted.text
            # And nothing was created.
            assert len(owner.get(f"{API_PREFIX}/users/me/token").json()) == 0


# --- Seamless desktop (Electron) loopback session ---------------------------

DESKTOP_TOKEN = "desktop-session-token-0123456789abcdef"


def test_seed_desktop_session_authenticates_loopback_owner(server, monkeypatch):
    """A seeded desktop token logs the local window straight in - no /login."""
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)

    seeded = server.auth.seed_desktop_session()
    assert seeded == DESKTOP_TOKEN
    # The token maps to the owner user with no password/registration step.
    assert server.auth.active_session_ids.get(DESKTOP_TOKEN) is not None

    client = TestClient(server.api)
    # Presenting the token as the session cookie grants owner access, exactly
    # as the Electron shell does by injecting it into its BrowserWindow.
    client.cookies.set("session_id", DESKTOP_TOKEN)
    response = client.get(f"{API_PREFIX}/protected")
    assert response.status_code == 200
    assert response.json()["message"] == "You are authenticated!"


def test_seed_desktop_session_noop_without_env(server, monkeypatch):
    """With no env var set (every non-desktop install), nothing is seeded."""
    monkeypatch.delenv(server.auth.DESKTOP_SESSION_ENV, raising=False)
    assert server.auth.seed_desktop_session() is None
    assert server.auth.active_session_ids == {}


def test_seed_desktop_session_rejects_short_token(server, monkeypatch):
    """A weak (<32 char) token is refused so it can't grant owner access."""
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, "short")
    assert server.auth.seed_desktop_session() is None
    assert "short" not in server.auth.active_session_ids


def test_seed_desktop_session_rejects_token_below_contract_floor(server, monkeypatch):
    """A 16-31 char token is now rejected: the floor matches the 32+ char contract.

    The shell ships a 32-byte token rendered as 64 hex chars, so the documented
    contract is 32+ chars. A token between the old floor (16) and the contract
    (32) must be refused so a regression in the shell's generator can't hand out
    a weaker owner credential.
    """
    below_floor = "a" * (server.auth.DESKTOP_SESSION_MIN_LEN - 1)
    assert len(below_floor) >= 16  # would have passed the old <16 floor
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, below_floor)
    assert server.auth.seed_desktop_session() is None
    assert below_floor not in server.auth.active_session_ids


def test_seed_desktop_session_accepts_token_at_contract_floor(server, monkeypatch):
    """A token exactly at the 32-char contract floor is accepted (no over-block)."""
    at_floor = "b" * server.auth.DESKTOP_SESSION_MIN_LEN
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, at_floor)
    assert server.auth.seed_desktop_session() == at_floor
    assert server.auth.active_session_ids.get(at_floor) is not None


def test_desktop_session_does_not_authenticate_other_clients(server, monkeypatch):
    """Remote clients without the token still hit the normal auth wall."""
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()

    client = TestClient(server.api)
    # No cookie / a different cookie value must not be authenticated.
    assert client.get(f"{API_PREFIX}/protected").status_code == 401
    client.cookies.set("session_id", "some-other-unseeded-value")
    assert client.get(f"{API_PREFIX}/protected").status_code == 401


def test_desktop_session_rejected_from_non_local_ip(server, monkeypatch):
    """The loopback owner session must not grant access on the external listener.

    The desktop app can expose an optional external listener; the seeded owner
    session is high-privilege and pinned to local connections, so a request
    presenting it from a non-local IP is fail-closed (falls through to the normal
    auth wall) even though the cookie is normally scoped to the loopback origin.
    """
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()

    # Simulate the request arriving from a public (non-local) client IP.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")

    client = TestClient(server.api)
    client.cookies.set("session_id", DESKTOP_TOKEN)
    assert client.get(f"{API_PREFIX}/protected").status_code == 401

    # A private RFC 1918 LAN IP must ALSO be rejected: the external listener is
    # reached over the LAN, so the backstop is pinned to loopback, not is_local_ip.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: LAN_IPV4)
    assert client.get(f"{API_PREFIX}/protected").status_code == 401

    # Sanity: a loopback client with the same cookie is still authenticated.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    assert client.get(f"{API_PREFIX}/protected").status_code == 200


# --- First-owner registration must be loopback-only -------------------------


def test_registration_blocked_from_lan_ip(server, monkeypatch):
    """A LAN/non-loopback client must NOT be able to claim the empty owner account.

    The desktop owner is auto-logged-in and never sets a password; if the
    external listener is exposed, a co-network device could POST /login to set
    the owner credentials and take over the library (BLOCKER 1). Registration is
    pinned to loopback.
    """
    # Public IP - registration must be refused.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")
    client = TestClient(server.api)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "attacker", "password": "attackerpass"},
    )
    assert response.status_code == 403
    # The account must remain unclaimed.
    assert server.auth.get_user().password_hash is None

    # Private RFC 1918 LAN IP - also refused.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: LAN_IPV4)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "attacker", "password": "attackerpass"},
    )
    assert response.status_code == 403
    assert server.auth.get_user().password_hash is None


def test_registration_allowed_from_loopback(server, monkeypatch):
    """The legitimate owner can still claim the account from loopback (no over-block)."""
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    client = TestClient(server.api)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "owner", "password": "ownerpassword"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Username and password set successfully."
    assert server.auth.get_user().password_hash is not None


def test_login_with_existing_credentials_allowed_from_lan(server, monkeypatch):
    """Once credentials exist, a normal password login from the LAN is NOT blocked here.

    The loopback gate only protects first-owner *registration* (claiming the empty
    account). After credentials are set, remote-access policy is governed by the
    separate require_local_for_write check, not the registration gate - so a valid
    password login over the LAN must still authenticate (it would otherwise be an
    over-block regression).
    """
    # Claim the account from loopback first.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    with TestClient(server.api) as setup_client:
        assert (
            setup_client.post(
                f"{API_PREFIX}/login",
                json={"username": "owner", "password": "ownerpassword"},
            ).status_code
            == 200
        )

    # Remote write protection off so the registration gate is the only thing
    # under test here.
    monkeypatch.setitem(server.auth._server_config, "require_local_for_write", False)
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: LAN_IPV4)
    with TestClient(server.api) as client:
        response = client.post(
            f"{API_PREFIX}/login",
            json={"username": "owner", "password": "ownerpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Login successful."


# --- change_password on an UNCLAIMED account must be loopback-only -----------


def _fake_request(client_host):
    """Minimal Starlette-Request stand-in for the IP-dependent auth guards."""
    return SimpleNamespace(
        client=SimpleNamespace(host=client_host),
        headers={},
        url=SimpleNamespace(scheme="http"),
    )


def test_change_password_on_unclaimed_account_blocked_from_lan(server, monkeypatch):
    """Setting the first password on the empty owner account must require loopback.

    The desktop owner is auto-logged-in with no password (``password_hash``
    None). ``change_password`` skips the current-password check for such an
    account, so without an explicit guard anyone holding a session for it could
    set its password. Claiming it that way must be pinned to loopback exactly
    like first-owner registration.
    """
    unclaimed = server.auth.get_user()
    assert unclaimed.password_hash is None
    monkeypatch.setattr(server.auth, "get_user_for_request", lambda request: unclaimed)

    payload = SimpleNamespace(current_password=None, new_password="newownerpass")

    # Public IP - refused.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")
    with pytest.raises(Exception) as public_exc:
        server.auth.change_password(_fake_request("8.8.8.8"), payload)
    assert getattr(public_exc.value, "status_code", None) == 403

    # Private RFC 1918 LAN IP - also refused.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: LAN_IPV4)
    with pytest.raises(Exception) as lan_exc:
        server.auth.change_password(_fake_request(LAN_IPV4), payload)
    assert getattr(lan_exc.value, "status_code", None) == 403

    # The account must remain unclaimed.
    assert server.auth.get_user().password_hash is None


def test_change_password_on_unclaimed_account_allowed_from_loopback(
    server, monkeypatch
):
    """The local desktop window (loopback) can still set the first password."""
    unclaimed = server.auth.get_user()
    assert unclaimed.password_hash is None
    monkeypatch.setattr(server.auth, "get_user_for_request", lambda request: unclaimed)
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")

    payload = SimpleNamespace(current_password=None, new_password="newownerpass")
    result = server.auth.change_password(_fake_request("127.0.0.1"), payload)
    assert result["status"] == "success"
    assert server.auth.get_user().password_hash is not None


def test_change_password_on_claimed_account_allowed_from_lan(server, monkeypatch):
    """With a password already set, a valid current-password change is NOT loopback-gated.

    The loopback gate only protects the *claim* (first password). Once claimed,
    a normal authenticated password change from the LAN must still work (the
    current-password check governs it), or it would be an over-block regression.
    """
    # Claim from loopback first.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    claimed = server.auth.get_user()
    monkeypatch.setattr(server.auth, "get_user_for_request", lambda request: claimed)
    server.auth.change_password(
        _fake_request("127.0.0.1"),
        SimpleNamespace(current_password=None, new_password="firstpass"),
    )
    claimed = server.auth.get_user()
    assert claimed.password_hash is not None
    monkeypatch.setattr(server.auth, "get_user_for_request", lambda request: claimed)

    # Now change it again from a LAN IP with the correct current password.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: LAN_IPV4)
    result = server.auth.change_password(
        _fake_request(LAN_IPV4),
        SimpleNamespace(current_password="firstpass", new_password="secondpass"),
    )
    assert result["status"] == "success"


# --- WebSocket desktop-session backstop (must match the HTTP path) -----------


def _fake_websocket(session_id, client_host):
    """Build a minimal stand-in for a Starlette WebSocket handshake."""
    return SimpleNamespace(
        cookies={"session_id": session_id} if session_id else {},
        headers={},
        query_params={},
        client=SimpleNamespace(host=client_host),
    )


def test_ws_desktop_session_rejected_from_non_loopback(server, monkeypatch):
    """A non-loopback WS handshake presenting the desktop session must NOT authenticate.

    The HTTP path drops the seeded desktop session for non-loopback clients; the
    WS path must agree (BLOCKER 2), or a LAN client could ride the desktop token
    to full owner access on /ws/updates.
    """
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()

    # Public IP - rejected.
    public_ws = _fake_websocket(DESKTOP_TOKEN, "8.8.8.8")
    assert server.auth.authenticate_websocket(public_ws) is None

    # Private RFC 1918 LAN IP - also rejected.
    lan_ws = _fake_websocket(DESKTOP_TOKEN, LAN_IPV4)
    assert server.auth.authenticate_websocket(lan_ws) is None


def test_ws_desktop_session_allowed_from_loopback(server, monkeypatch):
    """The local desktop window (loopback) still authenticates over WS (no over-block)."""
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()

    loopback_ws = _fake_websocket(DESKTOP_TOKEN, "127.0.0.1")
    auth = server.auth.authenticate_websocket(loopback_ws)
    assert auth is not None
    assert auth.is_owner is True


# --- Env-provisioned initial owner credentials (Docker first-run) ------------
#
# Inside a container the host's traffic arrives as the bridge-gateway IP, never
# loopback, so the loopback-only registration gate makes first-run setup
# impossible. The fix is NOT to relax the IP guard (under Docker's userland
# proxy an attacker and the operator are indistinguishable by IP) but to claim
# the account from PIXLSTASH_INITIAL_USERNAME/PIXLSTASH_INITIAL_PASSWORD at
# startup, before any client can race for it.

DOCKER_403_MARKER = "PIXLSTASH_INITIAL_USERNAME"
NON_DOCKER_403_DETAIL = (
    "Initial setup must be completed from the device running PixlStash."
)


def _set_initial_creds(monkeypatch, server, username, password):
    monkeypatch.setenv(server.auth.INITIAL_OWNER_LOGIN_ENV, username)
    monkeypatch.setenv(server.auth.INITIAL_OWNER_AUTH_ENV, password)


def test_env_claim_provisions_unclaimed_account(server, monkeypatch):
    """Unclaimed account + both env vars → claimed at startup, and the creds
    work for a normal login from a NON-loopback client (the whole point:
    Docker operators never reach the server over loopback)."""
    _set_initial_creds(monkeypatch, server, "dockerowner", "s3cretpass")

    assert server.auth.claim_owner_from_env() is True
    user = server.auth.get_user()
    assert user.username == "dockerowner"
    assert user.password_hash is not None

    # Registration-gate policy no longer applies (the account is claimed);
    # disable the separate remote-write gate so the login itself is under test.
    monkeypatch.setitem(server.auth._server_config, "require_local_for_write", False)
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: LAN_IPV4)
    with TestClient(server.api) as client:
        response = client.post(
            f"{API_PREFIX}/login",
            json={"username": "dockerowner", "password": "s3cretpass"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Login successful."


def test_env_claim_never_touches_claimed_account(server, monkeypatch, caplog):
    """Claimed account + env vars → NOT modified (a restart with stale env
    must not be a takeover vector), with an INFO notice that they are ignored."""
    # Claim the account the normal way first.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    with TestClient(server.api) as client:
        assert (
            client.post(
                f"{API_PREFIX}/login",
                json={"username": "realowner", "password": "realownerpass"},
            ).status_code
            == 200
        )
    claimed = server.auth.get_user()
    original_hash = claimed.password_hash
    assert original_hash is not None

    _set_initial_creds(monkeypatch, server, "attacker", "attackerpass")
    with caplog.at_level(logging.INFO, logger="pixlstash.server"):
        assert server.auth.claim_owner_from_env() is False
    unchanged = server.auth.get_user()
    assert unchanged.username == "realowner"
    assert unchanged.password_hash == original_hash
    assert any("already claimed" in r.getMessage() for r in caplog.records)


def test_env_claim_with_only_one_var_claims_nothing(server, monkeypatch, caplog):
    """Exactly one of the two vars set → loud warning, nothing claimed."""
    for present, absent in (
        (server.auth.INITIAL_OWNER_LOGIN_ENV, server.auth.INITIAL_OWNER_AUTH_ENV),
        (server.auth.INITIAL_OWNER_AUTH_ENV, server.auth.INITIAL_OWNER_LOGIN_ENV),
    ):
        caplog.clear()
        monkeypatch.setenv(present, "half-configured")
        monkeypatch.delenv(absent, raising=False)
        with caplog.at_level(logging.WARNING, logger="pixlstash.server"):
            assert server.auth.claim_owner_from_env() is False
        user = server.auth.get_user()
        assert user.username is None
        assert user.password_hash is None
        assert any(
            r.levelno == logging.WARNING and "nothing was claimed" in r.getMessage()
            for r in caplog.records
        )
        monkeypatch.delenv(present, raising=False)


def test_env_claim_rejects_password_over_72_bytes(server, monkeypatch, caplog):
    """An env password over bcrypt's 72-byte limit → existing validation fires,
    loud error, nothing claimed."""
    _set_initial_creds(monkeypatch, server, "dockerowner", "x" * 73)
    with caplog.at_level(logging.ERROR, logger="pixlstash.server"):
        assert server.auth.claim_owner_from_env() is False
    user = server.auth.get_user()
    assert user.username is None
    assert user.password_hash is None
    assert any(
        r.levelno == logging.ERROR and "72 bytes" in r.getMessage()
        for r in caplog.records
    )


def test_env_claim_rejects_password_below_login_floor(server, monkeypatch, caplog):
    """An env password under the login endpoint's 8-char floor would provision
    an account that can never log in (422 at /login) - refuse it loudly."""
    _set_initial_creds(monkeypatch, server, "dockerowner", "short7c")
    with caplog.at_level(logging.ERROR, logger="pixlstash.server"):
        assert server.auth.claim_owner_from_env() is False
    user = server.auth.get_user()
    assert user.username is None
    assert user.password_hash is None
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_registration_guard_unchanged_in_docker_but_message_actionable(
    server, monkeypatch
):
    """Under PIXLSTASH_IN_DOCKER=1 the guard REJECTS exactly as before (a
    container bridge gateway is an RFC 1918 address and carries no
    operator-vs-attacker signal), but the 403 tells the operator about the
    env-var provisioning path."""
    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    # Host traffic appears in-container as the container bridge's gateway
    # (Docker's is in 172.17.x); the guard reads only that it is private and
    # not loopback, so any RFC 1918 stand-in exercises the same branch.
    monkeypatch.setattr(
        server.auth, "_get_real_client_ip", lambda request: PRIVATE_172_IPV4
    )
    client = TestClient(server.api)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "operator", "password": "operatorpass"},
    )
    assert response.status_code == 403
    assert DOCKER_403_MARKER in response.json()["detail"]
    assert server.auth.get_user().password_hash is None

    # Same guard, same message on the second claim path (first password on an
    # unclaimed account via change_password).
    unclaimed = server.auth.get_user()
    monkeypatch.setattr(server.auth, "get_user_for_request", lambda request: unclaimed)
    payload = SimpleNamespace(current_password=None, new_password="operatorpass")
    with pytest.raises(Exception) as exc:
        server.auth.change_password(_fake_request(PRIVATE_172_IPV4), payload)
    assert getattr(exc.value, "status_code", None) == 403
    assert DOCKER_403_MARKER in getattr(exc.value, "detail", "")
    assert server.auth.get_user().password_hash is None


def test_registration_message_unchanged_outside_docker(server, monkeypatch):
    """Without the Docker flag the 403 detail is byte-identical to before."""
    monkeypatch.delenv("PIXLSTASH_IN_DOCKER", raising=False)
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: LAN_IPV4)
    client = TestClient(server.api)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "attacker", "password": "attackerpass"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == NON_DOCKER_403_DETAIL
    assert server.auth.get_user().password_hash is None


def test_registration_still_allowed_from_loopback_in_docker(server, monkeypatch):
    """The 'docker exec' loopback path keeps working under the Docker flag
    (the guard's allow side is untouched too)."""
    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    client = TestClient(server.api)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "owner", "password": "ownerpassword"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Username and password set successfully."
    assert server.auth.get_user().password_hash is not None


def test_secure_endpoint_works_on_loopback_with_require_ssl(server, monkeypatch):
    """require_ssl drives the external listener - it must NOT 403 the HTTP loopback.

    The desktop window always reaches the backend over plain-HTTP loopback while
    require_ssl may be enabled for the external listener. Secure-required
    endpoints (settings, taggers, share lookups, ...) must keep working for the
    local window; only genuinely remote plaintext requests are rejected.
    """
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()
    # Enable require_ssl as the external-listener setting would.
    monkeypatch.setitem(server.auth._server_config, "require_ssl", True)

    client = TestClient(server.api)
    client.cookies.set("session_id", DESKTOP_TOKEN)
    # TestClient requests look like local HTTP, so a secure-required endpoint
    # must still succeed (this is the settings-save / shared-ids regression).
    response = client.get(
        f"{API_PREFIX}/users/me/shared-resource-ids?resource_type=character"
    )
    assert response.status_code == 200


def test_secure_required_rejects_remote_plaintext(server, monkeypatch):
    """With require_ssl on, a remote (non-local) plaintext request is still 403."""
    from fastapi import HTTPException

    monkeypatch.setitem(server.auth._server_config, "require_ssl", True)
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")

    remote_http = SimpleNamespace(url=SimpleNamespace(scheme="http"))
    with pytest.raises(HTTPException) as exc:
        server.auth.ensure_secure_when_required(remote_http)
    assert exc.value.status_code == 403

    # https from the same remote client is allowed (it's over TLS).
    remote_https = SimpleNamespace(url=SimpleNamespace(scheme="https"))
    server.auth.ensure_secure_when_required(remote_https)  # no raise


def test_login_lockout_response_carries_retry_after(server):
    """The 429 backoff must tell the client how long to wait.

    Regression for #1097: a custom CORS exception handler rebuilt every
    HTTPException as a fresh JSONResponse and dropped exc.headers, so
    Retry-After never reached a client. Only the login 429 and the authz
    gate's 503 raise HTTPException(headers=...) - the other backoff paths
    (rate_limiter, restore, library admission) build their response directly
    and never pass through an exception handler at all.

    The Origin header matters: it is what makes CORSMiddleware stamp the
    response, and proving Retry-After survives *alongside* the CORS pair is
    the whole point of deleting the handler that used to overwrite it.
    """
    server.auth._login_lockout_until = time.monotonic() + 30

    client = TestClient(server.api)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "testuser", "password": "testpassword"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 429
    # The real backoff, not just any number: the lockout was armed for 30s.
    assert 28 <= int(response.headers["Retry-After"]) <= 31
    # CORSMiddleware still answers an allowed origin, Vary included.
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in response.headers["vary"]


def test_login_lockout_denies_cors_to_a_disallowed_origin(server):
    """Forwarding exc.headers must not become a way to widen CORS.

    The deleted handler merged a dict of headers into the response; anything
    that re-adds one has to keep this false, or an error body becomes readable
    cross-origin with credentials attached.
    """
    server.auth._login_lockout_until = time.monotonic() + 30

    client = TestClient(server.api)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "testuser", "password": "testpassword"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"]
    assert "access-control-allow-origin" not in response.headers
