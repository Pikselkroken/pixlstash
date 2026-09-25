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

from pixlstash.authz.membership import enforce_set_scope
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
    cohesion: float | None = None
    tags_matched: int | None = None
    tags_total: int | None = None


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


def _leave_one_out_cohesion(member_matrix: np.ndarray) -> float | None:
    """Median similarity of each member to the centroid of the OTHER members.

    Leave-one-out, because a member's similarity to a centroid it helped build
    is inflated, and the smaller the set the worse: a one-member set would score
    1.0 and a two-member set sits above its own pairwise similarity, seating
    any cut built on it above every outside candidate. ``None`` for a single
    member, which has nothing to compare against.
    """
    if member_matrix.shape[0] < 2:
        return None
    others = member_matrix.sum(axis=0) - member_matrix  # (M, D)
    norms = np.linalg.norm(others, axis=1)
    norms[norms == 0] = 1.0
    sims = np.einsum("ij,ij->i", member_matrix, others) / norms
    return round(float(np.median(sims)), 6)


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
            "**Suggesting pictures for a set**\n"
            "- `source_set_id` queries with the set's centroid: the normalised mean "
            "of its members' embeddings. Every match carries `cohesion`, the median "
            "similarity of each member to the centroid of the others (absent for a "
            "one-picture set), so a caller can "
            'seat its cut at "as alike as a typical member".\n'
            "- `exclude_set_id` drops pictures already in that set.\n"
            "- `include_tag_counts` (with `source_set_id`) adds `tags_matched` and "
            "`tags_total`: how many of the set's signature tags (those on at least "
            "half its members) the match carries.\n\n"
            "Results are ordered by descending similarity score. "
            "Only pictures with a pre-computed image embedding are considered."
        ),
        response_model=list[ImageLikenessMatchResponse],
        response_model_exclude_none=True,
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
        source_set_id: int | None = Query(
            None,
            description=(
                "Use this set's centroid (the mean of its members' embeddings) as "
                "the query, so the search finds more pictures that belong in it."
            ),
        ),
        exclude_set_id: int | None = Query(
            None,
            description=(
                "Drop pictures already in this set. Pair it with `source_set_id` "
                "to search for only the pictures not yet added."
            ),
        ),
        include_tag_counts: bool = Query(
            False,
            description=(
                "With `source_set_id`, add `tags_matched` and `tags_total` to every "
                "match: how many of the set's signature tags it carries."
            ),
        ),
    ):
        # ── Authentication ────────────────────────────────────────────────
        server.auth.require_user_id(request)

        # A scoped token must not learn anything about a set outside its scope,
        # not even whether it exists or has members: check before any
        # membership query so the 422 below cannot become an existence oracle.
        for scoped_set_id in (source_set_id, exclude_set_id):
            if scoped_set_id is not None:
                enforce_set_scope(server, request, scoped_set_id)

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

        # ── Already-in-set exclusion ──────────────────────────────────────
        # Subtracted from the fetched candidates rather than intersected into
        # `filter_candidate_ids`, because `None` there means "unrestricted" and
        # has no set to subtract from.
        excluded_picture_ids: set[int] = set()
        if exclude_set_id is not None:
            excluded_picture_ids = search_query_service.fetch_set_member_ids(
                server.vault.db, exclude_set_id
            )

        # ── Load and validate query embeddings ──────────────────────────
        # Merge source_picture_ids and the legacy single source_picture_id.
        effective_source_ids: list[int] = list(source_picture_ids)
        if (
            source_picture_id is not None
            and source_picture_id not in effective_source_ids
        ):
            effective_source_ids.insert(0, source_picture_id)

        if source_set_id is not None and (effective_source_ids or files):
            raise HTTPException(
                status_code=400,
                detail="Provide either 'source_set_id', source picture IDs or uploaded files, not more than one.",
            )

        cohesion: float | None = None
        set_member_ids: set[int] = set()
        if source_set_id is not None:
            set_member_ids = search_query_service.fetch_set_member_ids(
                server.vault.db, source_set_id
            )
            member_embeddings = [
                emb
                for _pic_id, emb in search_query_service.fetch_candidate_clip_embeddings(
                    server.vault.db, list(set_member_ids)
                )
            ]
            if not member_embeddings:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Set {source_set_id} has no picture with a stored "
                        "embedding to search with."
                    ),
                )
            # One query vector: for unit vectors the mean of the cosines to every
            # member equals the cosine to their mean, so the centroid is exact
            # `combine="mean"` at a fraction of the cost.
            #
            # A set whose members span a CLIP model change holds two widths, and
            # a mean across them is meaningless: keep the most common width.
            widths = [emb.shape[0] for emb in member_embeddings]
            width = max(set(widths), key=widths.count)
            if len(set(widths)) > 1:
                logger.warning(
                    "likeness-search: set %d mixes embedding widths %s; building "
                    "its centroid from the %d members of width %d only",
                    source_set_id,
                    sorted(set(widths)),
                    widths.count(width),
                    width,
                )
            member_matrix = np.stack(
                [emb for emb in member_embeddings if emb.shape[0] == width]
            ).astype(np.float32)
            centroid = member_matrix.mean(axis=0)
            norm = float(np.linalg.norm(centroid))
            if norm > 0:
                centroid = centroid / norm
            cohesion = _leave_one_out_cohesion(member_matrix)
            query_embeddings = [centroid]
        elif effective_source_ids:
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
                detail="Provide either 'source_picture_id', 'source_picture_ids', 'source_set_id', or upload at least one image file.",
            )

        # ── Fetch candidate embeddings from DB ───────────────────────────
        candidates = search_query_service.fetch_candidate_clip_embeddings(
            server.vault.db, candidate_ids
        )
        if excluded_picture_ids:
            candidates = [c for c in candidates if c[0] not in excluded_picture_ids]
        # A vault that has been through a CLIP model change holds two widths,
        # and a cosine between them is not a similarity (the face search skips
        # them the same way).
        query_width = query_embeddings[0].shape[0]
        mismatched = sum(1 for c in candidates if c[1].shape[0] != query_width)
        if mismatched:
            logger.warning(
                "likeness-search: skipping %d candidate pictures whose embedding "
                "width differs from the query's %d",
                mismatched,
                query_width,
            )
            candidates = [c for c in candidates if c[1].shape[0] == query_width]
        if not candidates:
            return []

        # ── Compute cosine similarities ──────────────────────────────────
        ids_arr = np.array([c[0] for c in candidates], dtype=np.int64)
        emb_matrix = np.stack([c[1] for c in candidates])  # (N, D)
        query_matrix = np.stack(query_embeddings)  # (Q, D)

        # (N, Q) - one similarity per candidate per query
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

        matches = [
            {"picture_id": int(pic_id), "likeness": round(float(sim), 6)}
            for pic_id, sim in zip(ids_pool, sim_pool)
        ]
        if source_set_id is not None:
            for match in matches:
                match["cohesion"] = cohesion
            if include_tag_counts:
                # Counted on the returned matches only, never the whole library.
                signature_tags = search_query_service.fetch_signature_tags(
                    server.vault.db, set_member_ids
                )
                tag_counts = search_query_service.fetch_tag_counts_for_pictures(
                    server.vault.db,
                    [m["picture_id"] for m in matches],
                    signature_tags,
                )
                for match in matches:
                    match["tags_matched"] = tag_counts.get(match["picture_id"], 0)
                    match["tags_total"] = len(signature_tags)
        return matches
