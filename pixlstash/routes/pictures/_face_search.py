"""Endpoint for reverse-image face search against the vault.

POST /pictures/face-search

Accepts one or more image uploads, extracts the dominant face from each, and
returns picture IDs from the vault ranked by ArcFace cosine similarity.  The
score for a picture is the *best* face match across all faces detected in that
picture (max over faces), which naturally surfaces pictures where the queried
person appears regardless of how many other people are also in the picture.

When multiple query images are provided their per-picture scores are combined
according to the ``combine`` parameter before ranking.
"""

from __future__ import annotations

import asyncio
import random as _random
from io import BytesIO
from typing import List

import cv2
import numpy as np
from fastapi import File, HTTPException, Query, Request, UploadFile
from PIL import Image
from pydantic import BaseModel, ConfigDict

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.face_detection_task import FaceDetectionTask

from pixlstash.services import search_query_service
from pixlstash.utils.service.filter_helpers import (
    VALID_COMBINE_MODES,
    collect_set_filter_ids,
    combine_likeness_scores,
    fetch_scope_allowed_picture_ids,
    normalize_set_mode,
)

logger = get_logger(__name__)

_DEFAULT_TOP_N = 20
_MAX_TOP_N = 500
_MAX_POOL_M = 2000


class FaceLikenessMatchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    picture_id: int
    likeness: float


def _picture_face_score(query_emb: np.ndarray, face_embs: list[np.ndarray]) -> float:
    """Return the best cosine similarity between *query_emb* and any face in *face_embs*.

    Args:
        query_emb: Normalised query face embedding (float32 array).
        face_embs: List of face embeddings for one picture.

    Returns:
        Maximum cosine similarity in ``[-1, 1]``, or ``-1.0`` if the list is
        empty.
    """
    if not face_embs:
        return -1.0
    ref = np.stack(face_embs)
    ref_norm = ref / np.maximum(np.linalg.norm(ref, axis=1, keepdims=True), 1e-8)
    sims = ref_norm @ query_emb  # (n_faces,)
    return float(np.clip(sims, -1.0, 1.0).max())


def register_routes(router, server):
    """Register the face-search endpoint on *router*."""

    @router.post(
        "/pictures/face-search",
        summary="Search by face likeness",
        description=(
            "Upload one or more images and retrieve vault pictures ranked by face "
            "similarity (cosine similarity on InsightFace ArcFace embeddings).\n\n"
            "The most prominent face (largest bounding box) in each uploaded image is "
            "used as the query.  For each candidate picture the score is the "
            "**best-matching face** it contains, so pictures where the queried person "
            "appears alongside others are still found accurately.\n\n"
            "When multiple query images are provided, per-picture scores from each "
            "image are combined using the ``combine`` strategy before ranking.  "
            "Images with no detectable face are skipped; returns 422 when no face is "
            "detected in any image.\n\n"
            "**Combine modes**\n"
            "- `mean` (default): arithmetic mean across query images.\n"
            "- `max`: best match to any query image.\n"
            "- `min`: must match all query images.\n"
            "- `harmonic_mean`: emphasises the worst-matching query.\n"
            "- `geometric_mean`: product-like balance.\n\n"
            "**Random modes**\n"
            "- `random=false` (default): returns the top `top_n` most similar pictures.\n"
            "- `random=true`: selects `top_n` pictures at random from the `pool_m` "
            "most similar candidates.\n\n"
            "Results are ordered by descending similarity score. "
            "Only pictures that contain at least one pre-computed face embedding are "
            "considered."
        ),
        response_model=list[FaceLikenessMatchResponse],
    )
    async def search_by_face_likeness(
        request: Request,
        files: List[UploadFile] = File(
            default=[],
            description="One or more query images containing a face to search against.",
        ),
        source_picture_id: int | None = Query(
            None,
            description="Use the stored ArcFace embedding(s) of this picture ID as the query instead of uploading a file.",
        ),
        source_face_id: int | None = Query(
            None,
            description="Use the stored ArcFace embedding of this specific face ID as the query.",
        ),
        top_n: int = Query(
            _DEFAULT_TOP_N,
            ge=1,
            le=_MAX_TOP_N,
            description="Maximum number of results to return.",
        ),
        pool_m: int = Query(
            0,
            ge=0,
            le=_MAX_POOL_M,
            description=(
                "Pool size for random mode. When >0 and `random=true`, the top "
                "`pool_m` matches are collected first and then `top_n` are drawn "
                "at random. Ignored when `random=false`."
            ),
        ),
        use_random: bool = Query(
            False,
            alias="random",
            description="When true, return a random sample from the top-M pool.",
        ),
        threshold: float = Query(
            0.0,
            ge=0.0,
            le=1.0,
            description="Minimum similarity score required to include a result.",
        ),
        combine: str = Query(
            "mean",
            description=(
                "How to combine scores when multiple query images are uploaded. "
                "One of: mean, max, min, harmonic_mean, geometric_mean."
            ),
        ),
        project_id: str | None = Query(
            None,
            description="Filter to pictures in a specific project (numeric ID or 'UNASSIGNED').",
        ),
        set_id: str | None = Query(
            None, description="Filter to pictures in a specific set."
        ),
        set_ids: List[str] = Query(
            [], description="Filter to pictures in multiple sets."
        ),
        set_mode: str = Query(
            "union",
            description="How to combine set filters: union, intersection, difference, xor.",
        ),
        character_id: str | None = Query(
            None,
            description="Filter to pictures containing a specific character (numeric ID).",
        ),
    ):
        # ── Authentication ────────────────────────────────────────────────
        server.auth.require_user_id(request)

        if combine not in VALID_COMBINE_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid combine mode {combine!r}. Must be one of: {', '.join(sorted(VALID_COMBINE_MODES))}",
            )

        # ── Optional filters: set / project / character ────────────────────────
        set_filter_ids = collect_set_filter_ids(
            set_id_value=set_id,
            set_ids_values=list(set_ids),
        )
        normalized_set_mode = normalize_set_mode(set_mode)

        filter_candidate_ids: set[int] | None = None

        if set_filter_ids:
            filter_candidate_ids = search_query_service.fetch_set_filter_candidate_ids(
                server.vault.db,
                set_ids=set_filter_ids,
                set_mode=normalized_set_mode,
            )

        if project_id is not None:
            project_candidate_ids = search_query_service.fetch_project_candidate_ids(
                server.vault.db, project_id
            )
            filter_candidate_ids = (
                project_candidate_ids
                if filter_candidate_ids is None
                else filter_candidate_ids & project_candidate_ids
            )

        if character_id is not None and character_id not in ("ALL", ""):
            try:
                char_id_int = int(character_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid character_id")

            char_candidate_ids = search_query_service.fetch_character_candidate_ids(
                server.vault.db, char_id_int
            )
            filter_candidate_ids = (
                char_candidate_ids
                if filter_candidate_ids is None
                else filter_candidate_ids & char_candidate_ids
            )

        # ── Scope-based candidate restriction ────────────────────────────
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            candidate_ids = (
                filter_candidate_ids & scope_allowed
                if filter_candidate_ids is not None
                else scope_allowed
            )
        else:
            candidate_ids = filter_candidate_ids  # None means unrestricted

        # ── Validate inputs ────────────────────────────────────────────────
        has_files = bool(files)
        has_source_id = source_picture_id is not None or source_face_id is not None
        if not has_files and not has_source_id:
            raise HTTPException(
                status_code=400,
                detail="Provide either 'source_picture_id', 'source_face_id', or upload at least one image file.",
            )
        if has_files and has_source_id:
            raise HTTPException(
                status_code=400,
                detail="Provide either uploaded files or a source ID, not both.",
            )
        if source_picture_id is not None and source_face_id is not None:
            raise HTTPException(
                status_code=400,
                detail="Provide either 'source_picture_id' or 'source_face_id', not both.",
            )

        # ── Build query embeddings ─────────────────────────────────────────
        query_embeddings: list[np.ndarray] = []

        if source_face_id is not None:
            source_embs = search_query_service.fetch_face_embedding_by_face_id(
                server.vault.db, source_face_id
            )
            if not source_embs:
                raise HTTPException(
                    status_code=422,
                    detail=f"No face embedding found for face {source_face_id}.",
                )

            for emb in source_embs:
                emb = emb.astype(np.float32)
                norm = np.linalg.norm(emb)
                if norm > 1e-8:
                    emb = emb / norm
                query_embeddings.append(emb)

        elif source_picture_id is not None:
            source_embs = search_query_service.fetch_face_embeddings_by_picture(
                server.vault.db, source_picture_id
            )
            if not source_embs:
                raise HTTPException(
                    status_code=422,
                    detail=f"No face embeddings found for picture {source_picture_id}.",
                )

            for emb in source_embs:
                emb = emb.astype(np.float32)
                norm = np.linalg.norm(emb)
                if norm > 1e-8:
                    emb = emb / norm
                query_embeddings.append(emb)

        else:
            # ── Decode uploaded images into BGR arrays ───────────────────────
            bgr_images: list[np.ndarray] = []
            for idx, file in enumerate(files):
                content_type = file.content_type or ""
                if not content_type.startswith("image/"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"File {idx + 1}: uploaded file must be an image.",
                    )

                raw_bytes = await file.read()
                if not raw_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File {idx + 1}: uploaded file is empty.",
                    )

                try:
                    pil_image = Image.open(BytesIO(raw_bytes)).convert("RGB")
                except Exception as exc:
                    logger.warning(
                        "face-search: could not open uploaded image %d (%s bytes): %s",
                        idx + 1,
                        len(raw_bytes),
                        exc,
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"File {idx + 1}: could not decode uploaded image.",
                    ) from exc

                bgr_images.append(cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR))

            # ── Run face detection via the GPU task queue ──────────────────
            # FaceDetectionTask runs at URGENT priority, loads InsightFace if not
            # yet initialised, and returns list[list[FaceResult]] — one per image.
            engine = getattr(server.vault, "_engine", None)
            if engine is None:
                raise HTTPException(
                    status_code=503,
                    detail="Inference engine not available.",
                )
            task_runner = getattr(server.vault, "_task_runner", None)
            if task_runner is None:
                raise HTTPException(
                    status_code=503,
                    detail="Task runner not available.",
                )

            detection_task = FaceDetectionTask(engine, bgr_images)
            loop = asyncio.get_event_loop()
            try:
                all_face_results = await loop.run_in_executor(
                    None, task_runner.submit_and_wait, detection_task, 60.0
                )
            except TimeoutError as exc:
                logger.error("face-search: face detection timed out: %s", exc)
                raise HTTPException(
                    status_code=503,
                    detail="Face detection timed out; the server may be under heavy load.",
                ) from exc
            except RuntimeError as exc:
                logger.error("face-search: face detection task failed: %s", exc)
                raise HTTPException(
                    status_code=503,
                    detail="Face detection failed.",
                ) from exc

            # ── Extract query embeddings from detection results ──────────────
            for idx, face_results in enumerate(all_face_results):
                if not face_results:
                    logger.debug(
                        "face-search: no face detected in file %d; skipping", idx + 1
                    )
                    continue

                # Pick the face with the largest bounding box area.
                best_face = max(
                    face_results,
                    key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
                )
                if best_face.embedding is None:
                    logger.warning(
                        "face-search: face in file %d has no embedding; skipping",
                        idx + 1,
                    )
                    continue

                q_emb = best_face.embedding.astype(np.float32)
                norm = np.linalg.norm(q_emb)
                if norm > 1e-8:
                    q_emb = q_emb / norm
                query_embeddings.append(q_emb)

            if not query_embeddings:
                raise HTTPException(
                    status_code=422,
                    detail="No face detected in any of the uploaded images.",
                )

        # ── Fetch candidate face embeddings from DB ───────────────────────
        candidates = search_query_service.fetch_face_candidates(
            server.vault.db, candidate_ids
        )
        if not candidates:
            return []

        # ── Compute per-picture, per-query similarity ─────────────────────
        pic_ids = [pid for pid, _ in candidates]
        pic_face_embs = [embs for _, embs in candidates]

        # scores_matrix shape: (Q, N_pictures)
        scores_matrix = np.array(
            [
                [_picture_face_score(q_emb, embs) for embs in pic_face_embs]
                for q_emb in query_embeddings
            ],
            dtype=np.float32,
        )

        # Combine across queries → (N_pictures,)
        combined = combine_likeness_scores(scores_matrix, combine)

        # Apply threshold
        mask = combined >= threshold
        filtered_ids = [pic_ids[i] for i in range(len(pic_ids)) if mask[i]]
        filtered_scores = combined[mask]

        if not filtered_ids:
            return []

        # Sort descending by combined score
        order = np.argsort(filtered_scores)[::-1]
        sorted_ids = [filtered_ids[i] for i in order]
        sorted_scores = filtered_scores[order]

        # ── Select results ────────────────────────────────────────────────
        effective_pool = top_n if not use_random or pool_m <= 0 else pool_m
        pool_ids = sorted_ids[:effective_pool]
        pool_scores = sorted_scores[:effective_pool]

        if use_random and pool_m > 0 and len(pool_ids) > top_n:
            indices = _random.sample(range(len(pool_ids)), top_n)
            indices.sort(key=lambda i: -pool_scores[i])
            pool_ids = [pool_ids[i] for i in indices]
            pool_scores = pool_scores[indices]
        else:
            pool_ids = pool_ids[:top_n]
            pool_scores = pool_scores[:top_n]

        return [
            {"picture_id": pid, "likeness": round(float(score), 6)}
            for pid, score in zip(pool_ids, pool_scores)
        ]
