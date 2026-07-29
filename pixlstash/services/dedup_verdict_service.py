"""Applying and remembering duplicate verdicts.

There are exactly two verdicts in v1.9 and neither of them deletes anything:

* **stack** — the chosen members become one stack led by the chosen cover, with
  the metadata union applied; excluded members stay exactly where they were;
* **keep separate** — nothing changes on disk or in the picture rows, but the
  group's signature is remembered so no rescan and no re-import ever re-asks.

Both are recorded against the group *signature*, not against picture or group
ids, which is what makes the memory survive a re-import (see
:func:`pixlstash.services.dedup_tier_service.group_signature`). "Keep separate"
is permanent until :func:`reopen_verdict_in_session` is called from the Stacks
view.

Metadata union (design delta 5)
-------------------------------
Stacking unions **tags, project membership, set membership and characters** onto
every member and lifts every member to the **highest score** in the group.
Nothing is overwritten and nothing is lost — a union cannot break an album,
which is the failure mode that burns Immich users.

Project and set membership already had a union in
:func:`pixlstash.services.stack_membership.reconcile_stack_membership`; this
module calls it and adds the three the design requires on top:

* **tags** — every member gains every non-sentinel tag any member carries.
  Sentinel tags (``__tag``, ``__tag:<engine>``) are pipeline markers, not user
  metadata, so they are deliberately excluded: copying a "needs retagging"
  marker onto a picture that was already tagged would re-queue it for no reason.
* **score** — every member is lifted to ``max(score)``. Only lifted: a union
  never lowers a rating the user set.
* **characters** — a real face-to-character union is not expressible without
  fabricating :class:`~pixlstash.db_models.face.Face` rows (a face has a bbox and
  an embedding that belong to one specific picture), and inventing detection data
  is worse than not unioning. Instead, when the group's members between them
  reference exactly **one** character, every member that does not already carry
  it gets ``Picture.pending_character_id`` set — the shipped deferred-assignment
  mechanism that the face-extraction task consumes. A group spanning several
  characters is left alone and logged; the members keep their own faces, which is
  the non-lossy outcome.

Operation log (§21)
-------------------
Every verdict raises an action receipt and lands in the operation log. Each
verdict records **exactly one** :class:`~pixlstash.db_models.operation.Operation`
row, and a bulk auto-stack shares a single ``batch_id`` across every group in the
run, so ``POST /operations/batches/{batch_id}/undo`` reverses a thousand stacks in
one step.

Two details this module owns rather than inherits:

* **It does not go through** ``routes/stacks.py``. Those handlers already wrap
  themselves in ``run_recorded_metadata_task``; calling them would produce a
  second operation row per verdict, and "one verdict, one undo" would stop being
  true. :func:`_stack_members` does the stacking in-session instead, and this
  module records once around the whole verdict.
* **It snapshots the stack-expanded set**, not just the group's members
  (§21's ``expand_stacks`` rule). Folding an existing stack into the new one
  reparents co-members the group never named, and ``normalize_stack_positions``
  renumbers *every* member including soft-deleted ones — so the snapshot is taken
  over :func:`expand_picture_ids_to_stacks` with ``include_deleted=True``, or an
  undo would restore the group and leave its siblings behind.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
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
from pixlstash.db_models.operation import Operation
from pixlstash.db_models.tag import Tag, is_tag_sentinel
from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service
from pixlstash.services.dedup_tier_service import (
    ID_CHUNK,
    DedupScope,
    DedupTier,
    prune_stale_groups_in_session,
)
from pixlstash.services.stack_membership import (
    expand_picture_ids_to_stacks,
    reconcile_stack_membership,
)
from pixlstash.stacking import normalize_stack_positions

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# The one op_type this module records. Stable — part of the API contract the
# frontend keys its undo affordances off. A bulk auto-stack shares one batch_id
# across every row it writes, so the whole run reverses in a single step.
#
# Keep-separate and reopen have no op_type on purpose: they change no reversible
# picture facet, so they record nothing rather than writing a no-op row that
# would still consume a Ctrl+Z.
OP_TYPE_STACK = "dedup.stack"

# Per-group outcome vocabulary for a bulk auto-stack. Closed set; the response
# reports every group under exactly one of these, so a partial run is legible
# rather than inferred from a count mismatch.
BULK_REASON_APPLIED = "applied"
BULK_REASON_BLOCKED = "blocked"
"""The group was refused by a guard that returns an HTTP status — in practice a
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
    """

    signature: str
    verdict: str
    stack_id: Optional[int]
    cover_picture_id: Optional[int]
    picture_ids: list[int]
    excluded_picture_ids: list[int]
    batch_id: Optional[str]
    metadata_union: dict[str, Any]

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
) -> None:
    """Append **one** operation row for this verdict.

    Called once per verdict, around the whole mutation, on the verdict's own
    session — so the row and the change it describes commit against the same
    serialised writer (§21). The verdict path deliberately does not reuse
    ``routes/stacks.py``, which records itself; going through it would produce a
    second row and break "one verdict, one undo".
    """
    operation_log_service.record_operation_in_session(
        session,
        op_type=op_type,
        before=before,
        after=after,
        batch_id=batch_id,
        summary=summary,
        actor=actor,
        source=source,
        origin_client_id=origin_client_id,
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
        session: Pre-opened session. Not committed here — the caller owns the
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


def _stack_members(
    session: Session, picture_ids: list[int], cover_picture_id: int
) -> int:
    """Put *picture_ids* into one stack led by *cover_picture_id*.

    Reuses an existing stack when the members already share one (growing it
    rather than orphaning it), and folds several stacks into the cover's when the
    group spans more than one. Always additive: no picture leaves a stack it was
    in, and no stack row is dropped that still has members.
    """
    # Imported locally: set_lock_service imports stack_membership, which imports
    # this package's siblings; a module-level import here would be circular.
    from pixlstash.services.set_lock_service import enforce_stack_membership_not_locked

    pictures = {
        int(pic.id): pic
        for pic in session.exec(
            select(Picture).where(Picture.id.in_(picture_ids))
        ).all()
    }
    missing = sorted(set(picture_ids) - set(pictures))
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
    # at position 0 — the leader convention the whole app reads.
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

    The union is **not** run to work this out — nothing is written and no
    membership is reconciled. Each figure is derived from the planned verdict:
    the cover is the group's stored preselection, and a cover "gains" a facet
    when some other member of its group already carries something the cover does
    not (which is exactly what :func:`apply_metadata_union_in_session` would then
    copy onto it).

    Returns:
        ``groups_by_tier`` (always keyed by every tier, zero-filled),
        ``pictures``, ``covers_gaining_tags``, ``covers_gaining_score`` and
        ``covers_gaining_metadata`` (the union of the previous two — the row the
        design's dialog promises).
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

    group_ids = [int(group.id) for group in groups]
    members_by_group: dict[int, list[int]] = defaultdict(list)
    for group_id, picture_id in session.exec(
        select(DedupGroupMember.group_id, DedupGroupMember.picture_id)
        .join(Picture, Picture.id == DedupGroupMember.picture_id)
        .where(
            DedupGroupMember.group_id.in_(group_ids),
            Picture.deleted.is_(False),
        )
    ).all():
        members_by_group[int(group_id)].append(int(picture_id))

    all_ids = [pid for ids in members_by_group.values() for pid in ids]
    scores = dict(
        session.exec(
            select(Picture.id, Picture.score).where(Picture.id.in_(all_ids))
        ).all()
    )
    tags_by_picture: dict[int, set[str]] = defaultdict(set)
    for picture_id, tag in session.exec(
        select(Tag.picture_id, Tag.tag).where(Tag.picture_id.in_(all_ids))
    ).all():
        if not is_tag_sentinel(tag):
            tags_by_picture[int(picture_id)].add(str(tag))

    for group in groups:
        member_ids = members_by_group.get(int(group.id), [])
        summary["groups_by_tier"][str(group.tier)] = (
            summary["groups_by_tier"].get(str(group.tier), 0) + 1
        )
        summary["pictures"] += len(member_ids)
        cover_id = int(group.cover_picture_id or (member_ids[0] if member_ids else 0))
        if cover_id not in member_ids:
            continue
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
) -> VerdictResult:
    """Stack a group's members and remember the decision.

    Args:
        session: Pre-opened session; this function commits once, so the stack,
            the metadata union and the verdict row land together or not at all.
        signature: The group signature from the queue.
        cover_picture_id: The cover the user confirmed. Defaults to the server's
            preselection stored on the group.
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
        The :class:`VerdictResult` behind the action receipt.

    Raises:
        DedupVerdictError: Unknown signature, a cover that is not a member, or
            fewer than two members left after exclusions.
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
    if cover_id not in included:
        raise DedupVerdictError(
            f"cover {cover_id} is not an included member of group {signature!r}"
        )

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
        summary=f"Stacked {len(included)} duplicates",
        actor=actor,
        source=source,
        origin_client_id=origin_client_id,
    )
    session.commit()
    logger.info(
        "[dedup-verdict] stacked %d picture(s) into stack %s (cover=%s, "
        "excluded=%s, signature=%s, batch=%s)",
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
    )


def apply_keep_separate_in_session(
    session: Session, signature: str, batch_id: Optional[str] = None
) -> VerdictResult:
    """Remember that this group is *not* duplicates. Changes no picture row.

    Permanent until :func:`reopen_verdict_in_session`. This is the verdict that
    makes the sidebar count trustworthy: without it every rescan would re-offer
    the same rejected group and the badge would never reach zero.
    """
    _group, member_ids = _load_group(session, signature)
    # batch_id is deliberately NOT stored on the row: keep-separate records no
    # operation, so a stored id could only correlate it to OTHER verdicts'
    # restores. With a shared client gesture id that made undoing a stack
    # silently reverse an unrelated keep-separate (CSO R5). The id is still
    # echoed in the response so the client can group its receipts.
    _upsert_verdict(
        session,
        signature=signature,
        verdict=VERDICT_KEEP_SEPARATE,
        picture_ids=member_ids,
        excluded_picture_ids=[],
        cover_picture_id=None,
        stack_id=None,
        batch_id=None,
    )
    # No operation row: keep-separate changes no reversible picture facet, so
    # there is nothing for undo to restore. Recording an empty diff would be a
    # no-op row (``record_operation_in_session`` returns None for one) that still
    # consumed a Ctrl+Z, which is worse than not offering undo here. The user's
    # way back is the explicit reopen action, which is what the Stacks view shows.
    session.commit()
    logger.info(
        "[dedup-verdict] keep-separate recorded for signature=%s (%d members)",
        signature,
        len(member_ids),
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
    )


def reopen_verdict_in_session(session: Session, signature: str) -> dict[str, Any]:
    """Undo the *memory* of a verdict so the group returns to the queue.

    Stamps ``reopened_at`` rather than deleting the row: the decision history is
    worth keeping, and a reopened verdict is simply no longer live. The pictures
    are untouched — reopening a ``stacked`` verdict does not unstack anything,
    because unstacking is the Stacks view's own action.
    """
    row = session.exec(
        select(DedupVerdict).where(DedupVerdict.signature == signature)
    ).first()
    if row is None:
        raise DedupVerdictError(f"No verdict recorded for signature {signature!r}")
    if row.reopened_at is not None:
        raise DedupVerdictError(f"Verdict for {signature!r} is already reopened")
    row.reopened_at = datetime.utcnow()
    session.add(row)
    group = session.exec(
        select(DedupGroup).where(DedupGroup.signature == signature)
    ).first()
    if group is not None:
        group.resolved = False
        session.add(group)
    # No operation row, for the same reason as keep-separate: reopening touches
    # only the verdict row, which is not a reversible picture facet.
    session.commit()
    logger.info("[dedup-verdict] reopened verdict for signature=%s", signature)
    return {
        "signature": signature,
        "previous_verdict": row.verdict,
        "reopened_at": row.reopened_at,
        "group_returned_to_queue": group is not None,
    }


def restore_verdicts_in_session(
    session: Session, operations: list[Operation], direction: str
) -> None:
    """Reopen (on undo) or re-decide (on redo) the verdicts *operations* recorded.

    Registered with the operation log as the post-restore hook for
    :data:`OP_TYPE_STACK` (see
    :func:`pixlstash.services.operation_log_service.register_post_restore_hook`).

    Why this is needed at all: the operation log restores the reversible *picture*
    facets, and a stack verdict changes two more things that are not picture
    facets — the ``DedupVerdict`` row (decided) and the ``DedupGroup`` row
    (resolved). Without this hook an undo unstacked the pictures but left the
    group decided, so it never returned to the queue, survived a rescan (the
    signature still carried a live verdict) and was recoverable only through
    ``POST /dedup/verdicts/reopen``.

    Correlation is by ``batch_id``: a stack verdict is always recorded under one
    (minted server-side when the caller supplies none), and the verdict row
    stores the same id. One query covers a 2 700-group batch undo, which is why
    the hook takes the whole list rather than one operation at a time.

    Args:
        session: The restore's own session. Not committed here — the operation
            log commits the restore and this together, so the pictures and the
            queue can never disagree.
        operations: Every :data:`OP_TYPE_STACK` operation in this restore.
        direction: ``operation_log_service.RESTORE_UNDO`` or ``RESTORE_REDO``.
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
    reopened_at = None if is_redo else datetime.utcnow()
    signatures: list[str] = []
    for start in range(0, len(batch_ids), ID_CHUNK):
        chunk = batch_ids[start : start + ID_CHUNK]
        for row in session.exec(
            # Only STACK verdicts: the restored operations are OP_TYPE_STACK, and
            # a keep-separate sharing the same client gesture batch id (#644)
            # must not be silently reversed by undoing the stack — the product
            # calls keep-separate permanent-until-reopened (CSO R5).
            select(DedupVerdict).where(
                DedupVerdict.batch_id.in_(chunk),
                DedupVerdict.verdict == VERDICT_STACKED,
            )
        ).all():
            # decided_at is deliberately not re-stamped on redo: it records when
            # the user decided, and the row is "live" precisely when reopened_at
            # is NULL, so one field carries the whole lifecycle honestly.
            row.reopened_at = reopened_at
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
        DedupGroup.resolved.is_(False), DedupGroup.tier == TIER_EXACT
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
        picture_total = 0
        for group in groups:
            picture_total += int(group.member_count or 0)
        return {
            "dry_run_summary": _dry_run_summary_in_session(session, groups),
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
            # applied bulk mutation whose batch id the caller never receives —
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
    }


# --- Vault wrappers ---------------------------------------------------------


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
    on the request's own task — never read here, where the contextvar is dead.
    """
    return vault.db.run_task(
        apply_stack_verdict_in_session,
        signature,
        cover_picture_id,
        list(excluded_picture_ids or []),
        batch_id,
        actor,
        source,
        origin_client_id,
    )


def apply_keep_separate(
    vault: "Vault", signature: str, batch_id: Optional[str] = None
) -> VerdictResult:
    """Write-path vault wrapper around :func:`apply_keep_separate_in_session`."""
    return vault.db.run_task(apply_keep_separate_in_session, signature, batch_id)


def reopen_verdict(vault: "Vault", signature: str) -> dict[str, Any]:
    """Write-path vault wrapper around :func:`reopen_verdict_in_session`."""
    return vault.db.run_task(reopen_verdict_in_session, signature)


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
    """Write-path vault wrapper around :func:`bulk_auto_stack_in_session`."""
    return vault.db.run_task(
        bulk_auto_stack_in_session,
        scope,
        batch_id,
        dry_run,
        limit,
        actor,
        source,
        origin_client_id,
    )


# Registered at import time, and this module is imported by
# ``pixlstash/routes/dedup.py``, which ``Server`` mounts at startup — so the hook
# is in place before any request can reach undo. The registration lives here, not
# in the operation log, so the op-log core keeps no dedup knowledge.
operation_log_service.register_post_restore_hook(
    OP_TYPE_STACK, restore_verdicts_in_session
)


__all__ = [
    "BULK_REASON_APPLIED",
    "BULK_REASON_BLOCKED",
    "BULK_REASON_FAILED",
    "OP_TYPE_STACK",
    "DedupVerdictError",
    "VerdictResult",
    "apply_keep_separate",
    "apply_keep_separate_in_session",
    "apply_metadata_union_in_session",
    "apply_stack_verdict",
    "apply_stack_verdict_in_session",
    "bulk_auto_stack",
    "bulk_auto_stack_in_session",
    "new_batch_id",
    "reopen_verdict",
    "reopen_verdict_in_session",
    "restore_verdicts_in_session",
]
