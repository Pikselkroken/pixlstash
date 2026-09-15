"""Keep cover only: collapsing a stack to its cover.

Owner of the one destructive action on the stack surface
(``docs/design/keep-cover-only.md``). A stack keeps its **current** leader and
every other live member is **soft-deleted to the Scrapheap**, as one operation
with one ``batch_id``, so a single ``Ctrl+Z`` puts the stack back.

Five properties this module exists to guarantee, each of which was a real hazard
in the design review:

1. **The metadata union is mandatory and unconditional.**
   :func:`~pixlstash.services.dedup_verdict_service.apply_metadata_union_in_session`
   is called from exactly one other place, the dedup stack verdict, so stacks
   made by hand in the grid have **never** been unioned. Measured on the owner's
   library: **110 of 160 stacks** have a copy carrying tags the cover lacks.
   Collapsing without unioning first is therefore silent metadata loss on two
   thirds of a real library. The union runs on every eligible stack, before any
   soft delete, and it is idempotent where it already ran. Do not optimise it
   away on the grounds that the queue does it: the queue is not the only way
   stacks get made.

2. **A stack whose only link to a character sits on a non-cover member is
   skipped, counted and named.** The union deliberately refuses to guess when
   the members reference more than one character. Under *stacking* that is
   right, because nothing is lost. Here the copy carrying the link would leave,
   so the link would be **destroyed**, see :func:`_character_loss`.

3. **A locked-set member refuses the WHOLE stack**, never just that member.
   Stack membership reconciles to the union of its members' sets
   (:mod:`~pixlstash.services.stack_membership`), so removing one member is
   exactly the mutation a locked set forbids, and a partial collapse is the
   worst outcome available: some copies gone, the stack still there, no visible
   reason. Siblings in the same request still proceed, matching the shipped bulk
   soft-delete's skip-and-report behaviour.

4. **The stack is not dissolved and no member is detached.** A soft-deleted
   picture keeps its ``stack_id``; ``POST /pictures/scrapheap/restore`` already
   clears ``deleted_at`` and re-normalizes positions, so a restored copy
   genuinely rejoins its stack. Leaving the row intact is what makes undo a flag
   flip. No "stack of 1" is ever rendered, because the grid's badge gates on
   *live* members.

5. **Soft delete only.** :mod:`~pixlstash.services.scrapheap_service` opens by
   stating there is deliberately no second permanent-destruction path. This is
   not it: it reuses the same soft delete the grid's ``Delete`` uses. Nothing is
   removed from disk, and no reference-folder original is touched.

The dry run
-----------
:func:`preview_in_session` is the dialog's **only** source of truth, computed in
one read over the same selection the mutation acts on and through the same
:func:`plan_in_session`. Its stack buckets are **disjoint and sum to
``stacks_selected``**, and every one of them is counted by appending to its own
list: **never derived by subtraction**. The neighbouring auto-stack dialog once
reported "62 stacks to create" for work that would create 3, precisely because
its headline came from a different query than its rows.

The preview also reports the bytes the copies **hold**, deliberately named
:attr:`bytes_held_by_copies` and never ``bytes_freed``: a soft delete frees
nothing. Nothing is freed until the Scrapheap is emptied, and
``scrapheap_service.DEFAULT_RETENTION_DAYS`` is ``None``, so on a default
install it never empties on its own. The live
``scrapheap_retention_days`` setting is served alongside it so the client can
render "never" instead of hardcoding "30 days".

Keep recipes only
-----------------
The same action with one more condition on every copy: it moves only when it
could be **made again** after the Scrapheap is emptied (issue #1315). That needs
four things, checked in this order so each copy that stays has exactly one
reason (:data:`STAYING_REASONS`): an instance row in the hub (the parameters
and, filed with it, the recipe), every model the recipe loads still on the shelf,
a thumbnail (a ghost is never written without one), and a ghost the retention
setting will actually keep at purge (:mod:`~pixlstash.services.workflow_ghost_service`).
Copies that fail stay in their stack, live. A stack with no copy that passes is
skipped as :data:`SKIP_NOTHING_REPRODUCIBLE`, never collapsed to nothing.

The pixels still leave through the Scrapheap and nothing else: the ghost is
written by the purge, under the setting in force then. The dialog can raise that
setting to ``on`` (``keep_every_ghost``), which is the only way this action
changes anything outside the library, and it is planned under that position so
the figures describe what the button does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Optional

from sqlmodel import Session, select

from pixlstash.db_models import Character, Face, Picture, Tag
from pixlstash.db_models.tag import is_tag_sentinel
from pixlstash.event_types import EventType
from pixlstash.hub.workflows import filed_instance_hashes, recipes_missing_a_model
from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service, scrapheap_service
from pixlstash.services.dedup_verdict_service import apply_metadata_union_in_session
from pixlstash.services.set_lock_service import (
    enforce_pictures_not_locked,
    locked_sets_for_pictures,
)
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.services.workflow_ghost_service import (
    GHOST_RETENTION_COVERED,
    GHOST_RETENTION_ON,
)
from pixlstash.utils.sql_chunking import chunked
from pixlstash.stacking import normalize_stack_positions

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)


# --- Constants --------------------------------------------------------------

OP_TYPE_KEEP_COVER_ONLY = operation_log_service.OP_STACK_KEEP_COVER_ONLY
"""Dotted op type recorded for the collapse; re-exported for callers/tests."""

MIN_STACK_MEMBERS = 2
"""A stack needs two live members before there is anything to collapse."""

MAX_SELECTION_IDS = 2000
"""Upper bound on the ids one request may carry, **across both lists together**.

Each id costs a bounded amount of DB work, but an unbounded list would serialise
an arbitrary amount of it on the DB queue from one request (the reasoning behind
``BULK_DELETE_MAX_IDS``). It is set higher than that 1000 because the natural
gesture here is "select every stacked picture in the library and collapse it":
the owner's 160 stacks hold 574 pictures, and forcing that into chunks would
split one user gesture across several undo batches.

**It bounds the request, not the result.** Two ways this cap used to be nothing
of the kind, both of which a client could reach on purpose:

* it was applied to the **de-duplicated** set, so a body of 2,000,000 repeats of
  the same id passed. The cost being bounded is the cost of *parsing and
  de-duplicating the body*, which happens first, so :func:`coerce_selection_ids`
  now checks the raw length before the loop;
* ``stack_ids`` and ``picture_ids`` were capped **separately**, so one request
  legitimately carried 4,000. :func:`enforce_selection_budget` applies the cap to
  their sum, which is what "per request" has to mean.
"""

SKIP_LOCKED = "set_locked"
"""Skip reason: a live member is frozen by a locked picture set."""

SKIP_CHARACTER_ON_COPY = "character_only_on_copy"
"""Skip reason: a character link exists only on a member that would leave."""

SKIP_SINGLE_MEMBER = "single_member"
"""Skip reason: fewer than :data:`MIN_STACK_MEMBERS` live members, nothing to do."""

SKIP_NOTHING_REPRODUCIBLE = "nothing_reproducible"
"""Skip reason (Keep recipes only): no copy in the stack could be made again."""

SKIP_REASONS = (
    SKIP_SINGLE_MEMBER,
    SKIP_LOCKED,
    SKIP_NOTHING_REPRODUCIBLE,
    SKIP_CHARACTER_ON_COPY,
)
"""Every skip reason, in the order :func:`plan_in_session` evaluates them.

A stack can satisfy more than one; it is reported under the **first** that
matches, so the buckets stay disjoint. :data:`SKIP_NOTHING_REPRODUCIBLE` only
occurs under Keep recipes only.
"""

OP_TYPE_KEEP_RECIPES_ONLY = operation_log_service.OP_STACK_KEEP_RECIPES_ONLY
"""Dotted op type recorded for Keep recipes only."""

STAY_NO_RECIPE = "no_recipe"
"""A copy stays: the hub holds no instance of its recipe for this library."""

STAY_MODEL_MISSING = "model_missing"
"""A copy stays: its recipe loads a model the shelf does not hold."""

STAY_NO_THUMBNAIL = "no_thumbnail"
"""A copy stays: it has no thumbnail yet, and a ghost is never written without one."""

STAY_GHOST_NOT_KEPT = "ghost_not_kept"
"""A copy stays: the retention setting would not keep its ghost at purge."""

STAYING_REASONS = (
    STAY_NO_RECIPE,
    STAY_MODEL_MISSING,
    STAY_NO_THUMBNAIL,
    STAY_GHOST_NOT_KEPT,
)
"""Why a copy stays under Keep recipes only, in evaluation order (first wins)."""


class KeepCoverOnlyError(Exception):
    """Raised when a keep-cover-only request cannot be honoured as asked."""


@dataclass(frozen=True)
class RecipeCheck:
    """What Keep recipes only needs beyond the vault.

    Attributes:
        hub: The hub database, or ``None`` for a vault opened without one, in
            which case no copy can be made again and every copy stays.
        library_uuid: The active library, which keys instance rows and ghosts.
        ghost_retention: The position to plan under: the live setting, or
            ``on`` when the dialog's keep-every-ghost box is ticked.
    """

    hub: Any
    library_uuid: Optional[str]
    ghost_retention: str


# --- The plan ---------------------------------------------------------------


@dataclass(frozen=True)
class StackPlan:
    """What Keep cover only would do to one stack, decided before anything runs.

    Attributes:
        stack_id: The stack.
        cover_picture_id: Its **current** leader. This action never picks a new
            cover; fusing a cover choice into a destructive click is two
            decisions in one press.
        member_ids: Every live member, cover first, in leader order.
        copy_ids: The live members that would move to the Scrapheap, i.e.
            :attr:`member_ids` without the cover. Empty on a skipped stack.
        reference_copy_ids: The subset of :attr:`copy_ids` that belong to a
            reference folder. A **subset**, not a bucket: their rows move like
            any other, but their files are user-managed and are not touched.
        bytes_held: Sum of ``size_bytes`` over :attr:`copy_ids`. Bytes *held*,
            never bytes freed: a soft delete frees nothing.
        gains_tags: The union would copy at least one tag onto the cover.
        gains_score: The union would lift the cover's score.
        skip_reason: One of :data:`SKIP_REASONS`, or ``None`` when eligible.
        locked_sets: ``[{"id", "name"}, ...]`` freezing this stack. Non-empty
            only for :data:`SKIP_LOCKED`.
        lost_characters: ``[{"id", "name", "picture_ids"}, ...]`` naming each
            character whose only link sits on a copy. Non-empty only for
            :data:`SKIP_CHARACTER_ON_COPY`.
        staying_copies: Keep recipes only: ``{reason: [picture ids]}`` for the
            copies that stay live, one of :data:`STAYING_REASONS` each. Empty
            for Keep cover only and for a stack refused before eligibility was
            judged (single member, locked).
    """

    stack_id: int
    cover_picture_id: int
    member_ids: list[int]
    copy_ids: list[int]
    reference_copy_ids: list[int]
    bytes_held: int
    gains_tags: bool
    gains_score: bool
    skip_reason: Optional[str] = None
    locked_sets: list[dict] = field(default_factory=list)
    lost_characters: list[dict] = field(default_factory=list)
    staying_copies: dict[str, list[int]] = field(default_factory=dict)

    @property
    def eligible(self) -> bool:
        """Whether this stack would actually collapse."""
        return self.skip_reason is None

    def as_dict(self) -> dict[str, Any]:
        """Serialise one row of the dry run, for the confirm dialog."""
        return {
            "stack_id": self.stack_id,
            "cover_picture_id": self.cover_picture_id,
            "member_count": len(self.member_ids),
            "copy_picture_ids": list(self.copy_ids),
            "reference_folder_picture_ids": list(self.reference_copy_ids),
            "bytes_held_by_copies": self.bytes_held,
            "cover_gains_tags": self.gains_tags,
            "cover_gains_score": self.gains_score,
            "eligible": self.eligible,
            "skip_reason": self.skip_reason,
            "locked_sets": [dict(entry) for entry in self.locked_sets],
            "lost_characters": [dict(entry) for entry in self.lost_characters],
            "staying_picture_ids": {
                reason: list(ids) for reason, ids in self.staying_copies.items()
            },
        }


@dataclass(frozen=True)
class KeepCoverOnlyPlan:
    """The whole dry run: one :class:`StackPlan` per selected stack.

    Attributes:
        stacks: Every stack the selection resolved to, in stack-id order.
        unknown_stack_ids: Ids the caller named that resolve to no live stack.
            Reported **outside** the bucket arithmetic below, because they are
            not stacks: a caller that names a purged or dissolved stack should
            see that, not have it folded into a skip count.
    """

    stacks: list[StackPlan]
    unknown_stack_ids: list[int] = field(default_factory=list)

    @property
    def eligible(self) -> list[StackPlan]:
        return [plan for plan in self.stacks if plan.eligible]

    def skipped(self, reason: str) -> list[StackPlan]:
        """Every stack skipped for exactly *reason*."""
        return [plan for plan in self.stacks if plan.skip_reason == reason]

    @property
    def moving_picture_ids(self) -> list[int]:
        """Every picture that would move, across the eligible stacks."""
        return sorted(pid for plan in self.eligible for pid in plan.copy_ids)


# --- Selection --------------------------------------------------------------


def coerce_selection_ids(raw_ids, label: str) -> list[int]:
    """Validate and de-duplicate one id list from the request body.

    **The length is checked on the raw list, before de-duplication.** Checking
    the de-duplicated set instead bounds the *outcome* of the work rather than
    the request: a body of two million repeats of one id de-duplicates to a
    single id and was accepted outright. What this cannot un-do is Pydantic
    having already materialised that list while binding the request model, which
    is inherent to any modelled body and is bounded by the transport, not here;
    what it does stop is the request being *honoured*, and the set build and DB
    work behind it.

    Args:
        raw_ids: The raw JSON value; ``None`` and an absent field both mean "no
            ids of this kind", which is legal as long as the other kind has some.
        label: The field name, echoed in the error so the client is told which
            of the two lists was wrong.

    Raises:
        KeepCoverOnlyError: The value is not a list, carries a non-integer, or
            holds more than :data:`MAX_SELECTION_IDS` entries.
    """
    if raw_ids is None:
        return []
    if not isinstance(raw_ids, (list, tuple)):
        raise KeepCoverOnlyError(f"{label} must be a list of integers")
    if len(raw_ids) > MAX_SELECTION_IDS:
        raise KeepCoverOnlyError(
            f"{label} exceeds the maximum of {MAX_SELECTION_IDS} ids per request"
        )
    ids: set[int] = set()
    for raw in raw_ids:
        if isinstance(raw, bool):
            raise KeepCoverOnlyError(f"{label} must contain valid integers")
        try:
            ids.add(int(raw))
        except (TypeError, ValueError) as exc:
            raise KeepCoverOnlyError(f"{label} must contain valid integers") from exc
    return sorted(ids)


def enforce_selection_budget(stack_ids, picture_ids) -> None:
    """Raise when the two id lists together exceed :data:`MAX_SELECTION_IDS`.

    The cap is documented and reasoned about as a **per-request** bound, so it
    has to be applied to the request. Capping each list on its own let one call
    carry ``MAX_SELECTION_IDS`` stacks *and* ``MAX_SELECTION_IDS`` pictures, i.e.
    twice the work the number was chosen to permit, and the two are unioned into
    one selection anyway (:func:`resolve_selection_in_session`).

    Args:
        stack_ids: The coerced stack-id list.
        picture_ids: The coerced picture-id list.

    Raises:
        KeepCoverOnlyError: The combined count is over the cap.
    """
    total = len(stack_ids or []) + len(picture_ids or [])
    if total > MAX_SELECTION_IDS:
        raise KeepCoverOnlyError(
            f"stack_ids and picture_ids together carry {total} ids, over the "
            f"maximum of {MAX_SELECTION_IDS} per request"
        )


def resolve_selection_in_session(
    session: Session,
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
) -> tuple[list[int], list[int]]:
    """Turn a mixed grid selection into the stacks it names.

    **The unit is the stack.** A selection *names* stacks: any selected picture
    pulls in its whole stack, so a partial selection inside a stack collapses the
    whole stack (the dialog must say so: it is the one place this action does
    more than the selection literally names). Loose pictures name no stack and
    are ignored, which is honest only because the dialog counts *stacks*.

    Args:
        session: Pre-opened DB session.
        stack_ids: Stacks named directly.
        picture_ids: Pictures whose stacks should be collapsed. Soft-deleted
            pictures are ignored: a scrapheaped row is not something the grid
            can select, and honouring it would let a selection reach a stack the
            user cannot see.

    Returns:
        ``(resolved_stack_ids, unknown_stack_ids)``: the stacks with at least
        one live member, and the explicitly named ids that have none.
    """
    named = {int(sid) for sid in (stack_ids or []) if sid is not None}
    seeds = [int(pid) for pid in (picture_ids or []) if pid is not None]
    from_pictures: set[int] = set()
    if seeds:
        from_pictures = {
            int(sid)
            for sid in session.exec(
                select(Picture.stack_id).where(
                    Picture.id.in_(seeds),
                    Picture.stack_id.is_not(None),
                    Picture.deleted.is_(False),
                )
            ).all()
            if sid is not None
        }
    candidates = named | from_pictures
    if not candidates:
        return [], sorted(named)

    live = {
        int(sid)
        for sid in session.exec(
            select(Picture.stack_id).where(
                Picture.stack_id.in_(sorted(candidates)),
                Picture.deleted.is_(False),
            )
        ).all()
        if sid is not None
    }
    return sorted(live), sorted(named - live)


# --- Planning ---------------------------------------------------------------


def _live_members_by_stack(
    session: Session, stack_ids: list[int]
) -> dict[int, list[Picture]]:
    """Load every live member of *stack_ids*, each stack in leader order.

    Leader order is the one :func:`~pixlstash.stacking.normalize_stack_positions`
    writes: explicit positions ascending, ``NULL`` positions last, ties by id.
    The first entry is therefore the stack's current cover, the same row the
    grid renders, so this function and the grid can never disagree about which
    picture is kept.
    """
    if not stack_ids:
        return {}
    members: dict[int, list[Picture]] = {}
    for picture in session.exec(
        select(Picture).where(
            Picture.stack_id.in_(stack_ids),
            Picture.deleted.is_(False),
        )
    ).all():
        members.setdefault(int(picture.stack_id), []).append(picture)
    for stack_id in list(members):
        members[stack_id].sort(
            key=lambda pic: (
                pic.stack_position is None,
                pic.stack_position if pic.stack_position is not None else 0,
                int(pic.id or 0),
            )
        )
    return members


def _tags_by_picture(session: Session, picture_ids: list[int]) -> dict[int, set[str]]:
    """Real (non-sentinel) tags per picture, in one query.

    Sentinels are dropped for the same reason
    :func:`~pixlstash.services.dedup_verdict_service.apply_metadata_union_in_session`
    drops them: they are bookkeeping rows, not metadata, so a cover "gaining"
    one would be a figure the union never produces.
    """
    tags: dict[int, set[str]] = {pid: set() for pid in picture_ids}
    if not picture_ids:
        return tags
    for picture_id, tag in session.exec(
        select(Tag.picture_id, Tag.tag).where(Tag.picture_id.in_(picture_ids))
    ).all():
        if is_tag_sentinel(tag):
            continue
        tags.setdefault(int(picture_id), set()).add(str(tag))
    return tags


def _faces_by_picture(session: Session, picture_ids: list[int]) -> dict[int, set[int]]:
    """Assigned character ids per picture, in one query."""
    faces: dict[int, set[int]] = {}
    if not picture_ids:
        return faces
    for picture_id, character_id in session.exec(
        select(Face.picture_id, Face.character_id).where(
            Face.picture_id.in_(picture_ids),
            Face.character_id.is_not(None),
        )
    ).all():
        if character_id is None:
            continue
        faces.setdefault(int(picture_id), set()).add(int(character_id))
    return faces


def _character_loss(
    members: list[Picture],
    faces_by_picture: dict[int, set[int]],
    leaving_ids: Optional[set[int]] = None,
) -> dict[int, list[int]]:
    """Characters this stack would lose, mapped to the copies that carry them.

    Mirrors ``apply_metadata_union_in_session`` **exactly**, because a mismatch
    in either direction is a bug: predicting a loss the union prevents skips a
    stack for nothing, and missing one destroys a character link.

    * The union assigns a character to the cover: through
      ``pending_character_id``, never a fabricated ``Face`` row, only when the
      stack references **exactly one** character. That case therefore loses
      nothing and is not reported here.
    * With more than one character the union writes nothing, so every character
      the cover does not already hold walks out with its copy.

    ``leaving_ids`` narrows who leaves (Keep recipes only moves some copies and
    not others): a character a staying copy still carries is not lost. ``None``
    means every copy leaves.

    A copy's own ``pending_character_id`` is deliberately **not** treated as a
    link to preserve. It is an unconfirmed suggestion, and it is what the union
    itself writes, so counting it would make an already-unioned stack look like
    it was about to lose the very character the union just propagated.

    Returns:
        ``{character_id: [picture ids carrying it]}``, empty when nothing is
        lost.
    """
    if len(members) < MIN_STACK_MEMBERS:
        return {}
    cover = members[0]
    if leaving_ids is None:
        leaving_ids = {int(member.id) for member in members[1:]}
    stack_characters: set[int] = set()
    for member in members:
        stack_characters |= faces_by_picture.get(int(member.id), set())
    if not stack_characters:
        return {}

    retained = set(faces_by_picture.get(int(cover.id), set()))
    if cover.pending_character_id is not None:
        retained.add(int(cover.pending_character_id))
    if len(stack_characters) == 1:
        # The union propagates the single unambiguous character onto the cover.
        retained |= stack_characters

    lost = stack_characters - retained
    if not lost:
        return {}
    carriers: dict[int, list[int]] = {}
    for member in members[1:]:
        # A staying copy keeps its own link, so only a leaving carrier loses one.
        if int(member.id) not in leaving_ids:
            continue
        for character_id in faces_by_picture.get(int(member.id), set()) & lost:
            carriers.setdefault(character_id, []).append(int(member.id))
    return {cid: sorted(pids) for cid, pids in carriers.items()}


def _staying_reasons_in_session(
    session: Session, copies: list[Picture], recipes: RecipeCheck
) -> dict[int, str]:
    """Why each copy that cannot be made again stays, as ``{picture_id: reason}``.

    A copy absent from the answer can move. Each copy gets the **first** of
    :data:`STAYING_REASONS` it fails, so the per-picture buckets are disjoint.

    Coverage (``covered``) is judged against what survives the action: a live
    picture carrying the same instance hash that is not itself one of the copies
    about to move. Two copies covering only each other both stay. That is a
    single pass and fails toward keeping pixels: a copy left to stay for another
    reason could have been cover, and is not counted as such.
    """
    reasons: dict[int, str] = {}
    hub, library_uuid = recipes.hub, recipes.library_uuid
    if hub is None or not library_uuid:
        return {int(pic.id): STAY_NO_RECIPE for pic in copies}

    filed = filed_instance_hashes(
        hub,
        library_uuid,
        [pic.workflow_instance_hash for pic in copies if pic.workflow_instance_hash],
    )
    missing_model = recipes_missing_a_model(
        hub,
        [
            pic.workflow_structural_hash
            for pic in copies
            if pic.workflow_structural_hash
        ],
    )
    passing: list[Picture] = []
    for pic in copies:
        if not pic.workflow_instance_hash or pic.workflow_instance_hash not in filed:
            reasons[int(pic.id)] = STAY_NO_RECIPE
        elif pic.workflow_structural_hash in missing_model:
            reasons[int(pic.id)] = STAY_MODEL_MISSING
        elif pic.thumbnail_width is None:
            reasons[int(pic.id)] = STAY_NO_THUMBNAIL
        else:
            passing.append(pic)

    if recipes.ghost_retention == GHOST_RETENTION_ON:
        return reasons
    covered: set[str] = set()
    if recipes.ghost_retention == GHOST_RETENTION_COVERED:
        moving_ids = {int(pic.id) for pic in passing}
        for batch in chunked(sorted({pic.workflow_instance_hash for pic in passing})):
            covered.update(
                instance_hash
                for picture_id, instance_hash in session.exec(
                    select(Picture.id, Picture.workflow_instance_hash).where(
                        Picture.workflow_instance_hash.in_(batch),
                        Picture.deleted.is_(False),
                    )
                ).all()
                if int(picture_id) not in moving_ids
            )
    for pic in passing:
        if pic.workflow_instance_hash not in covered:
            reasons[int(pic.id)] = STAY_GHOST_NOT_KEPT
    return reasons


def plan_in_session(
    session: Session,
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
    recipes: Optional[RecipeCheck] = None,
) -> KeepCoverOnlyPlan:
    """Decide, in one read, exactly what Keep cover only would do.

    **The single source of truth for both the dry run and the mutation.** The
    preview endpoint renders this; the mutation endpoint acts on it. They cannot
    disagree about a selection because there is only one function that reads it.

    Every stack lands in **exactly one** bucket. A stack can satisfy more than
    one test, so the evaluation order below is the tie-break, and it is what
    keeps the buckets disjoint:

    1. :data:`SKIP_SINGLE_MEMBER`: fewer than two live members, so there is no
       copy to move. Checked first because the other two tests are meaningless
       on a stack of one.
    2. :data:`SKIP_LOCKED`: any live member is frozen by a locked picture set.
       The **whole** stack is refused: stack membership reconciles to the union
       of its members' sets, so removing one member is the mutation the lock
       forbids, and a partial collapse is the worst outcome available.
    3. :data:`SKIP_NOTHING_REPRODUCIBLE` (Keep recipes only): no copy could be
       made again, so there is nothing to move.
    4. :data:`SKIP_CHARACTER_ON_COPY`: see :func:`_character_loss`, over the
       copies that would actually leave.

    Everything else is eligible. No bucket is ever computed by subtraction: each
    stack is appended to exactly one, and :func:`preview_in_session` asserts the
    sum.

    Args:
        session: Pre-opened DB session.
        stack_ids: Stacks named directly.
        picture_ids: Pictures whose stacks should be collapsed.
        recipes: Plan Keep recipes only instead: a copy moves only when it could
            be made again. ``None`` is Keep cover only.

    Returns:
        A :class:`KeepCoverOnlyPlan`.
    """
    resolved, unknown = resolve_selection_in_session(session, stack_ids, picture_ids)
    if not resolved:
        return KeepCoverOnlyPlan([], unknown)

    members_by_stack = _live_members_by_stack(session, resolved)
    all_member_ids = sorted(
        int(pic.id) for members in members_by_stack.values() for pic in members
    )
    tags_by_picture = _tags_by_picture(session, all_member_ids)
    faces_by_picture = _faces_by_picture(session, all_member_ids)
    # One batched lock lookup for the whole selection: per-stack calls would be
    # an N+1, and this is the same helper the bulk soft-delete and the scrapheap
    # purge use, so the three cannot disagree about what is frozen.
    locked_by_picture = locked_sets_for_pictures(session, all_member_ids)
    staying: Optional[dict[int, str]] = None
    if recipes is not None:
        # Judged only over stacks that could still collapse, so a locked stack's
        # copies are neither counted as staying nor as moving: they are cover.
        staying = _staying_reasons_in_session(
            session,
            [
                pic
                for members in members_by_stack.values()
                if len(members) >= MIN_STACK_MEMBERS
                and not any(locked_by_picture.get(int(m.id)) for m in members)
                for pic in members[1:]
            ],
            recipes,
        )
    # Computed once per stack and threaded through, so the names query and the
    # per-stack classification can never disagree about what would be lost.
    loss_by_stack = {
        stack_id: _character_loss(
            members,
            faces_by_picture,
            None
            if staying is None
            else {int(pic.id) for pic in members[1:] if int(pic.id) not in staying},
        )
        for stack_id, members in members_by_stack.items()
    }
    character_names = _character_names(
        session, {cid for lost in loss_by_stack.values() for cid in lost}
    )

    plans: list[StackPlan] = []
    for stack_id in resolved:
        members = members_by_stack.get(stack_id) or []
        if not members:
            # A stack that resolved live but has no member row is a contradiction
            # the read cannot see through; report it as unknown rather than
            # planning a collapse over nothing.
            logger.warning(
                "[keep-cover-only] stack %s resolved as live but loaded no "
                "members; treating it as unknown so nothing is planned for it",
                stack_id,
            )
            unknown.append(stack_id)
            continue
        plans.append(
            _plan_one_stack(
                stack_id,
                members,
                tags_by_picture,
                locked_by_picture,
                loss_by_stack.get(stack_id) or {},
                character_names,
                staying,
            )
        )
    return KeepCoverOnlyPlan(plans, sorted(set(unknown)))


def _character_names(session: Session, character_ids: set[int]) -> dict[int, str]:
    """Names for the characters a skip has to name, in one query."""
    if not character_ids:
        return {}
    return {
        int(cid): str(name)
        for cid, name in session.exec(
            select(Character.id, Character.name).where(
                Character.id.in_(sorted(character_ids))
            )
        ).all()
    }


def _plan_one_stack(
    stack_id: int,
    members: list[Picture],
    tags_by_picture: dict[int, set[str]],
    locked_by_picture: dict[int, list[dict]],
    lost_characters: dict[int, list[int]],
    character_names: dict[int, str],
    staying: Optional[dict[int, str]] = None,
) -> StackPlan:
    """Classify one stack into exactly one bucket. See :func:`plan_in_session`."""
    cover = members[0]
    cover_id = int(cover.id)
    member_ids = [int(pic.id) for pic in members]
    staying_copies: dict[str, list[int]] = {}
    copies = list(members[1:])
    if staying is not None:
        for pic in copies:
            reason = staying.get(int(pic.id))
            if reason is not None:
                staying_copies.setdefault(reason, []).append(int(pic.id))
        copies = [pic for pic in copies if int(pic.id) not in staying]
    copy_ids = [int(pic.id) for pic in copies]

    cover_tags = tags_by_picture.get(cover_id, set())
    union_tags: set[str] = set()
    for member in members:
        union_tags |= tags_by_picture.get(int(member.id), set())
    best_score = max((int(pic.score or 0) for pic in members), default=0)

    common = {
        "stack_id": stack_id,
        "cover_picture_id": cover_id,
        "member_ids": member_ids,
        "reference_copy_ids": [
            int(pic.id) for pic in copies if pic.reference_folder_id is not None
        ],
        "bytes_held": sum(int(pic.size_bytes or 0) for pic in copies),
        "gains_tags": bool(union_tags - cover_tags),
        "gains_score": best_score > int(cover.score or 0),
    }

    if len(members) < MIN_STACK_MEMBERS:
        return StackPlan(copy_ids=[], skip_reason=SKIP_SINGLE_MEMBER, **common)

    locked_sets: dict[int, str] = {}
    for member in members:
        for entry in locked_by_picture.get(int(member.id), []):
            locked_sets[int(entry["id"])] = str(entry["name"])
    if locked_sets:
        return StackPlan(
            copy_ids=[],
            skip_reason=SKIP_LOCKED,
            locked_sets=[
                {"id": sid, "name": name} for sid, name in sorted(locked_sets.items())
            ],
            **common,
        )

    if staying is not None and not copies:
        return StackPlan(
            copy_ids=[],
            skip_reason=SKIP_NOTHING_REPRODUCIBLE,
            staying_copies=staying_copies,
            **common,
        )

    if lost_characters:
        return StackPlan(
            copy_ids=[],
            skip_reason=SKIP_CHARACTER_ON_COPY,
            staying_copies=staying_copies,
            lost_characters=[
                {
                    "id": cid,
                    "name": character_names.get(cid),
                    "picture_ids": pids,
                }
                for cid, pids in sorted(lost_characters.items())
            ],
            **common,
        )

    return StackPlan(copy_ids=copy_ids, staying_copies=staying_copies, **common)


# --- The dry run ------------------------------------------------------------


def preview_in_session(
    session: Session,
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
    retention_days: Optional[int] = None,
    recipes: Optional[RecipeCheck] = None,
) -> dict[str, Any]:
    """The confirm dialog's only source of truth, in one read.

    Args:
        session: Pre-opened DB session.
        stack_ids: Stacks named directly.
        picture_ids: Pictures whose stacks should be collapsed.
        retention_days: The live ``scrapheap_retention_days`` setting, read by
            the handler from server-config and passed in so the client never
            hardcodes a window. ``None`` means "Never", the default, in which
            case the Scrapheap never empties on its own.
        recipes: Preview Keep recipes only; see :func:`plan_in_session`.

    Returns:
        The response body documented on
        ``POST /api/v1/stacks/keep-cover-only/preview``. The four stack buckets
        are disjoint and sum to ``stacks_selected``; the sum is asserted here so
        a future bucket cannot be added without being counted.
    """
    plan = plan_in_session(session, stack_ids, picture_ids, recipes)
    eligible = plan.eligible
    skipped_locked = plan.skipped(SKIP_LOCKED)
    skipped_character = plan.skipped(SKIP_CHARACTER_ON_COPY)
    skipped_single = plan.skipped(SKIP_SINGLE_MEMBER)
    skipped_nothing = plan.skipped(SKIP_NOTHING_REPRODUCIBLE)

    moving = plan.moving_picture_ids
    reference_moving = sorted(pid for row in eligible for pid in row.reference_copy_ids)
    covers_gaining_tags = [row for row in eligible if row.gains_tags]
    covers_gaining_score = [row for row in eligible if row.gains_score]
    covers_gaining_metadata = [
        row for row in eligible if row.gains_tags or row.gains_score
    ]

    buckets = (
        len(eligible)
        + len(skipped_locked)
        + len(skipped_character)
        + len(skipped_single)
        + len(skipped_nothing)
    )
    if buckets != len(plan.stacks):
        # Not a fallback: the arithmetic is the dialog's whole safety property,
        # so a mismatch must surface as a failure rather than as a wrong figure
        # on a destructive confirm.
        raise KeepCoverOnlyError(
            f"keep-cover-only preview buckets sum to {buckets} but "
            f"{len(plan.stacks)} stacks were selected; refusing to report "
            "figures that do not add up"
        )

    return {
        "stacks_selected": len(plan.stacks),
        "stacks_eligible": len(eligible),
        "stacks_skipped_locked": len(skipped_locked),
        "stacks_skipped_character_on_copy": len(skipped_character),
        "stacks_skipped_single_member": len(skipped_single),
        "pictures_moving": len(moving),
        "picture_ids_moving": moving,
        "covers_kept": len(eligible),
        "cover_picture_ids": [row.cover_picture_id for row in eligible],
        "covers_gaining_tags": len(covers_gaining_tags),
        "covers_gaining_score": len(covers_gaining_score),
        "covers_gaining_metadata": len(covers_gaining_metadata),
        "reference_folder_pictures_moving": len(reference_moving),
        "reference_folder_picture_ids_moving": reference_moving,
        "bytes_held_by_copies": sum(row.bytes_held for row in eligible),
        "originals_deleted_from_disk": 0,
        "scrapheap_retention_days": retention_days,
        "unknown_stack_ids": list(plan.unknown_stack_ids),
        "stacks": [row.as_dict() for row in plan.stacks],
        "keep_recipes": recipes is not None,
        "stacks_skipped_nothing_reproducible": len(skipped_nothing),
        "ghost_retention": recipes.ghost_retention if recipes else None,
        # Keep recipes only: the copies that stay live, per reason. Counted over
        # the stacks that are eligible or have nothing reproducible, which are the
        # only ones whose copies were judged one by one, so a copy of a locked
        # stack is never in two places.
        **{
            f"pictures_staying_{reason}": sum(
                len(row.staying_copies.get(reason, []))
                for row in [*eligible, *skipped_nothing]
            )
            for reason in STAYING_REASONS
        },
    }


# --- The mutation -----------------------------------------------------------


def keep_cover_only_in_session(
    session: Session,
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
    recipes: Optional[RecipeCheck] = None,
) -> dict[str, Any]:
    """Collapse every eligible stack in the selection to its cover.

    One session, one commit, **one** operation-log row under **one** ``batch_id``:
    so the whole gesture is a single ``Ctrl+Z`` however many stacks it named.

    Per eligible stack, in this order and never the other way round:

    1. :func:`~pixlstash.services.dedup_verdict_service.apply_metadata_union_in_session`
       over the stack's live members, so the cover carries the union of the
       tags and the best score **before** any copy leaves;
    2. soft-delete the non-cover members (``deleted`` + ``deleted_at``, the same
       pair the grid's ``Delete`` writes), leaving ``stack_id`` alone;
    3. :func:`~pixlstash.stacking.normalize_stack_positions`, so the cover holds
       position 0 and the scrapheaped copies sort behind it.

    Args:
        session: Pre-opened session; this commits once.
        stack_ids: Stacks named directly.
        picture_ids: Pictures whose stacks should be collapsed.
        batch_id: Operation-log batch; minted server-side when absent.
        actor / source / origin_client_id: §21 origin discipline, read from the
            request in the handler and passed down explicitly.
        recipes: Keep recipes only; see :func:`plan_in_session`. Copies that
            could not be made again stay live in their stack.

    Returns:
        The response body documented on ``POST /api/v1/stacks/keep-cover-only``,
        plus ``event_picture_ids`` for the vault wrapper's announcement.
    """
    plan = plan_in_session(session, stack_ids, picture_ids, recipes)
    eligible = plan.eligible
    moving = plan.moving_picture_ids

    result: dict[str, Any] = {
        "status": "success",
        "stacks_collapsed": len(eligible),
        "stack_ids_collapsed": [row.stack_id for row in eligible],
        "pictures_moved": 0,
        "picture_ids_moved": [],
        "cover_picture_ids": [row.cover_picture_id for row in eligible],
        "covers_gaining_metadata": 0,
        "tags_added": 0,
        "scores_lifted": 0,
        "reference_folder_pictures_moved": 0,
        "originals_deleted_from_disk": 0,
        "stacks_skipped_locked": [row.as_dict() for row in plan.skipped(SKIP_LOCKED)],
        "stacks_skipped_character_on_copy": [
            row.as_dict() for row in plan.skipped(SKIP_CHARACTER_ON_COPY)
        ],
        "stacks_skipped_single_member": [
            row.stack_id for row in plan.skipped(SKIP_SINGLE_MEMBER)
        ],
        "unknown_stack_ids": list(plan.unknown_stack_ids),
        "keep_recipes": recipes is not None,
        "stacks_skipped_nothing_reproducible": [
            row.as_dict() for row in plan.skipped(SKIP_NOTHING_REPRODUCIBLE)
        ],
        "pictures_staying": sum(
            len(ids) for row in eligible for ids in row.staying_copies.values()
        ),
        "batch_id": None,
        "event_picture_ids": [],
    }
    if not eligible:
        logger.info(
            "[keep-cover-only] nothing to collapse: %d stack(s) selected, "
            "%d locked, %d character-only-on-a-copy, %d single-member",
            len(plan.stacks),
            len(plan.skipped(SKIP_LOCKED)),
            len(plan.skipped(SKIP_CHARACTER_ON_COPY)),
            len(plan.skipped(SKIP_SINGLE_MEMBER)),
        )
        return result

    # Defense in depth, evaluated before ANY write so a refusal can never leave a
    # half-collapsed stack. :func:`plan_in_session` has already dropped every
    # stack with a frozen member into the ``set_locked`` bucket, so this can only
    # fire if the planner and the lock helper disagree and in that case
    # refusing outright is the only safe answer: skipping the picture would
    # produce exactly the partial collapse the design forbids, and soft-deleting
    # it would mutate a set the user froze. Nothing is committed until the end of
    # this function, so a raise here rolls the whole call back.
    enforce_pictures_not_locked(
        session,
        [pid for row in eligible for pid in row.member_ids],
        "keep only the cover of a locked stack",
    )

    batch_id = batch_id or operation_log_service.new_batch_id()
    # Snapshot the stack-expanded set INCLUDING soft-deleted members: the union
    # writes onto the cover, the soft delete writes onto the copies, and
    # normalize_stack_positions renumbers every member of the stack, deleted
    # ones included (§21.1). An unsnapshotted renumber is a change undo could
    # not reverse.
    undo_targets = expand_picture_ids_to_stacks(
        session,
        [pid for row in eligible for pid in row.member_ids],
        include_deleted=True,
    )
    before = operation_log_service.capture_state_in_session(session, undo_targets)

    tags_added = 0
    scores_lifted = 0
    covers_gaining_metadata = 0
    deleted_at = datetime.now(timezone.utc)
    for row in eligible:
        # UNCONDITIONAL, and before anything leaves. Stacks made by hand in the
        # grid have never been unioned; skipping this is silent metadata loss.
        union = apply_metadata_union_in_session(session, row.member_ids, row.stack_id)
        tags_added += int(union.get("tags_added") or 0)
        scores_lifted += int(union.get("scores_lifted") or 0)
        if row.gains_tags or row.gains_score:
            covers_gaining_metadata += 1
        session.flush()

        for picture in session.exec(
            select(Picture).where(Picture.id.in_(row.copy_ids))
        ).all():
            if picture.deleted:
                continue
            picture.deleted = True
            # Same retention clock the grid's Delete starts, stamped only on the
            # False -> True transition. stack_id is deliberately left alone: a
            # restored copy has to rejoin its stack.
            picture.deleted_at = deleted_at
            session.add(picture)
        session.flush()
        normalize_stack_positions(session, row.stack_id)

    after = operation_log_service.capture_state_in_session(session, undo_targets)
    recorded = operation_log_service.record_operation_in_session(
        session,
        op_type=OP_TYPE_KEEP_COVER_ONLY
        if recipes is None
        else OP_TYPE_KEEP_RECIPES_ONLY,
        before=before,
        after=after,
        batch_id=batch_id,
        summary=(
            operation_log_service.keep_cover_only_summary(len(eligible), len(moving))
            if recipes is None
            else operation_log_service.keep_recipes_only_summary(
                len(eligible), len(moving)
            )
        ),
        actor=actor,
        source=source,
        origin_client_id=origin_client_id,
    )
    if recorded is None:
        # Every eligible stack soft-deletes at least one live member, so the diff
        # cannot be empty. If it somehow is, returning a batch id that points at
        # no operation would hand the client a broken undo handle so the handle
        # is dropped and the anomaly is loud.
        logger.error(
            "[keep-cover-only] collapsed %d stack(s) moving %d picture(s) yet "
            "produced an empty operation diff; no operation was recorded and "
            "batch %s is dropped, so this change is NOT undoable",
            len(eligible),
            len(moving),
            batch_id,
        )
        batch_id = None
    session.commit()

    reference_moved = sum(len(row.reference_copy_ids) for row in eligible)
    logger.info(
        "[keep-cover-only] collapsed %d stack(s), moved %d picture(s) to the "
        "Scrapheap (%d in reference folders, nothing removed from disk), "
        "unioned %d tag(s) and lifted %d score(s) onto covers; skipped %d "
        "locked and %d character-only-on-a-copy; batch=%s",
        len(eligible),
        len(moving),
        reference_moved,
        tags_added,
        scores_lifted,
        len(plan.skipped(SKIP_LOCKED)),
        len(plan.skipped(SKIP_CHARACTER_ON_COPY)),
        batch_id,
    )
    result.update(
        {
            "pictures_moved": len(moving),
            "picture_ids_moved": moving,
            "covers_gaining_metadata": covers_gaining_metadata,
            "tags_added": tags_added,
            "scores_lifted": scores_lifted,
            "reference_folder_pictures_moved": reference_moved,
            "batch_id": batch_id,
            "event_picture_ids": sorted(undo_targets),
        }
    )
    return result


# --- Vault wrappers ---------------------------------------------------------


def preview(
    vault: "Vault",
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
    retention_days: Optional[int] = None,
    recipes: Optional[RecipeCheck] = None,
) -> dict[str, Any]:
    """Read-only vault wrapper around :func:`preview_in_session`."""
    return vault.db.run_immediate_read_task(
        preview_in_session,
        list(stack_ids or []),
        list(picture_ids or []),
        retention_days,
        recipes,
    )


def keep_cover_only(
    vault: "Vault",
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
    recipes: Optional[RecipeCheck] = None,
) -> dict[str, Any]:
    """Write-path vault wrapper around :func:`keep_cover_only_in_session`."""
    result = vault.db.run_task(
        keep_cover_only_in_session,
        list(stack_ids or []),
        list(picture_ids or []),
        batch_id,
        actor,
        source,
        origin_client_id,
        recipes,
    )
    moved = result.get("picture_ids_moved") or []
    covers = result.get("cover_picture_ids") or []
    if moved:
        # The copies leave every active grid view; the covers stay but now carry
        # the unioned tags and score, so the two halves are announced with the
        # change_kind each actually is.
        vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": sorted(int(pid) for pid in moved),
                "origin_client_id": origin_client_id,
                "change_kind": "removed",
                "source": source,
            },
        )
    if covers and moved:
        # The cover's STACK is what always changes here: it led a stack of five
        # and now leads nothing live, and a card renders that count as its stack
        # badge. So this announcement is unconditional, gated only on something
        # having actually moved, and it is deliberately separate from the
        # metadata-union one below: a collapse whose union added nothing used to
        # say nothing at all about the cover, which left every view rendering a
        # stack of five around a picture that was on its own.
        #
        # ``stack_count`` is the field name because that is the derived,
        # listing-only value the client re-reads; it is computed per stack over
        # LIVE members by ``_enrich_stack_counts`` and is absent from
        # ``GET /pictures/{id}/metadata``, so a per-card metadata refresh cannot
        # repair the badge. The SPA routes this field to its own targeted
        # stack-badge read (``useGridRealtimeSync``'s stack-facet branch).
        vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": sorted(int(pid) for pid in covers),
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
                "fields": ["stack_count"],
                "source": source,
            },
        )
    if covers and (result.get("tags_added") or result.get("scores_lifted")):
        # The union's own announcement, carrying no ``fields`` because a tag or
        # score change may affect any view. Kept separate from the stack one
        # above so neither narrows the other.
        vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": sorted(int(pid) for pid in covers),
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
                "source": source,
            },
        )
        vault.notify(EventType.CHANGED_TAGS, {"origin_client_id": origin_client_id})
    result.pop("event_picture_ids", None)
    return result


def read_retention_days(server) -> Optional[int]:
    """The live ``scrapheap_retention_days``, or ``None`` for "Never".

    A one-line indirection on purpose: the preview must serve the **configured**
    window, and the copy that renders it must branch on "never". Reading the
    constant instead would be the same class of error the whole dialog exists to
    avoid.
    """
    return scrapheap_service.read_retention_days(getattr(server, "_server_config", {}))


def recipe_check(vault: "Vault", keep_every_ghost: bool = False) -> RecipeCheck:
    """The :class:`RecipeCheck` for the active library, planned under the live
    ghost retention, or under ``on`` when the dialog asked to keep every ghost."""
    return RecipeCheck(
        hub=vault.hub,
        library_uuid=vault.library_uuid,
        ghost_retention=GHOST_RETENTION_ON
        if keep_every_ghost
        else vault.ghost_retention,
    )


__all__ = [
    "KeepCoverOnlyError",
    "KeepCoverOnlyPlan",
    "MAX_SELECTION_IDS",
    "MIN_STACK_MEMBERS",
    "OP_TYPE_KEEP_COVER_ONLY",
    "OP_TYPE_KEEP_RECIPES_ONLY",
    "RecipeCheck",
    "SKIP_CHARACTER_ON_COPY",
    "SKIP_LOCKED",
    "SKIP_NOTHING_REPRODUCIBLE",
    "SKIP_REASONS",
    "SKIP_SINGLE_MEMBER",
    "STAYING_REASONS",
    "STAY_GHOST_NOT_KEPT",
    "STAY_MODEL_MISSING",
    "STAY_NO_RECIPE",
    "STAY_NO_THUMBNAIL",
    "StackPlan",
    "coerce_selection_ids",
    "enforce_selection_budget",
    "keep_cover_only",
    "keep_cover_only_in_session",
    "plan_in_session",
    "preview",
    "preview_in_session",
    "read_retention_days",
    "recipe_check",
    "resolve_selection_in_session",
]
