"""Reference-aware project-membership reconciliation.

When a character's or picture set's ``project_id`` changes, the entity's member
pictures must be moved between :class:`~pixlstash.db_models.PictureProjectMember`
rows: each picture is *added* to the new project and *removed* from the old one.
Removal is **reference-aware** — a picture stays in the old project when another
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

* **member-picture derivation** — characters resolve the pictures of their faces
  and expand them to whole stacks (project membership is stack-atomic for a
  character); picture sets read their explicit members. The caller passes the
  already-resolved ``picture_ids``.
* **the trigger** — characters reconcile only when ``project_id`` actually
  changes; picture sets also reconcile on an idempotent same-project re-assign
  to repair historical drift. The caller decides whether to call this function.
* **the "did anything change" signal** — characters treat "the entity had member
  pictures" as the signal; picture sets use the precise change counts returned
  here. This function returns :class:`ProjectMembershipReconcileResult` so either
  interpretation is available.

The function takes a **pre-opened** ``Session`` (the same threading discipline as
``enforce_picture_scope`` and the set-lock guards) and never touches
``vault.db`` — per the services DB-access rule (backend_architecture.md §10.1)
the caller owns the transaction and commit.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureProjectMember
from pixlstash.routes._helpers import picture_referenced_by_project
from pixlstash.utils.service.scope_table import scope_id_subquery

__all__ = [
    "ProjectMembershipReconcileResult",
    "reconcile_entity_project_change",
]


@dataclass
class ProjectMembershipReconcileResult:
    """Outcome counts from a single reconciliation pass.

    Attributes:
        memberships_added: ``PictureProjectMember`` rows created for the new
            project.
        memberships_removed: ``PictureProjectMember`` rows deleted from the old
            project (reference-aware — only pictures no longer anchored there).
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
    2. **Remove** the ``PictureProjectMember`` for ``old_project_id`` — unless
       another character or picture set still assigned to that project anchors
       the picture there (reference-aware; the moving entity is excluded via
       ``exclude_character_id`` / ``exclude_set_id``). Skipped when
       ``old_project_id`` is ``None`` or equals ``new_project_id``.
    3. **Repoint** the scalar ``Picture.project_id``: to ``new_project_id`` when
       a new project is set, otherwise — if the picture still pointed at the old
       project — fall back to any remaining membership (lowest ``project_id``) or
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
    result = ProjectMembershipReconcileResult()

    pic_id_list = [pid for pid in picture_ids if pid is not None]
    if not pic_id_list:
        return result

    picture_scope = scope_id_subquery(
        session, pic_id_list, name="_pixlstash_entity_project_picture_ids"
    )
    for pic in session.exec(
        select(Picture).where(Picture.id.in_(picture_scope))
    ).all():
        if pic.id is None:
            continue

        # 1. Associate the picture with the new project.
        if new_project_id is not None:
            membership = session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == int(pic.id),
                    PictureProjectMember.project_id == new_project_id,
                )
            ).first()
            if membership is None:
                session.add(
                    PictureProjectMember(
                        picture_id=int(pic.id),
                        project_id=new_project_id,
                    )
                )
                result.memberships_added += 1

        # 2. Disassociate the picture from the old project, unless another
        #    character or picture set still assigned to that project anchors it.
        if (
            old_project_id is not None
            and old_project_id != new_project_id
            and not picture_referenced_by_project(
                session,
                int(pic.id),
                old_project_id,
                exclude_character_id=exclude_character_id,
                exclude_set_id=exclude_set_id,
            )
        ):
            old_membership = session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == int(pic.id),
                    PictureProjectMember.project_id == old_project_id,
                )
            ).first()
            if old_membership is not None:
                session.delete(old_membership)
                result.memberships_removed += 1

        # 3. Update the picture's primary project pointer.
        if new_project_id is not None:
            if pic.project_id != new_project_id:
                pic.project_id = new_project_id
                session.add(pic)
                result.pointers_repointed += 1
        elif pic.project_id == old_project_id:
            # Entity left the project entirely; fall back to any project the
            # picture still belongs to. Flush first so the just-deleted old
            # membership is not counted as a remaining anchor.
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
