"""Shared filter helpers for picture query construction."""

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import aliased
from sqlmodel import Session
import numpy as np

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureProjectMember,
    PictureSetMember,
)
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

VALID_COMBINE_MODES: frozenset[str] = frozenset(
    {"mean", "max", "min", "harmonic_mean", "geometric_mean"}
)


def combine_likeness_scores(scores: np.ndarray, combine: str) -> np.ndarray:
    """Combine per-query similarity scores across multiple query images.

    Args:
        scores: Shape ``(Q, N)`` — Q query images, N candidates.  For a
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
            return {
                int(r[0])
                for r in session.exec(
                    select(Character.id).where(Character.project_id == project_id)
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
    ``character_id`` is NULL and *no* face assigned to any character — the same
    EXISTS/NOT-EXISTS clause used by the picture-scoring queries (see
    ``pixlstash.picture_scoring``). A numeric id matches pictures having a Face
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
        ``None`` when no dimension is provided (no scope — caller should not
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
