import ast
import asyncio
import concurrent.futures
import base64
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from io import BytesIO
from collections import defaultdict, deque, OrderedDict
from email.utils import formatdate
from datetime import datetime

from PIL import Image
from fastapi import (
    APIRouter,
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
from sqlalchemy import (
    case,
    delete,
    func,
    or_,
    text,
    update,
)
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureLikeness,
    PictureProjectMember,
    PictureSetMember,
    Project,
    ReferenceFolder,
    SortMechanism,
    Tag,
)
from pixlstash.db_models.guest_score import GuestScore
from pixlstash.db_models.user import User
from pixlstash.db_models.user_token import UserToken
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.picture_scoring import (
    compute_character_likeness_for_faces,
    fetch_smart_score_data,
    find_pictures_by_character_likeness,
    get_smart_score_penalised_tags_from_request,
    prepare_smart_score_inputs,
    select_reference_faces_for_character,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.quality.smart_score_utils import SmartScoreUtils
from pixlstash.utils.service.caption_utils import (
    _normalize_hidden_tags,
    serialize_tag_objects,
    sync_picture_sidecar,
)
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.utils.stack.stack_utils import _deduplicate_by_stack
from pixlstash.utils.watermark import apply_watermark, get_watermark_bytes
from pixlstash.tasks import TaskType
from pixlstash.db_models.tag import TAG_EMPTY_SENTINEL
from pixlstash.services._filter_helpers import (
    collect_set_filter_ids,
    fetch_set_candidate_ids,
    normalize_set_mode,
    project_membership_exists_clause,
    project_unassigned_clause,
)
from pixlstash.services import plugin_service
from pixlstash.services.picture_stats import PictureStatsParams, compute_picture_stats

from ._helpers import enforce_picture_scope


logger = get_logger(__name__)


def register_routes(router, server):
    thumbnail_generation_locks: dict[int, asyncio.Lock] = {}
    thumbnail_memory_cache: OrderedDict[int, bytes] = OrderedDict()
    thumbnail_memory_cache_max = 128


    def get_thumbnail_lock(picture_id: int) -> asyncio.Lock:
        lock = thumbnail_generation_locks.get(picture_id)
        if lock is None:
            lock = asyncio.Lock()
            thumbnail_generation_locks[picture_id] = lock
        return lock

    def get_cached_thumbnail_bytes(picture_id: int) -> bytes | None:
        data = thumbnail_memory_cache.pop(picture_id, None)
        if data is None:
            return None
        thumbnail_memory_cache[picture_id] = data
        return data

    def cache_thumbnail_bytes(picture_id: int, thumbnail_bytes: bytes) -> None:
        if not thumbnail_bytes:
            return
        if picture_id in thumbnail_memory_cache:
            thumbnail_memory_cache.pop(picture_id, None)
        thumbnail_memory_cache[picture_id] = thumbnail_bytes
        while len(thumbnail_memory_cache) > thumbnail_memory_cache_max:
            thumbnail_memory_cache.popitem(last=False)


    @router.get(
        "/pictures/thumbnails/{id}.webp",
        summary="Get picture thumbnail image",
        description="Returns a WebP thumbnail for a picture id, generating and caching it on demand when needed.",
    )
    async def get_thumbnail(request: Request, id: int):
        started_at = datetime.now()

        def fetch_picture(session: Session, picture_id: int):
            pics = Picture.find(
                session,
                id=picture_id,
                select_fields=[
                    "id",
                    "file_path",
                ],
                include_deleted=True,
                include_unimported=True,
            )
            return pics[0] if pics else None

        pic = server.vault.db.run_immediate_read_task(fetch_picture, id)
        if not pic or not getattr(pic, "file_path", None):
            raise HTTPException(status_code=404, detail="Picture not found")
        enforce_picture_scope(server, request, id)

        thumb_path = ImageUtils.get_thumbnail_path(
            server.vault.image_root, pic.file_path
        )
        if thumb_path and os.path.exists(thumb_path):
            # For reference-folder pictures (absolute file_path) the source file
            # can change when a Docker volume is remapped to a different host
            # directory while the container path stays the same.  If the source
            # file is newer than the cached thumbnail we treat it as stale and
            # regenerate so the user always sees the correct image.
            stale = False
            if pic.file_path and os.path.isabs(pic.file_path):
                source_path = ImageUtils.resolve_picture_path(
                    server.vault.image_root, pic.file_path
                )
                if source_path and os.path.exists(source_path):
                    try:
                        source_mtime = os.path.getmtime(source_path)
                        thumb_mtime = os.path.getmtime(thumb_path)
                        if source_mtime > thumb_mtime:
                            stale = True
                            logger.debug(
                                "Thumbnail stale (source newer): id=%s source=%s",
                                id,
                                source_path,
                            )
                            try:
                                os.remove(thumb_path)
                            except Exception as exc:
                                logger.warning(
                                    "Failed to remove stale thumbnail %s: %s",
                                    thumb_path,
                                    exc,
                                )
                    except Exception as exc:
                        logger.debug(
                            "Could not compare thumbnail mtime for id=%s: %s", id, exc
                        )
            if not stale:
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.debug(
                    "Thumbnail GET cache-hit: id=%s path=%s elapsed_ms=%.1f",
                    id,
                    thumb_path,
                    elapsed_ms,
                )
                return FileResponse(thumb_path, media_type="image/webp")

        cached_bytes = get_cached_thumbnail_bytes(id)
        if cached_bytes:
            elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
            logger.debug(
                "Thumbnail GET memory-hit: id=%s elapsed_ms=%.1f",
                id,
                elapsed_ms,
            )
            return Response(content=cached_bytes, media_type="image/webp")

        lock = get_thumbnail_lock(id)
        async with lock:
            if thumb_path and os.path.exists(thumb_path):
                # Re-check staleness inside the lock.
                recheck_stale = False
                if pic.file_path and os.path.isabs(pic.file_path):
                    source_path = ImageUtils.resolve_picture_path(
                        server.vault.image_root, pic.file_path
                    )
                    if source_path and os.path.exists(source_path):
                        try:
                            if os.path.getmtime(source_path) > os.path.getmtime(
                                thumb_path
                            ):
                                recheck_stale = True
                                try:
                                    os.remove(thumb_path)
                                except Exception as exc:
                                    logger.warning(
                                        "Failed to remove stale thumbnail on recheck %s: %s",
                                        thumb_path,
                                        exc,
                                    )
                        except Exception as exc:
                            logger.warning(
                                "Failed to compare thumbnail mtime on recheck %s: %s",
                                thumb_path,
                                exc,
                            )
                if not recheck_stale:
                    elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                    logger.debug(
                        "Thumbnail GET cache-hit-after-wait: id=%s path=%s elapsed_ms=%.1f",
                        id,
                        thumb_path,
                        elapsed_ms,
                    )
                    return FileResponse(thumb_path, media_type="image/webp")

            cached_bytes = get_cached_thumbnail_bytes(id)
            if cached_bytes:
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.debug(
                    "Thumbnail GET memory-hit-after-wait: id=%s elapsed_ms=%.1f",
                    id,
                    elapsed_ms,
                )
                return Response(content=cached_bytes, media_type="image/webp")

            def build_thumbnail_blocking() -> tuple[
                str, str | None, bytes | None, str | None
            ]:
                resolved = ImageUtils.resolve_picture_path(
                    server.vault.image_root, pic.file_path
                )
                if not resolved or not os.path.exists(resolved):
                    return "missing-source", resolved, None, None

                img = ImageUtils.load_image_or_video(resolved)
                if img is None:
                    return "load-failed", resolved, None, None

                if not isinstance(img, Image.Image):
                    img = Image.fromarray(img)

                thumbnail_bytes = ImageUtils.generate_thumbnail_bytes(img)
                if not thumbnail_bytes:
                    return "encode-failed", resolved, None, None

                saved_thumb_path = ImageUtils.write_thumbnail_bytes(
                    server.vault.image_root, pic.file_path, thumbnail_bytes
                )
                if saved_thumb_path and os.path.exists(saved_thumb_path):
                    return "saved", resolved, None, saved_thumb_path

                return "memory-only", resolved, thumbnail_bytes, None

            (
                status,
                resolved_path,
                thumbnail_bytes,
                saved_thumb,
            ) = await asyncio.to_thread(build_thumbnail_blocking)

            if status == "saved" and saved_thumb:
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.debug(
                    "Thumbnail GET generated: id=%s source=%s elapsed_ms=%.1f",
                    id,
                    resolved_path,
                    elapsed_ms,
                )
                return FileResponse(saved_thumb, media_type="image/webp")

            if status == "memory-only" and thumbnail_bytes:
                cache_thumbnail_bytes(id, thumbnail_bytes)
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.warning(
                    "Thumbnail GET generated-memory-only: id=%s source=%s elapsed_ms=%.1f",
                    id,
                    resolved_path,
                    elapsed_ms,
                )
                return Response(content=thumbnail_bytes, media_type="image/webp")

            if status == "missing-source":
                logger.warning(
                    "Missing source file for on-demand thumbnail: %s",
                    resolved_path,
                )
            elif status == "load-failed":
                logger.warning(
                    "Failed to load image for on-demand thumbnail: %s",
                    resolved_path,
                )
            elif status == "encode-failed":
                logger.warning(
                    "Failed to encode on-demand thumbnail: %s",
                    resolved_path,
                )

        elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
        logger.warning(
            "Thumbnail GET failed: id=%s elapsed_ms=%.1f",
            id,
            elapsed_ms,
        )

        raise HTTPException(status_code=404, detail="Thumbnail not found")

    @router.post(
        "/pictures/thumbnails",
        summary="Get batch thumbnail metadata",
        description="Returns thumbnail URLs and mapped face/hand overlays for a list of picture ids, including penalised-tag hints.",
    )
    def get_thumbnails(request: Request, payload: dict = Body(...)):
        ids = payload.get("ids", [])
        if not isinstance(ids, list):
            raise HTTPException(status_code=400, detail="'ids' must be a list")

        logger.debug(
            "Thumbnail batch request: client=%s count=%s ids_preview=%s",
            getattr(getattr(request, "client", None), "host", None),
            len(ids),
            ids[:8],
        )

        penalised_tags = get_smart_score_penalised_tags_from_request(server, request)
        penalised_tag_set = {
            str(tag).strip().lower() for tag in (penalised_tags or {}).keys() if tag
        }
        ids_int = []
        for raw_id in ids:
            try:
                ids_int.append(int(raw_id))
            except (TypeError, ValueError):
                continue

        penalised_tag_map = defaultdict(list)
        if ids_int and penalised_tag_set:

            def fetch_penalised_tags(session: Session):
                return session.exec(
                    select(Tag.picture_id, Tag.tag).where(
                        Tag.picture_id.in_(ids_int),
                        Tag.tag.is_not(None),
                        func.lower(Tag.tag).in_(penalised_tag_set),
                    )
                ).all()

            rows = server.vault.db.run_task(
                fetch_penalised_tags, priority=DBPriority.IMMEDIATE
            )
            for pic_id, tag in rows or []:
                if tag:
                    penalised_tag_map[pic_id].append(tag)

        def map_bbox_to_thumbnail(bbox, picture):
            if not bbox or len(bbox) != 4:
                return bbox, False
            left = getattr(picture, "thumbnail_left", None)
            top = getattr(picture, "thumbnail_top", None)
            side = getattr(picture, "thumbnail_side", None)
            if left is None or top is None or side in (None, 0):
                return bbox, False
            try:
                scale = 256.0 / float(side)
                x1, y1, x2, y2 = bbox
                x1 = max(0.0, min(256.0, (x1 - left) * scale))
                y1 = max(0.0, min(256.0, (y1 - top) * scale))
                x2 = max(0.0, min(256.0, (x2 - left) * scale))
                y2 = max(0.0, min(256.0, (y2 - top) * scale))
                return (
                    [
                        int(round(x1)),
                        int(round(y1)),
                        int(round(x2)),
                        int(round(y2)),
                    ],
                    True,
                )
            except Exception:
                return bbox, False

        pics = server.vault.db.run_task(
            lambda session: Picture.find(
                session,
                id=ids,
                select_fields=[
                    "id",
                    "file_path",
                    "faces",
                    "thumbnail_left",
                    "thumbnail_top",
                    "thumbnail_side",
                    "imported_at",
                ],
                include_deleted=True,
                include_unimported=True,
            ),
            priority=DBPriority.IMMEDIATE,
        )
        logger.debug(
            "Thumbnail batch resolved: requested=%s found=%s",
            len(ids),
            len(pics or []),
        )
        character_name_map = {}
        character_ids = set()
        for pic in pics:
            for face in getattr(pic, "faces", []):
                if getattr(face, "character_id", None) is not None:
                    character_ids.add(face.character_id)
        if character_ids:

            def fetch_character_names(session: Session):
                return session.exec(
                    select(Character.id, Character.name).where(
                        Character.id.in_(character_ids)
                    )
                ).all()

            rows = server.vault.db.run_task(
                fetch_character_names, priority=DBPriority.IMMEDIATE
            )
            character_name_map = {char_id: name for char_id, name in rows or []}
        results = {}
        for pic in pics:
            try:
                face_entries = []
                mapped_any = False
                raw_face_bboxes = []
                for face in getattr(pic, "faces", []):
                    bbox = None
                    try:
                        bbox = face.bbox if hasattr(face, "bbox") else None
                        if bbox and isinstance(bbox, str):
                            bbox = ast.literal_eval(bbox)
                    except Exception:
                        bbox = None
                    if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                        raw_face_bboxes.append(list(bbox))
                        face_entries.append(
                            {
                                "id": face.id,
                                "bbox": list(bbox),
                                "character_id": face.character_id,
                                "character_name": character_name_map.get(
                                    face.character_id
                                ),
                                "frame_index": getattr(face, "frame_index", None),
                            }
                        )
                face_data = []
                for entry in face_entries:
                    mapped_bbox, mapped = map_bbox_to_thumbnail(entry.get("bbox"), pic)
                    mapped_any = mapped_any or mapped
                    face_data.append({**entry, "bbox": mapped_bbox})

                imported_at = getattr(pic, "imported_at", None)
                v = int(imported_at.timestamp()) if imported_at is not None else 0
                thumbnail_url = f"/pictures/thumbnails/{pic.id}.webp?v={v}"
                results[pic.id] = {
                    "thumbnail": thumbnail_url,
                    "faces": face_data,
                    "thumbnail_width": 256 if mapped_any else None,
                    "thumbnail_height": 256 if mapped_any else None,
                    "penalised_tags": list(
                        dict.fromkeys(penalised_tag_map.get(pic.id, []))
                    ),
                }
            except Exception as exc:
                logger.error(
                    f"Picture not found or error for id={pic.id} (thumbnail request): {exc}"
                )
                results[pic.id] = {
                    "thumbnail": None,
                    "faces": [],
                    "penalised_tags": [],
                }
        response = JSONResponse(results)
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

