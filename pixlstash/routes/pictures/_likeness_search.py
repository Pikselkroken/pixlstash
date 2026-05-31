"""Endpoint for reverse-image likeness search against the vault.

POST /pictures/likeness-search

Accepts one or more image uploads and returns picture IDs from the vault
ranked by visual similarity (cosine similarity on CLIP embeddings).  When
multiple query images are provided their per-candidate scores are combined
according to the ``combine`` parameter before ranking.
"""

from __future__ import annotations

import random as _random
from io import BytesIO
from typing import List

import numpy as np
from fastapi import File, HTTPException, Query, Request, UploadFile
from PIL import Image
from pydantic import BaseModel, ConfigDict

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.likeness.likeness_utils import LikenessUtils
from pixlstash.services import search_query_service
from pixlstash.utils.service.filter_helpers import (
    collect_set_filter_ids,
    combine_likeness_scores,
    fetch_scope_allowed_picture_ids,
    normalize_set_mode,
    VALID_COMBINE_MODES,
)

logger = get_logger(__name__)

_MAX_TOP_N = 500
_MAX_POOL_M = 2000
_DEFAULT_TOP_N = 20


class ImageLikenessMatchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    picture_id: int
    likeness: float


def _encode_query_image(server, pil_image: Image.Image) -> np.ndarray:
    """Encode *pil_image* into a normalised CLIP embedding.

    Raises :class:`~fastapi.HTTPException` 503 when CLIP is unavailable or
    503 when encoding fails.
    """
    engine = getattr(server.vault, "_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Inference engine not available; CLIP model not loaded.",
        )

    workflow = engine.clip_embedding_workflow
    try:
        embeddings = workflow.encode_images([pil_image])
    except Exception as exc:
        logger.error("likeness-search: CLIP encoding failed for query image: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Failed to encode query image with CLIP.",
        ) from exc

    if embeddings is None or embeddings.shape[0] == 0:
        raise HTTPException(
            status_code=503,
            detail="CLIP model returned no embedding for the query image.",
        )

    emb = embeddings[0].astype(np.float32)
    norm = float(np.linalg.norm(emb))
    if norm > 0:
        emb = emb / norm
    return emb


def register_routes(router, server):
    """Register the likeness-search endpoint on *router*."""

    @router.post(
        "/pictures/likeness-search",
        summary="Search by image likeness",
        description=(
            "Upload one or more images and retrieve vault pictures ranked by visual "
            "similarity (cosine similarity on CLIP embeddings).\n\n"
            "When multiple query images are provided, per-candidate scores from each "
            "image are combined using the ``combine`` strategy before ranking.\n\n"
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
            "Only pictures with a pre-computed image embedding are considered."
        ),
        response_model=list[ImageLikenessMatchResponse],
    )
    async def search_by_image_likeness(
        request: Request,
        files: List[UploadFile] = File(
            default=[], description="One or more query images to search against."
        ),
        source_picture_id: int | None = Query(
            None,
            description="Use the stored embedding of this picture ID as the query (single ID, kept for backward compatibility).",
        ),
        source_picture_ids: List[int] = Query(
            default=[],
            description="Use the stored embeddings of these picture IDs as the query. When multiple IDs are supplied results are ranked by the minimum similarity across all sources.",
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
            description="Minimum cosine similarity required to include a result.",
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
            merged: set[int] | None = (
                filter_candidate_ids & scope_allowed
                if filter_candidate_ids is not None
                else scope_allowed
            )
        else:
            merged = filter_candidate_ids  # None means unrestricted
        candidate_ids = list(merged) if merged is not None else None

        # ── Load and validate query embeddings ──────────────────────────
        # Merge source_picture_ids and the legacy single source_picture_id.
        effective_source_ids: list[int] = list(source_picture_ids)
        if (
            source_picture_id is not None
            and source_picture_id not in effective_source_ids
        ):
            effective_source_ids.insert(0, source_picture_id)

        if effective_source_ids:
            # Fast path: fetch stored CLIP embeddings for all source pictures.
            source_rows = search_query_service.fetch_source_clip_embeddings(
                server.vault.db, effective_source_ids
            )
            if not source_rows:
                raise HTTPException(
                    status_code=404,
                    detail="None of the supplied source picture IDs were found or have stored embeddings.",
                )

            query_embeddings: list[np.ndarray] = []
            for pic_id, raw_blob in source_rows:
                emb = LikenessUtils.decode_embedding(raw_blob)
                if emb is None or emb.size == 0:
                    logger.warning(
                        "likeness-search: picture %d has an invalid stored embedding; skipping",
                        pic_id,
                    )
                    continue
                norm = float(np.linalg.norm(emb))
                if norm > 0:
                    emb = emb / norm
                query_embeddings.append(emb.astype(np.float32))

            if not query_embeddings:
                raise HTTPException(
                    status_code=422,
                    detail="None of the supplied source pictures have a valid stored embedding.",
                )

            # When multiple source pictures are supplied, rank by the minimum
            # similarity (each candidate must be similar to *all* sources).
            if len(query_embeddings) > 1:
                combine = "min"
        elif files:
            query_embeddings = []
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
                        "likeness-search: could not open uploaded image %d (%s bytes): %s",
                        idx + 1,
                        len(raw_bytes),
                        exc,
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"File {idx + 1}: could not decode uploaded image.",
                    ) from exc

                query_embeddings.append(_encode_query_image(server, pil_image))
        else:
            raise HTTPException(
                status_code=400,
                detail="Provide either 'source_picture_id', 'source_picture_ids', or upload at least one image file.",
            )

        # ── Fetch candidate embeddings from DB ───────────────────────────
        candidates = search_query_service.fetch_candidate_clip_embeddings(
            server.vault.db, candidate_ids
        )
        if not candidates:
            return []

        # ── Compute cosine similarities ──────────────────────────────────
        ids_arr = np.array([c[0] for c in candidates], dtype=np.int64)
        emb_matrix = np.stack([c[1] for c in candidates])  # (N, D)
        query_matrix = np.stack(query_embeddings)  # (Q, D)

        # (N, Q) — one similarity per candidate per query
        sim_matrix = emb_matrix @ query_matrix.T

        # Combine across Q queries → (N,)
        similarities = combine_likeness_scores(sim_matrix.T, combine)

        # Apply threshold
        mask = similarities >= threshold
        ids_arr = ids_arr[mask]
        similarities = similarities[mask]

        if ids_arr.size == 0:
            return []

        # Sort descending by similarity
        order = np.argsort(similarities)[::-1]
        ids_arr = ids_arr[order]
        similarities = similarities[order]

        # ── Select results ────────────────────────────────────────────────
        effective_pool = top_n if not use_random or pool_m <= 0 else pool_m
        ids_pool = ids_arr[:effective_pool]
        sim_pool = similarities[:effective_pool]

        if use_random and pool_m > 0 and len(ids_pool) > top_n:
            indices = _random.sample(range(len(ids_pool)), top_n)
            # Re-sort the random selection by similarity (descending)
            indices.sort(key=lambda i: -sim_pool[i])
            ids_pool = ids_pool[indices]
            sim_pool = sim_pool[indices]
        else:
            ids_pool = ids_pool[:top_n]
            sim_pool = sim_pool[:top_n]

        return [
            {"picture_id": int(pic_id), "likeness": round(float(sim), 6)}
            for pic_id, sim in zip(ids_pool, sim_pool)
        ]
