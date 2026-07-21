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

**Report-only in Step 1 (``AUTHZ_GATE_ENFORCING = False``).** The gate denies
nothing at runtime and the startup enumeration only *prints* the undeclared-route
backlog. The fail-closed machinery — 403 on a miss at request time and boot
failure on a miss at startup — exists behind the constant and is proven by the
decoy-route guardrail test. Later steps flip the constant on. This is the
single-boolean rollback switch of plan §6: a code constant, flipped per release,
not runtime config.
"""

from __future__ import annotations

import logging
import re

from fastapi import HTTPException, Request

from pixlstash.authz.policy import (
    SCOPED_POLICIES,
    RoutePolicy,
    validate_policy_declarations,
)
from pixlstash.authz.registry import ROUTE_POLICIES
from pixlstash.route_inventory import iter_api_route_contexts

logger = logging.getLogger(__name__)

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
    ) -> None:
        """Initialise the gate.

        Args:
            registry: The declaration table to enforce. Defaults to the shared
                ``ROUTE_POLICIES``; an explicit table is injected by tests.
            enforcing: Whether misses fail closed (403 / boot failure) or are
                report-only. Defaults to the ``AUTHZ_GATE_ENFORCING`` constant.
        """
        self._registry = registry if registry is not None else ROUTE_POLICIES
        self._enforcing = enforcing
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

        # Registry-authoring errors are always fatal.
        if authoring_problems:
            raise RuntimeError(
                "authz registry declaration error(s) — fix the declaration "
                "table:\n" + "\n".join(f"  {problem}" for problem in authoring_problems)
            )

        if self._enforcing:
            gaps: list[str] = []
            if undeclared:
                gaps.append(f"{len(undeclared)} undeclared route(s)")
            if dead:
                gaps.append(f"{len(dead)} dead declaration(s)")
            if gaps:
                detail = "\n".join(f"  {method} {path}" for method, path in undeclared)
                raise RuntimeError(
                    "authz gate is ENFORCING but the coverage matrix is "
                    "incomplete: "
                    + "; ".join(gaps)
                    + ".\nEvery mounted data route must declare an AccessPolicy "
                    "in pixlstash/authz/registry.py.\nUndeclared routes:\n" + detail
                )

        logger.info(
            "[authz-gate] resolved %d declared route policies (enforcing=%s); "
            "%d route(s) undeclared, %d dead declaration(s).",
            len(self._policy_by_route_id),
            self._enforcing,
            len(undeclared),
            len(dead),
        )

    async def __call__(self, request: Request) -> None:
        """Router-level dependency: deny-by-default on an undeclared route.

        Keys the policy map by the matched route's object identity
        (``id(request.scope["route"])``). A route not in the map is a miss:
        report-only logs it once (deduped per route) and lets it through;
        enforcing raises 403. A declared route passes the gate untouched in Step 1
        — object-scope enforcement (``*_SCOPED`` membership, ``SCOPED_LIST``
        filtering, ``body_ids`` batch checks, ``OWNER_ONLY``) lands in Steps 3-4.
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
        # Declared route: Step 1 performs no object-scope enforcement here. The
        # inline enforce_picture_scope calls remain the live enforcement until
        # Steps 3-5 relocate them behind this gate.
        return

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


__all__ = ["AUTHZ_GATE_ENFORCING", "AuthzGate"]
