"""Shared filter helpers for picture query construction."""

from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import aliased
from sqlmodel import Session
import numpy as np

from pixlstash.db_models import (
    CharacterProjectMember,
    Face,
    Picture,
    PictureProjectMember,
    PictureSetMember,
    PictureSetProjectMember,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.scope_table import scope_id_subquery

logger = get_logger(__name__)

VALID_COMBINE_MODES: frozenset[str] = frozenset(
    {"mean", "max", "min", "harmonic_mean", "geometric_mean"}
)


def combine_likeness_scores(scores: np.ndarray, combine: str) -> np.ndarray:
    """Combine per-query similarity scores across multiple query images.

    Args:
        scores: Shape ``(Q, N)`` - Q query images, N candidates.  For a
            single query pass shape ``(1, N)``; the result is still ``(N,)``.
        combine: One of ``"mean"``, ``"max"``, ``"min"``,
            ``"harmonic_mean"``, or ``"geometric_mean"``.

    Returns:
        Shape ``(N,)`` combined scores in the same range as the input.
    """
    if scores.shape[0] == 1:
        return scores[0]

    if combine == "max":
        return scores.max(axis=0)
    if combine == "min":
        return scores.min(axis=0)

    # For harmonic and geometric mean, shift to (0, 1] to ensure positivity.
    # Cosine similarities are in [-1, 1]; (x + 1) / 2 maps them to [0, 1].
    if combine in ("harmonic_mean", "geometric_mean"):
        shifted = (scores + 1.0) / 2.0  # (Q, N) in [0, 1]
        shifted = np.maximum(shifted, 1e-10)
        if combine == "geometric_mean":
            combined_shifted = np.exp(np.log(shifted).mean(axis=0))
        else:  # harmonic_mean
            combined_shifted = 1.0 / (1.0 / shifted).mean(axis=0)
        return combined_shifted * 2.0 - 1.0  # unshift back to [-1, 1]

    # Default: arithmetic mean
    return scores.mean(axis=0)


def normalize_set_mode(value: str | None) -> str:
    """Normalise a raw set_mode query parameter to a canonical string.

    Args:
        value: The raw string from the request, or None.

    Returns:
        One of ``"union"``, ``"intersection"``, ``"difference"``, or ``"xor"``.

    Raises:
        HTTPException: 400 if the value is not one of the accepted modes.
    """
    mode = (value or "union").strip().lower()
    if mode not in {"union", "intersection", "difference", "xor"}:
        raise HTTPException(status_code=400, detail="Invalid set_mode")
    return mode


def collect_set_filter_ids(
    *,
    set_id_value: int | str | None,
    set_ids_values: list[int | str] | None,
) -> list[int]:
    """Merge the singular ``set_id`` and plural ``set_ids`` query params.

    Args:
        set_id_value: Optional single set id.
        set_ids_values: Optional list of set ids.

    Returns:
        Deduplicated, ordered list of positive integer set ids.
    """
    raw_values: list[int | str] = []
    if set_id_value is not None and str(set_id_value).strip() != "":
        raw_values.append(set_id_value)
    if set_ids_values:
        raw_values.extend(set_ids_values)

    normalized: list[int] = []
    seen: set[int] = set()
    for raw in raw_values:
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    return normalized


def fetch_set_candidate_ids(
    session: Session,
    *,
    set_ids: list[int],
    set_mode: str,
    deleted_only: bool,
) -> set[int]:
    """Return picture ids matching *set_ids* under *set_mode*.

    Args:
        session: Active database session.
        set_ids: Non-empty list of set ids to filter by.
        set_mode: One of ``"union"``, ``"intersection"``, ``"difference"``,
            or ``"xor"``.
        deleted_only: When ``True`` consider only soft-deleted pictures.

    Returns:
        Set of picture ids that satisfy the filter.
    """
    if not set_ids:
        return set()

    rows = session.exec(
        select(PictureSetMember.set_id, PictureSetMember.picture_id)
        .join(Picture, Picture.id == PictureSetMember.picture_id)
        .where(PictureSetMember.set_id.in_(set_ids))
        .where(
            Picture.deleted.is_(True) if deleted_only else Picture.deleted.is_(False)
        )
    ).all()

    members_by_set: dict[int, set[int]] = {sid: set() for sid in set_ids}
    for set_id_row, picture_id_row in rows:
        if picture_id_row is None:
            continue
        members_by_set.setdefault(int(set_id_row), set()).add(int(picture_id_row))

    if set_mode == "intersection":
        intersection: set[int] | None = None
        for sid in set_ids:
            current = members_by_set.get(sid, set())
            if intersection is None:
                intersection = set(current)
            else:
                intersection &= current
        return intersection or set()

    if set_mode == "difference":
        if not set_ids:
            return set()
        first_set = members_by_set.get(set_ids[0], set())
        rest: set[int] = set()
        for sid in set_ids[1:]:
            rest |= members_by_set.get(sid, set())
        return first_set - rest

    if set_mode == "xor":
        xor_union: set[int] = set()
        for sid in set_ids:
            xor_union |= members_by_set.get(sid, set())
        xor_intersection: set[int] | None = None
        for sid in set_ids:
            cur = members_by_set.get(sid, set())
            xor_intersection = (
                set(cur) if xor_intersection is None else xor_intersection & cur
            )
        return xor_union - (xor_intersection or set())

    union_ids: set[int] = set()
    for sid in set_ids:
        union_ids |= members_by_set.get(sid, set())
    return union_ids


def project_membership_exists_clause(project_id: int, picture_model=Picture):
    """Return a SQLAlchemy EXISTS clause matching pictures in *project_id*.

    Args:
        project_id: The project to check membership for.
        picture_model: SQLModel class to compare against (defaults to
            ``Picture``).

    Returns:
        A SQLAlchemy ``exists()`` expression.
    """
    return exists(
        select(PictureProjectMember.picture_id).where(
            PictureProjectMember.picture_id == picture_model.id,
            PictureProjectMember.project_id == project_id,
        )
    )


def project_unassigned_clause(picture_model=Picture):
    """Return a SQLAlchemy NOT-EXISTS clause for pictures with no project.

    Args:
        picture_model: SQLModel class to compare against (defaults to
            ``Picture``).

    Returns:
        A negated SQLAlchemy ``exists()`` expression.
    """
    return ~exists(
        select(PictureProjectMember.picture_id).where(
            PictureProjectMember.picture_id == picture_model.id
        )
    )


def fetch_scope_allowed_picture_ids(server, request) -> set[int] | None:
    """Return picture IDs accessible to the current token scope.

    Args:
        server: The server instance.
        request: The current FastAPI request.

    Returns:
        ``None`` when the token has unrestricted access (no scope set).
        A ``set[int]`` of allowed picture IDs for scoped tokens.
        An empty ``set`` when the scope resource type is unrecognised
        (fail-safe: grants no access rather than full access).
    """
    token_scope = getattr(request.state, "token_scope", None)
    if token_scope is None or token_scope.resource_type is None:
        return None

    resource_id = token_scope.resource_id

    if token_scope.resource_type == "picture_set":

        def _fetch_set(session: Session, set_id: int) -> set[int]:
            return {
                int(r[0])
                for r in session.exec(
                    select(PictureSetMember.picture_id).where(
                        PictureSetMember.set_id == set_id
                    )
                ).all()
            }

        return server.vault.db.run_immediate_read_task(_fetch_set, resource_id)

    if token_scope.resource_type == "character":

        def _fetch_char(session: Session, character_id: int) -> set[int]:
            return {
                int(r[0])
                for r in session.exec(
                    select(Face.picture_id).where(Face.character_id == character_id)
                ).all()
            }

        return server.vault.db.run_immediate_read_task(_fetch_char, resource_id)

    if token_scope.resource_type == "project":

        def _fetch_project(session: Session, project_id: int) -> set[int]:
            return {
                int(r[0])
                for r in session.exec(
                    select(PictureProjectMember.picture_id).where(
                        PictureProjectMember.project_id == project_id
                    )
                ).all()
            }

        return server.vault.db.run_immediate_read_task(_fetch_project, resource_id)

    if token_scope.resource_type == "picture":
        # Single-picture share token: only that specific picture is allowed.
        return {int(resource_id)}

    logger.warning(
        "fetch_scope_allowed_picture_ids: unrecognised token_scope resource_type %r;"
        " returning empty set (no access)",
        token_scope.resource_type,
    )
    return set()


def fetch_scope_allowed_set_ids(server, request) -> set[int] | None:
    """Return picture-set IDs the current token scope may *learn about*.

    Object-level scope authorizes a *picture*; it says nothing about the related
    entities named in that picture's payload. A picture-set-scoped token can
    legitimately read a picture that also belongs to some other, private set -
    and without this helper the response would hand over that set's id and
    user-authored name, which the token cannot obtain from ``GET /picture_sets``.
    Any handler that embeds set identity in a picture-derived payload must filter
    it through this function first.

    The policy mirrors, and is the single source of truth for, the set-visibility
    ladder already implemented by ``GET /picture_sets`` and
    ``GET /picture_sets/locked-members`` in :mod:`pixlstash.routes.picture_sets`:
    a ``picture_set`` token sees exactly its own set, a ``project`` token sees
    that project's sets, and every other scoped token sees no sets at all.

    Args:
        server: The server instance.
        request: The current FastAPI request.

    Returns:
        ``None`` when the token is unscoped / owner (no restriction - the caller
        must not filter). A ``set[int]`` of visible set IDs for a scoped token,
        which may be empty. An empty ``set`` for a ``character``, ``picture``, or
        unrecognised ``resource_type`` (fail-closed: no set is disclosed).
    """
    token_scope = getattr(request.state, "token_scope", None)
    if token_scope is None or token_scope.resource_type is None:
        return None

    resource_id = token_scope.resource_id

    if token_scope.resource_type == "picture_set":
        return {int(resource_id)}

    if token_scope.resource_type == "project":

        def _fetch_project_sets(session: Session, project_id: int) -> set[int]:
            # Issue #125: a set may be in several projects, so membership comes
            # from the join table, not the primary-project FK.
            return {
                int(r[0])
                for r in session.exec(
                    select(PictureSetProjectMember.set_id).where(
                        PictureSetProjectMember.project_id == project_id
                    )
                ).all()
            }

        return server.vault.db.run_immediate_read_task(_fetch_project_sets, resource_id)

    # character / picture / anything unrecognised: no set visibility at all.
    # Deliberately fail-closed rather than defaulting to disclosure.
    logger.debug(
        "fetch_scope_allowed_set_ids: token_scope resource_type %r has no picture-set"
        " visibility; returning empty set",
        token_scope.resource_type,
    )
    return set()


def fetch_scope_allowed_character_ids(server, request) -> set[int] | None:
    """Return character IDs accessible to the current token scope.

    Args:
        server: The server instance.
        request: The current FastAPI request.

    Returns:
        ``None`` when the token has unrestricted access (no scope set).
        A ``set[int]`` of allowed character IDs for scoped tokens.
        An empty ``set`` when the scope resource type is unrecognised
        (fail-safe: grants no access rather than full access).
    """
    token_scope = getattr(request.state, "token_scope", None)
    if token_scope is None or token_scope.resource_type is None:
        return None

    resource_id = token_scope.resource_id

    if token_scope.resource_type == "character":
        return {int(resource_id)}

    if token_scope.resource_type == "project":

        def _fetch_project_chars(session: Session, project_id: int) -> set[int]:
            # Issue #125: a character may be in several projects, so membership
            # comes from the join table, not the primary-project FK.
            return {
                int(r[0])
                for r in session.exec(
                    select(CharacterProjectMember.character_id).where(
                        CharacterProjectMember.project_id == project_id
                    )
                ).all()
            }

        return server.vault.db.run_immediate_read_task(
            _fetch_project_chars, resource_id
        )

    if token_scope.resource_type == "picture_set":

        def _fetch_set_chars(session: Session, set_id: int) -> set[int]:
            return {
                int(r[0])
                for r in session.exec(
                    select(Face.character_id)
                    .join(
                        PictureSetMember, Face.picture_id == PictureSetMember.picture_id
                    )
                    .where(
                        PictureSetMember.set_id == set_id,
                        Face.character_id.is_not(None),
                    )
                    .distinct()
                ).all()
            }

        return server.vault.db.run_immediate_read_task(_fetch_set_chars, resource_id)

    if token_scope.resource_type == "picture":

        def _fetch_picture_chars(session: Session, picture_id: int) -> set[int]:
            return {
                int(r[0])
                for r in session.exec(
                    select(Face.character_id).where(
                        Face.picture_id == picture_id,
                        Face.character_id.is_not(None),
                    )
                ).all()
            }

        return server.vault.db.run_immediate_read_task(
            _fetch_picture_chars, resource_id
        )

    logger.warning(
        "fetch_scope_allowed_character_ids: unrecognised token_scope resource_type %r;"
        " returning empty set (no access)",
        token_scope.resource_type,
    )
    return set()


def visible_project_ids(server, request) -> set[int] | None:
    """Return project IDs the current token scope may *learn about*.

    A character or picture set may belong to several projects (issue #125) and
    every serialisation of one carries a ``project_ids`` list. That list is
    *membership metadata about other projects*, not part of the object the token
    was granted: a token scoped to one character legitimately reads that
    character, but the complete membership list tells it how many other projects
    the character is filed under and what their ids are - facts it can obtain
    from no endpoint it is allowed to call (``GET /projects/{other_id}`` is
    project-scoped and 403s). Any handler that serialises ``project_ids`` must
    intersect it with this function's result first.

    The ladder mirrors :func:`fetch_scope_allowed_set_ids`: a ``project`` token
    sees exactly its own project, and every other scoped token sees no projects
    at all.

    Args:
        server: The server instance (unused; kept for signature symmetry with the
            other scope helpers, which need it to run a read task).
        request: The current FastAPI request.

    Returns:
        ``None`` when the token is unscoped / owner (no restriction - the caller
        must not filter). ``{project_id}`` for a ``project``-scoped token. An
        empty ``set`` for a ``character``, ``picture_set``, ``picture``, or
        unrecognised ``resource_type`` (fail-closed: no project id is disclosed).
    """
    token_scope = getattr(request.state, "token_scope", None)
    if token_scope is None or token_scope.resource_type is None:
        return None

    if token_scope.resource_type == "project":
        return {int(token_scope.resource_id)}

    # character / picture_set / picture / anything unrecognised: no project
    # visibility at all. Deliberately fail-closed rather than defaulting to
    # disclosure.
    logger.debug(
        "visible_project_ids: token_scope resource_type %r has no project"
        " visibility; returning empty set",
        token_scope.resource_type,
    )
    return set()


def filter_visible_project_ids(
    project_ids: Iterable[int] | None, visible: set[int] | None
) -> list[int]:
    """Narrow an entity's ``project_ids`` to what the caller may see.

    Args:
        project_ids: The entity's full project membership, from the join table.
        visible: The result of :func:`visible_project_ids` - ``None`` for an
            owner / unscoped token (no narrowing), otherwise the set of project
            ids the token may learn about.

    Returns:
        A sorted list of project ids: the full membership for an owner, the
        intersection with ``visible`` for a scoped token.
    """
    ids = sorted({int(pid) for pid in (project_ids or []) if pid is not None})
    if visible is None:
        return ids
    return [pid for pid in ids if pid in visible]


def narrow_project_fields(
    payload: dict, project_ids: Iterable[int] | None, visible: set[int] | None
) -> dict:
    """Set scope-narrowed ``project_ids`` *and* ``project_id`` on *payload*.

    The legacy scalar ``project_id`` must be derived from the narrowed list,
    never serialised straight off the model: the stored scalar names the
    entity's *primary* project, which a token scoped to a secondary project
    (or to the entity itself) has no grant to learn (issue #125 / R1b).

    Args:
        payload: The response dict being built; mutated in place.
        project_ids: The entity's full project membership, from the join table.
        visible: The result of :func:`visible_project_ids`.

    Returns:
        The same *payload*, with ``project_ids`` narrowed and ``project_id``
        set to the first narrowed id (the primary project when the caller may
        see it) or ``None`` when none are visible.
    """
    narrowed = filter_visible_project_ids(project_ids, visible)
    payload["project_ids"] = narrowed
    payload["project_id"] = narrowed[0] if narrowed else None
    return payload


def picture_project_ids_map(
    session: Session, picture_ids: Iterable[int]
) -> dict[int, list[int]]:
    """Return every project each of *picture_ids* belongs to, lowest id first.

    The picture twin of ``character_project_ids`` / ``picture_set_project_ids``
    in ``project_membership_service``, batched: one query for a whole page of
    rows rather than one per row. The id scope goes through
    :func:`scope_id_subquery` because a page can be a whole picture set, which
    would otherwise bind one SQL variable per member.

    Args:
        session: Active database session.
        picture_ids: The pictures to look up.

    Returns:
        ``picture_id -> [project_id, ...]``. A picture with no membership is
        absent from the mapping.
    """
    ids = {int(pid) for pid in picture_ids if pid is not None}
    if not ids:
        return {}
    scope = scope_id_subquery(session, ids, name="_pixlstash_picture_project_ids")
    rows = session.exec(
        select(PictureProjectMember.picture_id, PictureProjectMember.project_id).where(
            PictureProjectMember.picture_id.in_(scope)
        )
    ).all()
    grouped: dict[int, list[int]] = {}
    for picture_id, project_id in rows:
        if picture_id is None or project_id is None:
            continue
        grouped.setdefault(int(picture_id), []).append(int(project_id))
    for project_list in grouped.values():
        project_list.sort()
    return grouped


def narrow_picture_project_ids(server, request, payloads: Iterable[dict]) -> None:
    """Narrow the scalar ``project_id`` on serialised picture rows, in place.

    The picture-row twin of :func:`narrow_project_fields`, minus the
    ``project_ids`` list a picture payload does not carry. ``Picture.project_id``
    is a real column and rides in ``Picture.metadata_fields()``, so every payload
    built from that projection serialises the picture's *primary* project id
    straight off the model. That is a fact a token scoped to the picture, to a
    set, or to a *secondary* project has no grant to learn, and cannot obtain
    from ``GET /projects/{id}``, which 403s it (issue #719, backend architecture
    §16.6).

    Owners and unscoped tokens return on the first line: their payload is
    untouched and no membership query runs, so neither the response nor the cost
    of their request changes.

    Args:
        server: The server instance, used to run the membership read.
        request: The current FastAPI request.
        payloads: Serialised picture rows, mutated in place. A row without a
            ``project_id`` key is left alone; a row that has one must also carry
            its ``id``, or the scalar is cleared rather than guessed.
    """
    visible = visible_project_ids(server, request)
    if visible is None:
        return
    rows = [row for row in payloads if isinstance(row, dict) and "project_id" in row]
    if not rows:
        return
    picture_ids = {int(row["id"]) for row in rows if row.get("id") is not None}
    membership = (
        server.vault.db.run_immediate_read_task(picture_project_ids_map, picture_ids)
        if picture_ids
        else {}
    )
    for row in rows:
        row_id = row.get("id")
        if row_id is None:
            logger.warning(
                "narrow_picture_project_ids: picture row carries project_id but no"
                " id; clearing the scalar rather than disclosing it unnarrowed"
            )
            row["project_id"] = None
            continue
        narrowed = filter_visible_project_ids(membership.get(int(row_id), []), visible)
        row["project_id"] = narrowed[0] if narrowed else None


def narrow_project_assignments(
    assignments: dict[int, list[int]], visible: set[int] | None
) -> dict[int, list[int]]:
    """Drop the project keys a caller may not see from a membership mapping.

    The sibling of :func:`narrow_project_fields` for the one payload shape that
    is *keyed* by project id rather than carrying a ``project_ids`` list:
    ``POST /projects/membership`` answers "which of these pictures belong to
    which project". Every key is a project id, so an unnarrowed mapping tells a
    ``picture_set``- or ``picture``-scoped token which projects exist and which
    of its pictures are filed under them - the facts
    :func:`visible_project_ids` exists to withhold (issue #125 / R1b, #708 F1).

    Args:
        assignments: ``project_id -> [picture_id, ...]``, straight from the join
            table.
        visible: The result of :func:`visible_project_ids` - ``None`` for an
            owner / unscoped token (no narrowing).

    Returns:
        A new mapping containing only the visible projects, each with its picture
        ids sorted. The owner's mapping is returned whole.
    """
    return {
        int(project_id): sorted({int(pid) for pid in picture_ids})
        for project_id, picture_ids in assignments.items()
        if visible is None or int(project_id) in visible
    }


def _project_scope_picture_ids(session: Session, project_id: int) -> set[int]:
    """Picture ids that are members of *project_id* (excluding soft-deleted)."""
    rows = session.exec(
        select(Picture.id)
        .where(project_membership_exists_clause(project_id, Picture))
        .where(Picture.deleted.is_(False))
    ).all()
    return {int(r[0]) for r in rows if r[0] is not None}


def _set_scope_picture_ids(session: Session, set_id: int) -> set[int]:
    """Picture ids that are members of *set_id* (excluding soft-deleted)."""
    rows = session.exec(
        select(PictureSetMember.picture_id)
        .join(Picture, Picture.id == PictureSetMember.picture_id)
        .where(PictureSetMember.set_id == set_id)
        .where(Picture.deleted.is_(False))
    ).all()
    return {int(r[0]) for r in rows if r[0] is not None}


def _character_scope_picture_ids(session: Session, character_id: str) -> set[int]:
    """Picture ids matching a character filter (excluding soft-deleted).

    ``"UNASSIGNED"`` means a picture that has at least one face whose
    ``character_id`` is NULL and *no* face assigned to any character - the same
    EXISTS/NOT-EXISTS clause used by the picture-scoring queries (see
    ``pixlstash.scoring``). A numeric id matches pictures having a Face
    with that ``character_id``.
    """
    base = (
        select(Face.picture_id)
        .join(Picture, Picture.id == Face.picture_id)
        .where(Picture.deleted.is_(False))
    )
    if character_id == "UNASSIGNED":
        other_face = aliased(Face)
        query = base.where(Face.character_id.is_(None)).where(
            ~exists(
                select(other_face.id)
                .where(
                    other_face.picture_id == Face.picture_id,
                    other_face.character_id.is_not(None),
                )
                .correlate(Face)
            )
        )
    else:
        query = base.where(Face.character_id == int(character_id))
    rows = session.exec(query).all()
    return {int(r[0]) for r in rows if r[0] is not None}


def fetch_tag_review_scope_picture_ids(
    session: Session,
    *,
    project_id: int | None = None,
    set_id: int | None = None,
    character_id: str | None = None,
) -> set[int] | None:
    """Resolve the tag-review scope filters to an intersection of picture ids.

    Each provided dimension (project / picture-set / character) is resolved to the
    set of picture ids it matches, and the dimensions are AND-ed together by
    intersection. This is the central builder for narrowing the tag-suggestion
    review queue to a project, a set, and/or a character.

    Args:
        session: Active database session.
        project_id: Optional project id; pictures that are members of the project.
        set_id: Optional picture-set id; pictures that are members of the set.
        character_id: Optional character id as a string, or the literal
            ``"UNASSIGNED"``. A numeric id matches pictures with a Face for that
            character; ``"UNASSIGNED"`` matches pictures with an unassigned face
            and no assigned face.

    Returns:
        ``None`` when no dimension is provided (no scope - caller should not
        filter). Otherwise the intersection of the provided dimensions' picture
        ids; an empty set is a valid result (e.g. an empty set, an unknown id, or
        dimensions that do not overlap) and means "no in-scope pictures".

    Notes:
        All dimensions exclude soft-deleted pictures (``Picture.deleted == False``),
        consistent with the other helpers in this module.
    """
    result: set[int] | None = None

    if project_id is not None:
        result = _project_scope_picture_ids(session, project_id)

    if set_id is not None:
        set_ids = _set_scope_picture_ids(session, set_id)
        result = set_ids if result is None else (result & set_ids)

    if character_id is not None and character_id != "":
        char_ids = _character_scope_picture_ids(session, character_id)
        result = char_ids if result is None else (result & char_ids)

    return result
