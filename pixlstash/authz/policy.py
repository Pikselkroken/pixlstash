"""Access-policy vocabulary for the centralised authorization gate.

Defines the closed :class:`AccessPolicy` vocabulary and the :class:`RoutePolicy`
declaration record that the authz registry (:mod:`pixlstash.authz.registry`) maps
each mounted route to. This is Phase 1 of the backend authorization refactor — see
``docs/backend_architecture.md`` §16.2 and the backend refactor plan §3.1 / §3.2.

The enum is deliberately **closed**: adding an access level is a deliberate edit
here plus its tests, which is exactly the friction that keeps the authorization
vocabulary small and reviewable. A route is made safe by *declaring* one of these
policies in the registry, never by omission — an undeclared data route is denied
by the gate (deny-by-default), not allowed through.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AccessPolicy(str, Enum):
    """The complete, closed set of access levels a route may declare.

    Reads like English on a route: ``PICTURE_SCOPED`` means "a scoped share token
    reaches this only if the picture is in its grant"; ``OWNER_ONLY`` means "share
    tokens never reach this". Loosening a route is a one-line, diff-visible change
    of the declared value — there is no way to loosen by omitting a declaration.
    """

    PUBLIC = "public"
    """No auth at all (login, ``/version``, ``/share/*``). Returns no owner data."""

    ANY_TOKEN = "any_token"
    """Any authenticated principal; the route returns no per-object resource data
    (e.g. ``/sort_mechanisms``), so no object check is needed."""

    PICTURE_SCOPED = "picture_scoped"
    """Object check via the picture-scope membership logic on a picture id."""

    SET_SCOPED = "set_scoped"
    """Object check on a picture-set id."""

    CHARACTER_SCOPED = "character_scoped"
    """Object check on a character id."""

    PROJECT_SCOPED = "project_scoped"
    """Object check on a project id."""

    SCOPED_LIST = "scoped_list"
    """List/search endpoint: no single id; results are filtered through the
    scope-allowed id set. Object filtering is Step 4 handler work — the gate does
    not perform list filtering, it only records the declaration."""

    OWNER_ONLY = "owner_only"
    """Requires an unscoped owner (cookie session or unscoped ``ALL`` token);
    scoped share tokens never reach it."""

    LOCAL_OWNER_ONLY = "local_owner_only"
    """``OWNER_ONLY`` plus a loopback / local-IP check (host-filesystem browse,
    reference-folder writes — the §16.3 accepted-risk class)."""


# The object-scoped policies whose enforcement resolves a single resource id from
# the route (Step 4 work). Declared here so the startup validator can require an
# ``id_param`` (or ``body_ids``) for each and reject a ``*_SCOPED`` declaration
# that names a path param its route template does not contain.
SCOPED_POLICIES = frozenset(
    {
        AccessPolicy.PICTURE_SCOPED,
        AccessPolicy.SET_SCOPED,
        AccessPolicy.CHARACTER_SCOPED,
        AccessPolicy.PROJECT_SCOPED,
    }
)

# Policies whose declaration MUST carry a written justification. This is the
# machine-checked replacement for the §16.1 "written justification + named
# reviewer sign-off" prose rule (the reviewer sign-off still lives in the PR).
# ``PUBLIC`` opens a route to the world; ``LOCAL_OWNER_ONLY`` grants host-
# filesystem authority — both are decisions someone must own in writing.
JUSTIFICATION_REQUIRED = frozenset(
    {
        AccessPolicy.PUBLIC,
        AccessPolicy.LOCAL_OWNER_ONLY,
    }
)


@dataclass(frozen=True)
class RoutePolicy:
    """One route's declared access requirement — a single coverage-matrix cell.

    Attributes:
        policy: The required :class:`AccessPolicy` (the only mandatory field).
        id_param: For a ``*_SCOPED`` policy, the path-template parameter carrying
            the resource id (e.g. ``"picture_id"``). Validated at startup: a
            ``*_SCOPED`` policy whose ``id_param`` is absent from the route
            template is a boot failure, not a silent no-op.
        body_ids: For a batch route, the JSON field holding the id list the gate
            must check every element of (Step 4 enforcement).
        justification: Mandatory for the policies in
            :data:`JUSTIFICATION_REQUIRED`; a written reason the route is public
            or grants local-owner filesystem authority.
    """

    policy: AccessPolicy
    id_param: str | None = None
    body_ids: str | None = None
    justification: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, AccessPolicy):
            raise TypeError(
                f"RoutePolicy.policy must be an AccessPolicy, got {self.policy!r}"
            )


def validate_policy_declarations(
    registry: dict[tuple[str, str], RoutePolicy],
) -> list[str]:
    """Return human-readable problems with the registry declarations (pure checks).

    These are structural invariants that need no built app: a
    :data:`JUSTIFICATION_REQUIRED` policy must carry a non-empty ``justification``,
    and a ``*_SCOPED`` policy must name an ``id_param`` or ``body_ids`` so the gate
    knows where to find the resource id. An empty list means the declarations are
    clean. The startup validator treats a non-empty result as a boot failure — a
    registry-authoring mistake is always fatal, independent of the report-only
    gate flag, because it is an error in the declaration table itself.
    """
    problems: list[str] = []
    for (method, path), route_policy in registry.items():
        if route_policy.policy in JUSTIFICATION_REQUIRED and not (
            (route_policy.justification or "").strip()
        ):
            problems.append(
                f"{method} {path}: {route_policy.policy.value} requires a "
                "justification string"
            )
        if (
            route_policy.policy in SCOPED_POLICIES
            and not route_policy.id_param
            and not route_policy.body_ids
        ):
            problems.append(
                f"{method} {path}: {route_policy.policy.value} requires an "
                "id_param (or body_ids for a batch route)"
            )
    return problems


__all__ = [
    "AccessPolicy",
    "SCOPED_POLICIES",
    "JUSTIFICATION_REQUIRED",
    "RoutePolicy",
    "validate_policy_declarations",
]
