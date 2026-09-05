"""Stack-atomic project & set membership helpers.

Stacks are treated as a single unit for *grouping* membership: every member of a
stack always shares the same project membership (``PictureProjectMember`` /
``Picture.project_id``) and the same set membership (``PictureSetMember``).

Two operations maintain that invariant:

* :func:`expand_picture_ids_to_stacks` - used by grouping *mutations* so that an
  add/remove/set applied to any stacked picture is applied to **every** member of
  its stack. Callers pass the resulting id list to their normal per-picture
  mutation logic, so state can never go partial.
* :func:`reconcile_stack_membership` - used only when a picture *joins* an
  existing stack (stack create / add-members). The enlarged stack reconciles to
  the **union** of its members' project & set memberships so it becomes
  consistent again.

Character assignment also uses the expansion helper: an authoritative face stays
fixed for its reviewed picture while the established face-selection rules choose
one face for every other live stack member.
"""

from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import (
    Picture,
    PictureProjectMember,
    PictureSetMember,
)
from pixlstash.utils.service.scope_table import scope_id_subquery


def in_a_live_stack():
    """A picture that is in a stack **that is still a stack**.

    ``stack_id IS NOT NULL`` on its own is not that. A soft-deleted picture keeps
    its ``stack_id``, deliberately: it is what lets a Scrapheap restore put the
    picture back into the stack it came from, and what makes undoing a collapse a
    flag flip rather than a rebuild. So a stack whose other members are all in the
    Scrapheap leaves one live picture still carrying a ``stack_id``, and that
    picture is not stacked in any sense the user would recognise.

    The rest of the app already knew this and this predicate did not, which is the
    bug it exists to close. ``_enrich_stack_counts`` counts live members only and
    ``StackBadge`` hides below two, so the grid draws that survivor as a plain
    picture while the ``stacked`` filter served it as a stack. Collapsing a stack
    to its cover produces exactly that state every time, so what had been a rare
    inconsistency became a guaranteed one.

    One definition, used by every surface that asks the question, so the filter,
    the count and the badge cannot drift apart again.

    Returns:
        A SQLAlchemy predicate over :class:`Picture`, true when the picture is
        live, carries a ``stack_id``, and at least one OTHER live picture shares
        it.
    """
    return Picture.stack_id.is_not(None) & Picture.stack_id.in_(
        select(Picture.stack_id)
        .where(Picture.stack_id.is_not(None), Picture.deleted.is_(False))
        .group_by(Picture.stack_id)
        .having(func.count(Picture.id) >= 2)
    )


def expand_picture_ids_to_stacks(
    session: Session, picture_ids, include_deleted: bool = False
) -> list[int]:
    """Return *picture_ids* plus every non-deleted co-member of any stack they
    belong to.

    Grouping mutations call this first so an action on a single stacked picture
    (e.g. a collapsed-stack leader) is applied to the whole stack.

    Args:
        session: Pre-opened DB session.
        picture_ids: Seed ids whose stacks should be expanded.
        include_deleted: Also return soft-deleted (scrapheaped) co-members.
            Grouping mutations leave those alone and keep the default; the
            scrapheap operations pass ``True`` because
            :func:`~pixlstash.stacking.normalize_stack_positions` renumbers
            **every** member of a stack, deleted ones included, so the operation
            log has to snapshot them or an undo would restore the wrong order.
    """
    ids: set[int] = {int(pid) for pid in picture_ids if pid is not None}
    if not ids:
        return []

    input_scope = scope_id_subquery(session, ids, name="_pixlstash_expand_picture_ids")
    stack_ids = {
        int(stack_id)
        for stack_id in session.exec(
            select(Picture.stack_id).where(
                Picture.id.in_(input_scope),
                Picture.stack_id.is_not(None),
            )
        ).all()
        if stack_id is not None
    }
    if stack_ids:
        stack_scope = scope_id_subquery(
            session, stack_ids, name="_pixlstash_expand_stack_ids"
        )
        member_query = select(Picture.id).where(Picture.stack_id.in_(stack_scope))
        if not include_deleted:
            member_query = member_query.where(Picture.deleted.is_(False))
        member_ids = session.exec(member_query).all()
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

    # Defense in depth, evaluated before *any* mutation below so a refusal never
    # leaves a half-reconciled stack: the union performed here would add members
    # to every set any member belongs to, and a locked set's membership cannot
    # change. The stack routes run the same guard up-front; this catches any
    # caller that skips it.
    #
    # Imported locally because set_lock_service imports this module
    # (expand_picture_ids_to_stacks), so a module-level import would be circular.
    from pixlstash.services.set_lock_service import (
        enforce_stack_membership_not_locked,
    )

    enforce_stack_membership_not_locked(
        session, member_ids, stack_id, "stack pictures together"
    )
    member_scope = scope_id_subquery(
        session, member_ids, name="_pixlstash_reconcile_stack_members"
    )

    changed = False

    # --- Project membership: union of all members' projects ---
    project_ids = {
        int(pid)
        for pid in session.exec(
            select(PictureProjectMember.project_id).where(
                PictureProjectMember.picture_id.in_(member_scope)
            )
        ).all()
        if pid is not None
    }
    for project_id in project_ids:
        present = {
            int(pic_id)
            for pic_id in session.exec(
                select(PictureProjectMember.picture_id).where(
                    PictureProjectMember.picture_id.in_(member_scope),
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
    for pic in session.exec(select(Picture).where(Picture.id.in_(member_scope))).all():
        if pic.project_id != primary_project_id:
            pic.project_id = primary_project_id
            session.add(pic)
            changed = True

    # --- Set membership: union of all members' sets ---
    set_ids = {
        int(sid)
        for sid in session.exec(
            select(PictureSetMember.set_id).where(
                PictureSetMember.picture_id.in_(member_scope)
            )
        ).all()
        if sid is not None
    }
    for set_id in set_ids:
        present = {
            int(pic_id)
            for pic_id in session.exec(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.picture_id.in_(member_scope),
                    PictureSetMember.set_id == set_id,
                )
            ).all()
        }
        for member_id in member_ids:
            if member_id not in present:
                session.add(PictureSetMember(set_id=set_id, picture_id=member_id))
                changed = True

    return changed
