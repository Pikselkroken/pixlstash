"""Keep cover only — collapsing a stack to its cover.

Owner of the one destructive action on the stack surface
(``docs/design/keep-cover-only.md``). A stack keeps its **current** leader and
every other live member is **soft-deleted to the Scrapheap**, as one operation
with one ``batch_id``, so a single ``Ctrl+Z`` puts the stack back.

Five properties this module exists to guarantee, each of which was a real hazard
in the design review:

1. **The metadata union is mandatory and unconditional.**
   :func:`~pixlstash.services.dedup_verdict_service.apply_metadata_union_in_session`
   is called from exactly one other place — the dedup stack verdict — so stacks
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
   so the link would be **destroyed** — see :func:`_character_loss`.

3. **A locked-set member refuses the WHOLE stack**, never just that member.
   Stack membership reconciles to the union of its members' sets
   (:mod:`~pixlstash.services.stack_membership`), so removing one member is
   exactly the mutation a locked set forbids — and a partial collapse is the
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
list — **never derived by subtraction**. The neighbouring auto-stack dialog once
reported "62 stacks to create" for work that would create 3, precisely because
its headline came from a different query than its rows.

The preview also reports the bytes the copies **hold**, deliberately named
:attr:`bytes_held_by_copies` and never ``bytes_freed``: a soft delete frees
nothing. Nothing is freed until the Scrapheap is emptied, and
``scrapheap_service.DEFAULT_RETENTION_DAYS`` is ``None``, so on a default
install it never empties on its own. The live
``scrapheap_retention_days`` setting is served alongside it so the client can
render "never" instead of hardcoding "30 days".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Optional

from sqlmodel import Session, select

from pixlstash.db_models import Character, Face, Picture, Tag
from pixlstash.db_models.tag import is_tag_sentinel
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service, scrapheap_service
from pixlstash.services.dedup_verdict_service import apply_metadata_union_in_session
from pixlstash.services.set_lock_service import (
    enforce_pictures_not_locked,
    locked_sets_for_pictures,
)
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
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
"""Upper bound on ``picture_ids`` **and** on ``stack_ids`` per request.

Each id costs a bounded amount of DB work, but an unbounded list would serialise
an arbitrary amount of it on the DB queue from one request (the reasoning behind
``BULK_DELETE_MAX_IDS``). It is set higher than that 1000 because the natural
gesture here is "select every stacked picture in the library and collapse it":
the owner's 160 stacks hold 574 pictures, and forcing that into chunks would
split one user gesture across several undo batches.
"""

SKIP_LOCKED = "set_locked"
"""Skip reason: a live member is frozen by a locked picture set."""

SKIP_CHARACTER_ON_COPY = "character_only_on_copy"
"""Skip reason: a character link exists only on a member that would leave."""

SKIP_SINGLE_MEMBER = "single_member"
"""Skip reason: fewer than :data:`MIN_STACK_MEMBERS` live members — nothing to do."""

SKIP_REASONS = (SKIP_SINGLE_MEMBER, SKIP_LOCKED, SKIP_CHARACTER_ON_COPY)
"""Every skip reason, in the order :func:`plan_in_session` evaluates them.

A stack can satisfy more than one; it is reported under the **first** that
matches, so the buckets stay disjoint.
"""


class KeepCoverOnlyError(Exception):
    """Raised when a keep-cover-only request cannot be honoured as asked."""


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
        copy_ids: The live members that would move to the Scrapheap — i.e.
            :attr:`member_ids` without the cover. Empty on a skipped stack.
        reference_copy_ids: The subset of :attr:`copy_ids` that belong to a
            reference folder. A **subset**, not a bucket: their rows move like
            any other, but their files are user-managed and are not touched.
        bytes_held: Sum of ``size_bytes`` over :attr:`copy_ids`. Bytes *held*,
            never bytes freed — a soft delete frees nothing.
        gains_tags: The union would copy at least one tag onto the cover.
        gains_score: The union would lift the cover's score.
        skip_reason: One of :data:`SKIP_REASONS`, or ``None`` when eligible.
        locked_sets: ``[{"id", "name"}, ...]`` freezing this stack. Non-empty
            only for :data:`SKIP_LOCKED`.
        lost_characters: ``[{"id", "name", "picture_ids"}, ...]`` naming each
            character whose only link sits on a copy. Non-empty only for
            :data:`SKIP_CHARACTER_ON_COPY`.
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
        }


@dataclass(frozen=True)
class KeepCoverOnlyPlan:
    """The whole dry run: one :class:`StackPlan` per selected stack.

    Attributes:
        stacks: Every stack the selection resolved to, in stack-id order.
        unknown_stack_ids: Ids the caller named that resolve to no live stack.
            Reported **outside** the bucket arithmetic below, because they are
            not stacks — a caller that names a purged or dissolved stack should
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

    Args:
        raw_ids: The raw JSON value; ``None`` and an absent field both mean "no
            ids of this kind", which is legal as long as the other kind has some.
        label: The field name, echoed in the error so the client is told which
            of the two lists was wrong.

    Raises:
        KeepCoverOnlyError: The value is not a list, carries a non-integer, or
            is longer than :data:`MAX_SELECTION_IDS`.
    """
    if raw_ids is None:
        return []
    if not isinstance(raw_ids, (list, tuple)):
        raise KeepCoverOnlyError(f"{label} must be a list of integers")
    ids: set[int] = set()
    for raw in raw_ids:
        if isinstance(raw, bool):
            raise KeepCoverOnlyError(f"{label} must contain valid integers")
        try:
            ids.add(int(raw))
        except (TypeError, ValueError) as exc:
            raise KeepCoverOnlyError(f"{label} must contain valid integers") from exc
    if len(ids) > MAX_SELECTION_IDS:
        raise KeepCoverOnlyError(
            f"{label} exceeds the maximum of {MAX_SELECTION_IDS} ids per request"
        )
    return sorted(ids)


def resolve_selection_in_session(
    session: Session,
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
) -> tuple[list[int], list[int]]:
    """Turn a mixed grid selection into the stacks it names.

    **The unit is the stack.** A selection *names* stacks: any selected picture
    pulls in its whole stack, so a partial selection inside a stack collapses the
    whole stack (the dialog must say so — it is the one place this action does
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
        ``(resolved_stack_ids, unknown_stack_ids)`` — the stacks with at least
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
    The first entry is therefore the stack's current cover — the same row the
    grid renders — so this function and the grid can never disagree about which
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
    members: list[Picture], faces_by_picture: dict[int, set[int]]
) -> dict[int, list[int]]:
    """Characters this stack would lose, mapped to the copies that carry them.

    Mirrors ``apply_metadata_union_in_session`` **exactly**, because a mismatch
    in either direction is a bug: predicting a loss the union prevents skips a
    stack for nothing, and missing one destroys a character link.

    * The union assigns a character to the cover — through
      ``pending_character_id``, never a fabricated ``Face`` row — only when the
      stack references **exactly one** character. That case therefore loses
      nothing and is not reported here.
    * With more than one character the union writes nothing, so every character
      the cover does not already hold walks out with its copy.

    A copy's own ``pending_character_id`` is deliberately **not** treated as a
    link to preserve. It is an unconfirmed suggestion, and it is what the union
    itself writes — so counting it would make an already-unioned stack look like
    it was about to lose the very character the union just propagated.

    Returns:
        ``{character_id: [picture ids carrying it]}``, empty when nothing is
        lost.
    """
    if len(members) < MIN_STACK_MEMBERS:
        return {}
    cover = members[0]
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
        for character_id in faces_by_picture.get(int(member.id), set()) & lost:
            carriers.setdefault(character_id, []).append(int(member.id))
    return {cid: sorted(pids) for cid, pids in carriers.items()}


def plan_in_session(
    session: Session,
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
) -> KeepCoverOnlyPlan:
    """Decide, in one read, exactly what Keep cover only would do.

    **The single source of truth for both the dry run and the mutation.** The
    preview endpoint renders this; the mutation endpoint acts on it. They cannot
    disagree about a selection because there is only one function that reads it.

    Every stack lands in **exactly one** bucket. A stack can satisfy more than
    one test, so the evaluation order below is the tie-break, and it is what
    keeps the buckets disjoint:

    1. :data:`SKIP_SINGLE_MEMBER` — fewer than two live members, so there is no
       copy to move. Checked first because the other two tests are meaningless
       on a stack of one.
    2. :data:`SKIP_LOCKED` — any live member is frozen by a locked picture set.
       The **whole** stack is refused: stack membership reconciles to the union
       of its members' sets, so removing one member is the mutation the lock
       forbids, and a partial collapse is the worst outcome available.
    3. :data:`SKIP_CHARACTER_ON_COPY` — see :func:`_character_loss`.

    Everything else is eligible. No bucket is ever computed by subtraction: each
    stack is appended to exactly one, and :func:`preview_in_session` asserts the
    sum.

    Args:
        session: Pre-opened DB session.
        stack_ids: Stacks named directly.
        picture_ids: Pictures whose stacks should be collapsed.

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
    # Computed once per stack and threaded through, so the names query and the
    # per-stack classification can never disagree about what would be lost.
    loss_by_stack = {
        stack_id: _character_loss(members, faces_by_picture)
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
) -> StackPlan:
    """Classify one stack into exactly one bucket. See :func:`plan_in_session`."""
    cover = members[0]
    cover_id = int(cover.id)
    copies = list(members[1:])
    copy_ids = [int(pic.id) for pic in copies]
    member_ids = [cover_id, *copy_ids]

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

    if lost_characters:
        return StackPlan(
            copy_ids=[],
            skip_reason=SKIP_CHARACTER_ON_COPY,
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

    return StackPlan(copy_ids=copy_ids, **common)


# --- The dry run ------------------------------------------------------------


def preview_in_session(
    session: Session,
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
    retention_days: Optional[int] = None,
) -> dict[str, Any]:
    """The confirm dialog's only source of truth, in one read.

    Args:
        session: Pre-opened DB session.
        stack_ids: Stacks named directly.
        picture_ids: Pictures whose stacks should be collapsed.
        retention_days: The live ``scrapheap_retention_days`` setting, read by
            the handler from server-config and passed in so the client never
            hardcodes a window. ``None`` means "Never" — the default — in which
            case the Scrapheap never empties on its own.

    Returns:
        The response body documented on
        ``POST /api/v1/stacks/keep-cover-only/preview``. The four stack buckets
        are disjoint and sum to ``stacks_selected``; the sum is asserted here so
        a future bucket cannot be added without being counted.
    """
    plan = plan_in_session(session, stack_ids, picture_ids)
    eligible = plan.eligible
    skipped_locked = plan.skipped(SKIP_LOCKED)
    skipped_character = plan.skipped(SKIP_CHARACTER_ON_COPY)
    skipped_single = plan.skipped(SKIP_SINGLE_MEMBER)

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
) -> dict[str, Any]:
    """Collapse every eligible stack in the selection to its cover.

    One session, one commit, **one** operation-log row under **one** ``batch_id``
    — so the whole gesture is a single ``Ctrl+Z`` however many stacks it named.

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

    Returns:
        The response body documented on ``POST /api/v1/stacks/keep-cover-only``,
        plus ``event_picture_ids`` for the vault wrapper's announcement.
    """
    plan = plan_in_session(session, stack_ids, picture_ids)
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
    # fire if the planner and the lock helper disagree — and in that case
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
        op_type=OP_TYPE_KEEP_COVER_ONLY,
        before=before,
        after=after,
        batch_id=batch_id,
        summary=operation_log_service.keep_cover_only_summary(
            len(eligible), len(moving)
        ),
        actor=actor,
        source=source,
        origin_client_id=origin_client_id,
    )
    if recorded is None:
        # Every eligible stack soft-deletes at least one live member, so the diff
        # cannot be empty. If it somehow is, returning a batch id that points at
        # no operation would hand the client a broken undo handle — so the handle
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
) -> dict[str, Any]:
    """Read-only vault wrapper around :func:`preview_in_session`."""
    return vault.db.run_immediate_read_task(
        preview_in_session,
        list(stack_ids or []),
        list(picture_ids or []),
        retention_days,
    )


def keep_cover_only(
    vault: "Vault",
    stack_ids: Optional[Iterable[int]] = None,
    picture_ids: Optional[Iterable[int]] = None,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
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
    if covers and (result.get("tags_added") or result.get("scores_lifted")):
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


__all__ = [
    "KeepCoverOnlyError",
    "KeepCoverOnlyPlan",
    "MAX_SELECTION_IDS",
    "MIN_STACK_MEMBERS",
    "OP_TYPE_KEEP_COVER_ONLY",
    "SKIP_CHARACTER_ON_COPY",
    "SKIP_LOCKED",
    "SKIP_REASONS",
    "SKIP_SINGLE_MEMBER",
    "StackPlan",
    "coerce_selection_ids",
    "keep_cover_only",
    "keep_cover_only_in_session",
    "plan_in_session",
    "preview",
    "preview_in_session",
    "read_retention_days",
    "resolve_selection_in_session",
]
