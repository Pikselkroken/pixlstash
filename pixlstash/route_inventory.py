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

import fastapi
import fastapi.dependencies.utils
import fastapi.routing
from fastapi.routing import APIWebSocketRoute
from starlette.routing import WebSocketRoute

# Fail loud if the FastAPI internal we depend on to flatten lazily-included
# routers ever disappears. Without this, a version bump that renamed/removed the
# helper would make enumeration fall back to under-counting and report false
# "complete coverage" — the exact silent failure a security matrix must never
# have. See the module docstring and the principal-engineer decision memo.
#
# Resolved with getattr rather than imported by name ON PURPOSE. A plain
# ``from fastapi.routing import iter_route_contexts`` raises ImportError on the
# import line itself, so this message — the whole point of the guard — could
# never be reached. That is exactly how it failed: a workstation installed from
# requirements.txt (then pinned at fastapi 0.135.1, three minors below the floor)
# got a bare "cannot import name 'iter_route_contexts'" with no hint of the
# cause. Keep the guard reachable.
iter_route_contexts = getattr(fastapi.routing, "iter_route_contexts", None)
if iter_route_contexts is None:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        "fastapi.routing.iter_route_contexts is missing (installed FastAPI "
        f"{getattr(fastapi, '__version__', 'unknown')}). PixlStash requires "
        "fastapi>=0.138.0, which is where that helper first appears; below it "
        "the route inventory cannot enumerate lazily-included routers. Upgrade "
        "with `pip install -U 'fastapi>=0.138.0'`. If this is a NEWER FastAPI "
        "that renamed or removed the helper, fix pixlstash/route_inventory.py "
        "before trusting any route-coverage claim — see "
        "docs/backend_architecture.md §16.2."
    )

# Flattens a route's dependency tree so parameters contributed by nested
# ``Depends(...)`` are enumerated alongside the handler's own. Resolved with
# getattr for the same reason as ``iter_route_contexts`` above: a bare import
# would raise on the import line and this message — the point of the guard —
# could never be reached. Used by :func:`iter_api_query_params`, on which the
# ``project_id``-filter coverage guardrail rests (§16.6); a silent fallback to
# "no query parameters found" would report false completeness.
get_flat_dependant = getattr(fastapi.dependencies.utils, "get_flat_dependant", None)
if get_flat_dependant is None:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        "fastapi.dependencies.utils.get_flat_dependant is missing (installed "
        f"FastAPI {getattr(fastapi, '__version__', 'unknown')}). The route "
        "inventory needs it to enumerate every declared query parameter, which "
        "is what makes the project-filter coverage check (§16.6) arithmetic "
        "rather than a human-remembered rule. Fix pixlstash/route_inventory.py "
        "before trusting any parameter-coverage claim."
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
QueryParam = tuple[str, str, str]  # (method, path_template, query_param_name)


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


def iter_api_query_params(app) -> Iterator[QueryParam]:
    """Yield ``(method, path_template, name)`` for every declared query parameter.

    The name is the wire name (the ``alias`` when the handler declares one),
    because that is what arrives in ``request.query_params`` and therefore what
    ``authz.membership.enforce_project_filter_scope`` matches on. Parameters
    contributed by nested ``Depends(...)`` are included: the dependency tree is
    flattened with FastAPI's own ``get_flat_dependant``, so a filter parameter
    hidden one level down in a shared dependency is still enumerated.

    Routes without a ``dependant`` (FastAPI's own ``/docs`` and
    ``/openapi.json``, which are plain Starlette routes) declare no parameters
    and are skipped. Shares the walk in :func:`iter_api_route_contexts`, so this
    and the endpoint inventory can never disagree about which routes exist.
    """
    for method, path, route in iter_api_route_contexts(app):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        flat = get_flat_dependant(dependant, skip_repeats=True)
        for field in flat.query_params:
            yield (method, path, getattr(field, "alias", None) or field.name)


def api_query_param_names(app) -> set[str]:
    """Return the distinct query-parameter names declared anywhere on ``app``."""
    return {name for _method, _path, name in iter_api_query_params(app)}


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
