"""Endpoint for reverse-image face search against the vault.

POST /pictures/face-search

The query is either one or more image uploads (the dominant face of each is
extracted), or a stored ``source_picture_id`` / ``source_face_id`` /
``source_character_id``.  The endpoint returns picture IDs ranked by ArcFace
cosine similarity.  A picture's score is its *best* face match (max over faces),
which surfaces pictures where the queried person appears regardless of how many
other people are also in them; that winning face travels back as ``face_id``.

When the query carries several embeddings, each candidate *face* is scored
against every query embedding and combined per the ``combine`` parameter before
the picture's best face is chosen.

``source_character_id`` queries with a character's reference faces, which is how
the UI finds more pictures of a person; ``exclude_character_id`` drops the ones
already assigned to them so the result set is only the un-assigned candidates.
``include_reference_scores`` additionally returns the winning face's similarity
to *each* reference, which is what lets a caller ask "how many of this person's
reference faces agree?" without a second round trip.
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
    face_id: int | None = None
    # The winning face's similarity to each query embedding, in query order.
    # Only populated when `include_reference_scores` is set; `likeness` is the
    # `combine` of these, so for `combine=max` it is exactly their maximum.
    reference_likeness: list[float] | None = None


def _score_best_faces(
    query_embeddings: list[np.ndarray],
    candidates: list[tuple[int, list[tuple[int, np.ndarray]]]],
    combine: str,
) -> tuple[list[int], np.ndarray, list[int], np.ndarray]:
    """Score every candidate picture by its best-matching face.

    Combines across queries **per face** and only then takes the maximum over a
    picture's faces.  The reverse order (max over faces first, combine second)
    lets different faces satisfy different queries, which makes ``combine=min``
    mean something other than its documented "must match all query images" -
    and leaves no single face to name as the winner.  For a single query
    embedding the two orders are identical.

    Faces whose embedding width differs from the query's are skipped: a vault
    that has been through a face-model change (``FaceModelRefreshTask``) holds
    embeddings of two widths, and a cosine between them is not a similarity at
    all.  Only pictures with at least one comparable face are returned, so a
    picture is dropped rather than scored against the wrong basis.

    Args:
        query_embeddings: Normalised query embeddings (each shape ``(D,)``).
        candidates: ``(picture_id, [(face_id, embedding), ...])`` pairs.
        combine: A member of ``VALID_COMBINE_MODES``.

    Returns:
        ``(picture_ids, scores, face_ids, per_query)``: one entry each,
        aligned, in the order the comparable candidates were seen.  ``scores``
        is a float32 array in ``[-1, 1]``; ``per_query`` is the ``(P, Q)``
        float32 array of the winning face's similarity to every query
        embedding, from which ``scores`` is the ``combine``.  Keeping the
        un-combined row is what lets a caller ask how *many* queries a match
        satisfies, not merely how well it satisfies the best one.
    """
    valid_queries = [
        np.asarray(embedding, dtype=np.float32)
        for embedding in query_embeddings
        if np.asarray(embedding).ndim == 1 and np.asarray(embedding).size > 0
    ]
    widths = sorted({int(embedding.shape[0]) for embedding in valid_queries})
    if not valid_queries:
        raise HTTPException(
            status_code=422,
            detail="No valid one-dimensional face embeddings were available.",
        )
    if len(widths) != 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "Face references use incompatible embedding widths "
                f"{widths}; finish face-model refresh or choose one compatible face."
            ),
        )

    query_matrix = np.stack(valid_queries).astype(np.float32)  # (Q, D)
    query_matrix /= np.maximum(
        np.linalg.norm(query_matrix, axis=1, keepdims=True), 1e-8
    )
    query_dim = query_matrix.shape[1]

    face_ids: list[int] = []
    pic_index: list[int] = []
    embeddings: list[np.ndarray] = []
    picture_ids: list[int] = []
    skipped_dim_mismatch = 0

    for pic_id, faces in candidates:
        comparable = [
            (fid, np.asarray(emb, dtype=np.float32))
            for fid, emb in faces
            if np.asarray(emb).ndim == 1 and np.asarray(emb).shape[0] == query_dim
        ]
        skipped_dim_mismatch += len(faces) - len(comparable)
        if not comparable:
            continue
        picture_ids.append(int(pic_id))
        for face_id, emb in comparable:
            face_ids.append(face_id)
            pic_index.append(len(picture_ids) - 1)
            embeddings.append(emb)

    if skipped_dim_mismatch:
        logger.warning(
            "face-search: skipped %d face embedding(s) whose width differs from the "
            "query's (%d); they were produced by a different face model and cannot be "
            "compared. Re-run face extraction to make those pictures searchable.",
            skipped_dim_mismatch,
            query_dim,
        )

    if not embeddings:
        return (
            [],
            np.empty(0, dtype=np.float32),
            [],
            np.empty((0, len(valid_queries)), dtype=np.float32),
        )

    # One matmul over every candidate face beats a per-picture Python loop: a
    # mature vault holds six figures of faces and this endpoint is interactive.
    face_matrix = np.stack(embeddings).astype(np.float32)  # (F, D)
    face_matrix /= np.maximum(np.linalg.norm(face_matrix, axis=1, keepdims=True), 1e-8)

    sims = np.clip(face_matrix @ query_matrix.T, -1.0, 1.0)  # (F, Q)
    face_scores = combine_likeness_scores(sims.T, combine)  # (F,)

    pic_index_arr = np.asarray(pic_index, dtype=np.int64)
    # Group faces by picture, best score first inside each group, then keep the
    # first of each group: that row is both the picture's score and its winner.
    order = np.lexsort((-face_scores, pic_index_arr))
    grouped = pic_index_arr[order]
    is_first = np.ones(grouped.shape[0], dtype=bool)
    is_first[1:] = grouped[1:] != grouped[:-1]
    best = order[is_first]

    scores = np.zeros(len(picture_ids), dtype=np.float32)
    best_face_ids = [0] * len(picture_ids)
    # The winning face's whole similarity row, not just its combined score: the
    # combine collapses Q numbers into one and there is no way back from it.
    per_query = np.zeros((len(picture_ids), query_matrix.shape[0]), dtype=np.float32)
    for row in best:
        slot = int(pic_index_arr[row])
        scores[slot] = face_scores[row]
        best_face_ids[slot] = face_ids[row]
        per_query[slot] = sims[row]

    return picture_ids, scores, best_face_ids, per_query


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
            "appears alongside others are still found accurately.  That winning face "
            "is reported as `face_id`, so a caller assigning the results to a "
            "character does not have to redo the comparison.\n\n"
            "When the query has several embeddings (several uploaded images, a "
            "`source_picture_id` with several faces, or a `source_character_id`'s "
            "reference faces), each candidate *face* is scored against every query "
            "embedding and combined with the ``combine`` strategy before the best "
            "face is picked.  Images with no detectable face are skipped; returns 422 "
            "when no face is detected in any image.\n\n"
            "`source_character_id` searches with a character's reference faces, which "
            "is how you find more pictures of a person you have already started "
            "tagging; pair it with `exclude_character_id` to leave out the pictures "
            "already assigned to them.\n\n"
            "`include_reference_scores` adds `reference_likeness` to each match: the "
            "winning face's similarity to every query embedding, in query order. "
            "Since `likeness` is the `combine` of that row, it answers how *well* a "
            "match scores; `reference_likeness` is what answers how *many* of the "
            "references it satisfies, and it costs no extra work to compute.\n\n"
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
        source_character_id: int | None = Query(
            None,
            description=(
                "Use this character's reference faces as the query, so the search "
                "finds more pictures of that person. Combine defaults to `max` for "
                "this source (a match to any one reference is enough)."
            ),
        ),
        exclude_character_id: int | None = Query(
            None,
            description=(
                "Drop pictures that already contain a face assigned to this "
                "character. Pair it with `source_character_id` to search for only "
                "the pictures of that person you have not assigned yet."
            ),
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
        combine: str | None = Query(
            None,
            description=(
                "How to combine scores when the query has several embeddings. "
                "One of: mean, max, min, harmonic_mean, geometric_mean. "
                "Defaults to `max` for `source_character_id` and `mean` otherwise."
            ),
        ),
        include_reference_scores: bool = Query(
            False,
            description=(
                "Include `reference_likeness` on every match: the winning face's "
                "similarity to each query embedding, in query order. Lets a caller "
                "filter on how many references a match satisfies without refetching."
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
        engine = getattr(server.vault, "_engine", None)
        active_model_pack = getattr(engine, "insightface_model_pack", None) or getattr(
            server.vault, "_insightface_model_pack", None
        )

        # A character query carries up to 10 reference faces of the same person
        # shot years and angles apart. Their mean is nobody, so a good match to
        # one reference must not be averaged away: default this source to `max`.
        if combine is None:
            combine = "max" if source_character_id is not None else "mean"
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

        # ── Already-assigned exclusion ─────────────────────────────────────
        # "More pictures of this person" must not re-list the ones already
        # assigned: they inflate every count the caller shows and the assignment
        # endpoint would skip them anyway. Subtracted from the fetched candidates
        # rather than intersected into `filter_candidate_ids`, because `None`
        # there means "unrestricted" and has no set to subtract from.
        excluded_picture_ids: set[int] = set()
        if exclude_character_id is not None:
            excluded_picture_ids = search_query_service.fetch_character_candidate_ids(
                server.vault.db, exclude_character_id
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
        source_ids_given = [
            name
            for name, value in (
                ("source_picture_id", source_picture_id),
                ("source_face_id", source_face_id),
                ("source_character_id", source_character_id),
            )
            if value is not None
        ]
        if not has_files and not source_ids_given:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provide either 'source_picture_id', 'source_face_id', "
                    "'source_character_id', or upload at least one image file."
                ),
            )
        if has_files and source_ids_given:
            raise HTTPException(
                status_code=400,
                detail="Provide either uploaded files or a source ID, not both.",
            )
        if len(source_ids_given) > 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provide exactly one source ID; got "
                    f"{', '.join(sorted(source_ids_given))}."
                ),
            )

        # ── Build query embeddings ─────────────────────────────────────────
        query_embeddings: list[np.ndarray] = []

        if source_character_id is not None:
            source_embs = search_query_service.fetch_character_reference_embeddings(
                server.vault.db, source_character_id, active_model_pack
            )
            if not source_embs:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Character {source_character_id} has no reference face with "
                        "a stored embedding to search with."
                    ),
                )

            for emb in source_embs:
                emb = emb.astype(np.float32)
                norm = np.linalg.norm(emb)
                if norm > 1e-8:
                    emb = emb / norm
                query_embeddings.append(emb)

        elif source_face_id is not None:
            source_embs = search_query_service.fetch_face_embedding_by_face_id(
                server.vault.db, source_face_id, active_model_pack
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
                server.vault.db, source_picture_id, active_model_pack
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
            # yet initialised, and returns list[list[FaceResult]] - one per image.
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
            server.vault.db, candidate_ids, active_model_pack
        )
        if excluded_picture_ids:
            candidates = [
                entry for entry in candidates if entry[0] not in excluded_picture_ids
            ]
        if not candidates:
            return []

        # ── Score each picture by its best-matching face ───────────────────
        pic_ids, combined, best_face_ids, per_query = _score_best_faces(
            query_embeddings, candidates, combine
        )
        if not pic_ids:
            return []

        # Apply threshold
        mask = combined >= threshold
        filtered_ids = [pic_ids[i] for i in range(len(pic_ids)) if mask[i]]
        filtered_faces = [best_face_ids[i] for i in range(len(pic_ids)) if mask[i]]
        filtered_scores = combined[mask]
        filtered_per_query = per_query[mask]

        if not filtered_ids:
            return []

        # Sort descending by combined score
        order = np.argsort(filtered_scores)[::-1]
        sorted_ids = [filtered_ids[i] for i in order]
        sorted_faces = [filtered_faces[i] for i in order]
        sorted_scores = filtered_scores[order]
        sorted_per_query = filtered_per_query[order]

        # ── Select results ────────────────────────────────────────────────
        effective_pool = top_n if not use_random or pool_m <= 0 else pool_m
        pool_ids = sorted_ids[:effective_pool]
        pool_faces = sorted_faces[:effective_pool]
        pool_scores = sorted_scores[:effective_pool]
        pool_per_query = sorted_per_query[:effective_pool]

        if use_random and pool_m > 0 and len(pool_ids) > top_n:
            indices = _random.sample(range(len(pool_ids)), top_n)
            indices.sort(key=lambda i: -pool_scores[i])
            pool_ids = [pool_ids[i] for i in indices]
            pool_faces = [pool_faces[i] for i in indices]
            pool_scores = pool_scores[indices]
            pool_per_query = pool_per_query[indices]
        else:
            pool_ids = pool_ids[:top_n]
            pool_faces = pool_faces[:top_n]
            pool_scores = pool_scores[:top_n]
            pool_per_query = pool_per_query[:top_n]

        results = [
            {
                "picture_id": pid,
                "likeness": round(float(score), 6),
                "face_id": face_id,
            }
            for pid, face_id, score in zip(pool_ids, pool_faces, pool_scores)
        ]
        if include_reference_scores:
            # 4 decimals, not 6: this is Q floats per row over up to 500 rows and
            # the consumer compares it against a slider quantised to 1%.
            for entry, row in zip(results, pool_per_query):
                entry["reference_likeness"] = [round(float(v), 4) for v in row]
        return results
