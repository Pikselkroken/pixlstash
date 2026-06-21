"""Bulk-clear the wrong tags surfaced by the live "Impossible tags" grid filters.

The human reviews the filtered grid, multi-selects the genuinely-wrong pictures, and
clears. For each selected picture this removes exactly the tags the active filters imply
(see :func:`pixlstash.utils.service.person_tags.tags_to_clear`) and records a human
**NEG** per ``(picture, tag)`` — like the suggestion-accept path, so a deliberate cleanup
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
from pixlstash.utils.service.label_ledger import (
    NEG,
    clear_human_label,
    record_human_label,
)
from pixlstash.utils.service.person_tags import tags_to_clear
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# SQLite caps bound variables per statement (~999); a multi-select is usually small, but
# chunk the id lists to stay safe.
_ID_CHUNK = 900

# Filter kinds the clear understands (mirrors the live predicate in PredicateFilter).
# "object" is the description-driven signal (face-independent), cleared the same way.
VALID_FILTERS = ("no_face", "no_humans", "object")


def _chunks(seq: list, size: int = _ID_CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def clear_in_session(
    session: Session, picture_ids: list[int], filters: list[str]
) -> list[tuple[int, str]]:
    """Remove the filter-implied tags from each picture; record a NEG per removed tag.

    Returns the removed ``(picture_id, tag)`` pairs (for the undo / a toast). Commits.
    """
    if not picture_ids or not filters:
        return []
    removed: list[tuple[int, str]] = []
    for chunk in _chunks(list(picture_ids)):
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
    session.commit()
    return removed


def restore_in_session(session: Session, pairs: list[tuple[int, str]]) -> list[int]:
    """Re-add removed tags and clear their ledger entries (undo). Returns touched pids."""
    touched: set[int] = set()
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
    """Vault wrapper for :func:`clear_in_session`. Returns the removed pairs + count."""
    removed = vault.db.run_task(clear_in_session, list(picture_ids), list(filters))
    logger.info(
        "Impossible-tag clear: removed %d tags across %d pictures (filters=%s)",
        len(removed),
        len({p for p, _t in removed}),
        filters,
    )
    return {
        "removed": [{"picture_id": p, "tag": t} for p, t in removed],
        "count": len(removed),
    }


def restore_cleared_tags(vault: "Vault", pairs: list[tuple[int, str]]) -> dict:
    """Vault wrapper for :func:`restore_in_session` (undo)."""
    touched = vault.db.run_task(restore_in_session, list(pairs))
    return {"restored": len(pairs), "picture_ids": touched}
