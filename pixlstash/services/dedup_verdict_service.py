"""Applying and remembering duplicate verdicts.

There are exactly two verdicts in v1.9 and neither of them deletes anything:

* **stack** - the chosen members become one stack led by the chosen cover, with
  the metadata union applied; excluded members stay exactly where they were;
* **keep separate** - nothing changes on disk or in the picture rows, but the
  group's signature is remembered so no rescan and no re-import ever re-asks.

Both are recorded against the group *signature*, not against picture or group
ids, which is what makes the memory survive a re-import (see
:func:`pixlstash.services.dedup_tier_service.group_signature`). "Keep separate"
stands until :func:`reopen_verdict_in_session` is called from the Decided page's
"Clear decision" - or, since 2026-07-30, until its own operation is undone (see
below).

Metadata union (design delta 5)
-------------------------------
Stacking unions **tags, project membership, set membership and characters** onto
every member and lifts every member to the **highest score** in the group.
Nothing is overwritten and nothing is lost - a union cannot break an album,
which is the failure mode that burns Immich users.

Project and set membership already had a union in
:func:`pixlstash.services.stack_membership.reconcile_stack_membership`; this
module calls it and adds the three the design requires on top:

* **tags** - every member gains every non-sentinel tag any member carries.
  Sentinel tags (``__tag``, ``__tag:<engine>``) are pipeline markers, not user
  metadata, so they are deliberately excluded: copying a "needs retagging"
  marker onto a picture that was already tagged would re-queue it for no reason.
* **score** - every member is lifted to ``max(score)``. Only lifted: a union
  never lowers a rating the user set.
* **characters** - a real face-to-character union is not expressible without
  fabricating :class:`~pixlstash.db_models.face.Face` rows (a face has a bbox and
  an embedding that belong to one specific picture), and inventing detection data
  is worse than not unioning. Instead, when the group's members between them
  reference exactly **one** character, every member that does not already carry
  it gets ``Picture.pending_character_id`` set - the shipped deferred-assignment
  mechanism that the face-extraction task consumes. A group spanning several
  characters is left alone and logged; the members keep their own faces, which is
  the non-lossy outcome.

Operation log (§21)
-------------------
Every verdict raises an action receipt and lands in the operation log. Each
verdict - stack **and** keep-separate - records **exactly one**
:class:`~pixlstash.db_models.operation.Operation` row, and a bulk action shares a
single ``batch_id`` across every group in the run, so
``POST /operations/batches/{batch_id}/undo`` reverses a thousand verdicts in one
step. Keep-separate changes no picture facet, so its row carries empty
before/after payloads and the whole restore is done by its post-restore hook
(undo reopens the verdict and returns the group to the queue; redo re-resolves
it). Keep-separate was originally *not* op-logged (CSO ruling around #644: no
reversible picture facet); the owner explicitly reversed that ruling on
2026-07-30 and keep-separate is now a first-class, undoable operation,
symmetric with the stack verdict.

Clearing a decision ("Clear decision" on the Decided page) goes through
:func:`reopen_verdict_in_session`. Since 2026-07-30 a clear of a still-standing
``stacked`` verdict **dissolves the verdict's stack** by restoring the recorded
pre-verdict stack state from the verdict's own operation row - without that,
the reopened group failed the queue's two-stack-units rule and never returned
to review. That unstack is itself one undoable ``dedup.reopen`` operation; see
the function's docstring for the full contract.

Two details this module owns rather than inherits:

* **It does not go through** ``routes/stacks.py``. Those handlers already wrap
  themselves in ``run_recorded_metadata_task``; calling them would produce a
  second operation row per verdict, and "one verdict, one undo" would stop being
  true. :func:`_stack_members` does the stacking in-session instead, and this
  module records once around the whole verdict.
* **It snapshots the stack-expanded set**, not just the group's members
  (§21's ``expand_stacks`` rule). Folding an existing stack into the new one
  reparents co-members the group never named, and ``normalize_stack_positions``
  renumbers *every* member including soft-deleted ones - so the snapshot is taken
  over :func:`expand_picture_ids_to_stacks` with ``include_deleted=True``, or an
  undo would restore the group and leave its siblings behind.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureStack
from pixlstash.db_models.dedup import (
    TIER_EXACT,
    VERDICT_KEEP_SEPARATE,
    VERDICT_STACKED,
    DedupGroup,
    DedupGroupMember,
    DedupVerdict,
)
from pixlstash.db_models.face import Face
from pixlstash.db_models.operation import STATUS_APPLIED, Operation
from pixlstash.db_models.tag import Tag, is_tag_sentinel
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service
from pixlstash.utils.sql_chunking import SQLITE_ID_CHUNK as ID_CHUNK
from pixlstash.services.dedup_tier_service import (
    DedupScope,
    DedupTier,
    prune_stale_groups_in_session,
)
from pixlstash.services.dedup_tier_service import (
    # The queue list, the badge and the tier split all filter on this; since D1
    # (2026-08-01) so does bulk auto-stack, so the run plans exactly the
    # population the queue shows. Shared rather than duplicated: one definition
    # of "poses a decision" keeps the list, the counts and the bulk run from
    # ever disagreeing again.
    live_groups_filter,
)
from pixlstash.services.set_lock_service import (
    LOCKED_STATUS_CODE,
    build_locked_set_lookup,
    enforce_stack_membership_not_locked,
    partition_stackable_members,
)
from pixlstash.services.stack_membership import (
    expand_picture_ids_to_stacks,
    reconcile_stack_membership,
)
from pixlstash.stacking import normalize_stack_positions
from pixlstash.utils.service.scope_table import scope_id_subquery

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# The op_types this module records. Stable - part of the API contract the
# frontend keys its undo affordances off. A bulk action shares one batch_id
# across every row it writes, so the whole run reverses in a single step.
#
# Keep-separate had no op_type until 2026-07-30 (it changes no reversible
# picture facet, so the original CSO ruling around #644 recorded nothing rather
# than writing a row); the owner explicitly reversed that ruling and it now
# records one operation whose undo is carried entirely by its post-restore hook.
# Reopen ("Clear decision") records an operation exactly when it mutates
# pictures: clearing a still-standing *stacked* verdict dissolves the verdict's
# stack (restoring the recorded pre-verdict stack state) and that mutation must
# be undoable like every other stack mutation. A clear that touches no picture
# (keep-separate, or a stack the user already dissolved by hand) still records
# nothing - see :func:`reopen_verdict_in_session` for why that line is where
# the old "no second confusing way to re-decide" concern now lives.
OP_TYPE_STACK = "dedup.stack"
OP_TYPE_KEEP_SEPARATE = "dedup.keep_separate"
OP_TYPE_REOPEN = "dedup.reopen"

# Per-group outcome vocabulary for a bulk auto-stack. Closed set; the response
# reports every group under exactly one of these, so a partial run is legible
# rather than inferred from a count mismatch.
BULK_REASON_APPLIED = "applied"
BULK_REASON_BLOCKED = "blocked"
"""The group was refused by a guard that returns an HTTP status - in practice a
locked picture set (423). Nothing was written for it."""
BULK_REASON_FAILED = "failed"
"""The group could not be resolved at all (stale signature, too few members)."""


class DedupVerdictError(Exception):
    """A verdict could not be applied (unknown signature, bad cover, ...)."""


@dataclass(frozen=True)
class VerdictResult:
    """What a verdict did, for the response and the action receipt.

    Attributes:
        signature: The group signature the verdict was recorded against.
        verdict: ``"stacked"`` or ``"keep_separate"``.
        stack_id: The resulting stack, for a stack verdict.
        cover_picture_id: The cover the stack leads with.
        picture_ids: Members the verdict covers.
        excluded_picture_ids: Members deliberately left out of the stack.
        batch_id: The operation-log batch this verdict belongs to.
        metadata_union: What the union actually changed, so the receipt can say
            so instead of claiming a silent merge.
        skipped: Members a locked set kept out of the stack, as
            ``[{"picture_id", "reason", "sets": [{"id","name"}]}]``. Empty on the
            everyday path. They are also listed in ``excluded_picture_ids`` (the
            verdict records them as exclusions so a rescan does not re-ask); this
            field is what tells the client the exclusion was the server's doing
            rather than the user's.
        event_picture_ids: The pictures the WS ``pictures_changed`` announcement
            should name - for a stack verdict the stack-expanded set (folding a
            stack in touches siblings the group never named), for keep-separate
            the group's members. Deliberately NOT part of :meth:`as_dict`: it is
            broadcast plumbing, not response contract.
    """

    signature: str
    verdict: str
    stack_id: Optional[int]
    cover_picture_id: Optional[int]
    picture_ids: list[int]
    excluded_picture_ids: list[int]
    batch_id: Optional[str]
    metadata_union: dict[str, Any]
    skipped: list[dict[str, Any]] = field(default_factory=list)
    event_picture_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "verdict": self.verdict,
            "stack_id": self.stack_id,
            "cover_picture_id": self.cover_picture_id,
            "picture_ids": list(self.picture_ids),
            "excluded_picture_ids": list(self.excluded_picture_ids),
            "batch_id": self.batch_id,
            "metadata_union": dict(self.metadata_union),
            "skipped": [dict(entry) for entry in self.skipped],
        }


def new_batch_id() -> str:
    """Mint a batch id grouping one bulk action's operations into one undo.

    Delegates to :func:`operation_log_service.new_batch_id` rather than minting
    its own shape: batch ids are namespaced (``srv-`` for server-minted, ``cli-``
    for a client-supplied one, validated at the request boundary), and a third
    un-namespaced shape from this module would make a dedup batch
    indistinguishable from a client-grafted one in the log.
    """
    return operation_log_service.new_batch_id()


def _record_operation(
    session: Session,
    *,
    op_type: str,
    before: dict[str, dict],
    after: dict[str, dict],
    batch_id: Optional[str],
    summary: str,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
    empty_diff_target_ids: Optional[list[int]] = None,
    commit: bool = True,
) -> Optional[Operation]:
    """Append **one** operation row for this verdict.

    Called once per verdict, around the whole mutation, on the verdict's own
    session - so the row and the change it describes commit against the same
    serialised writer (§21). The verdict path deliberately does not reuse
    ``routes/stacks.py``, which records itself; going through it would produce a
    second row and break "one verdict, one undo".

    ``empty_diff_target_ids`` is the keep-separate path: that verdict changes no
    picture facet, so its diff is empty by construction and the row is recorded
    against the group's member ids instead (§21's empty-diff escape hatch).
    """
    return operation_log_service.record_operation_in_session(
        session,
        op_type=op_type,
        before=before,
        after=after,
        batch_id=batch_id,
        summary=summary,
        actor=actor,
        source=source,
        origin_client_id=origin_client_id,
        empty_diff_target_ids=empty_diff_target_ids,
        commit=commit,
    )


def _capture_state(session: Session, picture_ids: list[int]) -> dict[str, dict]:
    """Snapshot every reversible facet of *picture_ids* (§21's undo payload)."""
    return operation_log_service.capture_state_in_session(session, picture_ids)


def _undo_targets(session: Session, picture_ids: list[int]) -> list[int]:
    """The ids an undo of this verdict has to restore.

    Stacking is stack-atomic: folding an existing stack into the verdict's stack
    reparents co-members the group never named, and ``normalize_stack_positions``
    renumbers **every** member of an affected stack, soft-deleted ones included.
    Snapshotting only the group's members would leave those siblings stranded on
    undo, which is exactly the ``expand_stacks`` /
    ``expand_stacks_include_deleted`` pairing §21 requires of a grouping mutation.
    """
    return expand_picture_ids_to_stacks(session, picture_ids, include_deleted=True)


# --- Group lookup -----------------------------------------------------------


def _load_group(session: Session, signature: str) -> tuple[DedupGroup, list[int]]:
    """Return the group row and its live member ids, or raise."""
    group = session.exec(
        select(DedupGroup).where(DedupGroup.signature == signature)
    ).first()
    if group is None:
        raise DedupVerdictError(f"No duplicate group with signature {signature!r}")
    member_ids = [
        int(row)
        for row in session.exec(
            select(DedupGroupMember.picture_id)
            .join(Picture, Picture.id == DedupGroupMember.picture_id)
            .where(
                DedupGroupMember.group_id == group.id,
                Picture.deleted.is_(False),
            )
            .order_by(DedupGroupMember.position)
        ).all()
    ]
    return group, member_ids


def _upsert_verdict(
    session: Session,
    *,
    signature: str,
    verdict: str,
    picture_ids: list[int],
    excluded_picture_ids: list[int],
    cover_picture_id: Optional[int],
    stack_id: Optional[int],
    batch_id: Optional[str],
) -> DedupVerdict:
    """Write (or refresh) the verdict row and mark its group resolved."""
    row = session.exec(
        select(DedupVerdict).where(DedupVerdict.signature == signature)
    ).first()
    if row is None:
        row = DedupVerdict(signature=signature, verdict=verdict)
    row.verdict = verdict
    row.picture_ids = json.dumps(sorted(picture_ids))
    row.excluded_picture_ids = json.dumps(sorted(excluded_picture_ids))
    row.cover_picture_id = cover_picture_id
    row.stack_id = stack_id
    row.batch_id = batch_id
    row.decided_at = datetime.utcnow()
    row.reopened_at = None
    session.add(row)

    group = session.exec(
        select(DedupGroup).where(DedupGroup.signature == signature)
    ).first()
    if group is not None:
        group.resolved = True
        session.add(group)
    return row


# --- Metadata union ---------------------------------------------------------


def apply_metadata_union_in_session(
    session: Session, picture_ids: list[int], stack_id: int
) -> dict[str, Any]:
    """Union tags, membership, score and (where safe) characters across a stack.

    Additive only. See the module docstring for why characters go through
    ``pending_character_id`` rather than through fabricated ``Face`` rows.

    Args:
        session: Pre-opened session. Not committed here - the caller owns the
            transaction so a verdict lands as one unit.
        picture_ids: The stack's members.
        stack_id: The stack they were just placed in; the project / set union
            reconciles over the whole stack, not only over the group.

    Returns:
        A summary of what changed, for the action receipt.
    """
    # Imported locally: set_lock_service imports stack_membership, so a
    # module-level import here would be circular.
    from pixlstash.services.set_lock_service import enforce_pictures_not_locked

    if len(picture_ids) < 2:
        return {"tags_added": 0, "scores_lifted": 0, "characters_pending": 0}

    # The union writes tags and scores, which are curation state. A picture
    # frozen by a locked set must not have either changed behind the user's
    # back, so this is a hard 423 rather than a skip: a partially applied union
    # would be worse than a refused one.
    enforce_pictures_not_locked(session, picture_ids, "union duplicate metadata")

    membership_changed = reconcile_stack_membership(session, stack_id)

    # --- Tags: every member gains every real tag any member carries ---
    tag_rows = session.exec(
        select(Tag.picture_id, Tag.tag).where(Tag.picture_id.in_(picture_ids))
    ).all()
    tags_by_picture: dict[int, set[str]] = {pid: set() for pid in picture_ids}
    union: set[str] = set()
    for picture_id, tag in tag_rows:
        if is_tag_sentinel(tag):
            continue
        tags_by_picture.setdefault(int(picture_id), set()).add(str(tag))
        union.add(str(tag))
    tags_added = 0
    for picture_id in picture_ids:
        for tag in sorted(union - tags_by_picture.get(picture_id, set())):
            session.add(Tag(picture_id=picture_id, tag=tag))
            tags_added += 1

    # --- Score: lift every member to the highest rating in the group ---
    pictures = session.exec(select(Picture).where(Picture.id.in_(picture_ids))).all()
    best_score = max((int(pic.score or 0) for pic in pictures), default=0)
    scores_lifted = 0
    if best_score > 0:
        for pic in pictures:
            if int(pic.score or 0) < best_score:
                pic.score = best_score
                session.add(pic)
                scores_lifted += 1

    # --- Characters: only the unambiguous single-character case ---
    character_ids = {
        int(row)
        for row in session.exec(
            select(Face.character_id).where(
                Face.picture_id.in_(picture_ids), Face.character_id.is_not(None)
            )
        ).all()
        if row is not None
    }
    characters_pending = 0
    if len(character_ids) == 1:
        character_id = next(iter(character_ids))
        assigned = {
            int(row)
            for row in session.exec(
                select(Face.picture_id).where(
                    Face.picture_id.in_(picture_ids),
                    Face.character_id == character_id,
                )
            ).all()
        }
        for pic in pictures:
            if int(pic.id) in assigned or pic.pending_character_id == character_id:
                continue
            pic.pending_character_id = character_id
            session.add(pic)
            characters_pending += 1
    elif len(character_ids) > 1:
        logger.info(
            "[dedup-verdict] stack over pictures %s references %d characters %s; "
            "characters are left as-is rather than guessing which one the stack "
            "belongs to (no face data is fabricated)",
            picture_ids,
            len(character_ids),
            sorted(character_ids),
        )

    return {
        "tags_added": tags_added,
        "scores_lifted": scores_lifted,
        "characters_pending": characters_pending,
        "membership_changed": bool(membership_changed),
        "best_score": best_score,
    }


# --- Stacking ---------------------------------------------------------------


def _stack_expanded_ids(session: Session, picture_ids: list[int]) -> set[int]:
    """Every live picture that will end up in the stack *picture_ids* produce.

    The members themselves plus the **full** membership of every stack any of
    them already belongs to: precisely the set :func:`_stack_members`
    materialises when it folds those stacks in, because a stack moves as a unit.

    Two callers need it and they must agree: the cover check (B2, a folded
    stack's leader is a legal cover even though it is not a group member) and
    the receipt's count (B4: a verdict that folds a stack in moves more
    pictures than the group named).

    Soft-deleted co-members are excluded: they are not in the resulting stack in
    any sense the user can see, and neither the cover nor the count should
    mention them. The undo snapshot is the one place that *does* need them, and
    it takes its own expansion (see :func:`_undo_targets`).
    """
    return set(expand_picture_ids_to_stacks(session, picture_ids))


def _stack_members(
    session: Session, picture_ids: list[int], cover_picture_id: int
) -> int:
    """Put *picture_ids* into one stack led by *cover_picture_id*.

    Reuses an existing stack when the members already share one (growing it
    rather than orphaning it), and folds several stacks into the cover's when the
    group spans more than one. Always additive: no picture leaves a stack it was
    in, and no stack row is dropped that still has members.

    *cover_picture_id* **need not be one of** *picture_ids*: a duplicate group
    frequently names only one member of an existing stack, and the queue renders
    that stack as a single unit whose face is its leader, so the leader is the
    cover the user picks (B2). It is loaded alongside the members here; the
    caller is responsible for having validated it against
    :func:`_stack_expanded_ids`, so a picture unrelated to the fold never
    reaches this function.
    """
    wanted = sorted({int(pid) for pid in picture_ids} | {int(cover_picture_id)})
    pictures = {
        int(pic.id): pic
        for pic in session.exec(select(Picture).where(Picture.id.in_(wanted))).all()
    }
    missing = sorted(set(wanted) - set(pictures))
    if missing:
        raise DedupVerdictError(f"pictures {missing} no longer exist")

    existing_stack_ids = sorted(
        {int(pic.stack_id) for pic in pictures.values() if pic.stack_id is not None}
    )
    cover = pictures[cover_picture_id]
    if cover.stack_id is not None:
        stack_id = int(cover.stack_id)
    elif existing_stack_ids:
        stack_id = existing_stack_ids[0]
    else:
        stack = PictureStack(name=None)
        session.add(stack)
        session.flush()
        stack_id = int(stack.id)

    enforce_stack_membership_not_locked(
        session, list(picture_ids), stack_id, "stack duplicates together"
    )

    # Pull in every member of any stack this group touches: stacks move as a unit.
    folded_ids = [sid for sid in existing_stack_ids if sid != stack_id]
    if folded_ids:
        for pic in session.exec(
            select(Picture).where(Picture.stack_id.in_(folded_ids))
        ).all():
            pictures.setdefault(int(pic.id), pic)

    # The cover sorts ahead of everything so normalize_stack_positions lands it
    # at position 0 - the leader convention the whole app reads.
    for pic in pictures.values():
        pic.stack_id = stack_id
        pic.stack_position = -1 if int(pic.id) == cover_picture_id else 1
        session.add(pic)
    session.flush()

    for folded_id in folded_ids:
        remaining = session.exec(
            select(func.count(Picture.id)).where(Picture.stack_id == folded_id)
        ).one()
        if int(remaining) == 0:
            orphan = session.get(PictureStack, folded_id)
            if orphan is not None:
                session.delete(orphan)

    normalize_stack_positions(session, stack_id)
    stack = session.get(PictureStack, stack_id)
    if stack is not None:
        stack.updated_at = datetime.utcnow()
        session.add(stack)
    return stack_id


# --- Bulk dry-run aggregates -------------------------------------------------


def _dry_run_summary_in_session(
    session: Session, groups: list[DedupGroup]
) -> dict[str, Any]:
    """Aggregate what a bulk auto-stack would do, for the consent dialog.

    Computed from the **same** ``groups`` list the dry-run counts come from, in
    the same read, so the dialog's "N groups" and its "M covers gain metadata"
    row cannot disagree because a scan landed between two queries.

    The union is **not** run to work this out - nothing is written and no
    membership is reconciled. Each figure is derived from the planned verdict:
    the cover is the group's stored preselection, and a cover "gains" a facet
    when some other member of its group already carries something the cover does
    not (which is exactly what :func:`apply_metadata_union_in_session` would then
    copy onto it).

    Returns:
        ``groups_by_tier`` (always keyed by every tier, zero-filled),
        ``pictures``, ``covers_gaining_tags``, ``covers_gaining_score`` and
        ``covers_gaining_metadata`` (the union of the previous two - the row the
        design's dialog promises).

        ``pictures`` counts the **distinct stack-expanded** set the run would
        move (B4): a group that folds an existing stack in reparents that
        stack's whole membership, so counting the group's own members
        under-reported the dialog, and two groups can name members of the same
        stack, so summing per group would then over-report it. The per-cover
        figures deliberately stay on the group's own
        members, because the tag and score union runs over exactly those,
        :func:`apply_metadata_union_in_session` is passed ``included``, not the
        folded stack, so widening them would promise gains the run never makes.
    """
    summary: dict[str, Any] = {
        "groups_by_tier": {tier.value: 0 for tier in DedupTier},
        "groups": len(groups),
        "pictures": 0,
        "covers_gaining_tags": 0,
        "covers_gaining_score": 0,
        "covers_gaining_metadata": 0,
    }
    if not groups:
        return summary

    # Every id set below goes through a TEMP TABLE rather than a Python-set
    # ``.in_()``: this runs over the WHOLE candidate population, not one group, so
    # a real library plans tens of thousands of groups and hundreds of thousands
    # of members. One bound parameter per id blows SQLite's
    # ``SQLITE_MAX_VARIABLE_NUMBER`` and the preview dies with "too many SQL
    # variables" (#751, reported at 30,140 exact groups). The three scopes below
    # carry DISTINCT names on purpose: they are live at the same time and the
    # picture scope is read again *after* the stack scope is materialised, so a
    # shared name would clobber it and silently return wrong counts rather than
    # raising.
    group_scope = scope_id_subquery(
        session,
        [int(group.id) for group in groups],
        name="_pixlstash_dry_run_group_ids",
    )
    members_by_group: dict[int, list[int]] = defaultdict(list)
    for group_id, picture_id in session.exec(
        select(DedupGroupMember.group_id, DedupGroupMember.picture_id)
        .join(Picture, Picture.id == DedupGroupMember.picture_id)
        .where(
            DedupGroupMember.group_id.in_(group_scope),
            Picture.deleted.is_(False),
        )
    ).all():
        members_by_group[int(group_id)].append(int(picture_id))

    # Frozen members are dropped from every figure below: the real run skips
    # them, so counting them would make the consent dialog promise pictures that
    # will not move and covers that will not gain anything.
    all_ids = [pid for ids in members_by_group.values() for pid in ids]
    lock_lookup = build_locked_set_lookup(session, all_ids)
    members_by_group = {
        group_id: [pid for pid in ids if not lock_lookup.sets_for(pid)]
        for group_id, ids in members_by_group.items()
    }
    all_ids = [pid for ids in members_by_group.values() for pid in ids]
    # Materialised after the lock filter, so the scope is the population the run
    # would actually move. Reused by the tag query below.
    picture_scope = scope_id_subquery(
        session, all_ids, name="_pixlstash_dry_run_picture_ids"
    )
    scores: dict[int, Any] = {}
    stack_by_picture: dict[int, Optional[int]] = {}
    for picture_id, score, stack_id in session.exec(
        select(Picture.id, Picture.score, Picture.stack_id).where(
            Picture.id.in_(picture_scope)
        )
    ).all():
        scores[int(picture_id)] = score
        stack_by_picture[int(picture_id)] = (
            int(stack_id) if stack_id is not None else None
        )

    # B4: a group that folds an existing stack in moves that stack's WHOLE
    # membership, not only the members the group named, stacks move as a unit
    # (:func:`_stack_members`). Counting group members alone made the consent
    # dialog promise fewer pictures than the run would touch. Resolved in two
    # bulk queries rather than one expansion per group, since a run can plan
    # thousands of groups.
    stack_ids = {sid for sid in stack_by_picture.values() if sid is not None}
    members_by_stack: dict[int, list[int]] = defaultdict(list)
    if stack_ids:
        stack_scope = scope_id_subquery(
            session, stack_ids, name="_pixlstash_dry_run_stack_ids"
        )
        for picture_id, stack_id in session.exec(
            select(Picture.id, Picture.stack_id).where(
                Picture.stack_id.in_(stack_scope),
                Picture.deleted.is_(False),
            )
        ).all():
            members_by_stack[int(stack_id)].append(int(picture_id))

    tags_by_picture: dict[int, set[str]] = defaultdict(set)
    for picture_id, tag in session.exec(
        select(Tag.picture_id, Tag.tag).where(Tag.picture_id.in_(picture_scope))
    ).all():
        if not is_tag_sentinel(tag):
            tags_by_picture[int(picture_id)].add(str(tag))

    # Accumulated DISTINCT, not summed per group: two groups can each name a
    # picture of the same stack, and both would then fold that whole stack in.
    # Summing would promise its members twice.
    moved: set[int] = set()
    for group in groups:
        member_ids = members_by_group.get(int(group.id), [])
        summary["groups_by_tier"][str(group.tier)] = (
            summary["groups_by_tier"].get(str(group.tier), 0) + 1
        )
        moved.update(member_ids)
        for pid in member_ids:
            stack_id = stack_by_picture.get(pid)
            if stack_id is not None:
                moved.update(members_by_stack.get(stack_id, ()))
        cover_id = int(group.cover_picture_id or (member_ids[0] if member_ids else 0))
        if cover_id not in member_ids:
            # A frozen preselection is moved by the verdict rather than failing
            # it, so the preview has to move it too or the group silently drops
            # out of the "covers gaining metadata" row.
            if not member_ids:
                continue
            cover_id = member_ids[0]
        others = [pid for pid in member_ids if pid != cover_id]
        gains_tags = any(
            tags_by_picture[pid] - tags_by_picture[cover_id] for pid in others
        )
        gains_score = any(
            int(scores.get(pid) or 0) > int(scores.get(cover_id) or 0) for pid in others
        )
        summary["covers_gaining_tags"] += int(gains_tags)
        summary["covers_gaining_score"] += int(gains_score)
        summary["covers_gaining_metadata"] += int(gains_tags or gains_score)
    summary["pictures"] = len(moved)
    return summary


# --- Verdicts ---------------------------------------------------------------


def apply_stack_verdict_in_session(
    session: Session,
    signature: str,
    cover_picture_id: Optional[int] = None,
    excluded_picture_ids: Optional[Iterable[int]] = None,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
    commit: bool = True,
) -> VerdictResult:
    """Stack a group's members and remember the decision.

    Args:
        session: Pre-opened session; this function commits once, so the stack,
            the metadata union and the verdict row land together or not at all.
        signature: The group signature from the queue.
        cover_picture_id: The cover the user confirmed. Defaults to the server's
            preselection stored on the group. It may be a **folded stack's
            leader** rather than a group member (B2): the queue renders an
            existing stack as one unit whose face is its leader, and a group
            often names only one of that stack's pictures. Anything outside the
            resulting stack is still refused.
        excluded_picture_ids: Members the user left out (the design's X key).
            They keep their current stack and are recorded on the verdict so a
            rescan does not treat the exclusion as an unfinished decision.
        batch_id: Operation-log batch. Bulk auto-stack passes one id for every
            group so the whole run reverses with a single undo.
        actor: Who performed the change, from ``request_context`` in the handler.
        source: WS-envelope source, likewise read from the request (§21 origin
            discipline: never from a contextvar, which is dead on this thread).
        origin_client_id: WS-envelope per-tab origin, likewise.

    Returns:
        The :class:`VerdictResult` behind the action receipt. ``skipped`` names
        any member a locked set kept out; ``picture_ids`` is what actually got
        stacked, and ``cover_picture_id`` where the cover ended up.

    Raises:
        DedupVerdictError: Unknown signature, a cover that would not end up in
            the resulting stack, or fewer than two members left after
            exclusions.
        HTTPException: ``423`` when a locked set leaves fewer than two members
            that may legally be stacked together, so there is no partial success
            to report. The detail names both the sets and the picture ids.
    """
    group, member_ids = _load_group(session, signature)
    # Always under a batch id, even for a single verdict. The batch id is the key
    # that ties the recorded Operation back to the DedupVerdict row, which is how
    # an undo knows to reopen the verdict as well as restoring the pictures (see
    # :func:`restore_verdicts_in_session`). A verdict recorded without one would
    # be undoable on the picture side and permanently decided on the queue side.
    batch_id = batch_id or new_batch_id()
    excluded = sorted({int(pid) for pid in (excluded_picture_ids or [])})
    unknown = sorted(set(excluded) - set(member_ids))
    if unknown:
        raise DedupVerdictError(
            f"excluded pictures {unknown} are not members of group {signature!r}"
        )
    included = [pid for pid in member_ids if pid not in set(excluded)]
    if len(included) < 2:
        raise DedupVerdictError(
            f"group {signature!r} has {len(included)} member(s) left after "
            "exclusions; a stack needs at least two"
        )

    cover_id = (
        int(cover_picture_id)
        if cover_picture_id is not None
        else int(group.cover_picture_id or included[0])
    )
    # The cover is validated against what will actually END UP in the stack, not
    # against the group's own members (B2). A group frequently names only ONE
    # picture of an existing stack, so that stack's leader is not a member and
    # the queue must be able to say "this stack leads" without promoting the
    # matched member instead, which would silently re-cover a stack the user
    # already curated. The legal set is therefore the group's members plus the
    # full membership of every stack the verdict folds in, exactly what
    # `_stack_members` materialises. It is NOT relaxed any further than that: an
    # arbitrary picture id, or the leader of a stack this group does not touch,
    # is still refused.
    stack_expanded = _stack_expanded_ids(session, included)
    if cover_id not in stack_expanded:
        raise DedupVerdictError(
            f"cover {cover_id} is not an included member of group {signature!r}, "
            "nor a member of any stack the verdict would fold in"
        )

    # A frozen member can be in neither the stack (its set's membership cannot
    # change) nor the metadata union (its labels cannot change), so a group
    # straddling a locked set has no legal whole-group stack. Partial success
    # rather than a whole-group refusal: the members that CAN be stacked are, and
    # the rest are recorded as exclusions. The queue already marks them, so this
    # path is the stale-client case, not the everyday one.
    partition = partition_stackable_members(session, included)
    skipped = list(partition.blocked)
    blocked_ids = set(partition.blocked_ids)
    if skipped:
        included = list(partition.stackable)
        excluded = sorted(set(excluded) | blocked_ids)
        # The stack the verdict will build has changed shape, so the cover's
        # legal set and the receipt's count both have to be re-derived from
        # what is left.
        stack_expanded = _stack_expanded_ids(session, included)
        logger.warning(
            "[dedup-verdict] skipped %d locked member(s) %s of group %s: stacking "
            "them would add member(s) to locked set(s) %s; the remaining %d "
            "member(s) %s still stacked",
            len(skipped),
            partition.blocked_ids,
            signature,
            sorted({entry["name"] for m in skipped for entry in m.sets}),
            len(included),
            included,
        )
    if len(included) < 2:
        # Nothing legal is left, so there is no partial success to report. This is
        # the one dedup stack path that still refuses outright, and it names the
        # pictures so the client can mark the exact thumbnails.
        set_list = {
            entry["id"]: entry["name"] for member in skipped for entry in member.sets
        }
        raise HTTPException(
            status_code=LOCKED_STATUS_CODE,
            detail={
                "code": "set_locked",
                "action": "stack duplicates together",
                "sets": [
                    {"id": sid, "name": name} for sid, name in sorted(set_list.items())
                ],
                "picture_ids": sorted(partition.blocked_ids),
            },
        )
    if cover_id in blocked_ids or cover_id not in stack_expanded:
        # The cover the caller confirmed is one of the skipped members, or the
        # stack it led is no longer folded in now that a frozen member has left.
        # Moving it is the same repair the client makes when a user excludes the
        # cover, and the response reports where it landed. The group's own
        # preselection gets first refusal, so the fallback is the server's
        # ranking rather than whichever member happens to sort first. Both
        # candidates are group members, so both are in the resulting stack by
        # construction.
        preselected = (
            int(group.cover_picture_id) if group.cover_picture_id is not None else None
        )
        cover_id = preselected if preselected in included else included[0]

    # Snapshot the stack-expanded set: folding an existing stack in reparents
    # co-members this group never named, and they must be restorable too.
    undo_targets = _undo_targets(session, included)
    before = _capture_state(session, undo_targets)
    stack_id = _stack_members(session, included, cover_id)
    union = apply_metadata_union_in_session(session, included, stack_id)
    _upsert_verdict(
        session,
        signature=signature,
        verdict=VERDICT_STACKED,
        picture_ids=included,
        excluded_picture_ids=excluded,
        cover_picture_id=cover_id,
        stack_id=stack_id,
        batch_id=batch_id,
    )
    after = _capture_state(session, undo_targets)
    _record_operation(
        session,
        op_type=OP_TYPE_STACK,
        before=before,
        after=after,
        batch_id=batch_id,
        # Count what MOVED, not what the group named (B4). Folding an existing
        # stack in reparents co-members the group never mentioned, so
        # `len(included)` under-reported the receipt every time a stack was
        # involved: "Stacked 2 duplicates" for a verdict that moved six.
        summary=f"Stacked {len(stack_expanded)} duplicates",
        actor=actor,
        source=source,
        origin_client_id=origin_client_id,
        commit=commit,
    )
    if commit:
        session.commit()
    logger.info(
        "[dedup-verdict] stacked %d picture(s) (%d named by the group) into "
        "stack %s (cover=%s, excluded=%s, signature=%s, batch=%s)",
        len(stack_expanded),
        len(included),
        stack_id,
        cover_id,
        excluded,
        signature,
        batch_id,
    )
    return VerdictResult(
        signature=signature,
        verdict=VERDICT_STACKED,
        stack_id=stack_id,
        cover_picture_id=cover_id,
        picture_ids=included,
        excluded_picture_ids=excluded,
        batch_id=batch_id,
        metadata_union=union,
        skipped=[member.as_dict() for member in skipped],
        # The stack-expanded set: the fold and the renumber touch siblings the
        # group never named, and the WS announcement must cover them too.
        event_picture_ids=sorted(undo_targets),
    )


def apply_keep_separate_in_session(
    session: Session,
    signature: str,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
    commit: bool = True,
) -> VerdictResult:
    """Remember that this group is *not* duplicates. Changes no picture row.

    Records **one** operation (:data:`OP_TYPE_KEEP_SEPARATE`), exactly like the
    stack verdict does - the owner's 2026-07-30 reversal of the #644-era CSO
    ruling that kept this verdict out of the log. The verdict changes no picture
    facet, so the operation's before/after payloads are empty and the whole
    restore is the post-restore hook's: undo reopens the verdict and returns the
    group to the queue; redo re-resolves it. :func:`reopen_verdict_in_session`
    remains the explicit, always-available way back.

    Args:
        session: Pre-opened session; this function commits once, so the verdict
            row and its operation land together or not at all.
        signature: The group signature from the queue.
        batch_id: Operation-log batch. A client gesture spanning several
            verdicts passes one ``cli-`` id so the whole gesture reverses with a
            single undo; minted server-side (``srv-``) when absent, exactly as
            the stack verdict does - the batch id is what ties the Operation row
            back to this verdict row on restore.
        actor: Who performed the change, from ``request_context`` in the handler.
        source: WS-envelope source, likewise read from the request (§21 origin
            discipline: never from a contextvar, which is dead on this thread).
        origin_client_id: WS-envelope per-tab origin, likewise.
    """
    _group, member_ids = _load_group(session, signature)
    # Always under a batch id, for the same reason the stack verdict is: the
    # batch id is the key restore_verdicts_in_session uses to find this verdict
    # row again on undo/redo. Stored on the row since 2026-07-30 - safe now that
    # keep-separate records its own operation, so a shared gesture id reverses it
    # only through THAT operation, explicitly and visibly, never as a silent
    # side effect of undoing a sibling stack (the original CSO R5 concern).
    batch_id = batch_id or new_batch_id()
    _upsert_verdict(
        session,
        signature=signature,
        verdict=VERDICT_KEEP_SEPARATE,
        picture_ids=member_ids,
        excluded_picture_ids=[],
        cover_picture_id=None,
        stack_id=None,
        batch_id=batch_id,
    )
    # The diff is empty by construction (no picture facet changes), so the row
    # is recorded through §21's empty-diff escape hatch against the member ids.
    _record_operation(
        session,
        op_type=OP_TYPE_KEEP_SEPARATE,
        before={},
        after={},
        batch_id=batch_id,
        summary=f"Kept {len(member_ids)} pictures separate",
        actor=actor,
        source=source,
        origin_client_id=origin_client_id,
        empty_diff_target_ids=member_ids,
        commit=commit,
    )
    if commit:
        session.commit()
    logger.info(
        "[dedup-verdict] keep-separate recorded for signature=%s (%d members, "
        "batch=%s)",
        signature,
        len(member_ids),
        batch_id,
    )
    return VerdictResult(
        signature=signature,
        verdict=VERDICT_KEEP_SEPARATE,
        stack_id=None,
        cover_picture_id=None,
        picture_ids=member_ids,
        excluded_picture_ids=[],
        batch_id=batch_id,
        metadata_union={},
        # No picture row changed, but the members' dedup state did (they left
        # the queue), so other tabs get the standard refresh signal for them.
        event_picture_ids=sorted(member_ids),
    )


def _verdict_stack_still_standing(session: Session, row: DedupVerdict) -> bool:
    """True when the verdict's members still sit together in the verdict's stack.

    This is the condition under which a clear must unstack: the open queue's
    live-groups filter requires a group's live members to span two or more
    stack units (``COALESCE(stack_id, -id)``), so a group whose members all
    share the verdict's stack is invisible to the queue and clearing only the
    memory would strand it - gone from Decided, never back in review (the
    owner-reported 2026-07-30 bug).

    The check is deliberately narrow: it must be the **verdict's own** stack.
    If the user already dissolved it by hand the members span their own units
    and the group is queue-visible the moment the memory clears; and if the
    user re-stacked the members into some *other* stack, that is their own
    fresh decision, which the queue's stack-units rule deliberately does not
    re-offer - mutating either arrangement would fight the user.
    """
    if row.stack_id is None:
        logger.warning(
            "[dedup-verdict] stacked verdict for signature=%s carries no "
            "stack_id; treating its stack as already dissolved and clearing "
            "the memory only",
            row.signature,
        )
        return False
    member_ids = [int(pid) for pid in json.loads(row.picture_ids or "[]")]
    if not member_ids:
        return False
    live = session.exec(
        select(Picture).where(Picture.id.in_(member_ids), Picture.deleted.is_(False))
    ).all()
    if len(live) < 2:
        return False
    units = {
        int(pic.stack_id) if pic.stack_id is not None else -int(pic.id) for pic in live
    }
    return units == {int(row.stack_id)}


def _correlate_stack_operation(session: Session, row: DedupVerdict) -> Operation:
    """Find the one applied ``dedup.stack`` operation recorded for *row*.

    Correlation is by the verdict's ``batch_id`` - every verdict is recorded
    under one - plus membership: a bulk auto-stack coalesces MANY groups into
    one batch, but each group records its **own** operation row, so within the
    batch the verdict's operation is the one whose (stack-expanded) target set
    covers the verdict's members. When a fold dragged this group's members into
    a batch sibling's snapshot too, the verdict's own operation is the one with
    the smallest target set (the sibling's is a strict superset taken *after*
    this group was stacked, so restoring from it would not be pre-verdict
    state).

    Raises:
        DedupVerdictError: The verdict has no batch id (pre-batching data) or
            no unambiguous applied operation matches. No fallback: the caller
            must not guess at pre-verdict state.
    """
    if not row.batch_id:
        raise DedupVerdictError(
            f"cannot locate the stack operation for verdict {row.signature!r}: "
            "it was recorded without a batch id (pre-batching data), so its "
            "pre-verdict stack state is unknown. Unstack the pictures from the "
            "Stacks view, then clear the decision again."
        )
    member_ids = {int(pid) for pid in json.loads(row.picture_ids or "[]")}
    candidates = session.exec(
        select(Operation).where(
            Operation.batch_id == row.batch_id,
            Operation.op_type == OP_TYPE_STACK,
            Operation.status == STATUS_APPLIED,
        )
    ).all()
    matches = [
        op
        for op in candidates
        if member_ids
        and member_ids <= {int(pid) for pid in json.loads(op.target_ids or "[]")}
    ]
    if not matches:
        raise DedupVerdictError(
            f"cannot locate the stack operation for verdict {row.signature!r}: "
            f"no applied {OP_TYPE_STACK!r} operation in batch {row.batch_id!r} "
            f"covers members {sorted(member_ids)}. Unstack the pictures from "
            "the Stacks view, then clear the decision again."
        )
    if len(matches) > 1:
        matches.sort(key=lambda op: (int(op.target_count or 0), int(op.id or 0)))
        if int(matches[0].target_count or 0) == int(matches[1].target_count or 0):
            raise DedupVerdictError(
                f"cannot locate the stack operation for verdict "
                f"{row.signature!r}: operations "
                f"{sorted(int(op.id) for op in matches if op.id)} in batch "
                f"{row.batch_id!r} match it equally well. Unstack the pictures "
                "from the Stacks view, then clear the decision again."
            )
        logger.info(
            "[dedup-verdict] batch %s holds %d operations covering verdict %s "
            "(a fold pulled its members into a sibling's snapshot); using the "
            "smallest, operation %s",
            row.batch_id,
            len(matches),
            row.signature,
            matches[0].id,
        )
    return matches[0]


def _recorded_stack_state(operation: Operation, signature: str) -> dict[str, dict]:
    """The ``stack`` facet of *operation*'s before-state, and nothing else.

    A clear reverts the verdict's *stacking* only. The metadata union (tags,
    scores, membership) stays: clearing means "review this again", not "undo
    everything the verdict did" - the full inverse remains the operation log's
    undo of the verdict itself.
    """
    try:
        recorded = json.loads(operation.before_state or "{}")
    except (TypeError, ValueError) as exc:
        raise DedupVerdictError(
            f"cannot revert the stack for verdict {signature!r}: the recorded "
            f"before-state of operation {operation.id} is unreadable ({exc})"
        ) from exc
    state: dict[str, dict] = {}
    for picture_id, facets in (recorded or {}).items():
        if not isinstance(facets, dict):
            continue
        stack = facets.get(operation_log_service.FACET_STACK)
        if stack is not None:
            state[str(picture_id)] = {operation_log_service.FACET_STACK: stack}
    if not state:
        raise DedupVerdictError(
            f"cannot revert the stack for verdict {signature!r}: operation "
            f"{operation.id} recorded no stack change to restore"
        )
    return state


def reopen_verdict_in_session(
    session: Session,
    signature: str,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Clear a verdict so the group returns to the open Duplicates queue.

    Stamps ``reopened_at`` rather than deleting the row: the decision history
    is worth keeping, and a reopened verdict is simply no longer live.

    **Clearing a still-standing stacked verdict also dissolves its stack.**
    The open queue only offers groups whose live members span two or more
    stack units, so a cleared-but-still-stacked group would vanish from the
    Decided page yet never return to review (the owner-reported 2026-07-30
    bug). The stack is dissolved by restoring the **recorded pre-verdict stack
    state** from the verdict's own operation-log row - so a pre-existing stack
    the verdict folded in comes back instead of being flattened - scoped to
    that one operation's targets, so clearing one group of a bulk batch never
    touches its batch siblings. Stack rows the restore empties are deleted
    (`operation_log_service.delete_emptied_stacks`, the #643 hygiene). The
    metadata union is deliberately **not** reverted: a clear means "review
    this again", not "undo the verdict" - that full inverse remains the
    operation log's.

    **The unstack is itself one undoable operation** (:data:`OP_TYPE_REOPEN`),
    recorded under its own batch id (returned as ``batch_id``); undoing it
    restacks the pictures AND re-marks the verdict decided via the
    :func:`restore_reopens_in_session` post-restore hook, so the pictures and
    the queue can never disagree. The old rule was "reopen records nothing,
    or undo-of-reopen becomes a second, confusing way to re-decide a group";
    that rule survives exactly where its rationale still holds - a clear that
    touches **no picture** (keep-separate, or a stack the user already
    dissolved by hand) records nothing and returns ``batch_id: None``. Once a
    clear moves pictures, *not* re-deciding on undo would leave the pictures
    restacked while the group sat open - the same half-restore class the
    verdict hooks exist to prevent - so the operation is recorded and its
    undo re-decides.

    A stacked verdict whose stack still stands but whose operation cannot be
    located (or not unambiguously) is **refused** with
    :class:`DedupVerdictError` rather than degraded: no fallback may guess at
    pre-verdict state. The way out is unstacking from the Stacks view, after
    which the clear needs no picture mutation and succeeds.

    Args:
        session: Pre-opened session; committed here, so the unstack, the
            verdict stamp and the operation row land together or not at all.
        signature: The decided group's signature.
        batch_id: Optional client (``cli-``) batch id grouping several clears
            into one undo step; minted server-side (``srv-``) when absent and
            pictures change. Must not equal the verdict's own batch id - that
            graft would make one undo apply the stack and its inverse in the
            same restore.
        actor: Who performed the change, from ``request_context`` in the
            handler.
        source: WS-envelope source, likewise read from the request (§21 origin
            discipline: never from a contextvar, dead on this thread).
        origin_client_id: WS-envelope per-tab origin, likewise.

    Returns:
        The response dict: ``signature``, ``previous_verdict``,
        ``reopened_at``, ``group_returned_to_queue``, ``batch_id`` (the undo
        handle, or ``None`` when no picture changed),
        ``unstacked_picture_ids``, plus ``event_picture_ids`` for the vault
        wrapper's WS announcement (popped before the response).
    """
    row = session.exec(
        select(DedupVerdict).where(DedupVerdict.signature == signature)
    ).first()
    if row is None:
        raise DedupVerdictError(f"No verdict recorded for signature {signature!r}")
    if row.reopened_at is not None:
        raise DedupVerdictError(f"Verdict for {signature!r} is already reopened")
    if batch_id and batch_id == row.batch_id:
        raise DedupVerdictError(
            f"batch_id {batch_id!r} is the verdict's own batch: grafting the "
            "clear into it would make one undo apply the stack and its inverse "
            "in the same restore. Supply a fresh batch id or omit it."
        )

    member_ids = [int(pid) for pid in json.loads(row.picture_ids or "[]")]
    event_ids: set[int] = set(member_ids)
    unstacked: list[int] = []
    clear_batch: Optional[str] = None
    target_ids: list[int] = []

    if row.verdict == VERDICT_STACKED and _verdict_stack_still_standing(session, row):
        operation = _correlate_stack_operation(session, row)
        stack_state = _recorded_stack_state(operation, signature)
        target_ids = sorted(int(pid) for pid in stack_state)
        clear_batch = batch_id or new_batch_id()
        before = _capture_state(session, target_ids)
        # apply_state_in_session is the same guarded sink undo uses: the
        # locked-set freeze applies (423 rather than a half-cleared stack) and
        # a vanished picture is skipped with a warning, exactly as a restore
        # would.
        vacated: set[int] = set()
        operation_log_service.apply_state_in_session(
            session,
            stack_state,
            "clear a duplicate decision",
            origin_client_id=origin_client_id,
            vacated_stack_ids=vacated,
        )
        operation_log_service.delete_emptied_stacks(session, vacated)
        unstacked = target_ids
        event_ids.update(target_ids)

    row.reopened_at = datetime.utcnow()
    if clear_batch:
        row.reopen_batch_id = clear_batch
    session.add(row)
    group = session.exec(
        select(DedupGroup).where(DedupGroup.signature == signature)
    ).first()
    if group is not None:
        group.resolved = False
        session.add(group)

    if clear_batch:
        after = _capture_state(session, target_ids)
        recorded = _record_operation(
            session,
            op_type=OP_TYPE_REOPEN,
            before=before,
            after=after,
            batch_id=clear_batch,
            summary=f"Returned {len(member_ids)} duplicates to the queue",
            actor=actor,
            source=source,
            origin_client_id=origin_client_id,
        )
        if recorded is None:
            # Cannot happen while the standing check gates the unstack (the
            # restore must have moved at least one stack pointer), but a
            # batch_id pointing at no operation would be a broken undo handle,
            # so fail visibly rather than return it.
            logger.error(
                "[dedup-verdict] clear of signature=%s restored a stack state "
                "yet produced an empty diff; no operation was recorded and no "
                "batch id is returned (batch=%s dropped)",
                signature,
                clear_batch,
            )
            row.reopen_batch_id = None
            session.add(row)
            clear_batch = None
            unstacked = []
    session.commit()
    logger.info(
        "[dedup-verdict] reopened verdict for signature=%s (previous=%s, "
        "unstacked=%s, batch=%s)",
        signature,
        row.verdict,
        unstacked,
        clear_batch,
    )
    return {
        "signature": signature,
        "previous_verdict": row.verdict,
        "reopened_at": row.reopened_at,
        "group_returned_to_queue": group is not None,
        "batch_id": clear_batch,
        "unstacked_picture_ids": unstacked,
        "event_picture_ids": sorted(event_ids),
    }


def restore_verdicts_in_session(
    session: Session,
    operations: list[Operation],
    direction: str,
    verdict_kind: str = VERDICT_STACKED,
) -> None:
    """Reopen (on undo) or re-decide (on redo) the verdicts *operations* recorded.

    Registered with the operation log as the post-restore hook for both
    :data:`OP_TYPE_STACK` (with ``verdict_kind=VERDICT_STACKED``) and
    :data:`OP_TYPE_KEEP_SEPARATE` (with ``verdict_kind=VERDICT_KEEP_SEPARATE``)
    - see
    :func:`pixlstash.services.operation_log_service.register_post_restore_hook`.

    Why this is needed at all: the operation log restores the reversible *picture*
    facets, and a verdict changes two more things that are not picture facets -
    the ``DedupVerdict`` row (decided) and the ``DedupGroup`` row (resolved).
    Without this hook an undo left the group decided, so it never returned to
    the queue, survived a rescan (the signature still carried a live verdict)
    and was recoverable only through ``POST /dedup/verdicts/reopen``. For
    keep-separate this hook is the *entire* restore: the operation's recorded
    diff is empty because the verdict changed no picture facet.

    Correlation is by ``batch_id``: a verdict is always recorded under one
    (minted server-side when the caller supplies none), and the verdict row
    stores the same id. One query covers a 2 700-group batch undo, which is why
    the hook takes the whole list rather than one operation at a time.

    Args:
        session: The restore's own session. Not committed here - the operation
            log commits the restore and this together, so the pictures and the
            queue can never disagree.
        operations: Every operation of this hook's ``op_type`` in this restore.
        direction: ``operation_log_service.RESTORE_UNDO`` or ``RESTORE_REDO``.
        verdict_kind: Which ``DedupVerdict.verdict`` this hook owns. The filter
            is what keeps each hook on its own rows when a client gesture id
            spans both verdict kinds: the stack hook must not touch a
            keep-separate row and vice versa - each is reversed only through its
            OWN operation, so nothing is ever undone silently.
    """
    batch_ids = sorted({op.batch_id for op in operations if op.batch_id})
    unbatched = sorted(int(op.id) for op in operations if not op.batch_id and op.id)
    if unbatched:
        # Rows written before verdicts were always batched. Nothing correlates
        # them to a verdict, so say so rather than silently half-restoring: the
        # pictures are back but the group stays decided until the user reopens it.
        logger.warning(
            "[dedup-verdict] %s: operation(s) %s carry no batch_id, so their "
            "duplicate verdict cannot be located; the pictures were restored but "
            "the group stays decided. Use POST /dedup/verdicts/reopen to return "
            "it to the queue.",
            direction,
            unbatched,
        )
    if not batch_ids:
        return

    is_redo = direction == operation_log_service.RESTORE_REDO
    now = datetime.utcnow()
    reopened_at = None if is_redo else now
    signatures: list[str] = []
    for start in range(0, len(batch_ids), ID_CHUNK):
        chunk = batch_ids[start : start + ID_CHUNK]
        for row in session.exec(
            select(DedupVerdict).where(
                DedupVerdict.batch_id.in_(chunk),
                DedupVerdict.verdict == verdict_kind,
            )
        ).all():
            # The row is "live" precisely when reopened_at is NULL; one field
            # carries the lifecycle. decided_at is RE-stamped on redo
            # (2026-07-30): it means "when this decision last became live", not
            # "when it was first made" - pressing redo is the user re-deciding
            # now. The Decided page uses decided_at for keep-separate verdicts
            # and as the fallback when a stacked verdict has no live stack
            # timestamp. The original decision instant survives in the
            # operation row's created_at, so no audit history is lost. (An undo
            # leaves decided_at alone: the row is not live, and the stamp still
            # names the decision the redo would restore.)
            row.reopened_at = reopened_at
            if is_redo:
                row.decided_at = now
            session.add(row)
            signatures.append(str(row.signature))
    if not signatures:
        return
    for start in range(0, len(signatures), ID_CHUNK):
        chunk = signatures[start : start + ID_CHUNK]
        for group in session.exec(
            select(DedupGroup).where(DedupGroup.signature.in_(chunk))
        ).all():
            group.resolved = is_redo
            session.add(group)
    logger.info(
        "[dedup-verdict] %s returned %d verdict(s) to %s across batch(es) %s",
        direction,
        len(signatures),
        "decided" if is_redo else "the queue",
        batch_ids,
    )


def restore_reopens_in_session(
    session: Session,
    operations: list[Operation],
    direction: str,
) -> None:
    """Re-decide (on undo) or re-clear (on redo) what a clear operation reopened.

    Registered as the post-restore hook for :data:`OP_TYPE_REOPEN`. The
    direction mapping is the **inverse** of :func:`restore_verdicts_in_session`:
    a clear *reopened* its verdict, so undoing the clear marks the verdict
    decided again (the operation's before-state has just restacked the
    pictures, and a decided verdict is the only state consistent with that),
    while redoing the clear reopens it once more (the after-state has just
    unstacked them again; the emptied-stack hygiene is the restore's own,
    via ``delete_emptied_stacks`` in ``_restore``).

    Correlation is by ``DedupVerdict.reopen_batch_id`` - stamped by
    :func:`reopen_verdict_in_session` whenever a clear records an operation -
    NOT by ``batch_id``, which keeps pointing at the verdict's own operation
    so undoing the original stack still finds its verdict.

    On undo, ``decided_at`` is re-stamped to now, matching the redo semantics
    of the verdict hook (§22.10): the stamp means "when this decision last
    became live", and the Decided page sorts by it descending, so the
    just-restored decision surfaces where the user looks for it.
    """
    batch_ids = sorted({op.batch_id for op in operations if op.batch_id})
    unbatched = sorted(int(op.id) for op in operations if not op.batch_id and op.id)
    if unbatched:
        # A clear operation is always recorded under a batch id; this is
        # defensive symmetry with restore_verdicts_in_session, not a live path.
        logger.warning(
            "[dedup-verdict] %s: clear operation(s) %s carry no batch_id, so "
            "their verdict cannot be located; the pictures were restored but "
            "the verdict's decided/reopened state was not touched.",
            direction,
            unbatched,
        )
    if not batch_ids:
        return

    is_undo = direction == operation_log_service.RESTORE_UNDO
    now = datetime.utcnow()
    signatures: list[str] = []
    for start in range(0, len(batch_ids), ID_CHUNK):
        chunk = batch_ids[start : start + ID_CHUNK]
        for row in session.exec(
            select(DedupVerdict).where(DedupVerdict.reopen_batch_id.in_(chunk))
        ).all():
            if is_undo:
                row.reopened_at = None
                row.decided_at = now
            else:
                row.reopened_at = now
            session.add(row)
            signatures.append(str(row.signature))
    if not signatures:
        logger.warning(
            "[dedup-verdict] %s: no verdict carries reopen_batch_id in %s; "
            "the pictures were restored but no verdict state was touched",
            direction,
            batch_ids,
        )
        return
    for start in range(0, len(signatures), ID_CHUNK):
        chunk = signatures[start : start + ID_CHUNK]
        for group in session.exec(
            select(DedupGroup).where(DedupGroup.signature.in_(chunk))
        ).all():
            group.resolved = is_undo
            session.add(group)
    logger.info(
        "[dedup-verdict] %s of clear returned %d verdict(s) to %s across batch(es) %s",
        direction,
        len(signatures),
        "decided" if is_undo else "the queue",
        batch_ids,
    )


def bulk_auto_stack_in_session(
    session: Session,
    scope: Optional[DedupScope] = None,
    batch_id: Optional[str] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Stack every unresolved **exact** group under one batch id.

    Tier 1 is the tier with no human judgment left in it, so the design gives it
    a single consent dialog instead of per-group adjudication: the dialog shows
    the dry-run counts, and accepting stacks them all under one operation-log
    batch id so N stacks reverse with one Ctrl+Z.

    Only exact groups are eligible. A near or embedding group always goes through
    the queue, no matter how confident it looks.

    **The candidate query uses the queue's own filter**
    (:func:`~pixlstash.services.dedup_tier_service.live_groups_filter`), so the
    run acts on exactly the groups the list shows and the badge counts. See the
    comment on the query for the already-collapsed groups the old, weaker filter
    let through.

    Args:
        session: Pre-opened session.
        scope: Restrict to a scope; defaults to the whole vault.
        batch_id: The shared batch id. Minted when omitted and returned.
        dry_run: Count what would happen and write nothing. This is what the
            consent dialog reads.
        limit: Cap the number of groups acted on, for a paged run.
        actor: Who performed the change, from ``request_context`` in the handler.
        source: WS-envelope source, likewise read from the request.
        origin_client_id: WS-envelope per-tab origin, likewise.

    Returns:
        The batch id, the counts, and a per-group outcome for **every** group the
        run considered: ``results`` for the applied ones and ``failures`` for the
        rest, each carrying an ``outcome`` of :data:`BULK_REASON_APPLIED`,
        :data:`BULK_REASON_BLOCKED` or :data:`BULK_REASON_FAILED`. The batch id is
        always present, so a partially applied run always hands back its undo
        handle.
    """
    scope = scope or DedupScope()
    query = select(DedupGroup).where(
        DedupGroup.resolved.is_(False),
        DedupGroup.tier == TIER_EXACT,
        # THE SAME filter the queue list, the badge and the tier counts apply.
        # This used to run on a weaker, now-deleted filter that only required
        # two unfrozen live members and said nothing about stack
        # units, so a group whose live members already sit in one and the same
        # stack was still planned: on a real library the run planned 62 groups
        # where the badge and the button said 3. The 59 extra posed no decision
        # (`_stack_members` reuses their existing stack, so nothing is created),
        # and 21 of them would have had their curated cover replaced, because
        # this path passes no cover and the group's preselection is forced to
        # stack_position 0. One filter, so the count, the button and the run
        # cannot disagree.
        live_groups_filter(),
    )
    predicate = scope.picture_predicate()
    if predicate is not None:
        query = query.where(
            DedupGroup.id.in_(
                select(DedupGroupMember.group_id)
                .join(Picture, Picture.id == DedupGroupMember.picture_id)
                .where(Picture.deleted.is_(False), predicate)
            )
        )
    query = query.order_by(DedupGroup.confidence.desc(), DedupGroup.id.asc())
    if limit is not None:
        query = query.limit(max(1, int(limit)))
    groups = session.exec(query).all()

    if dry_run:
        summary = _dry_run_summary_in_session(session, groups)
        # `member_count` is the group's stored total and counts frozen members the
        # run will skip. The summary already excludes them, so the top-level
        # figure is taken from there rather than recomputed and disagreeing.
        picture_total = int(summary["pictures"])
        return {
            "dry_run_summary": summary,
            "batch_id": batch_id,
            "dry_run": True,
            "groups": len(groups),
            "pictures": picture_total,
            "scope": scope.as_dict(),
            "results": [],
        }

    batch_id = batch_id or new_batch_id()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    event_picture_ids: set[int] = set()
    for group in groups:
        try:
            result = apply_stack_verdict_in_session(
                session,
                group.signature,
                batch_id=batch_id,
                actor=actor,
                source=source,
                origin_client_id=origin_client_id,
            )
        except (DedupVerdictError, HTTPException) as exc:
            # One unstackable group must never abort the run: every earlier group
            # has already committed, so aborting here would leave a partially
            # applied bulk mutation whose batch id the caller never receives -
            # i.e. no undo handle for work that did happen.
            #
            # HTTPException is caught alongside DedupVerdictError because the
            # locked-set guards raise 423, and a locked member is the *most
            # likely* reason a group cannot be stacked. Catching only the former
            # made this function's own API description ("a single unstackable
            # group never aborts the run") false for the common case.
            session.rollback()
            if isinstance(exc, HTTPException):
                reason, detail, status_code = (
                    BULK_REASON_BLOCKED,
                    exc.detail,
                    exc.status_code,
                )
            else:
                reason, detail, status_code = BULK_REASON_FAILED, str(exc), None
            logger.warning(
                "[dedup-verdict] auto-stack %s group %s (batch=%s): %s",
                reason,
                group.signature,
                batch_id,
                detail,
            )
            failures.append(
                {
                    "signature": group.signature,
                    "outcome": reason,
                    "status_code": status_code,
                    "error": detail,
                }
            )
            continue
        results.append({**result.as_dict(), "outcome": BULK_REASON_APPLIED})
        # A group can name one member of an already-existing stack. Folding that
        # stack reparents and renumbers every sibling, so aggregate the verdict's
        # complete affected set rather than reconstructing it from picture_ids.
        event_picture_ids.update(result.event_picture_ids)
    prune_stale_groups_in_session(session)
    logger.info(
        "[dedup-verdict] auto-stacked %d exact group(s) under batch %s "
        "(%d blocked, %d failed)",
        len(results),
        batch_id,
        sum(1 for f in failures if f["outcome"] == BULK_REASON_BLOCKED),
        sum(1 for f in failures if f["outcome"] == BULK_REASON_FAILED),
    )
    return {
        # Always present once anything could have committed, so the caller always
        # holds the POST /operations/batches/{batch_id}/undo handle.
        "batch_id": batch_id,
        "dry_run": False,
        "groups": len(results),
        "pictures": sum(len(item["picture_ids"]) for item in results),
        "scope": scope.as_dict(),
        "results": results,
        "failures": failures,
        "blocked": sum(1 for f in failures if f["outcome"] == BULK_REASON_BLOCKED),
        "failed": sum(1 for f in failures if f["outcome"] == BULK_REASON_FAILED),
        # Wrapper-only broadcast plumbing; removed before the API response.
        "event_picture_ids": sorted(event_picture_ids),
    }


# --- Vault wrappers ---------------------------------------------------------


def _notify_pictures_changed(
    vault: "Vault",
    picture_ids: list[int],
    origin_client_id: Optional[str],
    source: str,
) -> None:
    """Announce a committed verdict on the WS envelope (§15).

    The standard ``pictures_changed`` emit every other mutation path raises, so
    a second tab refreshes its grid, queue and counts. Called from the vault
    wrappers, after the verdict's own commit, mirroring how the operation log's
    undo/redo wrappers emit after their DB task returns. ``origin_client_id`` /
    ``source`` are the values the handler read from the request and passed down
    explicitly - never a contextvar, which is dead off the request's task.
    """
    if not picture_ids:
        return
    vault.notify(
        EventType.CHANGED_PICTURES,
        {
            "picture_ids": sorted({int(pid) for pid in picture_ids}),
            "origin_client_id": origin_client_id,
            "change_kind": "updated",
            "source": source,
        },
    )


def apply_stack_verdict(
    vault: "Vault",
    signature: str,
    cover_picture_id: Optional[int] = None,
    excluded_picture_ids: Optional[Iterable[int]] = None,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> VerdictResult:
    """Write-path vault wrapper around :func:`apply_stack_verdict_in_session`.

    ``actor`` / ``source`` / ``origin_client_id`` come from
    ``operation_log_service.request_context(request)``, evaluated in the handler
    on the request's own task - never read here, where the contextvar is dead.
    """
    result = vault.db.run_task(
        apply_stack_verdict_in_session,
        signature,
        cover_picture_id,
        list(excluded_picture_ids or []),
        batch_id,
        actor,
        source,
        origin_client_id,
    )
    _notify_pictures_changed(vault, result.event_picture_ids, origin_client_id, source)
    return result


def apply_keep_separate(
    vault: "Vault",
    signature: str,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> VerdictResult:
    """Write-path vault wrapper around :func:`apply_keep_separate_in_session`.

    ``actor`` / ``source`` / ``origin_client_id`` come from
    ``operation_log_service.request_context(request)``, evaluated in the handler
    on the request's own task - never read here, where the contextvar is dead.
    """
    result = vault.db.run_task(
        apply_keep_separate_in_session,
        signature,
        batch_id,
        actor,
        source,
        origin_client_id,
    )
    _notify_pictures_changed(vault, result.event_picture_ids, origin_client_id, source)
    return result


def apply_verdict_batch_in_session(
    session: Session,
    actions: list[dict[str, Any]],
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Apply one frontend bulk gesture as one transaction and undo unit.

    A shared batch id alone cannot make several HTTP requests one history unit:
    another client can record between requests, producing ``A1(X), B, A2(X)``
    and a correctly stale-rejected batch. Keeping the whole gesture in one DB
    task makes its operation rows contiguous and commits all verdicts or none.
    """
    if not actions:
        raise DedupVerdictError("a verdict batch needs at least one action")

    batch_id = batch_id or new_batch_id()
    results: list[VerdictResult] = []
    event_picture_ids: set[int] = set()
    try:
        for action in actions:
            verdict = action["verdict"]
            if verdict == VERDICT_STACKED:
                result = apply_stack_verdict_in_session(
                    session,
                    action["signature"],
                    action.get("cover_picture_id"),
                    action.get("excluded_picture_ids") or [],
                    batch_id,
                    actor,
                    source,
                    origin_client_id,
                    commit=False,
                )
            elif verdict == VERDICT_KEEP_SEPARATE:
                result = apply_keep_separate_in_session(
                    session,
                    action["signature"],
                    batch_id,
                    actor,
                    source,
                    origin_client_id,
                    commit=False,
                )
            else:
                raise DedupVerdictError(f"unknown verdict {verdict!r}")
            results.append(result)
            event_picture_ids.update(result.event_picture_ids)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return {
        "batch_id": batch_id,
        "results": [result.as_dict() for result in results],
        "event_picture_ids": sorted(event_picture_ids),
    }


def apply_verdict_batch(
    vault: "Vault",
    actions: list[dict[str, Any]],
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Write-path wrapper for one atomic frontend verdict gesture."""
    report = vault.db.run_task(
        apply_verdict_batch_in_session,
        actions,
        batch_id,
        actor,
        source,
        origin_client_id,
    )
    event_picture_ids = report.pop("event_picture_ids", [])
    _notify_pictures_changed(vault, event_picture_ids, origin_client_id, source)
    return report


def reopen_verdict(
    vault: "Vault",
    signature: str,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Write-path vault wrapper around :func:`reopen_verdict_in_session`.

    ``actor`` / ``source`` / ``origin_client_id`` come from
    ``operation_log_service.request_context(request)``, evaluated in the handler
    on the request's own task - never read here, where the contextvar is dead.
    Emits the standard ``pictures_changed`` announcement over the affected
    members (and, for a clear that unstacked, the whole restored target set),
    since a clear changes state other tabs render.
    """
    result = vault.db.run_task(
        reopen_verdict_in_session,
        signature,
        batch_id,
        actor,
        source,
        origin_client_id,
    )
    event_picture_ids = result.pop("event_picture_ids", [])
    _notify_pictures_changed(vault, event_picture_ids, origin_client_id, source)
    return result


def bulk_auto_stack(
    vault: "Vault",
    scope: Optional[DedupScope] = None,
    batch_id: Optional[str] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Write-path vault wrapper around :func:`bulk_auto_stack_in_session`.

    A dry run returns before minting a batch id and writes nothing, so it goes to
    ``run_immediate_read_task`` instead of the serialised writer queue. On the
    queue it held the single writer thread for as long as the aggregate took, and
    every stack verdict behind it (also a write task) waited: the consent dialog
    timed out and the queue then refused each group with "Could not stack that
    group" until the preview finished (#751). The preview is read-only, so
    it has no business blocking writes even when it is slow.
    """
    runner = vault.db.run_immediate_read_task if dry_run else vault.db.run_task
    report = runner(
        bulk_auto_stack_in_session,
        scope,
        batch_id,
        dry_run,
        limit,
        actor,
        source,
        origin_client_id,
    )
    if not report.get("dry_run"):
        event_picture_ids = report.pop("event_picture_ids", [])
        # One post-commit announcement over the full stack-expanded affected set.
        _notify_pictures_changed(
            vault,
            event_picture_ids,
            origin_client_id,
            source,
        )
    return report


# Registered at import time, and this module is imported by
# ``pixlstash/routes/dedup.py``, which ``Server`` mounts at startup - so the
# hooks are in place before any request can reach undo. The registration lives
# here, not in the operation log, so the op-log core keeps no dedup knowledge.
# One hook per op_type, each scoped to its own verdict kind: a gesture batch
# spanning both never lets one hook reverse the other's rows.
operation_log_service.register_post_restore_hook(
    OP_TYPE_STACK,
    partial(restore_verdicts_in_session, verdict_kind=VERDICT_STACKED),
)
operation_log_service.register_post_restore_hook(
    OP_TYPE_KEEP_SEPARATE,
    partial(restore_verdicts_in_session, verdict_kind=VERDICT_KEEP_SEPARATE),
)
# The clear's own hook, correlated by reopen_batch_id (never batch_id, which
# stays the verdict's own undo handle). Direction-inverted by design: undoing a
# clear re-decides; redoing it reopens.
operation_log_service.register_post_restore_hook(
    OP_TYPE_REOPEN,
    restore_reopens_in_session,
)


__all__ = [
    "BULK_REASON_APPLIED",
    "BULK_REASON_BLOCKED",
    "BULK_REASON_FAILED",
    "OP_TYPE_KEEP_SEPARATE",
    "OP_TYPE_REOPEN",
    "OP_TYPE_STACK",
    "DedupVerdictError",
    "VerdictResult",
    "apply_keep_separate",
    "apply_keep_separate_in_session",
    "apply_metadata_union_in_session",
    "apply_stack_verdict",
    "apply_stack_verdict_in_session",
    "apply_verdict_batch",
    "apply_verdict_batch_in_session",
    "bulk_auto_stack",
    "bulk_auto_stack_in_session",
    "new_batch_id",
    "reopen_verdict",
    "reopen_verdict_in_session",
    "restore_reopens_in_session",
    "restore_verdicts_in_session",
]
