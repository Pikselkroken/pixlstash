"""Access-policy vocabulary for the centralised authorization gate.

Defines the closed :class:`AccessPolicy` vocabulary and the :class:`RoutePolicy`
declaration record that the authz registry (:mod:`pixlstash.authz.registry`) maps
each mounted route to. This is Phase 1 of the backend authorization refactor - see
``docs/backend_architecture.md`` §16.2 and the backend refactor plan §3.1 / §3.2.

The enum is deliberately **closed**: adding an access level is a deliberate edit
here plus its tests, which is exactly the friction that keeps the authorization
vocabulary small and reviewable. A route is made safe by *declaring* one of these
policies in the registry, never by omission - an undeclared data route is denied
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
    of the declared value - there is no way to loosen by omitting a declaration.
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
    scope-allowed id set. Object filtering is Step 4 handler work - the gate does
    not perform list filtering, it only records the declaration."""

    OWNER_ONLY = "owner_only"
    """Requires an unscoped owner (cookie session or unscoped ``ALL`` token);
    scoped share tokens never reach it."""

    LOCAL_OWNER_ONLY = "local_owner_only"
    """``OWNER_ONLY`` plus a loopback / local-IP / Tailscale-CGNAT check
    (host-filesystem browse, reference-folder writes - the §16.3 accepted-risk
    class). A remote owner is admitted only when the dedicated
    ``allow_remote_host_ops`` server-config flag is ``True`` (default ``False``);
    the deny path names that flag."""

    LOOPBACK_OWNER_ONLY = "loopback_owner_only"
    """``OWNER_ONLY`` plus a strict **loopback-only** check (127.0.0.0/8 + ::1) -
    stricter than ``LOCAL_OWNER_ONLY``: RFC1918 LAN and Tailscale addresses are
    NOT accepted, and ``allow_remote_host_ops`` can NEVER loosen it. The §16.3
    hard red line for the highest-privilege host-shell routes (server restart,
    open-folder / open-file-location in the host file manager) which drive the
    server process's own shell and must be unreachable from any non-loopback host
    (principal ruling 2026-07-21: closed-enum extension, Option A)."""


class LibraryAccessMode(str, Enum):
    """Whether a route reads the active vault or coordinates its replacement."""

    ACTIVE_VAULT = "active_vault"
    HUB_ONLY = "hub_only"
    SWITCH_WRITER = "switch_writer"


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
# ``PUBLIC`` opens a route to the world; ``LOCAL_OWNER_ONLY`` /
# ``LOOPBACK_OWNER_ONLY`` grant host-filesystem / host-shell authority - all are
# decisions someone must own in writing.
JUSTIFICATION_REQUIRED = frozenset(
    {
        AccessPolicy.PUBLIC,
        AccessPolicy.LOCAL_OWNER_ONLY,
        AccessPolicy.LOOPBACK_OWNER_ONLY,
    }
)


@dataclass(frozen=True)
class RoutePolicy:
    """One route's declared access requirement - a single coverage-matrix cell.

    Attributes:
        policy: The required :class:`AccessPolicy` (the only mandatory field).
        id_param: For a ``*_SCOPED`` policy, the path-template parameter carrying
            the resource id (e.g. ``"picture_id"``). Validated at startup: a
            ``*_SCOPED`` policy whose ``id_param`` is absent from the route
            template is a boot failure, not a silent no-op.
        body_ids: For a batch route, the JSON body field holding the id(s) the
            gate must check. A list is checked element by element; a single scalar
            (e.g. ``run_t2i``'s optional ``source_picture_id``) is checked as one
            id; ``None`` / absent is a no-op.
        justification: Mandatory for the policies in
            :data:`JUSTIFICATION_REQUIRED`, and for ``resolved_inline`` routes; a
            written reason the route is public, grants local-owner filesystem
            authority, or defers its object check to an inline handler ladder.
        resolved_inline: ``True`` marks a ``*_SCOPED`` route whose object id is
            **name-derived** (e.g. ``/projects/{project_name}/...``) and therefore
            cannot be resolved to a numeric id at the gate without duplicating the
            handler's own name→id lookup - the exact divergence this refactor
            exists to kill (matrix §N3; principal ruling 2026-07-21 D2). The gate
            does **not** object-check these; the handler's inline
            ``_require_scope_allows_*`` check remains the live enforcement and must
            not be removed in Step 5 until a shared name→id resolver exists. This
            is a typed, validator-checked exemption - not a comment.
        scope_aware: Only valid on :attr:`AccessPolicy.SCOPED_LIST`. ``True`` marks
            a list/search route that has been **audited** to filter its own result
            set for a resource-scoped token (the 39 current list routes all do).
            A ``SCOPED_LIST`` route left ``scope_aware=False`` (the safe-by-omission
            default) is failed **closed** by the gate for a resource-scoped token -
            a new, unaudited list route leaks nothing (matrix §3.6; principal
            ruling 2026-07-21 D4). The gate cannot synthesise a correct empty
            envelope for an arbitrary list shape, so "leak nothing" is a 403, not
            an empty body.
        library_independent: ``True`` exempts the route from the library pin -
            the rule that a token only authenticates while the library it was
            minted for is the active one (multi-library plan §4). **The default,
            ``False``, is the safe one:** a new data route is pinned by
            omission, exactly as an undeclared route is denied by omission.
            A route qualifies for the exemption only if it satisfies *both*
            clauses: it returns no library content, **and** it cannot be used to
            acquire access to a different library. The second clause is what
            keeps token minting pinned - a token stamped for library A that
            could mint while B is active would hand itself a B-stamped token and
            reopen the pivot the pin exists to close.
        id_resolver: Names a registered resolver that maps the route's raw id(s)
            (from :attr:`id_param` or :attr:`body_ids`) to a **picture id** before
            the picture-membership check - for routes keyed by a non-picture id
            that nonetheless authorise on picture scope (matrix §N4: the
            ``tag_suggestions`` mutators key on a ``suggestion_id`` that resolves to
            ``TagSuggestion.picture_id``). Only valid on a ``*_SCOPED`` policy.
    """

    policy: AccessPolicy
    id_param: str | None = None
    body_ids: str | None = None
    justification: str | None = None
    resolved_inline: bool = False
    scope_aware: bool = False
    id_resolver: str | None = None
    library_independent: bool = False
    library_access: LibraryAccessMode = LibraryAccessMode.ACTIVE_VAULT

    def __post_init__(self) -> None:
        if not isinstance(self.policy, AccessPolicy):
            raise TypeError(
                f"RoutePolicy.policy must be an AccessPolicy, got {self.policy!r}"
            )
        if (
            self.library_independent
            and self.library_access is LibraryAccessMode.ACTIVE_VAULT
        ):
            object.__setattr__(self, "library_access", LibraryAccessMode.HUB_ONLY)


def validate_policy_declarations(
    registry: dict[tuple[str, str], RoutePolicy],
) -> list[str]:
    """Return human-readable problems with the registry declarations (pure checks).

    These are structural invariants that need no built app: a
    :data:`JUSTIFICATION_REQUIRED` policy must carry a non-empty ``justification``,
    and a ``*_SCOPED`` policy must name an ``id_param`` or ``body_ids`` so the gate
    knows where to find the resource id. An empty list means the declarations are
    clean. The startup validator treats a non-empty result as a boot failure - a
    registry-authoring mistake is always fatal, independent of the report-only
    gate flag, because it is an error in the declaration table itself.
    """
    problems: list[str] = []
    for (method, path), route_policy in registry.items():
        justification_required = (
            route_policy.policy in JUSTIFICATION_REQUIRED
            or route_policy.resolved_inline
        )
        if justification_required and not ((route_policy.justification or "").strip()):
            reason = (
                route_policy.policy.value
                if route_policy.policy in JUSTIFICATION_REQUIRED
                else "resolved_inline"
            )
            problems.append(
                f"{method} {path}: {reason} requires a justification string"
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
        # resolved_inline is a *_SCOPED-only deferral (name-derived id, §N3).
        if route_policy.resolved_inline and route_policy.policy not in SCOPED_POLICIES:
            problems.append(
                f"{method} {path}: resolved_inline is only valid on a *_SCOPED policy"
            )
        # scope_aware only means something on a list route (§3.6).
        if route_policy.scope_aware and route_policy.policy is not (
            AccessPolicy.SCOPED_LIST
        ):
            problems.append(
                f"{method} {path}: scope_aware is only valid on a scoped_list policy"
            )
        # id_resolver maps a non-picture id to a picture id for the membership
        # check; it only applies where an object scope check runs (§N4).
        if route_policy.id_resolver and route_policy.policy not in SCOPED_POLICIES:
            problems.append(
                f"{method} {path}: id_resolver is only valid on a *_SCOPED policy"
            )
    return problems


__all__ = [
    "AccessPolicy",
    "SCOPED_POLICIES",
    "JUSTIFICATION_REQUIRED",
    "RoutePolicy",
    "validate_policy_declarations",
]
