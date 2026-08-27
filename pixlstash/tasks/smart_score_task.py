import logging
import time

from sqlalchemy import bindparam, desc, func, update
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Picture,
    Quality,
)
from pixlstash.scoring.smart_score import (
    _load_builtin_anchors,
    _BUILTIN_MIN_GOOD,
    _BUILTIN_MIN_BAD,
    attach_anomaly_inputs,
    prepare_smart_score_inputs,
    resolve_penalised_tag_weights,
)
from pixlstash.utils.quality.smart_score_utils import SmartScoreUtils
from pixlstash.utils.service.anomaly_thresholds import resolve_anomaly_apply_thresholds
from pixlstash.utils.service.smart_score_invalidation import anomaly_state_signature
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask


logger = get_logger(__name__)


class SmartScoreTask(BaseTask):
    """Task that pre-computes and stores smart scores for one batch of pictures.

    Needs the :class:`~pixlstash.vault.Vault` (not just the database) because two of the
    scorer's inputs are owned outside the DB session: the tagger's per-label acceptance
    thresholds, which live in the model's ``meta.json`` plus the user's threshold offset.
    The owner's penalised-tag weights are resolved from the DB inside the read session by
    :func:`~pixlstash.scoring.smart_score.attach_anomaly_inputs`, so this background path and
    the request path score identically.

    Args:
        vault: Vault used to resolve the tagger's anomaly apply thresholds.
        pictures: Pictures to score in this batch.
    """

    BATCH_SIZE = 64
    #: A batch slower than this logs its [SMART_SCORE_TIMING] line at INFO
    #: instead of DEBUG. Same threshold as FaceExtractionTask.SLOW_BATCH_LOG_S.
    SLOW_BATCH_LOG_S = 5.0

    def __init__(self, vault, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="SmartScoreTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._vault = vault
        self._db = vault.db
        self._pictures = pictures or []

    def _run_task(self):
        start = time.perf_counter()
        pics = self._pictures
        if not pics:
            return {"changed_count": 0}

        picture_ids = [pic.id for pic in pics if getattr(pic, "id", None)]
        if not picture_ids:
            return {"changed_count": 0}

        apply_thresholds = resolve_anomaly_apply_thresholds(self._vault)
        # The owner's table comes from the hub's user row, never from ``self._db`` -
        # that is the vault, and identity does not live there.
        owner_penalised_tags = resolve_penalised_tag_weights(
            getattr(self._vault, "auth_service", None)
        )
        fetch_start = time.perf_counter()
        good_anchors, bad_anchors, candidates, scorer_config, before_signature = (
            self._db.run_immediate_read_task(
                self._fetch_score_data,
                picture_ids,
                apply_thresholds,
                owner_penalised_tags,
            )
        )
        fetch_s = time.perf_counter() - fetch_start

        good_list, bad_list, cand_list, cand_ids = prepare_smart_score_inputs(
            good_anchors, bad_anchors, candidates
        )

        if not cand_list:
            logger.debug("SmartScoreTask: no valid candidates in batch, skipping.")
            return {"changed_count": 0}

        inference_start = time.perf_counter()
        scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
            cand_list,
            good_list,
            bad_list,
            config=scorer_config,
        )
        inference_s = time.perf_counter() - inference_start

        id_to_score = {cand_ids[i]: float(scores[i]) for i in range(len(cand_ids))}

        db_start = time.perf_counter()
        persisted_ids = self._db.run_task(
            self._persist_scores,
            id_to_score,
            before_signature,
            priority=DBPriority.LOW,
        )
        db_s = time.perf_counter() - db_start

        total_s = time.perf_counter() - start
        n = len(persisted_ids)
        # The scorer is numpy on the host, whatever queue the task rides on.
        logger.log(
            logging.INFO if total_s >= self.SLOW_BATCH_LOG_S else logging.DEBUG,
            "[SMART_SCORE_TIMING] task_id=%s n=%d device=cpu preload_wait_s=0.000 "
            "fetch_s=%.3f inference_s=%.3f db_s=%.3f total_s=%.3f throughput=%.1f/s",
            self.id,
            n,
            fetch_s,
            inference_s,
            db_s,
            total_s,
            n / total_s if total_s > 0 else 0.0,
        )
        # ``persisted_ids`` is the *actually-written* subset - ids the CAS in
        # ``_persist_scores`` skipped (anomaly signature drifted mid-scoring) are left
        # NULL and excluded, so the completion handler never announces a still-NULL score
        # as a finished rescore.
        return {"changed_count": len(persisted_ids), "persisted_ids": persisted_ids}

    @staticmethod
    def _fetch_score_data(
        session: Session,
        candidate_ids: list,
        apply_thresholds: dict | None = None,
        owner_penalised_tags: dict | None = None,
    ):
        """Fetch anchors, candidates, and the scorer config for smart score computation.

        Returns ``(good_anchors, bad_anchors, candidates, scorer_config,
        anomaly_signature)``. Mirrors
        :func:`pixlstash.scoring.smart_score.fetch_smart_score_data` and shares
        :func:`pixlstash.scoring.smart_score.attach_anomaly_inputs`, so the background task
        and the on-demand sort score identically.

        The ``anomaly_signature`` (``{picture_id: signature}`` from
        :func:`~pixlstash.utils.service.smart_score_invalidation.anomaly_state_signature`)
        is captured in the *same* read transaction as the scorer inputs, so it pins the
        exact anomaly state the scores are computed from. :meth:`_persist_scores`
        re-snapshots and compares against it, dropping any write whose inputs a concurrent
        tag edit moved in between - see that method.

        Args:
            session: Active DB session.
            candidate_ids: Pictures to score.
            apply_thresholds: Per-tag confidence gate; see
                :func:`pixlstash.scoring.smart_score.fetch_anomaly_confidences`.
            owner_penalised_tags: The owner's ``{tag: weight}`` table, resolved from the
                hub by the caller - ``session`` is a vault one and holds no identity.
        """
        good = session.exec(
            select(Picture.image_embedding, Picture.score)
            .where(Picture.score >= 4)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.deleted.is_(False))
            .order_by(desc(Picture.score), desc(Picture.created_at))
            .limit(200)
        ).all()

        bad = session.exec(
            select(Picture.image_embedding, Picture.score)
            .where(Picture.score <= 1)
            .where(Picture.score > 0)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.deleted.is_(False))
            .order_by(Picture.score, desc(Picture.created_at))
            .limit(200)
        ).all()

        query = (
            select(Picture, Quality)
            .outerjoin(
                Quality,
                Quality.picture_id == Picture.id,
            )
            .where(Picture.id.in_(candidate_ids))
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.deleted.is_(False))
        )
        candidate_rows = session.exec(query).all()

        candidates = []
        for pic, quality in candidate_rows:
            aest = pic.aesthetic_score
            if aest is None and quality is not None:
                try:
                    aest = quality.calculate_quality_score()
                except Exception as exc:
                    logger.warning(
                        "SmartScoreTask: quality score failed for picture %s: %s",
                        pic.id,
                        exc,
                    )
            candidates.append(
                {
                    "id": pic.id,
                    "image_embedding": pic.image_embedding,
                    "aesthetic_score": aest,
                    "width": pic.width,
                    "height": pic.height,
                    "sharpness": quality.sharpness if quality else None,
                    "edge_density": quality.edge_density if quality else None,
                    "luminance_entropy": quality.luminance_entropy if quality else None,
                    "noise_level": quality.noise_level if quality else None,
                    "colorfulness": quality.colorfulness if quality else None,
                    "text_score": pic.text_score,
                }
            )

        scorer_config = attach_anomaly_inputs(
            session,
            candidates,
            apply_thresholds=apply_thresholds,
            penalised_tag_weights=owner_penalised_tags,
        )

        builtin_good, builtin_bad = _load_builtin_anchors()
        if len(good) < _BUILTIN_MIN_GOOD:
            good = list(good) + builtin_good
        if len(bad) < _BUILTIN_MIN_BAD:
            bad = list(bad) + builtin_bad

        before_signature = anomaly_state_signature(session, candidate_ids)

        return good, bad, candidates, scorer_config, before_signature

    @staticmethod
    def _persist_scores(session: Session, id_to_score: dict, before: dict) -> list[int]:
        """Write each computed ``smart_score`` - but only if its inputs did not move.

        Scores are computed outside this write transaction. If a concurrent tag edit
        NULLs a picture's ``smart_score`` (via
        :func:`~pixlstash.utils.service.smart_score_invalidation.invalidate_on_anomaly_change`)
        after the inputs were read but before this runs, writing the stale score would
        resurrect an invalidated row that the finder can never re-pick (``WHERE
        smart_score IS NULL`` cannot tell "invalidated since claimed" from "not yet
        scored"). So this is a compare-and-swap: re-snapshot the anomaly signature in this
        transaction and write ``smart_score`` only for pictures whose signature is
        unchanged since :meth:`_fetch_score_data` captured *before*. A picture whose
        signature moved is **skipped and left NULL** on purpose - the finder re-picks it
        and it rescores from fresh inputs. Writing the old value here would recreate the
        bug.

        Args:
            session: Active DB session.
            id_to_score: ``{picture_id: score}`` computed from the *before* inputs.
            before: Anomaly signature map captured in the read transaction that loaded
                the scorer inputs.

        Returns:
            The picture ids whose score was actually written this transaction, in input
            order. Ids skipped due to anomaly-state drift (left NULL for recompute) are
            excluded - the interactive-refresh path keys off this list, so a skipped,
            still-NULL id is never announced as a finished rescore.
        """
        after = anomaly_state_signature(session, list(id_to_score.keys()))

        # Compare-and-swap (B1): keep only pictures whose anomaly inputs did not move
        # between the read transaction that computed the score and this write. A drifted
        # picture is deliberately left NULL so MissingSmartScoreFinder re-picks it and it
        # rescores from fresh inputs - writing the stale value would resurrect an
        # invalidated row the finder can never re-pick.
        unchanged: list[int] = []
        skipped = 0
        for pic_id in id_to_score:
            if before.get(pic_id) != after.get(pic_id):
                skipped += 1
                continue
            unchanged.append(pic_id)

        # Exclude any picture hard-deleted between compute and persist - the old per-row
        # ``session.get(...) is None`` guard. One batched SELECT (<= BATCH_SIZE ids, well
        # under SQLite's bound-variable cap) replaces N ``session.get`` round-trips.
        existing: set = set()
        if unchanged:
            existing = set(
                session.exec(select(Picture.id).where(Picture.id.in_(unchanged))).all()
            )
        to_write = [pic_id for pic_id in unchanged if pic_id in existing]

        if to_write:
            # One prepared statement, N parameter sets - replaces the old 64-get + 64-set
            # ORM loop on the single writer queue (this task runs full-library-wide via
            # migration 0076). Target ``Picture.__table__`` (Core ``Table``), NOT the ORM
            # ``Picture`` mapper, so SQLAlchemy does not route this through the ORM
            # bulk-by-primary-key path (which clashes with the explicit WHERE bindparam)
            # and so the metadata-hash ``after_flush`` hook does not re-fire. That hook is
            # a no-op for smart_score either way - ``smart_score`` is in
            # ``database._HASH_SKIP_COLS`` so the hash value does not depend on it - but
            # the old ORM loop dirtied each Picture and forced a wasted hash recompute to
            # the same value; this Core path skips that while leaving the stored
            # ``metadata_hash`` identical, matching the previous net behaviour.
            stmt = (
                update(Picture.__table__)
                .where(Picture.__table__.c.id == bindparam("_pid"))
                .values(smart_score=bindparam("_smart_score"))
            )
            session.execute(
                stmt,
                [
                    {"_pid": pic_id, "_smart_score": id_to_score[pic_id]}
                    for pic_id in to_write
                ],
            )
        session.commit()
        if skipped:
            logger.info(
                "SmartScoreTask: persisted %d score(s); skipped %d whose anomaly state "
                "changed during scoring (left NULL for recompute).",
                len(to_write),
                skipped,
            )
        else:
            logger.debug(
                "SmartScoreTask: persisted %d score(s); no anomaly-state drift.",
                len(to_write),
            )
        return to_write

    @classmethod
    def count_remaining(cls, session: Session) -> int:
        """Count pictures that have an embedding but no stored smart score."""
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.smart_score.is_(None))
            .where(Picture.deleted.is_(False))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def find_pictures_missing_smart_score(session: Session, limit: int) -> list:
        """Fetch pictures that need smart score computation.

        Only the ids are loaded. This is an idle probe the WorkPlanner runs on
        every sweep, and the full-ORM form read every column of every candidate -
        including ``image_embedding``, ``text_embedding`` and
        ``likeness_parameters``, three LargeBinary columns worth kilobytes each.
        Nothing downstream needs them here: :meth:`SmartScoreTask.__init__` and
        :meth:`_run_task` read only ``pic.id``, and :meth:`_fetch_score_data`
        re-selects the scorer inputs by id inside its own read transaction.

        Deliberately still an ORM ``select(Picture)`` rather than
        ``select(Picture.id)``: ``BaseTaskFinder._filter_and_claim`` selects
        candidates with ``getattr(picture, "id", None)``, so a scalar/tuple result
        would silently claim nothing and the finder would never produce a task.
        """
        return session.exec(
            select(Picture)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.smart_score.is_(None))
            .where(Picture.deleted.is_(False))
            .options(load_only(Picture.id))
            .order_by(Picture.id)
            .limit(limit)
        ).all()
