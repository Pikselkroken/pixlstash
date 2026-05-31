"""Stack-atomic project & set membership helpers.

Stacks are treated as a single unit for *grouping* membership: every member of a
stack always shares the same project membership (``PictureProjectMember`` /
``Picture.project_id``) and the same set membership (``PictureSetMember``).

Two operations maintain that invariant:

* :func:`expand_picture_ids_to_stacks` — used by grouping *mutations* so that an
  add/remove/set applied to any stacked picture is applied to **every** member of
  its stack. Callers pass the resulting id list to their normal per-picture
  mutation logic, so state can never go partial.
* :func:`reconcile_stack_membership` — used only when a picture *joins* an
  existing stack (stack create / add-members). The enlarged stack reconciles to
  the **union** of its members' project & set memberships so it becomes
  consistent again.

Character/face assignment is intentionally *not* made atomic here (faces live in
specific pictures and a stack may mix characters); the UI refuses per-member
character edits instead.
"""

from sqlmodel import Session, select

from pixlstash.db_models import (
    Picture,
    PictureProjectMember,
    PictureSetMember,
)


def expand_picture_ids_to_stacks(session: Session, picture_ids) -> list[int]:
    """Return *picture_ids* plus every non-deleted co-member of any stack they
    belong to.

    Grouping mutations call this first so an action on a single stacked picture
    (e.g. a collapsed-stack leader) is applied to the whole stack.
    """
    ids: set[int] = {int(pid) for pid in picture_ids if pid is not None}
    if not ids:
        return []

    stack_ids = {
        int(stack_id)
        for stack_id in session.exec(
            select(Picture.stack_id).where(
                Picture.id.in_(ids),
                Picture.stack_id.is_not(None),
            )
        ).all()
        if stack_id is not None
    }
    if stack_ids:
        member_ids = session.exec(
            select(Picture.id).where(
                Picture.stack_id.in_(stack_ids),
                Picture.deleted.is_(False),
            )
        ).all()
        ids.update(int(mid) for mid in member_ids if mid is not None)

    return sorted(ids)


def reconcile_stack_membership(session: Session, stack_id) -> bool:
    """Union the project & set memberships across all members of *stack_id* so
    every member shares the same memberships.

    Called when a picture joins a stack (create / add-members). Returns ``True``
    if any membership row or scalar ``project_id`` was changed. Does not commit;
    the caller's task commits.
    """
    if stack_id is None:
        return False

    member_ids = [
        int(mid)
        for mid in session.exec(
            select(Picture.id).where(
                Picture.stack_id == stack_id,
                Picture.deleted.is_(False),
            )
        ).all()
        if mid is not None
    ]
    if len(member_ids) < 2:
        return False

    changed = False

    # --- Project membership: union of all members' projects ---
    project_ids = {
        int(pid)
        for pid in session.exec(
            select(PictureProjectMember.project_id).where(
                PictureProjectMember.picture_id.in_(member_ids)
            )
        ).all()
        if pid is not None
    }
    for project_id in project_ids:
        present = {
            int(pic_id)
            for pic_id in session.exec(
                select(PictureProjectMember.picture_id).where(
                    PictureProjectMember.picture_id.in_(member_ids),
                    PictureProjectMember.project_id == project_id,
                )
            ).all()
        }
        for member_id in member_ids:
            if member_id not in present:
                session.add(
                    PictureProjectMember(picture_id=member_id, project_id=project_id)
                )
                changed = True

    # Keep the scalar Picture.project_id consistent across the stack: a single
    # deterministic primary (lowest project id in the union, else None).
    primary_project_id = min(project_ids) if project_ids else None
    for pic in session.exec(select(Picture).where(Picture.id.in_(member_ids))).all():
        if pic.project_id != primary_project_id:
            pic.project_id = primary_project_id
            session.add(pic)
            changed = True

    # --- Set membership: union of all members' sets ---
    set_ids = {
        int(sid)
        for sid in session.exec(
            select(PictureSetMember.set_id).where(
                PictureSetMember.picture_id.in_(member_ids)
            )
        ).all()
        if sid is not None
    }
    for set_id in set_ids:
        present = {
            int(pic_id)
            for pic_id in session.exec(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.picture_id.in_(member_ids),
                    PictureSetMember.set_id == set_id,
                )
            ).all()
        }
        for member_id in member_ids:
            if member_id not in present:
                session.add(PictureSetMember(set_id=set_id, picture_id=member_id))
                changed = True

    return changed
