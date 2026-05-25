"""Endpoint for reverse-image likeness search against the vault.

POST /pictures/likeness-search

Accepts an image upload and returns picture IDs from the vault ranked by
visual similarity (cosine distance on CLIP embeddings), optionally drawing a
random sample from the top-M results.
"""

from __future__ import annotations

import random as _random
from io import BytesIO

import numpy as np
from fastapi import File, HTTPException, Query, Request, UploadFile
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.likeness.likeness_utils import LikenessUtils
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids

logger = get_logger(__name__)

_MAX_TOP_N = 500
_MAX_POOL_M = 2000
_DEFAULT_TOP_N = 20


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


def _fetch_candidate_embeddings(
    server, candidate_ids: list[int] | None
) -> list[tuple[int, np.ndarray]]:
    """Return ``(picture_id, normalised_embedding)`` pairs from the DB.

    Filters to non-deleted pictures that have a stored image embedding.
    When *candidate_ids* is provided only those IDs are considered.
    """

    def _query(session: Session) -> list[tuple[int, bytes]]:
        stmt = (
            select(Picture.id, Picture.image_embedding)
            .where(Picture.deleted.is_(False))
            .where(Picture.image_embedding.is_not(None))
        )
        if candidate_ids is not None:
            stmt = stmt.where(Picture.id.in_(candidate_ids))
        return session.exec(stmt).all()

    rows = server.vault.db.run_immediate_read_task(_query)

    results: list[tuple[int, np.ndarray]] = []
    for pic_id, blob in rows:
        emb = LikenessUtils.decode_embedding(blob)
        if emb is None or emb.size == 0:
            continue
        norm = float(np.linalg.norm(emb))
        if norm > 0:
            emb = emb / norm
        results.append((int(pic_id), emb))
    return results


def register_routes(router, server):
    """Register the likeness-search endpoint on *router*."""

    @router.post(
        "/pictures/likeness-search",
        summary="Search by image likeness",
        description=(
            "Upload an image and retrieve vault pictures ranked by visual similarity "
            "(cosine similarity on CLIP embeddings).\n\n"
            "**Modes**\n"
            "- `random=false` (default): returns the top `top_n` most similar pictures.\n"
            "- `random=true`: selects `top_n` pictures at random from the `pool_m` "
            "most similar candidates.\n\n"
            "Results are ordered by descending similarity score. "
            "Only pictures with a pre-computed image embedding are considered."
        ),
        tags=["pictures"],
    )
    async def search_by_image_likeness(
        request: Request,
        file: UploadFile = File(..., description="Query image to search against."),
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
    ):
        # ── Authentication ────────────────────────────────────────────────
        server.auth.require_user_id(request)

        # ── Scope-based candidate restriction ────────────────────────────
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        candidate_ids = list(scope_allowed) if scope_allowed is not None else None

        # ── Load and validate uploaded image ─────────────────────────────
        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            raise HTTPException(
                status_code=400,
                detail="Uploaded file must be an image.",
            )

        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        try:
            pil_image = Image.open(BytesIO(raw_bytes)).convert("RGB")
        except Exception as exc:
            logger.warning(
                "likeness-search: could not open uploaded image (%s bytes): %s",
                len(raw_bytes),
                exc,
            )
            raise HTTPException(
                status_code=400, detail="Could not decode uploaded image."
            ) from exc

        # ── Encode query image ───────────────────────────────────────────
        query_emb = _encode_query_image(server, pil_image)

        # ── Fetch candidate embeddings from DB ───────────────────────────
        candidates = _fetch_candidate_embeddings(server, candidate_ids)
        if not candidates:
            return []

        # ── Compute cosine similarities ──────────────────────────────────
        ids_arr = np.array([c[0] for c in candidates], dtype=np.int64)
        emb_matrix = np.stack([c[1] for c in candidates])  # (N, D)
        similarities = emb_matrix @ query_emb  # (N,) — both sides already normalised

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
