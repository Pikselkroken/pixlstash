"""Service layer for review sessions - one tag + a frozen scope + one scan's results.

A :class:`~pixlstash.db_models.review.Review` is the first-class noun of the tag
review workflow (see ``docs/reviews/2026-07-review-sessions-redesign-draft.md``):
created explicitly, scanned once at creation, refreshed append-only, and finally
archived or aborted. Per-item decisions (accept/dismiss/fix-twin/swap/reopen)
stay in :mod:`pixlstash.services.tag_suggestion_service` and are written through
immediately - archiving/aborting a review never touches suggestion rows.

Mirrors the vault-task conventions of the sibling services (all DB access via
``vault.db.run_task`` / ``run_immediate_read_task``).
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, or_
from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureSet, Review, TaggerRun
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.pixl_logging import get_logger
from pixlstash.services import tag_scan_service, tag_suggestion_service
from pixlstash.services.set_lock_service import (
    locked_picture_id_subquery,
    locked_sets_for_pictures,
)
from pixlstash.services.tag_scan_service import DEFAULT_MAX_TWIN_HAMMING
from pixlstash.utils.near_neighbor import hamming_distance
from pixlstash.utils.service.filter_helpers import (
    fetch_tag_review_scope_picture_ids,
)
from pixlstash.utils.service.scope_table import scope_id_subquery

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# min_combined threshold used for the creation receipt's "N obvious pairs -
# auto-resolve?" count; matches the bulk-accept endpoint's default.
AUTO_RESOLVE_MIN_COMBINED = 0.9

OPEN = "OPEN"
ARCHIVED = "ARCHIVED"
ABORTED = "ABORTED"
VALID_STATUSES = (OPEN, ARCHIVED, ABORTED)


class ReviewConflictError(Exception):
    """Raised when the requested transition/creation conflicts with review state."""


class ReviewLockedError(Exception):
    """Raised when a review's scope set is locked (its pictures are read-only)."""


def _assert_set_scope_not_locked(session: Session, set_id: int | None) -> None:
    """Backstop for the create/preview scope: a locked set can't be reviewed (its
    pictures are frozen). No-op for a None/unknown set.

    Kept local (raises :class:`ReviewLockedError`, not ``HTTPException``) so the
    service stays transport-agnostic; the route maps it to 423.
    """
    if set_id is None:
        return
    picture_set = session.get(PictureSet, set_id)
    if picture_set is not None and getattr(picture_set, "locked", False):
        raise ReviewLockedError(
            f"Picture set {picture_set.name!r} is locked; its pictures are "
            "read-only, so it cannot be used as a review scope. Unlock it first."
        )


def _serialize(review: Review) -> dict:
    return {
        "id": review.id,
        "tag": review.tag,
        "scope": {
            "project_id": review.project_id,
            "set_id": review.set_id,
            "character_id": review.character_id,
        },
        "status": review.status,
        "stats": {
            "scanned": review.scanned,
            "found": review.found,
            "prev_reviewed": review.prev_reviewed,
        },
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "refreshed_at": review.refreshed_at.isoformat()
        if review.refreshed_at
        else None,
    }


def _resolve_scope_ids(
    vault: "Vault",
    *,
    project_id: int | None,
    set_id: int | None,
    character_id: str | None,
) -> set[int] | None:
    """Resolve the review's frozen scope filters to picture ids.

    ``None`` means unrestricted (no filters - whole vault). An empty set is a
    valid "nothing in scope" result. The ``/reviews`` surface is owner-only
    (scoped share tokens are rejected at the route boundary), so there is no
    token scope to intersect here.
    """
    if project_id is None and set_id is None and not character_id:
        return None

    def _fetch(session: Session) -> set[int] | None:
        return fetch_tag_review_scope_picture_ids(
            session,
            project_id=project_id,
            set_id=set_id,
            character_id=character_id,
        )

    return vault.db.run_immediate_read_task(_fetch)


def _auto_resolvable_count(vault: "Vault", review: Review) -> int:
    """How many of the review's PENDING rows the bulk auto-resolve would apply.

    Reuses the bulk-accept dry-run (both independent signals agree and clear
    :data:`AUTO_RESOLVE_MIN_COMBINED`), scoped to this review's suggestion rows.
    """
    if review.id is None:
        return 0
    result = tag_suggestion_service.bulk_accept(
        vault,
        review.tag,
        AUTO_RESOLVE_MIN_COMBINED,
        dry_run=True,
        review_id=review.id,
    )
    return int(result.get("count", 0))


def create_review(
    vault: "Vault",
    tag: str,
    *,
    project_id: int | None = None,
    set_id: int | None = None,
    character_id: str | None = None,
    include_reviewed: bool = False,
) -> dict:
    """Create a review for ``tag``, run its scan, and return the receipt.

    The scope (project/set/character) is frozen onto the review row; the scan
    is restricted to the resolved scope picture ids. At most one OPEN review
    may exist per tag (enforced both here and by the partial unique index).

    Returns the serialized review incl. ``stats`` (scanned/found/prev_reviewed/
    auto_resolvable).

    Raises:
        ReviewConflictError: An OPEN review for the tag already exists.
    """

    def _create(session: Session) -> Review:
        _assert_set_scope_not_locked(session, set_id)
        open_existing = session.exec(
            select(Review).where(Review.tag == tag, Review.status == OPEN)
        ).first()
        if open_existing is not None:
            raise ReviewConflictError(
                f"An open review for tag {tag!r} already exists (id={open_existing.id})"
            )
        review = Review(
            tag=tag,
            project_id=project_id,
            set_id=set_id,
            character_id=character_id,
            status=OPEN,
            created_at=datetime.utcnow(),
        )
        session.add(review)
        session.commit()
        session.refresh(review)
        return review

    review = vault.db.run_task(_create)

    scope_ids = _resolve_scope_ids(
        vault,
        project_id=project_id,
        set_id=set_id,
        character_id=character_id,
    )

    try:
        scan = tag_scan_service.scan_tag(
            vault,
            tag,
            project=None,  # scope comes from the resolved picture ids
            picture_ids=scope_ids,
            review_id=review.id,
            include_reviewed=include_reviewed,
        )
    except Exception:
        # Don't leave a half-created OPEN review blocking the tag.
        def _abort(session: Session) -> None:
            row = session.get(Review, review.id)
            if row is not None:
                row.status = ABORTED
                session.commit()

        vault.db.run_task(_abort)
        raise

    def _update(session: Session) -> Review:
        row = session.get(Review, review.id)
        row.scanned = scan["scanned"]
        row.found = scan["new"]
        row.prev_reviewed = scan["prev_reviewed"]
        session.commit()
        session.refresh(row)
        return row

    review = vault.db.run_task(_update)
    out = _serialize(review)
    out["stats"]["auto_resolvable"] = _auto_resolvable_count(vault, review)
    return out


def preview_review(
    vault: "Vault",
    tag: str,
    *,
    project_id: int | None = None,
    set_id: int | None = None,
    character_id: str | None = None,
) -> dict:
    """What a review with this tag+scope would cover, before creating it.

    Powers the New-review dialog: ``in_scope`` = pictures the scan would
    consider (non-deleted, inside the resolved scope), ``prev_reviewed`` =
    in-scope suspects for the tag already decided in earlier reviews (the
    count the "include previously reviewed" toggle re-surfaces).
    """
    scope_ids = _resolve_scope_ids(
        vault,
        project_id=project_id,
        set_id=set_id,
        character_id=character_id,
    )

    def _fetch(session: Session) -> dict:
        _assert_set_scope_not_locked(session, set_id)
        in_scope_q = (
            select(func.count()).select_from(Picture).where(Picture.deleted.is_(False))
        )
        prev_q = (
            select(func.count())
            .select_from(TagSuggestion)
            .where(
                TagSuggestion.tag == tag,
                TagSuggestion.source == tag_scan_service.SOURCE,
                # Only genuinely DECIDED rows are "previously reviewed"; a
                # SKIPPED row carries no decision, matching scan_tag's
                # prev_reviewed (which also excludes SKIPPED).
                TagSuggestion.status.notin_(["PENDING", "SKIPPED"]),
            )
        )
        if scope_ids is not None:
            # Filter via a temp-table subquery instead of one bound parameter
            # per id: a large scope (tens of thousands of pictures) would
            # otherwise exceed SQLite's bound-parameter ceiling and raise
            # OperationalError (a 500). One materialised table, referenced by
            # both membership tests. Result-identical to ``.in_(scope_ids)``.
            sub = scope_id_subquery(session, scope_ids)
            in_scope_q = in_scope_q.where(Picture.id.in_(sub))
            prev_q = prev_q.where(TagSuggestion.picture_id.in_(sub))
        return {
            "in_scope": int(session.exec(in_scope_q).one()),
            "prev_reviewed": int(session.exec(prev_q).one()),
        }

    return vault.db.run_immediate_read_task(_fetch)


def _progress_map(session: Session, review_ids: list[int]) -> dict[int, dict]:
    """``review_id -> {"done", "pending", "skipped", "locked"}`` over the rows.

    ``done`` counts decided rows only; SKIPPED rows carry no decision and are
    reported separately. A review is complete when ``pending`` reaches zero.

    ``locked`` counts still-PENDING rows whose suspect is frozen by a locked set.
    They are deliberately **not** counted in ``pending``: they are never served as
    cards (see :func:`list_review_suggestions`), so counting them as pending would
    leave the session reporting work remaining while its queue serves nothing -
    the UI would look stuck at "N remaining" with no card to act on. Splitting
    them out keeps the "complete when ``pending`` is 0" invariant true for a
    session whose pictures were locked mid-review, while still reporting the
    withheld rows so the UI can explain *why* the count dropped. Only PENDING rows
    can be ``locked``; a row decided before the lock stays counted as ``done``.
    """
    progress = {
        rid: {"done": 0, "pending": 0, "skipped": 0, "locked": 0} for rid in review_ids
    }
    if not review_ids:
        return progress
    is_locked = TagSuggestion.picture_id.in_(locked_picture_id_subquery())
    rows = session.exec(
        select(
            TagSuggestion.review_id,
            TagSuggestion.status,
            is_locked.label("is_locked"),
            func.count().label("n"),
        )
        .where(TagSuggestion.review_id.in_(review_ids))
        .group_by(TagSuggestion.review_id, TagSuggestion.status, is_locked)
    ).all()
    for rid, status, locked_flag, n in rows:
        if status == "PENDING":
            bucket = "locked" if locked_flag else "pending"
        elif status == "SKIPPED":
            bucket = "skipped"
        else:
            bucket = "done"
        progress[rid][bucket] += n
    return progress


def _receipt(session: Session, review_id: int) -> dict:
    """The review's outcome receipt: labels removed / added / kept / skipped.

    Derived from the review's resolved suggestion rows: ACCEPTED splits by
    direction (remove → removed, add → added); DISMISSED affirms the current
    label (kept); SWAPPED changed both sides (removed + added); TWIN_FIXED
    changed the twin in the suggestion's direction's favour (remove-direction
    → the twin gained the tag, add-direction → the twin lost it); SKIPPED
    made no decision at all and is reported as its own count.
    """
    removed = added = kept = skipped = 0
    for status, direction, n in session.exec(
        select(TagSuggestion.status, TagSuggestion.direction, func.count())
        .where(
            TagSuggestion.review_id == review_id,
            TagSuggestion.status != "PENDING",
        )
        .group_by(TagSuggestion.status, TagSuggestion.direction)
    ).all():
        if status == "ACCEPTED":
            if direction == "remove":
                removed += n
            else:
                added += n
        elif status == "DISMISSED":
            kept += n
        elif status == "SWAPPED":
            removed += n
            added += n
        elif status == "TWIN_FIXED":
            if direction == "remove":
                added += n
            else:
                removed += n
        elif status == "SKIPPED":
            skipped += n
    return {"removed": removed, "added": added, "kept": kept, "skipped": skipped}


def _frozen_snapshot(review: Review) -> dict | None:
    """The stored ``{"receipt", "progress"}`` for a closed review, or ``None``.

    A closed review's receipt/progress are frozen onto ``receipt_snapshot`` when
    it is archived/aborted (see :func:`set_review_status`), so a later scan that
    re-parents its rows into a new review cannot shrink its historical cover
    sheet. Returns ``None`` for OPEN reviews and for closed reviews with no
    snapshot (closed before the column existed) - both fall back to live
    aggregation at the call site.
    """
    if review.status != OPEN and review.receipt_snapshot:
        try:
            return json.loads(review.receipt_snapshot)
        except (ValueError, TypeError):
            logger.warning(
                "review %s has unparseable receipt_snapshot; falling back to "
                "live receipt/progress aggregation",
                review.id,
            )
    return None


def _latest_vault_change(session: Session) -> datetime | None:
    """The newest of (latest picture created_at, latest tagger-run completion)."""
    latest_pic = session.exec(
        select(func.max(Picture.created_at)).where(Picture.deleted.is_(False))
    ).one()
    latest_run = session.exec(select(func.max(TaggerRun.created_at))).one()
    candidates = [t for t in (latest_pic, latest_run) if t is not None]
    return max(candidates) if candidates else None


def _is_stale(review: Review, latest_change: datetime | None) -> bool:
    anchor = review.refreshed_at or review.created_at
    if latest_change is None or anchor is None:
        return False
    return latest_change > anchor


def list_reviews(vault: "Vault", status: str | None = None) -> list[dict]:
    """List reviews (newest first) with per-review progress and staleness.

    Each item is the serialized review plus ``progress`` (``done`` = the
    review's non-PENDING suggestion rows, ``pending``) and ``stale`` - True
    when the vault changed (new pictures or a tagger run) after the review's
    last scan.
    """

    def _fetch(session: Session) -> list[dict]:
        q = select(Review).order_by(Review.created_at.desc(), Review.id.desc())
        if status:
            q = q.where(Review.status == status.upper())
        reviews = list(session.exec(q).all())
        # Live-aggregate progress only for reviews without a frozen snapshot
        # (OPEN, or closed before the snapshot column existed); closed reviews
        # serve their frozen progress so a later re-parenting scan can't shrink
        # it.
        live_ids = [r.id for r in reviews if _frozen_snapshot(r) is None]
        progress = _progress_map(session, live_ids)
        latest_change = _latest_vault_change(session)
        out = []
        for r in reviews:
            item = _serialize(r)
            snap = _frozen_snapshot(r)
            if snap is not None:
                item["progress"] = snap["progress"]
            else:
                item["progress"] = progress.get(
                    r.id, {"done": 0, "pending": 0, "skipped": 0, "locked": 0}
                )
            item["stale"] = _is_stale(r, latest_change)
            out.append(item)
        return out

    return vault.db.run_immediate_read_task(_fetch)


def get_review(vault: "Vault", review_id: int) -> dict:
    """One review's detail: scan stats, outcome receipt, progress, staleness,
    and the live ``auto_resolvable`` count.

    Raises:
        KeyError: If no review with that id exists.
    """

    def _fetch(session: Session) -> dict:
        review = session.get(Review, review_id)
        if review is None:
            raise KeyError(f"Review not found: id={review_id}")
        item = _serialize(review)
        item["stale"] = _is_stale(review, _latest_vault_change(session))
        snap = _frozen_snapshot(review)
        if snap is not None:
            # Closed review: serve the frozen receipt/progress so a later scan
            # re-parenting its rows can't change this session's cover sheet.
            item["progress"] = snap["progress"]
            item["receipt"] = snap["receipt"]
        else:
            item["progress"] = _progress_map(session, [review.id])[review.id]
            item["receipt"] = _receipt(session, review.id)
        return item

    item = vault.db.run_immediate_read_task(_fetch)
    result = tag_suggestion_service.bulk_accept(
        vault,
        item["tag"],
        AUTO_RESOLVE_MIN_COMBINED,
        dry_run=True,
        review_id=review_id,
    )
    item["stats"]["auto_resolvable"] = int(result.get("count", 0))
    return item


def refresh_review(
    vault: "Vault",
    review_id: int,
) -> dict:
    """Re-run the review's scan append-only (same tag, same frozen scope).

    Only inserts suspects not already in the review; the review's decided rows
    are never resurrected (see :func:`tag_scan_service.scan_tag`). Updates
    ``refreshed_at``, ``scanned``, ``found`` and ``prev_reviewed``.

    Returns ``{"new_count", "found", "refreshed_at"}``.

    Raises:
        KeyError: If no review with that id exists.
        ReviewConflictError: If the review is not OPEN.
    """
    review = vault.db.run_immediate_read_task(lambda s: s.get(Review, review_id))
    if review is None:
        raise KeyError(f"Review not found: id={review_id}")
    if review.status != OPEN:
        raise ReviewConflictError(
            f"Review {review_id} is {review.status}; only OPEN reviews can refresh"
        )

    scope_ids = _resolve_scope_ids(
        vault,
        project_id=review.project_id,
        set_id=review.set_id,
        character_id=review.character_id,
    )
    scan = tag_scan_service.scan_tag(
        vault,
        review.tag,
        project=None,
        picture_ids=scope_ids,
        review_id=review_id,
        include_reviewed=False,
    )

    def _update(session: Session) -> dict:
        row = session.get(Review, review_id)
        row.refreshed_at = datetime.utcnow()
        row.scanned = scan["scanned"]
        row.prev_reviewed = scan["prev_reviewed"]
        # found = everything currently in the review's queue (all statuses).
        total = session.exec(
            select(func.count())
            .select_from(TagSuggestion)
            .where(TagSuggestion.review_id == review_id)
        ).one()
        row.found = int(total)
        session.commit()
        return {
            "new_count": scan["new"],
            "found": row.found,
            "refreshed_at": row.refreshed_at.isoformat(),
        }

    return vault.db.run_task(_update)


def set_review_status(vault: "Vault", review_id: int, status: str) -> dict:
    """Archive or abort a review. Idempotent for the target status.

    Suggestion rows are left untouched in both cases: per-item decisions were
    written through as they were made, and PENDING rows simply stay parented
    to the closed review as its record.

    Raises:
        KeyError: If no review with that id exists.
        ReviewConflictError: If the review is closed in a *different* state.
    """
    if status not in (ARCHIVED, ABORTED):
        raise ValueError(f"Invalid target status: {status!r}")

    def _set(session: Session) -> Review:
        review = session.get(Review, review_id)
        if review is None:
            raise KeyError(f"Review not found: id={review_id}")
        if review.status == status:
            return review  # idempotent
        if review.status != OPEN:
            raise ReviewConflictError(
                f"Review {review_id} is {review.status}; cannot set {status}"
            )
        review.status = status
        # Freeze the receipt/progress on close: both aggregate LIVE over this
        # review's suggestion rows, so a later scan that re-parents those rows
        # into a new review would otherwise shrink this closed session's
        # historical cover sheet. Once frozen, get_review/list_reviews serve the
        # snapshot for this review instead of re-aggregating.
        review.receipt_snapshot = json.dumps(
            {
                "receipt": _receipt(session, review.id),
                "progress": _progress_map(session, [review.id])[review.id],
            }
        )
        session.commit()
        session.refresh(review)
        return review

    review = vault.db.run_task(_set)
    return _serialize(review)


def delete_review(vault: "Vault", review_id: int) -> None:
    """Delete one review session by id (any status).

    Removes the :class:`Review` row only. Its :class:`TagSuggestion` rows are
    **not** deleted: ``TagSuggestion.review_id`` is an ``ON DELETE SET NULL`` FK
    (SQLite FK enforcement is on - see ``database.init_database``), so those rows
    survive with ``review_id`` cleared. This is deliberate:

    * A review is an *audit receipt* over per-item decisions that were written
      through to the ``Tag`` rows / human-label ledger as they were made.
      Deleting the session must never resurrect or alter those decisions.
    * The no-resurrection guarantee is keyed on the suggestion row's ``status``,
      not on ``review_id`` (see :func:`tag_scan_service.scan_tag`): a DECIDED row
      left with ``review_id = NULL`` is still counted as ``prev_reviewed`` and
      suppressed on the next scan of the tag. Deleting the suggestion rows would
      instead re-surface already-decided suspects - a resurrection. So they are
      detached, not destroyed.

    Deleting an OPEN review the owner is mid-review-of is allowed: the row goes
    away (freeing the tag for a new OPEN review) and its still-PENDING suggestion
    rows detach to ``review_id = NULL`` with no decision lost.

    Raises:
        KeyError: If no review with that id exists.
    """

    def _delete(session: Session) -> None:
        review = session.get(Review, review_id)
        if review is None:
            raise KeyError(f"Review not found: id={review_id}")
        # No ORM relationship maps Review -> TagSuggestion, so this issues a bare
        # DELETE and the DB-level ON DELETE SET NULL detaches the suggestion rows.
        session.delete(review)
        session.commit()

    vault.db.run_task(_delete)


def clear_reviews(vault: "Vault", status: str) -> int:
    """Delete every review in ``status`` and return how many were deleted.

    Powers the review rail's "clear all archived" bulk action. Each deleted
    review's suggestion rows are detached (``review_id`` set NULL), never
    destroyed - identical semantics to :func:`delete_review`, so the decision
    audit and the no-resurrection guarantee are preserved.

    Raises:
        ValueError: If ``status`` is not a member of :data:`VALID_STATUSES`.
    """
    normalized = (status or "").upper()
    if normalized not in VALID_STATUSES:
        raise ValueError(f"Invalid status for bulk delete: {status!r}")

    def _delete(session: Session) -> int:
        reviews = list(
            session.exec(select(Review).where(Review.status == normalized)).all()
        )
        for review in reviews:
            session.delete(review)
        session.commit()
        return len(reviews)

    return vault.db.run_task(_delete)


def derive_kind(
    suspect: tuple[int | None, str | None] | None,
    twin: tuple[int | None, str | None] | None,
    *,
    max_twin_hamming: int = DEFAULT_MAX_TWIN_HAMMING,
) -> str:
    """``"pair"`` when suspect and twin are versions of one shot, else ``"binary"``.

    Versions of one shot = same :class:`PictureStack` (equal non-null
    ``stack_id``) or dhash Hamming distance within ``max_twin_hamming`` bits.
    Derived at read time (not stored) so legacy and re-parented rows are
    classified uniformly; each argument is ``(stack_id, perceptual_hash)``.
    """
    if suspect is None or twin is None:
        return "binary"
    s_stack, s_hash = suspect
    t_stack, t_hash = twin
    if s_stack is not None and s_stack == t_stack:
        return "pair"
    if s_hash and t_hash:
        try:
            if hamming_distance(int(s_hash, 16), int(t_hash, 16)) <= max_twin_hamming:
                return "pair"
        except (ValueError, TypeError) as exc:
            # A malformed/non-hex perceptual hash can't be compared; fall back to
            # "binary" instead of silently swallowing it (no-silent-pass rule).
            logger.debug(
                "derive_kind: unparseable perceptual hash (suspect=%r, twin=%r): "
                "%s; classifying pair as binary",
                s_hash,
                t_hash,
                exc,
            )
    return "binary"


def list_review_suggestions(
    vault: "Vault",
    review_id: int,
    *,
    status: str = "PENDING",
    limit: int = 100,
    offset: int = 0,
    picture_ids: set[int] | None = None,
) -> list[dict]:
    """The review's ranked queue, enriched for the card UI.

    Each item carries the suggestion fields plus ``kind`` ("pair"/"binary",
    derived - see :func:`derive_kind`), ``neighbors`` (the scan-time evidence
    JSON parsed to a list of ``{"picture_id", "has"}``), file extensions, and
    the tagger's confidences for suspect and twin.

    Args:
        vault: Application vault, used for DB task dispatch.
        review_id: The review whose queue to list.
        status: Status filter (default ``PENDING``); pass ``""`` for all.
        limit, offset: Paging.
        picture_ids: Optional token-scope restriction on the suspect picture id
            (never the twin); ``None`` = unrestricted.

    Raises:
        KeyError: If no review with that id exists.
    """

    def _fetch(session: Session) -> list[TagSuggestion]:
        if session.get(Review, review_id) is None:
            raise KeyError(f"Review not found: id={review_id}")
        # Join Picture and exclude soft-deleted suspects: a deleted picture's
        # card must never be listed in the review queue.
        #
        # Locked suspects are withheld for the same reason, but the lock needs
        # its OWN read-time filter rather than relying on the scan: a set can be
        # locked at any time *after* the review was created, and the scan only
        # ever runs at create/refresh. Rows scanned while the set was still
        # unlocked would otherwise be served forever as cards whose every action
        # 423s - the reported "locked images still show up in the review". The
        # filter is applied before OFFSET/LIMIT so paging stays correct.
        #
        # Restricted to PENDING rows deliberately, matching the `locked` bucket
        # in _progress_map exactly. A row DECIDED before the lock is no longer
        # actionable work - it is this review's audit record, still counted in
        # progress.done - so hiding it would make the served list disagree with
        # the receipt. Only undecided work is withheld.
        q = (
            select(TagSuggestion)
            .join(Picture, Picture.id == TagSuggestion.picture_id)
            .where(
                TagSuggestion.review_id == review_id,
                Picture.deleted.is_(False),
                or_(
                    TagSuggestion.status != "PENDING",
                    TagSuggestion.picture_id.notin_(locked_picture_id_subquery()),
                ),
            )
        )
        if status:
            q = q.where(TagSuggestion.status == status.upper())
        if picture_ids is not None:
            q = q.where(TagSuggestion.picture_id.in_(picture_ids))
        q = (
            q.order_by(TagSuggestion.score.desc(), TagSuggestion.twin_sim.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(session.exec(q).all())

    suggestions = vault.db.run_immediate_read_task(_fetch)

    ids: list[int | None] = []
    pairs: list[tuple[int, str]] = []
    for s in suggestions:
        ids.append(s.picture_id)
        ids.append(s.twin_picture_id)
        pairs.append((s.picture_id, s.tag))
        if s.twin_picture_id is not None:
            pairs.append((s.twin_picture_id, s.tag))
    exts = tag_suggestion_service.get_picture_exts(vault, ids)
    confs = tag_suggestion_service.get_tagger_confidences(vault, pairs)

    wanted = sorted({i for i in ids if i is not None})

    def _fetch_card_context(session: Session) -> tuple[dict, dict]:
        """Kind inputs + lock state for every pictured id, in one session.

        Batched together (and the lock lookup batched across all rows) so
        labelling N cards stays a fixed number of queries rather than an N+1.
        """
        if not wanted:
            return {}, {}
        card_scope = scope_id_subquery(
            session, wanted, name="_pixlstash_review_card_picture_ids"
        )
        rows = session.exec(
            select(Picture.id, Picture.stack_id, Picture.perceptual_hash).where(
                Picture.id.in_(card_scope)
            )
        ).all()
        return (
            {pid: (stack_id, phash) for pid, stack_id, phash in rows},
            locked_sets_for_pictures(session, wanted),
        )

    kind_info, locked_sets = vault.db.run_immediate_read_task(_fetch_card_context)

    out = []
    for s in suggestions:
        neighbors = None
        if s.neighbors:
            try:
                neighbors = json.loads(s.neighbors)
            except (ValueError, TypeError):
                logger.warning(
                    "list_review_suggestions: unparseable neighbors JSON on "
                    "suggestion %s; returning null",
                    s.id,
                )
        # Per-side lock state. The scan keeps a frozen picture in the embedding
        # pool as a twin/neighbour (it guides the vote and writes nothing), so a
        # served card can have a LOCKED TWIN even though its suspect is free.
        # A card-level boolean cannot express that, because the two sides gate
        # different actions: the twin's lock blocks fix-twin and swap, while
        # accept and dismiss only ever write the suspect.
        suspect_locked_sets = locked_sets.get(s.picture_id, [])
        twin_locked_sets = (
            locked_sets.get(s.twin_picture_id, []) if s.twin_picture_id else []
        )
        kind = derive_kind(
            kind_info.get(s.picture_id),
            kind_info.get(s.twin_picture_id) if s.twin_picture_id is not None else None,
        )
        if twin_locked_sets:
            # Degrade to a binary card on the suspect. A pair card offers four
            # corners, but with a frozen twin two of them (swap, and whichever of
            # both/neither maps to fix-twin) can only 423 - while the other two
            # map to accept and dismiss, which are EXACTLY the actions a binary
            # card offers. So the degradation costs no reachable decision and
            # removes two dead corners. The row is still served rather than
            # dropped: the suspect's disagreement signal is real and fully
            # reviewable, so dropping it would discard legitimate work.
            kind = "binary"
        out.append(
            {
                "id": s.id,
                "picture_id": s.picture_id,
                "tag": s.tag,
                "direction": s.direction,
                "source": s.source,
                "score": s.score,
                "reason": s.reason,
                "twin_picture_id": s.twin_picture_id,
                "twin_sim": s.twin_sim,
                "model_version": s.model_version,
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "review_id": s.review_id,
                "kind": kind,
                # Per-side lock state (see above). ``locked`` is the suspect's:
                # today the queue filter means it is always False, but it is
                # emitted so a future selection gap surfaces as a labelled card
                # instead of an un-actionable one.
                "locked": bool(suspect_locked_sets),
                "locked_sets": suspect_locked_sets,
                "twin_locked": bool(twin_locked_sets),
                "twin_locked_sets": twin_locked_sets,
                "neighbors": neighbors,
                "picture_ext": exts.get(s.picture_id, ""),
                "twin_ext": exts.get(s.twin_picture_id, ""),
                # The suspect's / twin's tagger confidence for this tag. Named to
                # match the frontend card contract (item.confidence / twin_confidence).
                "confidence": confs.get((s.picture_id, s.tag)),
                "twin_confidence": confs.get((s.twin_picture_id, s.tag)),
            }
        )
    return out
