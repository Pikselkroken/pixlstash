"""Marking already-tagged pictures for a quality-crop re-check (#1648).

The route (``POST /taggers/pixlstash_tagger/quality-crop/recheck``) reaches this
through ``Vault.request_quality_crop_recheck``; ``QualityCropRecheckFinder``
then hands the marked pictures to ``QualityCropRecheckTask``.
"""

from sqlalchemy import func
from sqlmodel import Session, select, update

from pixlstash.db_models import (
    Picture,
    Tag,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import locked_picture_id_subquery

logger = get_logger(__name__)


def _recheckable_clauses() -> tuple:
    """The pictures a re-check may mark: tagged, live, and not frozen.

    "Tagged" is "carries no retag sentinel": a picture awaiting its first tag,
    or a retag, has its crop run by that ``TagTask``. A picture with a file but
    no tags at all was tagged and found nothing, and is re-checked like any
    other.
    """
    pending_tag = select(Tag.picture_id).where(
        Tag.tag.like(TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR)
    )
    return (
        Picture.deleted.is_(False),
        Picture.file_path.is_not(None),
        ~Picture.id.in_(pending_tag),
        ~Picture.id.in_(locked_picture_id_subquery()),
    )


def mark_quality_crop_recheck_in_session(session: Session) -> int:
    """Flag every re-checkable picture as pending and return how many there are.

    Idempotent: a second call re-marks the same pictures (and any tagged
    since), and the count is of pictures now pending, not of flags that
    changed. Commits.

    Args:
        session: The writer session.

    Returns:
        The number of pictures marked.
    """
    clauses = _recheckable_clauses()
    queued = session.exec(
        select(func.count()).select_from(Picture).where(*clauses)
    ).one()
    if isinstance(queued, (tuple, list)):
        queued = queued[0]
    session.exec(
        update(Picture)
        .where(*clauses)
        .values(quality_crop_pending=True)
        # A flag no loaded Picture instance needs to see; skips the ORM's
        # re-select of every matched row.
        .execution_options(synchronize_session=False)
    )
    session.commit()
    logger.info("Quality crop re-check requested for %d picture(s).", int(queued or 0))
    return int(queued or 0)


def count_pending_quality_crop_rechecks(session: Session) -> int:
    """Pictures still waiting for their crop re-check, for the tasks panel.

    A locked picture keeps its flag (the task skips it, and resumes once the set
    is unlocked) but is not counted, so the panel drains to zero rather than
    showing work that will not run.
    """
    result = session.exec(
        select(func.count())
        .select_from(Picture)
        .where(
            Picture.quality_crop_pending.is_(True),
            Picture.deleted.is_(False),
            ~Picture.id.in_(locked_picture_id_subquery()),
        )
    ).one()
    if isinstance(result, (tuple, list)):
        return int(result[0] or 0)
    return int(result or 0)
