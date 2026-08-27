"""Single source of truth for picture-set lock enforcement.

A :class:`~pixlstash.db_models.picture_set.PictureSet` with ``locked=True`` is a
hard, whole-set freeze. Two protections follow from it:

* **Set-level** - the set's own fields cannot be edited, it cannot be deleted, and
  its membership cannot change. Guarded with :func:`enforce_set_not_locked`.
* **Picture-level** - every picture that belongs (directly, or through a stack
  sibling) to at least one locked set has its *label data* frozen: confirmed-tag
  edits, description, user score, soft-delete, and tag-review decisions are all
  refused. Guarded with :func:`enforce_pictures_not_locked`.

The guards raise ``423 Locked`` with a structured ``detail`` so the frontend can
build the "why" tooltip without string-parsing, and ``423`` cannot be confused
with the existing ``403`` (token scope) or ``409`` (name conflict) meanings.

Every guard is a plain function that takes a **pre-opened** ``Session`` - it is
called at the top of the mutation closure that already owns the session (the same
threading discipline as ``enforce_picture_scope``). Per the services DB-access
rule (backend_architecture.md §10.1) this module never touches ``vault.db``.

Stack note: membership is stack-atomic (see ``services/stack_membership.py``), and
a collapsed-stack *leader* shown in the grid may not itself be the row that is a
member of a locked set - a sibling is. Every picture-level check therefore runs on
the **stack-expanded** id list, so a stacked sibling in a locked set blocks the
whole operation.

That expansion is also why the stack is guarded in **both** directions:
:func:`enforce_stack_membership_not_locked` refuses a picture *joining* a stack a
locked set touches, and :func:`enforce_stack_detach_not_locked` refuses one
*leaving* it. Leaving is the direction that escalates: detaching a sibling
severs a freeze that reached it through the stack, so an unguarded detach turns a
hard ``423`` into a soft delete two calls later.
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureSet, PictureSetMember
from pixlstash.pixl_logging import get_logger
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.utils.service.scope_table import scope_id_subquery

logger = get_logger(__name__)

# 423 Locked - semantically exact for "the resource is frozen"; distinct from the
# 403 (token scope) and 409 (name conflict) codes already used on these routes.
LOCKED_STATUS_CODE = 423


def _picture_stack_ids(session: Session, picture_ids) -> dict[int, Optional[int]]:
    """Return picture-to-stack mappings without exceeding SQLite bind limits."""
    scope = scope_id_subquery(
        session, picture_ids, name="_pixlstash_lock_stack_picture_ids"
    )
    return {
        int(pid): (int(sid) if sid is not None else None)
        for pid, sid in session.exec(
            select(Picture.id, Picture.stack_id).where(Picture.id.in_(scope))
        ).all()
    }


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
    scope = scope_id_subquery(session, expanded, name="_pixlstash_locked_picture_ids")
    rows = session.exec(
        select(PictureSetMember.picture_id, PictureSet.id, PictureSet.name)
        .join(PictureSet, PictureSet.id == PictureSetMember.set_id)
        .where(
            PictureSetMember.picture_id.in_(scope),
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
    queries - calling :func:`locked_by_sets_for_picture` per row would be an N+1.
    """
    ids = [int(pid) for pid in picture_ids if pid is not None]
    if not ids:
        return {}
    detail = _locked_sets_by_picture(session, ids)
    if not detail:
        return {}

    # A locked-set member freezes its whole stack, so roll each frozen picture's
    # sets up to its stack, then hand them to every input id on that stack.
    stack_by_picture = _picture_stack_ids(session, set(ids) | set(detail))
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
    returned - never the expanded siblings - so callers can filter their input
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
    stack_by_id = _picture_stack_ids(session, ids)
    locked_stack_ids = {
        int(sid)
        for sid in _picture_stack_ids(session, locked_expanded).values()
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
    paths instead need to *filter* an open-ended, paged query - applying the lock
    after ``LIMIT`` would silently shrink pages - so they need the rule expressed
    as SQL rather than as a Python set. This function is that expression, and it
    is deliberately the only other place the rule is written, so the read filters
    and the write guards cannot drift apart.

    Frozen means exactly what :func:`locked_picture_ids` means: the picture is
    itself a member of a locked set, **or** it shares a stack with a non-deleted
    picture that is. The ``deleted`` filter applies only to the stack-derived arm,
    mirroring :func:`~pixlstash.services.stack_membership.expand_picture_ids_to_stacks`
    (which drops deleted co-members while always keeping the input id itself), so
    this predicate neither over- nor under-blocks relative to the write guards.

    That filter is why the *label* guards and the *detach* guard answer
    differently for one state: a stack whose only locked-set member is
    scrapheaped has no frozen live picture here (its siblings are editable and
    deletable), yet :func:`enforce_stack_detach_not_locked` still refuses to
    break the stack up. The two answer different questions, and
    :func:`_stack_member_ids` carries the reasoning; do not "reconcile" them by
    dropping this filter, which would freeze live pictures nothing has frozen.

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
    direct user request to edit that set - failing the whole generation would
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
            "Skipped '%s' into %d locked set(s) %s for picture(s) %s - a locked "
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

    Unlike the propagation paths (which skip - see :func:`drop_locked_set_ids`),
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

    member_scope = scope_id_subquery(
        session, members, name="_pixlstash_stack_lock_members"
    )
    rows = session.exec(
        select(PictureSetMember.set_id, PictureSetMember.picture_id, PictureSet.name)
        .join(PictureSet, PictureSet.id == PictureSetMember.set_id)
        .where(
            PictureSetMember.picture_id.in_(member_scope),
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


def _stack_member_ids(session: Session, stack_ids) -> dict[int, list[int]]:
    """``{stack_id: [every member row id]}``, soft-deleted members included.

    Deliberately unfiltered on ``deleted``: every route that detaches members
    also detaches the stack's scrapheaped rows when the stack dissolves, so
    "member of this stack" has to mean the same thing to the guard that refuses
    the detach and to the read that predicts it.

    **This is the one place the stack rule and the picture-level rule
    deliberately differ, and it is load-bearing.** A picture that is itself a
    member of a locked set is frozen whether or not it is in the Scrapheap, but
    it projects no freeze onto its live stack siblings: both
    :func:`expand_picture_ids_to_stacks` and the stack-derived arm of
    :func:`locked_picture_id_subquery` drop deleted co-members, so the siblings'
    label data stays editable. Filtering ``deleted`` here to "match" that would
    make the stack detachable, and the dissolve would take the frozen row with
    it (``mixed_stack_service._apply_removal`` detaches the scrapheaped rows
    rather than leave a stack nothing can empty). Restore it afterwards and it
    comes back loose, so the freeze it would have projected never returns: a
    deferred lock escape rather than a live one, which is why the guard is
    whole-stack and reads every row.

    Tested by ``tests/test_picture_set_locking.py::
    test_a_scrapheaped_locked_member_still_freezes_its_stack_against_detach``
    (and its over-blocking twin), which is the regression this docstring
    describes: adding ``Picture.deleted.is_(False)`` below flips all three
    detach routes from 423 to 200.
    """
    ids = sorted({int(sid) for sid in stack_ids if sid is not None})
    if not ids:
        return {}
    stack_scope = scope_id_subquery(session, ids, name="_pixlstash_detach_stack_ids")
    members: dict[int, list[int]] = {}
    for picture_id, stack_id in session.exec(
        select(Picture.id, Picture.stack_id).where(Picture.stack_id.in_(stack_scope))
    ).all():
        members.setdefault(int(stack_id), []).append(int(picture_id))
    return members


def locked_sets_freezing_stacks(session: Session, stack_ids) -> dict[int, list[dict]]:
    """``{stack_id: [{"id", "name"}, ...]}`` for each stack a locked set freezes.

    The **read-side counterpart** to :func:`enforce_stack_detach_not_locked`,
    and deliberately computed over the same rows: both take every member of the
    stack (:func:`_stack_member_ids`, soft-deleted included) and ask
    :func:`_locked_sets_by_picture` which of them a locked set holds. That
    equivalence is the whole point of the function existing. A row that predicts
    "you may unstack this" and a server that then answers ``423`` is worse than
    no prediction at all, and the two would drift the moment they were computed
    from different member sets (live-only here, all-rows there).

    Answers per **stack**, not per picture, because the refusal is per stack:
    one frozen member freezes every sibling, so there is no partial answer to
    give. A stack no locked set touches is simply absent from the result.

    **Every read surface that predicts a detach must come through here.** There
    are two, ``GET /dedup/mixed-stacks`` and
    ``GET /dedup/stacks/{stack_id}/members``, and they once disagreed: the
    second rolled its unit-level ``stackable`` up from the live member ids it
    already had, so a stack whose only locked-set member was scrapheaped read
    ``true`` there, ``false`` on the row, and ``423`` from the server. Deriving
    the answer from a member list a caller happens to hold is exactly how that
    happens; call this instead.

    Args:
        session: Pre-opened DB session.
        stack_ids: Candidate ``PictureStack`` ids.

    Returns:
        ``{stack_id: [{"id", "name"}, ...]}`` sorted by set id, for the frozen
        stacks only. Empty when nothing is frozen.
    """
    members_by_stack = _stack_member_ids(session, stack_ids)
    if not members_by_stack:
        return {}
    detail = _locked_sets_by_picture(
        session, [pid for ids in members_by_stack.values() for pid in ids]
    )
    if not detail:
        return {}
    result: dict[int, list[dict]] = {}
    for stack_id, member_ids in members_by_stack.items():
        sets: dict[int, str] = {}
        for picture_id in member_ids:
            sets.update(dict(detail.get(picture_id, [])))
        if sets:
            result[stack_id] = [
                {"id": set_id, "name": name} for set_id, name in sorted(sets.items())
            ]
    return result


def enforce_stack_detach_not_locked(session: Session, stack_id, action: str) -> None:
    """Raise ``423`` if any member may not be detached from *stack_id*.

    The counterpart to :func:`enforce_stack_membership_not_locked`: that one
    guards a picture **joining** a stack, this one guards a picture **leaving**
    one. Both exist for the same reason, stacks are atomic for set membership,
    but leaving is the dangerous direction and it was the unguarded one.

    **A locked set freezes a stack's siblings THROUGH the stack.** Every
    picture-level guard runs on the stack-expanded id list
    (:func:`_locked_sets_by_picture`), so a member that is not itself in the
    locked set is still frozen while it shares a stack with one that is. Detach
    it and the freeze is simply gone: an operation that was refused ``423`` a
    moment ago succeeds. Two calls (unstack, then delete) therefore turned a hard
    freeze into a soft delete, which is a lock escape rather than a missing
    guard.

    **The whole stack is refused, never just the frozen member.** Removing any
    member changes the membership a locked set reconciles to, and detaching the
    *unfrozen* siblings is exactly the escape above, so there is no member of a
    touched stack that may safely leave. This is the same rule
    ``docs/design/keep-cover-only.md`` states ("Locked sets refuse the whole
    stack") and that :func:`~pixlstash.services.keep_cover_only_service.keep_cover_only_in_session`
    already enforces, applied to the three routes that detach members:
    ``POST /dedup/mixed-stacks/{id}/split``, ``POST /dedup/mixed-stacks/{id}/unstack``
    and ``DELETE /stacks/{id}/members``.

    **Soft-deleted members count.** All three call sites detach the stack's
    scrapheaped rows too when the stack dissolves, and a scrapheaped picture that
    is itself a locked-set member is still frozen (the ``deleted`` filter in
    :func:`locked_picture_id_subquery` applies only to the stack-derived arm).
    Checking live members only would leave that row detachable, which is the same
    escape one level down. See :func:`_stack_member_ids` for why that is a
    deferred escape rather than an immediate one, and why the whole stack is
    still refused: the live siblings of a scrapheaped frozen row are **not**
    themselves frozen, so this refusal is deliberately stricter than the
    picture-level guards on exactly that one state.

    Args:
        session: Pre-opened DB session.
        stack_id: The stack whose members are about to be detached. ``None`` and
            an unknown id are no-ops: there is no stack to freeze.
        action: Short human-facing verb phrase, echoed in the error detail.

    Raises:
        HTTPException: ``423`` with ``detail`` =
            ``{"code": "pictures_locked", "action", "sets": [{"id","name"}],
            "picture_ids": [...]}``, via :func:`enforce_pictures_not_locked`.
    """
    if stack_id is None:
        return
    member_ids = _stack_member_ids(session, [stack_id]).get(int(stack_id))
    if not member_ids:
        return
    # The raise itself stays in enforce_pictures_not_locked so the 423 body is
    # byte-identical to every other lock refusal. What this function owns is
    # WHICH ids are checked, and locked_sets_freezing_stacks answers the read
    # side from the same _stack_member_ids call, so prediction and enforcement
    # cannot disagree.
    enforce_pictures_not_locked(session, member_ids, action)


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


@dataclass(frozen=True)
class LockedSetLookup:
    """A :func:`locked_sets_for_pictures` result that knows which ids it covers.

    Built once for a whole page and reused per group, so a queue page costs three
    queries instead of three per group.

    **It carries its coverage on purpose.** The bare dict cannot distinguish "this
    picture is not frozen" from "this picture was never looked up", and both read
    as a falsy lookup. In a lock helper that difference is the whole point: the
    first is safe, the second silently admits a frozen picture. Asking for an id
    outside :attr:`covered` is a programming error and raises, rather than
    quietly answering "not frozen".
    """

    covered: frozenset
    sets_by_picture: dict

    def sets_for(self, picture_id: int) -> list[dict]:
        """The locked sets freezing *picture_id*, or ``[]`` when it is not frozen.

        Raises:
            KeyError: *picture_id* is outside this lookup's pool, so the answer
                would be a guess.
        """
        pid = int(picture_id)
        if pid not in self.covered:
            raise KeyError(
                f"picture {pid} is outside this LockedSetLookup's pool; "
                "build the lookup over every id you intend to ask about"
            )
        return [dict(entry) for entry in self.sets_by_picture.get(pid, [])]


def build_locked_set_lookup(session: Session, picture_ids) -> LockedSetLookup:
    """Resolve the locked-set membership of a whole pool of pictures at once."""
    ids = frozenset(int(pid) for pid in picture_ids if pid is not None)
    return LockedSetLookup(ids, locked_sets_for_pictures(session, sorted(ids)))


def partition_stackable_members(
    session: Session, picture_ids, lookup: Optional[LockedSetLookup] = None
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
        lookup: Optional pre-built :class:`LockedSetLookup` covering
            *picture_ids*. Built fresh from *session* when omitted. It raises if
            asked about an id it does not cover, so a caller that batches the
            lookup too narrowly fails loudly instead of stacking a frozen picture.

    Returns:
        A :class:`StackablePartition`. A group with fewer than two distinct
        candidates is returned whole and unblocked: the stack floor is the
        caller's error to raise, not this function's.

    Raises:
        KeyError: *lookup* was supplied and does not cover every id in
            *picture_ids*.
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

    frozen = lookup if lookup is not None else build_locked_set_lookup(session, ordered)
    blocked = [
        BlockedMember(pid, sets)
        for pid, sets in ((pid, frozen.sets_for(pid)) for pid in ordered)
        if sets
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
