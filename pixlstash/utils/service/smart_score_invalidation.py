"""Invalidate ``Picture.smart_score`` when a picture's anomaly-tag state changes.

``Picture.smart_score`` is a cached derived value: :class:`SmartScoreTask` only ever
picks up pictures whose ``smart_score`` is ``NULL``
(:meth:`~pixlstash.tasks.smart_score_task.SmartScoreTask.find_pictures_missing_smart_score`),
so a score that is already stored is never recomputed. One of its inputs - the
calibrated anomaly penalty applied in
:func:`pixlstash.utils.quality.anomaly_penalty.anomaly_penalty` - is a live function of
the human/model anomaly labels, which tag edits mutate. Without an explicit
invalidation the stored score silently goes stale after a re-tag or a manual tag edit.

The change signal is taken from :func:`pixlstash.scoring.smart_score.fetch_anomaly_confidences`
- *the exact function the scorer feeds from* - rather than from a derived summary such as
``Picture.anomaly_tag_uncertainty``. That column is a ``max()`` over per-tag scores and is
therefore lossy: two materially different anomaly states can collapse to the same value
(e.g. moving a 0.8 confidence from ``bad_hands`` to ``blurry`` leaves the max at 0.8 while
changing the penalty, because the penalty is per-tag-family and precision-weighted). Taking
the signature straight from the scorer's own input function makes the comparison faithful by
construction: if the signature is unchanged, the anomaly term of the score cannot have moved.

Scope is deliberately narrow. Only the anomaly/penalised-tag predictions feed the score;
the other inputs (image embedding, quality metrics, aesthetic score, text score) are not
tag-derived. Editing a non-penalised content tag therefore leaves the signature untouched
and the stored score stands - over-invalidating here would re-score the whole library on
every routine re-tag, which is a serious throughput regression on a small box.
"""

import json
import threading
from collections import OrderedDict
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterable

from sqlalchemy import func, or_, update
from sqlmodel import select

from pixlstash.db_models import Picture, Tag, TagPrediction
from pixlstash.db_models.tag import DEFAULT_TAG_MERGES
from pixlstash.scoring import fetch_anomaly_confidences
from pixlstash.utils.quality.anomaly_penalty import (
    ANOMALY_FAMILIES,
    ANOMALY_PENALTY_TAGS,
    _family_max_weights,
    normalise_tag_weights,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.sql_chunking import chunked

if TYPE_CHECKING:
    from sqlmodel import Session

logger = get_logger(__name__)


# Confidences are stored as floats. Rounding before comparison stops pure float
# representation noise from counting as a change, while staying far finer than any
# difference the penalty could express.
_CONFIDENCE_PRECISION = 6


class InteractiveRescoreRegistry:
    """Bounded ``picture_id -> origin_client_id`` map bridging a *user* tag edit to the
    background rescore its invalidation triggers.

    ``smart_score`` is recomputed asynchronously: a tag edit only NULLs the cached score,
    and a background :class:`~pixlstash.tasks.smart_score_task.SmartScoreTask` later
    rescores it. The vault normally defers the grid ``CHANGED_PICTURES`` refresh for a
    rescored batch until the *entire* backfill drains
    (``count_remaining() == 0``). Any migration NULL-reset keeps a backfill in flight, so
    an interactive edit made meanwhile would have its card refresh deferred indefinitely.

    This registry lets the invalidation side record "this id was invalidated by a user
    edit from *that* tab", so the completion side can emit an immediate, origin-stamped
    refresh for just those ids - independent of the global drain gate - while bulk
    backfill work still coalesces into the single drain-time emit.

    Thread-safe: :meth:`record` runs on the DB-task thread that performs the invalidation;
    :meth:`consume` runs on the task-completion thread. Bounded to *max_entries*; a record
    that would overflow the cap is **rejected and returned** so the caller can log the
    demotion. A demoted id is not dropped - because it is absent from the registry, the
    completion side simply falls back to the existing drain-time bulk emit for it.
    """

    # A large interactive burst during a full backfill is the worst case. The cap bounds
    # memory while staying far above any plausible count of hand-edited cards in flight.
    MAX_ENTRIES = 4096

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._max_entries = int(max_entries)
        self._lock = threading.Lock()
        # Insertion-ordered so the oldest recordings are easy to reason about; ids are
        # popped on consume, so the map self-evicts as rescores complete.
        self._entries: "OrderedDict[int, str | None]" = OrderedDict()

    def record(
        self, picture_ids: Iterable[int], origin_client_id: str | None
    ) -> list[int]:
        """Mark *picture_ids* as interactively invalidated by *origin_client_id*.

        An id already present has its origin refreshed to the latest editor (the most
        recent edit is the one whose tab should reconcile) without counting against the
        cap again. Returns the ids that could not be recorded because the cap is full, so
        the caller can log their demotion to the bulk refresh path.
        """
        demoted: list[int] = []
        with self._lock:
            for raw in picture_ids:
                if raw is None:
                    continue
                pid = int(raw)
                if pid in self._entries:
                    self._entries[pid] = origin_client_id
                    self._entries.move_to_end(pid)
                    continue
                if len(self._entries) >= self._max_entries:
                    demoted.append(pid)
                    continue
                self._entries[pid] = origin_client_id
        return demoted

    def consume(self, picture_ids: Iterable[int]) -> dict:
        """Remove *picture_ids* found in the registry and group them by origin.

        Returns ``{origin_client_id: [picture_id, ...]}`` for exactly the ids that were
        registered (ids absent from the registry - background-only rescores, or entries
        already consumed - are ignored). Consuming clears the entries so they cannot be
        re-emitted, and so the map self-evicts as rescores land.
        """
        grouped: dict = {}
        with self._lock:
            for raw in picture_ids:
                if raw is None:
                    continue
                pid = int(raw)
                origin = self._entries.pop(pid, _MISSING)
                if origin is _MISSING:
                    continue
                grouped.setdefault(origin, []).append(pid)
        return grouped

    def snapshot(self) -> dict:
        """Return a copy of the current ``{picture_id: origin}`` map (read-only, for tests)."""
        with self._lock:
            return dict(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# Sentinel distinguishing "id absent" from "id present with origin None" in ``consume``.
_MISSING = object()


def _normalise_ids(picture_ids: Iterable) -> list[int]:
    """Return the distinct, sorted, non-null picture ids from *picture_ids*."""
    return sorted({int(pid) for pid in picture_ids if pid is not None})


def anomaly_state_signature(session: "Session", picture_ids: Iterable) -> dict:
    """Return ``{picture_id: signature}`` capturing the scorer's anomaly inputs.

    The signature is a canonical, order-independent, hashable rendering of exactly the
    two values :func:`pixlstash.scoring.smart_score.attach_anomaly_inputs` hands the scorer:
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
    for chunk in chunked(ids):
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

    Does **not** commit - the caller owns the transaction, so the invalidation lands
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
    for chunk in chunked(ids):
        result = session.exec(
            update(Picture)
            .where(Picture.id.in_(chunk), Picture.smart_score.is_not(None))
            .values(smart_score=None)
        )
        cleared += result.rowcount or 0
    return cleared


def invalidate_changed_anomaly_scores(
    session: "Session",
    picture_ids: Iterable,
    before: dict,
    *,
    context: str,
    registry: "InteractiveRescoreRegistry | None" = None,
    origin_client_id: str | None = None,
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
        registry: When the invalidation is driven by an *interactive* user edit, the
            vault's :class:`InteractiveRescoreRegistry`; the changed ids are recorded so
            the background rescore emits an immediate origin-stamped refresh. ``None`` for
            background/settings-driven invalidations, which stay on the bulk drain path.
        origin_client_id: The originating tab's ``X-Client-Id`` to stamp the eventual
            refresh with, recorded alongside each changed id when *registry* is given.

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
    if registry is not None:
        demoted = registry.record(changed, origin_client_id)
        if demoted:
            logger.warning(
                "Smart-score invalidation (%s): interactive rescore registry full "
                "(cap %d); demoted %d picture id(s) to the bulk refresh path - they "
                "will refresh on the next full backfill drain, not immediately.",
                context,
                registry._max_entries,
                len(demoted),
            )
    return cleared


def changed_penalised_tags(before: dict | None, after: dict | None) -> set[str]:
    """Return every tag whose *effective* penalty weight moved between the two tables.

    This is not a plain dict diff. Unweighted family members (``jpeg artifacts``,
    ``film grain``, ``compression artifacts``) inherit their family's ceiling via
    :func:`~pixlstash.utils.quality.anomaly_penalty._tag_weight`, so removing the family's
    only weighted member silently drops those aliases to zero too. Diffing the raw tables
    alone would miss a picture tagged only ``jpeg artifacts`` when the user re-weighted
    ``blocky``, leaving its cached score stale forever. Any family whose ceiling moved
    therefore contributes all of its members, plus their merge children (which are scored
    under the parent's canonical tag but stored under their own name).

    Args:
        before: Resolved ``{tag: weight}`` table before the config edit.
        after: Resolved ``{tag: weight}`` table after the config edit.

    Returns:
        Set of lowercase tag names whose effective weight moved.
    """
    old = normalise_tag_weights(before or {})
    new = normalise_tag_weights(after or {})
    changed = {tag for tag in set(old) | set(new) if old.get(tag) != new.get(tag)}

    old_family_max = _family_max_weights(old)
    new_family_max = _family_max_weights(new)
    for family in ANOMALY_FAMILIES:
        name = family["name"]
        if old_family_max.get(name) != new_family_max.get(name):
            changed.update(family["tags"])
    # Merge children are stored under their own tag but scored as the parent, so they
    # must follow whenever their parent's effective weight moved.
    for child, parent in DEFAULT_TAG_MERGES.items():
        if parent in changed:
            changed.add(child)
    return changed


def invalidate_for_penalised_tag_change(
    session: "Session", changed_tags: Iterable[str]
) -> int:
    """NULL the cached score of every picture carrying one of *changed_tags*.

    Re-weighting a penalised tag only moves the score of pictures that actually carry
    that tag, so invalidating the whole library (the previous behaviour) forced a full
    re-score of every picture on any settings edit - tens of thousands of GPU-backed
    recomputes to fix a few hundred rows.

    "Carrying" spans both label sources the penalty reads: an applied :class:`Tag` row,
    or an anomaly :class:`TagPrediction` row (which is what
    :func:`~pixlstash.scoring.smart_score.fetch_anomaly_confidences` actually feeds the
    scorer, including human POS/NEG decisions). Predictions are matched without a
    confidence gate on purpose: a weight change must invalidate a picture whose
    prediction sits either side of the apply threshold, and over-invalidating a handful
    of rows is far cheaper than missing a stale score.

    Issued as one bulk UPDATE per tag chunk with an ``IN (subquery)``, so no picture ids
    are round-tripped into Python. Does **not** commit - the caller owns the transaction.

    Args:
        session: Active DB session.
        changed_tags: Lowercase tag names whose weight was added/removed/changed.

    Returns:
        Number of cached scores cleared.
    """
    tags = sorted({str(t).strip().lower() for t in changed_tags if t})
    if not tags:
        logger.debug(
            "Smart-score invalidation (penalised-tag config): no tag weights moved; "
            "no cached scores cleared."
        )
        return 0
    cleared = 0
    for chunk in chunked(tags):
        tagged = select(Tag.picture_id).where(func.lower(Tag.tag).in_(chunk))
        predicted = select(TagPrediction.picture_id).where(
            func.lower(TagPrediction.tag).in_(chunk)
        )
        result = session.exec(
            update(Picture)
            .where(
                Picture.smart_score.is_not(None),
                or_(Picture.id.in_(tagged), Picture.id.in_(predicted)),
            )
            .values(smart_score=None)
        )
        cleared += result.rowcount or 0
    logger.info(
        "Smart-score invalidation (penalised-tag config): %d tag weight(s) changed "
        "(%s), cleared %d cached score(s) for recompute.",
        len(tags),
        ", ".join(tags),
        cleared,
    )
    return cleared


def invalidate_all_anomaly_scores(session: "Session", *, context: str) -> int:
    """NULL the cached score of every picture that carries an anomaly prediction.

    The tagger's ``threshold_offset`` moves *two* things at once for every anomaly
    detection: the apply gate in
    :func:`~pixlstash.scoring.smart_score.fetch_anomaly_confidences` (which decides whether a
    model prediction reaches the scorer at all) and the acceptance threshold ``t`` that the
    penalty normalises each detection against via ``u = (p - t) / (1 - t)``. A change to
    the offset therefore invalidates *every* cached score that has an anomaly component,
    regardless of which specific tag is involved - so this is deliberately not scoped by
    tag the way :func:`invalidate_for_penalised_tag_change` is.

    The set is still bounded to *anomaly-bearing* pictures rather than the whole vault:
    the scorer's anomaly inputs come solely from :class:`TagPrediction` rows in the anomaly
    vocabulary (:data:`~pixlstash.utils.quality.anomaly_penalty.ANOMALY_PENALTY_TAGS` -
    exactly what ``fetch_anomaly_confidences`` reads). A picture with no such prediction has
    no anomaly term, so the offset cannot have moved its score, and re-scoring it would be a
    needless GPU-backed recompute. Predictions are matched without a confidence gate on
    purpose: the offset shifts the gate itself, so a prediction sitting either side of the
    old threshold can cross it and must be re-evaluated.

    Issued as one bulk UPDATE per tag chunk with an ``IN (subquery)``, so no picture ids are
    round-tripped into Python. Does **not** commit - the caller owns the transaction.

    Args:
        session: Active DB session.
        context: Short description of the trigger, for the log line.

    Returns:
        Number of cached scores cleared.
    """
    tags = sorted(ANOMALY_PENALTY_TAGS)
    if not tags:
        logger.warning(
            "Smart-score invalidation (%s): the anomaly vocabulary is empty; "
            "no cached scores cleared.",
            context,
        )
        return 0
    cleared = 0
    for chunk in chunked(tags):
        predicted = select(TagPrediction.picture_id).where(
            func.lower(TagPrediction.tag).in_(chunk)
        )
        result = session.exec(
            update(Picture)
            .where(
                Picture.smart_score.is_not(None),
                Picture.id.in_(predicted),
            )
            .values(smart_score=None)
        )
        cleared += result.rowcount or 0
    logger.info(
        "Smart-score invalidation (%s): tagger threshold offset changed, cleared %d "
        "cached anomaly-bearing score(s) for recompute.",
        context,
        cleared,
    )
    return cleared


@contextmanager
def invalidate_on_anomaly_change(
    session: "Session",
    picture_ids: Iterable,
    *,
    context: str,
    registry: "InteractiveRescoreRegistry | None" = None,
    origin_client_id: str | None = None,
):
    """Clear the cached smart score of any picture whose anomaly state the block changed.

    Snapshots the scorer's anomaly inputs for *picture_ids*, runs the wrapped mutation,
    re-snapshots, and NULLs ``Picture.smart_score`` for the pictures whose signature
    moved. Pictures whose anomaly state is untouched - the common case for a content-tag
    edit - keep their stored score.

    The caller must commit after the block so the invalidation and the mutation share a
    transaction.

    Args:
        session: Active DB session.
        picture_ids: Pictures the wrapped block may mutate.
        context: Short description of the mutation, for the log line.
        registry: Pass the vault's :class:`InteractiveRescoreRegistry` when the wrapped
            mutation is an *interactive* user edit, so the background rescore of any
            invalidated picture emits an immediate origin-stamped grid refresh instead of
            waiting for the whole backfill to drain. Leave ``None`` for background paths.
        origin_client_id: The originating tab's ``X-Client-Id``, stamped onto that
            eventual refresh so the initiating tab reconciles the card in place rather
            than raising the "sort order changed" pill.
    """
    ids = _normalise_ids(picture_ids)
    if not ids:
        yield
        return
    before = anomaly_state_signature(session, ids)
    yield
    invalidate_changed_anomaly_scores(
        session,
        ids,
        before,
        context=context,
        registry=registry,
        origin_client_id=origin_client_id,
    )


# ---------------------------------------------------------------------------
# Durable pending invalidations (hub/vault split)
# ---------------------------------------------------------------------------


def invalidate_all_smart_scores(session: "Session") -> int:
    """NULL every cached smart score in this library. Does not commit.

    The blunt instrument, for the one case where a narrow diff is not available:
    the owner's penalty weights changed while this library was closed, and the
    library stores only a keyed hash of those weights, never the weights
    themselves (see
    :func:`pixlstash.services.library_settings_service.reconcile_settings_fingerprint`).
    Knowing *that* something changed without knowing *what* leaves no way to
    scope it.

    Cheaper than it sounds: recomputing a smart score reads tags and quality
    metrics and runs no AI models, and it happens in background batches.

    Returns:
        The number of cached scores cleared.
    """
    result = session.exec(
        update(Picture).where(Picture.smart_score.is_not(None)).values(smart_score=None)
    )
    cleared = int(getattr(result, "rowcount", 0) or 0)
    logger.info("Invalidated %d cached smart score(s) library-wide", cleared)
    return cleared


def record_pending_invalidation(session: "Session", changed_tags: Iterable[str]) -> int:
    """Record that *changed_tags* are owed a score invalidation. Does not commit.

    Written to the vault **before** the setting that caused it is committed to
    the hub, which is what makes the pair safe without a cross-database
    transaction. See
    :class:`~pixlstash.db_models.pending_score_invalidation.PendingScoreInvalidation`
    for why the ordering is that way round.

    Args:
        session: Active vault session. The caller owns the transaction.
        changed_tags: Lowercase tag names whose weight changed.

    Returns:
        The number of tags recorded, or 0 when there was nothing to record.
    """
    from pixlstash.db_models.pending_score_invalidation import PendingScoreInvalidation

    tags = sorted({str(t).strip().lower() for t in changed_tags if t})
    if not tags:
        return 0

    session.add(PendingScoreInvalidation(tags=json.dumps(tags)))
    logger.info(
        "Recorded a pending smart-score invalidation for %d tag(s): %s",
        len(tags),
        ", ".join(tags),
    )
    return len(tags)


def apply_pending_invalidations(session: "Session") -> int:
    """Apply and clear every recorded pending invalidation. Commits.

    Consuming the record and NULLing the scores happen in one vault transaction,
    so that half cannot tear: either the scores are invalidated and the row is
    gone, or neither happened and the row is retried.

    A row whose application fails keeps its place with an incremented
    ``attempts`` count and a logged error, so a permanently failing entry is
    visible rather than retried forever in silence.

    Args:
        session: Active vault session.

    Returns:
        The number of cached scores cleared.
    """
    from pixlstash.db_models.pending_score_invalidation import PendingScoreInvalidation

    pending = session.exec(select(PendingScoreInvalidation)).all()
    if not pending:
        return 0

    cleared = 0
    for row in pending:
        try:
            tags = json.loads(row.tags)
        except (TypeError, ValueError) as exc:
            logger.error(
                "Pending invalidation %s holds unreadable tags (%r): %s. Dropping "
                "it; re-save the penalised-tag setting to rebuild it.",
                row.id,
                row.tags,
                exc,
            )
            session.delete(row)
            continue

        try:
            cleared += invalidate_for_penalised_tag_change(session, tags)
            session.delete(row)
        except Exception:
            row.attempts = (row.attempts or 0) + 1
            session.add(row)
            logger.exception(
                "Could not apply pending smart-score invalidation %s for tags %s "
                "(attempt %d). Scores for pictures carrying those tags stay stale "
                "until this succeeds.",
                row.id,
                ", ".join(tags),
                row.attempts,
            )

    session.commit()
    if cleared:
        logger.info(
            "Applied pending smart-score invalidations; %d cached score(s) cleared",
            cleared,
        )
    return cleared
