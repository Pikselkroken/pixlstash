import base64
import os
import re
from io import BytesIO
from email.utils import formatdate

from PIL import Image
from fastapi import (
    BackgroundTasks,
    Body,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import (
    case,
    delete,
    func,
    update,
)
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
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
from pixlstash.picture_scoring import (
    compute_character_likeness_for_faces,
    select_reference_faces_for_character,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.caption_utils import (
    serialize_tag_objects,
    sync_picture_sidecar,
)
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.utils.watermark import apply_watermark, get_watermark_bytes

from ._helpers import (
    MEDIA_TYPE_BY_FORMAT,
    enforce_picture_scope,
    _score_anchor_membership_changed,
)


logger = get_logger(__name__)


def register_routes(router, server):
    @router.patch(
        "/pictures/project",
        summary="Set project for pictures",
        description="Assigns, removes, or clears project association for a batch of pictures.",
    )
    def set_project_for_pictures(payload: dict = Body(...)):
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

            pics = session.exec(
                select(Picture)
                .where(Picture.id.in_(ids))
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
            server.vault.notify(EventType.CHANGED_PICTURES)

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
    )
    def apply_scores_for_pictures(payload: dict = Body(...)):
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
            server.vault.notify(EventType.CHANGED_PICTURES)

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
        summary="Create manual face entry",
        description="Adds a face bounding box to a picture and frame index, updating sentinel/ordering behavior for manual annotations.",
    )
    def create_picture_face(id: str, payload: dict = Body(...)):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

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
        server.vault.notify(EventType.CHANGED_PICTURES)
        return safe_model_dict(face)

    @router.delete(
        "/pictures/{id}/face/{index}",
        summary="Delete face by index",
        description="Deletes a face at frame 0 by index and reindexes remaining faces for stable ordering.",
    )
    def delete_picture_face(id: str, index: int):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

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
        server.vault.notify(EventType.CHANGED_PICTURES)
        return {"status": "success", "message": "Face deleted."}

    @router.get(
        "/pictures/{id}/character_likeness",
        summary="Get picture character likeness",
        description="Computes max character-likeness score for faces in a picture against a reference character.",
    )
    def get_picture_character_likeness(
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
                select(PictureSetMember.id).where(PictureSetMember.picture_id == pic_id)
            ).first()
            return member is not None

        if character_id == "UNASSIGNED" and (
            server.vault.db.run_task(has_assigned_faces)
            or server.vault.db.run_task(is_in_picture_set)
        ):
            return {
                "picture_id": pic_id,
                "character_likeness": None,
                "eligible": False,
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
                return {
                    "picture_id": pic_id,
                    "character_likeness": None,
                    "eligible": False,
                }
            return {
                "picture_id": pic_id,
                "character_likeness": 0.0,
                "eligible": True,
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
            return {
                "picture_id": pic_id,
                "character_likeness": 0.0,
                "eligible": False,
            }
        score = 0.0
        for face_id in face_ids:
            score = max(score, float(likeness_map.get(face_id, 0.0)))

        return {
            "picture_id": pic_id,
            "character_likeness": score,
            "eligible": True,
        }

    @router.get(
        "/pictures/{id}/{field}",
        summary="Get raw picture field",
        description="Returns a single picture field value; large binary fields are base64 encoded and thumbnail returns image bytes.",
    )
    def get_picture_field(id: str, field: str):
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
            server.vault.notify(EventType.CHANGED_PICTURES, [picture_id])

        # Write back description to caption sidecar when enabled.
        sync_picture_sidecar(server, picture_id)

        return {"status": "success", "picture": safe_model_dict(pic)}

    @router.post(
        "/pictures/scrapheap/restore",
        summary="Restore deleted pictures",
        description="Restores deleted pictures from scrapheap, either all deleted pictures or a provided picture id subset.",
    )
    def restore_scrapheap(payload: dict | None = Body(None)):
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
                Picture.import_excluded.is_(False),
            )
            if ids is not None:
                query = query.where(Picture.id.in_(ids))
            pics = session.exec(query).all()
            restored_count = 0
            for pic in pics:
                pic.deleted = False
                session.add(pic)
                restored_count += 1
            session.commit()
            return restored_count

        restored_count = server.vault.db.run_task(
            restore_pictures,
            picture_ids,
            priority=DBPriority.IMMEDIATE,
        )
        server.vault.notify(EventType.CHANGED_PICTURES)
        return {"status": "success", "restored_count": restored_count}

    @router.delete(
        "/pictures/scrapheap",
        summary="Permanently delete scrapheap pictures",
        description="Permanently removes deleted pictures from database and disk for provided ids or for all scrapheap items when omitted.",
    )
    def delete_scrapheap_selection(
        background_tasks: BackgroundTasks,
        payload: dict | None = Body(None),
    ):
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
                Picture.id, Picture.file_path, Picture.reference_folder_id
            ).where(
                Picture.deleted.is_(True),
                Picture.import_excluded.is_(False),
            )
            if ids is not None:
                query = query.where(Picture.id.in_(ids))
            return session.exec(query).all()

        rows = server.vault.db.run_task(
            fetch_deleted, ids, priority=DBPriority.IMMEDIATE
        )
        if not rows:
            return {"status": "success", "deleted_count": 0}

        # Find reference folders where files must not be deleted; for those
        # pictures keep the DB row as a scan sentinel (import_excluded=True) so
        # the file is never re-imported on the next scan pass.
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

        full_delete_ids: list[int] = []
        full_delete_file_paths: list[str] = []
        sentinel_ids: list[int] = []

        for row in rows:
            pic_id, file_path, ref_folder_id = row[0], row[1], row[2]
            if ref_folder_id is not None and ref_folder_id in no_delete_folder_ids:
                # Source file is protected — keep the DB row as a scan sentinel.
                if pic_id is not None:
                    sentinel_ids.append(pic_id)
            else:
                if pic_id is not None:
                    full_delete_ids.append(pic_id)
                if file_path:
                    full_delete_file_paths.append(file_path)

        if sentinel_ids:

            def mark_sentinels(session: Session, ids: list[int]) -> None:
                session.exec(
                    update(Picture)
                    .where(Picture.id.in_(ids))
                    .values(import_excluded=True)
                )
                session.commit()

            server.vault.db.run_task(
                mark_sentinels, sentinel_ids, priority=DBPriority.IMMEDIATE
            )
            logger.debug(
                "Scrapheap flush: kept %d picture(s) as import sentinels "
                "(allow_delete_file=False on reference folder).",
                len(sentinel_ids),
            )

        picture_ids = full_delete_ids
        file_paths = full_delete_file_paths

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

        def delete_rows(session: Session, ids: list[int]):
            if not ids:
                return 0
            session.exec(delete(Picture).where(Picture.id.in_(ids)))
            session.commit()
            return len(ids)

        deleted_count = server.vault.db.run_task(
            delete_rows,
            picture_ids,
            priority=DBPriority.IMMEDIATE,
        )
        server.vault.notify(EventType.CHANGED_PICTURES)
        return {
            "status": "success",
            "deleted_count": deleted_count + len(sentinel_ids),
        }

    @router.delete(
        "/pictures/{id}",
        summary="Move picture to scrapheap",
        description="Soft-deletes a picture by marking it deleted, making it appear in scrapheap views.",
    )
    def delete_picture(id: str):
        def delete_pic(session, id):
            pic = session.get(Picture, id)
            if not pic:
                return False
            if pic.deleted:
                return True
            pic.deleted = True
            session.add(pic)
            session.commit()
            return True

        success = server.vault.db.run_task(delete_pic, id)
        if not success:
            raise HTTPException(status_code=404, detail="Picture not found")
        return JSONResponse(
            content={"status": "success", "message": f"Picture id={id} deleted."}
        )
