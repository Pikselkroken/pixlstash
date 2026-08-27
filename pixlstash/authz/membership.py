"""Object-membership checks for the centralised authorization gate.

Phase 1 Step 4 of the backend authorization refactor (backend refactor plan
§3.5 / §3.7). This module is the **single home** for the per-object scope
membership ladder - the ``token_scope`` branching that decides whether a scoped
share token may reach a specific picture / picture-set / character / project.
Before this module the same ladder was copy-pasted in five places
(``routes/pictures/_helpers.py::enforce_picture_scope`` plus the inline
``_require_scope_allows_{picture_set,character,project}`` closures in
``routes/picture_sets.py`` / ``routes/characters.py`` / ``routes/projects.py``);
plan §1 goal 2 collapses that into one implementation with one test surface.

**Ownership / call sites.**

* The authz gate (:mod:`pixlstash.authz.gate`) calls these to enforce
  ``PICTURE_SCOPED`` / ``SET_SCOPED`` / ``CHARACTER_SCOPED`` / ``PROJECT_SCOPED``
  routes (and the ``body_ids`` batch routes) once the gate is enforcing.
* ``routes/pictures/_helpers.py::enforce_picture_scope`` is now a thin re-export
  of :func:`enforce_picture_scope` here, so its ~20 inline call sites (comfyui,
  tags, tag_predictions, _anomaly, _thumbnails, _crud, ...) keep importing the
  same symbol unchanged.
* The three inline ``_require_scope_allows_*`` closures delegate to
  :func:`enforce_set_scope` / :func:`enforce_character_scope` /
  :func:`enforce_project_scope` here, so there is exactly one implementation from
  the moment this module exists (principal ruling 2026-07-21, D3). Step 5 deletes
  the now-trivial shims.

**Semantics (unchanged from the code these functions replace).** An owner /
unscoped token (``token_scope is None``) always passes immediately - the checks
never narrow the owner. A resource-scoped token passes only when the requested
object is inside its grant, and an unrecognised ``resource_type`` fails closed
(403). Every check that touches the database does so through
``server.vault.db.run_immediate_read_task`` - the caller (the gate) runs these on
a threadpool worker so the blocking read never sits on the event loop.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlmodel import select

from pixlstash.db_models import (
    CharacterProjectMember,
    Face,
    PictureProjectMember,
    PictureSetMember,
    PictureSetProjectMember,
    TagSuggestion,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.filter_helpers import visible_project_ids

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Picture membership (moved verbatim from routes/pictures/_helpers.py)
# ---------------------------------------------------------------------------


def _picture_id_in_scoped_set(server, picture_id: int, set_id: int) -> bool:
    """Return True if picture_id is a member of set_id."""

    def check(session):
        return (
            session.exec(
                select(PictureSetMember).where(
                    PictureSetMember.set_id == set_id,
                    PictureSetMember.picture_id == picture_id,
                )
            ).first()
            is not None
        )

    return server.vault.db.run_immediate_read_task(check)


def _picture_id_in_scoped_character(server, picture_id: int, character_id: int) -> bool:
    """Return True if the picture has at least one face assigned to character_id."""

    def check(session):
        return (
            session.exec(
                select(Face).where(
                    Face.picture_id == picture_id,
                    Face.character_id == character_id,
                )
            ).first()
            is not None
        )

    return server.vault.db.run_immediate_read_task(check)


def _picture_id_in_scoped_project(server, picture_id: int, project_id: int) -> bool:
    """Return True if picture_id is a member of project_id."""

    def check(session):
        return (
            session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == picture_id,
                    PictureProjectMember.project_id == project_id,
                )
            ).first()
            is not None
        )

    return server.vault.db.run_immediate_read_task(check)


def enforce_picture_scope(server, request: Request, picture_id: int) -> None:
    """Raise 403 if a scoped token does not permit access to this picture."""
    scope = getattr(request.state, "token_scope", None)
    if scope is None:
        return
    if scope.resource_type == "picture_set":
        if not _picture_id_in_scoped_set(server, picture_id, scope.resource_id):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this picture",
            )
    elif scope.resource_type == "character":
        if not _picture_id_in_scoped_character(server, picture_id, scope.resource_id):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this picture",
            )
    elif scope.resource_type == "project":
        if not _picture_id_in_scoped_project(server, picture_id, scope.resource_id):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this picture",
            )
    elif scope.resource_type == "picture":
        # Single-picture share token: only that exact picture is permitted.
        if picture_id != scope.resource_id:
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this picture",
            )
    elif scope.resource_type is not None:
        raise HTTPException(
            status_code=403,
            detail="Token is not authorised for this resource type",
        )


# ---------------------------------------------------------------------------
# Picture-set membership (lifted from routes/picture_sets.py::_require_scope_allows_picture_set)
# ---------------------------------------------------------------------------


def enforce_set_scope(server, request: Request, set_id: int) -> None:
    """Raise 403 if the token scope does not cover the requested picture set.

    A ``picture_set`` token reaches only its own set; a ``project`` token reaches
    a set that belongs to its project; every other scoped ``resource_type`` is
    refused. An owner / unscoped token (``scope is None``) passes.

    Since issue #125 a set may belong to several projects, so the project branch
    tests the ``PictureSetProjectMember`` join rather than the scalar
    ``PictureSet.project_id`` (which now names only the *primary* project and
    would under-grant a legitimately shared set).
    """
    scope = getattr(request.state, "token_scope", None)
    if scope is None:
        return
    if scope.resource_type == "picture_set":
        if scope.resource_id != set_id:
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised for this picture set",
            )
    elif scope.resource_type == "project":

        def _check_set_in_project(session, sid: int, pid: int) -> bool:
            return (
                session.exec(
                    select(PictureSetProjectMember).where(
                        PictureSetProjectMember.set_id == sid,
                        PictureSetProjectMember.project_id == pid,
                    )
                ).first()
                is not None
            )

        if not server.vault.db.run_immediate_read_task(
            _check_set_in_project, set_id, scope.resource_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised for this picture set",
            )
    elif scope.resource_type is not None:
        raise HTTPException(
            status_code=403,
            detail="Token is not authorised for this resource type",
        )


# ---------------------------------------------------------------------------
# Character membership (lifted from routes/characters.py::_require_scope_allows_character)
# ---------------------------------------------------------------------------


def enforce_character_scope(server, request: Request, character_id: int) -> None:
    """Raise 403 if the token scope does not cover the requested character.

    A ``character`` token reaches only its own character; a ``project`` token
    reaches a character that belongs to its project; every other scoped
    ``resource_type`` is refused. An owner / unscoped token passes.

    Since issue #125 a character may belong to several projects, so the project
    branch tests the ``CharacterProjectMember`` join rather than the scalar
    ``Character.project_id`` (which now names only the *primary* project and would
    under-grant a legitimately shared character).
    """
    scope = getattr(request.state, "token_scope", None)
    if scope is None:
        return
    if scope.resource_type == "character":
        if scope.resource_id != character_id:
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised for this character",
            )
    elif scope.resource_type == "project":

        def check_char_project(session, cid: int, pid: int) -> bool:
            return (
                session.exec(
                    select(CharacterProjectMember).where(
                        CharacterProjectMember.character_id == cid,
                        CharacterProjectMember.project_id == pid,
                    )
                ).first()
                is not None
            )

        if not server.vault.db.run_immediate_read_task(
            check_char_project, character_id, scope.resource_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised for this character",
            )
    elif scope.resource_type is not None:
        raise HTTPException(
            status_code=403,
            detail="Token is not authorised for this resource type",
        )


# ---------------------------------------------------------------------------
# Project membership (lifted from routes/projects.py::_require_scope_allows_project)
# ---------------------------------------------------------------------------


def enforce_project_scope(server, request: Request, project_id: int) -> None:
    """Raise 403 if the token scope does not cover the requested project.

    A ``project`` token reaches only its own project; every other scoped
    ``resource_type`` is refused. An owner / unscoped token passes. Pure
    in-memory check (no database read).
    """
    scope = getattr(request.state, "token_scope", None)
    if scope is None:
        return
    if scope.resource_type == "project":
        if scope.resource_id != project_id:
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised for this project",
            )
    elif scope.resource_type is not None:
        raise HTTPException(
            status_code=403,
            detail="Token is not authorised for this resource type",
        )


# ---------------------------------------------------------------------------
# Project *path* scope: a project named in a path segment (issue #708)
# ---------------------------------------------------------------------------

# The single refusal a scoped token gets for a project it may not see, whatever
# the reason it may not see it. It is deliberately ONE constant: the whole point
# of :func:`enforce_project_path_scope` is that the *content* of the answer
# carries no information about the project space, so "no such project", "another
# project" and "a project that does not hold the resource you named" must be
# spelled identically. That covers status, body and headers - it does NOT make the
# refusal constant-time; see the residual timing channel in that function's
# docstring.
PROJECT_PATH_REFUSAL_DETAIL = "Token is not authorised for this project"


def enforce_project_path_scope(
    server, request: Request, project_id: int | None
) -> None:
    """Refuse - identically - a project named in the path that the token cannot see.

    The four name-derived routes (§16.1 residual inline exception) take a project
    in a **path segment** rather than in the ``project_id`` query parameter, so
    :func:`enforce_project_filter_scope` (which reads ``request.query_params``)
    cannot see it. Each one resolves that segment to a row *before* any scope
    check runs, and its ordinary error branches then answer from the project
    space the token is not allowed to probe (#708 condition 2)::

        GET /projects/Alpha/picture_sets/SharedSet        -> 200
        GET /projects/Gamma/picture_sets/SharedSet        -> 404 "Picture set not found"
        GET /projects/DoesNotExist/picture_sets/SharedSet -> 404 "Project not found"

    Three distinguishable answers told a picture_set-scoped token which projects
    exist and which of them hold its set - the exact disclosure
    ``visible_project_ids`` exists to prevent, arriving through a path segment
    instead of a query parameter. ``GET /projects/{id_or_name}`` had the same
    shape one rung up (403 for an existing project, 404 for a missing one, by
    numeric id *and* by name).

    So this is called with the **resolved** id - or ``None`` when the segment
    resolved to nothing - before the handler asks any membership question, and a
    token that may not see the project gets one indistinguishable 403 in all
    three cases. An owner / unscoped token (``visible_project_ids`` returns
    ``None``) is not restricted and the caller's own 404 branch still runs, so
    the owner-facing behaviour of these routes is unchanged.

    **Residual: the refusal is uniform in content, not in time (accepted risk).**
    "Indistinguishable" above means status, body and headers - it does *not* mean
    constant-time, and the difference is measurable. Both ``id_or_name`` routes
    (``GET /projects/{id_or_name}`` and ``.../picture_sets``) resolve a numeric
    segment with ``session.get(Project, int(v))`` and, when that returns nothing,
    fall through to a second name-based ``SELECT``. So a numeric segment naming a
    project that exists costs one query and one naming no project costs two,
    before both arrive at the same 403. Reproduced with a ``picture_set``-scoped
    token, 400 interleaved request pairs, status and body identical in every
    pair: the missing-project median ran +141 µs (+6.5 %) and +210 µs (+9.6 %)
    over two runs here, and +416 µs (+19.8 %) in the second #708 review's
    800-sample run. The magnitude is machine- and load-dependent; the *direction*
    is structural and reproduces.

    This is **not** equalised, deliberately: making the lookup constant-time is a
    runtime change to a hot read path, and the channel is worth far less than the
    content oracle it replaced. Reading it needs many samples per bit against a
    local, single-user server, and what it leaks is one bit - "is there a project
    with this id" - not the membership fact. A project that exists and holds the
    caller's resource and one that exists and does not cost the same single
    query, so the ``P1``/``P3`` distinction that motivated this function is not
    recoverable through the timing channel.

    - **Owner:** backend (``senior-backend-developer``), tracked on issue #708.
    - **Revisit trigger - any of:** (a) a resource-scoped share token becomes
      reachable by someone other than the owner (multi-user, or a hosted demo
      handing out scoped tokens), which turns a local timing channel into a remote
      one; (b) these routes gain a lookup whose cost varies with *membership*
      rather than mere existence, which would widen the channel from one bit of
      existence to the membership fact itself; (c) the name lookup becomes
      materially more expensive than the id lookup (an index change, a growing
      project table), which raises the signal above the noise floor.

    Args:
        server: The server instance (unused; kept for signature symmetry with the
            other membership helpers).
        request: The current FastAPI request.
        project_id: The id the path segment resolved to, or ``None`` if it
            resolved to no row. ``None`` is refused for a scoped token precisely
            so that "does not exist" cannot be told apart from "you may not see
            it".

    Raises:
        HTTPException: 403 with :data:`PROJECT_PATH_REFUSAL_DETAIL` when the
            caller is scoped and ``project_id`` is not among the ids it may see.
    """
    visible = visible_project_ids(server, request)
    if visible is None:
        return
    if project_id is None or project_id not in visible:
        logger.warning(
            "enforce_project_path_scope: scoped token named a project it may not "
            "see on %s %s (resolved id %r, visible %s); refusing with the uniform "
            "403 so existence is not disclosed",
            request.method,
            request.url.path,
            project_id,
            sorted(visible),
        )
        raise HTTPException(
            status_code=403,
            detail=PROJECT_PATH_REFUSAL_DETAIL,
        )


# ---------------------------------------------------------------------------
# Project *filter* scope: the `project_id` query parameter (issue #708)
# ---------------------------------------------------------------------------

# Query parameters that name a project the caller wants to filter by. Every
# route that takes one takes it under one of these names; a new spelling must be
# added here, or the gate will not see it. That is machine-checked:
# ``tests/test_architecture_guardrails.py::test_project_filter_params_are_declared``
# walks every mounted route's declared query parameters and fails the build on a
# project-ish name that is not listed here. It is a check over *declared* params
# with *project-ish wire names*; a parameter aliased to something that does not
# say "project", or read straight off ``request.query_params`` without being
# declared, is invisible to it - and in the alias case, to the gate as well. The
# three blind spots are enumerated in that test's docstring; read them before
# treating "machine-checked" as "total".
#
# ``project_ids`` is declared ahead of any route using it; listing a name no
# route takes costs nothing, whereas a route taking a name not listed here is a
# hole. The guardrail is deliberately one-directional for that reason.
PROJECT_FILTER_QUERY_PARAMS: tuple[str, ...] = ("project_id", "project_ids")


def enforce_project_filter_scope(server, request: Request) -> None:
    """Raise 403 when a scoped token filters by a project it may not see.

    ``visible_project_ids`` decides which project ids a token may *learn about*:
    its own for a ``project`` token, none at all for a ``character`` /
    ``picture_set`` / ``picture`` token (issue #125 / R1b). Narrowing the ids in
    the *response* is only half the rule - a ``project_id`` **filter** turns any
    list, count, or summary route into a membership oracle for the same hidden
    facts: ``GET /picture_sets?project_id=7`` returning a row tells a set-scoped
    token that project 7 exists and that its set is filed under it, which
    ``GET /projects/7`` (403) deliberately refuses to say (issue #708 F2/F3 and
    their siblings on ``/pictures``, ``/pictures/count``, ``/pictures/stream``,
    ``/pictures/stats``, ``/picture_sets/{id}`` and ``/characters/{id}/summary``).

    So the gate answers the filter the same way it answers the payload: a
    resource-scoped token may name **only** a project id it can already see.
    Anything else - another project's id, a non-existent id, or the
    ``UNASSIGNED`` sentinel (which asks the complementary question, "which of my
    things are in *no* project") - is refused with the same 403 regardless of
    whether the project exists, so the refusal itself is not an oracle either.

    An owner / unscoped token (``visible_project_ids`` returns ``None``) is never
    restricted, and a request that carries no project filter is a no-op.

    **Boundary - this reads ``request.query_params`` and nothing else.** A new
    route is covered the day it mounts only if it takes its project in the *query
    string*. A project named in a JSON body, a form field, or a path segment is
    outside this chokepoint:

    * **Body / form.** ``POST /pictures/import``, ``POST /pictures/import/staging``,
      ``POST /reviews``, ``POST /tag_suggestions/bulk-accept`` and
      ``POST /tag_suggestions/scan`` declare a project in their payload, and
      ``PATCH /pictures/project``, ``POST /characters``, ``POST /picture_sets``,
      ``PATCH /picture_sets/{id}`` and ``POST /comfyui/run_t2i`` read one out of an
      untyped ``payload: dict`` body. **The second list is hand-made and cannot be
      shown complete** - a ``dict`` annotation declares no keys, so nothing can
      enumerate what those handlers read; treat it as "the ones we know about",
      not "the ones there are". None of the ten is reachable by a resource-scoped
      token today, but only because a resource-scoped token can only be minted
      ``READ`` (§16.2 item 4) and the auth middleware blocks a non-``GET`` for a
      ``READ`` token unless the path is in ``auth.READ_SAFE_POST_PATHS`` - which
      none of these is. **Adding one of them to that frozenset would open the
      hole**; the payload half is not checked anywhere.
    * **Path segment.** Handled separately by :func:`enforce_project_path_scope`
      for the name-derived routes, and by the registry's ``PROJECT_SCOPED``
      declarations for the ``/projects/{project_id}/...`` family.
    * **Value-typed discriminators.** A pair such as
      ``?resource_type=project&resource_id=N`` names a project without any
      parameter *called* project. No name-based check can see that shape; it is
      an argument for keeping the discriminator pattern off read-filter routes.

    ``tests/test_architecture_guardrails.py::test_project_references_outside_the_query_chokepoint``
    pins the *declared* half of the body/form list above so a new typed field has
    to be looked at, and separately asserts that no opaque-bodied route becomes
    ``READ``-reachable without being hand-vetted. Read that test's docstring for
    which of its assertions are arithmetic and which are acknowledgments.

    Args:
        server: The server instance (unused; kept for signature symmetry with the
            other membership helpers).
        request: The current FastAPI request.
    """
    visible = visible_project_ids(server, request)
    if visible is None:
        return

    for param in PROJECT_FILTER_QUERY_PARAMS:
        for raw in request.query_params.getlist(param):
            if raw is None or raw == "":
                continue
            try:
                project_id = int(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "enforce_project_filter_scope: scoped token passed "
                    "non-numeric %s=%r on %s %s; refusing (a scoped token may "
                    "only name a project it can see)",
                    param,
                    raw,
                    request.method,
                    request.url.path,
                )
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised to filter by this project",
                )
            if project_id not in visible:
                logger.warning(
                    "enforce_project_filter_scope: scoped token requested "
                    "%s=%d on %s %s but may only see %s; refusing",
                    param,
                    project_id,
                    request.method,
                    request.url.path,
                    sorted(visible),
                )
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised to filter by this project",
                )


# ---------------------------------------------------------------------------
# Id resolvers: map a non-picture route id to the picture id it authorises on
# ---------------------------------------------------------------------------


def resolve_tag_suggestion_picture_id(server, raw_id) -> int | None:
    """Resolve a ``tag_suggestions`` route id to the picture it concerns.

    The ``tag_suggestions`` single-item mutators key on a ``suggestion_id`` and
    ``bulk-reopen`` on a body list of suggestion ids, but they authorise on
    **picture** scope - a suggestion belongs to exactly one picture
    (``TagSuggestion.picture_id``). This maps one raw id to that picture id so the
    gate can run :func:`enforce_picture_scope` on it (matrix §N4).

    Returns:
        The picture id for the suggestion, or ``None`` when the id is malformed or
        the suggestion does not exist (the caller fails closed for a scoped
        token - a suggestion the token cannot resolve is not one it may act on).
    """
    try:
        suggestion_id = int(raw_id)
    except (TypeError, ValueError):
        logger.warning(
            "resolve_tag_suggestion_picture_id: non-integer suggestion id %r; "
            "cannot resolve to a picture (failing closed)",
            raw_id,
        )
        return None

    def _lookup(session):
        suggestion = session.get(TagSuggestion, suggestion_id)
        return suggestion.picture_id if suggestion is not None else None

    return server.vault.db.run_immediate_read_task(_lookup)


# Registry of named id resolvers referenced by ``RoutePolicy.id_resolver``. A
# resolver maps one raw route id (path or body) to the picture id the route
# authorises on. Keep this closed and small - a new resolver is a deliberate,
# reviewable addition, exactly like the closed AccessPolicy enum.
ID_RESOLVERS = {
    "tag_suggestion": resolve_tag_suggestion_picture_id,
}


__all__ = [
    "enforce_picture_scope",
    "enforce_set_scope",
    "enforce_character_scope",
    "enforce_project_scope",
    "enforce_project_filter_scope",
    "resolve_tag_suggestion_picture_id",
    "ID_RESOLVERS",
    "PROJECT_FILTER_QUERY_PARAMS",
]
