"""Route inventory: the ground-truth enumeration of mounted API endpoints.

This module is the single, reusable source of truth for "which endpoints does
the built app actually expose". Phase 1 of the backend refactor (the
``authz/`` registry + gate startup assertion) and the CI guardrail in
``tests/test_architecture_guardrails.py`` both consume this enumeration so the
coverage matrix is arithmetic, not judgement — see
``docs/backend_architecture.md`` §16.2 and the backend refactor plan §3.4.

It is deliberately dependency-free (no ``authz`` import, no ``Server`` import):
it takes an already-built ASGI app and reflects over its routes.

**Why this is not a flat ``app.routes`` walk.** The refactor plan §3.4 assumed
``include_router`` flattens routes into ``app.routes``. Under the installed
FastAPI (0.138.x) that is *false*: ``include_router`` leaves a lazy
``_IncludedRouter`` placeholder in ``app.routes`` and resolves the real routes
on demand. A naive ``isinstance(route, starlette.routing.Route)`` walk therefore
finds only the ~14 app-level routes and silently misses every router-module
endpoint — a false-"coverage" trap for a security matrix. We instead flatten
through FastAPI's own resolver, :func:`fastapi.routing.iter_route_contexts`
(the same helper that powers ``/openapi.json`` generation), which yields the
effective, prefix-resolved ``(method, path)`` for every mounted route. The
capability assertion below fails loud if a future FastAPI removes/renames that
helper, so the enumeration can never silently degrade.

WebSocket routes are enumerated separately (:func:`iter_websocket_endpoints`):
the HTTP authz gate does not cover them; ``authenticate_websocket`` is their
chokepoint (plan §6). Keeping them out of the HTTP endpoint set prevents a false
sense of coverage. Note: an *included* WS route's effective path is not resolved
by ``iter_route_contexts`` (it returns ``""``); we fall back to the route's own
declared path, so the effective prefix may be missing for included WS routes.
This is acceptable because WS routes are acknowledged, not gated, in Phase 1.
"""

from collections.abc import Iterator

import fastapi.routing
from fastapi.routing import APIWebSocketRoute, iter_route_contexts
from starlette.routing import WebSocketRoute

# Fail loud if the FastAPI internal we depend on to flatten lazily-included
# routers ever disappears. Without this, a version bump that renamed/removed the
# helper would make enumeration fall back to under-counting and report false
# "complete coverage" — the exact silent failure a security matrix must never
# have. See the module docstring and the principal-engineer decision memo.
assert hasattr(fastapi.routing, "iter_route_contexts"), (
    "fastapi.routing.iter_route_contexts is missing — the route-inventory "
    "enumeration mechanism has broken (FastAPI upgrade?). Fix pixlstash/"
    "route_inventory.py before trusting any route-coverage claim. See "
    "docs/backend_architecture.md §16.2."
)

# HTTP methods FastAPI/Starlette add automatically for a declared handler. They
# are not endpoints an author declares a policy for, so the inventory omits them
# to keep ``(method, path)`` pairs aligned with the coverage matrix's cells.
AUTO_METHODS = frozenset({"HEAD", "OPTIONS"})

# Module prefix identifying an endpoint that comes from a mounted route module
# (as opposed to app-level routes in ``pixlstash.server`` or FastAPI internals
# such as ``/docs``). Used by :func:`route_module_names` for the router-coverage
# cross-check that catches a whole router silently vanishing.
ROUTE_MODULE_PREFIX = "pixlstash.routes."

Endpoint = tuple[str, str]  # (method, path_template), e.g. ("GET", "/pictures/{id}")
RouteContext = tuple[str, str, object]  # (method, path_template, original_route)
WebSocketEndpoint = tuple[str, str]  # (name, path_template)


def iter_api_route_contexts(app) -> Iterator[RouteContext]:
    """Yield ``(method, path_template, original_route)`` for every HTTP endpoint.

    This is the identity-preserving form of :func:`iter_api_endpoints`: it adds
    the persistent ``original_route`` object alongside the effective
    (prefix-resolved) ``(method, path)``. The authz gate
    (``pixlstash/authz/gate.py``) keys its policy map by that route object's
    IDENTITY (``id(route)``) rather than the prefix-stripped path string exposed at
    request time via ``request.scope["route"].path`` (which diverges from the
    effective path on the vast majority of routes and would fail *open*). Both
    this and :func:`iter_api_endpoints` share the SAME walk and filtering, so the
    gate's identity map and the CI coverage matrix can never disagree about which
    endpoints exist. See ``docs/backend_architecture.md`` §16.2 and the backend
    refactor plan §3.3 / §3.4.

    One tuple is produced per (method, path) pair — a route serving both GET and
    POST yields two contexts sharing the same route object. Auto-added
    HEAD/OPTIONS methods are excluded (see ``AUTO_METHODS``); WebSocket and Mount
    routes carry no HTTP methods and are naturally skipped.
    """
    for ctx in iter_route_contexts(app.routes):
        if isinstance(ctx.original_route, (WebSocketRoute, APIWebSocketRoute)):
            continue
        methods = ctx.methods or set()
        for method in methods:
            if method in AUTO_METHODS:
                continue
            yield (method, ctx.path, ctx.original_route)


def iter_api_endpoints(app) -> Iterator[Endpoint]:
    """Yield every ``(method, path_template)`` HTTP endpoint mounted on ``app``.

    One tuple is produced per (method, path) pair — a route that serves both GET
    and POST yields two endpoints, matching the registry's ``(method, path)``
    keying. Paths are effective (prefix-resolved), e.g.
    ``/api/v1/pictures/{picture_id}/thumbnail``. Auto-added HEAD/OPTIONS methods
    are excluded (see ``AUTO_METHODS``). WebSocket and Mount (static-file)
    routes carry no HTTP methods and are naturally skipped; use
    :func:`iter_websocket_endpoints` for WebSockets. Shares the single walk in
    :func:`iter_api_route_contexts` so this and the authz gate agree exactly.
    """
    for method, path, _route in iter_api_route_contexts(app):
        yield (method, path)


def api_endpoint_set(app) -> set[Endpoint]:
    """Return the set of ``(method, path_template)`` HTTP endpoints on ``app``."""
    return set(iter_api_endpoints(app))


def route_module_names(app) -> set[str]:
    """Return the distinct route-module names (``pixlstash.routes.*``) mounted.

    Each mounted route module contributes at least one endpoint whose handler
    lives under ``pixlstash.routes.``. Comparing this count against the expected
    number of mounted route modules is the decisive cross-check that a whole
    router has not silently disappeared behind a FastAPI internal change — it is
    independent of any hardcoded endpoint total. App-level routes
    (``pixlstash.server``) and FastAPI internals (``/docs`` etc.) are excluded.
    """
    modules: set[str] = set()
    for ctx in iter_route_contexts(app.routes):
        endpoint = getattr(ctx.original_route, "endpoint", None)
        module = getattr(endpoint, "__module__", None)
        if module and module.startswith(ROUTE_MODULE_PREFIX):
            modules.add(module)
    return modules


def iter_websocket_endpoints(app) -> Iterator[WebSocketEndpoint]:
    """Yield ``(name, path_template)`` for every WebSocket route on ``app``.

    WebSockets are outside the HTTP authz gate's scope; they are enumerated only
    so the coverage matrix can acknowledge them explicitly rather than silently
    imply they are gated (plan §6 — the WS chokepoint is
    ``authenticate_websocket``). Both app-level and *included* WS routes are
    found. For included WS routes ``iter_route_contexts`` does not resolve the
    effective prefixed path (yields ``""``); we fall back to the route's own
    declared path, so the returned template may lack the API prefix.
    """
    for ctx in iter_route_contexts(app.routes):
        route = ctx.original_route
        if not isinstance(route, (WebSocketRoute, APIWebSocketRoute)):
            continue
        path = ctx.path or getattr(route, "path", "") or ""
        name = getattr(route, "name", None) or "<unnamed>"
        yield (name, path)


def websocket_endpoint_set(app) -> set[WebSocketEndpoint]:
    """Return the set of ``(name, path_template)`` WebSocket routes on ``app``."""
    return set(iter_websocket_endpoints(app))
