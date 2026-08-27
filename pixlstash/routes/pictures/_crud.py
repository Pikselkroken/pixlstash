import base64
from datetime import datetime, timezone

from fastapi import (
    BackgroundTasks,
    Body,
    HTTPException,
    Query,
    Request,
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
    Detection,
    Picture,
    PictureProjectMember,
    Project,
    Tag,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service, scrapheap_service
from pixlstash.services.set_lock_service import (
    enforce_pictures_not_locked,
    locked_by_sets_for_picture,
    locked_picture_ids,
)
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.stacking import normalize_stack_positions
from pixlstash.utils.field_allowlist import (
    PICTURE_EXTRA_SERVABLE_FIELDS,
    require_servable_field,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.orientation import (
    ROTATE_180,
    ROTATE_CCW,
    ROTATE_CW,
    ROTATE_DIRECTIONS,
    read_orientation,
    rotate_orientation,
    supports_in_place_rotation,
)
from pixlstash.utils.service.caption_utils import (
    serialize_tag_objects,
    sync_picture_sidecar,
)
from pixlstash.utils.service.filter_helpers import (
    fetch_scope_allowed_picture_ids,
    fetch_scope_allowed_set_ids,
    narrow_picture_project_ids,
)
from pixlstash.utils.service.scope_table import scope_id_subquery
from pixlstash.utils.serialization_utils import safe_model_dict

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


# Upper bound on picture_ids per in-place rotate. Lower than the caps above on
# purpose: each id costs two EXIF reads, a whole-container splice, an fsync and a
# re-hash, all of it on the single DB writer thread, so this is the cap where the
# per-id work is file I/O rather than a row fetch.
ROTATE_MAX_IDS = 200


# picture belongs to a locked set. Organisation fields (e.g. project_id) are not
# listed - they stay editable per the lock semantics.
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
            "that may not see the locking set - use it to disable editing "
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
            "while ``locked`` is true - render the 'Locked by <names>' detail only "
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
    # Protected reference-folder originals left intact this call because
    # ``include_protected`` was false (rows kept, files untouched, still in the
    # scrapheap). 0 when ``include_protected`` is true or nothing was protected.
    skipped_count: int = 0
    # Ids left intact because a locked picture-set freezes them. A lock binds on
    # EVERY path - including ``include_protected=true`` - so these are never
    # destroyed by this endpoint at any flag value. Unlock the set to delete them.
    skipped_locked: list[int] = []
    # Ids left intact because they had LEFT the scrapheap by the time the rows
    # were deleted - a restore that landed mid-purge. Their rows, files and
    # permanent-deletion ledger entries are untouched; nothing is lost.
    skipped_restored: list[int] = []
    # Echoes the effective ``include_protected`` flag for this call.
    include_protected: bool = False
    # Snapshots that still contain metadata for the just-purged pictures, each
    # ``{id, kind, label, created_at, matched_count}``. The archives are not
    # scrubbed; the user can delete these snapshots to erase that metadata.
    snapshots_with_deleted: Optional[list] = None


class ScrapheapProtectedItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    # Absolute on-disk path of the protected reference-folder original that an
    # ``include_protected`` delete-forever would ``os.remove``.
    file_path: str


class ScrapheapDeletePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Authoritative counts over the FULL delete set (never a virtualized/grid
    # subset), so the confirmation names every protected original at risk.
    #
    # The three counts are DISJOINT and sum to total_count, keyed on which action
    # destroys the row rather than on which properties it has, so no count can
    # overstate destruction:
    #   locked_count      -> destroyed by NEITHER button (a lock overrides
    #                        include_protected, so a locked+protected row counts
    #                        here, not under protected_count);
    #   protected_count   -> destroyed only by "Delete all" (include_protected=true);
    #   unprotected_count -> destroyed by both buttons.
    total_count: int
    protected_count: int
    locked_count: int = 0
    unprotected_count: int
    protected: list[ScrapheapProtectedItem]
    # Ids frozen by a locked picture-set, so the dialog can name them.
    locked: list[int] = []
    # Single-use, short-lived proof that these counts were actually fetched.
    # ``DELETE /pictures/scrapheap`` refuses without it, so the irreversible
    # endpoint cannot be driven blind from another origin. Bound to THIS
    # selection and spent by the first delete that presents it; re-run the
    # preview to get another.
    confirm_token: str = ""


# What the undo toast says. The direction is a property of the *request*, not of
# the recorded state (the log stores the resulting orientation absolutely), so it
# is closed over here rather than read back out of the diff. The count IS read
# from the diff, like every other summary: a rotate that skipped a picture must
# not claim it turned one.
_ROTATE_SUMMARY_SUFFIX = {
    ROTATE_CW: "right",
    ROTATE_CCW: "left",
    ROTATE_180: "180°",
}


def _rotate_summary(direction: str):
    """Build the ``(before_delta, after_delta) -> str`` summary for a rotate."""
    suffix = _ROTATE_SUMMARY_SUFFIX.get(direction, "right")

    def build(before_delta, after_delta):
        count = len(after_delta or {})
        if not count:
            return None
        noun = "picture" if count == 1 else "pictures"
        return f"Rotated {count} {noun} {suffix}"

    return build


def _parse_scrapheap_ids(payload: dict | None) -> list[int] | None:
    """Parse the scrapheap id selection from a request body.

    Accepts either ``ids`` (the delete-preview field) or ``picture_ids`` (the
    delete field); ``None``/absent means "the entire scrapheap". A present but
    empty or non-integer list is a 400.
    """
    if not isinstance(payload, dict):
        return None
    maybe_ids = payload.get("ids")
    if maybe_ids is None:
        maybe_ids = payload.get("picture_ids")
    if maybe_ids is None:
        return None
    if not isinstance(maybe_ids, list) or not maybe_ids:
        raise HTTPException(status_code=400, detail="ids must be a non-empty list")
    try:
        return [int(pid) for pid in maybe_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ids must contain valid integers")


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


class PictureRotateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    # Pictures whose file now carries a new EXIF orientation. Disjoint from the
    # two lists below, and the three together account for every requested id -
    # the buckets are counted where they happen, never by subtraction.
    rotated_picture_ids: list[int] = []
    # Pictures that cannot be rotated in place and need the copy-producing rotate
    # plugin instead: a reference-folder original, or a container with no
    # orientation tag we can splice (anything but JPEG and PNG).
    unsupported_picture_ids: list[int] = []
    # Pictures that were addressed but had nothing to rotate: no row, already in
    # the Scrapheap, no file on disk, or a file the write failed on.
    skipped_picture_ids: list[int] = []
    # The operation-log batch this rotate was recorded under, so the client can
    # offer one Undo for the whole gesture. Null when nothing changed.
    batch_id: Optional[str] = None


def register_routes(router, server):
    # Per-server store of unspent delete-forever confirmations. Scoped to this
    # router (i.e. to this Server) rather than module-global so two servers in
    # one process - the test suite - cannot spend each other's confirmations.
    scrapheap_confirmations = scrapheap_service.ScrapheapDeleteConfirmations()

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
            target_scope = scope_id_subquery(
                session, target_ids, name="_pixlstash_project_picture_ids"
            )

            pics = session.exec(
                select(Picture)
                .where(Picture.id.in_(target_scope))
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
                session.flush()
            missing_ids = [pid for pid in ids if pid not in found_ids]
            return updated_ids, missing_ids

        # Project membership is stack-atomic, so the snapshot expands to whole
        # stacks - otherwise undo would restore the clicked picture and leave its
        # stack siblings on the new project.
        (updated_ids, missing_ids), _operation = (
            operation_log_service.run_recorded_metadata_task(
                server.vault,
                update_picture_projects,
                picture_ids,
                project_id_value,
                mode,
                op_type="pictures.project",
                picture_ids=picture_ids,
                expand_stacks=True,
                summary=f"Changed project membership ({mode}) "
                f"for {len(picture_ids)} picture(s)",
                **operation_log_service.request_context(request),
            )
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
                session.flush()

            return (
                sorted(updated_ids),
                sorted(skipped_ids),
                sorted(missing_ids),
                reset_triggered,
            )

        # Recorded as ONE operation so Ctrl+Z reverts the whole batch of ratings
        # rather than one picture at a time.
        (
            (updated_ids, skipped_ids, missing_ids, reset_triggered),
            _operation,
        ) = operation_log_service.run_recorded_metadata_task(
            server.vault,
            _apply_scores_batch,
            ordered_picture_ids,
            parsed_scores,
            only_unscored,
            op_type="pictures.score",
            picture_ids=ordered_picture_ids,
            summary=f"Rated {len(ordered_picture_ids)} picture(s)",
            **operation_log_service.request_context(request),
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
        # event is attributed to this user's own action - the SPA suppresses the
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
        # `metadata_fields()` is every scalar column minus the blobs, so the raw
        # `Picture.project_id` rides along; re-derive it from the narrowed
        # membership before it is serialised (issue #719, §16.6).
        narrow_picture_project_ids(server, request, [pic_dict])
        pic_dict["tags"] = serialize_tag_objects(pic_tags)
        # Locked sets freezing this picture, so the overlay can show the reason
        # without a second request.
        #
        # The authz gate authorizes the *picture* before the handler runs; it says nothing
        # about the related entities named in its payload. A set-scoped token may
        # legitimately read a picture that is also a member of some other, private
        # locked set, and that set's user-authored name (which routinely carries a
        # client / project / subject identifier) is not obtainable from
        # `GET /picture_sets`. So the *fact* of the freeze is disclosed -
        # `locked` is what the UI needs to disable the right controls - while the
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
        # Deny-by-default: only the picture's own column namespace (plus the
        # declared exceptions) is servable. This runs BEFORE the lookup so the
        # refusal cannot depend on whether the picture exists. Object
        # authorization is not this check's job and must not be added here --
        # the AuthzGate has already run (issue #721, §16.6).
        require_servable_field(Picture, field, PICTURE_EXTRA_SERVABLE_FIELDS)

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

        # NOTE: there is deliberately no `field == "thumbnail"` branch here.
        # `Picture` has no `thumbnail` attribute (thumbnails are files, served by
        # `GET /pictures/thumbnails/{id}.webp`), so the branch that used to sit
        # here raised `AttributeError` -> 500 on every call. The allowlist now
        # answers that name with the same 400 as any other non-column.
        if field in Picture.large_binary_fields():
            return {field: base64.b64encode(getattr(pic, field)).decode("utf-8")}
        if field == "project_id":
            # This route hands back any column by name, so it reaches the raw
            # scalar without going through the metadata payload. Same narrowing,
            # same reason (issue #719, §16.6), and the same shape the character
            # twin `GET /characters/{id}/{field}` already uses.
            payload = {"id": int(pic.id), "project_id": pic.project_id}
            narrow_picture_project_ids(server, request, [payload])
            return {"project_id": payload["project_id"]}
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
                        # Tag replacement is label data - frozen on a locked pic.
                        enforce_pictures_not_locked(
                            session, [pid], "replace tags on a locked picture"
                        )
                        session.exec(delete(Tag).where(Tag.picture_id == pid))
                        session.add_all([Tag(picture_id=pid, tag=t) for t in new_tags])
                        session.flush()

                    operation_log_service.run_recorded_metadata_task(
                        server.vault,
                        _replace_tags,
                        pic_id,
                        tag_values,
                        op_type="pictures.tags.replace",
                        picture_ids=[pic_id],
                        summary="Replaced the picture's tags",
                        **operation_log_service.request_context(request),
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
                # description and score are label/curation data - frozen on a
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
                session.flush()
                session.refresh(pic_db)
                return pic_db

            try:
                pic, _operation = operation_log_service.run_recorded_metadata_task(
                    server.vault,
                    apply_picture_updates,
                    picture_id,
                    updated_fields,
                    op_type="pictures.fields",
                    picture_ids=[picture_id],
                    summary="Edited "
                    + ", ".join(sorted(updated_fields))
                    + " on the picture",
                    **operation_log_service.request_context(request),
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
        description=(
            "Restores deleted pictures from scrapheap, either all deleted "
            "pictures or a provided picture id subset.\n\n"
            "Recorded in the operation log as a single "
            "`pictures.scrapheap.restore` operation with a `batch_id`, and it is "
            "the symmetric partner of `pictures.scrapheap.move`: undoing a "
            "restore puts the pictures back in the Scrapheap with the retention "
            "stamp they had, so the history stack stays coherent in both "
            "directions."
        ),
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
                # Leaving a stale deleted_at on a live picture would let any
                # future reader mistake it for a scrapheap deadline. The stamp
                # describes the CURRENT stay in the scrapheap only; a re-delete
                # writes a fresh one.
                pic.deleted_at = None
                session.add(pic)
                if pic.stack_id is not None:
                    affected_stack_ids.add(pic.stack_id)
                restored_count += 1
            # Re-fold restored pictures into their stack ordering so a restored
            # member is not left behind a (now lower-ranked) deleted leader.
            for stack_id in affected_stack_ids:
                normalize_stack_positions(session, stack_id)
            session.flush()
            return restored_count

        def _scrapheaped_targets(session: Session):
            # The endpoint's targets are not knowable from the request: an absent
            # picture_ids means "restore the entire scrapheap", and even a named
            # subset may include already-live ids that will not change. Resolve
            # the real set on the mutation's own session so the snapshot covers
            # exactly what the write is about to touch.
            query = select(Picture.id).where(Picture.deleted.is_(True))
            if picture_ids is not None:
                query = query.where(Picture.id.in_(picture_ids))
            return list(session.exec(query).all())

        restored_count, _operation = operation_log_service.run_recorded_metadata_task(
            server.vault,
            restore_pictures,
            picture_ids,
            op_type=operation_log_service.OP_SCRAPHEAP_RESTORE,
            picture_ids=[],
            resolve_picture_ids=_scrapheaped_targets,
            # Same stack caveat as the soft-delete: normalize_stack_positions
            # renumbers every member of an affected stack, deleted ones included.
            expand_stacks=True,
            expand_stacks_include_deleted=True,
            summary=operation_log_service.scrapheap_restore_summary,
            # Always batched: the caller's gesture id when it sent one, a
            # server-minted ``srv-…`` otherwise.
            **operation_log_service.request_context(
                request, fallback_batch_id=operation_log_service.new_batch_id()
            ),
        )
        # A restored picture re-enters active views. ``picture_ids`` is the
        # caller-supplied subset (None == "restore all"); pass it through when
        # known so the originating tab can target the affected cards.
        #
        # ``restored``, not ``added``: the card comes back, but the picture is
        # not new to the vault. The SPA's sidebar treats ``added`` as a fresh
        # import and flashes its NEW marker on the counts that grew, which is
        # wrong for something that was in the library the whole time.
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": list(picture_ids) if picture_ids else [],
                "origin_client_id": origin_client_id,
                "change_kind": "restored",
            },
        )
        return {"status": "success", "restored_count": restored_count}

    @router.post(
        "/pictures/scrapheap/delete-preview",
        summary="Preview a scrapheap delete-forever",
        description=(
            "Authoritative preview of a scrapheap delete-forever, computed over "
            "ALL matching scrapheap rows - never a virtualized/grid subset - so "
            "the confirmation can name each file at risk. Body "
            "{ids: int[] | null}; null/omitted = the entire scrapheap.\n\n"
            "`total_count` splits into three DISJOINT counts that sum to it, "
            "keyed on which action destroys the row so no count overstates "
            "destruction:\n\n"
            "- `locked_count` - frozen by a locked picture set; destroyed by "
            "NEITHER option. A lock overrides `include_protected`, so a row that "
            "is locked AND protected counts here, not under `protected_count`.\n"
            "- `protected_count` - protected reference-folder originals "
            "(`allow_delete_file=false`) that are not locked; destroyed only by "
            "`include_protected=true`. `protected` lists each one's absolute "
            "on-disk path.\n"
            "- `unprotected_count` - neither; destroyed by both options.\n\n"
            'So "Delete unprotected only" destroys exactly `unprotected_count` '
            'and "Delete all" destroys exactly '
            "`unprotected_count + protected_count`."
        ),
        response_model=ScrapheapDeletePreviewResponse,
    )
    def preview_scrapheap_delete(request: Request, payload: dict | None = Body(None)):
        lease = request.state.library_lease
        ids = _parse_scrapheap_ids(payload)
        rows = scrapheap_service.fetch_scrapheap_rows(server.vault, ids)
        if not rows:
            return {
                "total_count": 0,
                "protected_count": 0,
                "locked_count": 0,
                "unprotected_count": 0,
                "protected": [],
                "locked": [],
                "confirm_token": scrapheap_confirmations.issue(
                    ids,
                    0,
                    library_uuid=lease.library_uuid,
                    generation=lease.generation,
                ),
            }
        no_delete_folder_ids = scrapheap_service.fetch_no_delete_folder_ids(
            server.vault
        )
        # Same shared lock lookup the purge and the listing use, so the preview
        # cannot promise a deletion the delete endpoint will then refuse.
        locked_ids = scrapheap_service.locked_scrapheap_picture_ids(
            server.vault, [row.id for row in rows if row.id is not None]
        )
        preview = scrapheap_service.classify_delete_preview(
            rows, no_delete_folder_ids, locked_ids
        )
        # Resolve the at-risk originals to absolute on-disk paths so the confirm
        # dialog can name each file "Delete all" would os.remove.
        image_root = server.vault.image_root
        for item in preview["protected"]:
            item["file_path"] = (
                ImageUtils.resolve_picture_path(image_root, item["file_path"])
                or item["file_path"]
            )
        # The confirmation the destructive call must echo back. Minted here, and
        # only here, so a delete can never run without these counts having been
        # computed for exactly this selection first.
        preview["confirm_token"] = scrapheap_confirmations.issue(
            ids,
            preview["total_count"],
            library_uuid=lease.library_uuid,
            generation=lease.generation,
        )
        return preview

    @router.delete(
        "/pictures/scrapheap",
        summary="Permanently delete scrapheap pictures",
        description=(
            "Permanently removes deleted pictures from database and disk for the "
            "provided ids (or all scrapheap items when omitted). Body may carry "
            "include_protected (default false): when false, protected "
            "reference-folder originals in the selection are SKIPPED ENTIRELY "
            "(row kept, file untouched, still in the scrapheap) and only "
            "unprotected pictures are purged; when true, protected originals are "
            "destroyed too.\n\n"
            "Pictures frozen by a LOCKED picture set (directly or via a stack "
            "sibling) are never destroyed here, at EITHER include_protected "
            "value - a locked set is a hard whole-set freeze, and this is the "
            "one irreversible path, so it must not be the one that ignores it. "
            "They are skipped and returned in `skipped_locked` rather than "
            "failing the request; unlock the set to delete them.\n\n"
            "**A `confirm_token` is REQUIRED.** Call "
            "`POST /pictures/scrapheap/delete-preview` first - it reports "
            "exactly what each option will destroy and mints the token - then "
            "send that token back here. It is single-use, expires after five "
            "minutes, and is bound to the previewed selection, so this "
            "irreversible endpoint cannot be driven without the destruction "
            "preview having been fetched for exactly these pictures. A missing "
            "token is a 400; an unknown, spent, expired or wrong-selection "
            "token is a 409 and destroys nothing."
        ),
        response_model=ScrapheapDeleteResponse,
        responses={
            400: {"description": "confirm_token missing."},
            409: {
                "description": (
                    "confirm_token is unknown, already spent, expired, or was "
                    "minted for a different selection. Nothing was destroyed."
                )
            },
        },
    )
    def delete_scrapheap_selection(
        request: Request,
        background_tasks: BackgroundTasks,
        payload: dict | None = Body(None),
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        ids = _parse_scrapheap_ids(payload)
        # Server-side intent check, NOT an authorization check (the AuthzGate
        # owns authorization: OWNER_ONLY in authz/registry.py). The type-to-
        # confirm dialog lives entirely in the client, so without this the one
        # irreversible endpoint in the product accepted a bare, bodyless DELETE.
        confirmed, reason = scrapheap_confirmations.redeem(
            payload.get("confirm_token") if isinstance(payload, dict) else None,
            ids,
            library_uuid=request.state.library_lease.library_uuid,
            generation=request.state.library_lease.generation,
        )
        if not confirmed:
            if reason == scrapheap_service.CONFIRM_MISSING:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "confirm_token is required. Call POST "
                        "/pictures/scrapheap/delete-preview and send back the "
                        "confirm_token it returns; nothing was deleted."
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    "This delete confirmation is no longer valid (already used, "
                    "expired, or for a different selection). Nothing was "
                    "deleted - re-check what would be destroyed and try again."
                ),
            )
        # Default false: absent body (delete-all) never destroys protected
        # originals - that requires an explicit include_protected=true.
        include_protected = bool(
            payload.get("include_protected", False)
            if isinstance(payload, dict)
            else False
        )

        outcome = scrapheap_service.purge_scrapheap_pictures(
            server.vault,
            ids,
            include_protected,
            # Files are removed after the response is sent so a large purge never
            # blocks the request; the DB rows + ledger are already committed.
            schedule_file_removal=background_tasks.add_task,
        )
        picture_ids = outcome.purged_ids
        if picture_ids:
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
        snapshots_with_deleted = (
            server.vault.snapshot_service.snapshots_containing(picture_ids)
            if picture_ids
            else []
        )
        return {
            "status": "success",
            "deleted_count": outcome.deleted_count,
            "skipped_count": outcome.skipped_count,
            "skipped_locked": outcome.skipped_locked,
            "skipped_restored": outcome.skipped_restored,
            "include_protected": include_protected,
            "snapshots_with_deleted": snapshots_with_deleted,
        }

    @router.delete(
        "/pictures/{id}",
        summary="Move picture to scrapheap",
        description=(
            "Soft-deletes a picture by marking it deleted, making it appear in "
            "scrapheap views. Recorded in the operation log as "
            "`pictures.scrapheap.move` and **undoable**: undo restores the "
            "picture, redo moves it back. (A *permanent* delete - "
            "`DELETE /pictures/scrapheap` - is not recorded and cannot be undone.)"
        ),
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
            # Soft-deleting a member would silently mutate a frozen set - refuse.
            enforce_pictures_not_locked(session, [pic.id], "delete a locked picture")
            if pic.deleted:
                return True
            pic.deleted = True
            # Start (or restart) the scrapheap retention clock on the
            # False -> True transition only, so re-issuing DELETE on an
            # already-scrapheaped picture cannot silently extend its window.
            pic.deleted_at = datetime.now(timezone.utc)
            session.add(pic)
            # Promote a live member to the leader slot: a soft-deleted picture
            # must not keep stack_position 0, or the whole stack disappears from
            # the grid (no-op when the picture is not stacked).
            normalize_stack_positions(session, pic.stack_id)
            session.flush()
            return True

        # The soft-delete is a recorded, reversible operation: the `deleted`
        # facet carries the flag and the retention stamp, so undo puts the
        # picture back with the purge deadline it had. The snapshot expands to
        # the whole stack INCLUDING its scrapheaped members, because
        # normalize_stack_positions renumbers every member and an unsnapshotted
        # renumber is a change undo could not reverse.
        success, _operation = operation_log_service.run_recorded_metadata_task(
            server.vault,
            delete_pic,
            id,
            op_type=operation_log_service.OP_SCRAPHEAP_MOVE,
            picture_ids=[id],
            expand_stacks=True,
            expand_stacks_include_deleted=True,
            summary=operation_log_service.scrapheap_move_summary,
            **operation_log_service.request_context(request),
        )
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
            "id, which floods the client connection pool on large selections.\n\n"
            "Recorded in the operation log as a single `pictures.scrapheap.move` "
            "operation carrying a `batch_id`, so the whole bulk move is **one** "
            "undo: `POST /operations/undo` or "
            "`POST /operations/batches/{batch_id}/undo` restores every picture it "
            "moved. Pictures skipped for a locked set are not in the recorded "
            "change and are unaffected by the undo."
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
            deleted_at = datetime.now(timezone.utc)
            for pid in ids:
                if pid in locked:
                    continue
                pic = session.get(Picture, pid)
                if not pic or pic.deleted:
                    continue
                pic.deleted = True
                # Same retention clock as the single-picture soft delete; stamped
                # only on the False -> True transition (already-deleted rows are
                # skipped by the guard above).
                pic.deleted_at = deleted_at
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
            session.flush()
            return newly_deleted, sorted(locked)

        # One bulk action, one operation row, one batch id - so the client can
        # offer a single "Undo" for the whole move, by batch id or by simply
        # popping the newest operation.
        (deleted_ids, skipped_locked), _operation = (
            operation_log_service.run_recorded_metadata_task(
                server.vault,
                delete_pics,
                pic_ids,
                op_type=operation_log_service.OP_SCRAPHEAP_MOVE,
                picture_ids=pic_ids,
                expand_stacks=True,
                expand_stacks_include_deleted=True,
                summary=operation_log_service.scrapheap_move_summary,
                # Always batched: the caller's gesture id when it sent one, a
                # server-minted ``srv-…`` otherwise.
                **operation_log_service.request_context(
                    request, fallback_batch_id=operation_log_service.new_batch_id()
                ),
            )
        )
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

    @router.post(
        "/pictures/rotate",
        summary="Rotate pictures in place",
        description=(
            "Turns pictures by rewriting **only** their EXIF orientation tag. Not "
            "one pixel byte is re-encoded, so a JPEG takes no generational loss "
            "and a PNG keeps its ComfyUI workflow/prompt chunks. Body: "
            '{"picture_ids": [int, ...], "direction": "cw"|"ccw"|"180"}.\n\n'
            "Three disjoint result buckets that together account for every id "
            "sent: `rotated_picture_ids`, `unsupported_picture_ids` (a "
            "reference-folder original, or a container with no orientation tag "
            "this server can splice - use the `rotate` image plugin, which "
            "produces a rotated *copy*) and `skipped_picture_ids` (no row, "
            "already in the Scrapheap, or the write failed).\n\n"
            "Recorded as a single `pictures.rotate` operation carrying a "
            "`batch_id`, so the whole gesture is **one** undo. Undo is exact: the "
            "log stores the orientation the file had, not the turn that was "
            "applied, so undoing twice is the same as undoing once."
        ),
        response_model=PictureRotateResponse,
    )
    def rotate_pictures(request: Request, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        maybe_ids = payload.get("picture_ids") if isinstance(payload, dict) else None
        if not isinstance(maybe_ids, list) or not maybe_ids:
            raise HTTPException(
                status_code=400, detail="picture_ids must be a non-empty list"
            )
        if len(maybe_ids) > ROTATE_MAX_IDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"picture_ids exceeds the maximum of {ROTATE_MAX_IDS} ids "
                    "per request"
                ),
            )
        try:
            pic_ids = [int(pid) for pid in maybe_ids]
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="picture_ids must contain valid integers"
            )
        direction = str((payload or {}).get("direction") or "").strip().lower()
        if direction not in ROTATE_DIRECTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"direction must be one of {list(ROTATE_DIRECTIONS)}",
            )

        image_root = server.vault.image_root

        def prime_orientation(session, ids):
            """Fill the orientation mirror of any target that has none yet.

            ``capture_state_in_session`` reads the mirror, and it runs *before*
            the mutation - so a target still NULL here would record
            ``before_state {"orientation": null}`` and its undo would have
            nothing to write back. Every row predating the column is NULL, and
            ``MissingOrientationFinder`` may not have reached this one yet.

            Its own DB task, ahead of the recorded one, and it writes only where
            the column is NULL. A rotate that lands in between therefore cannot
            be clobbered: it leaves the column non-NULL, so this skips the row.

            The lock check is repeated here, ahead of ``rotate_pics``'s own, so a
            request that is going to be refused with 423 does not first open up
            to ``ROTATE_MAX_IDS`` files on the DB writer thread and write to rows
            the caller is not allowed to touch.
            """
            enforce_pictures_not_locked(session, ids, "rotate")
            for pid in ids:
                pic = session.get(Picture, pid)
                if pic is None or pic.orientation is not None or not pic.file_path:
                    continue
                path = ImageUtils.resolve_picture_path(image_root, pic.file_path)
                if not path:
                    continue
                pic.orientation = read_orientation(path)
                session.add(pic)
            session.commit()

        server.vault.db.run_task(prime_orientation, pic_ids)

        def rotate_pics(session, ids, direction):
            # A hard refusal, not a skip: a rotate rewrites the user's original
            # file, and quietly turning some of a frozen selection is worse than
            # turning none of it.
            enforce_pictures_not_locked(session, ids, "rotate")
            rotated: list[int] = []
            unsupported: list[int] = []
            skipped: list[int] = []
            for pid in ids:
                pic = session.get(Picture, pid)
                if pic is None or pic.deleted or not pic.file_path:
                    skipped.append(pid)
                    continue
                # A reference-folder picture is the user's own file, managed
                # outside the library: the mount may be read-only, and the
                # changed mtime reads as a source swap to the next scan.
                if pic.reference_folder_id is not None:
                    unsupported.append(pid)
                    continue
                path = ImageUtils.resolve_picture_path(image_root, pic.file_path)
                if not path:
                    # A file problem, not a format one - so `skipped`, beside the
                    # other "there is nothing on disk to turn" cases above. The
                    # two buckets are advice, not bookkeeping: the client answers
                    # `unsupported` with "use Filters > Rotate to make a rotated
                    # copy", which is the wrong thing to tell someone whose file
                    # cannot be located.
                    skipped.append(pid)
                    continue
                if not supports_in_place_rotation(path):
                    unsupported.append(pid)
                    continue
                # Read INSIDE the DB task, never in the handler: two concurrent
                # rotates reading in their handlers would both see 1, both write
                # 6, and one turn would be silently lost.
                try:
                    target = rotate_orientation(read_orientation(path), direction)
                    turned = operation_log_service.apply_orientation(
                        session, pid, target, image_root=image_root
                    )
                except (OSError, ValueError) as exc:
                    logger.warning(
                        "rotate_pictures: could not turn picture_id=%s path=%s "
                        "%s (%s); it is reported as skipped and its file is "
                        "unchanged",
                        pid,
                        path,
                        direction,
                        exc,
                    )
                    skipped.append(pid)
                    continue
                (rotated if turned else skipped).append(pid)
            session.flush()
            return rotated, unsupported, skipped

        # Always batched, so one Undo reverses the whole gesture: the caller's
        # gesture id when it sent one, a server-minted ``srv-…`` otherwise.
        context = operation_log_service.request_context(
            request, fallback_batch_id=operation_log_service.new_batch_id()
        )
        (rotated_ids, unsupported_ids, skipped_ids), _operation = (
            operation_log_service.run_recorded_metadata_task(
                server.vault,
                rotate_pics,
                pic_ids,
                direction,
                op_type=operation_log_service.OP_PICTURES_ROTATE,
                picture_ids=pic_ids,
                summary=_rotate_summary(direction),
                **context,
            )
        )
        if rotated_ids:
            # The card keeps its place and its id; its bitmap and its aspect
            # ratio changed. ``updated`` is the kind for that.
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": rotated_ids,
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )
        return {
            "status": "success",
            "rotated_picture_ids": rotated_ids,
            "unsupported_picture_ids": unsupported_ids,
            "skipped_picture_ids": skipped_ids,
            "batch_id": context.get("batch_id") if rotated_ids else None,
        }
