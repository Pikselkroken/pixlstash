"""Regression tests for WebSocket authentication and event scoping.

The HTTP auth middleware only runs for the ``http`` ASGI scope, so the
``/ws/updates`` and ``/ws/comfyui`` WebSocket routes must authenticate
themselves. These tests guard against:

* anonymous clients subscribing to vault activity,
* cross-site WebSocket hijacking (CSWSH) via a foreign ``Origin``,
* an unauthenticated ComfyUI proxy to the internal/default service, and
* resource-scoped / READ tokens receiving owner-level events.
"""

import asyncio
import json
import tempfile

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pixlstash.auth import WebSocketAuth
from pixlstash.event_types import EventType
from pixlstash.server import Server

API = "/api/v1"
WS_UPDATES = f"{API}/ws/updates"
WS_COMFYUI = f"{API}/ws/comfyui"


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = f"{tmp}/server-config.json"
        with open(config_path, "w") as fh:
            json.dump({"disable_background_workers": True}, fh)
        with Server(config_path) as srv:
            yield srv


@pytest.fixture
def owner_client(server):
    """A TestClient logged in as the owner (carries the session cookie)."""
    client = TestClient(server.api, raise_server_exceptions=True)
    r = client.post(
        f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
    )
    assert r.status_code == 200, r.text
    return client


def _read_token(owner_client) -> str:
    r = owner_client.post(
        f"{API}/users/me/token",
        json={"description": "read-only", "scope": "READ"},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---------------------------------------------------------------------------
# /ws/updates handshake
# ---------------------------------------------------------------------------


def test_updates_rejects_anonymous(server):
    """An unauthenticated client must not be able to open the update stream."""
    anon = TestClient(server.api)
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect(WS_UPDATES):
            pass


def test_updates_rejects_foreign_origin(owner_client):
    """Even with a valid session cookie, a cross-site Origin is refused (CSWSH):
    a malicious page can ride the victim's cookie, so the Origin guard is what
    actually stops it."""
    with pytest.raises(WebSocketDisconnect):
        with owner_client.websocket_connect(
            WS_UPDATES, headers={"origin": "https://evil.example"}
        ):
            pass


def test_updates_accepts_owner_cookie(owner_client):
    """The owner's same-origin cookie session is accepted."""
    with owner_client.websocket_connect(WS_UPDATES) as ws:
        # Sending a filter message round-trips without error → handshake good.
        ws.send_json({"type": "set_filters"})


def test_updates_accepts_read_token_via_query(server, owner_client):
    """A READ share token authenticates the handshake via ?token= (anonymous
    is rejected, so a cookie-less client connecting here proves the token was
    honoured)."""
    token = _read_token(owner_client)
    anon = TestClient(server.api)  # no session cookie — only the token can auth
    with anon.websocket_connect(f"{WS_UPDATES}?token={token}") as ws:
        ws.send_json({"type": "set_filters"})


# ---------------------------------------------------------------------------
# Event delivery is scoped: only owner connections receive the global stream
# ---------------------------------------------------------------------------


def test_broadcast_delivers_only_to_owner_clients(server):
    """A resource-scoped / READ client (owner=False) may be connected but must
    never receive the owner-level vault-activity broadcast."""

    class _FakeWS:
        def __init__(self):
            self.received = []

        async def send_json(self, payload):
            self.received.append(payload)

    owner_ws = _FakeWS()
    scoped_ws = _FakeWS()
    with server._ws_clients_lock:
        saved = list(server._ws_clients)
        server._ws_clients = [
            {"ws": owner_ws, "filters": {}, "owner": True},
            {"ws": scoped_ws, "filters": {}, "owner": False},
        ]
    try:
        asyncio.run(server._broadcast_ws_event(EventType.CHANGED_TAGS, [1, 2, 3]))
    finally:
        with server._ws_clients_lock:
            server._ws_clients = saved

    assert len(owner_ws.received) == 1, "Owner must receive the event"
    assert owner_ws.received[0]["type"] == "tags_changed"
    assert scoped_ws.received == [], "Scoped client must receive no global events"


# ---------------------------------------------------------------------------
# AuthService.authenticate_websocket / is_websocket_origin_allowed
# ---------------------------------------------------------------------------


class _FakeHandshake:
    """Minimal stand-in for a Starlette WebSocket at handshake time."""

    def __init__(self, cookies=None, query=None, headers=None):
        from starlette.datastructures import Headers

        self.cookies = cookies or {}
        self.query_params = query or {}
        self.headers = Headers(headers or {})


def test_authenticate_websocket_cookie_is_owner(server):
    # Seed a live session id the way /login does.
    user = server.auth.get_user()
    server.auth.active_session_ids["sess-abc"] = user.id
    try:
        ws = _FakeHandshake(cookies={"session_id": "sess-abc"})
        auth = server.auth.authenticate_websocket(ws)
        assert auth == WebSocketAuth(user_id=user.id, is_owner=True)
    finally:
        server.auth.active_session_ids.pop("sess-abc", None)


def test_authenticate_websocket_anonymous_returns_none(server):
    assert server.auth.authenticate_websocket(_FakeHandshake()) is None


def test_authenticate_websocket_read_token_is_not_owner(server, owner_client):
    token = _read_token(owner_client)
    ws = _FakeHandshake(query={"token": token})
    auth = server.auth.authenticate_websocket(ws)
    assert auth is not None
    assert auth.is_owner is False, "READ token must not be owner-scoped"


def test_origin_check(server):
    origins = ["https://app.example"]
    rx = r"^https?://(localhost)(:\d+)?$"
    # Missing Origin (non-browser) → allowed through to the auth check.
    assert server.auth.is_websocket_origin_allowed(_FakeHandshake(), origins, rx)
    # Same-origin (Origin host == Host) → allowed.
    same = _FakeHandshake(
        headers={"origin": "http://myhost:9537", "host": "myhost:9537"}
    )
    assert server.auth.is_websocket_origin_allowed(same, [], None)
    # Configured allow-list / regex → allowed.
    allowed = _FakeHandshake(headers={"origin": "https://app.example", "host": "h"})
    assert server.auth.is_websocket_origin_allowed(allowed, origins, rx)
    regexed = _FakeHandshake(headers={"origin": "http://localhost", "host": "h"})
    assert server.auth.is_websocket_origin_allowed(regexed, [], rx)
    # Foreign Origin → rejected.
    evil = _FakeHandshake(headers={"origin": "https://evil.example", "host": "h"})
    assert not server.auth.is_websocket_origin_allowed(evil, origins, rx)


# ---------------------------------------------------------------------------
# /ws/comfyui must not proxy for unauthenticated clients
# ---------------------------------------------------------------------------


def test_comfyui_proxy_rejects_anonymous(server):
    anon = TestClient(server.api)
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect(WS_COMFYUI):
            pass
