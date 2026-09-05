"""Step 4 of the centralised-authz refactor: the gate goes ENFORCING for the
object-scoped policy classes (``*_SCOPED`` single-id, ``body_ids`` batch, and
``SCOPED_LIST``).

Every assertion is both-directional per CLAUDE.md / §16.1: an out-of-scope
resource-scoped token is 403'd (or a list is emptied/denied) AND an in-scope
principal still reaches the route - over-blocking is its own regression.

Toggle mechanism (principal ruling 2026-07-21): the shipped constant
``AUTHZ_GATE_ENFORCING`` stays ``False``; Step-4 enforcement is proven here by
constructing / flipping the gate to ``enforcing=True``.

Two layers:

* **Gate-contract decoys** - a decoy app + the gate as a router dependency, with
  ``token_scope`` injected by a tiny scope-setter dependency. This proves the
  gate's Step-4 contract in isolation (no auth middleware), which is the ONLY way
  to exercise the *latent* routes whose scoped tokens the auth middleware blocks
  today: the ``body_ids`` batch routes, ``run_t2i``'s single/optional body id, and
  the ``tag_suggestions`` ``id_resolver`` routes (§N4). It also proves the
  ``SCOPED_LIST`` unaudited-leaks-nothing rule and the ``resolved_inline`` (§N3)
  skip.
* **Real-server integration** - a live ``Server`` with real pictures / set /
  character / project and real resource-scoped tokens, proving the gate enforces
  on routes a scoped token actually reaches (picture / set / character / project
  single-id), plus the ``project_id=UNASSIGNED`` historical-leak regression and
  the ``SCOPED_LIST`` scope-aware pass-through (stream vs list, /stacks/{id}/pictures).
"""

import contextlib
import gc
import json
import os
import tempfile
import types

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from starlette.testclient import TestClient

from pixlstash.authz.gate import AUTHZ_GATE_ENFORCING, AuthzGate
from pixlstash.authz.policy import AccessPolicy, RoutePolicy
from pixlstash.server import Server
from tests.authz_guard import (  # noqa: F401
    assert_real_route,
    no_spa_fallback,
    resolves_to_real_route,
)
from tests.utils import upload_pictures_and_wait

API = "/api/v1"

# Every positive assertion here must reach a real route: the SPA catch-all answers
# unmatched GETs with 200, which once made a whole-library BOLA vector's test
# vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


# ===========================================================================
# Layer A - gate-contract decoys
# ===========================================================================


class _Scope:
    """Minimal ``token_scope`` stand-in (the fields the membership ladder reads)."""

    def __init__(self, resource_type, resource_id):
        self.resource_type = resource_type
        self.resource_id = resource_id


def _scope_setter(request: Request):
    """Populate ``request.state.token_scope`` from test headers, standing in for
    the auth middleware. ``X-Test-Scope-Type`` absent => owner (no scope)."""
    rtype = request.headers.get("x-test-scope-type")
    if not rtype:
        return
    rid = request.headers.get("x-test-scope-id")
    request.state.token_scope = _Scope(
        None if rtype == "none" else rtype, int(rid) if rid else None
    )


class _FakeSession:
    def __init__(self, suggestion_to_picture):
        self._map = suggestion_to_picture

    def get(self, _model, sid):
        pid = self._map.get(int(sid))
        return types.SimpleNamespace(picture_id=pid) if pid is not None else None


class _FakeServer:
    """Server stub exposing ``vault.db.run_immediate_read_task`` for the
    ``tag_suggestion`` id_resolver's ``session.get`` lookup only."""

    def __init__(self, suggestion_to_picture):
        session = _FakeSession(suggestion_to_picture)

        class _DB:
            def run_immediate_read_task(self, func, *args, **kwargs):
                return func(session, *args, **kwargs)

        self.vault = types.SimpleNamespace(db=_DB())


def _decoy_app(gate, routes):
    """Build a decoy app: each (method, path) in *routes* is a trivial handler
    guarded by the scope-setter then the gate (dependency order is significant)."""
    router = APIRouter()
    for method, path in routes:
        adder = getattr(router, method.lower())

        @adder(path)
        async def _handler():
            # The gate reads any request body itself (request.json()); the handler
            # needs no params. Undeclared path params stay available via
            # request.path_params, which is what the gate reads.
            return {"ok": True}

    app = FastAPI()
    app.include_router(
        router,
        prefix="/api/v1/step4-decoy",
        dependencies=[Depends(_scope_setter), Depends(gate)],
    )
    gate.resolve_routes(app)
    return app


def _pic_scope_headers(picture_id: int) -> dict:
    """Headers simulating a single-picture-scoped token (in-memory membership)."""
    return {"X-Test-Scope-Type": "picture", "X-Test-Scope-Id": str(picture_id)}


# --- PICTURE_SCOPED single id ---------------------------------------------


def test_picture_scoped_single_both_directions():
    registry = {
        ("GET", "/api/v1/step4-decoy/pic/{id}"): RoutePolicy(
            AccessPolicy.PICTURE_SCOPED, id_param="id"
        ),
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=None)
    client = TestClient(_decoy_app(gate, [("GET", "/pic/{id}")]))

    # In-scope: picture 7, token scoped to picture 7 -> 200.
    r = client.get("/api/v1/step4-decoy/pic/7", headers=_pic_scope_headers(7))
    assert r.status_code == 200, r.text
    # Out-of-scope: picture 8, token scoped to picture 7 -> 403.
    r = client.get("/api/v1/step4-decoy/pic/8", headers=_pic_scope_headers(7))
    assert r.status_code == 403, r.text
    # Owner (no scope header) -> pass.
    assert client.get("/api/v1/step4-decoy/pic/8").status_code == 200


def test_owner_and_unscoped_read_short_circuit():
    """Owner (no token_scope) and an unscoped-READ token (resource_type None) both
    pass every object-scoped class untouched - exactly as today's ladders do."""
    registry = {
        ("GET", "/api/v1/step4-decoy/pic/{id}"): RoutePolicy(
            AccessPolicy.PICTURE_SCOPED, id_param="id"
        ),
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=None)
    client = TestClient(_decoy_app(gate, [("GET", "/pic/{id}")]))

    assert client.get("/api/v1/step4-decoy/pic/8").status_code == 200  # owner
    # Unscoped-READ: token_scope present but resource_type None -> not narrowed.
    r = client.get("/api/v1/step4-decoy/pic/8", headers={"X-Test-Scope-Type": "none"})
    assert r.status_code == 200, r.text


# --- body_ids batch: EVERY id checked, not just the first ------------------


def test_body_ids_batch_checks_every_id():
    registry = {
        ("DELETE", "/api/v1/step4-decoy/pics"): RoutePolicy(
            AccessPolicy.PICTURE_SCOPED, body_ids="picture_ids"
        ),
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=None)
    client = TestClient(_decoy_app(gate, [("DELETE", "/pics")]))
    hdr = _pic_scope_headers(7)  # only picture 7 in scope

    # [in] -> 200.
    r = client.request(
        "DELETE", "/api/v1/step4-decoy/pics", json={"picture_ids": [7]}, headers=hdr
    )
    assert r.status_code == 200, r.text
    # [in, out] -> 403 (the OUT id is second: proves it does not stop at the first).
    r = client.request(
        "DELETE",
        "/api/v1/step4-decoy/pics",
        json={"picture_ids": [7, 8]},
        headers=hdr,
    )
    assert r.status_code == 403, r.text
    # [out, in] -> 403 too.
    r = client.request(
        "DELETE",
        "/api/v1/step4-decoy/pics",
        json={"picture_ids": [8, 7]},
        headers=hdr,
    )
    assert r.status_code == 403, r.text
    # Owner -> pass regardless.
    r = client.request(
        "DELETE", "/api/v1/step4-decoy/pics", json={"picture_ids": [7, 8]}
    )
    assert r.status_code == 200, r.text


def test_body_ids_single_optional_scalar_run_t2i():
    """§N6: run_t2i's ``source_picture_id`` is a single, optional scalar - the gate
    tolerates absent (no-op) and checks a present one."""
    registry = {
        ("POST", "/api/v1/step4-decoy/t2i"): RoutePolicy(
            AccessPolicy.PICTURE_SCOPED, body_ids="source_picture_id"
        ),
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=None)
    client = TestClient(_decoy_app(gate, [("POST", "/t2i")]))
    hdr = _pic_scope_headers(7)

    # Absent source id -> nothing to check -> pass.
    r = client.post("/api/v1/step4-decoy/t2i", json={"caption": "x"}, headers=hdr)
    assert r.status_code == 200, r.text
    # In-scope single scalar -> pass.
    r = client.post(
        "/api/v1/step4-decoy/t2i", json={"source_picture_id": 7}, headers=hdr
    )
    assert r.status_code == 200, r.text
    # Out-of-scope single scalar -> 403.
    r = client.post(
        "/api/v1/step4-decoy/t2i", json={"source_picture_id": 8}, headers=hdr
    )
    assert r.status_code == 403, r.text


# --- id_resolver (§N4 tag_suggestions: suggestion -> picture) --------------


def test_id_resolver_single_item_both_directions():
    registry = {
        ("POST", "/api/v1/step4-decoy/sugg/{suggestion_id}/accept"): RoutePolicy(
            AccessPolicy.PICTURE_SCOPED,
            id_param="suggestion_id",
            id_resolver="tag_suggestion",
        ),
    }
    # suggestion 100 -> picture 7 (in scope); 200 -> picture 8 (out); 999 unknown.
    server = _FakeServer({100: 7, 200: 8})
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=server)
    client = TestClient(_decoy_app(gate, [("POST", "/sugg/{suggestion_id}/accept")]))
    hdr = _pic_scope_headers(7)

    # Suggestion whose picture is in scope -> 200.
    r = client.post("/api/v1/step4-decoy/sugg/100/accept", headers=hdr)
    assert r.status_code == 200, r.text
    # Suggestion whose picture is out of scope -> 403.
    r = client.post("/api/v1/step4-decoy/sugg/200/accept", headers=hdr)
    assert r.status_code == 403, r.text
    # Unknown suggestion (resolver returns None) -> fail closed 403.
    r = client.post("/api/v1/step4-decoy/sugg/999/accept", headers=hdr)
    assert r.status_code == 403, r.text
    # Owner -> pass.
    assert client.post("/api/v1/step4-decoy/sugg/200/accept").status_code == 200


def test_id_resolver_bulk_reopen_checks_every_suggestion():
    """The highest-risk carry-forward: bulk-reopen resolves EVERY body suggestion
    id to its picture and denies if any is out of scope."""
    registry = {
        ("POST", "/api/v1/step4-decoy/sugg/bulk-reopen"): RoutePolicy(
            AccessPolicy.PICTURE_SCOPED,
            body_ids="ids",
            id_resolver="tag_suggestion",
        ),
    }
    server = _FakeServer({100: 7, 200: 8})
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=server)
    client = TestClient(_decoy_app(gate, [("POST", "/sugg/bulk-reopen")]))
    hdr = _pic_scope_headers(7)

    # All in scope -> 200.
    r = client.post(
        "/api/v1/step4-decoy/sugg/bulk-reopen", json={"ids": [100]}, headers=hdr
    )
    assert r.status_code == 200, r.text
    # One out-of-scope suggestion in the batch -> 403 (every id checked).
    r = client.post(
        "/api/v1/step4-decoy/sugg/bulk-reopen", json={"ids": [100, 200]}, headers=hdr
    )
    assert r.status_code == 403, r.text


# --- SCOPED_LIST: scope-aware passes; unaudited leaks NOTHING (403) ---------


def test_scoped_list_unaudited_fails_closed_for_scoped_token():
    """A SCOPED_LIST route WITHOUT ``scope_aware`` (a new, unaudited list) must leak
    nothing to a resource-scoped token: the gate fails it closed (D4)."""
    registry = {
        ("GET", "/api/v1/step4-decoy/unaudited-list"): RoutePolicy(
            AccessPolicy.SCOPED_LIST  # scope_aware defaults False
        ),
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=None)
    client = TestClient(_decoy_app(gate, [("GET", "/unaudited-list")]))

    # Resource-scoped token -> 403 (leaks nothing).
    r = client.get(
        "/api/v1/step4-decoy/unaudited-list",
        headers={"X-Test-Scope-Type": "picture_set", "X-Test-Scope-Id": "1"},
    )
    assert r.status_code == 403, r.text
    # Owner -> pass (an unaudited list is not broken for the owner).
    assert client.get("/api/v1/step4-decoy/unaudited-list").status_code == 200


def test_scoped_list_audited_passes_through_and_stamps_signal():
    """An audited ``scope_aware`` SCOPED_LIST passes a resource-scoped token through
    to its own inline filter and stamps the declared-intent signal (no over-block)."""
    registry = {
        ("GET", "/api/v1/step4-decoy/audited-list"): RoutePolicy(
            AccessPolicy.SCOPED_LIST, scope_aware=True
        ),
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=None)

    seen = {}
    router = APIRouter()

    @router.get("/audited-list")
    async def _list(request: Request):
        seen["scope_filter_required"] = getattr(
            request.state, "scope_filter_required", None
        )
        return {"ok": True}

    app = FastAPI()
    app.include_router(
        router,
        prefix="/api/v1/step4-decoy",
        dependencies=[Depends(_scope_setter), Depends(gate)],
    )
    gate.resolve_routes(app)
    client = TestClient(app)

    r = client.get(
        "/api/v1/step4-decoy/audited-list",
        headers={"X-Test-Scope-Type": "picture_set", "X-Test-Scope-Id": "1"},
    )
    assert r.status_code == 200, r.text
    assert seen["scope_filter_required"] is True, (
        "the gate must stamp scope_filter_required for an audited scope-aware list"
    )


# --- resolved_inline (§N3): gate does NOT object-check; inline stays owner --


def test_resolved_inline_route_is_not_object_checked_by_gate():
    """A ``resolved_inline`` *_SCOPED route (name-derived id) is passed through by
    the gate even for a resource-scoped token - the inline handler check is the
    enforcement. ``server=None`` proves the gate does not attempt a DB resolution."""
    registry = {
        ("GET", "/api/v1/step4-decoy/projects/{id_or_name}"): RoutePolicy(
            AccessPolicy.PROJECT_SCOPED,
            id_param="id_or_name",
            resolved_inline=True,
            justification="§N3 name-derived id; inline check enforces",
        ),
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=None)
    client = TestClient(_decoy_app(gate, [("GET", "/projects/{id_or_name}")]))

    # A resource-scoped token would be 403'd if the gate object-checked; because
    # the route is resolved_inline, the gate passes it through (no DB, no 403).
    r = client.get(
        "/api/v1/step4-decoy/projects/my-project-name",
        headers={"X-Test-Scope-Type": "project", "X-Test-Scope-Id": "5"},
    )
    assert r.status_code == 200, r.text


# --- UNASSIGNED-style non-int id fails closed for a scoped token -----------


def test_non_integer_scoped_id_fails_closed():
    """A ``project_id=UNASSIGNED`` aggregate id on a numeric ``*_SCOPED`` route (not
    resolved_inline) cannot be parsed to an id; the gate fails it closed for a
    resource-scoped token - matching ``get_project_summary``'s own UNASSIGNED 403
    (the historical aggregate-leak class)."""
    registry = {
        ("GET", "/api/v1/step4-decoy/proj/{project_id}/summary"): RoutePolicy(
            AccessPolicy.PROJECT_SCOPED, id_param="project_id"
        ),
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None, server=None)
    client = TestClient(_decoy_app(gate, [("GET", "/proj/{project_id}/summary")]))

    r = client.get(
        "/api/v1/step4-decoy/proj/UNASSIGNED/summary",
        headers={"X-Test-Scope-Type": "project", "X-Test-Scope-Id": "5"},
    )
    assert r.status_code == 403, r.text
    # Owner may still request the aggregate (short-circuits before parsing).
    assert client.get("/api/v1/step4-decoy/proj/UNASSIGNED/summary").status_code == 200


def test_anti_vacuity_guard_recognises_the_spa_catch_all():
    """Pin the guard that keeps this file honest (``tests/authz_guard.py``).

    ``/stacks/{id}/stack`` is the URL this suite asserted 200 against for five
    days. It is not a route; the SPA catch-all answered it, so the assertion was
    vacuous while its docstring claimed to cover ``/stacks/{id}/pictures`` - a
    historical whole-library BOLA vector. Assert the discrimination directly, with
    no server, so the guard cannot itself rot unnoticed.
    """
    app = FastAPI()

    @app.get("/api/v1/stacks/{stack_id}/pictures")
    def _real():  # pragma: no cover - never called
        return []

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):  # pragma: no cover - never called
        return "index.html"

    assert resolves_to_real_route(app, "GET", "/api/v1/stacks/1/pictures")
    assert not resolves_to_real_route(app, "GET", "/api/v1/stacks/1/stack")
    with pytest.raises(AssertionError, match="matches no mounted API route"):
        assert_real_route(app, "GET", "/api/v1/stacks/1/stack")


def test_shipped_default_is_enforcing():
    """Step 6 flipped the rollback constant: the gate ships ENFORCING. Report-only
    remains the one-line rollback (flip back to False)."""
    assert AUTHZ_GATE_ENFORCING is True


# ===========================================================================
# Layer B - real-server integration (gate enforcing on live scoped routes)
# ===========================================================================


def _good_picture_files():
    pictures_dir = os.path.join(os.path.dirname(__file__), "..", "pictures", "good")
    results = []
    for name in sorted(os.listdir(pictures_dir)):
        path = os.path.join(pictures_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            ct = "image/png" if ext == ".png" else "image/jpeg"
            with open(path, "rb") as fh:
                results.append((name, fh.read(), ct))
    return results


@contextlib.contextmanager
def _enforcing(server):
    prev = server.authz._enforcing
    server.authz._enforcing = True
    try:
        yield
    finally:
        server.authz._enforcing = prev


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env():
    """A live server with 2 pictures, a set containing pic A, a character, and a
    project containing pic A; plus resource-scoped READ tokens for each."""
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(config_path)
    server.__enter__()
    try:
        client = TestClient(server.api, raise_server_exceptions=True)
        anon = TestClient(server.api, raise_server_exceptions=True)
        r = client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert r.status_code == 200, r.text

        files = [("file", (n, d, c)) for n, d, c in _good_picture_files()[:2]]
        assert len(files) >= 2, "need >=2 test pictures"
        st = upload_pictures_and_wait(client, files, timeout_s=30)
        assert st["status"] == "completed", st
        pic_ids = [p["id"] for p in client.get(f"{API}/pictures").json()]
        assert len(pic_ids) >= 2
        pic_a, pic_b = pic_ids[0], pic_ids[1]

        # Set containing only pic A.
        set_id = client.post(f"{API}/picture_sets", json={"name": "S"}).json()[
            "picture_set"
        ]["id"]
        r = client.post(f"{API}/picture_sets/{set_id}/members/{pic_a}")
        assert r.status_code in (200, 201), r.text

        # Character (no face needed: a character-scoped token compares ids only).
        char_id = client.post(f"{API}/characters", json={"name": "C"}).json()[
            "character"
        ]["id"]

        # Project containing only pic A.
        proj_id = client.post(f"{API}/projects", json={"name": "P"}).json()["id"]
        r = client.patch(
            f"{API}/pictures/project",
            json={"picture_ids": [pic_a], "project_id": proj_id, "mode": "set"},
        )
        assert r.status_code == 200, r.text

        def mint(resource_type, resource_id):
            r = client.post(
                f"{API}/users/me/token",
                json={
                    "description": f"{resource_type}:{resource_id}",
                    "scope": "READ",
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                },
            )
            assert r.status_code == 200, r.text
            return r.json()["token"]

        tokens = {
            "set": mint("picture_set", set_id),
            "set_other": mint("picture_set", set_id + 999),
            "char": mint("character", char_id),
            "char_other": mint("character", char_id + 999),
            "proj": mint("project", proj_id),
        }
        yield {
            "server": server,
            "owner": client,
            "anon": anon,
            "pic_a": pic_a,
            "pic_b": pic_b,
            "set_id": set_id,
            "char_id": char_id,
            "proj_id": proj_id,
            "mint": mint,
            "tokens": tokens,
        }
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture
def stacked_env(env):
    """``env`` plus a stack holding BOTH pictures and a *picture*-scoped token for
    pic A only - the fixture the stack read-route leak tests need.

    The grant shape matters and is not interchangeable. Stacking is deliberately
    **set/project-membership-atomic**: ``create_stack`` calls
    ``reconcile_stack_membership``, which unions every member's set and project
    memberships across the stack. So stacking A with B *adds B to A's set and
    project*, and a set- or project-scoped token therefore cannot straddle a
    stack - by construction, never a leak, and useless as a probe.

    A ``picture`` token grants exactly ``{resource_id}``
    (``fetch_scope_allowed_picture_ids``) and is untouched by stacking, so it is
    the one grant that does straddle. It is also the realistic threat model: a
    single-picture share link whose holder tries to enumerate the library through
    that picture's stack.

    Note the stack is built here rather than in ``env`` precisely because of the
    reconciliation above: creating it widens set S and project P to include B,
    which would silently defeat the set/project scope tests that share ``env``.
    """
    owner, mint = env["owner"], env["mint"]
    r = owner.post(f"{API}/stacks", json={"picture_ids": [env["pic_a"], env["pic_b"]]})
    assert r.status_code == 200, r.text
    body = r.json()
    stack_id = body.get("id") or body.get("stack_id")
    assert stack_id, body
    assert sorted(body["picture_ids"]) == sorted([env["pic_a"], env["pic_b"]]), body
    return {
        "stack_id": stack_id,
        "pic_token": mint("picture", env["pic_a"]),
        # Grants a picture that is not a member of the stack, so the whole stack
        # is out of scope: the deny direction.
        "outsider_token": mint("picture", env["pic_b"] + 999),
    }


def test_integration_picture_scoped_via_set_token(env):
    """GET /pictures/{id}/metadata is PICTURE_SCOPED. A set-scoped token reaches its
    member picture (A) but is 403'd on a non-member (B) - real membership query."""
    anon, tok = env["anon"], env["tokens"]["set"]
    with _enforcing(env["server"]):
        r = anon.get(f"{API}/pictures/{env['pic_a']}/metadata", headers=_bearer(tok))
        assert r.status_code == 200, f"in-scope A should pass: {r.status_code} {r.text}"
        r = anon.get(f"{API}/pictures/{env['pic_b']}/metadata", headers=_bearer(tok))
        assert r.status_code == 403, (
            f"out-of-scope B must 403: {r.status_code} {r.text}"
        )
        # Owner still reads both.
        assert (
            env["owner"].get(f"{API}/pictures/{env['pic_b']}/metadata").status_code
            == 200
        )


def test_integration_picture_layout_read_is_picture_scoped(env):
    """v1.11 Phase 4b. ``GET /pictures/{id}/layout`` is PICTURE_SCOPED.

    Both directions on a real membership query, and the positive one is not
    decoration: over-blocking is its own regression, and the route answering
    "this library has no layout" for a picture the token legitimately reaches
    is the whole in-scope behaviour."""
    anon, tok = env["anon"], env["tokens"]["set"]
    with _enforcing(env["server"]):
        r = anon.get(f"{API}/pictures/{env['pic_a']}/layout", headers=_bearer(tok))
        assert r.status_code == 200, f"in-scope A should pass: {r.status_code} {r.text}"
        assert r.json()["suggested_folder"] is None, r.text
        r = anon.get(f"{API}/pictures/{env['pic_b']}/layout", headers=_bearer(tok))
        assert r.status_code == 403, (
            f"out-of-scope B must 403: {r.status_code} {r.text}"
        )


def test_integration_move_to_match_refuses_a_mixed_batch_whole(env):
    """v1.11 Phase 4b. ``POST /pictures/layout/move-to-match`` is PICTURE_SCOPED
    over ``body_ids``, so one out-of-scope id refuses the request and moves
    nothing - the ``POST /pictures/rotate`` contract (#950).

    A READ token is refused by the middleware before the gate (POST is not in
    ``READ_SAFE_POST_PATHS``), which is what makes "write-enabled" the operative
    condition; that refusal is the deny direction here. The owner's own call is
    the positive control: it reaches the handler and reports zero moved, because
    this library has no layout and therefore nothing to match."""
    anon, tok = env["anon"], env["tokens"]["set"]
    with _enforcing(env["server"]):
        r = anon.post(
            f"{API}/pictures/layout/move-to-match",
            json={"picture_ids": [env["pic_a"], env["pic_b"]]},
            headers=_bearer(tok),
        )
        assert r.status_code == 403, f"a READ token must not move files: {r.text}"

        r = env["owner"].post(
            f"{API}/pictures/layout/move-to-match",
            json={"picture_ids": [env["pic_a"], env["pic_b"]]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["moved_count"] == 0, r.text


def test_integration_picture_scoped_via_project_token(env):
    """Sibling vector: a project-scoped token reaches its member picture (A) and is
    403'd on a non-member (B)."""
    anon, tok = env["anon"], env["tokens"]["proj"]
    with _enforcing(env["server"]):
        r = anon.get(f"{API}/pictures/{env['pic_a']}/metadata", headers=_bearer(tok))
        assert r.status_code == 200, r.text
        r = anon.get(f"{API}/pictures/{env['pic_b']}/metadata", headers=_bearer(tok))
        assert r.status_code == 403, r.text


def test_integration_set_scoped_route_both_directions(env):
    """GET /picture_sets/{id} is SET_SCOPED. The token for set S reaches S; a token
    for a different set is 403'd on S."""
    anon = env["anon"]
    with _enforcing(env["server"]):
        r = anon.get(
            f"{API}/picture_sets/{env['set_id']}", headers=_bearer(env["tokens"]["set"])
        )
        assert r.status_code == 200, r.text
        r = anon.get(
            f"{API}/picture_sets/{env['set_id']}",
            headers=_bearer(env["tokens"]["set_other"]),
        )
        assert r.status_code == 403, r.text


def test_integration_character_scoped_route_both_directions(env):
    """GET /characters/{id} is CHARACTER_SCOPED. The token for character C reaches
    C; a token for a different character is 403'd on C."""
    anon = env["anon"]
    with _enforcing(env["server"]):
        r = anon.get(
            f"{API}/characters/{env['char_id']}",
            headers=_bearer(env["tokens"]["char"]),
        )
        assert r.status_code == 200, r.text
        r = anon.get(
            f"{API}/characters/{env['char_id']}",
            headers=_bearer(env["tokens"]["char_other"]),
        )
        assert r.status_code == 403, r.text


def test_integration_project_summary_unassigned_regression(env):
    """PROJECT_SCOPED /projects/{project_id}/summary: the project token reaches its
    own project's summary, but the aggregate ``UNASSIGNED`` id is denied - the
    historical aggregate-leak class, now failed closed at the gate."""
    anon, tok = env["anon"], env["tokens"]["proj"]
    with _enforcing(env["server"]):
        r = anon.get(f"{API}/projects/{env['proj_id']}/summary", headers=_bearer(tok))
        assert r.status_code == 200, f"own project summary should pass: {r.text}"
        r = anon.get(f"{API}/projects/UNASSIGNED/summary", headers=_bearer(tok))
        assert r.status_code == 403, f"UNASSIGNED aggregate must 403: {r.text}"


def test_integration_scoped_list_pass_through_not_overblocked(env, stacked_env):
    """The SCOPED_LIST historical-leak vectors (list, stream, ?character_id=
    UNASSIGNED, /stacks/{id}/pictures, /pictures/{id}/stack) stay reachable by a
    resource-scoped token when the gate is enforcing - scope_aware pass-through
    must not over-block; the inline filter (unchanged) remains the enforcement.

    Previously this asserted 200 against ``/stacks/{pic_id}/stack``, which is not a
    route: the SPA catch-all answered it with 200 and the assertion was vacuous.
    The two real stack routes are used now, and every path is checked against the
    mounted route table before it is requested, so a rename fails loudly here
    instead of dissolving into a fallback 200.
    """
    anon, tok = env["anon"], stacked_env["pic_token"]
    paths = (
        f"{API}/pictures",
        f"{API}/pictures/stream",
        f"{API}/pictures?character_id=UNASSIGNED",
        f"{API}/stacks/{stacked_env['stack_id']}/pictures",
        f"{API}/pictures/{env['pic_a']}/stack",
    )
    for path in paths:
        assert_real_route(env["server"].api, "GET", path.split("?")[0])
    with _enforcing(env["server"]):
        for path in paths:
            r = anon.get(path, headers=_bearer(tok))
            assert r.status_code == 200, (
                f"scope_aware SCOPED_LIST {path} must not be over-blocked by the "
                f"gate; got {r.status_code}: {r.text}"
            )


def test_integration_stack_pictures_does_not_leak_out_of_scope_members(
    env, stacked_env
):
    """``GET /stacks/{id}/pictures`` - one of the three historical whole-library
    BOLA leaks - filters stack members down to the caller's grant.

    Both directions. The stack holds A (granted to the picture token) and B (not):
    the scoped caller reaches the stack but must see A only, while the owner must
    still see both, because over-blocking is its own regression. A picture token
    for a stack non-member must not learn the stack exists at all.
    """
    anon, tok = env["anon"], stacked_env["pic_token"]
    path = f"{API}/stacks/{stacked_env['stack_id']}/pictures"
    assert_real_route(env["server"].api, "GET", path)
    with _enforcing(env["server"]):
        # In scope: reachable, but the out-of-scope sibling is filtered out.
        r = anon.get(path, headers=_bearer(tok))
        assert r.status_code == 200, f"in-scope stack read must pass: {r.text}"
        ids = {row["id"] for row in r.json()}
        assert ids == {env["pic_a"]}, (
            f"stack listing leaked out-of-scope members: expected only "
            f"{env['pic_a']}, got {sorted(ids)}"
        )

        # Not over-blocked: the owner still sees the whole stack.
        r = env["owner"].get(path)
        assert r.status_code == 200, r.text
        owner_ids = {row["id"] for row in r.json()}
        assert owner_ids == {env["pic_a"], env["pic_b"]}, sorted(owner_ids)

        # No member in scope at all => the stack must not be disclosed.
        r = anon.get(path, headers=_bearer(stacked_env["outsider_token"]))
        assert r.status_code == 404, (
            f"a token granting no stack member must not see the stack; "
            f"got {r.status_code}: {r.text}"
        )


def test_integration_picture_stack_does_not_leak_out_of_scope_membership(
    env, stacked_env
):
    """``GET /pictures/{picture_id}/stack`` is the sibling read of the same leak
    class: it must not tell an out-of-scope caller which stack a picture is in,
    nor name that stack's out-of-scope members.

    Both directions: A (granted) resolves to the stack but lists only A; B
    (ungranted) is refused outright; the owner still sees both members.
    """
    anon, tok = env["anon"], stacked_env["pic_token"]
    in_scope = f"{API}/pictures/{env['pic_a']}/stack"
    out_of_scope = f"{API}/pictures/{env['pic_b']}/stack"
    assert_real_route(env["server"].api, "GET", in_scope)
    with _enforcing(env["server"]):
        # In scope: resolves, but members outside the grant are stripped.
        r = anon.get(in_scope, headers=_bearer(tok))
        assert r.status_code == 200, f"in-scope picture's stack must resolve: {r.text}"
        body = r.json()
        assert body["id"] == stacked_env["stack_id"], body
        assert body["picture_ids"] == [env["pic_a"]], (
            f"stack members leaked outside the grant: {body['picture_ids']}"
        )

        # Out of scope: the caller must not learn B's stack membership.
        r = anon.get(out_of_scope, headers=_bearer(tok))
        assert r.status_code == 404, (
            f"out-of-scope picture's stack must be refused; got {r.status_code}: "
            f"{r.text}"
        )

        # Not over-blocked: the owner sees the full membership.
        r = env["owner"].get(in_scope)
        assert r.status_code == 200, r.text
        assert sorted(r.json()["picture_ids"]) == sorted([env["pic_a"], env["pic_b"]])


def test_integration_stack_detail_does_not_leak_out_of_scope_members(env, stacked_env):
    """``GET /stacks/{id}`` is the third route of the same ``_LIST_AWARE`` family
    and the same leak class; cover it rather than leave the sibling untested."""
    anon = env["anon"]
    path = f"{API}/stacks/{stacked_env['stack_id']}"
    assert_real_route(env["server"].api, "GET", path)
    with _enforcing(env["server"]):
        r = anon.get(path, headers=_bearer(stacked_env["pic_token"]))
        assert r.status_code == 200, f"in-scope stack detail must pass: {r.text}"
        assert r.json()["picture_ids"] == [env["pic_a"]], r.json()

        r = env["owner"].get(path)
        assert r.status_code == 200, r.text
        assert sorted(r.json()["picture_ids"]) == sorted([env["pic_a"], env["pic_b"]])

        r = anon.get(path, headers=_bearer(stacked_env["outsider_token"]))
        assert r.status_code == 404, (
            f"a token granting no stack member must not see the stack; "
            f"got {r.status_code}: {r.text}"
        )
