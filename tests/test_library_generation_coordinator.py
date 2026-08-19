"""Generation admission keeps every active-vault request on one runtime tuple."""

import asyncio
from types import SimpleNamespace

import pytest

from pixlstash.authz.policy import LibraryAccessMode
from pixlstash.middleware.library_admission import LibraryAdmissionMiddleware
from pixlstash.services.library_generation_coordinator import (
    LibraryGenerationCoordinator,
)
from pixlstash.services.library_switch_service import SwitchState


class _Registry:
    def __init__(self, library):
        self.library = library

    def active_library(self):
        return self.library


def _coherent_server():
    db = SimpleNamespace(is_open=True)
    vault = SimpleNamespace(is_open=True, db=db, image_root="/library-a")
    library = SimpleNamespace(uuid="library-a", path="/library-a")
    return SimpleNamespace(
        vault=vault,
        auth=SimpleNamespace(vault_db=db),
        library_registry=_Registry(library),
    )


def test_lease_captures_one_coherent_generation_and_runtime_tuple():
    server = _coherent_server()
    coordinator = LibraryGenerationCoordinator(server)

    lease = coordinator.acquire_read()

    assert lease is not None
    assert (lease.generation, lease.library_uuid) == (0, "library-a")
    assert lease.vault is server.vault
    assert lease.db is server.vault.db
    coordinator.release_read(lease)


def test_mismatched_registry_vault_auth_tuple_is_never_admitted():
    server = _coherent_server()
    coordinator = LibraryGenerationCoordinator(server)

    server.auth.vault_db = SimpleNamespace(is_open=True)
    assert coordinator.acquire_read() is None


def test_writer_wait_is_bounded_and_restores_ready_for_coherent_runtime():
    server = _coherent_server()
    coordinator = LibraryGenerationCoordinator(server)
    lease = coordinator.acquire_read()
    assert lease is not None

    with pytest.raises(RuntimeError, match="Timed out"):
        coordinator.begin_switch(timeout=0.01)

    assert coordinator.state is SwitchState.READY
    coordinator.release_read(lease)


def test_outer_http_lease_lives_through_the_final_body_frame():
    events = []

    class Coordinator:
        state = SwitchState.READY

        def acquire_read(self):
            events.append("acquire")
            return SimpleNamespace(library_uuid="library-a")

        def release_read(self, _lease):
            events.append("release")

    async def app(scope, receive, send):
        assert scope["state"]["library_lease"].library_uuid == "library-a"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        assert events == ["acquire"]
        await send({"type": "http.response.body", "body": b"last"})
        assert events == ["acquire"]

    server = SimpleNamespace(library_coordinator=Coordinator())
    middleware = LibraryAdmissionMiddleware(app, server=server)
    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/pictures",
        "state": {},
    }

    asyncio.run(middleware(scope, None, send))

    assert events == ["acquire", "release"]
    assert sent[-1]["body"] == b"last"


def test_route_mode_resolution_prefers_api_route_over_spa_catch_all():
    server = SimpleNamespace(library_coordinator=SimpleNamespace())
    middleware = LibraryAdmissionMiddleware(lambda *_args: None, server=server)

    assert (
        middleware._mode({"method": "GET", "path": "/api/v1/pictures"})
        is LibraryAccessMode.ACTIVE_VAULT
    )
    assert (
        middleware._mode({"method": "GET", "path": "/api/v1/pictures/42/metadata"})
        is LibraryAccessMode.ACTIVE_VAULT
    )
    assert (
        middleware._mode({"method": "GET", "path": "/api/v1/future-undeclared-route"})
        is LibraryAccessMode.ACTIVE_VAULT
    )
    assert (
        middleware._mode({"method": "GET", "path": "/some/frontend/deep-link"})
        is LibraryAccessMode.HUB_ONLY
    )
    assert (
        middleware._mode({"method": "POST", "path": "/api/v1/libraries/active"})
        is LibraryAccessMode.SWITCH_WRITER
    )
