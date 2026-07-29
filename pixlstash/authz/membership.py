"""Object-membership checks for the centralised authorization gate.

Phase 1 Step 4 of the backend authorization refactor (backend refactor plan
§3.5 / §3.7). This module is the **single home** for the per-object scope
membership ladder — the ``token_scope`` branching that decides whether a scoped
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
unscoped token (``token_scope is None``) always passes immediately — the checks
never narrow the owner. A resource-scoped token passes only when the requested
object is inside its grant, and an unrecognised ``resource_type`` fails closed
(403). Every check that touches the database does so through
``server.vault.db.run_immediate_read_task`` — the caller (the gate) runs these on
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
# Id resolvers: map a non-picture route id to the picture id it authorises on
# ---------------------------------------------------------------------------


def resolve_tag_suggestion_picture_id(server, raw_id) -> int | None:
    """Resolve a ``tag_suggestions`` route id to the picture it concerns.

    The ``tag_suggestions`` single-item mutators key on a ``suggestion_id`` and
    ``bulk-reopen`` on a body list of suggestion ids, but they authorise on
    **picture** scope — a suggestion belongs to exactly one picture
    (``TagSuggestion.picture_id``). This maps one raw id to that picture id so the
    gate can run :func:`enforce_picture_scope` on it (matrix §N4).

    Returns:
        The picture id for the suggestion, or ``None`` when the id is malformed or
        the suggestion does not exist (the caller fails closed for a scoped
        token — a suggestion the token cannot resolve is not one it may act on).
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
# authorises on. Keep this closed and small — a new resolver is a deliberate,
# reviewable addition, exactly like the closed AccessPolicy enum.
ID_RESOLVERS = {
    "tag_suggestion": resolve_tag_suggestion_picture_id,
}


__all__ = [
    "enforce_picture_scope",
    "enforce_set_scope",
    "enforce_character_scope",
    "enforce_project_scope",
    "resolve_tag_suggestion_picture_id",
    "ID_RESOLVERS",
]
