"""Smart-score computation: anchor-based heuristic combining image embedding,
CLIP anchors, an objective quality probe, and a calibrated anomaly penalty.

Split out of the former ``pixlstash.picture_scoring`` module (Backend Refactor
Phase 2 §4.6). Character-likeness scoring lives in the sibling
:mod:`pixlstash.scoring.character_likeness`. The public ``pixlstash.picture_scoring``
import path is preserved as a thin re-export shim.
"""

import functools
import pathlib
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from sqlalchemy import desc, func
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    Picture,
    Quality,
    TagPrediction,
    User,
)
from pixlstash.services.tagger_run_service import get_latest_tag_precisions
from pixlstash.utils.quality.anomaly_penalty import ANOMALY_PENALTY_TAGS
from pixlstash.utils.quality.smart_score_utils import (
    SmartScoreUtils,
    smart_score_penalised_tags,
)
from pixlstash.utils.service.anomaly_thresholds import resolve_anomaly_apply_thresholds
from pixlstash.utils.service.label_ledger import HUMAN, NEG, POS
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# If the user has fewer than this many rated images in a category, built-in
# anchor embeddings are added to prevent empty-anchor edge cases.
_BUILTIN_MIN_GOOD = 10
_BUILTIN_MIN_BAD = 10


@dataclass
class _BuiltinAnchor:
    """Thin wrapper so built-in numpy embeddings look like DB anchor rows."""

    image_embedding: np.ndarray
    score: int


@functools.lru_cache(maxsize=1)
def _load_builtin_anchors() -> tuple[list["_BuiltinAnchor"], list["_BuiltinAnchor"]]:
    """Load pre-computed built-in CLIP anchor embeddings from package data.

    Returns:
        Tuple of (good_anchors, bad_anchors) where each element is a list of
        _BuiltinAnchor objects compatible with prepare_smart_score_inputs.
    """
    data_dir = pathlib.Path(__file__).parent / "data" / "anchors"
    good_path = data_dir / "builtin_good.npy"
    bad_path = data_dir / "builtin_bad.npy"

    def _load(path: pathlib.Path, score: int) -> list[_BuiltinAnchor]:
        if not path.is_file():
            logger.debug("Built-in anchor file not found: %s", path)
            return []
        try:
            arr = np.load(path)
            if arr.ndim != 2 or arr.shape[1] == 0:
                logger.warning("Unexpected shape in %s: %s", path.name, arr.shape)
                return []
            return [
                _BuiltinAnchor(image_embedding=arr[i], score=score)
                for i in range(len(arr))
            ]
        except Exception as e:
            logger.warning("Failed to load built-in anchor file %s: %s", path.name, e)
            return []

    good = _load(good_path, score=4)
    bad = _load(bad_path, score=1)
    logger.debug(
        "Loaded built-in anchors: %d good, %d bad",
        len(good),
        len(bad),
    )
    return good, bad


def get_smart_score_penalised_tags_from_request(server, request):
    user_id = server.auth.get_user_id(request)
    if user_id is None:
        return DEFAULT_SMART_SCORE_PENALIZED_TAGS
    user = server.vault.db.run_task(
        lambda session: session.get(User, user_id),
        priority=DBPriority.IMMEDIATE,
    )
    return smart_score_penalised_tags(
        user.smart_score_penalised_tags if user else None,
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )


def fetch_anomaly_confidences(
    session: Session, picture_ids, apply_thresholds: dict | None = None
) -> tuple[dict, dict]:
    """Return per-picture anomaly probabilities and human-verified tags.

    Reads ``TagPrediction`` for the anomaly vocabulary only. A human decision in the
    label ledger overrides the model: a human POS folds to probability 1.0 (and is
    flagged so the penalty bypasses the precision floor), a human NEG folds to 0.0.

    Args:
        session: Active DB session.
        picture_ids: Pictures to read predictions for.
        apply_thresholds: ``{tag: minimum confidence}`` from
            :func:`~pixlstash.utils.service.anomaly_thresholds.resolve_anomaly_apply_thresholds`.
            A **model** prediction below its tag's threshold never became a visible
            ``Tag``, so it is dropped rather than silently penalising the picture.
            Human decisions are exempt in both directions: a human POS under threshold
            still counts, a human NEG still suppresses — which is precisely why this
            gate is applied here and not by reading the ``Tag`` table instead.
            ``None`` disables gating (used for change-detection signatures, which want
            the raw prediction state).

    Returns:
        ``(probs_map, human_map)`` where ``probs_map`` is
        ``{picture_id: {tag: probability}}`` and ``human_map`` is
        ``{picture_id: {tag, ...}}`` of human-verified present tags.
    """
    probs_map: dict = defaultdict(dict)
    human_map: dict = defaultdict(set)
    if not picture_ids:
        return probs_map, human_map

    rows = session.exec(
        select(
            TagPrediction.picture_id,
            TagPrediction.tag,
            TagPrediction.confidence,
            TagPrediction.label_state,
            TagPrediction.label_source,
        ).where(
            TagPrediction.picture_id.in_(picture_ids),
            func.lower(TagPrediction.tag).in_(ANOMALY_PENALTY_TAGS),
        )
    ).all()

    below_threshold = 0
    for picture_id, tag, confidence, label_state, label_source in rows:
        if not tag:
            continue
        key = tag.strip().lower()
        if label_source == HUMAN and label_state == POS:
            probs_map[picture_id][key] = 1.0
            human_map[picture_id].add(key)
        elif label_source == HUMAN and label_state == NEG:
            probs_map[picture_id][key] = 0.0
        else:
            value = float(confidence) if confidence is not None else 0.0
            if apply_thresholds is not None:
                threshold = apply_thresholds.get(key)
                if threshold is not None and value < threshold:
                    below_threshold += 1
                    continue
            probs_map[picture_id][key] = value

    if below_threshold:
        logger.debug(
            "Anomaly penalty inputs: dropped %d sub-threshold model prediction(s) "
            "across %d picture(s); they never became applied tags.",
            below_threshold,
            len(picture_ids),
        )

    return probs_map, human_map


def resolve_penalised_tag_weights(session: Session) -> dict:
    """Return the owner's effective ``{tag: weight}`` penalised-tag table.

    PixlStash is single-user, so "the owner" is the single row in ``user`` (the same
    resolution :mod:`pixlstash.auth` uses). The user's table *replaces* the shipped
    defaults rather than merging with them — that is the contract of
    :func:`~pixlstash.utils.quality.smart_score_utils.smart_score_penalised_tags`, which
    only returns the fallback when the stored value is absent or unparseable. A tag the
    user deleted is therefore genuinely no longer penalised.

    Resolving this inside the scoring session is what lets the background
    :class:`~pixlstash.tasks.smart_score_task.SmartScoreTask` honour the user's config;
    the request-scoped :func:`get_smart_score_penalised_tags_from_request` cannot, since
    a background task has no request.

    Args:
        session: Active DB session.

    Returns:
        ``{tag: weight}`` with lowercase tags and weights clamped to 1-5.
    """
    user = session.exec(select(User)).first()
    if user is None:
        logger.warning(
            "No user row found while resolving penalised-tag weights; falling back to "
            "the shipped DEFAULT_SMART_SCORE_PENALIZED_TAGS seed."
        )
        return dict(DEFAULT_SMART_SCORE_PENALIZED_TAGS)
    return smart_score_penalised_tags(
        user.smart_score_penalised_tags,
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )


def attach_anomaly_inputs(
    session: Session, candidates, apply_thresholds: dict | None = None
) -> dict:
    """Attach calibrated anomaly inputs to candidates; return the scorer's config block.

    Adds ``anomaly_probs`` and ``anomaly_human`` to each candidate (see
    :func:`fetch_anomaly_confidences`). Shared by both smart-score fetch paths so the
    on-demand sort and the background task stay in lockstep.

    Args:
        session: Active DB session.
        candidates: Candidate dicts to annotate in place.
        apply_thresholds: Confidence gate per anomaly tag; see
            :func:`fetch_anomaly_confidences`.

    Returns:
        Config overrides for
        :meth:`~pixlstash.utils.quality.smart_score_utils.SmartScoreUtils.calculate_smart_score_batch_numpy`:
        ``tag_precisions`` from the latest evaluated :class:`TaggerRun`,
        ``penalised_tag_weights`` resolved from the owner's config, and ``tag_thresholds``
        — the same gate applied here, forwarded so the penalty can grade each detection's
        confidence relative to its own acceptance threshold rather than in absolute terms.
    """
    config = {
        "tag_precisions": get_latest_tag_precisions(session),
        "penalised_tag_weights": resolve_penalised_tag_weights(session),
        "tag_thresholds": dict(apply_thresholds or {}),
    }
    ids = [c.get("id") for c in candidates if c.get("id") is not None]
    if not ids:
        return config
    probs_map, human_map = fetch_anomaly_confidences(
        session, ids, apply_thresholds=apply_thresholds
    )
    for candidate in candidates:
        pid = candidate.get("id")
        candidate["anomaly_probs"] = probs_map.get(pid, {})
        candidate["anomaly_human"] = human_map.get(pid, frozenset())
    return config


def fetch_smart_score_data(
    server,
    format,
    candidate_ids=None,
    penalised_tags=None,
    include_deleted: bool = False,
    only_deleted: bool = False,
):
    """Fetch anchors, character references, and candidates for smart score calculation.

    Returns ``(good_anchors, bad_anchors, candidates, scorer_config)``, where
    ``scorer_config`` carries the per-tag precisions and the owner's penalised-tag
    weights (see :func:`attach_anomaly_inputs`). ``penalised_tags`` is retained for
    signature compatibility but is not used: the weights are resolved from the owner's
    stored config inside the read session, so the request path and the background task
    resolve them identically.
    """
    apply_thresholds = resolve_anomaly_apply_thresholds(server.vault)

    def fetch_data(session: Session):
        # Anchors
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

        # Candidates — join to picture-level quality rows.
        query = select(Picture, Quality).outerjoin(
            Quality,
            Quality.picture_id == Picture.id,
        )
        if only_deleted:
            query = query.where(Picture.deleted.is_(True))
        elif not include_deleted:
            query = query.where(Picture.deleted.is_(False))

        if candidate_ids is not None:
            if not candidate_ids:
                return good, bad, [], {}
            query = query.where(Picture.id.in_(candidate_ids))

        if format:
            query = query.where(Picture.format.in_(format))

        query = query.where(Picture.image_embedding.is_not(None))

        candidate_rows = session.exec(query).all()

        candidates = []
        for pic, quality in candidate_rows:
            aest = pic.aesthetic_score
            quality_score = None
            if quality is not None:
                try:
                    quality_score = quality.calculate_quality_score()
                except Exception as e:
                    logger.warning(
                        "Failed to compute heuristic quality score for picture %s: %s",
                        pic.id,
                        e,
                    )
            if aest is None:
                aest = quality_score
            candidates.append(
                {
                    "id": pic.id,
                    "image_embedding": pic.image_embedding,
                    "aesthetic_score": aest,
                    "width": pic.width,
                    "height": pic.height,
                    "sharpness": quality.sharpness if quality else None,
                    "edge_density": quality.edge_density if quality else None,
                    "luminance_entropy": (
                        quality.luminance_entropy if quality else None
                    ),
                    "noise_level": quality.noise_level if quality else None,
                    "colorfulness": quality.colorfulness if quality else None,
                    "text_score": pic.text_score,
                }
            )

        # Calibrated anomaly inputs, per-tag precision, and the owner's penalised-tag
        # weights.
        scorer_config = attach_anomaly_inputs(
            session, candidates, apply_thresholds=apply_thresholds
        )

        # Supplement with built-in anchors when the user has few rated images.
        builtin_good, builtin_bad = _load_builtin_anchors()
        if len(good) < _BUILTIN_MIN_GOOD:
            good = list(good) + builtin_good
        if len(bad) < _BUILTIN_MIN_BAD:
            bad = list(bad) + builtin_bad

        return good, bad, candidates, scorer_config

    return server.vault.db.run_immediate_read_task(fetch_data)


def fetch_smart_score_unscored_ids(
    server,
    format,
    candidate_ids=None,
    descending=True,
    include_deleted: bool = False,
    only_deleted: bool = False,
):
    def fetch_ids(session: Session):
        query = select(Picture.id)
        if only_deleted:
            query = query.where(Picture.deleted.is_(True))
        elif not include_deleted:
            query = query.where(Picture.deleted.is_(False))

        if candidate_ids is not None:
            if not candidate_ids:
                return []
            query = query.where(Picture.id.in_(candidate_ids))

        if format:
            query = query.where(Picture.format.in_(format))

        query = query.where(Picture.image_embedding.is_(None))

        if descending:
            query = query.order_by(desc(Picture.created_at), desc(Picture.id))
        else:
            query = query.order_by(Picture.created_at, Picture.id)

        return [row for row in session.exec(query).all()]

    return server.vault.db.run_task(fetch_ids, priority=DBPriority.IMMEDIATE)


def prepare_smart_score_inputs(good_anchors, bad_anchors, candidates):
    """Decode embeddings and prepare lists of dictionaries for calculation."""

    def get_attr(item, key):
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    def get_vec(blob):
        if blob is None:
            return None
        if isinstance(blob, (memoryview, bytearray)):
            blob = bytes(blob)
        if isinstance(blob, np.ndarray):
            arr = np.asarray(blob, dtype=np.float32)
            return arr if arr.ndim == 1 and arr.size > 0 else None
        if not isinstance(blob, (bytes, bytearray)):
            try:
                blob = bytes(blob)
            except Exception:
                return None
        try:
            arr = np.frombuffer(blob, dtype=np.float32)
            if arr.ndim != 1 or arr.size == 0:
                return None
            return arr.copy()
        except Exception:
            return None

    def process_list(items):
        result = []
        for p in items:
            v = get_vec(p.image_embedding)
            if v is not None:
                result.append({"embedding": v, "score": getattr(p, "score", 0)})
        return result

    good_list = process_list(good_anchors)
    bad_list = process_list(bad_anchors)

    cand_list = []
    cand_ids = []

    for p in candidates:
        pid = get_attr(p, "id")
        v = get_vec(get_attr(p, "image_embedding"))
        if v is not None:
            cand_ids.append(pid)
            cand_list.append(
                {
                    "id": pid,
                    "embedding": v,
                    "aesthetic_score": get_attr(p, "aesthetic_score"),
                    "anomaly_probs": get_attr(p, "anomaly_probs") or {},
                    "anomaly_human": get_attr(p, "anomaly_human") or frozenset(),
                    "width": get_attr(p, "width"),
                    "height": get_attr(p, "height"),
                    "sharpness": get_attr(p, "sharpness"),
                    "edge_density": get_attr(p, "edge_density"),
                    "luminance_entropy": get_attr(p, "luminance_entropy"),
                    "noise_level": get_attr(p, "noise_level"),
                    "colorfulness": get_attr(p, "colorfulness"),
                    "text_score": get_attr(p, "text_score"),
                }
            )

    return good_list, bad_list, cand_list, cand_ids


def find_pictures_by_smart_score(
    server,
    format,
    offset,
    limit,
    descending,
    candidate_ids=None,
    penalised_tags=None,
    include_deleted: bool = False,
    only_deleted: bool = False,
    progress_reporter=None,
):
    def report_progress(status: str, current: int, total: int, message: str):
        if not callable(progress_reporter):
            return
        safe_total = max(0, int(total or 0))
        safe_current = max(0, min(int(current or 0), safe_total))
        progress = (safe_current / safe_total * 100.0) if safe_total else 0.0
        try:
            progress_reporter(
                {
                    "status": status,
                    "current": safe_current,
                    "total": safe_total,
                    "progress": progress,
                    "message": message,
                }
            )
        except Exception:
            # Progress reporting should never break sorting.
            logger.debug("Progress reporting failed during sort.", exc_info=True)

    # 1. Fetch data
    good_anchors, bad_anchors, candidates, scorer_config = fetch_smart_score_data(
        server,
        format,
        candidate_ids=candidate_ids,
        penalised_tags=penalised_tags,
        include_deleted=include_deleted,
        only_deleted=only_deleted,
    )

    unscored_ids = fetch_smart_score_unscored_ids(
        server,
        format,
        candidate_ids=candidate_ids,
        descending=descending,
        include_deleted=include_deleted,
        only_deleted=only_deleted,
    )

    score_map = {}
    scored_ids = []

    if candidates:
        good_list, bad_list, cand_list, cand_ids = prepare_smart_score_inputs(
            good_anchors, bad_anchors, candidates
        )

        if cand_list:
            total_candidates = len(cand_list)
            report_progress(
                "running",
                0,
                total_candidates,
                f"Calculating smart scores (0/{total_candidates})",
            )

            chunk_size = 1024
            score_chunks = []
            processed = 0
            for start in range(0, total_candidates, chunk_size):
                end = min(start + chunk_size, total_candidates)
                batch = cand_list[start:end]
                batch_scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
                    batch,
                    good_list,
                    bad_list,
                    config=scorer_config,
                )
                score_chunks.append(np.asarray(batch_scores, dtype=np.float32))
                processed = end
                report_progress(
                    "running",
                    processed,
                    total_candidates,
                    f"Calculating smart scores ({processed}/{total_candidates})",
                )

            scores = (
                np.concatenate(score_chunks)
                if len(score_chunks) > 1
                else (score_chunks[0] if score_chunks else np.array([]))
            )

            # Primary sort key is raw smart score so UI labels and ordering
            # always align. Picture ID is a deterministic tiebreaker.
            scores_array = np.asarray(scores, dtype=np.float32)
            ids_array = np.array(cand_ids, dtype=np.int64)
            if descending:
                # lexsort key order: last key is primary.
                # Primary: -score (highest score first)
                # Secondary: id (lowest id first within tied bucket)
                sorted_indices = np.lexsort((ids_array, -scores_array))
            else:
                sorted_indices = np.lexsort((ids_array, scores_array))

            scored_ids = [cand_ids[i] for i in sorted_indices]
            score_map = {cand_ids[i]: float(scores[i]) for i in range(len(scores))}
            report_progress(
                "completed",
                total_candidates,
                total_candidates,
                f"Calculated smart scores ({total_candidates}/{total_candidates})",
            )

    combined_ids = scored_ids + unscored_ids
    if not combined_ids:
        return []

    seen = set()
    unique_ids = []
    for pid in combined_ids:
        if pid is None:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        unique_ids.append(pid)

    final_ids = unique_ids[offset : offset + limit]

    if len(final_ids) == 0:
        return []

    def fetch_final_pics(session, ids):
        return session.exec(select(Picture).where(Picture.id.in_(ids))).all()

    res_pics = server.vault.db.run_task(
        fetch_final_pics, final_ids, priority=DBPriority.IMMEDIATE
    )
    pmap = {p.id: p for p in res_pics}
    metadata_fields = Picture.metadata_fields()

    results = []
    for pid in final_ids:
        if pid in pmap:
            p = pmap[pid]
            d = {field: getattr(p, field) for field in metadata_fields}
            d["smartScore"] = score_map.get(pid)
            results.append(d)

    return results
