"""Service layer for review sessions — one tag + a frozen scope + one scan's results.

A :class:`~pixlstash.db_models.review.Review` is the first-class noun of the tag
review workflow (see ``docs/reviews/2026-07-review-sessions-redesign-draft.md``):
created explicitly, scanned once at creation, refreshed append-only, and finally
archived or aborted. Per-item decisions (accept/dismiss/fix-twin/swap/reopen)
stay in :mod:`pixlstash.services.tag_suggestion_service` and are written through
immediately — archiving/aborting a review never touches suggestion rows.

Mirrors the vault-task conventions of the sibling services (all DB access via
``vault.db.run_task`` / ``run_immediate_read_task``).
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import Picture, Review, TaggerRun
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.pixl_logging import get_logger
from pixlstash.services import tag_scan_service, tag_suggestion_service
from pixlstash.services.tag_scan_service import DEFAULT_MAX_TWIN_HAMMING
from pixlstash.utils.near_neighbor import hamming_distance
from pixlstash.utils.service.filter_helpers import (
    fetch_tag_review_scope_picture_ids,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# min_combined threshold used for the creation receipt's "N obvious pairs —
# auto-resolve?" count; matches the bulk-accept endpoint's default.
AUTO_RESOLVE_MIN_COMBINED = 0.9

OPEN = "OPEN"
ARCHIVED = "ARCHIVED"
ABORTED = "ABORTED"
VALID_STATUSES = (OPEN, ARCHIVED, ABORTED)


class ReviewConflictError(Exception):
    """Raised when the requested transition/creation conflicts with review state."""


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
    token_scope_ids: set[int] | None,
) -> set[int] | None:
    """Intersect the review's frozen scope filters with the caller's token scope.

    ``None`` means unrestricted (owner token, no filters). An empty set is a
    valid "nothing in scope" result. The token scope can only ever narrow,
    never widen — same contract as the tag-suggestions routes.
    """
    filter_ids: set[int] | None = None
    if project_id is not None or set_id is not None or character_id:

        def _fetch(session: Session) -> set[int] | None:
            return fetch_tag_review_scope_picture_ids(
                session,
                project_id=project_id,
                set_id=set_id,
                character_id=character_id,
            )

        filter_ids = vault.db.run_immediate_read_task(_fetch)

    if token_scope_ids is None:
        return filter_ids
    if filter_ids is None:
        return token_scope_ids
    return token_scope_ids & filter_ids


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
    token_scope_ids: set[int] | None = None,
) -> dict:
    """Create a review for ``tag``, run its scan, and return the receipt.

    The scope (project/set/character) is frozen onto the review row; the scan
    is restricted to the resolved scope picture ids intersected with the
    caller's token scope. At most one OPEN review may exist per tag (enforced
    both here and by the partial unique index).

    Returns the serialized review incl. ``stats`` (scanned/found/prev_reviewed/
    auto_resolvable).

    Raises:
        ReviewConflictError: An OPEN review for the tag already exists.
    """

    def _create(session: Session) -> Review:
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
        token_scope_ids=token_scope_ids,
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
    token_scope_ids: set[int] | None = None,
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
        token_scope_ids=token_scope_ids,
    )

    def _fetch(session: Session) -> dict:
        in_scope_q = (
            select(func.count()).select_from(Picture).where(Picture.deleted.is_(False))
        )
        if scope_ids is not None:
            in_scope_q = in_scope_q.where(Picture.id.in_(scope_ids))
        prev_q = (
            select(func.count())
            .select_from(TagSuggestion)
            .where(
                TagSuggestion.tag == tag,
                TagSuggestion.source == tag_scan_service.SOURCE,
                TagSuggestion.status != "PENDING",
            )
        )
        if scope_ids is not None:
            prev_q = prev_q.where(TagSuggestion.picture_id.in_(scope_ids))
        return {
            "in_scope": int(session.exec(in_scope_q).one()),
            "prev_reviewed": int(session.exec(prev_q).one()),
        }

    return vault.db.run_immediate_read_task(_fetch)


def _progress_map(session: Session, review_ids: list[int]) -> dict[int, dict]:
    """``review_id -> {"done", "pending", "skipped"}`` over the suggestion rows.

    ``done`` counts decided rows only; SKIPPED rows carry no decision and are
    reported separately. A review is complete when ``pending`` reaches zero.
    """
    progress = {rid: {"done": 0, "pending": 0, "skipped": 0} for rid in review_ids}
    if not review_ids:
        return progress
    rows = session.exec(
        select(
            TagSuggestion.review_id,
            TagSuggestion.status,
            func.count().label("n"),
        )
        .where(TagSuggestion.review_id.in_(review_ids))
        .group_by(TagSuggestion.review_id, TagSuggestion.status)
    ).all()
    for rid, status, n in rows:
        if status == "PENDING":
            bucket = "pending"
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
    review's non-PENDING suggestion rows, ``pending``) and ``stale`` — True
    when the vault changed (new pictures or a tagger run) after the review's
    last scan.
    """

    def _fetch(session: Session) -> list[dict]:
        q = select(Review).order_by(Review.created_at.desc(), Review.id.desc())
        if status:
            q = q.where(Review.status == status.upper())
        reviews = list(session.exec(q).all())
        progress = _progress_map(session, [r.id for r in reviews])
        latest_change = _latest_vault_change(session)
        out = []
        for r in reviews:
            item = _serialize(r)
            item["progress"] = progress.get(r.id, {"done": 0, "pending": 0})
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
        item["progress"] = _progress_map(session, [review.id])[review.id]
        item["stale"] = _is_stale(review, _latest_vault_change(session))
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
    *,
    token_scope_ids: set[int] | None = None,
) -> dict:
    """Re-run the review's scan append-only (same tag, same frozen scope).

    Only inserts suspects not already in the review; the review's decided rows
    are never resurrected (see :func:`tag_scan_service.scan_tag`). Updates
    ``refreshed_at``, ``scanned`` and ``found``.

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
        token_scope_ids=token_scope_ids,
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
        session.commit()
        session.refresh(review)
        return review

    review = vault.db.run_task(_set)
    return _serialize(review)


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
        except (ValueError, TypeError):
            pass
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
    derived — see :func:`derive_kind`), ``neighbors`` (the scan-time evidence
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
        q = select(TagSuggestion).where(TagSuggestion.review_id == review_id)
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

    def _fetch_kind_info(session: Session) -> dict[int, tuple[int | None, str | None]]:
        if not wanted:
            return {}
        rows = session.exec(
            select(Picture.id, Picture.stack_id, Picture.perceptual_hash).where(
                Picture.id.in_(wanted)
            )
        ).all()
        return {pid: (stack_id, phash) for pid, stack_id, phash in rows}

    kind_info = vault.db.run_immediate_read_task(_fetch_kind_info)

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
                "kind": derive_kind(
                    kind_info.get(s.picture_id),
                    kind_info.get(s.twin_picture_id)
                    if s.twin_picture_id is not None
                    else None,
                ),
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
