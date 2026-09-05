"""Step 3 of the centralised-authz refactor: the gate goes ENFORCING for the
non-id-resolution policy classes (``OWNER_ONLY`` / ``LOCAL_OWNER_ONLY`` /
``PUBLIC``-consistency).

This is the first step that actually DENIES, so every assertion here is
both-directional per CLAUDE.md / §16.1: an out-of-scope principal is 403'd AND an
in-scope principal still reaches the route (over-blocking - especially of the
demo's share tokens - is its own regression).

Toggle mechanism (principal ruling 2026-07-21): the shipped constant
``AUTHZ_GATE_ENFORCING`` stays ``False`` (report-only) through Step 5; the
owner-class enforcement *code* lands now and is proven by flipping the gate to
``enforcing=True`` in these tests. Per-policy-class staging is carried by which
branches are implemented - ``*_SCOPED`` / ``SCOPED_LIST`` / ``body_ids`` batch are
pass-through until Step 4, asserted below. Because the shipped default stays
report-only, none of this changes runtime behaviour until the Step-6 flip; the
inline handler checks remain the live enforcement in the meantime.

Named regressions required by the plan:
* §16.3 host-capability retarget (``owner_only`` -> ``local_owner_only``): a remote
  owner COOKIE session is newly constrained vs a loopback one.
* N2: reviews / tag_health flip - an unscoped-READ token (which the looser inline
  ``fetch_scope_allowed`` gate admits) is newly 403'd by ``OWNER_ONLY``.
* F-c rider: ``GET /users/me/auth`` tightened ``any_token`` -> ``owner_only``.
"""

import contextlib
import json
import os
import tempfile

import pytest
from fastapi import APIRouter, Depends, FastAPI
from starlette.testclient import TestClient

from pixlstash import auth
from pixlstash.authz.gate import AUTHZ_GATE_ENFORCING, AuthzGate
from pixlstash.authz.policy import AccessPolicy, RoutePolicy
from pixlstash.server import Server
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL can make a
# positive assertion vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")
_LOCALITY_403 = "restricted to local"  # substring of the LOCAL_OWNER_ONLY 403 detail
# The gate's owner-check 403 (AuthService.require_unscoped_owner). Distinct from
# the middleware's "Token is read-only", which is the point of asserting on it.
_OWNER_REQUIRED_403 = "Owner-level (full, unscoped) access required"


# ---------------------------------------------------------------------------
# Integration fixture: one real server, owner login, two token shapes.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _owner_env():
    """Build a real Server, log in the owner, mint an unscoped-READ and a
    resource-scoped-READ token.

    ``trusted_proxies=["testclient"]`` lets a test opt a single request into a
    spoofed *remote* client IP via ``X-Forwarded-For`` (the LOCAL_OWNER_ONLY
    locality regression); requests without that header keep the in-process
    ``testclient`` peer, which ``is_local_ip`` treats as local.
    """
    tmp = tempfile.TemporaryDirectory()
    cfg = os.path.join(tmp.name, "server-config.json")
    with open(cfg, "w") as fh:
        json.dump({"port": 8000, "trusted_proxies": ["testclient"]}, fh)
    server = Server(cfg)
    server.__enter__()
    try:
        client = TestClient(server.api, raise_server_exceptions=True)
        r = client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"{API}/users/me/token",
            json={"description": "global read", "scope": "READ"},
        )
        assert r.status_code == 200, r.text
        unscoped_read = r.json()["token"]

        # create_token does not require the resource to exist - a resource-scoped
        # READ token is enough to exercise the scoped-token branch.
        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "set share",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 1,
            },
        )
        assert r.status_code == 200, r.text
        scoped_read = r.json()["token"]

        # A cookie-less client for token-bearing requests: the auth middleware
        # prefers a cookie session over a Bearer token (auth.py:1623), so a
        # Bearer request on the logged-in owner client would authenticate as the
        # owner and never exercise the token scope. ``anon`` never logs in.
        anon = TestClient(server.api, raise_server_exceptions=True)

        yield {
            "server": server,
            "owner": client,
            "anon": anon,
            "unscoped_read": unscoped_read,
            "scoped_read": scoped_read,
        }
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


@contextlib.contextmanager
def _enforcing(server):
    """Flip the already-resolved live gate to enforcing for the block's duration.

    ``resolve_routes`` ran at boot (report-only), so the route-identity map is
    already built; only the ``enforcing`` flag changes. Restored on exit so the
    module-shared server is left report-only.
    """
    prev = server.authz._enforcing
    server.authz._enforcing = True
    try:
        yield
    finally:
        server.authz._enforcing = prev


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# OWNER_ONLY - N2 reviews flip (the gate, not the inline check, is the enforcer)
# ---------------------------------------------------------------------------


def test_owner_only_reviews_flip_both_directions():
    """GET /reviews is OWNER_ONLY. The inline ``fetch_scope_allowed`` gate ADMITS
    an unscoped-READ token (resource_type is None), so report-only lets it read;
    the enforcing gate's ``require_unscoped_owner`` newly 403s it - while the owner
    still reads (in-scope) and a resource-scoped token stays 403 in both modes."""
    with _owner_env() as env:
        server, owner, anon = env["server"], env["owner"], env["anon"]

        with _enforcing(server):
            # NEGATIVE: unscoped-READ is now denied by the gate.
            r = anon.get(f"{API}/reviews", headers=_bearer(env["unscoped_read"]))
            assert r.status_code == 403, (
                f"enforcing OWNER_ONLY must 403 an unscoped-READ token on /reviews, "
                f"got {r.status_code}: {r.text}"
            )
            # NEGATIVE sibling: a resource-scoped token is denied too.
            r = anon.get(f"{API}/reviews", headers=_bearer(env["scoped_read"]))
            assert r.status_code == 403, (
                f"enforcing OWNER_ONLY must 403 a resource-scoped token on /reviews, "
                f"got {r.status_code}: {r.text}"
            )
            # POSITIVE: the owner (cookie session) still reads - over-blocking the
            # owner would be its own regression.
            r = owner.get(f"{API}/reviews")
            assert r.status_code == 200, (
                f"enforcing OWNER_ONLY must NOT block the owner on /reviews, "
                f"got {r.status_code}: {r.text}"
            )


def test_owner_only_tag_health_flip():
    """N2 sibling: GET /tag_health has the same unscoped-READ admit-then-deny
    contrast - report-only 200, enforcing 403 - with the owner still allowed."""
    with _owner_env() as env:
        server, owner, anon = env["server"], env["owner"], env["anon"]

        with _enforcing(server):
            r = anon.get(f"{API}/tag_health", headers=_bearer(env["unscoped_read"]))
            assert r.status_code == 403, (
                f"enforcing OWNER_ONLY must 403 unscoped-READ on /tag_health, "
                f"got {r.status_code}: {r.text}"
            )
            r = owner.get(f"{API}/tag_health")
            assert r.status_code == 200, (
                f"owner must still reach /tag_health, got {r.status_code}: {r.text}"
            )


# ---------------------------------------------------------------------------
# F-c rider - GET /users/me/auth tightened any_token -> owner_only
# ---------------------------------------------------------------------------


def test_users_me_auth_tightened_to_owner_only():
    """The owner-account read (username + has_password) was any_token - reachable
    by a share token today. The F-c rider declares it OWNER_ONLY; the enforcing
    gate 403s both an unscoped-READ and a resource-scoped token while the owner
    still reads."""
    with _owner_env() as env:
        server, owner, anon = env["server"], env["owner"], env["anon"]

        with _enforcing(server):
            for label, tok in (
                ("unscoped", "unscoped_read"),
                ("scoped", "scoped_read"),
            ):
                r = anon.get(f"{API}/users/me/auth", headers=_bearer(env[tok]))
                assert r.status_code == 403, (
                    f"enforcing OWNER_ONLY must 403 a {label}-READ token on "
                    f"/users/me/auth, got {r.status_code}: {r.text}"
                )
            r = owner.get(f"{API}/users/me/auth")
            assert r.status_code == 200, (
                f"owner must still read /users/me/auth, got {r.status_code}: {r.text}"
            )


# ---------------------------------------------------------------------------
# LOCAL_OWNER_ONLY - §16.3 retarget: remote cookie session vs loopback
# ---------------------------------------------------------------------------


def _is_locality_403(resp) -> bool:
    return resp.status_code == 403 and _LOCALITY_403 in resp.text


def test_local_owner_only_remote_cookie_regression():
    """GET /filesystem/browse retargets owner_only -> local_owner_only. A remote
    owner COOKIE session reaches it today (report-only; the §16.3 gap the retarget
    closes) but is newly locality-403'd when enforcing, while a LOCAL owner cookie
    is not locality-blocked in either mode."""
    remote = {"X-Forwarded-For": "8.8.8.8"}  # spoofed via trusted testclient proxy
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]

        with _enforcing(server):
            # NEGATIVE: remote owner cookie is now denied on locality grounds.
            r = owner.get(f"{API}/filesystem/browse", headers=remote)
            assert _is_locality_403(r), (
                "enforcing LOCAL_OWNER_ONLY must locality-403 a remote owner "
                f"cookie; got {r.status_code}: {r.text}"
            )
            # POSITIVE: a LOCAL owner cookie (in-process peer) is NOT
            # locality-blocked - over-blocking the local owner is a regression.
            r = owner.get(f"{API}/filesystem/browse")
            assert not _is_locality_403(r), (
                "enforcing LOCAL_OWNER_ONLY must NOT locality-403 a LOCAL owner "
                f"cookie; got {r.status_code}: {r.text}"
            )


def test_local_owner_only_get_refused_at_the_gate(monkeypatch):
    """#831: the GATE, not the middleware's path list, must refuse a READ token
    on a locality-tier GET.

    Every untemplated locality-tier GET sits in ``READ_BLOCKED_GET_PATHS`` - a
    derivation, not a list, enforced by
    ``tests/test_authz_host_capability_16_3.py::test_every_untemplated_locality_get_is_on_the_read_blocked_belt``
    - so the
    middleware answers a share token before routing and the gate's
    ``_enforce_unscoped_owner`` on the ``LOCAL_OWNER_ONLY`` branch never runs -
    correct, load-bearing, and completely unobservable, which is why deleting it
    left the whole suite green. Emptying that frozenset is exactly the shape of a
    new locality GET added without its entry: the token now reaches the gate, and
    the gate must still refuse it.

    The 403 detail is asserted, not just the status: ``Owner-level (full,
    unscoped) access required`` comes from the gate's
    ``AuthService.require_unscoped_owner`` delegation, whereas the middleware's
    refusal reads ``Token is read-only``. Matching the former proves the denial
    came from the gate and not from the layer we just removed.

    Both token shapes are checked - a resource-scoped share token and an unscoped
    READ token - because ``require_unscoped_owner`` refuses both, and the
    positive direction (a local owner cookie still browses) guards against
    over-blocking.
    """
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
    with _owner_env() as env:
        with _enforcing(env["server"]):
            for label in ("scoped_read", "unscoped_read"):
                r = env["anon"].get(
                    f"{API}/filesystem/browse", headers=_bearer(env[label])
                )
                assert r.status_code == 403 and _OWNER_REQUIRED_403 in r.text, (
                    "the gate must refuse a READ token on a LOCAL_OWNER_ONLY GET "
                    f"with no READ_BLOCKED_GET_PATHS entry ({label}); got "
                    f"{r.status_code}: {r.text}"
                )

            # POSITIVE: a local owner cookie still browses - over-blocking the
            # owner is its own regression.
            r = env["owner"].get(f"{API}/filesystem/browse")
            assert r.status_code == 200, (
                "the local owner must still reach GET /filesystem/browse; got "
                f"{r.status_code}: {r.text}"
            )


# ---------------------------------------------------------------------------
# PUBLIC still passes when enforcing (over-blocking a public probe is a regression)
# ---------------------------------------------------------------------------


def test_public_route_still_reachable_when_enforcing():
    """A PUBLIC, auth-excluded probe (GET /version) stays 200 under the enforcing
    gate - PUBLIC-consistency must not turn a genuine public route into a 403."""
    with _owner_env() as env:
        server = env["server"]
        with _enforcing(server):
            r = env["owner"].get("/version")
            assert r.status_code == 200, (
                f"enforcing gate must not block a PUBLIC route; got {r.status_code}"
            )


# ---------------------------------------------------------------------------
# Step-3 SCOPE BOUNDARY: *_SCOPED / SCOPED_LIST stay pass-through (no over-reach)
# ---------------------------------------------------------------------------


def _build_scoped_decoy_app(gate):
    router = APIRouter()

    @router.get("/thing/{id}")
    async def _thing(id: int):
        return {"ok": "scoped", "id": id}

    @router.get("/things")
    async def _things():
        return {"ok": "list"}

    app = FastAPI()
    app.include_router(
        router, prefix="/api/v1/step3-scoped-decoy", dependencies=[Depends(gate)]
    )
    return app


def test_scoped_and_list_policies_not_enforced_at_step3():
    """A SET_SCOPED single-object route and a SCOPED_LIST route are declared, and
    the gate is ENFORCING - but Step 3 leaves the id-resolving classes as
    pass-through (their inline checks are the live enforcement until Step 4). The
    gate must NOT deny them here; enforcing scoped classes at Step 3 would be
    over-reach. ``auth=None`` is deliberate: a scoped class must not touch auth."""
    registry = {
        ("GET", "/api/v1/step3-scoped-decoy/thing/{id}"): RoutePolicy(
            AccessPolicy.SET_SCOPED, id_param="id"
        ),
        ("GET", "/api/v1/step3-scoped-decoy/things"): RoutePolicy(
            AccessPolicy.SCOPED_LIST
        ),
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None)
    app = _build_scoped_decoy_app(gate)
    gate.resolve_routes(app)

    client = TestClient(app)
    assert client.get("/api/v1/step3-scoped-decoy/thing/5").status_code == 200, (
        "Step 3 must not enforce a *_SCOPED route (that is Step 4) - pass-through"
    )
    assert client.get("/api/v1/step3-scoped-decoy/things").status_code == 200, (
        "Step 3 must not enforce a SCOPED_LIST route (that is Step 4) - pass-through"
    )


# ---------------------------------------------------------------------------
# Construction gap: enforcing + owner-class route + no auth => boot failure
# ---------------------------------------------------------------------------


def _build_owner_decoy_app(gate):
    router = APIRouter()

    @router.get("/owned")
    async def _owned():
        return {"ok": "owned"}

    app = FastAPI()
    app.include_router(
        router, prefix="/api/v1/step3-owner-decoy", dependencies=[Depends(gate)]
    )
    return app


def test_owner_class_without_auth_boot_fails_when_enforcing():
    """An enforcing gate whose registry declares an owner-class route but was given
    no AuthService must FAIL BOOT - a skipped owner check is the BOLA-by-omission
    class this refactor exists to kill, never a silent pass-through."""
    import pytest

    registry = {
        ("GET", "/api/v1/step3-owner-decoy/owned"): RoutePolicy(AccessPolicy.OWNER_ONLY)
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None)
    app = _build_owner_decoy_app(gate)

    with pytest.raises(RuntimeError, match="no AuthService was injected"):
        gate.enforce_startup(app)


# ---------------------------------------------------------------------------
# PUBLIC-consistency: drift boot-fails when enforcing; SPA catch-all is exempt
# ---------------------------------------------------------------------------


def test_public_consistency_flags_non_excluded_public_route():
    """A route declared PUBLIC that the middleware does NOT auth-exclude is drift:
    report-only lists it, enforcing boot-fails."""
    import pytest

    registry = {
        ("GET", "/api/v1/step3-public-decoy/open"): RoutePolicy(
            AccessPolicy.PUBLIC, justification="decoy public but not auth-excluded"
        )
    }
    router = APIRouter()

    @router.get("/open")
    async def _open():
        return {"ok": "open"}

    app = FastAPI()
    report_gate = AuthzGate(registry=registry, enforcing=False, auth=None)
    app.include_router(
        router, prefix="/api/v1/step3-public-decoy", dependencies=[Depends(report_gate)]
    )

    assert report_gate._public_consistency_problems(), (
        "a PUBLIC route not in AUTH_EXCLUDED_* must be flagged as drift"
    )
    # Report-only: logs drift, does not raise.
    report_gate.enforce_startup(app)

    # Enforcing: the same drift is a boot failure.
    enforce_gate = AuthzGate(registry=registry, enforcing=True, auth=None)
    app2 = FastAPI()
    router2 = APIRouter()

    @router2.get("/open")
    async def _open2():
        return {"ok": "open"}

    app2.include_router(
        router2,
        prefix="/api/v1/step3-public-decoy",
        dependencies=[Depends(enforce_gate)],
    )
    with pytest.raises(RuntimeError, match="PUBLIC-consistency"):
        enforce_gate.enforce_startup(app2)


def test_public_consistency_exempts_spa_catchall():
    """The SPA catch-all (/{full_path:path}) is legitimately PUBLIC but can never be
    a static AUTH_EXCLUDED_* entry (matrix §N1); it must be exempt from the
    consistency check so a correct declaration does not boot-fail."""
    registry = {
        ("GET", "/{full_path:path}"): RoutePolicy(
            AccessPolicy.PUBLIC, justification="SPA fallback; matrix N1 exempt"
        )
    }
    gate = AuthzGate(registry=registry, enforcing=True, auth=None)
    assert gate._public_consistency_problems() == [], (
        "the SPA catch-all must be exempt from the PUBLIC-consistency check"
    )


# ---------------------------------------------------------------------------
# The shipped rollback constant is untouched (plan §6)
# ---------------------------------------------------------------------------


def test_shipped_default_is_enforcing():
    """Step 6 flipped the single-boolean switch: the gate now ships ENFORCING.
    Report-only remains the one-line rollback (flip the constant back to False)."""
    assert AUTHZ_GATE_ENFORCING is True, (
        "Step 6 ships AUTHZ_GATE_ENFORCING=True (enforcement live); report-only is "
        "reachable as the one-line rollback (plan §6) - flip the constant to False."
    )
