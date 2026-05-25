"""Shared filter helpers for picture query construction."""

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlmodel import Session

from pixlstash.db_models import Face, Picture, PictureProjectMember, PictureSetMember
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


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
