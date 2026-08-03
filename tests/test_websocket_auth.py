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
import threading
import types

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


def _read_token(owner_client, scope="READ") -> str:
    r = owner_client.post(
        f"{API}/users/me/token",
        json={"description": f"{scope.lower()} token", "scope": scope},
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


def test_switch_cleanup_closes_and_forgets_every_live_websocket(server):
    class _ClosableWS:
        def __init__(self):
            self.closed = None

        async def close(self, code, reason):
            self.closed = (code, reason)

    first = _ClosableWS()
    second = _ClosableWS()
    comfyui_proxy = _ClosableWS()
    with server._ws_clients_lock:
        saved = list(server._ws_clients)
        server._ws_clients = [{"ws": first}, {"ws": second}]
        server._ws_clients.append({"ws": comfyui_proxy, "broadcast": False})
    try:
        asyncio.run(server._close_all_websockets())
        assert first.closed == (1012, "Library switched")
        assert second.closed == (1012, "Library switched")
        assert comfyui_proxy.closed == (1012, "Library switched")
        assert server._ws_clients == []
    finally:
        with server._ws_clients_lock:
            server._ws_clients = saved


# ---------------------------------------------------------------------------
# Origin-aware event envelope (X-Client-Id echo for slick grid updates)
# ---------------------------------------------------------------------------


class _CaptureWS:
    """An owner WebSocket stand-in that records the payloads it is sent."""

    def __init__(self):
        self.received = []

    async def send_json(self, payload):
        self.received.append(payload)


def _broadcast_capture(server, event_type, data):
    """Run ``_broadcast_ws_event`` against a single owner client, return payload."""
    ws = _CaptureWS()
    with server._ws_clients_lock:
        saved = list(server._ws_clients)
        server._ws_clients = [{"ws": ws, "filters": {}, "owner": True}]
    try:
        asyncio.run(server._broadcast_ws_event(event_type, data))
    finally:
        with server._ws_clients_lock:
            server._ws_clients = saved
    assert len(ws.received) == 1, f"Expected one payload, got {ws.received}"
    return ws.received[0]


# Every broadcast event type that reaches owner clients. The envelope contract
# is that EACH of these carries ``source`` and ``origin_client_id``.
_BROADCAST_EVENT_TYPES = [
    EventType.CHANGED_PICTURES,
    EventType.PICTURE_IMPORTED,
    EventType.CHANGED_TAGS,
    EventType.CLEARED_TAGS,
    EventType.CHANGED_DESCRIPTIONS,
    EventType.CHANGED_CHARACTERS,
    EventType.CHANGED_FACES,
]


@pytest.mark.parametrize("event_type", _BROADCAST_EVENT_TYPES)
def test_every_broadcast_carries_envelope(server, event_type):
    """Every owner-delivered event carries ``source`` + ``origin_client_id``,
    even when emitted with a bare id list (the defaults kick in)."""
    payload = _broadcast_capture(server, event_type, [1, 2, 3])
    assert "source" in payload, f"{event_type.name} missing source"
    assert "origin_client_id" in payload, f"{event_type.name} missing origin_client_id"
    # Bare list / no envelope data → background/external defaults.
    assert payload["source"] == "external"
    assert payload["origin_client_id"] is None


def test_import_envelope_ui_source_and_origin(server):
    """A UI-initiated import carries source 'ui', the originating client id, and
    change_kind 'added'."""
    payload = _broadcast_capture(
        server,
        EventType.PICTURE_IMPORTED,
        {
            "ids": [10, 11],
            "source": "ui",
            "origin_client_id": "tab-xyz",
            "change_kind": "added",
        },
    )
    assert payload["type"] == "picture_imported"
    assert payload["picture_ids"] == [10, 11]
    assert payload["source"] == "ui"
    assert payload["origin_client_id"] == "tab-xyz"
    assert payload["change_kind"] == "added"


def test_legacy_user_source_is_migrated_to_ui(server):
    """The legacy ``source: 'user'`` value migrates to ``'ui'`` on the wire."""
    payload = _broadcast_capture(
        server,
        EventType.PICTURE_IMPORTED,
        {"ids": [1], "source": "user"},
    )
    assert payload["source"] == "ui"


def test_external_import_is_source_external_origin_null(server):
    """An externally-ingested picture (no source/origin in data) is external."""
    payload = _broadcast_capture(server, EventType.PICTURE_IMPORTED, {"ids": [42]})
    assert payload["source"] == "external"
    assert payload["origin_client_id"] is None
    assert "change_kind" not in payload  # not set for external imports


def test_delete_carries_change_kind_removed(server):
    """A delete broadcast carries change_kind 'removed' and the origin id."""
    payload = _broadcast_capture(
        server,
        EventType.CHANGED_PICTURES,
        {
            "picture_ids": [7],
            "origin_client_id": "tab-del",
            "change_kind": "removed",
        },
    )
    assert payload["type"] == "pictures_changed"
    assert payload["picture_ids"] == [7]
    assert payload["change_kind"] == "removed"
    assert payload["origin_client_id"] == "tab-del"


def test_edit_carries_change_kind_updated_and_origin(server):
    """An in-UI edit broadcast carries change_kind 'updated' and the origin id."""
    payload = _broadcast_capture(
        server,
        EventType.CHANGED_PICTURES,
        {
            "picture_ids": [3, 4],
            "origin_client_id": "tab-edit",
            "change_kind": "updated",
            "fields": ["score"],
        },
    )
    assert payload["change_kind"] == "updated"
    assert payload["origin_client_id"] == "tab-edit"
    assert payload["fields"] == ["score"]


def test_tags_changed_dict_envelope_extracts_ids(server):
    """tags_changed accepts the dict envelope and still surfaces picture_ids."""
    payload = _broadcast_capture(
        server,
        EventType.CHANGED_TAGS,
        {
            "picture_ids": [5, 6],
            "origin_client_id": "tab-tag",
            "change_kind": "updated",
        },
    )
    assert payload["type"] == "tags_changed"
    assert payload["picture_ids"] == [5, 6]
    assert payload["origin_client_id"] == "tab-tag"


def test_x_client_id_header_populates_request_state(owner_client):
    """The OriginClientMiddleware reads X-Client-Id into request.state and a
    sane (<=200 char) value survives; an oversized one is dropped."""
    # A normal request with a header should succeed (echo-matching is opaque;
    # we assert the middleware doesn't break the request pipeline).
    r = owner_client.get(f"{API}/check-session", headers={"X-Client-Id": "tab-abc"})
    assert r.status_code == 200, r.text
    # An oversized header must not break the request either (it is ignored).
    r = owner_client.get(f"{API}/check-session", headers={"X-Client-Id": "x" * 5000})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# AuthService.authenticate_websocket / is_websocket_origin_allowed
# ---------------------------------------------------------------------------


class _FakeHandshake:
    """Minimal stand-in for a Starlette WebSocket at handshake time."""

    def __init__(self, cookies=None, query=None, headers=None, client_ip=None):
        from starlette.datastructures import Headers

        self.cookies = cookies or {}
        self.query_params = query or {}
        self.headers = Headers(headers or {})
        self.client = types.SimpleNamespace(host=client_ip) if client_ip else None


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


def test_authenticate_websocket_rejects_token_for_inactive_library(
    server, owner_client
):
    token = _read_token(owner_client)
    original_provider = server.auth.library_uuid_provider
    server.auth.library_uuid_provider = lambda: "inactive-library"
    try:
        assert (
            server.auth.authenticate_websocket(_FakeHandshake(query={"token": token}))
            is None
        )
        assert (
            server.auth.authenticate_websocket(
                _FakeHandshake(headers={"authorization": f"Bearer {token}"})
            )
            is None
        )
    finally:
        server.auth.library_uuid_provider = original_provider


@pytest.mark.parametrize("client_ip", ["8.8.8.8", "100.64.0.1"])
def test_authenticate_websocket_rejects_remote_all_bearer(
    server, owner_client, client_ip
):
    token = _read_token(owner_client, scope="ALL")
    auth = server.auth.authenticate_websocket(
        _FakeHandshake(
            headers={"authorization": f"Bearer {token}"}, client_ip=client_ip
        )
    )
    assert auth is None


def test_token_derived_cookie_websocket_keeps_library_pin(server):
    user = server.auth.get_user()
    server.auth._register_session(
        "token-cookie",
        user.id,
        token_public_id="public-token",
        token_library_uuid="library-a",
    )
    original_provider = server.auth.library_uuid_provider
    server.auth.library_uuid_provider = lambda: "library-b"
    try:
        assert (
            server.auth.authenticate_websocket(
                _FakeHandshake(cookies={"session_id": "token-cookie"})
            )
            is None
        )
    finally:
        server.auth.library_uuid_provider = original_provider
        server.auth._forget_session("token-cookie")


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


def test_updates_accept_failure_releases_generation_lease(
    server, owner_client, monkeypatch
):
    from starlette.websockets import WebSocket

    async def fail_accept(_self, *args, **kwargs):
        raise RuntimeError("injected accept failure")

    monkeypatch.setattr(WebSocket, "accept", fail_accept)
    with pytest.raises(RuntimeError, match="injected accept failure"):
        with owner_client.websocket_connect(WS_UPDATES):
            pass
    assert server.library_coordinator._readers == {}


def test_updates_registration_failure_releases_generation_lease(server, owner_client):
    class ExplodingClients(list):
        def append(self, _client):
            raise RuntimeError("injected registration failure")

    original = server._ws_clients
    server._ws_clients = ExplodingClients(original)
    try:
        with pytest.raises(RuntimeError, match="injected registration failure"):
            with owner_client.websocket_connect(WS_UPDATES):
                pass
        assert server.library_coordinator._readers == {}
    finally:
        server._ws_clients = original


def test_library_switch_terminates_the_comfyui_proxy(
    server, owner_client, tmp_path, monkeypatch
):
    """The progress proxy is old-library work and shares the WS drain."""
    from pixlstash.routes import comfyui as comfyui_routes

    class _BlockingUpstream:
        def __init__(self):
            self.entered = threading.Event()
            self.exited = threading.Event()

        async def __aenter__(self):
            self.entered.set()
            return self

        async def __aexit__(self, *_args):
            self.exited.set()

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Future()

        async def send(self, _message):
            return None

    upstream = _BlockingUpstream()
    monkeypatch.setattr(
        comfyui_routes.websockets, "connect", lambda *_a, **_k: upstream
    )
    original = server.library_registry.active_library()
    target = server.library_registry.create(str(tmp_path / "ws-switch"), "WS switch")

    try:
        with owner_client.websocket_connect(WS_COMFYUI) as websocket:
            assert upstream.entered.wait(timeout=5)
            with server._ws_clients_lock:
                assert any(
                    client.get("ws") is not None and client.get("broadcast") is False
                    for client in server._ws_clients
                )
            response = owner_client.post(
                f"{API}/libraries/active", json={"uuid": target.uuid}
            )
            assert response.status_code == 200, response.text
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_text()
            assert excinfo.value.code == 1012
        assert upstream.exited.wait(timeout=5), "upstream proxy context must terminate"
    finally:
        if server.library_registry.active_library().uuid != original.uuid:
            server.library_switch.switch_to(original.uuid)


# ---------------------------------------------------------------------------
# The HTTP authz gate must NEVER run on a WebSocket route
# ---------------------------------------------------------------------------


def test_authz_gate_noops_on_websocket_scope():
    """Regression: the HTTP authz gate must no-op on a WebSocket connection.

    The gate is mounted router-wide (``dependencies=[Depends(self.authz)]``), so
    FastAPI also attaches it to ``@router.websocket`` routes in those routers.
    WS routes are out of the HTTP gate by design (their chokepoint is
    ``authenticate_websocket``). The gate must therefore resolve harmlessly and
    enforce nothing on a WS scope: it must not crash the handshake (the old
    ``request: Request`` param did — ``TypeError: missing 'request'``) and, even
    when ENFORCING against an empty registry (which 403s any *HTTP* route as an
    undeclared miss), a WS connection must return ``None`` before that lookup.
    """
    from starlette.websockets import WebSocket

    from pixlstash.authz.gate import AuthzGate

    # enforcing=True + empty registry: an HTTP route with no declaration would be
    # denied 403 here. A WS connection must short-circuit before that.
    gate = AuthzGate(registry={}, enforcing=True)
    ws = WebSocket(
        {"type": "websocket", "path": "/api/v1/ws/comfyui", "headers": []},
        receive=None,
        send=None,
    )
    assert asyncio.run(gate(ws)) is None
