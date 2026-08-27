"""Character-likeness scoring endpoints.

- ``GET /pictures/{id}/character_likeness`` scores one stored picture's faces
  against a reference character.
- ``POST /pictures/character_likeness/batch`` scores many stored pictures in one
  request, matching the single-id endpoint id-for-id.
- ``POST /pictures/score_character_likeness`` scores uploaded images in-memory
  against a reference character without persisting anything.

Object scope: the single-id GET is a per-object data endpoint declared
``PICTURE_SCOPED`` in ``pixlstash/authz/registry.py`` (gated centrally). The batch
and upload endpoints filter inline via ``fetch_scope_allowed_picture_ids`` /
``fetch_scope_allowed_character_ids``.
"""

import asyncio
from io import BytesIO
from typing import Optional

import cv2
import numpy as np
from PIL import Image
from fastapi import Body, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Face, Picture, PictureSetMember
from pixlstash.pixl_logging import get_logger
from pixlstash.scoring import (
    compute_character_likeness_for_faces,
    find_pictures_by_character_likeness_sql,
    pack_reference_blobs,
    select_reference_faces_for_character,
)
from pixlstash.utils.service.filter_helpers import (
    VALID_COMBINE_MODES,
    fetch_scope_allowed_character_ids,
    fetch_scope_allowed_picture_ids,
)

from ._faces import _DetectedFace


logger = get_logger(__name__)


# Upper bound on picture_ids per batch character-likeness request. Bounds the
# work of a single request (one SQL scoring pass plus a handful of grouped
# eligibility queries over the id set). Requests over this cap are rejected
# rather than silently truncated.
BATCH_CHARACTER_LIKENESS_MAX_IDS = 1000


class ScoreCharacterLikenessResultEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int
    character_likeness: Optional[float] = None
    eligible: bool


class ScoreCharacterLikenessResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    reference_character_id: int
    results: list[ScoreCharacterLikenessResultEntry]


class PictureCharacterLikenessResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    picture_id: int
    character_likeness: Optional[float] = None
    eligible: bool
    ready: bool = Field(
        ...,
        description=(
            "True when face extraction has completed for this picture and "
            "`character_likeness` is the final score; the client must stop "
            "polling even if the value is 0.0 or null. False means face "
            "extraction is still pending and the client may poll again."
        ),
    )


class BatchCharacterLikenessRequest(BaseModel):
    """Request body for the batch character-likeness endpoint.

    Lets a client score many pictures against a single reference character in
    one request instead of one request per picture per poll cycle.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "reference_character_id": 12,
                "picture_ids": [101, 102, 103],
                "character_id": "ALL",
            }
        },
    )

    reference_character_id: int = Field(
        ...,
        description=(
            "Id of the character whose reference faces define the likeness "
            "target. Each picture's faces are scored against this character's "
            "reference faces; the picture's score is the maximum across its "
            "faces."
        ),
    )
    picture_ids: list[int] = Field(
        ...,
        description=(
            "Picture ids to score. Must contain at least one id and at most "
            "1000. Every requested id is echoed back in `results`, including "
            "ids that are missing, deleted, or ineligible (deny-by-default), "
            "so a client can match results to its request positionally or by "
            "id."
        ),
    )
    character_id: Optional[str] = Field(
        None,
        description=(
            "Optional candidate-face filter, mirroring the single-id endpoint. "
            "Omit (or null) / 'ALL' to score every face on each picture; "
            "'UNASSIGNED' to score only faces not assigned to any character "
            "(and only for pictures that have no assigned face and are in no "
            "picture set); or a character id (as a string) to score only "
            "faces assigned to that character."
        ),
    )


class BatchCharacterLikenessResult(BaseModel):
    """Per-picture result, identical in meaning to the single-id endpoint."""

    model_config = ConfigDict(extra="forbid")

    picture_id: int = Field(
        ...,
        description="The picture id this result corresponds to.",
    )
    character_likeness: Optional[float] = Field(
        None,
        description=(
            "Maximum likeness score across the picture's scored faces, in "
            "[0, 1]. Null when the picture is ineligible (missing, deleted, "
            "out of scope, or excluded by the `character_id` filter)."
        ),
    )
    eligible: bool = Field(
        ...,
        description=(
            "True when the picture is a valid candidate for this reference "
            "character under the requested `character_id` filter. False for "
            "missing/deleted/out-of-scope pictures and for the ineligible "
            "cases the single-id endpoint also reports as ineligible."
        ),
    )
    ready: bool = Field(
        ...,
        description=(
            "True when face extraction has completed for this picture and "
            "`character_likeness` is final (stop polling, even if the value is "
            "0.0 or null). False means face extraction is still pending and "
            "the client may poll this id again."
        ),
    )


class BatchCharacterLikenessResponse(BaseModel):
    """Batch character-likeness response.

    `results` contains exactly one entry per requested picture id, in the same
    order they were requested.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "reference_character_id": 12,
                "results": [
                    {
                        "picture_id": 101,
                        "character_likeness": 0.87,
                        "eligible": True,
                        "ready": True,
                    },
                    {
                        "picture_id": 102,
                        "character_likeness": 0.0,
                        "eligible": True,
                        "ready": False,
                    },
                    {
                        "picture_id": 103,
                        "character_likeness": None,
                        "eligible": False,
                        "ready": True,
                    },
                ],
            }
        },
    )

    reference_character_id: int = Field(
        ...,
        description="The reference character id the batch was scored against.",
    )
    results: list[BatchCharacterLikenessResult] = Field(
        ...,
        description=(
            "One result per requested picture id, in request order. Every "
            "requested id appears exactly once."
        ),
    )


def register_routes(router, server):
    @router.get(
        "/pictures/{id}/character_likeness",
        include_in_schema=False,
        summary="Get picture character likeness",
        description="Computes max character-likeness score for faces in a picture against a reference character.",
        response_model=PictureCharacterLikenessResponse,
    )
    def get_picture_character_likeness(
        request: Request,
        id: str,
        reference_character_id: int = Query(...),
        character_id: str = Query(None),
    ):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def fetch_picture_characters(session):
            pic = session.exec(
                select(Picture).where(
                    Picture.id == pic_id,
                    Picture.deleted.is_(False),
                )
            ).first()
            if not pic:
                return None
            char_ids = [c.id for c in pic.characters] if pic.characters else []
            return {"character_ids": char_ids}

        context = server.vault.db.run_task(fetch_picture_characters)
        if not context:
            raise HTTPException(status_code=404, detail="Picture not found")

        def has_assigned_faces(session):
            face = session.exec(
                select(Face.id).where(
                    Face.picture_id == pic_id,
                    Face.character_id.is_not(None),
                )
            ).first()
            return face is not None

        def is_in_picture_set(session):
            member = session.exec(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.picture_id == pic_id
                )
            ).first()
            return member is not None

        if character_id == "UNASSIGNED" and (
            server.vault.db.run_task(has_assigned_faces)
            or server.vault.db.run_task(is_in_picture_set)
        ):
            # Eligibility-ineligible: a final answer, nothing more to compute.
            return {
                "picture_id": pic_id,
                "character_likeness": None,
                "eligible": False,
                "ready": True,
            }

        def fetch_face_ids(session):
            query = select(Face.id).where(Face.picture_id == pic_id)
            if character_id == "UNASSIGNED":
                query = query.where(Face.character_id.is_(None))
            elif character_id and character_id != "ALL":
                query = query.where(Face.character_id == int(character_id))
            return session.exec(query).all()

        face_ids = server.vault.db.run_task(fetch_face_ids)
        if not face_ids:
            if character_id and character_id not in ("ALL", "UNASSIGNED"):
                # Ineligible: the requested character has no faces here. Final.
                return {
                    "picture_id": pic_id,
                    "character_likeness": None,
                    "eligible": False,
                    "ready": True,
                }

            # Eligible but no faces matched: the genuinely ambiguous case.
            # Authoritative "extraction done" signal: the picture has at least
            # one Face row. MissingFaceExtractionFinder selects pictures with
            # ``~Picture.faces.any()``, and FaceExtractionTask always inserts a
            # row when it runs (a real face, or a sentinel face_index=-1,
            # bbox=None when none are detected). So zero Face rows means
            # extraction has not run yet (poll again); any Face row means it has
            # (score is final, stop polling).
            def has_any_face(session):
                return (
                    session.exec(
                        select(Face.id).where(Face.picture_id == pic_id)
                    ).first()
                    is not None
                )

            extraction_done = server.vault.db.run_task(has_any_face)
            return {
                "picture_id": pic_id,
                "character_likeness": 0.0,
                "eligible": True,
                "ready": extraction_done,
            }

        def fetch_faces(session, ids):
            return session.exec(select(Face).where(Face.id.in_(ids))).all()

        candidate_faces = server.vault.db.run_task(fetch_faces, face_ids)
        reference_faces = server.vault.db.run_task(
            select_reference_faces_for_character,
            int(reference_character_id),
            10,
            priority=DBPriority.IMMEDIATE,
        )
        likeness_map = compute_character_likeness_for_faces(
            reference_faces,
            candidate_faces,
        )
        if not likeness_map:
            # No reference faces for the reference character: a real 0.0. The
            # picture's own faces are already extracted (face_ids is non-empty),
            # so this is final.
            return {
                "picture_id": pic_id,
                "character_likeness": 0.0,
                "eligible": False,
                "ready": True,
            }
        score = 0.0
        for face_id in face_ids:
            score = max(score, float(likeness_map.get(face_id, 0.0)))

        # The picture has at least one extracted face and a score was computed.
        # Always final, even when the score is low (the reported bug).
        return {
            "picture_id": pic_id,
            "character_likeness": score,
            "eligible": True,
            "ready": True,
        }

    @router.post(
        "/pictures/character_likeness/batch",
        summary="Batch picture character likeness",
        description=(
            "Scores many pictures against one reference character in a single "
            "request, so a client polling N pictures makes one request per "
            "poll cycle instead of N. Each result's `character_likeness`, "
            "`eligible`, and `ready` mean exactly what the single-id "
            "`/pictures/{id}/character_likeness` endpoint returns for that id, "
            "including the same eligibility and deny-by-default rules: every "
            "requested id appears once in `results`, and a missing, deleted, "
            "out-of-scope, or otherwise-refused id yields "
            "`{character_likeness: null, eligible: false, ready: true}` "
            "without revealing whether the id exists."
        ),
        response_model=BatchCharacterLikenessResponse,
    )
    def get_pictures_character_likeness_batch(
        request: Request,
        payload: BatchCharacterLikenessRequest = Body(...),
    ):
        reference_character_id = payload.reference_character_id
        character_id = payload.character_id

        # Validation: non-empty and bounded. Preserve request order while
        # de-duplicating, then map answers back onto the original list so the
        # response is positionally aligned with what the client sent.
        if not payload.picture_ids:
            raise HTTPException(
                status_code=422,
                detail="picture_ids must contain at least one id",
            )
        if len(payload.picture_ids) > BATCH_CHARACTER_LIKENESS_MAX_IDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    "picture_ids exceeds the maximum of "
                    f"{BATCH_CHARACTER_LIKENESS_MAX_IDS} ids per request"
                ),
            )

        # `character_id` accepts the keywords ALL/UNASSIGNED (and null) or a
        # numeric character id as a string, matching the single-id endpoint.
        # Reject any other string with a 422 instead of letting int() 500 deep
        # in a query (the single-id endpoint's unguarded int() would 500 here).
        if character_id is not None and character_id not in ("ALL", "UNASSIGNED", ""):
            try:
                int(character_id)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "character_id must be 'ALL', 'UNASSIGNED', or a numeric "
                        "character id"
                    ),
                ) from None

        requested_ids = list(payload.picture_ids)
        unique_ids = list(dict.fromkeys(requested_ids))

        # Object-level scope enforcement BEFORE any DB read or return, so every
        # branch below is uniformly gated (mirrors the single-id sibling
        # get_picture_character_likeness, which is PICTURE_SCOPED via the authz gate).
        # fetch_scope_allowed_picture_ids returns None for unscoped/owner tokens
        # (full access) and a fail-closed set for scoped resource tokens.
        # Out-of-scope ids are dropped from the set we query and fall through to
        # deny_result in classify(), so they are indistinguishable from missing
        # ids (no existence/score leak, no per-id 403 oracle).
        allowed_ids = fetch_scope_allowed_picture_ids(server, request)
        if allowed_ids is not None:
            scoped_ids = [pid for pid in unique_ids if pid in allowed_ids]
        else:
            scoped_ids = unique_ids

        def deny_result(picture_id: int) -> dict:
            # Deny-by-default: indistinguishable from an out-of-scope/ineligible
            # picture, so the response never leaks whether the id exists.
            return {
                "picture_id": picture_id,
                "character_likeness": None,
                "eligible": False,
                "ready": True,
            }

        # One grouped pass collects every per-id signal the single-id endpoint
        # derives, so the batch matches it id-for-id without a per-id loop:
        #   live_ids        - exists and not soft-deleted (else 404 -> deny)
        #   ids_with_faces  - has >=1 Face row (the authoritative `ready` signal)
        #   assigned_ids    - has a Face assigned to some character
        #   in_set_ids      - is a member of a picture set
        #   matched_ids     - has >=1 Face matching the character_id filter
        def gather_signals(session: Session, ids: list[int]):
            live_ids = {
                row
                for row in session.exec(
                    select(Picture.id)
                    .where(Picture.id.in_(ids))
                    .where(Picture.deleted.is_(False))
                ).all()
                if row is not None
            }
            ids_with_faces = {
                row
                for row in session.exec(
                    select(Face.picture_id)
                    .where(Face.picture_id.in_(ids))
                    .group_by(Face.picture_id)
                ).all()
                if row is not None
            }
            assigned_ids = {
                row
                for row in session.exec(
                    select(Face.picture_id)
                    .where(
                        Face.picture_id.in_(ids),
                        Face.character_id.is_not(None),
                    )
                    .group_by(Face.picture_id)
                ).all()
                if row is not None
            }
            in_set_ids = {
                row
                for row in session.exec(
                    select(PictureSetMember.picture_id)
                    .where(PictureSetMember.picture_id.in_(ids))
                    .group_by(PictureSetMember.picture_id)
                ).all()
                if row is not None
            }

            # Faces matching the same character filter the single-id endpoint
            # applies in fetch_face_ids. `matched_ids` mirrors a non-empty
            # fetch_face_ids; `scorable_match_ids` additionally requires a
            # matching face with usable features, mirroring single-id's test
            # that compute_character_likeness_for_faces produced a non-empty
            # map (a sentinel/featureless-only picture matches but cannot be
            # scored, so single-id reports it ineligible).
            def _match_query():
                q = select(Face.picture_id).where(Face.picture_id.in_(ids))
                if character_id == "UNASSIGNED":
                    q = q.where(Face.character_id.is_(None))
                elif character_id and character_id not in ("ALL", ""):
                    q = q.where(Face.character_id == int(character_id))
                return q

            matched_ids = {
                row
                for row in session.exec(_match_query().group_by(Face.picture_id)).all()
                if row is not None
            }
            scorable_match_ids = {
                row
                for row in session.exec(
                    _match_query()
                    .where(Face.features.is_not(None))
                    .group_by(Face.picture_id)
                ).all()
                if row is not None
            }
            return (
                live_ids,
                ids_with_faces,
                assigned_ids,
                in_set_ids,
                matched_ids,
                scorable_match_ids,
            )

        (
            live_ids,
            ids_with_faces,
            assigned_ids,
            in_set_ids,
            matched_ids,
            scorable_match_ids,
        ) = server.vault.db.run_task(gather_signals, scoped_ids)

        # Determine whether the reference character has any usable reference
        # faces at all. When it does not, the single-id endpoint reports an
        # eligible picture's faces as ready with a real 0.0 score, and reports
        # `eligible: false` only once a score "would have" been computed (its
        # likeness_map is empty). We mirror that below.
        reference_faces = server.vault.db.run_task(
            select_reference_faces_for_character,
            int(reference_character_id),
            10,
            priority=DBPriority.IMMEDIATE,
        )
        has_reference = bool(reference_faces) and (
            pack_reference_blobs(reference_faces) is not None
        )

        # Single SQL scoring pass over the live candidate ids that have a
        # matching face with usable features. Reuses the registered
        # character_face_likeness scalar (identical algorithm to the single-id
        # path's compute_character_likeness_for_faces), so scores are
        # bit-for-bit equal.
        scorable_ids = [
            pid for pid in scoped_ids if pid in live_ids and pid in scorable_match_ids
        ]
        likeness_by_id: dict[int, float] = {}
        if scorable_ids and has_reference:
            scored = find_pictures_by_character_likeness_sql(
                server,
                character_id,
                int(reference_character_id),
                offset=0,
                limit=len(scorable_ids),
                descending=True,
                candidate_ids=scorable_ids,
            )
            for entry in scored:
                entry_id = entry.get("id")
                if entry_id is None:
                    continue
                likeness_by_id[int(entry_id)] = max(
                    0.0, float(entry.get("character_likeness", 0.0) or 0.0)
                )

        def classify(pid: int) -> dict:
            # Mirrors get_picture_character_likeness exactly, per id.
            if pid not in live_ids:
                # Not found or soft-deleted -> single-id raises 404. Deny.
                return deny_result(pid)

            ready = pid in ids_with_faces

            if character_id == "UNASSIGNED" and (
                pid in assigned_ids or pid in in_set_ids
            ):
                # Eligibility-ineligible: a final answer.
                return {
                    "picture_id": pid,
                    "character_likeness": None,
                    "eligible": False,
                    "ready": True,
                }

            if pid not in matched_ids:
                if character_id and character_id not in ("ALL", "UNASSIGNED"):
                    # The requested character has no faces here. Final, ineligible.
                    return {
                        "picture_id": pid,
                        "character_likeness": None,
                        "eligible": False,
                        "ready": True,
                    }
                # Eligible but no faces matched: ambiguous case. `ready` follows
                # the authoritative has-any-Face-row signal.
                return {
                    "picture_id": pid,
                    "character_likeness": 0.0,
                    "eligible": True,
                    "ready": ready,
                }

            if not has_reference or pid not in scorable_match_ids:
                # Single-id's likeness_map is empty here: either the reference
                # character has no usable reference faces, or none of this
                # picture's matching faces have features to score (e.g. a
                # detection sentinel). Either way a real 0.0, and final because
                # the picture already has at least one extracted Face row.
                return {
                    "picture_id": pid,
                    "character_likeness": 0.0,
                    "eligible": False,
                    "ready": True,
                }

            # Matched, scorable faces and a computable score: always final.
            return {
                "picture_id": pid,
                "character_likeness": likeness_by_id.get(pid, 0.0),
                "eligible": True,
                "ready": True,
            }

        per_id = {pid: classify(pid) for pid in unique_ids}
        results = [per_id[pid] for pid in requested_ids]

        return {
            "reference_character_id": int(reference_character_id),
            "results": results,
        }

    @router.post(
        "/pictures/score_character_likeness",
        summary="Score uploaded images by character likeness",
        description=(
            "Scores one or more uploaded images by how closely a detected face "
            "matches a reference character, without importing or persisting "
            "anything. Faces are detected in-memory on the GPU face queue and "
            "compared against the reference character's reference faces with "
            "cosine similarity. By default (`combine=softmax`) the aggregation "
            "matches the stored-picture `character_likeness` endpoints, so the "
            "scores are directly comparable; see `combine` to change how a "
            "character's multiple reference faces are aggregated.\n\n"
            "Returns one result per uploaded file, in upload order, as "
            "`{index, character_likeness, eligible}`:\n"
            "- `index`: 0-based position of the file in the request.\n"
            "- `character_likeness`: the maximum likeness [0-1] over the faces "
            "detected in that image, or `null` when the frame is not "
            "scorable.\n"
            "- `eligible`: `false` when the image has no detectable face, or "
            "the reference character has no usable reference faces to score "
            "against (the score is then `null`); otherwise `true`.\n\n"
            "Intended for quality gates (e.g. the ComfyUI Face Likeness Gate) "
            "that score generated frames against a character without polluting "
            "the vault - no tagging, captioning, embedding, or vault-wide "
            "likeness work is triggered."
        ),
        response_model=ScoreCharacterLikenessResponse,
    )
    async def score_character_likeness(
        request: Request,
        files: list[UploadFile] = File(
            ...,
            description="Images to score against the reference character.",
        ),
        reference_character_id: int = Form(
            ...,
            description="Character to score each uploaded image's face(s) against.",
        ),
        combine: str = Form(
            "softmax",
            description=(
                "How to aggregate each frame's face similarity across the "
                "reference character's multiple reference faces. `softmax` "
                "(default) is the legacy softmax-weighted average and keeps "
                "scores directly comparable with the stored-picture "
                "`character_likeness` endpoints. `max` accepts a frame that "
                "matches any single reference face (lenient gate), `min` "
                "requires matching every reference face (strict), `mean` is the "
                "plain average; `harmonic_mean` and `geometric_mean` are also "
                "accepted."
            ),
        ),
    ):
        # ── Authentication & scope ────────────────────────────────────────
        server.auth.require_user_id(request)
        combine_mode = (combine or "softmax").strip().lower()
        allowed_combine = VALID_COMBINE_MODES | {"softmax"}
        if combine_mode not in allowed_combine:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid combine '{combine}'. "
                    f"Valid values: {', '.join(sorted(allowed_combine))}."
                ),
            )
        scope_allowed = fetch_scope_allowed_character_ids(server, request)
        if (
            scope_allowed is not None
            and int(reference_character_id) not in scope_allowed
        ):
            raise HTTPException(
                status_code=403,
                detail="Token does not have access to the reference character.",
            )

        if not files:
            raise HTTPException(
                status_code=400, detail="At least one file must be uploaded."
            )

        # ── Decode uploads to BGR numpy arrays ────────────────────────────
        bgr_images: list[np.ndarray] = []
        for idx, file in enumerate(files):
            raw_bytes = await file.read()
            if not raw_bytes:
                raise HTTPException(
                    status_code=400,
                    detail=f"File {idx + 1}: uploaded file is empty.",
                )
            try:
                pil_image = Image.open(BytesIO(raw_bytes)).convert("RGB")
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"File {idx + 1}: could not decode uploaded image.",
                ) from exc
            bgr_images.append(cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR))

        # ── Reference faces for the character ─────────────────────────────
        reference_faces = server.vault.db.run_task(
            select_reference_faces_for_character,
            int(reference_character_id),
            10,
            priority=DBPriority.IMMEDIATE,
        )

        # No reference faces → nothing is scorable regardless of what the
        # uploaded frames contain, so short-circuit before doing any GPU face
        # detection (which also means the endpoint works without an inference
        # engine). Every frame is ineligible with a null score, mirroring the
        # stored-picture endpoints' empty-likeness_map case.
        if not reference_faces:
            return {
                "reference_character_id": int(reference_character_id),
                "results": [
                    {"index": idx, "character_likeness": None, "eligible": False}
                    for idx in range(len(bgr_images))
                ],
            }

        # ── Detect faces in-memory on the GPU queue (nothing persisted) ───
        from pixlstash.tasks.face_detection_task import FaceDetectionTask

        engine = getattr(server.vault, "_engine", None)
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Inference engine not available."
            )
        task_runner = getattr(server.vault, "_task_runner", None)
        if task_runner is None:
            raise HTTPException(status_code=503, detail="Task runner not available.")

        detection_task = FaceDetectionTask(engine, bgr_images)
        loop = asyncio.get_event_loop()
        # Detection is batched on the GPU; allow a little more headroom per image
        # so a large batch under load does not time out prematurely.
        timeout_s = max(60.0, 2.0 * len(bgr_images))
        try:
            all_face_results = await loop.run_in_executor(
                None, task_runner.submit_and_wait, detection_task, timeout_s
            )
        except TimeoutError as exc:
            logger.error("score_character_likeness: face detection timed out: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="Face detection timed out; the server may be under heavy load.",
            ) from exc
        except RuntimeError as exc:
            logger.error("score_character_likeness: face detection failed: %s", exc)
            raise HTTPException(
                status_code=503, detail="Face detection failed."
            ) from exc

        # ── Score each image: max likeness over its detected faces ────────
        results: list[dict] = []
        for idx, face_results in enumerate(all_face_results):
            candidate_faces = [
                _DetectedFace(face_i, fr.embedding)
                for face_i, fr in enumerate(face_results)
                if fr.embedding is not None
            ]
            if not candidate_faces:
                # No detectable face - nothing to score, so the frame is
                # ineligible and a gate rejects it regardless of threshold.
                results.append(
                    {"index": idx, "character_likeness": None, "eligible": False}
                )
                continue

            likeness_map = compute_character_likeness_for_faces(
                reference_faces, candidate_faces, combine=combine_mode
            )
            score = max(likeness_map.values(), default=0.0)
            results.append(
                {"index": idx, "character_likeness": float(score), "eligible": True}
            )

        return {
            "reference_character_id": int(reference_character_id),
            "results": results,
        }
