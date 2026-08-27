"""Bulk-clear the wrong tags surfaced by the live "Impossible tags" grid filters.

The human reviews the filtered grid, multi-selects the genuinely-wrong pictures, and
clears. For each selected picture this removes exactly the tags the active filters imply
(see :func:`pixlstash.utils.service.person_tags.tags_to_clear`) and records a human
**NEG** per ``(picture, tag)`` - like the suggestion-accept path, so a deliberate cleanup
is durable training signal, not a silent delete. The NEG is recorded *unconditionally*
(not gated to the anomaly vocabulary, unlike the manual tag-panel remove): the whole point
here is to capture reviewed person-tag negatives for a future person-tagger.

``restore_cleared_tags`` is the undo: re-add the removed tags and clear their ledger
entries, symmetric with a suggestion reopen.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from pixlstash.db_models import Face, Picture, Tag
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import locked_picture_ids
from pixlstash.utils.service.label_ledger import (
    NEG,
    clear_human_label,
    record_human_label,
)
from pixlstash.utils.service.person_tags import tags_to_clear
from pixlstash.utils.service.smart_score_invalidation import (
    invalidate_on_anomaly_change,
)
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)
from pixlstash.utils.sql_chunking import chunked

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


# Filter kinds the clear understands (mirrors the live predicate in PredicateFilter).
# "object" is the description-driven signal (face-independent), cleared the same way.
VALID_FILTERS = ("no_face", "no_humans", "object")


def clear_in_session(
    session: Session, picture_ids: list[int], filters: list[str]
) -> list[tuple[int, str]]:
    """Remove the filter-implied tags from each picture; record a NEG per removed tag.

    Returns the removed ``(picture_id, tag)`` pairs (for the undo / a toast). Commits.
    """
    if not picture_ids or not filters:
        return []
    # Pictures frozen by a locked set are read-only: drop them from the clear so a
    # bulk cleanup never mutates a frozen set's labels (the rest still clear).
    locked = locked_picture_ids(session, picture_ids)
    if locked:
        logger.info(
            "Impossible-tag clear: skipping %d locked picture(s) %s",
            len(locked),
            sorted(locked),
        )
        picture_ids = [pid for pid in picture_ids if pid not in locked]
    if not picture_ids:
        return []
    removed: list[tuple[int, str]] = []
    # One snapshot/compare over the whole selection so the stale cached smart scores
    # are cleared in a single bulk UPDATE rather than one write per picture.
    with invalidate_on_anomaly_change(
        session, picture_ids, context="impossible-tag clear"
    ):
        removed = _clear_tags_in_session(session, picture_ids, filters)
    session.commit()
    return removed


def _clear_tags_in_session(
    session: Session, picture_ids: list[int], filters: list[str]
) -> list[tuple[int, str]]:
    """Remove the filter-implied tags and record a NEG per removed tag. No commit."""
    removed: list[tuple[int, str]] = []
    for chunk in chunked(list(picture_ids)):
        faced = set(
            session.exec(
                select(Face.picture_id).where(
                    Face.picture_id.in_(chunk), Face.face_index != -1
                )
            ).all()
        )
        tags_by_pic: dict[int, list[Tag]] = defaultdict(list)
        for tag_row in session.exec(select(Tag).where(Tag.picture_id.in_(chunk))).all():
            tags_by_pic[tag_row.picture_id].append(tag_row)
        # Captions for the description-driven "object" filter (face-independent signal).
        desc_by_pic: dict[int, str | None] = dict(
            session.exec(
                select(Picture.id, Picture.description).where(Picture.id.in_(chunk))
            ).all()
        )

        for pid in chunk:
            rows = tags_by_pic.get(pid, [])
            strip = tags_to_clear(
                filters,
                [r.tag for r in rows],
                has_real_face=pid in faced,
                description=desc_by_pic.get(pid),
            )
            if not strip:
                continue
            for row in rows:
                if row.tag in strip:
                    # Record the human NEG before the delete so the reviewed negative
                    # outlives the lost Tag row (mirrors the suggestion-accept path).
                    record_human_label(session, pid, row.tag, NEG)
                    session.delete(row)
                    removed.append((pid, row.tag))
            session.flush()
            recompute_anomaly_tag_uncertainty(session, pid)
    return removed


def restore_in_session(session: Session, pairs: list[tuple[int, str]]) -> list[int]:
    """Re-add removed tags and clear their ledger entries (undo). Returns touched pids."""
    # Skip any picture that has since become frozen by a locked set - restoring a
    # tag onto it would mutate the frozen set's labels.
    locked = locked_picture_ids(session, [pid for pid, _tag in pairs])
    if locked:
        logger.info(
            "Impossible-tag restore: skipping %d locked picture(s) %s",
            len(locked),
            sorted(locked),
        )
        pairs = [(pid, tag) for pid, tag in pairs if pid not in locked]
    touched: set[int] = set()
    # Clearing the ledger entries reverses the human NEGs the clear recorded, which
    # moves the scorer's anomaly inputs back - invalidate in one bulk UPDATE.
    with invalidate_on_anomaly_change(
        session, [pid for pid, _tag in pairs], context="impossible-tag restore"
    ):
        for pid, tag in pairs:
            existing = session.exec(
                select(Tag).where(Tag.picture_id == pid, Tag.tag == tag)
            ).first()
            if existing is None:
                session.add(Tag(picture_id=pid, tag=tag))
            clear_human_label(session, pid, tag)
            touched.add(pid)
        for pid in touched:
            session.flush()
            recompute_anomaly_tag_uncertainty(session, pid)
    session.commit()
    return sorted(touched)


def clear_impossible_tags(
    vault: "Vault", picture_ids: list[int], filters: list[str]
) -> dict:
    """Vault wrapper for :func:`clear_in_session`. Returns the removed pairs, count,
    and any ids skipped because a locked set freezes them."""

    def _run(session, ids, active_filters):
        skipped = sorted(locked_picture_ids(session, ids))
        removed = clear_in_session(session, ids, active_filters)
        return removed, skipped

    removed, skipped_locked = vault.db.run_task(_run, list(picture_ids), list(filters))
    logger.info(
        "Impossible-tag clear: removed %d tags across %d pictures (filters=%s), "
        "skipped %d locked",
        len(removed),
        len({p for p, _t in removed}),
        filters,
        len(skipped_locked),
    )
    return {
        "removed": [{"picture_id": p, "tag": t} for p, t in removed],
        "count": len(removed),
        "skipped_locked": skipped_locked,
    }


def restore_cleared_tags(vault: "Vault", pairs: list[tuple[int, str]]) -> dict:
    """Vault wrapper for :func:`restore_in_session` (undo).

    ``restored`` counts the pairs actually re-added (locked pictures are skipped),
    and ``skipped_locked`` lists any picture ids frozen by a locked set."""

    def _run(session, restore_pairs):
        skipped_set = locked_picture_ids(session, [p for p, _t in restore_pairs])
        touched = restore_in_session(session, restore_pairs)
        restored_pairs = [p for p, _t in restore_pairs if p not in skipped_set]
        return touched, sorted(skipped_set), len(restored_pairs)

    touched, skipped_locked, restored_count = vault.db.run_task(_run, list(pairs))
    return {
        "restored": restored_count,
        "picture_ids": touched,
        "skipped_locked": skipped_locked,
    }
