"""Single source of truth for picture-set lock enforcement.

A :class:`~pixlstash.db_models.picture_set.PictureSet` with ``locked=True`` is a
hard, whole-set freeze. Two protections follow from it:

* **Set-level** — the set's own fields cannot be edited, it cannot be deleted, and
  its membership cannot change. Guarded with :func:`enforce_set_not_locked`.
* **Picture-level** — every picture that belongs (directly, or through a stack
  sibling) to at least one locked set has its *label data* frozen: confirmed-tag
  edits, description, user score, soft-delete, and tag-review decisions are all
  refused. Guarded with :func:`enforce_pictures_not_locked`.

The guards raise ``423 Locked`` with a structured ``detail`` so the frontend can
build the "why" tooltip without string-parsing, and ``423`` cannot be confused
with the existing ``403`` (token scope) or ``409`` (name conflict) meanings.

Every guard is a plain function that takes a **pre-opened** ``Session`` — it is
called at the top of the mutation closure that already owns the session (the same
threading discipline as ``enforce_picture_scope``). Per the services DB-access
rule (backend_architecture.md §10.1) this module never touches ``vault.db``.

Stack note: membership is stack-atomic (see ``services/stack_membership.py``), and
a collapsed-stack *leader* shown in the grid may not itself be the row that is a
member of a locked set — a sibling is. Every picture-level check therefore runs on
the **stack-expanded** id list, so a stacked sibling in a locked set blocks the
whole operation.
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureSet, PictureSetMember
from pixlstash.pixl_logging import get_logger
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks

logger = get_logger(__name__)

# 423 Locked — semantically exact for "the resource is frozen"; distinct from the
# 403 (token scope) and 409 (name conflict) codes already used on these routes.
LOCKED_STATUS_CODE = 423


def _locked_sets_by_picture(
    session: Session, picture_ids
) -> dict[int, list[tuple[int, str]]]:
    """Map each (stack-expanded) picture id to the locked sets it belongs to.

    Args:
        session: Pre-opened DB session.
        picture_ids: Candidate picture ids. Expanded to whole stacks first so a
            stacked sibling that is the actual locked-set member is caught even
            when only the collapsed-stack leader was passed in.

    Returns:
        ``{picture_id: [(set_id, set_name), ...]}`` for every expanded picture id
        that is a member of one or more locked sets. Empty when nothing is locked.
    """
    ids = [int(pid) for pid in picture_ids if pid is not None]
    if not ids:
        return {}
    expanded = expand_picture_ids_to_stacks(session, ids)
    if not expanded:
        return {}
    rows = session.exec(
        select(PictureSetMember.picture_id, PictureSet.id, PictureSet.name)
        .join(PictureSet, PictureSet.id == PictureSetMember.set_id)
        .where(
            PictureSetMember.picture_id.in_(expanded),
            PictureSet.locked.is_(True),
        )
    ).all()
    result: dict[int, list[tuple[int, str]]] = {}
    for pic_id, set_id, set_name in rows:
        result.setdefault(int(pic_id), []).append((int(set_id), set_name))
    return result


def locked_set_names_for_pictures(
    session: Session, picture_ids
) -> dict[int, list[str]]:
    """Return ``{picture_id: [locked set name, ...]}`` for the given pictures.

    Convenience view over :func:`_locked_sets_by_picture` used by the metadata
    endpoint and anywhere only the human-facing set names are needed.
    """
    detail = _locked_sets_by_picture(session, picture_ids)
    return {pid: [name for _sid, name in pairs] for pid, pairs in detail.items()}


def locked_sets_for_pictures(session: Session, picture_ids) -> dict[int, list[dict]]:
    """Batch ``{picture_id: [{"id", "name"}, ...]}`` of the locked sets freezing
    each of *picture_ids*.

    Keyed by the **input** ids (never the expanded stack siblings), so a caller
    can look up the picture it actually holds. A picture's entry includes sets
    that freeze it via a stack sibling, matching :func:`locked_picture_ids`;
    unfrozen ids are simply absent. Each list is deduplicated and stable-sorted
    by set id for a deterministic payload.

    Exists so a list endpoint can label many pictures in a fixed number of
    queries — calling :func:`locked_by_sets_for_picture` per row would be an N+1.
    """
    ids = [int(pid) for pid in picture_ids if pid is not None]
    if not ids:
        return {}
    detail = _locked_sets_by_picture(session, ids)
    if not detail:
        return {}

    # A locked-set member freezes its whole stack, so roll each frozen picture's
    # sets up to its stack, then hand them to every input id on that stack.
    stack_by_picture = {
        int(pid): (int(sid) if sid is not None else None)
        for pid, sid in session.exec(
            select(Picture.id, Picture.stack_id).where(
                Picture.id.in_(sorted(set(ids) | set(detail)))
            )
        ).all()
    }
    sets_by_stack: dict[int, dict[int, str]] = {}
    for frozen_id, pairs in detail.items():
        stack_id = stack_by_picture.get(frozen_id)
        if stack_id is None:
            continue
        sets_by_stack.setdefault(stack_id, {}).update(dict(pairs))

    result: dict[int, list[dict]] = {}
    for pid in ids:
        sets: dict[int, str] = dict(detail.get(pid, []))
        stack_id = stack_by_picture.get(pid)
        if stack_id is not None:
            sets.update(sets_by_stack.get(stack_id, {}))
        if sets:
            result[pid] = [
                {"id": sid, "name": name} for sid, name in sorted(sets.items())
            ]
    return result


def locked_by_sets_for_picture(session: Session, picture_id: int) -> list[dict]:
    """Return ``[{"id", "name"}, ...]`` locked sets freezing a single picture.

    Deduplicated and stable-sorted by set id for a deterministic payload. Thin
    single-id wrapper over :func:`locked_sets_for_pictures`, so the two surfaces
    cannot disagree about what freezes a picture.
    """
    return locked_sets_for_pictures(session, [picture_id]).get(picture_id, [])


def locked_picture_ids(session: Session, picture_ids) -> set[int]:
    """Return the subset of *input* ids frozen by a locked set (directly or via a
    stack sibling).

    Used by batch mutations (e.g. bulk soft-delete) that skip locked ids instead
    of failing the whole request. Only ids from the original ``picture_ids`` are
    returned — never the expanded siblings — so callers can filter their input
    list directly.
    """
    ids = [int(pid) for pid in picture_ids if pid is not None]
    if not ids:
        return set()
    detail = _locked_sets_by_picture(session, ids)
    locked_expanded = set(detail.keys())
    if not locked_expanded:
        return set()
    # An input id is frozen if it is itself a locked-set member, or shares a stack
    # with one. Map both the inputs and the locked members to their stack ids.
    stack_by_id = {
        int(pid): (int(sid) if sid is not None else None)
        for pid, sid in session.exec(
            select(Picture.id, Picture.stack_id).where(Picture.id.in_(ids))
        ).all()
    }
    locked_stack_ids = {
        int(sid)
        for _pid, sid in session.exec(
            select(Picture.id, Picture.stack_id).where(Picture.id.in_(locked_expanded))
        ).all()
        if sid is not None
    }
    frozen: set[int] = set()
    for pid in ids:
        if pid in locked_expanded:
            frozen.add(pid)
            continue
        stack_id = stack_by_id.get(pid)
        if stack_id is not None and stack_id in locked_stack_ids:
            frozen.add(pid)
    return frozen


def locked_picture_id_subquery():
    """A ``SELECT`` of **every** frozen picture id in the vault, for use as a SQL
    membership test (``col.in_(...)`` / ``col.notin_(...)``).

    The set-valued helpers above ( :func:`locked_picture_ids` and friends) answer
    "is *this* id frozen?" for a caller that already holds a bounded id list. Read
    paths instead need to *filter* an open-ended, paged query — applying the lock
    after ``LIMIT`` would silently shrink pages — so they need the rule expressed
    as SQL rather than as a Python set. This function is that expression, and it
    is deliberately the only other place the rule is written, so the read filters
    and the write guards cannot drift apart.

    Frozen means exactly what :func:`locked_picture_ids` means: the picture is
    itself a member of a locked set, **or** it shares a stack with a non-deleted
    picture that is. The ``deleted`` filter applies only to the stack-derived arm,
    mirroring :func:`~pixlstash.services.stack_membership.expand_picture_ids_to_stacks`
    (which drops deleted co-members while always keeping the input id itself), so
    this predicate neither over- nor under-blocks relative to the write guards.

    Returns:
        A SQLAlchemy ``Select`` of ``Picture.id``. Correlates to nothing, so it is
        safe to embed in any query.
    """
    locked_members = (
        select(PictureSetMember.picture_id)
        .join(PictureSet, PictureSet.id == PictureSetMember.set_id)
        .where(PictureSet.locked.is_(True))
    )
    locked_stacks = select(Picture.stack_id).where(
        Picture.id.in_(locked_members),
        Picture.stack_id.is_not(None),
        Picture.deleted.is_(False),
    )
    return select(Picture.id).where(
        or_(
            Picture.id.in_(locked_members),
            Picture.stack_id.in_(locked_stacks),
        )
    )


def locked_set_ids(session: Session, set_ids) -> set[int]:
    """Return the subset of *set_ids* that are locked.

    The set-level counterpart to :func:`locked_picture_ids`. Used by *propagation*
    paths (ComfyUI generation, image-plugin runs) that copy a source picture's set
    memberships onto derived outputs: those paths must drop the locked sets rather
    than fail, so they need to know which ids to drop.

    Args:
        session: Pre-opened DB session.
        set_ids: Candidate ``PictureSet`` ids.

    Returns:
        The locked ids among *set_ids*. Empty when nothing is locked.
    """
    ids = {int(sid) for sid in set_ids if sid is not None}
    if not ids:
        return set()
    rows = session.exec(
        select(PictureSet.id).where(
            PictureSet.id.in_(sorted(ids)),
            PictureSet.locked.is_(True),
        )
    ).all()
    return {int(sid) for sid in rows}


def drop_locked_set_ids(
    session: Session, set_ids, action: str, picture_ids=None
) -> list[int]:
    """Filter *set_ids* down to the unlocked ones, logging every id dropped.

    The shared implementation behind the propagation paths' "skip the locked set,
    keep going" behaviour. A locked set's membership cannot change
    (:func:`enforce_set_not_locked`), but a derived-output propagation is not a
    direct user request to edit that set — failing the whole generation would
    discard work the user did ask for. So the locked sets are skipped and the
    unlocked ones still propagate.

    The skip is never silent: every dropped set is logged at ``WARNING`` with the
    action and the affected picture ids, so an unexpectedly missing membership is
    traceable (CLAUDE.md forbids silent failures).

    Args:
        session: Pre-opened DB session.
        set_ids: Candidate ``PictureSet`` ids to propagate into.
        action: Short human-facing phrase naming the propagation, for the log.
        picture_ids: Optional pictures that would have been added, for the log.

    Returns:
        Sorted list of the unlocked ids among *set_ids*.
    """
    ids = {int(sid) for sid in set_ids if sid is not None}
    if not ids:
        return []
    locked = locked_set_ids(session, ids)
    if locked:
        logger.warning(
            "Skipped '%s' into %d locked set(s) %s for picture(s) %s — a locked "
            "set's membership cannot change; the unlocked sets %s still applied",
            action,
            len(locked),
            sorted(locked),
            sorted(int(pid) for pid in (picture_ids or []) if pid is not None),
            sorted(ids - locked),
        )
    return sorted(ids - locked)


def enforce_stack_membership_not_locked(
    session: Session, picture_ids, stack_id, action: str
) -> None:
    """Raise ``423`` if stacking *picture_ids* into *stack_id* would change a
    locked set's membership.

    Stacks are atomic for set membership (see
    :mod:`~pixlstash.services.stack_membership`): an enlarged stack reconciles to
    the **union** of its members' sets. So stacking a picture onto a stack whose
    members sit in a locked set would add that picture to the locked set.

    Unlike the propagation paths (which skip — see :func:`drop_locked_set_ids`),
    stacking is a **direct user request**, so it fails loudly. Skipping instead
    would leave the stack violating its own atomicity invariant, and a later
    reconcile would then quietly pull the new picture into the locked set anyway.

    Args:
        session: Pre-opened DB session.
        picture_ids: Pictures being joined into the stack.
        stack_id: Target stack id, or ``None`` when a new stack is being created
            from *picture_ids* alone.
        action: Short human-facing verb phrase, echoed in the error detail.

    Raises:
        HTTPException: ``423`` with ``detail`` =
            ``{"code": "set_locked", "action", "sets": [{"id","name"}]}``.
    """
    ids = {int(pid) for pid in picture_ids if pid is not None}
    if not ids:
        return

    # The resulting stack = the incoming pictures (expanded to any stack they are
    # already in, since those come along on a merge) plus the target stack's
    # current members.
    members = set(expand_picture_ids_to_stacks(session, sorted(ids)))
    if stack_id is not None:
        members.update(
            int(pid)
            for pid in session.exec(
                select(Picture.id).where(
                    Picture.stack_id == int(stack_id),
                    Picture.deleted.is_(False),
                )
            ).all()
            if pid is not None
        )
    if len(members) < 2:
        # A single-member stack has no sibling to inherit membership from.
        return

    rows = session.exec(
        select(PictureSetMember.set_id, PictureSetMember.picture_id, PictureSet.name)
        .join(PictureSet, PictureSet.id == PictureSetMember.set_id)
        .where(
            PictureSetMember.picture_id.in_(sorted(members)),
            PictureSet.locked.is_(True),
        )
    ).all()
    if not rows:
        return

    # Only a locked set that does not already contain every resulting member
    # would gain a row from the reconcile. One that already contains them all is
    # untouched, so blocking it would be an over-block.
    members_by_set: dict[int, set[int]] = {}
    names: dict[int, str] = {}
    for set_id, pic_id, set_name in rows:
        members_by_set.setdefault(int(set_id), set()).add(int(pic_id))
        names[int(set_id)] = set_name
    gaining = {
        set_id for set_id, present in members_by_set.items() if present != members
    }
    if not gaining:
        return

    set_list = [{"id": sid, "name": names[sid]} for sid in sorted(gaining)]
    # Name the pictures each gaining set would swallow, so a client can point at
    # the thumbnails rather than re-deriving them from the set names. Restricted
    # to the caller's own input ids: a stack sibling the caller never named is
    # not a row it holds and could not mark.
    gaining_pids = sorted(
        ids & {pid for sid in gaining for pid in members - members_by_set[sid]}
    )
    logger.info(
        "Blocked '%s' on picture(s) %s, would add member(s) %s to locked set(s) %s",
        action,
        sorted(ids),
        gaining_pids,
        [s["name"] for s in set_list],
    )
    raise HTTPException(
        status_code=LOCKED_STATUS_CODE,
        detail={
            "code": "set_locked",
            "action": action,
            "sets": set_list,
            "picture_ids": gaining_pids,
        },
    )


@dataclass(frozen=True)
class BlockedMember:
    """One candidate that cannot join a dedup group's stack, and why.

    Attributes:
        picture_id: The candidate that has to stay out.
        sets: ``[{"id", "name"}, ...]`` locked sets freezing it, sorted by set id.
            Never empty: a member is only blocked because of a locked set.
    """

    picture_id: int
    sets: list[dict]

    def as_dict(self) -> dict:
        return {
            "picture_id": self.picture_id,
            "reason": "set_locked",
            "sets": [dict(entry) for entry in self.sets],
        }


@dataclass(frozen=True)
class StackablePartition:
    """The legally stackable subset of a duplicate group, and the rest.

    Attributes:
        stackable: Candidates that may be stacked together, in the caller's own
            order. Fewer than two means the group has no legal stack at all.
        blocked: Every frozen candidate, each with the sets that freeze it.
    """

    stackable: list[int]
    blocked: list[BlockedMember]

    @property
    def blocked_ids(self) -> list[int]:
        return [member.picture_id for member in self.blocked]

    def sets_for(self, picture_id: int) -> list[dict]:
        """The locked sets keeping *picture_id* out, or ``[]`` if it is in."""
        for member in self.blocked:
            if member.picture_id == int(picture_id):
                return [dict(entry) for entry in member.sets]
        return []


def partition_stackable_members(
    session: Session, picture_ids, locked_sets: Optional[dict] = None
) -> StackablePartition:
    """Split a duplicate group's candidates into the stackable ones and the frozen.

    **A frozen picture cannot be in a dedup stack at all**, which is a stricter
    rule than :func:`enforce_stack_membership_not_locked`'s on its own. Two gates
    sit on the dedup stack path and this has to satisfy the tighter one:

    * the membership guard, which refuses only when a locked set would *gain* a
      member, so it would happily stack a group that already sits wholly inside
      one locked set; but
    * :func:`~pixlstash.services.dedup_verdict_service.apply_metadata_union_in_session`,
      which unions tags and lifts scores onto every member and therefore calls
      :func:`enforce_pictures_not_locked` - a hard refusal for *any* frozen
      member, gain or no gain, because those are label edits.

    So the stackable subset is exactly the candidates that are not frozen. Once
    they are the only members, no locked set is touched at all and the membership
    guard is satisfied for free, which is why this function does not restate it.

    Args:
        session: Pre-opened DB session.
        picture_ids: A group's candidate ids, in the order the caller holds them.
            Duplicates are collapsed and ``None`` dropped.
        locked_sets: Optional pre-computed :func:`locked_sets_for_pictures` result
            covering *picture_ids*. A queue page builds one for every candidate on
            the page and passes it in for each group, so a page costs three
            queries rather than three per group.

    Returns:
        A :class:`StackablePartition`. A group with fewer than two distinct
        candidates is returned whole and unblocked: the stack floor is the
        caller's error to raise, not this function's.
    """
    ordered: list[int] = []
    seen: set[int] = set()
    for pid in picture_ids:
        if pid is None:
            continue
        value = int(pid)
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    if len(ordered) < 2:
        return StackablePartition(list(ordered), [])

    frozen = (
        locked_sets
        if locked_sets is not None
        else locked_sets_for_pictures(session, ordered)
    )
    blocked = [
        BlockedMember(pid, [dict(entry) for entry in frozen[pid]])
        for pid in ordered
        if frozen.get(pid)
    ]
    if not blocked:
        return StackablePartition(list(ordered), [])
    blocked_ids = {member.picture_id for member in blocked}
    return StackablePartition(
        [pid for pid in ordered if pid not in blocked_ids], blocked
    )


def enforce_pictures_not_locked(session: Session, picture_ids, action: str) -> None:
    """Raise ``423`` if any of *picture_ids* is frozen by a locked set.

    Args:
        session: Pre-opened DB session.
        picture_ids: Picture ids the caller is about to mutate.
        action: Short human-facing verb phrase for the operation (e.g.
            ``"edit tags"``), echoed back in the error detail.

    Raises:
        HTTPException: ``423`` with ``detail`` =
            ``{"code": "pictures_locked", "action", "sets": [{"id","name"}],
            "picture_ids": [...]}`` naming the frozen pictures and their sets.
    """
    detail = _locked_sets_by_picture(session, picture_ids)
    if not detail:
        return
    locked_pids = sorted(detail.keys())
    sets: dict[int, str] = {}
    for pairs in detail.values():
        for set_id, set_name in pairs:
            sets[set_id] = set_name
    set_list = [{"id": sid, "name": name} for sid, name in sorted(sets.items())]
    logger.info(
        "Blocked '%s' on %d locked picture(s) %s frozen by set(s) %s",
        action,
        len(locked_pids),
        locked_pids,
        [s["name"] for s in set_list],
    )
    raise HTTPException(
        status_code=LOCKED_STATUS_CODE,
        detail={
            "code": "pictures_locked",
            "action": action,
            "sets": set_list,
            "picture_ids": locked_pids,
        },
    )


def enforce_set_not_locked(session: Session, picture_set, action: str) -> None:
    """Raise ``423`` if *picture_set* is locked.

    A no-op for a missing set (``None``) or an unlocked one, so callers can pass
    the result of ``session.get(PictureSet, id)`` directly. ``session`` is accepted
    for signature symmetry with :func:`enforce_pictures_not_locked` (the set is
    already loaded, so no query is issued).

    Raises:
        HTTPException: ``423`` with ``detail`` =
            ``{"code": "set_locked", "action", "sets": [{"id","name"}]}``.
    """
    if picture_set is None or not getattr(picture_set, "locked", False):
        return
    logger.info(
        "Blocked '%s' on locked set id=%s name=%r",
        action,
        picture_set.id,
        picture_set.name,
    )
    raise HTTPException(
        status_code=LOCKED_STATUS_CODE,
        detail={
            "code": "set_locked",
            "action": action,
            "sets": [{"id": picture_set.id, "name": picture_set.name}],
        },
    )
