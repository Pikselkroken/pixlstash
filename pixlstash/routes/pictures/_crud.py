import asyncio
import base64
import os
import re
from datetime import datetime, timezone
from io import BytesIO
from email.utils import formatdate

import cv2
import numpy as np
from PIL import Image
from fastapi import (
    BackgroundTasks,
    Body,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    case,
    delete,
    func,
    update,
)
from sqlmodel import Session, select
from typing import Optional

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    DeletedFileLog,
    Face,
    Picture,
    PictureProjectMember,
    PictureSetMember,
    Project,
    ReferenceFolder,
    Tag,
)
from pixlstash.db_models.user import User
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.stacking import normalize_stack_positions
from pixlstash.picture_scoring import (
    compute_character_likeness_for_faces,
    find_pictures_by_character_likeness_sql,
    pack_reference_blobs,
    select_reference_faces_for_character,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.caption_utils import (
    serialize_tag_objects,
    sync_picture_sidecar,
)
from pixlstash.utils.service.filter_helpers import (
    fetch_scope_allowed_character_ids,
    fetch_scope_allowed_picture_ids,
    VALID_COMBINE_MODES,
)
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.utils.watermark import apply_watermark, get_watermark_bytes

from ._helpers import (
    MEDIA_TYPE_BY_FORMAT,
    enforce_picture_scope,
    _score_anchor_membership_changed,
)


logger = get_logger(__name__)

# Upper bound on picture_ids per batch character-likeness request. Bounds the
# work of a single request (one SQL scoring pass plus a handful of grouped
# eligibility queries over the id set). Requests over this cap are rejected
# rather than silently truncated.
BATCH_CHARACTER_LIKENESS_MAX_IDS = 1000


class _DetectedFace:
    """Adapter exposing an in-memory face detection as the ``(.id, .features)``
    shape ``compute_character_likeness_for_faces`` consumes.

    ``FaceResult.embedding`` (the normalised ArcFace vector from the recognition
    model) is the same value face extraction stores in ``Face.features`` as
    ``embedding.astype("float32").tobytes()``, so scoring an uploaded image this
    way is bit-for-bit identical to scoring a stored picture — without writing
    any ``Picture``/``Face`` rows.
    """

    __slots__ = ("id", "features")

    def __init__(self, face_id: int, embedding):
        self.id = face_id
        self.features = np.asarray(embedding, dtype=np.float32).tobytes()


class ScoreCharacterLikenessResultEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int
    character_likeness: Optional[float] = None
    eligible: bool


class ScoreCharacterLikenessResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    reference_character_id: int
    results: list[ScoreCharacterLikenessResultEntry]


class SetProjectForPicturesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    project_id: Optional[int] = None
    mode: str
    updated_ids: list[int] = []
    updated_count: int
    missing_ids: list[int] = []


class ApplyScoresResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    only_unscored: bool
    updated_ids: list[int] = []
    updated_count: int
    skipped_ids: list[int] = []
    skipped_count: int
    missing_ids: list[int] = []
    missing_count: int
    reset_triggered: bool


class PictureFullMetadataResponse(BaseModel):
    """Single picture's full metadata, tags, optional smart score, and any
    embedded file metadata. The picture model is large/dynamic so common
    fields are enumerated and ``extra="allow"`` preserves the rest."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    format: Optional[str] = None
    score: Optional[int] = None
    tags: Optional[list] = None
    smartScore: Optional[float] = None
    metadata: Optional[dict] = None


class PictureFaceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    picture_id: Optional[int] = None
    frame_index: Optional[int] = None
    face_index: Optional[int] = None
    bbox: Optional[list] = None
    character_id: Optional[int] = None


class FaceDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    message: str


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


class PicturePatchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    picture: dict


class ScrapheapRestoreResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    restored_count: int


class ScrapheapDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    deleted_count: int
    # Snapshots that still contain metadata for the just-purged pictures, each
    # ``{id, kind, label, created_at, matched_count}``. The archives are not
    # scrubbed; the user can delete these snapshots to erase that metadata.
    snapshots_with_deleted: Optional[list] = None


class PictureDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    message: str


def register_routes(router, server):
    @router.patch(
        "/pictures/project",
        summary="Set project for pictures",
        description="Assigns, removes, or clears project association for a batch of pictures.",
        response_model=SetProjectForPicturesResponse,
    )
    def set_project_for_pictures(request: Request, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        picture_ids_raw = payload.get("picture_ids")
        if not isinstance(picture_ids_raw, list):
            raise HTTPException(status_code=400, detail="picture_ids must be a list")

        try:
            picture_ids = sorted(
                {
                    int(pid)
                    for pid in picture_ids_raw
                    if pid is not None and int(pid) > 0
                }
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="picture_ids must contain valid positive integers",
            ) from exc

        if not picture_ids:
            raise HTTPException(
                status_code=400,
                detail="At least one valid picture id is required",
            )

        # Scope guard (BOLA): a write-capable resource-scoped token may only set
        # project membership for pictures within its granted resource. None ==
        # owner / unscoped == no filter; an empty/disjoint set denies all.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            picture_ids = [pid for pid in picture_ids if pid in scope_allowed]
            if not picture_ids:
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised to access these pictures",
                )

        project_id_raw = payload.get("project_id", None)
        if project_id_raw is None:
            project_id_value = None
        else:
            try:
                project_id_value = int(project_id_raw)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="project_id must be an integer or null",
                ) from exc

        mode_raw = payload.get("mode", "set")
        mode = str(mode_raw).strip().lower()
        if mode not in {"set", "add", "remove"}:
            raise HTTPException(
                status_code=400,
                detail="mode must be one of: set, add, remove",
            )
        if mode in {"add", "remove"} and project_id_value is None:
            raise HTTPException(
                status_code=400,
                detail="project_id is required when mode is add or remove",
            )

        def update_picture_projects(
            session: Session,
            ids: list[int],
            project_id_target: int | None,
            update_mode: str,
        ):
            if project_id_target is not None:
                project = session.get(Project, project_id_target)
                if project is None:
                    raise HTTPException(status_code=404, detail="Project not found")

            # Stacks are atomic for project membership: applying a change to any
            # stacked picture applies it to every member of its stack.
            target_ids = expand_picture_ids_to_stacks(session, ids)

            pics = session.exec(
                select(Picture)
                .where(Picture.id.in_(target_ids))
                .where(Picture.deleted.is_(False))
            ).all()
            updated_ids: list[int] = []
            found_ids: set[int] = set()
            for pic in pics:
                if pic.id is None:
                    continue
                found_ids.add(int(pic.id))
                changed = False
                if update_mode == "set" and project_id_target is None:
                    existing_memberships = session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == int(pic.id)
                        )
                    ).all()
                    if existing_memberships:
                        for membership in existing_memberships:
                            session.delete(membership)
                        changed = True
                    if pic.project_id is not None:
                        pic.project_id = None
                        session.add(pic)
                        changed = True
                elif update_mode == "remove" and project_id_target is not None:
                    existing_memberships = session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == int(pic.id),
                            PictureProjectMember.project_id == project_id_target,
                        )
                    ).all()
                    if existing_memberships:
                        for membership in existing_memberships:
                            session.delete(membership)
                        changed = True
                    if pic.project_id == project_id_target:
                        fallback_project_id = session.exec(
                            select(PictureProjectMember.project_id)
                            .where(
                                PictureProjectMember.picture_id == int(pic.id),
                                PictureProjectMember.project_id != project_id_target,
                            )
                            .order_by(PictureProjectMember.project_id.asc())
                        ).first()
                        pic.project_id = (
                            int(fallback_project_id)
                            if fallback_project_id is not None
                            else None
                        )
                        session.add(pic)
                        changed = True
                else:
                    member = session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == int(pic.id),
                            PictureProjectMember.project_id == project_id_target,
                        )
                    ).first()
                    if member is None:
                        session.add(
                            PictureProjectMember(
                                picture_id=int(pic.id),
                                project_id=project_id_target,
                            )
                        )
                        changed = True
                    if pic.project_id != project_id_target:
                        pic.project_id = project_id_target
                        session.add(pic)
                        changed = True

                if changed:
                    updated_ids.append(int(pic.id))
            if updated_ids:
                session.commit()
            missing_ids = [pid for pid in ids if pid not in found_ids]
            return updated_ids, missing_ids

        updated_ids, missing_ids = server.vault.db.run_task(
            update_picture_projects,
            picture_ids,
            project_id_value,
            mode,
            priority=DBPriority.IMMEDIATE,
        )

        if updated_ids:
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": updated_ids,
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )

        return {
            "status": "success",
            "project_id": project_id_value,
            "mode": mode,
            "updated_ids": updated_ids,
            "updated_count": len(updated_ids),
            "missing_ids": missing_ids,
        }

    @router.post(
        "/pictures/apply-scores",
        summary="Batch apply manual scores",
        description="Applies 0-5 manual scores to multiple pictures in one request while optionally enforcing only-unscored updates.",
        response_model=ApplyScoresResponse,
    )
    def apply_scores_for_pictures(request: Request, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        scores_payload = payload.get("scores")
        if not isinstance(scores_payload, dict) or not scores_payload:
            raise HTTPException(
                status_code=400,
                detail="scores must be a non-empty object mapping picture ids to integer scores",
            )

        only_unscored_raw = payload.get("only_unscored", True)
        if not isinstance(only_unscored_raw, bool):
            raise HTTPException(
                status_code=400,
                detail="only_unscored must be a boolean",
            )
        only_unscored = bool(only_unscored_raw)

        parsed_scores: dict[int, int] = {}
        for raw_picture_id, raw_score in scores_payload.items():
            try:
                picture_id = int(raw_picture_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="scores keys must be valid positive integer picture ids",
                ) from exc
            if picture_id <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="scores keys must be valid positive integer picture ids",
                )

            try:
                score_value = int(raw_score)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="scores values must be integers in range 0..5",
                ) from exc
            if score_value < 0 or score_value > 5:
                raise HTTPException(
                    status_code=400,
                    detail="scores values must be integers in range 0..5",
                )

            parsed_scores[picture_id] = score_value

        ordered_picture_ids = sorted(parsed_scores.keys())

        # Scope guard (BOLA): a write-capable resource-scoped token may only
        # score pictures within its granted resource. None == owner / unscoped
        # == no filter; an empty/disjoint set denies all.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            parsed_scores = {
                pid: score
                for pid, score in parsed_scores.items()
                if pid in scope_allowed
            }
            ordered_picture_ids = sorted(parsed_scores.keys())
            if not ordered_picture_ids:
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised to access these pictures",
                )

        def _apply_scores_batch(
            session: Session,
            picture_ids: list[int],
            picture_scores: dict[int, int],
            apply_only_unscored: bool,
        ):
            # Read only id+score to avoid loading heavy blob columns on Picture.
            # Loading full ORM rows here can be very expensive for large batches.
            score_rows = session.exec(
                select(Picture.id, Picture.score)
                .where(Picture.id.in_(picture_ids))
                .where(Picture.deleted.is_(False))
            ).all()

            found_ids: set[int] = set()
            updated_ids: list[int] = []
            skipped_ids: list[int] = []
            reset_triggered = False
            score_updates: dict[int, int] = {}

            for row in score_rows:
                pic_id_raw, old_score = row
                if pic_id_raw is None:
                    continue
                pic_id = int(pic_id_raw)
                found_ids.add(pic_id)

                if apply_only_unscored and old_score is not None:
                    skipped_ids.append(pic_id)
                    continue

                new_score = picture_scores[pic_id]
                if old_score == new_score:
                    continue

                if _score_anchor_membership_changed(old_score, new_score):
                    reset_triggered = True

                score_updates[pic_id] = new_score
                updated_ids.append(pic_id)

            missing_ids = [pid for pid in picture_ids if pid not in found_ids]

            if score_updates:
                session.exec(
                    update(Picture)
                    .where(Picture.id.in_(score_updates.keys()))
                    .values(
                        score=case(
                            score_updates,
                            value=Picture.id,
                            else_=Picture.score,
                        )
                    )
                )

            if reset_triggered:
                session.exec(
                    update(Picture)
                    .where(Picture.smart_score.is_not(None))
                    .values(smart_score=None)
                )

            if updated_ids or reset_triggered:
                session.commit()

            return (
                sorted(updated_ids),
                sorted(skipped_ids),
                sorted(missing_ids),
                reset_triggered,
            )

        updated_ids, skipped_ids, missing_ids, reset_triggered = (
            server.vault.db.run_task(
                _apply_scores_batch,
                ordered_picture_ids,
                parsed_scores,
                only_unscored,
                priority=DBPriority.IMMEDIATE,
            )
        )

        if updated_ids or reset_triggered:
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": updated_ids,
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )

        return {
            "status": "success",
            "only_unscored": only_unscored,
            "updated_ids": updated_ids,
            "updated_count": len(updated_ids),
            "skipped_ids": skipped_ids,
            "skipped_count": len(skipped_ids),
            "missing_ids": missing_ids,
            "missing_count": len(missing_ids),
            "reset_triggered": bool(reset_triggered),
        }

    @router.get(
        "/pictures/{id}.{ext}",
        summary="Get original picture file",
        description="Streams the original media file for a picture id when the requested extension matches the stored format.",
        response_class=FileResponse,
        responses={200: {"content": {"image/*": {}}}},
    )
    def get_picture(request: Request, id: str, ext: str):
        if not isinstance(id, str):
            logger.error(f"Invalid id type: {type(id)} value: {id}")
            raise HTTPException(status_code=400, detail="Invalid picture id type")

        if not ext or not isinstance(ext, str):
            logger.error(f"Invalid extension type: {type(ext)} value: {ext}")
            raise HTTPException(status_code=400, detail="Invalid picture extension")
        id = int(id)

        pics = server.vault.db.run_immediate_read_task(
            lambda session: Picture.find(session, id=id, include_deleted=True)
        )
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]
        enforce_picture_scope(server, request, id)

        file_path = ImageUtils.resolve_picture_path(
            server.vault.image_root, pic.file_path
        )
        if not file_path or not os.path.isfile(file_path):
            logger.error(
                f"File path missing or does not exist for picture id={pic.id}, file_path={pic.file_path}"
            )
            raise HTTPException(
                status_code=404, detail=f"File not found for picture id={pic.id}"
            )
        if pic.format.lower() != ext.lower():
            logger.error(
                f"Requested extension '{ext}' does not match picture format '{pic.format}' for id={pic.id}"
            )
            raise HTTPException(
                status_code=400,
                detail="Requested extension does not match picture format",
            )

        fmt_lower = pic.format.lower()

        # Determine whether the active token requires a watermark.
        _token_scope = getattr(request.state, "token_scope", None)
        apply_wm = bool(_token_scope and getattr(_token_scope, "watermark", False))

        def _get_user_watermark_bytes() -> bytes | None:
            user_id = getattr(request.state, "auth_user_id", None)
            if not user_id:
                return None
            user = server.vault.db.run_immediate_read_task(
                lambda session: session.get(User, user_id)
            )
            return get_watermark_bytes(
                getattr(user, "watermark_image", None) if user else None
            )

        # Browsers (Chrome, Firefox) cannot display HEIC/HEIF natively.
        # Transcode to JPEG on-the-fly so the overlay image loads correctly.
        # Watermark compositing also requires PIL, so we share this branch.
        #
        # Disk cache for watermarked images: stored as {stem}_watermarked.{ext}
        # next to the original. Valid while the cached file is at least as new as
        # the source. Served directly via FileResponse so the browser can also
        # cache it by ETag.
        is_heic = fmt_lower in ("heic", "heif")
        if apply_wm and not is_heic:
            file_stem, file_ext = os.path.splitext(file_path)
            wm_cache_path = f"{file_stem}_watermarked{file_ext}"
            if os.path.isfile(wm_cache_path):
                try:
                    if os.path.getmtime(wm_cache_path) >= os.path.getmtime(file_path):
                        media_type = MEDIA_TYPE_BY_FORMAT.get(
                            fmt_lower, "application/octet-stream"
                        )
                        stat = os.stat(wm_cache_path)
                        etag = f'W/"{stat.st_size}-{int(stat.st_mtime)}"'
                        if request.headers.get("if-none-match") == etag:
                            return Response(status_code=304)
                        resp = FileResponse(wm_cache_path, media_type=media_type)
                        resp.headers["ETag"] = etag
                        resp.headers["Cache-Control"] = "public, max-age=86400"
                        return resp
                except OSError as exc:
                    logger.warning(
                        "Failed to access watermark cache for id=%s: %s", pic.id, exc
                    )
        else:
            wm_cache_path = None

        if is_heic or apply_wm:
            try:
                with Image.open(file_path) as pil_img:
                    if apply_wm:
                        wm_bytes = _get_user_watermark_bytes()
                        if wm_bytes:
                            pil_img = apply_watermark(pil_img, wm_bytes)
                    # HEIC/HEIF → JPEG (browser compat);
                    # other formats preserve original so content-type matches URL.
                    if is_heic:
                        out_fmt = "JPEG"
                        out_mime = "image/jpeg"
                        save_kwargs = {"quality": 92}
                        pil_img = pil_img.convert("RGB")
                    else:
                        out_fmt = pil_img.format or fmt_lower.upper()
                        if out_fmt.upper() in ("JPG", "JPEG"):
                            out_fmt = "JPEG"
                            pil_img = pil_img.convert("RGB")
                            save_kwargs = {"quality": 92}
                        else:
                            save_kwargs = {}
                        out_mime = MEDIA_TYPE_BY_FORMAT.get(
                            fmt_lower, "application/octet-stream"
                        )
                    buf = BytesIO()
                    pil_img.save(buf, format=out_fmt, **save_kwargs)
                    buf.seek(0)
                    encoded_bytes = buf.read()
            except Exception as exc:
                logger.error(
                    "Failed to process picture id=%s: %s",
                    pic.id,
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail="Failed to process image",
                )

            # Persist the watermarked result to disk so future requests are free.
            if wm_cache_path is not None:
                try:
                    with open(wm_cache_path, "wb") as _f:
                        _f.write(encoded_bytes)
                except OSError as exc:
                    logger.warning(
                        "Could not write watermark cache for id=%s: %s", pic.id, exc
                    )

            response = Response(
                content=encoded_bytes,
                media_type=out_mime,
            )
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

        media_type = MEDIA_TYPE_BY_FORMAT.get(fmt_lower)
        response = FileResponse(file_path, media_type=media_type)
        try:
            stat = os.stat(file_path)
            etag = f'W/"{stat.st_size}-{int(stat.st_mtime)}"'
            response.headers["ETag"] = etag
            response.headers["Last-Modified"] = formatdate(stat.st_mtime, usegmt=True)
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        except OSError:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        if pic.original_file_name:
            # Suggest the original filename when using "Save image as" in the browser.
            # Using 'inline' keeps the image rendering in-page while still providing
            # the filename hint — no URL change needed.
            safe_name = pic.original_file_name.replace('"', "")
            response.headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
        origin = request.headers.get("origin")
        if origin and (
            origin in server.allow_origins
            or (
                server.allow_origin_regex
                and re.match(server.allow_origin_regex, origin)
            )
        ):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    @router.get(
        "/pictures/{id}/metadata",
        summary="Get picture metadata",
        description="Returns metadata, tags, and optional smart score for a single picture, including embedded file metadata when available.",
        response_model=PictureFullMetadataResponse,
    )
    def get_picture_metadata(
        request: Request,
        id: str,
        smart_score: bool = Query(False),
    ):
        metadata_fields = Picture.metadata_fields()
        pics = server.vault.db.run_immediate_read_task(
            Picture.find, id=id, select_fields=metadata_fields, include_deleted=True
        )
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]
        enforce_picture_scope(server, request, pic.id)

        def fetch_image_only_tags(session: Session, pic_id: int):
            return session.exec(select(Tag).where(Tag.picture_id == pic_id)).all()

        pic_tags = server.vault.db.run_immediate_read_task(
            fetch_image_only_tags, pic.id
        )
        pic_dict = safe_model_dict(pic)
        pic_dict["tags"] = serialize_tag_objects(pic_tags)

        if smart_score:
            pic_dict["smartScore"] = pic.smart_score  # already stored in DB

        embedded_metadata = {}
        try:
            file_path = ImageUtils.resolve_picture_path(
                server.vault.image_root, pic.file_path
            )
            logger.debug(
                "[metadata] Extracting embedded metadata for id=%s path=%s",
                pic.id,
                file_path,
            )
            embedded_metadata = ImageUtils.extract_embedded_metadata(file_path)
        except Exception as exc:
            logger.warning(
                "Failed to read embedded metadata for picture id=%s: %s",
                pic.id,
                exc,
            )

        if embedded_metadata:
            pic_dict["metadata"] = embedded_metadata

        if embedded_metadata:
            logger.debug(
                "[metadata] id=%s embedded_top_keys=%s",
                pic.id,
                list(embedded_metadata.keys()),
            )

        logger.debug("Returning dict: " + str(pic_dict))
        return pic_dict

    @router.post(
        "/pictures/{id}/face",
        include_in_schema=False,
        summary="Create manual face entry",
        description="Adds a face bounding box to a picture and frame index, updating sentinel/ordering behavior for manual annotations.",
        response_model=PictureFaceResponse,
    )
    def create_picture_face(request: Request, id: str, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")
        # Scope guard (BOLA): a write-capable resource-scoped token may only
        # mutate faces on pictures within its granted resource.
        enforce_picture_scope(server, request, pic_id)

        bbox = payload.get("bbox") if isinstance(payload, dict) else None
        frame_index = payload.get("frame_index", 0) if isinstance(payload, dict) else 0
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [x1, y1, x2, y2]")
        try:
            bbox_vals = [int(round(float(v))) for v in bbox]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="bbox values must be numbers")
        try:
            frame_index = int(frame_index)
        except (TypeError, ValueError):
            frame_index = 0

        def create_face(session: Session):
            pic = session.get(Picture, pic_id)
            if not pic:
                return None
            sentinel = session.exec(
                select(Face).where(
                    Face.picture_id == pic_id,
                    Face.frame_index == frame_index,
                    Face.face_index == -1,
                )
            ).first()
            if sentinel is not None:
                session.delete(sentinel)
            max_index = session.exec(
                select(func.max(Face.face_index)).where(
                    Face.picture_id == pic_id,
                    Face.frame_index == frame_index,
                )
            ).one()
            next_index = (max_index or 0) + 1 if max_index is not None else 0
            face = Face(
                picture_id=pic_id,
                frame_index=frame_index,
                face_index=next_index,
                bbox=bbox_vals,
            )
            session.add(face)
            session.commit()
            session.refresh(face)
            return face

        face = server.vault.db.run_task(create_face, priority=DBPriority.IMMEDIATE)
        if not face:
            raise HTTPException(status_code=404, detail="Picture not found")
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        return safe_model_dict(face)

    @router.delete(
        "/pictures/{id}/face/{index}",
        include_in_schema=False,
        summary="Delete face by index",
        description="Deletes a face at frame 0 by index and reindexes remaining faces for stable ordering.",
        response_model=FaceDeleteResponse,
    )
    def delete_picture_face(request: Request, id: str, index: int):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")
        # Scope guard (BOLA): a write-capable resource-scoped token may only
        # mutate faces on pictures within its granted resource.
        enforce_picture_scope(server, request, pic_id)

        def delete_face(session: Session):
            face = session.exec(
                select(Face).where(
                    Face.picture_id == pic_id,
                    Face.frame_index == 0,
                    Face.face_index == index,
                )
            ).first()
            if not face:
                return False
            session.delete(face)
            remaining = session.exec(
                select(Face)
                .where(
                    Face.picture_id == pic_id,
                    Face.frame_index == 0,
                    Face.face_index >= 0,
                )
                .order_by(Face.face_index, Face.id)
            ).all()
            for next_idx, entry in enumerate(remaining):
                if entry.face_index != next_idx:
                    entry.face_index = next_idx
                    session.add(entry)
            if not remaining:
                sentinel = session.exec(
                    select(Face).where(
                        Face.picture_id == pic_id,
                        Face.frame_index == 0,
                        Face.face_index == -1,
                    )
                ).first()
                if sentinel is None:
                    session.add(
                        Face(
                            picture_id=pic_id,
                            frame_index=0,
                            face_index=-1,
                            character_id=None,
                            bbox=None,
                        )
                    )
            session.commit()
            return True

        deleted = server.vault.db.run_task(delete_face, priority=DBPriority.IMMEDIATE)
        if not deleted:
            raise HTTPException(status_code=404, detail="Face not found")
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        return {"status": "success", "message": "Face deleted."}

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

        # Object-level access check before any DB work, so every return branch
        # below is uniformly gated. Owner/unscoped sessions have token_scope is
        # None and pass straight through; a scoped token outside this picture's
        # grant gets a 403 here (mirrors get_picture / get_picture_metadata /
        # get_picture_field).
        enforce_picture_scope(server, request, pic_id)

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
        # get_picture_character_likeness, which calls enforce_picture_scope).
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
            "the vault — no tagging, captioning, embedding, or vault-wide "
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
                # No detectable face — nothing to score, so the frame is
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

    @router.get(
        "/pictures/{id}/{field}",
        include_in_schema=False,
        summary="Get raw picture field",
        description="Returns a single picture field value; large binary fields are base64 encoded and thumbnail returns image bytes.",
        responses={
            200: {
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "additionalProperties": True}
                    },
                    "image/png": {},
                }
            }
        },
    )
    def get_picture_field(request: Request, id: str, field: str):
        pics = server.vault.db.run_task(
            lambda session: Picture.find(
                session,
                id=id,
                select_fields=[field],
                include_deleted=True,
            )
        )
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]
        # Scope guard (BOLA): a resource-scoped token may only read its own
        # picture's fields/thumbnail, like get_picture and get_picture_metadata.
        enforce_picture_scope(server, request, pic.id)

        if field == "thumbnail":
            return Response(content=pic.thumbnail, media_type="image/png")
        if field in Picture.large_binary_fields():
            return {field: base64.b64encode(getattr(pic, field)).decode("utf-8")}
        return {field: safe_model_dict(getattr(pic, field))}

    @router.patch(
        "/pictures/{id}",
        summary="Patch picture fields",
        description="Updates mutable picture fields from query/body parameters, including tag replacement when provided.",
        response_model=PicturePatchResponse,
    )
    async def patch_picture(id: str, request: Request):
        params = dict(request.query_params)

        logger.debug("Got a PATCH request for picture id={}".format(id))

        content_type = request.headers.get("content-type", "")

        json_body = None
        if "application/json" in content_type:
            try:
                json_body = await request.json()
            except Exception:
                json_body = None

        try:
            pic_list = server.vault.db.run_task(
                lambda session: Picture.find(session, id=id, include_deleted=True)
            )
            if not pic_list:
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pic_list[0]
        except KeyError:
            raise HTTPException(status_code=404, detail="Picture not found")

        picture_id = pic.id
        # Scope guard (BOLA): a resource-scoped token may only mutate a picture
        # within its grant. Placed before any field is written, covering every
        # update path (score/description/tag replacement). Mirrors delete_picture
        # and the guarded read siblings get_picture / get_picture_field.
        enforce_picture_scope(server, request, picture_id)
        logger.debug(f"Updating picture id={id}")
        if json_body and isinstance(json_body, dict):
            params.update(json_body)

        logger.debug(
            f"Updating picture id={id} with params: {params} and json_body: {json_body}"
        )
        updated = False
        updated_fields = {}
        for key, value in params.items():
            if not hasattr(pic, key):
                logger.warning(
                    f"Picture does not have key '{key}' in PATCH request. Ignoring."
                )
                continue
            if key == "tags":
                if value is None:
                    continue
                if not isinstance(value, list):
                    raise HTTPException(
                        status_code=400,
                        detail="tags must be a list",
                    )
                if not value:
                    continue
                tag_values = [
                    tag if isinstance(tag, str) else str(tag)
                    for tag in value
                    if tag is not None
                ]
                if tag_values:
                    pic_id = pic.id

                    def _replace_tags(
                        session: Session,
                        pid: int,
                        new_tags: list[str],
                    ) -> None:
                        session.exec(delete(Tag).where(Tag.picture_id == pid))
                        session.add_all([Tag(picture_id=pid, tag=t) for t in new_tags])
                        session.commit()

                    server.vault.db.run_task(
                        _replace_tags,
                        pic_id,
                        tag_values,
                        priority=DBPriority.IMMEDIATE,
                    )
                    updated = True
                continue
            if key == "score":
                try:
                    value = int(value)
                except Exception:
                    value = None
            if getattr(pic, key) != value:
                updated_fields[key] = value
                updated = True

        if updated:
            old_score = pic.score if "score" in updated_fields else None

            def apply_picture_updates(session: Session, picture_id: int, fields: dict):
                pic_db = session.get(Picture, picture_id)
                if pic_db is None:
                    raise KeyError("Picture not found")
                for field_name, field_value in fields.items():
                    setattr(pic_db, field_name, field_value)
                session.add(pic_db)
                session.commit()
                session.refresh(pic_db)
                return pic_db

            try:
                pic = server.vault.db.run_task(
                    apply_picture_updates,
                    picture_id,
                    updated_fields,
                    priority=DBPriority.IMMEDIATE,
                )
            except KeyError:
                raise HTTPException(status_code=404, detail="Picture not found")
            if "score" in updated_fields:
                new_score = updated_fields["score"]

                if _score_anchor_membership_changed(old_score, new_score):

                    def _reset_smart_scores(session: Session) -> None:
                        session.exec(update(Picture).values(smart_score=None))
                        session.commit()

                    server.vault.db.run_task(
                        _reset_smart_scores, priority=DBPriority.LOW
                    )
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": [picture_id],
                    "origin_client_id": getattr(
                        request.state, "origin_client_id", None
                    ),
                    "change_kind": "updated",
                },
            )

        # Write back description to caption sidecar when enabled.
        sync_picture_sidecar(server, picture_id)

        return {"status": "success", "picture": safe_model_dict(pic)}

    @router.post(
        "/pictures/scrapheap/restore",
        summary="Restore deleted pictures",
        description="Restores deleted pictures from scrapheap, either all deleted pictures or a provided picture id subset.",
        response_model=ScrapheapRestoreResponse,
    )
    def restore_scrapheap(request: Request, payload: dict | None = Body(None)):
        # Scope guard (BOLA/F2): scrapheap restore mutates library-wide deleted
        # state — with no picture_ids it restores *every* deleted picture — so it
        # is owner-only. require_unscoped_owner rejects READ-scoped tokens and
        # ALL-scope tokens narrowed to a resource, fail-closed.
        server.auth.require_unscoped_owner(request)
        origin_client_id = getattr(request.state, "origin_client_id", None)
        picture_ids = None
        if payload:
            ids = payload.get("picture_ids")
            if ids is not None:
                if not isinstance(ids, list) or not ids:
                    raise HTTPException(
                        status_code=400,
                        detail="picture_ids must be a non-empty list",
                    )
                picture_ids = ids

        def restore_pictures(session: Session, ids: list[int] | None):
            query = select(Picture).where(
                Picture.deleted.is_(True),
            )
            if ids is not None:
                query = query.where(Picture.id.in_(ids))
            pics = session.exec(query).all()
            restored_count = 0
            affected_stack_ids: set[int] = set()
            for pic in pics:
                pic.deleted = False
                session.add(pic)
                if pic.stack_id is not None:
                    affected_stack_ids.add(pic.stack_id)
                restored_count += 1
            # Re-fold restored pictures into their stack ordering so a restored
            # member is not left behind a (now lower-ranked) deleted leader.
            for stack_id in affected_stack_ids:
                normalize_stack_positions(session, stack_id)
            session.commit()
            return restored_count

        restored_count = server.vault.db.run_task(
            restore_pictures,
            picture_ids,
            priority=DBPriority.IMMEDIATE,
        )
        # A restored picture re-enters active views. ``picture_ids`` is the
        # caller-supplied subset (None == "restore all"); pass it through when
        # known so the originating tab can target the affected cards.
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": list(picture_ids) if picture_ids else [],
                "origin_client_id": origin_client_id,
                "change_kind": "added",
            },
        )
        return {"status": "success", "restored_count": restored_count}

    @router.delete(
        "/pictures/scrapheap",
        summary="Permanently delete scrapheap pictures",
        description="Permanently removes deleted pictures from database and disk for provided ids or for all scrapheap items when omitted.",
        response_model=ScrapheapDeleteResponse,
    )
    def delete_scrapheap_selection(
        request: Request,
        background_tasks: BackgroundTasks,
        payload: dict | None = Body(None),
    ):
        # Scope guard (BOLA/F2): permanent scrapheap deletion is an irreversible,
        # library-wide destructive op (removes DB rows + disk files + writes the
        # deletion ledger; with no picture_ids it purges *every* deleted picture).
        # Same risk class as snapshot/restore ops — owner-only. fail-closed.
        server.auth.require_unscoped_owner(request)
        origin_client_id = getattr(request.state, "origin_client_id", None)
        ids = None
        if payload is not None:
            maybe_ids = (
                payload.get("picture_ids") if isinstance(payload, dict) else None
            )
            if maybe_ids is not None:
                if not isinstance(maybe_ids, list) or not maybe_ids:
                    raise HTTPException(
                        status_code=400,
                        detail="picture_ids must be a non-empty list",
                    )
                try:
                    ids = [int(pid) for pid in maybe_ids]
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail="picture_ids must contain valid integers",
                    )

        def fetch_deleted(session: Session, ids: list[int] | None):
            query = select(
                Picture.id,
                Picture.file_path,
                Picture.reference_folder_id,
                Picture.pixel_sha,
            ).where(
                Picture.deleted.is_(True),
            )
            if ids is not None:
                query = query.where(Picture.id.in_(ids))
            return session.exec(query).all()

        rows = server.vault.db.run_task(
            fetch_deleted, ids, priority=DBPriority.IMMEDIATE
        )
        if not rows:
            return {"status": "success", "deleted_count": 0}

        # allow_delete_file=False on a reference folder protects only the source
        # file on disk — never the DB row or the deletion ledger.  Every deleted
        # picture loses its row and gains a DeletedFileLog entry (so the file is
        # never re-imported / resurrected); these folder ids only decide whether
        # the physical file is also removed.
        def fetch_no_delete_folder_ids(session: Session) -> set[int]:
            result = session.exec(
                select(ReferenceFolder.id).where(
                    ReferenceFolder.allow_delete_file.is_(False),
                )
            ).all()
            return {r for r in result if r is not None}

        no_delete_folder_ids: set[int] = server.vault.db.run_task(
            fetch_no_delete_folder_ids, priority=DBPriority.IMMEDIATE
        )

        picture_ids: list[int] = []
        file_paths: list[str] = []
        log_records: list[dict] = []

        for row in rows:
            pic_id, file_path, ref_folder_id, pixel_sha = (
                row[0],
                row[1],
                row[2],
                row[3],
            )
            if pic_id is not None:
                picture_ids.append(pic_id)
            if file_path:
                # Log every deleted picture so it can never be resurrected,
                # regardless of whether the on-disk file is protected.
                log_records.append(
                    {
                        "path_sha": DeletedFileLog.hash_path(file_path),
                        "pixel_sha": pixel_sha,
                    }
                )
                # Only enqueue the physical file for removal when its reference
                # folder permits it; a protected file stays on disk.
                file_protected = (
                    ref_folder_id is not None and ref_folder_id in no_delete_folder_ids
                )
                if not file_protected:
                    file_paths.append(file_path)

        def delete_files(image_root: str, paths: list[str]):
            for rel_path in paths:
                file_path = ImageUtils.resolve_picture_path(image_root, rel_path)
                if file_path and os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logger.error(
                            "Failed to delete picture file %s: %s",
                            file_path,
                            e,
                        )
                thumb_path = ImageUtils.get_thumbnail_path(image_root, rel_path)
                if thumb_path and os.path.isfile(thumb_path):
                    try:
                        os.remove(thumb_path)
                    except Exception as e:
                        logger.warning(
                            "Failed to delete thumbnail %s: %s",
                            thumb_path,
                            e,
                        )

        background_tasks.add_task(
            delete_files,
            server.vault.image_root,
            file_paths,
        )

        def delete_rows(session: Session, ids: list[int], log_records: list[dict]):
            if not ids:
                return 0
            # Record each permanently deleted file in deleted_file_log so we
            # retain a durable record of what can no longer be restored (e.g.
            # when rolling a vault back to an older snapshot). Logged and
            # deleted in the same transaction so the two never diverge.
            now = datetime.now(timezone.utc)
            for record in log_records:
                path_sha = record.get("path_sha")
                if not path_sha:
                    continue
                already_logged = session.exec(
                    select(DeletedFileLog).where(DeletedFileLog.path_sha == path_sha)
                ).first()
                if already_logged is None:
                    session.add(
                        DeletedFileLog(
                            path_sha=path_sha,
                            pixel_sha=record.get("pixel_sha"),
                            deleted_at=now,
                        )
                    )
            session.exec(delete(Picture).where(Picture.id.in_(ids)))
            session.commit()
            return len(ids)

        deleted_count = server.vault.db.run_task(
            delete_rows,
            picture_ids,
            log_records,
            priority=DBPriority.IMMEDIATE,
        )
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": list(picture_ids),
                "origin_client_id": origin_client_id,
                "change_kind": "removed",
            },
        )

        # Tell the caller which snapshots still hold metadata for the pictures
        # just purged. Snapshot archives are not scrubbed, so the user may want
        # to delete those snapshots if the deletion was for privacy. Discovery
        # reads only the JSON manifests (no snapshot DB is opened).
        snapshots_with_deleted = server.vault.snapshot_service.snapshots_containing(
            picture_ids
        )

        return {
            "status": "success",
            "deleted_count": deleted_count,
            "snapshots_with_deleted": snapshots_with_deleted,
        }

    @router.delete(
        "/pictures/{id}",
        summary="Move picture to scrapheap",
        description="Soft-deletes a picture by marking it deleted, making it appear in scrapheap views.",
        response_model=PictureDeleteResponse,
    )
    def delete_picture(request: Request, id: str):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")
        # Scope guard (BOLA): a write-capable resource-scoped token may only
        # soft-delete pictures within its granted resource.
        enforce_picture_scope(server, request, pic_id)

        def delete_pic(session, id):
            pic = session.get(Picture, id)
            if not pic:
                return False
            if pic.deleted:
                return True
            pic.deleted = True
            session.add(pic)
            # Promote a live member to the leader slot: a soft-deleted picture
            # must not keep stack_position 0, or the whole stack disappears from
            # the grid (no-op when the picture is not stacked).
            normalize_stack_positions(session, pic.stack_id)
            session.commit()
            return True

        success = server.vault.db.run_task(delete_pic, id)
        if not success:
            raise HTTPException(status_code=404, detail="Picture not found")
        # Soft-delete removes the card from active grid views. Broadcast a
        # ``removed`` event so other tabs drop the stale card (and never leave a
        # 404-clickable thumbnail behind).
        try:
            removed_id = int(id)
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": [removed_id],
                    "origin_client_id": origin_client_id,
                    "change_kind": "removed",
                },
            )
        except (TypeError, ValueError):
            logger.warning(
                "delete_picture: could not coerce id=%r to int for WS notify; "
                "skipping the removed broadcast",
                id,
            )
        return JSONResponse(
            content={"status": "success", "message": f"Picture id={id} deleted."}
        )
