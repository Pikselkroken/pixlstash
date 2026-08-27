"""The centralised authorization gate - one router-level dependency.

Phase 1 of the backend authorization refactor (``docs/backend_architecture.md``
§16.2, backend refactor plan §3.3 / §3.4 / §3.5 step 1). :class:`AuthzGate` is
attached once to every ``include_router`` call in ``pixlstash/server.py`` and runs
after authentication (the auth middleware has already populated
``request.state``). It looks up the policy for the matched route and, on a miss,
denies by default.

**Route-identity keying (CSO-required).** The gate keys its policy map by the
persistent ``original_route`` object captured from
:func:`pixlstash.route_inventory.iter_api_route_contexts` - the *same* walk the CI
coverage matrix uses - NOT by ``request.scope["route"].path``. That request-time
path is prefix-stripped (``/pictures/{id}/metadata``) and diverges from the
enumerated effective path (``/api/v1/pictures/{id}/metadata``) on the vast
majority of routes, so string keying would fail to match ~93% of routes and
fail *open*. At request time ``request.scope["route"]`` is the very same route
object the enumeration yielded (verified: dependency-time identity matches
enumeration identity), so ``id(route)`` is a stable, correct key. A request-time
route object not present in the map resolves to **deny**, never allow.

**Enforcing shipped state (``AUTHZ_GATE_ENFORCING = True``; Step 6 flipped
2026-07-21).** The gate is the sole object-authorization chokepoint: an undeclared
route is denied at runtime, the startup enumeration fails closed on the backlog,
and the redundant inline handler checks were removed in Step 5. The single boolean
is the per-release rollback switch of plan §6 - a code constant, not runtime
config - held ``False`` through Steps 3-5 (report-only) and flipped fail-closed at
Step 6 under the adversarial sign-off; set it back to ``False`` to revert both
object-enforcement and unknown-route fail-closed in one line.

**Step-3 owner-class enforcement (behind the flag; principal ruling 2026-07-21).**
The enforcement *code* for the non-id-resolution classes lands now:
``OWNER_ONLY`` / ``LOCAL_OWNER_ONLY`` / ``LOOPBACK_OWNER_ONLY`` delegate to the
existing ``AuthService`` helpers (``require_unscoped_owner`` + ``real_client_ip``
with the scoped ``is_local_or_tailscale_ip`` / strict ``is_loopback_ip``
predicates), and a
startup ``PUBLIC``-consistency check reconciles ``PUBLIC`` declarations against
the middleware's ``AUTH_EXCLUDED_*``. Per-policy-class staging is carried by
*which branches are implemented* - there is deliberately no second toggle. It is
proven now by ``AuthzGate(enforcing=True)`` tests; the id-resolving classes
(``*_SCOPED`` / ``SCOPED_LIST`` / ``body_ids`` batch) stay pass-through until
Step 4. Because the shipped default stays report-only, none of this changes
runtime behaviour until the Step-6 flip - no window is weaker *or* stronger than
today (the inline checks still run).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

from pixlstash.auth import (
    is_auth_excluded_path,
    is_local_or_tailscale_ip,
    is_loopback_ip,
)
from pixlstash.authz.membership import (
    ID_RESOLVERS,
    enforce_character_scope,
    enforce_picture_scope,
    enforce_project_filter_scope,
    enforce_project_scope,
    enforce_set_scope,
)
from pixlstash.authz.policy import (
    SCOPED_POLICIES,
    AccessPolicy,
    RoutePolicy,
    LibraryAccessMode,
    validate_policy_declarations,
)
from pixlstash.authz.registry import CONDITIONALLY_MOUNTED_ROUTES, ROUTE_POLICIES
from pixlstash.route_inventory import iter_api_route_contexts

if TYPE_CHECKING:
    from pixlstash.auth import AuthService

logger = logging.getLogger(__name__)

# Membership check for each object-scoped policy class (Step 4). Each is a
# blocking DB read via ``server.vault.db.run_immediate_read_task``; the gate runs
# them on a threadpool worker (:func:`run_in_threadpool`) so the event loop is
# never blocked (principal ruling 2026-07-21 D1). ``token_scope is None`` (owner)
# is handled inside each function - it returns immediately.
_MEMBERSHIP_BY_POLICY = {
    AccessPolicy.PICTURE_SCOPED: enforce_picture_scope,
    AccessPolicy.SET_SCOPED: enforce_set_scope,
    AccessPolicy.CHARACTER_SCOPED: enforce_character_scope,
    AccessPolicy.PROJECT_SCOPED: enforce_project_scope,
}


def _is_resource_scoped(request: Request) -> bool:
    """Return True for a *resource-scoped* share token (the only principal an
    object check can narrow).

    An owner (``token_scope is None``) and an unscoped-READ token
    (``resource_type is None``) both have unrestricted object access today - the
    membership ladder returns "no restriction" for both (``filter_helpers`` and
    the ``enforce_*`` functions). The gate's object enforcement therefore only
    engages a token that names a specific resource; anything looser passes exactly
    as it does now.
    """
    token_scope = getattr(request.state, "token_scope", None)
    return token_scope is not None and token_scope.resource_type is not None


# The owner-class policies the gate resolves WITHOUT resolving a per-object
# resource id. Step 3 makes the gate enforcing for exactly these (plus the
# PUBLIC-consistency startup check); the id-resolving classes
# (``SCOPED_POLICIES`` + ``SCOPED_LIST`` + ``body_ids`` batch) stay pass-through
# until Step 4. Enforcement of an owner class delegates to the existing
# ``AuthService`` helpers (plan §3.3 item 4 - the ``token_scope`` ladder is NOT
# reimplemented here), so a route declaring one of these needs the ``auth``
# service injected; a missing service while enforcing is a boot failure, never a
# silently-skipped check.
OWNER_CLASS_POLICIES = frozenset(
    {
        AccessPolicy.OWNER_ONLY,
        AccessPolicy.LOCAL_OWNER_ONLY,
        AccessPolicy.LOOPBACK_OWNER_ONLY,
    }
)

# The SPA catch-all can never be a static ``AUTH_EXCLUDED_*`` entry (it is a
# path-template, not a literal path/prefix), yet it is legitimately PUBLIC - it
# serves the static shell/assets and returns no owner data (matrix §N1). Exempt
# it from the PUBLIC-consistency check so a correct declaration does not boot-fail.
_PUBLIC_CONSISTENCY_EXEMPT_PATHS = frozenset({"/{full_path:path}"})

# Master rollback switch (plan §6). A CODE CONSTANT flipped per release, NOT
# runtime config. FALSE == report-only: the gate logs undeclared routes and the
# startup enumeration prints the backlog, but nothing is denied and boot never
# fails on the backlog. TRUE == fail-closed: an undeclared route is 403 at
# request time and a boot failure at startup. Phase 1 Step 1 ships FALSE; the
# enforcing steps (3-6) flip it on.
#
# STEP 6 (2026-07-21): flipped to True. Enforcement is LIVE - the gate is now the
# sole object-authorization chokepoint; the redundant inline handler checks were
# removed in Step 5. Flip this single constant back to False to revert the entire
# object-enforcement + fail-closed behaviour in one line (the plan §6 rollback).
AUTHZ_GATE_ENFORCING = True

# Path-template parameter extractor: ``{picture_id}`` and ``{path:path}`` -> the
# bare name. Used to validate that a ``*_SCOPED`` declaration's ``id_param``
# actually exists in its route template.
_TEMPLATE_PARAM_RE = re.compile(r"{([^}:]+)(?::[^}]+)?}")


def _template_params(path: str) -> set[str]:
    """Return the set of path-parameter names in a route template."""
    return set(_TEMPLATE_PARAM_RE.findall(path))


class AuthzGate:
    """Router-level dependency plus startup enumeration for route authorization.

    A single instance is shared across all routers; it is per-request stateless
    (it reads only ``request.scope`` / ``request.state``). Construct it, mount it
    as a dependency on every router, then call :meth:`enforce_startup` once after
    all routers are mounted to build the identity-keyed policy map and report (or,
    when enforcing, fail-close on) the undeclared-route backlog.
    """

    def __init__(
        self,
        *,
        registry: dict[tuple[str, str], RoutePolicy] | None = None,
        enforcing: bool = AUTHZ_GATE_ENFORCING,
        auth: "AuthService | None" = None,
        server=None,
    ) -> None:
        """Initialise the gate.

        Args:
            registry: The declaration table to enforce. Defaults to the shared
                ``ROUTE_POLICIES``; an explicit table is injected by tests.
            enforcing: Whether misses fail closed (403 / boot failure) or are
                report-only. Defaults to the ``AUTHZ_GATE_ENFORCING`` constant.
            auth: The :class:`~pixlstash.auth.AuthService`. Required to enforce an
                owner-class policy (``OWNER_ONLY`` / ``LOCAL_OWNER_ONLY``), whose
                enforcement delegates to ``require_unscoped_owner`` /
                ``real_client_ip`` rather than reimplementing the scope ladder
                (plan §3.3 item 4). May be ``None`` when the gate is report-only
                or the registry declares no owner-class route (e.g. the PUBLIC-only
                decoy tests); an enforcing gate with owner-class routes but no
                ``auth`` is a boot failure (a skipped owner check is the
                BOLA-by-omission class this refactor exists to kill).
            server: The application ``Server`` (holds ``vault.db``). Required to
                enforce an object-scoped policy (``*_SCOPED`` with a resolvable id
                / ``body_ids`` batch), whose membership check reads the database
                via ``server.vault.db.run_immediate_read_task`` (Step 4). May be
                ``None`` when the gate is report-only or declares no DB-scoped
                route (e.g. owner/list/PUBLIC-only decoy tests); an enforcing gate
                with a DB-scoped route but no ``server`` is a boot failure - the
                object check must never be silently skipped.
        """
        self._registry = registry if registry is not None else ROUTE_POLICIES
        self._enforcing = enforcing
        self._auth = auth
        self._server = server
        self._policy_by_route_id: dict[int, RoutePolicy] = {}
        self._logged_misses: set[int] = set()
        self._resolved = False

    @property
    def enforcing(self) -> bool:
        """Whether the gate fails closed (True) or is report-only (False)."""
        return self._enforcing

    @property
    def resolved(self) -> bool:
        """Whether the route-identity policy map has been built yet."""
        return self._resolved

    def resolve_routes(
        self, app
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Build the ``id(route) -> RoutePolicy`` map from the shared route walk.

        Consumes :func:`iter_api_route_contexts` - the same enumeration the CI
        coverage matrix uses - so the gate's map and the matrix can never disagree
        about which endpoints exist. Building the map does not deny or raise; it is
        safe to call even when ``enforcing`` is True (the enforcing boot check is
        in :meth:`enforce_startup`).

        Returns:
            ``(undeclared, dead)``: ``undeclared`` is the sorted list of live
            ``(method, path)`` pairs with no registry entry (the backlog);
            ``dead`` is the sorted list of registry keys with no live route.
        """
        live: dict[tuple[str, str], object] = {}
        for method, path, route in iter_api_route_contexts(app):
            live[(method, path)] = route

        mapping: dict[int, RoutePolicy] = {}
        for key, route in live.items():
            route_policy = self._registry.get(key)
            if route_policy is not None:
                mapping[id(route)] = route_policy
        self._policy_by_route_id = mapping
        self._resolved = True

        undeclared = sorted(key for key in live if key not in self._registry)
        # A conditionally-mounted route (see CONDITIONALLY_MOUNTED_ROUTES) is
        # absent in the normal configuration by design, so its declaration is not
        # rot. Absence is waived; coverage is not - when the route IS mounted it
        # is resolved and enforced exactly like any other, and an undeclared
        # route is still denied and still aborts boot.
        dead = sorted(
            key
            for key in self._registry
            if key not in live and key not in CONDITIONALLY_MOUNTED_ROUTES
        )
        return undeclared, dead

    def enforce_startup(self, app) -> None:
        """Build the route map, report the backlog, and fail-close when enforcing.

        Always fatal (independent of the report-only flag): registry-authoring
        errors - a ``PUBLIC``/``LOCAL_OWNER_ONLY`` entry missing its justification,
        or a ``*_SCOPED`` ``id_param`` absent from its template - abort boot,
        because they are mistakes in the declaration table itself.

        Report-only vs. enforcing: an *undeclared route* (or a *dead declaration*)
        is logged as a backlog when ``enforcing`` is False, and aborts boot when
        ``enforcing`` is True. Step 1 ships report-only, so all 207 routes log as
        backlog and boot proceeds.
        """
        undeclared, dead = self.resolve_routes(app)
        authoring_problems = validate_policy_declarations(self._registry)
        authoring_problems += self._scoped_id_param_problems(app)
        authoring_problems += self._id_resolver_problems()
        public_drift = self._public_consistency_problems()

        if undeclared:
            logger.warning(
                "[authz-gate] %d mounted route(s) are UNDECLARED in the authz "
                "registry (report-only backlog; Phase 1 declaration back-fill "
                "pending):\n%s",
                len(undeclared),
                "\n".join(f"  {method} {path}" for method, path in undeclared),
            )
        if dead:
            logger.warning(
                "[authz-gate] %d authz registry declaration(s) match no mounted "
                "route (dead declarations - prune or fix the path):\n%s",
                len(dead),
                "\n".join(f"  {method} {path}" for method, path in dead),
            )
        if public_drift:
            logger.warning(
                "[authz-gate] %d PUBLIC declaration(s) are NOT auth-excluded in "
                "the middleware (AUTH_EXCLUDED_*) - the two lists have drifted; a "
                "PUBLIC route the middleware still authenticates is a "
                "mis-declaration (boot-fails when enforcing):\n%s",
                len(public_drift),
                "\n".join(f"  {problem}" for problem in public_drift),
            )

        # Registry-authoring errors are ALWAYS fatal (independent of the
        # report-only flag): they are mistakes in the declaration table itself,
        # provable without runtime config.
        if authoring_problems:
            raise RuntimeError(
                "authz registry declaration error(s) - fix the declaration "
                "table:\n" + "\n".join(f"  {problem}" for problem in authoring_problems)
            )

        if self._enforcing:
            # Construction gap: enforcing an owner-class policy requires the auth
            # service (enforcement delegates to it). Missing it is a wiring bug -
            # fail loud, never silently skip the owner check.
            owner_routes = [
                key
                for key, rp in self._registry.items()
                if rp.policy in OWNER_CLASS_POLICIES
            ]
            if owner_routes and self._auth is None:
                raise RuntimeError(
                    "authz gate is ENFORCING and the registry declares "
                    f"{len(owner_routes)} owner-class route(s) "
                    "(OWNER_ONLY/LOCAL_OWNER_ONLY), but no AuthService was "
                    "injected. Owner-class enforcement delegates to "
                    "AuthService.require_unscoped_owner; construct the gate with "
                    "auth=... so the owner check is never silently skipped."
                )

            # Construction gap (Step 4): an object-scoped route whose id the gate
            # resolves needs server.vault.db for the membership read. A
            # ``resolved_inline`` route is exempt (its inline handler check is the
            # enforcement, not the gate). Missing server is a wiring bug - fail
            # loud, never silently skip the object check.
            db_scoped_routes = [
                key
                for key, rp in self._registry.items()
                if rp.policy in SCOPED_POLICIES and not rp.resolved_inline
            ]
            if db_scoped_routes and self._server is None:
                raise RuntimeError(
                    "authz gate is ENFORCING and the registry declares "
                    f"{len(db_scoped_routes)} object-scoped route(s) "
                    "(*_SCOPED with a gate-resolved id), but no server was "
                    "injected. Object-scope enforcement reads membership via "
                    "server.vault.db; construct the gate with server=... so the "
                    "object check is never silently skipped."
                )

            gaps: list[str] = []
            if undeclared:
                gaps.append(f"{len(undeclared)} undeclared route(s)")
            if dead:
                gaps.append(f"{len(dead)} dead declaration(s)")
            if public_drift:
                gaps.append(f"{len(public_drift)} PUBLIC-consistency drift(s)")
            if gaps:
                detail = "\n".join(f"  {method} {path}" for method, path in undeclared)
                drift_detail = "\n".join(f"  {problem}" for problem in public_drift)
                raise RuntimeError(
                    "authz gate is ENFORCING but the coverage matrix is "
                    "incomplete: "
                    + "; ".join(gaps)
                    + ".\nEvery mounted data route must declare an AccessPolicy "
                    "in pixlstash/authz/registry.py, and every PUBLIC route must "
                    "be auth-excluded in the middleware.\nUndeclared routes:\n"
                    + detail
                    + ("\nPUBLIC drift:\n" + drift_detail if public_drift else "")
                )

        logger.info(
            "[authz-gate] resolved %d declared route policies (enforcing=%s); "
            "%d route(s) undeclared, %d dead declaration(s), %d PUBLIC drift(s).",
            len(self._policy_by_route_id),
            self._enforcing,
            len(undeclared),
            len(dead),
            len(public_drift),
        )

    async def __call__(self, conn: HTTPConnection) -> None:
        """Router-level dependency: deny-by-default on an undeclared route, plus
        owner-class enforcement (Step 3).

        **WebSocket short-circuit (this gate is HTTP-only by design).** The gate is
        mounted router-wide (``dependencies=[Depends(self.authz)]`` on every
        ``include_router``), so FastAPI also attaches it to any ``@router.websocket``
        route in those routers. WebSocket routes are deliberately OUT of the HTTP
        gate - their chokepoint is ``authenticate_websocket`` +
        ``is_websocket_origin_allowed`` inside each handler (plan §6; see the
        ``# WS routes: see authn/websocket.py`` sentinel). The parameter is typed
        ``HTTPConnection`` (not ``Request``) because FastAPI fills an
        ``HTTPConnection`` param for BOTH http and websocket scopes, whereas a
        ``Request`` param is left unset on a WS handshake - which crashed the
        dependency (``TypeError: missing 'request'``) *before* the handler's own
        auth could run. A non-``Request`` connection is a WebSocket: return
        immediately so the gate resolves harmlessly and enforces nothing on it.
        This runs before the policy-map lookup, so an enforcing gate never 403s a
        WS route as an "undeclared" miss either.

        Keys the policy map by the matched route's object identity
        (``id(request.scope["route"])``). A route not in the map is a miss:
        report-only logs it once (deduped per route) and lets it through;
        enforcing raises 403.

        A declared route, when enforcing, has its policy applied here. Owner-class
        (``OWNER_ONLY`` / ``LOCAL_OWNER_ONLY``, plus ``PUBLIC`` / ``ANY_TOKEN`` which
        need no per-object check) is applied synchronously; the object-scoped
        classes (``*_SCOPED`` single / ``body_ids`` batch / ``SCOPED_LIST``) run the
        Step-4 membership enforcement, which reads the DB on a threadpool worker.
        When report-only, every declared route passes untouched (the inline checks
        are the sole enforcement).
        """
        if not isinstance(conn, Request):
            # WebSocket connection: out of the HTTP gate (see docstring). WS auth
            # is enforced entirely by authenticate_websocket in the handler.
            return
        request = conn
        route = request.scope.get("route")
        route_policy = (
            self._policy_by_route_id.get(id(route)) if route is not None else None
        )
        if route_policy is None:
            if self._enforcing:
                raise HTTPException(
                    status_code=403,
                    detail="Route is not declared in the authorization registry",
                )
            route_id = id(route) if route is not None else 0
            if route_id not in self._logged_misses:
                self._logged_misses.add(route_id)
                logger.warning(
                    "[authz-gate] report-only: undeclared route reached %s %s "
                    "(would be denied 403 when AUTHZ_GATE_ENFORCING is enabled)",
                    request.method,
                    request.url.path,
                )
            return

        if not self._enforcing:
            # Report-only: the inline handler checks are the live enforcement.
            return
        await self._enforce_policy(request, route_policy)

    async def _enforce_policy(
        self, request: Request, route_policy: RoutePolicy
    ) -> None:
        """Apply a declared policy on the pre-branch request path (enforcing mode).

        ``PUBLIC`` / ``ANY_TOKEN`` need no object check (the middleware already
        authenticated non-excluded paths). ``OWNER_ONLY`` / ``LOCAL_OWNER_ONLY``
        delegate to the existing ``AuthService`` helpers so the ``token_scope``
        ladder lives in exactly one place. The object-scoped classes are handled
        by :meth:`_enforce_scoped_policy` (Step 4).

        **The library checks run first, then the project-filter check, for every
        declared route and every policy class (issue #708).** The project filter
        is deliberately policy-independent: a ``project_id`` filter is a question
        about the *project space*, not about the object the route is named after,
        so which ``AccessPolicy`` the route carries says nothing about whether the
        filter is allowed. Placing it here rather than in each handler is what
        makes it inherited - a new route that accepts ``project_id`` is covered
        the day it is mounted, with no declaration to remember (the omission class
        of §16.2). It resolves project ids against the server, so it must run
        *after* the library checks have settled which vault is being read.
        """
        # Mid-swap the server's vault is being replaced, so a request served now
        # could read from one library and write to another. Refused before any
        # other check, including the pin, because during the swap there is no
        # settled answer to "which library is active".
        self._refuse_while_switching(request, route_policy)
        if (
            route_policy.library_access is LibraryAccessMode.ACTIVE_VAULT
            and getattr(self._server, "library_coordinator", None) is not None
            and getattr(request.state, "library_lease", None) is None
        ):
            raise HTTPException(
                status_code=503,
                detail="Active-library request was not admitted safely.",
            )

        # The library pin runs before every policy branch, so no access level
        # can sidestep it and a route added without thinking about libraries is
        # pinned by default.
        self._enforce_library_pin(request, route_policy)

        enforce_project_filter_scope(self._server, request)

        policy = route_policy.policy
        if policy in (AccessPolicy.PUBLIC, AccessPolicy.ANY_TOKEN):
            return
        if policy is AccessPolicy.OWNER_ONLY:
            self._enforce_unscoped_owner(request)
            return
        if policy is AccessPolicy.LOCAL_OWNER_ONLY:
            self._enforce_unscoped_owner(request)
            self._enforce_local(request)
            return
        if policy is AccessPolicy.LOOPBACK_OWNER_ONLY:
            self._enforce_unscoped_owner(request)
            self._enforce_loopback(request)
            return
        # *_SCOPED single / body_ids batch / SCOPED_LIST → Step 4 object scoping.
        await self._enforce_scoped_policy(request, route_policy)

    async def _enforce_scoped_policy(
        self, request: Request, route_policy: RoutePolicy
    ) -> None:
        """Enforce an object-scoped policy (Step 4) once the gate is enforcing.

        The check engages only a **resource-scoped** token (:func:`_is_resource_scoped`);
        an owner or unscoped-READ token has unrestricted object access and passes
        immediately - no body read, no DB - exactly as today's inline ladders do.

        * ``SCOPED_LIST`` - no single id to check. An audited ``scope_aware`` list
          filters its own results (its inline ``fetch_scope_allowed_*`` remains the
          enforcement); the gate stamps the declared-intent signal and passes. An
          unaudited list fails **closed** (403): a new list route added without
          scope-aware filtering leaks nothing to a scoped token (§3.6 / D4).
        * ``resolved_inline`` ``*_SCOPED`` - the id is name-derived (§N3); the gate
          cannot resolve it without duplicating handler logic, so the inline
          ``_require_scope_allows_*`` check is the enforcement and the gate passes.
        * ``*_SCOPED`` single / ``body_ids`` batch - resolve the id(s) and run the
          per-object membership check for every one on a threadpool worker.
        """
        if not _is_resource_scoped(request):
            return

        policy = route_policy.policy
        if policy is AccessPolicy.SCOPED_LIST:
            if not route_policy.scope_aware:
                raise HTTPException(
                    status_code=403,
                    detail="This resource is not available to scoped tokens",
                )
            # Belt-and-suspenders declared-intent signal; the handler's own
            # fetch_scope_allowed_* filter is the actual enforcement for the 39
            # audited list routes (§3.6).
            request.state.scope_filter_required = True
            return

        if route_policy.resolved_inline:
            return

        raw_ids = await self._collect_raw_ids(request, route_policy)
        for raw_id in raw_ids:
            # Blocking membership read → threadpool so the event loop is never
            # blocked (D1). One id per hop; the gate runs before the handler, so
            # this is sequential occupancy, not doubled.
            await run_in_threadpool(self._check_one_id, request, route_policy, raw_id)

    async def _collect_raw_ids(
        self, request: Request, route_policy: RoutePolicy
    ) -> list:
        """Collect the raw id(s) to object-check: from ``body_ids`` or ``id_param``.

        Returns raw (unparsed) ids; :meth:`_check_one_id` parses/resolves each.
        Returns an empty list when there is nothing to check (absent path param,
        absent/empty body field) - a resource-scoped token that supplies no id
        gets no data anyway (the handler rejects a malformed request).
        """
        if route_policy.body_ids:
            return await self._read_body_ids(request, route_policy.body_ids)
        if route_policy.id_param:
            raw = request.path_params.get(route_policy.id_param)
            return [] if raw is None else [raw]
        return []

    async def _read_body_ids(self, request: Request, field: str) -> list:
        """Extract the id(s) from a JSON body field for a ``body_ids`` batch route.

        Handles a list (checked element by element), a single scalar (``run_t2i``'s
        optional ``source_picture_id``, §N6), and an absent/``None`` value (no-op).
        Reading the body here is safe: Starlette caches it on ``request._body`` so
        the handler's own ``Body(...)`` parse re-reads the cache.
        """
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                "[authz-gate] could not parse JSON body to extract %r for object "
                "scoping on %s %s (%s); no ids to check - the handler will reject a "
                "malformed body",
                field,
                request.method,
                request.url.path,
                exc,
            )
            return []
        if not isinstance(payload, dict):
            return []
        value = payload.get(field)
        if value is None:
            return []
        if isinstance(value, list):
            return [item for item in value if item is not None]
        return [value]

    def _check_one_id(
        self, request: Request, route_policy: RoutePolicy, raw_id
    ) -> None:
        """Resolve (if needed) and membership-check one raw id. Runs on a
        threadpool worker (blocking DB read), never on the event loop.

        Raises ``HTTPException(403)`` when the scoped token may not reach the
        object. A malformed id or an id_resolver that returns ``None`` fails
        closed - a resource-scoped token must not act on an id whose membership
        cannot be established.
        """
        if route_policy.id_resolver:
            resolver = ID_RESOLVERS.get(route_policy.id_resolver)
            if resolver is None:
                logger.error(
                    "[authz-gate] route declares unknown id_resolver %r on %s %s; "
                    "failing closed. This is a registry-authoring bug.",
                    route_policy.id_resolver,
                    request.method,
                    request.url.path,
                )
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised to access this resource",
                )
            picture_id = resolver(self._server, raw_id)
            if picture_id is None:
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised to access this resource",
                )
            enforce_picture_scope(self._server, request, picture_id)
            return

        try:
            obj_id = int(raw_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this resource",
            )
        check = _MEMBERSHIP_BY_POLICY[route_policy.policy]
        check(self._server, request, obj_id)

    def _refuse_while_switching(
        self, request: Request, route_policy: RoutePolicy
    ) -> None:
        """Return 503 while the active library is being replaced.

        The swap closes one vault and opens another. A request that landed in
        that window would be served against a half-swapped server, which is
        worse than an error: it could return a plausible answer from the wrong
        library. 503 with ``Retry-After`` says "come back in a second", which is
        exactly what is true.

        Library-independent routes are exempt: they touch no library content, so
        they remain answerable throughout, which is what lets a client ask what
        is going on instead of seeing everything fail.
        """
        if route_policy.library_access is not LibraryAccessMode.ACTIVE_VAULT:
            return

        from pixlstash.services.library_switch_service import (
            SwitchState,
            switching_state_of,
        )

        state = switching_state_of(self._server)
        if state is SwitchState.READY:
            return

        raise HTTPException(
            status_code=503,
            detail=(
                "PixlStash has no verified open library. Restart the server."
                if state is SwitchState.UNAVAILABLE
                else "PixlStash is switching library. Try again in a moment."
            ),
            headers={"Retry-After": "2"},
        )

    def _enforce_library_pin(self, request: Request, route_policy: RoutePolicy) -> None:
        """Refuse a token whose library is not the active one.

        Every token belongs to exactly one library (multi-library plan §4).
        Without this, switching library would silently change what an existing
        token grants: a share link would start serving somebody else's pictures,
        and an automation holding an ALL token would write into the wrong place.

        Cookie sessions are deliberately exempt. A session says "I am the owner,
        show me what is active", and following the switch is the entire point of
        the feature; a token says "programmatic access to *this* library".

        Fails closed in both directions that matter: a token with no stamp at all
        is refused rather than treated as universal.
        """
        if route_policy.library_independent:
            return

        matched_token = getattr(request.state, "matched_token", None)
        if matched_token is None:
            # Password/browser sessions follow switches. A cookie session
            # created from a token inherits that token's pin.
            session_library = getattr(request.state, "session_library_uuid", None)
            if session_library is None:
                return
            active_uuid = self._auth.active_library_uuid()
            if active_uuid is None or session_library == active_uuid:
                return
            raise HTTPException(
                status_code=403,
                detail=(
                    "This session was created from a token for a library that "
                    "is not currently active."
                ),
            )

        active_uuid = self._auth.active_library_uuid()
        if active_uuid is None:
            # No registry (a Vault built without a Server, or a hub that has no
            # libraries yet). Nothing to pin against, so nothing to enforce.
            return

        token_library = getattr(matched_token, "library_uuid", None)
        if token_library == active_uuid:
            return

        # A resource-scoped share token learns nothing: 404 is what every other
        # out-of-scope resource returns, so a link to a non-active library is
        # indistinguishable from one that never existed.
        if _is_resource_scoped(request):
            logger.info(
                "Refusing a resource-scoped token stamped for library %s while "
                "%s is active",
                token_library,
                active_uuid,
            )
            raise HTTPException(status_code=404, detail="Not found")

        # An owner token holder owns both libraries, so name the problem.
        logger.info(
            "Refusing an owner token stamped for library %s while %s is active",
            token_library,
            active_uuid,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "This token belongs to a library that is not currently active. "
                "Switch to that library, or use a token created for the active "
                "one."
            ),
        )

    def _enforce_unscoped_owner(self, request: Request) -> None:
        """Require a fully-unscoped owner via the shared AuthService helper.

        Delegates to ``AuthService.require_unscoped_owner`` (401 if unauthenticated,
        403 for any scoped/unscoped-READ token or a resource-restricted token) so
        the wire contract stays byte-identical to today's inline call. ``auth`` is
        guaranteed present here: an enforcing gate with owner-class routes but no
        auth service boot-fails in :meth:`enforce_startup`. The ``None`` guard is a
        defensive fail-closed for any construction path that bypasses that check.
        """
        if self._auth is None:
            logger.error(
                "[authz-gate] owner-class route reached with no AuthService while "
                "enforcing (%s) - failing closed. This is a wiring bug.",
                request.url.path,
            )
            raise HTTPException(status_code=403, detail="Authorization unavailable")
        self._auth.require_unscoped_owner(request)

    def _enforce_local(self, request: Request) -> None:
        """Require a loopback / LAN / Tailscale client, unless remote host-ops are
        explicitly enabled (the ``LOCAL_OWNER_ONLY`` locality half, §16.3).

        Locality uses ``AuthService.real_client_ip`` (trusted-proxy aware) +
        :func:`is_local_or_tailscale_ip` - the scoped predicate that counts a
        Tailscale-over-IPv4 owner (CGNAT ``100.64.0.0/10``) as local, fixing the
        false-deny without widening the shared ``is_local_ip`` (and its unrelated
        LAN callers). A genuinely remote owner is admitted ONLY when the dedicated
        ``allow_remote_host_ops`` flag is set; otherwise the 403 names that flag so
        the operator knows the exact setting that enables it. Assumes
        ``_enforce_unscoped_owner`` ran first (owner identity established); ``auth``
        is therefore non-None.
        """
        if self._auth is None:  # defensive; unreachable after _enforce_unscoped_owner
            raise HTTPException(status_code=403, detail="Authorization unavailable")
        client_ip = self._auth.real_client_ip(request)
        if is_local_or_tailscale_ip(client_ip):
            return
        if self._auth.allow_remote_host_ops:
            return
        raise HTTPException(
            status_code=403,
            detail=(
                "This host-capability operation is restricted to local "
                "(loopback / LAN / Tailscale) connections. To allow a remote "
                "authenticated owner to use it, set allow_remote_host_ops=true in "
                "the server config."
            ),
        )

    def _enforce_loopback(self, request: Request) -> None:
        """Require the request to originate from a strict loopback client.

        The ``LOOPBACK_OWNER_ONLY`` red line (§16.3): the highest-privilege
        host-shell routes (server restart, open-folder / open-file-location in the
        host file manager) must be unreachable from any non-loopback host -
        RFC1918 LAN and Tailscale are NOT accepted, and ``allow_remote_host_ops``
        deliberately does NOT appear here, so the flag can never loosen this tier.
        Assumes ``_enforce_unscoped_owner`` ran first; ``auth`` is non-None.
        """
        if self._auth is None:  # defensive; unreachable after _enforce_unscoped_owner
            raise HTTPException(status_code=403, detail="Authorization unavailable")
        if not is_loopback_ip(self._auth.real_client_ip(request)):
            raise HTTPException(
                status_code=403,
                detail=(
                    "This operation drives the server's host shell and is "
                    "restricted to loopback (127.0.0.0/8 / ::1) connections only. "
                    "It is not reachable from the LAN, from Tailscale, or with "
                    "allow_remote_host_ops enabled."
                ),
            )

    def _scoped_id_param_problems(self, app) -> list[str]:
        """Return problems where a ``*_SCOPED`` ``id_param`` is not in its template."""
        params_by_key = {
            (method, path): _template_params(path)
            for method, path, _route in iter_api_route_contexts(app)
        }
        problems: list[str] = []
        for (method, path), route_policy in self._registry.items():
            if route_policy.policy in SCOPED_POLICIES and route_policy.id_param:
                template = params_by_key.get((method, path))
                if template is not None and route_policy.id_param not in template:
                    problems.append(
                        f"{method} {path}: id_param {route_policy.id_param!r} is "
                        "not a parameter of the route template"
                    )
        return problems

    def _id_resolver_problems(self) -> list[str]:
        """Return problems where a declared ``id_resolver`` names no real resolver.

        A typo'd or removed resolver name would otherwise fail closed silently at
        request time (a skipped-then-403 object check). Catch it at boot so a
        registry-authoring mistake is fatal, not a latent 403.
        """
        problems: list[str] = []
        for (method, path), route_policy in self._registry.items():
            if (
                route_policy.id_resolver
                and route_policy.id_resolver not in ID_RESOLVERS
            ):
                problems.append(
                    f"{method} {path}: id_resolver {route_policy.id_resolver!r} is "
                    "not a registered resolver (pixlstash/authz/membership.py "
                    "ID_RESOLVERS)"
                )
        return problems

    def _public_consistency_problems(self) -> list[str]:
        """Return ``PUBLIC`` declarations that the middleware does not auth-exclude.

        A ``PUBLIC`` route must also be excluded from authentication by the
        middleware (``AUTH_EXCLUDED_*``), or the two lists have drifted: the
        registry says "no auth" while the middleware still demands it. This
        reconciles the declaration table with the live auth surface (plan §3.3
        item 3). Unlike the pure authoring checks this is not unconditionally
        fatal - it compares against the middleware's exclusion surface, so it is
        report-only until the gate is enforcing (then it boot-fails). The SPA
        catch-all (:data:`_PUBLIC_CONSISTENCY_EXEMPT_PATHS`) is exempt: it is a
        path-template that can never be a static ``AUTH_EXCLUDED_*`` entry yet is
        legitimately public (matrix §N1).
        """
        problems: list[str] = []
        for (method, path), route_policy in self._registry.items():
            if route_policy.policy is not AccessPolicy.PUBLIC:
                continue
            if path in _PUBLIC_CONSISTENCY_EXEMPT_PATHS:
                continue
            if not is_auth_excluded_path(path):
                problems.append(
                    f"{method} {path}: declared PUBLIC but not in AUTH_EXCLUDED_* "
                    "(middleware would still require auth)"
                )
        return problems


__all__ = ["AUTHZ_GATE_ENFORCING", "OWNER_CLASS_POLICIES", "AuthzGate"]
