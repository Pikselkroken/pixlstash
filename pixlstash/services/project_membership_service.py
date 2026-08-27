"""Reference-aware project-membership reconciliation.

When a character's or picture set's ``project_id`` changes, the entity's member
pictures must be moved between :class:`~pixlstash.db_models.PictureProjectMember`
rows: each picture is *added* to the new project and *removed* from the old one.
Removal is **reference-aware** - a picture stays in the old project when another
character or picture set still assigned to that project anchors it there (see
:func:`pixlstash.routes._helpers.picture_referenced_by_project`). When the entity
leaves all projects, each picture's scalar ``Picture.project_id`` pointer falls
back to any remaining membership.

This module is the single implementation of that reconciliation. It was
previously duplicated, verbatim in behaviour, between
``routes/characters.py::patch_character`` and
``routes/picture_sets.py::update_picture_set``; both call sites now delegate to
:func:`reconcile_entity_project_change`.

Each caller keeps three responsibilities of its own, because they differ by
entity kind and were never part of the shared algorithm:

* **member-picture derivation** - characters resolve the pictures of their faces
  and expand them to whole stacks (project membership is stack-atomic for a
  character); picture sets read their explicit members. The caller passes the
  already-resolved ``picture_ids``.
* **the trigger** - characters reconcile only when ``project_id`` actually
  changes; picture sets also reconcile on an idempotent same-project re-assign
  to repair historical drift. The caller decides whether to call this function.
* **the "did anything change" signal** - characters treat "the entity had member
  pictures" as the signal; picture sets use the precise change counts returned
  here. This function returns :class:`ProjectMembershipReconcileResult` so either
  interpretation is available.

The function takes a **pre-opened** ``Session`` (the same threading discipline as
``enforce_picture_scope`` and the set-lock guards) and never touches
``vault.db`` - per the services DB-access rule (backend_architecture.md §10.1)
the caller owns the transaction and commit.

**Multi-project entities (issue #125).** A character or picture set may now belong
to several projects at once. This module also owns the *entity*→project write
path: :func:`set_character_projects` / :func:`set_picture_set_projects` maintain
the ``CharacterProjectMember`` / ``PictureSetProjectMember`` join rows **and**
re-derive the legacy scalar ``project_id`` pointer (lowest member project id, or
``None``). Nothing else may assign those FKs - see
:mod:`pixlstash.db_models.entity_project` for the read-side predicates and the
"write both, read the join" contract. The picture-side reconciliation follows in
:func:`reconcile_entity_projects_change`, of which the original single-project
:func:`reconcile_entity_project_change` is now a thin shim.
"""

from dataclasses import dataclass, field
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from pixlstash.db_models import (
    Character,
    CharacterProjectMember,
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetProjectMember,
    Project,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.routes._helpers import picture_referenced_by_project
from pixlstash.utils.service.scope_table import scope_id_subquery

logger = get_logger(__name__)

__all__ = [
    "EntityProjectChange",
    "ProjectMembershipReconcileResult",
    "character_project_ids",
    "picture_set_project_ids",
    "reconcile_entity_project_change",
    "reconcile_entity_projects_change",
    "set_character_projects",
    "set_picture_set_projects",
]


@dataclass
class EntityProjectChange:
    """Outcome of writing a character's or picture set's project membership set.

    Attributes:
        added: Project ids the entity newly joined.
        removed: Project ids the entity left.
        primary_project_id: The value written to the entity's scalar
            ``project_id`` pointer - the lowest member project id, or ``None``
            when the entity now belongs to no project.
        target_project_ids: The full, normalised membership set after the write.
    """

    added: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    primary_project_id: Optional[int] = None
    target_project_ids: list[int] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True if the entity joined or left at least one project."""
        return bool(self.added or self.removed)


@dataclass
class ProjectMembershipReconcileResult:
    """Outcome counts from a single reconciliation pass.

    Attributes:
        memberships_added: ``PictureProjectMember`` rows created for the new
            project.
        memberships_removed: ``PictureProjectMember`` rows deleted from the old
            project (reference-aware - only pictures no longer anchored there).
        pointers_repointed: Pictures whose scalar ``Picture.project_id`` pointer
            was updated (to the new project, or to a fallback membership when the
            entity left all projects).
    """

    memberships_added: int = 0
    memberships_removed: int = 0
    pointers_repointed: int = 0

    @property
    def changed(self) -> bool:
        """True if any membership row or picture pointer was modified."""
        return bool(
            self.memberships_added
            or self.memberships_removed
            or self.pointers_repointed
        )


def reconcile_entity_project_change(
    session: Session,
    *,
    picture_ids: Iterable[int],
    old_project_id: Optional[int],
    new_project_id: Optional[int],
    exclude_character_id: Optional[int] = None,
    exclude_set_id: Optional[int] = None,
) -> ProjectMembershipReconcileResult:
    """Reconcile per-picture project membership after an entity's project change.

    For every picture in ``picture_ids``:

    1. **Add** a ``PictureProjectMember`` for ``new_project_id`` when one does not
       already exist (skipped when ``new_project_id`` is ``None``).
    2. **Remove** the ``PictureProjectMember`` for ``old_project_id`` - unless
       another character or picture set still assigned to that project anchors
       the picture there (reference-aware; the moving entity is excluded via
       ``exclude_character_id`` / ``exclude_set_id``). Skipped when
       ``old_project_id`` is ``None`` or equals ``new_project_id``.
    3. **Repoint** the scalar ``Picture.project_id``: to ``new_project_id`` when
       a new project is set, otherwise - if the picture still pointed at the old
       project - fall back to any remaining membership (lowest ``project_id``) or
       ``None`` when none remain.

    Passing ``old_project_id == new_project_id`` with a non-``None`` project is
    the idempotent-repair path: memberships and pointers are ensured, and no
    removal is attempted.

    Args:
        session: A pre-opened session owned by the caller; this function does not
            commit.
        picture_ids: The entity's member picture ids, already resolved (and
            stack-expanded where the caller requires it).
        old_project_id: The project the entity is leaving, or ``None``.
        new_project_id: The project the entity now belongs to, or ``None``.
        exclude_character_id: Character to exclude from the reference check (the
            character being moved).
        exclude_set_id: Picture set to exclude from the reference check (the set
            being moved).

    Returns:
        A :class:`ProjectMembershipReconcileResult` with per-operation counts.
    """
    return reconcile_entity_projects_change(
        session,
        picture_ids=picture_ids,
        ensure_project_ids=[new_project_id] if new_project_id is not None else [],
        remove_project_ids=(
            [old_project_id]
            if old_project_id is not None and old_project_id != new_project_id
            else []
        ),
        exclude_character_id=exclude_character_id,
        exclude_set_id=exclude_set_id,
    )


def reconcile_entity_projects_change(
    session: Session,
    *,
    picture_ids: Iterable[int],
    ensure_project_ids: Iterable[int],
    remove_project_ids: Iterable[int],
    exclude_character_id: Optional[int] = None,
    exclude_set_id: Optional[int] = None,
) -> ProjectMembershipReconcileResult:
    """Reconcile per-picture project membership for a **multi-project** entity.

    The multi-project generalisation of :func:`reconcile_entity_project_change`
    (issue #125). Instead of one old and one new project it takes the entity's
    **full target project set** and the set of projects it just left, so a
    character or picture set that belongs to several projects at once anchors its
    pictures in all of them.

    For every picture in ``picture_ids``:

    1. **Add** a ``PictureProjectMember`` for every id in ``ensure_project_ids``
       that does not already have one. Passing the entity's *whole* membership set
       (not just the newly added ids) keeps the historical idempotent-repair path:
       a drifted picture missing a row is healed.
    2. **Remove** the ``PictureProjectMember`` for every id in
       ``remove_project_ids`` - unless another character or picture set still in
       that project anchors the picture there (reference-aware; the moving entity
       is excluded via ``exclude_character_id`` / ``exclude_set_id``). Ids present
       in ``ensure_project_ids`` are never removed.
    3. **Repoint** the scalar ``Picture.project_id``: when the entity is in at
       least one project and the picture does not already point at one of them,
       point it at the lowest such id. When the entity left every project, a
       picture pointing at one of the departed projects falls back to any
       remaining membership (lowest ``project_id``) or ``None``.

    Args:
        session: A pre-opened session owned by the caller; this function does not
            commit.
        picture_ids: The entity's member picture ids, already resolved (and
            stack-expanded where the caller requires it).
        ensure_project_ids: Every project the entity belongs to *after* the
            change.
        remove_project_ids: Every project the entity belonged to *before* the
            change and no longer does.
        exclude_character_id: Character to exclude from the reference check (the
            character being moved).
        exclude_set_id: Picture set to exclude from the reference check (the set
            being moved).

    Returns:
        A :class:`ProjectMembershipReconcileResult` with per-operation counts.
    """
    result = ProjectMembershipReconcileResult()

    pic_id_list = [pid for pid in picture_ids if pid is not None]
    if not pic_id_list:
        return result

    ensure_ids = sorted({int(pid) for pid in ensure_project_ids if pid is not None})
    remove_ids = sorted(
        {int(pid) for pid in remove_project_ids if pid is not None} - set(ensure_ids)
    )
    if not ensure_ids and not remove_ids:
        return result

    picture_scope = scope_id_subquery(
        session, pic_id_list, name="_pixlstash_entity_project_picture_ids"
    )
    for pic in session.exec(select(Picture).where(Picture.id.in_(picture_scope))).all():
        if pic.id is None:
            continue

        # 1. Associate the picture with every project the entity now belongs to.
        for project_id in ensure_ids:
            membership = session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == int(pic.id),
                    PictureProjectMember.project_id == project_id,
                )
            ).first()
            if membership is None:
                session.add(
                    PictureProjectMember(
                        picture_id=int(pic.id),
                        project_id=project_id,
                    )
                )
                result.memberships_added += 1

        # 2. Disassociate the picture from every departed project, unless another
        #    character or picture set still in that project anchors it.
        for project_id in remove_ids:
            if picture_referenced_by_project(
                session,
                int(pic.id),
                project_id,
                exclude_character_id=exclude_character_id,
                exclude_set_id=exclude_set_id,
            ):
                continue
            old_membership = session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == int(pic.id),
                    PictureProjectMember.project_id == project_id,
                )
            ).first()
            if old_membership is not None:
                session.delete(old_membership)
                result.memberships_removed += 1

        # 3. Update the picture's primary project pointer.
        if ensure_ids:
            if pic.project_id not in ensure_ids:
                pic.project_id = ensure_ids[0]
                session.add(pic)
                result.pointers_repointed += 1
        elif pic.project_id is not None and pic.project_id in remove_ids:
            # Entity left every project; fall back to any project the picture
            # still belongs to. Flush first so the just-deleted memberships are
            # not counted as remaining anchors.
            session.flush()
            fallback_project_id = session.exec(
                select(PictureProjectMember.project_id)
                .where(PictureProjectMember.picture_id == int(pic.id))
                .order_by(PictureProjectMember.project_id.asc())
            ).first()
            pic.project_id = (
                int(fallback_project_id) if fallback_project_id is not None else None
            )
            session.add(pic)
            result.pointers_repointed += 1

    return result


# ---------------------------------------------------------------------------
# Entity → project membership (issue #125): the single write path
# ---------------------------------------------------------------------------


def _normalise_project_ids(project_ids: Optional[Iterable[int]]) -> list[int]:
    """Coerce a caller-supplied project id list to a sorted, de-duplicated list.

    Args:
        project_ids: Raw ids (possibly ``None``, duplicated, unsorted, or string
            encoded).

    Returns:
        A sorted list of unique ints; empty when ``project_ids`` is ``None`` or
        contains no usable id.

    Raises:
        HTTPException: ``400`` when an entry is not an integer.
    """
    if project_ids is None:
        return []
    normalised: set[int] = set()
    for raw in project_ids:
        if raw is None:
            continue
        try:
            normalised.add(int(raw))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid project_id") from exc
    return sorted(normalised)


def _require_projects_exist(session: Session, project_ids: Iterable[int]) -> None:
    """Raise 404 if any id in ``project_ids`` is not an existing project.

    Args:
        session: A pre-opened session owned by the caller.
        project_ids: Project ids to validate.

    Raises:
        HTTPException: ``404`` with ``detail="Project not found"`` - the same
            shape the character / picture-set handlers returned before #125, so
            the API contract is unchanged.
    """
    wanted = sorted({int(pid) for pid in project_ids})
    if not wanted:
        return
    found = {
        int(row)
        for row in session.exec(select(Project.id).where(Project.id.in_(wanted))).all()
        if row is not None
    }
    missing = [pid for pid in wanted if pid not in found]
    if missing:
        logger.warning(
            "Refusing entity project assignment: project id(s) %s do not exist "
            "(requested %s)",
            missing,
            wanted,
        )
        raise HTTPException(status_code=404, detail="Project not found")


def character_project_ids(session: Session, character_id: int) -> list[int]:
    """Return every project a character belongs to, lowest id first.

    Args:
        session: A pre-opened session owned by the caller.
        character_id: The character to look up.

    Returns:
        A sorted list of project ids; empty when the character is unassigned.
    """
    return sorted(
        int(row)
        for row in session.exec(
            select(CharacterProjectMember.project_id).where(
                CharacterProjectMember.character_id == int(character_id)
            )
        ).all()
        if row is not None
    )


def picture_set_project_ids(session: Session, set_id: int) -> list[int]:
    """Return every project a picture set belongs to, lowest id first.

    Args:
        session: A pre-opened session owned by the caller.
        set_id: The picture set to look up.

    Returns:
        A sorted list of project ids; empty when the set is unassigned.
    """
    return sorted(
        int(row)
        for row in session.exec(
            select(PictureSetProjectMember.project_id).where(
                PictureSetProjectMember.set_id == int(set_id)
            )
        ).all()
        if row is not None
    )


def set_character_projects(
    session: Session,
    character: Character,
    project_ids: Optional[Iterable[int]],
) -> EntityProjectChange:
    """Write a character's full project membership set (join rows + FK pointer).

    This is the **only** supported way to change which projects a character
    belongs to. It writes both representations in one place: the
    ``CharacterProjectMember`` join rows (the read model) and the legacy scalar
    ``Character.project_id`` pointer, which is re-derived as the lowest member
    project id (``None`` when the character joins no project). Assigning the FK
    directly leaves the join table stale and makes the character invisible to
    every project-scoped read and authorization check.

    Args:
        session: A pre-opened session owned by the caller; this function does not
            commit.
        character: The character row to update (already loaded in ``session``).
        project_ids: The complete target membership set. ``None`` and ``[]`` both
            mean "belongs to no project".

    Returns:
        An :class:`EntityProjectChange` describing what moved.

    Raises:
        HTTPException: ``400`` for a non-integer id, ``404`` when a project does
            not exist.
    """
    target = _normalise_project_ids(project_ids)
    _require_projects_exist(session, target)

    character_id = int(character.id)
    current = set(character_project_ids(session, character_id))
    target_set = set(target)

    added = sorted(target_set - current)
    removed = sorted(current - target_set)

    for project_id in added:
        session.add(
            CharacterProjectMember(character_id=character_id, project_id=project_id)
        )
    for project_id in removed:
        row = session.exec(
            select(CharacterProjectMember).where(
                CharacterProjectMember.character_id == character_id,
                CharacterProjectMember.project_id == project_id,
            )
        ).first()
        if row is not None:
            session.delete(row)

    primary = target[0] if target else None
    if character.project_id != primary:
        character.project_id = primary
    session.add(character)

    return EntityProjectChange(
        added=added,
        removed=removed,
        primary_project_id=primary,
        target_project_ids=target,
    )


def set_picture_set_projects(
    session: Session,
    picture_set: PictureSet,
    project_ids: Optional[Iterable[int]],
) -> EntityProjectChange:
    """Write a picture set's full project membership set (join rows + FK pointer).

    The picture-set twin of :func:`set_character_projects`; see that function for
    the write-both/read-the-join contract.

    Args:
        session: A pre-opened session owned by the caller; this function does not
            commit.
        picture_set: The picture set row to update (already loaded in
            ``session``).
        project_ids: The complete target membership set. ``None`` and ``[]`` both
            mean "belongs to no project".

    Returns:
        An :class:`EntityProjectChange` describing what moved.

    Raises:
        HTTPException: ``400`` for a non-integer id, ``404`` when a project does
            not exist.
    """
    target = _normalise_project_ids(project_ids)
    _require_projects_exist(session, target)

    set_id = int(picture_set.id)
    current = set(picture_set_project_ids(session, set_id))
    target_set = set(target)

    added = sorted(target_set - current)
    removed = sorted(current - target_set)

    for project_id in added:
        session.add(PictureSetProjectMember(set_id=set_id, project_id=project_id))
    for project_id in removed:
        row = session.exec(
            select(PictureSetProjectMember).where(
                PictureSetProjectMember.set_id == set_id,
                PictureSetProjectMember.project_id == project_id,
            )
        ).first()
        if row is not None:
            session.delete(row)

    primary = target[0] if target else None
    if picture_set.project_id != primary:
        picture_set.project_id = primary
    session.add(picture_set)

    return EntityProjectChange(
        added=added,
        removed=removed,
        primary_project_id=primary,
        target_project_ids=target,
    )
