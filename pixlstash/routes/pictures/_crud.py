import base64
import os
from datetime import datetime, timezone

from fastapi import (
    BackgroundTasks,
    Body,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    case,
    delete,
    update,
)
from sqlmodel import Session, select
from typing import Optional

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    DeletedFileLog,
    Detection,
    Picture,
    PictureProjectMember,
    Project,
    ReferenceFolder,
    Tag,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import (
    enforce_pictures_not_locked,
    locked_by_sets_for_picture,
    locked_picture_ids,
)
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.stacking import normalize_stack_positions
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.caption_utils import (
    serialize_tag_objects,
    sync_picture_sidecar,
)
from pixlstash.utils.service.filter_helpers import (
    fetch_scope_allowed_picture_ids,
    fetch_scope_allowed_set_ids,
)
from pixlstash.utils.service.serialization_utils import safe_model_dict

from ._helpers import (
    _score_anchor_membership_changed,
)


logger = get_logger(__name__)


# Upper bound on picture_ids per detection request. Each id becomes a unit of
# GPU work in a single HIGH-priority DetectionTask, so an unbounded list lets a
# caller enqueue detection over an entire library in one request. Mirrors the
# batch-likeness cap above (reject over the limit rather than truncate).
DETECT_MAX_IDS = 1000


# Upper bound on picture_ids per bulk soft-delete request. A scoped token runs one
# per-id scope DB read, and the delete pass one row fetch per id, so an unbounded
# list would serialise that much work on the DB queue from a single request.
# Mirrors the caps above; the frontend chunks larger selections into multiple calls.
BULK_DELETE_MAX_IDS = 1000


# picture belongs to a locked set. Organisation fields (e.g. project_id) are not
# listed — they stay editable per the lock semantics.
_LOCK_SENSITIVE_PATCH_FIELDS = frozenset({"description", "score"})


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
    locked: bool = Field(
        default=False,
        description=(
            "True when a locked picture set freezes this picture's label data "
            "(directly, or through a stack sibling). This is the authoritative "
            "'is it frozen' signal and is always accurate, even for a share token "
            "that may not see the locking set — use it to disable editing "
            "controls. ``locked_by_sets`` may be empty while this is true."
        ),
    )
    locked_by_sets: list[dict] = Field(
        default=[],
        description=(
            "The locked sets freezing this picture, as ``{id, name}``, restricted "
            "to the sets the caller's token may see (owner/unscoped sees all). A "
            "resource-scoped share token is not told the id or the user-authored "
            "name of a locked set outside its grant, so this list can be empty "
            "while ``locked`` is true — render the 'Locked by <names>' detail only "
            "when it is non-empty, and never derive locked-ness from its length."
        ),
    )


class PictureDetectionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    picture_id: Optional[int] = None
    frame_index: Optional[int] = None
    detection_index: Optional[int] = None
    label: Optional[str] = None
    bbox: Optional[list] = None
    score: Optional[float] = None
    source: Optional[str] = None


class DetectPicturesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    task_id: Optional[str] = None
    picture_ids: list[int] = []
    prompt: str = ""


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


class BulkPictureDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    # Number of pictures newly soft-deleted by this request (already-deleted or
    # missing ids are skipped and not counted).
    deleted_count: int
    # Picture ids skipped because a locked set freezes them (not deleted); unlock
    # the set to delete them.
    skipped_locked: list[int] = []


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

    @router.post(
        "/pictures/detect",
        summary="Detect objects in pictures",
        description=(
            "Queues a user-triggered Florence-2 object-detection pass over a "
            "batch of pictures. With an empty `prompt` it runs dense `<OD>` "
            "detection; with a non-empty `prompt` it runs open-vocabulary "
            "phrase grounding for that phrase. Detected labelled boxes are "
            "stored per picture (replacing any previous detections) and "
            "progress surfaces in the task manager."
        ),
        response_model=DetectPicturesResponse,
    )
    def detect_pictures(request: Request, payload: dict = Body(...)):
        # Capture the originating tab's client id so the detection-complete
        # event is attributed to this user's own action — the SPA suppresses the
        # "view changed externally" pill for its own origin.
        origin_client_id = getattr(request.state, "origin_client_id", None)
        raw_ids = payload.get("picture_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(
                status_code=400, detail="picture_ids must be a non-empty list"
            )
        if len(raw_ids) > DETECT_MAX_IDS:
            raise HTTPException(
                status_code=422,
                detail=f"picture_ids exceeds the maximum of {DETECT_MAX_IDS} ids per request",
            )
        try:
            picture_ids = sorted(
                {int(pid) for pid in raw_ids if pid is not None and int(pid) > 0}
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="picture_ids must contain valid positive integers",
            ) from exc
        if not picture_ids:
            raise HTTPException(
                status_code=400, detail="At least one valid picture id is required"
            )

        prompt = payload.get("prompt") or ""
        if not isinstance(prompt, str):
            prompt = str(prompt)
        prompt = prompt.strip()

        # Scope guard (BOLA): a scoped token may only run detection on pictures
        # within its granted resource. None == owner / unscoped == no filter; an
        # empty/disjoint set denies all (mirrors set_project_for_pictures).
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            picture_ids = [pid for pid in picture_ids if pid in scope_allowed]
            if not picture_ids:
                raise HTTPException(
                    status_code=403,
                    detail="Token is not authorised to access these pictures",
                )

        engine = getattr(server.vault, "_engine", None)
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Inference engine not available."
            )

        def fetch_pictures(session: Session, ids: list[int]):
            return session.exec(
                select(Picture).where(
                    Picture.id.in_(ids),
                    Picture.deleted.is_(False),
                )
            ).all()

        pics = server.vault.db.run_immediate_read_task(fetch_pictures, picture_ids)
        if not pics:
            raise HTTPException(status_code=404, detail="No pictures found")

        from pixlstash.tasks.detection_task import DetectionTask

        task = DetectionTask(
            server.vault.db,
            engine,
            list(pics),
            prompt=prompt or None,
            origin_client_id=origin_client_id,
        )
        task_id = server.vault.submit_task(task)
        if task_id is None:
            raise HTTPException(status_code=503, detail="Task runner not available.")

        return {
            "status": "queued",
            "task_id": task_id,
            "picture_ids": [pic.id for pic in pics],
            "prompt": prompt,
        }

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

        def fetch_image_only_tags(session: Session, pic_id: int):
            return session.exec(select(Tag).where(Tag.picture_id == pic_id)).all()

        pic_tags = server.vault.db.run_immediate_read_task(
            fetch_image_only_tags, pic.id
        )
        pic_dict = safe_model_dict(pic)
        pic_dict["tags"] = serialize_tag_objects(pic_tags)
        # Locked sets freezing this picture, so the overlay can show the reason
        # without a second request.
        #
        # The authz gate authorizes the *picture* before the handler runs; it says nothing
        # about the related entities named in its payload. A set-scoped token may
        # legitimately read a picture that is also a member of some other, private
        # locked set, and that set's user-authored name (which routinely carries a
        # client / project / subject identifier) is not obtainable from
        # `GET /picture_sets`. So the *fact* of the freeze is disclosed —
        # `locked` is what the UI needs to disable the right controls — while the
        # names are filtered down to the sets this token may actually see.
        locked_sets = server.vault.db.run_immediate_read_task(
            locked_by_sets_for_picture, pic.id
        )
        pic_dict["locked"] = bool(locked_sets)
        visible_set_ids = fetch_scope_allowed_set_ids(server, request)
        if visible_set_ids is not None:
            locked_sets = [s for s in locked_sets if s["id"] in visible_set_ids]
        pic_dict["locked_by_sets"] = locked_sets

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

    @router.get(
        "/pictures/{id}/detections",
        summary="Get picture detections",
        description=(
            "Returns the stored object-detection bounding boxes for a picture, "
            "as produced by the Segment action. Boxes are pixel `xyxy` in the "
            "original picture's coordinate space."
        ),
        response_model=list[PictureDetectionResponse],
    )
    def get_picture_detections(request: Request, id: str):
        try:
            pic_id = int(id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid picture id") from exc

        def fetch_detections(session: Session):
            return Detection.find(session, picture_id=pic_id)

        rows = server.vault.db.run_immediate_read_task(fetch_detections)
        return [
            {
                "id": det.id,
                "picture_id": det.picture_id,
                "frame_index": det.frame_index,
                "detection_index": det.detection_index,
                "label": det.label,
                "bbox": det.bbox,
                "score": det.score,
                "source": det.source,
            }
            for det in rows
        ]

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
        logger.debug(f"Updating picture id={id}")
        if json_body and isinstance(json_body, dict):
            params.update(json_body)

        logger.debug(
            f"Updating picture id={id} with params: {params} and json_body: {json_body}"
        )
        updated = False
        updated_fields = {}
        for key, value in params.items():
            # Validate the key against the mapped CLASS, not the instance:
            # hasattr(pic, "tags") lazy-loads the tags relationship, which raises
            # DetachedInstanceError (a 500) when pic is detached from its session.
            # The class exposes the same mapped attributes without a DB access.
            if not hasattr(type(pic), key):
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
                        # Tag replacement is label data — frozen on a locked pic.
                        enforce_pictures_not_locked(
                            session, [pid], "replace tags on a locked picture"
                        )
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
                # description and score are label/curation data — frozen on a
                # picture in a locked set. Project assignment and other org fields
                # remain editable, so only guard when a locked-sensitive field is
                # actually being written.
                if any(f in _LOCK_SENSITIVE_PATCH_FIELDS for f in fields):
                    enforce_pictures_not_locked(
                        session, [picture_id], "edit a locked picture"
                    )
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
                # Only enqueue the physical file for removal when its reference
                # folder permits it; a protected file stays on disk.
                file_protected = (
                    ref_folder_id is not None and ref_folder_id in no_delete_folder_ids
                )
                # Log every deleted picture so the scanner never re-imports its
                # path, but record whether the on-disk file was actually removed.
                # ``file_removed=False`` (protected file kept on disk) means the
                # content is NOT gone: restore must not treat it as a permanent
                # deletion and drop the alive picture. ``file_removed=True`` is a
                # genuine purge the restore drop must honour.
                log_records.append(
                    {
                        "path_sha": DeletedFileLog.hash_path(file_path),
                        "pixel_sha": pixel_sha,
                        "file_removed": not file_protected,
                    }
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
                new_file_removed = record.get("file_removed", True)
                if already_logged is None:
                    session.add(
                        DeletedFileLog(
                            path_sha=path_sha,
                            pixel_sha=record.get("pixel_sha"),
                            deleted_at=now,
                            file_removed=new_file_removed,
                        )
                    )
                elif new_file_removed and not already_logged.file_removed:
                    # A path first logged file_removed=False (protected file kept
                    # on disk) is now being genuinely hard-deleted. Upgrade the
                    # stale flag to True so the ledger stays truthful rather than
                    # leaving a False row that only restore's missing-file net
                    # would catch. Only ever raise False -> True; never downgrade
                    # a genuine permanent deletion back to "kept".
                    already_logged.file_removed = True
                    session.add(already_logged)
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
        # Validate the id shape (owner path returns 400 on a malformed id; the
        # authz gate independently 403s a scoped token that cannot reach it).
        try:
            int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def delete_pic(session, id):
            pic = session.get(Picture, id)
            if not pic:
                return False
            # Soft-deleting a member would silently mutate a frozen set — refuse.
            enforce_pictures_not_locked(session, [pic.id], "delete a locked picture")
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

    @router.delete(
        "/pictures",
        summary="Bulk move pictures to scrapheap",
        description=(
            "Soft-deletes multiple pictures in one request by marking them deleted "
            '(they appear in scrapheap views). Body: {"picture_ids": [int, ...]}. '
            "Single-round-trip replacement for issuing one DELETE /pictures/{id} per "
            "id, which floods the client connection pool on large selections."
        ),
        response_model=BulkPictureDeleteResponse,
    )
    def delete_pictures_bulk(request: Request, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        maybe_ids = payload.get("picture_ids") if isinstance(payload, dict) else None
        if not isinstance(maybe_ids, list) or not maybe_ids:
            raise HTTPException(
                status_code=400, detail="picture_ids must be a non-empty list"
            )
        # Cap the id count so one request can't serialise unbounded per-id scope
        # reads + row fetches on the DB queue (mirrors DETECT_MAX_IDS et al.).
        if len(maybe_ids) > BULK_DELETE_MAX_IDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"picture_ids exceeds the maximum of {BULK_DELETE_MAX_IDS} "
                    "ids per request"
                ),
            )
        try:
            pic_ids = [int(pid) for pid in maybe_ids]
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="picture_ids must contain valid integers"
            )

        def delete_pics(session, ids):
            # Pictures frozen by a locked set are read-only: skip them (reporting
            # which) rather than failing the whole batch, so the rest still delete.
            locked = locked_picture_ids(session, ids)
            newly_deleted: list[int] = []
            affected_stacks: set = set()
            for pid in ids:
                if pid in locked:
                    continue
                pic = session.get(Picture, pid)
                if not pic or pic.deleted:
                    continue
                pic.deleted = True
                session.add(pic)
                # Soft-deleting a stack member must not strand stack_position 0 (the
                # whole stack would vanish from the grid). Collect the affected stacks
                # and normalise each once after every selected member is marked deleted,
                # so a still-live member is promoted to leader.
                if pic.stack_id is not None:
                    affected_stacks.add(pic.stack_id)
                newly_deleted.append(pid)
            for stack_id in affected_stacks:
                normalize_stack_positions(session, stack_id)
            session.commit()
            return newly_deleted, sorted(locked)

        deleted_ids, skipped_locked = server.vault.db.run_task(delete_pics, pic_ids)
        # Soft-delete removes the cards from active grid views. Broadcast a single
        # ``removed`` event so other tabs drop the stale cards in one update.
        if deleted_ids:
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": deleted_ids,
                    "origin_client_id": origin_client_id,
                    "change_kind": "removed",
                },
            )
        return {
            "status": "success",
            "deleted_count": len(deleted_ids),
            "skipped_locked": skipped_locked,
        }
