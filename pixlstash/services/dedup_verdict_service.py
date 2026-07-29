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

Operation log
-------------
Every verdict is meant to raise an action receipt and land in the operation log,
with a bulk auto-stack coalescing into one batch id so N stacks reverse with one
undo. The operation log ships on its own lane (``feature/operation-log``); this
branch is stacked on the dedup sweep planner and does not contain it, so the call
is made through :func:`_record_operation`, which imports the service lazily and
logs a clear warning naming the missing module when it is absent. Wiring the two
lanes together at merge time is a no-op: the seam already passes ``batch_id``,
``op_type`` and the before/after picture id set.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable, Optional

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
from pixlstash.db_models.tag import Tag, is_tag_sentinel
from pixlstash.pixl_logging import get_logger
from pixlstash.services.dedup_tier_service import (
    DedupScope,
    prune_stale_groups_in_session,
)
from pixlstash.services.stack_membership import reconcile_stack_membership
from pixlstash.stacking import normalize_stack_positions

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# One op-log entry per verdict; the bulk path shares a single batch id.
OP_TYPE_STACK = "dedup.stack"
OP_TYPE_KEEP_SEPARATE = "dedup.keep_separate"
OP_TYPE_REOPEN = "dedup.reopen"


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
    """Mint a batch id grouping one bulk action's operations into one undo."""
    return uuid.uuid4().hex


def _record_operation(
    session: Session,
    *,
    op_type: str,
    picture_ids: list[int],
    before: dict[str, dict],
    after: dict[str, dict],
    batch_id: Optional[str],
    summary: str,
) -> None:
    """Append this verdict to the operation log, if that lane is present.

    Cross-lane seam, not a fallback: ``pixlstash.services.operation_log_service``
    lands on ``feature/operation-log`` and is not on this branch. When it is
    missing the verdict still applies (it is an ordinary, individually reversible
    stacking action) but is not undoable in one keystroke, and that consequence is
    logged with the module name so the gap is visible rather than silent.
    """
    try:
        from pixlstash.services import operation_log_service
    except ImportError as exc:
        logger.warning(
            "[dedup-verdict] operation log unavailable (%s): %s %s on %d picture(s) "
            "batch=%s applied but NOT recorded, so Ctrl+Z will not reverse it. "
            "This is the feature/operation-log lane seam; wiring it up needs no "
            "change here.",
            exc,
            op_type,
            summary,
            len(picture_ids),
            batch_id,
        )
        return
    operation_log_service.record_operation_in_session(
        session,
        op_type=op_type,
        before=before,
        after=after,
        batch_id=batch_id,
        summary=summary,
    )


def _capture_state(session: Session, picture_ids: list[int]) -> dict[str, dict]:
    """Snapshot the picture state an undo would restore, if the log is present."""
    try:
        from pixlstash.services import operation_log_service
    except ImportError:
        # Already reported by _record_operation with full context; capturing
        # nothing here keeps the verdict path free of a second identical warning.
        return {}
    return operation_log_service.capture_state_in_session(session, picture_ids)


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


# --- Verdicts ---------------------------------------------------------------


def apply_stack_verdict_in_session(
    session: Session,
    signature: str,
    cover_picture_id: Optional[int] = None,
    excluded_picture_ids: Optional[Iterable[int]] = None,
    batch_id: Optional[str] = None,
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

    Returns:
        The :class:`VerdictResult` behind the action receipt.

    Raises:
        DedupVerdictError: Unknown signature, a cover that is not a member, or
            fewer than two members left after exclusions.
    """
    group, member_ids = _load_group(session, signature)
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

    before = _capture_state(session, included)
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
    after = _capture_state(session, included)
    _record_operation(
        session,
        op_type=OP_TYPE_STACK,
        picture_ids=included,
        before=before,
        after=after,
        batch_id=batch_id,
        summary=f"Stacked {len(included)} duplicates",
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
    _record_operation(
        session,
        op_type=OP_TYPE_KEEP_SEPARATE,
        picture_ids=member_ids,
        before={},
        after={},
        batch_id=batch_id,
        summary=f"Kept {len(member_ids)} pictures separate",
    )
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
    _record_operation(
        session,
        op_type=OP_TYPE_REOPEN,
        picture_ids=json.loads(row.picture_ids or "[]"),
        before={},
        after={},
        batch_id=None,
        summary="Reopened a duplicate decision",
    )
    session.commit()
    logger.info("[dedup-verdict] reopened verdict for signature=%s", signature)
    return {
        "signature": signature,
        "previous_verdict": row.verdict,
        "reopened_at": row.reopened_at,
        "group_returned_to_queue": group is not None,
    }


def bulk_auto_stack_in_session(
    session: Session,
    scope: Optional[DedupScope] = None,
    batch_id: Optional[str] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
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

    Returns:
        Counts, the batch id, and the per-group results (empty for a dry run).
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
                session, group.signature, batch_id=batch_id
            )
        except DedupVerdictError as exc:
            # One unstackable group must not abort the run; record it so the
            # response reports an honest partial result instead of pretending
            # every group succeeded.
            logger.warning(
                "[dedup-verdict] auto-stack skipped group %s: %s",
                group.signature,
                exc,
            )
            failures.append({"signature": group.signature, "error": str(exc)})
            continue
        results.append(result.as_dict())
    prune_stale_groups_in_session(session)
    logger.info(
        "[dedup-verdict] auto-stacked %d exact group(s) under batch %s (%d skipped)",
        len(results),
        batch_id,
        len(failures),
    )
    return {
        "batch_id": batch_id,
        "dry_run": False,
        "groups": len(results),
        "pictures": sum(len(item["picture_ids"]) for item in results),
        "scope": scope.as_dict(),
        "results": results,
        "failures": failures,
    }


# --- Vault wrappers ---------------------------------------------------------


def apply_stack_verdict(
    vault: "Vault",
    signature: str,
    cover_picture_id: Optional[int] = None,
    excluded_picture_ids: Optional[Iterable[int]] = None,
    batch_id: Optional[str] = None,
) -> VerdictResult:
    """Write-path vault wrapper around :func:`apply_stack_verdict_in_session`."""
    return vault.db.run_task(
        apply_stack_verdict_in_session,
        signature,
        cover_picture_id,
        list(excluded_picture_ids or []),
        batch_id,
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
) -> dict[str, Any]:
    """Write-path vault wrapper around :func:`bulk_auto_stack_in_session`."""
    return vault.db.run_task(
        bulk_auto_stack_in_session, scope, batch_id, dry_run, limit
    )


__all__ = [
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
    "bulk_auto_stack",
    "bulk_auto_stack_in_session",
    "new_batch_id",
    "reopen_verdict",
    "reopen_verdict_in_session",
]
