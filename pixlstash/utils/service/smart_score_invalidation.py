"""Invalidate ``Picture.smart_score`` when a picture's anomaly-tag state changes.

``Picture.smart_score`` is a cached derived value: :class:`SmartScoreTask` only ever
picks up pictures whose ``smart_score`` is ``NULL``
(:meth:`~pixlstash.tasks.smart_score_task.SmartScoreTask.find_pictures_missing_smart_score`),
so a score that is already stored is never recomputed. One of its inputs — the
calibrated anomaly penalty applied in
:func:`pixlstash.utils.quality.anomaly_penalty.anomaly_penalty` — is a live function of
the human/model anomaly labels, which tag edits mutate. Without an explicit
invalidation the stored score silently goes stale after a re-tag or a manual tag edit.

The change signal is taken from :func:`pixlstash.picture_scoring.fetch_anomaly_confidences`
— *the exact function the scorer feeds from* — rather than from a derived summary such as
``Picture.anomaly_tag_uncertainty``. That column is a ``max()`` over per-tag scores and is
therefore lossy: two materially different anomaly states can collapse to the same value
(e.g. moving a 0.8 confidence from ``bad_hands`` to ``blurry`` leaves the max at 0.8 while
changing the penalty, because the penalty is per-tag-family and precision-weighted). Taking
the signature straight from the scorer's own input function makes the comparison faithful by
construction: if the signature is unchanged, the anomaly term of the score cannot have moved.

Scope is deliberately narrow. Only the anomaly/penalised-tag predictions feed the score;
the other inputs (image embedding, quality metrics, aesthetic score, text score) are not
tag-derived. Editing a non-penalised content tag therefore leaves the signature untouched
and the stored score stands — over-invalidating here would re-score the whole library on
every routine re-tag, which is a serious throughput regression on a small box.
"""

from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterable

from sqlalchemy import update

from pixlstash.db_models import Picture
from pixlstash.picture_scoring import fetch_anomaly_confidences
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from sqlmodel import Session

logger = get_logger(__name__)

# SQLite caps bound variables per statement (~999); chunk id lists to stay under it.
_ID_CHUNK = 900

# Confidences are stored as floats. Rounding before comparison stops pure float
# representation noise from counting as a change, while staying far finer than any
# difference the penalty could express.
_CONFIDENCE_PRECISION = 6


def _normalise_ids(picture_ids: Iterable) -> list[int]:
    """Return the distinct, sorted, non-null picture ids from *picture_ids*."""
    return sorted({int(pid) for pid in picture_ids if pid is not None})


def _chunks(seq: list, size: int = _ID_CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def anomaly_state_signature(session: "Session", picture_ids: Iterable) -> dict:
    """Return ``{picture_id: signature}`` capturing the scorer's anomaly inputs.

    The signature is a canonical, order-independent, hashable rendering of exactly the
    two values :func:`pixlstash.picture_scoring.attach_anomaly_inputs` hands the scorer:
    the per-tag anomaly probability map (with human POS/NEG already folded in) and the
    set of human-verified present tags.

    Args:
        session: Active DB session. Callers must ``flush()`` any pending mutation first
            so the read observes it.
        picture_ids: Picture ids to snapshot.

    Returns:
        Mapping of picture id to its comparable anomaly-state signature.
    """
    ids = _normalise_ids(picture_ids)
    if not ids:
        return {}
    signatures: dict[int, tuple] = {}
    # Chunked so a large batch stays under SQLite's bound-variable cap.
    for chunk in _chunks(ids):
        probs_map, human_map = fetch_anomaly_confidences(session, chunk)
        for pid in chunk:
            probs = probs_map.get(pid) or {}
            human = human_map.get(pid) or set()
            signatures[pid] = (
                tuple(
                    sorted(
                        (tag, round(float(prob), _CONFIDENCE_PRECISION))
                        for tag, prob in probs.items()
                    )
                ),
                tuple(sorted(human)),
            )
    return signatures


def invalidate_smart_scores(session: "Session", picture_ids: Iterable) -> int:
    """NULL ``Picture.smart_score`` for *picture_ids* so the finder re-picks them.

    Issues one bulk Core UPDATE per id chunk rather than a statement per picture: the
    tagger and the impossible-tag clear both operate on whole batches, and a
    write-per-row there would saturate the single writer queue.

    Does **not** commit — the caller owns the transaction, so the invalidation lands
    atomically with the tag mutation that caused it.

    Args:
        session: Active DB session.
        picture_ids: Pictures whose cached score is now stale.

    Returns:
        Number of rows actually cleared (rows already ``NULL`` are not counted).
    """
    ids = _normalise_ids(picture_ids)
    if not ids:
        return 0
    cleared = 0
    for chunk in _chunks(ids):
        result = session.exec(
            update(Picture)
            .where(Picture.id.in_(chunk), Picture.smart_score.is_not(None))
            .values(smart_score=None)
        )
        cleared += result.rowcount or 0
    return cleared


def invalidate_changed_anomaly_scores(
    session: "Session", picture_ids: Iterable, before: dict, *, context: str
) -> int:
    """Clear the cached score of every picture whose anomaly signature moved since *before*.

    Re-snapshots *picture_ids* and compares against the ``before`` signature map from
    :func:`anomaly_state_signature`, then bulk-NULLs the stale scores. Does not commit.

    Use this directly when the mutation is too spread out to wrap in
    :func:`invalidate_on_anomaly_change`; otherwise prefer the context manager.

    Args:
        session: Active DB session.
        picture_ids: Pictures that were snapshotted before the mutation.
        before: Signature map captured before the mutation.
        context: Short description of the mutation, for the log line.

    Returns:
        Number of cached scores cleared.
    """
    ids = _normalise_ids(picture_ids)
    if not ids:
        return 0
    # Make any pending mutation visible to the re-read.
    session.flush()
    after = anomaly_state_signature(session, ids)
    changed = [pid for pid in ids if before.get(pid) != after.get(pid)]
    if not changed:
        logger.debug(
            "Smart-score invalidation (%s): anomaly state unchanged for %d picture(s), "
            "cached scores kept",
            context,
            len(ids),
        )
        return 0
    cleared = invalidate_smart_scores(session, changed)
    logger.info(
        "Smart-score invalidation (%s): anomaly state changed for %d of %d picture(s), "
        "cleared %d cached score(s) for recompute",
        context,
        len(changed),
        len(ids),
        cleared,
    )
    return cleared


@contextmanager
def invalidate_on_anomaly_change(
    session: "Session", picture_ids: Iterable, *, context: str
):
    """Clear the cached smart score of any picture whose anomaly state the block changed.

    Snapshots the scorer's anomaly inputs for *picture_ids*, runs the wrapped mutation,
    re-snapshots, and NULLs ``Picture.smart_score`` for the pictures whose signature
    moved. Pictures whose anomaly state is untouched — the common case for a content-tag
    edit — keep their stored score.

    The caller must commit after the block so the invalidation and the mutation share a
    transaction.

    Args:
        session: Active DB session.
        picture_ids: Pictures the wrapped block may mutate.
        context: Short description of the mutation, for the log line.
    """
    ids = _normalise_ids(picture_ids)
    if not ids:
        yield
        return
    before = anomaly_state_signature(session, ids)
    yield
    invalidate_changed_anomaly_scores(session, ids, before, context=context)
