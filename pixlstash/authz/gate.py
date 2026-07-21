"""The centralised authorization gate — one router-level dependency.

Phase 1 of the backend authorization refactor (``docs/backend_architecture.md``
§16.2, backend refactor plan §3.3 / §3.4 / §3.5 step 1). :class:`AuthzGate` is
attached once to every ``include_router`` call in ``pixlstash/server.py`` and runs
after authentication (the auth middleware has already populated
``request.state``). It looks up the policy for the matched route and, on a miss,
denies by default.

**Route-identity keying (CSO-required).** The gate keys its policy map by the
persistent ``original_route`` object captured from
:func:`pixlstash.route_inventory.iter_api_route_contexts` — the *same* walk the CI
coverage matrix uses — NOT by ``request.scope["route"].path``. That request-time
path is prefix-stripped (``/pictures/{id}/metadata``) and diverges from the
enumerated effective path (``/api/v1/pictures/{id}/metadata``) on the vast
majority of routes, so string keying would fail to match ~93% of routes and
fail *open*. At request time ``request.scope["route"]`` is the very same route
object the enumeration yielded (verified: dependency-time identity matches
enumeration identity), so ``id(route)`` is a stable, correct key. A request-time
route object not present in the map resolves to **deny**, never allow.

**Report-only shipped default (``AUTHZ_GATE_ENFORCING = False``).** At the shipped
default the gate denies nothing at runtime and the startup enumeration only
*prints* the undeclared-route backlog; the inline handler checks remain the live
enforcement. The single boolean is the per-release rollback switch of plan §6 — a
code constant, not runtime config — and stays ``False`` through Steps 3-5, flipping
fail-closed only at Step 6 under the adversarial sign-off.

**Step-3 owner-class enforcement (behind the flag; principal ruling 2026-07-21).**
The enforcement *code* for the non-id-resolution classes lands now:
``OWNER_ONLY`` / ``LOCAL_OWNER_ONLY`` delegate to the existing ``AuthService``
helpers (``require_unscoped_owner`` + ``real_client_ip``/``is_local_ip``), and a
startup ``PUBLIC``-consistency check reconciles ``PUBLIC`` declarations against
the middleware's ``AUTH_EXCLUDED_*``. Per-policy-class staging is carried by
*which branches are implemented* — there is deliberately no second toggle. It is
proven now by ``AuthzGate(enforcing=True)`` tests; the id-resolving classes
(``*_SCOPED`` / ``SCOPED_LIST`` / ``body_ids`` batch) stay pass-through until
Step 4. Because the shipped default stays report-only, none of this changes
runtime behaviour until the Step-6 flip — no window is weaker *or* stronger than
today (the inline checks still run).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from pixlstash.auth import is_auth_excluded_path, is_local_ip
from pixlstash.authz.policy import (
    SCOPED_POLICIES,
    AccessPolicy,
    RoutePolicy,
    validate_policy_declarations,
)
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.route_inventory import iter_api_route_contexts

if TYPE_CHECKING:
    from pixlstash.auth import AuthService

logger = logging.getLogger(__name__)

# The owner-class policies the gate resolves WITHOUT resolving a per-object
# resource id. Step 3 makes the gate enforcing for exactly these (plus the
# PUBLIC-consistency startup check); the id-resolving classes
# (``SCOPED_POLICIES`` + ``SCOPED_LIST`` + ``body_ids`` batch) stay pass-through
# until Step 4. Enforcement of an owner class delegates to the existing
# ``AuthService`` helpers (plan §3.3 item 4 — the ``token_scope`` ladder is NOT
# reimplemented here), so a route declaring one of these needs the ``auth``
# service injected; a missing service while enforcing is a boot failure, never a
# silently-skipped check.
OWNER_CLASS_POLICIES = frozenset(
    {AccessPolicy.OWNER_ONLY, AccessPolicy.LOCAL_OWNER_ONLY}
)

# The SPA catch-all can never be a static ``AUTH_EXCLUDED_*`` entry (it is a
# path-template, not a literal path/prefix), yet it is legitimately PUBLIC — it
# serves the static shell/assets and returns no owner data (matrix §N1). Exempt
# it from the PUBLIC-consistency check so a correct declaration does not boot-fail.
_PUBLIC_CONSISTENCY_EXEMPT_PATHS = frozenset({"/{full_path:path}"})

# Master rollback switch (plan §6). A CODE CONSTANT flipped per release, NOT
# runtime config. FALSE == report-only: the gate logs undeclared routes and the
# startup enumeration prints the backlog, but nothing is denied and boot never
# fails on the backlog. TRUE == fail-closed: an undeclared route is 403 at
# request time and a boot failure at startup. Phase 1 Step 1 ships FALSE; the
# enforcing steps (3-6) flip it on.
AUTHZ_GATE_ENFORCING = False

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
        """
        self._registry = registry if registry is not None else ROUTE_POLICIES
        self._enforcing = enforcing
        self._auth = auth
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

        Consumes :func:`iter_api_route_contexts` — the same enumeration the CI
        coverage matrix uses — so the gate's map and the matrix can never disagree
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
        dead = sorted(key for key in self._registry if key not in live)
        return undeclared, dead

    def enforce_startup(self, app) -> None:
        """Build the route map, report the backlog, and fail-close when enforcing.

        Always fatal (independent of the report-only flag): registry-authoring
        errors — a ``PUBLIC``/``LOCAL_OWNER_ONLY`` entry missing its justification,
        or a ``*_SCOPED`` ``id_param`` absent from its template — abort boot,
        because they are mistakes in the declaration table itself.

        Report-only vs. enforcing: an *undeclared route* (or a *dead declaration*)
        is logged as a backlog when ``enforcing`` is False, and aborts boot when
        ``enforcing`` is True. Step 1 ships report-only, so all 207 routes log as
        backlog and boot proceeds.
        """
        undeclared, dead = self.resolve_routes(app)
        authoring_problems = validate_policy_declarations(self._registry)
        authoring_problems += self._scoped_id_param_problems(app)
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
                "route (dead declarations — prune or fix the path):\n%s",
                len(dead),
                "\n".join(f"  {method} {path}" for method, path in dead),
            )
        if public_drift:
            logger.warning(
                "[authz-gate] %d PUBLIC declaration(s) are NOT auth-excluded in "
                "the middleware (AUTH_EXCLUDED_*) — the two lists have drifted; a "
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
                "authz registry declaration error(s) — fix the declaration "
                "table:\n" + "\n".join(f"  {problem}" for problem in authoring_problems)
            )

        if self._enforcing:
            # Construction gap: enforcing an owner-class policy requires the auth
            # service (enforcement delegates to it). Missing it is a wiring bug —
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

    async def __call__(self, request: Request) -> None:
        """Router-level dependency: deny-by-default on an undeclared route, plus
        owner-class enforcement (Step 3).

        Keys the policy map by the matched route's object identity
        (``id(request.scope["route"])``). A route not in the map is a miss:
        report-only logs it once (deduped per route) and lets it through;
        enforcing raises 403.

        A declared route, when enforcing, has its **owner-class** policy applied
        here (``OWNER_ONLY`` / ``LOCAL_OWNER_ONLY``, plus ``PUBLIC`` / ``ANY_TOKEN``
        which need no per-object check). The id-resolving classes
        (``*_SCOPED`` / ``SCOPED_LIST`` / ``body_ids`` batch) are **pass-through**
        until Step 4 — their inline ``enforce_picture_scope`` /
        ``fetch_scope_allowed`` checks remain the live enforcement. When
        report-only, every declared route passes untouched (the inline checks are
        the sole enforcement).
        """
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
        self._enforce_policy(request, route_policy)

    def _enforce_policy(self, request: Request, route_policy: RoutePolicy) -> None:
        """Apply a declared policy on the pre-branch request path (enforcing mode).

        Step 3 covers the non-id-resolution classes only. ``PUBLIC`` / ``ANY_TOKEN``
        need no object check (the middleware already authenticated non-excluded
        paths). ``OWNER_ONLY`` / ``LOCAL_OWNER_ONLY`` delegate to the existing
        ``AuthService`` helpers so the ``token_scope`` ladder lives in exactly one
        place. The id-resolving classes fall through as pass-through (Step 4).
        """
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
        # *_SCOPED / SCOPED_LIST / body_ids batch → Step 4. Pass-through: the inline
        # enforce_picture_scope / fetch_scope_allowed checks are still live.
        return

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
                "enforcing (%s) — failing closed. This is a wiring bug.",
                request.url.path,
            )
            raise HTTPException(status_code=403, detail="Authorization unavailable")
        self._auth.require_unscoped_owner(request)

    def _enforce_local(self, request: Request) -> None:
        """Require the request to originate from a loopback / local-IP client.

        The ``LOCAL_OWNER_ONLY`` locality half (§16.3 host-capability class). Uses
        ``AuthService.real_client_ip`` (trusted-proxy aware) + ``is_local_ip`` and
        raises the same 403 detail as ``_require_local_for_write`` for wire
        consistency. Assumes ``_enforce_unscoped_owner`` ran first (owner identity
        established); ``auth`` is therefore non-None.
        """
        if self._auth is None:  # defensive; unreachable after _enforce_unscoped_owner
            raise HTTPException(status_code=403, detail="Authorization unavailable")
        if not is_local_ip(self._auth.real_client_ip(request)):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Full access is restricted to local network connections. "
                    "Use a share token for remote access."
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

    def _public_consistency_problems(self) -> list[str]:
        """Return ``PUBLIC`` declarations that the middleware does not auth-exclude.

        A ``PUBLIC`` route must also be excluded from authentication by the
        middleware (``AUTH_EXCLUDED_*``), or the two lists have drifted: the
        registry says "no auth" while the middleware still demands it. This
        reconciles the declaration table with the live auth surface (plan §3.3
        item 3). Unlike the pure authoring checks this is not unconditionally
        fatal — it compares against the middleware's exclusion surface, so it is
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
