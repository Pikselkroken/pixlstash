import ast
import asyncio
import os
import re
from collections import defaultdict, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PIL import Image
from fastapi import (
    Body,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import (
    func,
)
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Picture,
    Tag,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.scoring import (
    get_smart_score_penalised_tags_from_request,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils

from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids


logger = get_logger(__name__)


# Thumbnails are served under content-addressed URLs (the ?v=WxH version changes
# whenever the bitmap is regenerated), so browsers may cache them briefly but must
# revalidate afterwards - that keeps reference-folder source swaps (same URL, new
# bytes) from serving stale forever, which the previous header-less heuristic
# caching allowed. `private` stops shared proxies caching access-controlled
# thumbnails. FileResponse still emits ETag/Last-Modified for cheap 304s.
_THUMBNAIL_CACHE_HEADERS = {"Cache-Control": "private, max-age=3600, must-revalidate"}


# Dedicated, bounded pool for on-the-fly thumbnail generation (full-resolution
# decode + resize + encode). Kept separate from asyncio's default executor so a
# large import job (which runs on the default executor via run_in_executor) can
# never starve on-demand thumbnail builds, and so the rare heavy build can't
# monopolise every default-executor thread. Small on purpose: this work is CPU
# bound and competes with the background TaskRunner workers.
_THUMBNAIL_EXECUTOR_WORKERS = min(4, (os.cpu_count() or 2))
_thumbnail_executor = ThreadPoolExecutor(
    max_workers=_THUMBNAIL_EXECUTOR_WORKERS,
    thread_name_prefix="thumb-gen",
)


def register_routes(router, server):
    thumbnail_generation_locks: dict[int, asyncio.Lock] = {}
    thumbnail_memory_cache: OrderedDict[int, bytes] = OrderedDict()
    thumbnail_memory_cache_max = 128

    def clear_thumbnail_runtime_cache() -> None:
        thumbnail_memory_cache.clear()
        thumbnail_generation_locks.clear()

    # The cache is route-local, so expose its one lifecycle operation to the
    # server's library switch coordinator without exposing the storage itself.
    server._clear_thumbnail_runtime_cache = clear_thumbnail_runtime_cache

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

    def discard_stale_thumbnail(picture_id: int, file_path: str, thumb_path: str):
        """Drop a cached thumbnail whose source file has been rewritten since.

        Checked for EVERY picture, not only reference-folder ones. A reference
        folder's source can be swapped under a stable container path, and an
        in-place rotate rewrites a library-managed original the same way: the
        pixels are copied through and only the EXIF orientation tag changes, so
        the stored bitmap is now sideways relative to the file. ``apply_orientation``
        NULLs ``thumbnail_width``/``height`` to re-queue the regeneration, but
        that runs on a background sweep - until it lands this route was handing
        back the pre-rotate bitmap, which is why a rotate used to paint the wrong
        way round and only correct itself on a second refresh.

        Both caches go, not just the file: the in-memory copy is keyed on the
        picture id alone and would otherwise be served in its place.

        Returns:
            Whether the thumbnail was stale (and has now been discarded).
        """
        source_path = ImageUtils.resolve_picture_path(
            server.vault.image_root, file_path
        )
        if not source_path or not os.path.exists(source_path):
            return False
        try:
            if os.path.getmtime(source_path) <= os.path.getmtime(thumb_path):
                return False
        except OSError as exc:
            logger.debug(
                "Could not compare thumbnail mtime for id=%s (%s); serving the "
                "cached bitmap",
                picture_id,
                exc,
            )
            return False
        logger.debug(
            "Thumbnail stale (source newer): id=%s source=%s", picture_id, source_path
        )
        thumbnail_memory_cache.pop(picture_id, None)
        try:
            os.remove(thumb_path)
        except OSError as exc:
            logger.warning("Failed to remove stale thumbnail %s: %s", thumb_path, exc)
        return True

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
        response_class=FileResponse,
        responses={200: {"content": {"image/webp": {}}}},
    )
    async def get_thumbnail(request: Request, id: int):
        started_at = datetime.now()
        vault = server.vault
        generation = getattr(server, "library_generation", 0)

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

        pic = vault.db.run_immediate_read_task(fetch_picture, id)
        if not pic or not getattr(pic, "file_path", None):
            raise HTTPException(status_code=404, detail="Picture not found")

        # Where the bitmap belongs, kept for the in-lock re-check below: a
        # concurrent request that generated it while this one waited writes
        # exactly here. `find_thumbnail` is the read that also brings a
        # pre-#1164 bitmap home, and may answer a legacy path if that move
        # failed, so the two are deliberately separate variables.
        thumb_path = ImageUtils.get_thumbnail_path(vault.image_root, pic.file_path)
        existing = ImageUtils.find_thumbnail(vault.image_root, pic.file_path)
        if existing:
            if not discard_stale_thumbnail(id, pic.file_path, existing):
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.debug(
                    "Thumbnail GET cache-hit: id=%s path=%s elapsed_ms=%.1f",
                    id,
                    existing,
                    elapsed_ms,
                )
                return FileResponse(
                    existing,
                    media_type="image/webp",
                    headers=_THUMBNAIL_CACHE_HEADERS,
                )

        cached_bytes = get_cached_thumbnail_bytes(id)
        if cached_bytes:
            elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
            logger.debug(
                "Thumbnail GET memory-hit: id=%s elapsed_ms=%.1f",
                id,
                elapsed_ms,
            )
            return Response(
                content=cached_bytes,
                media_type="image/webp",
                headers=_THUMBNAIL_CACHE_HEADERS,
            )

        lock = get_thumbnail_lock(id)
        async with lock:
            if thumb_path and os.path.exists(thumb_path):
                # Re-check staleness inside the lock: another request may have
                # regenerated it while this one waited.
                if not discard_stale_thumbnail(id, pic.file_path, thumb_path):
                    elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                    logger.debug(
                        "Thumbnail GET cache-hit-after-wait: id=%s path=%s elapsed_ms=%.1f",
                        id,
                        thumb_path,
                        elapsed_ms,
                    )
                    return FileResponse(
                        thumb_path,
                        media_type="image/webp",
                        headers=_THUMBNAIL_CACHE_HEADERS,
                    )

            cached_bytes = get_cached_thumbnail_bytes(id)
            if cached_bytes:
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.debug(
                    "Thumbnail GET memory-hit-after-wait: id=%s elapsed_ms=%.1f",
                    id,
                    elapsed_ms,
                )
                return Response(
                    content=cached_bytes,
                    media_type="image/webp",
                    headers=_THUMBNAIL_CACHE_HEADERS,
                )

            def build_thumbnail_blocking() -> tuple[
                str, str | None, bytes | None, str | None
            ]:
                resolved = ImageUtils.resolve_picture_path(
                    vault.image_root, pic.file_path
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
                    vault.image_root, pic.file_path, thumbnail_bytes
                )
                if saved_thumb_path and os.path.exists(saved_thumb_path):
                    return "saved", resolved, None, saved_thumb_path

                return "memory-only", resolved, thumbnail_bytes, None

            loop = asyncio.get_running_loop()
            (
                status,
                resolved_path,
                thumbnail_bytes,
                saved_thumb,
            ) = await loop.run_in_executor(
                _thumbnail_executor, build_thumbnail_blocking
            )
            if generation != getattr(server, "library_generation", 0):
                raise HTTPException(
                    status_code=503,
                    detail="The active library changed while the thumbnail was generated.",
                )

            if status == "saved" and saved_thumb:
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.debug(
                    "Thumbnail GET generated: id=%s source=%s elapsed_ms=%.1f",
                    id,
                    resolved_path,
                    elapsed_ms,
                )
                return FileResponse(
                    saved_thumb,
                    media_type="image/webp",
                    headers=_THUMBNAIL_CACHE_HEADERS,
                )

            if status == "memory-only" and thumbnail_bytes:
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.warning(
                    "Thumbnail GET generated-memory-only: id=%s source=%s elapsed_ms=%.1f",
                    id,
                    resolved_path,
                    elapsed_ms,
                )
                return Response(
                    content=thumbnail_bytes,
                    media_type="image/webp",
                    headers=_THUMBNAIL_CACHE_HEADERS,
                )

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
        response_model=dict,
    )
    def get_thumbnails(request: Request, payload: dict = Body(...)):
        ids = payload.get("ids", [])
        if not isinstance(ids, list):
            raise HTTPException(status_code=400, detail="'ids' must be a list")

        # Scope guard (BOLA): a READ-scoped share token may only resolve
        # thumbnails for pictures within its granted resource.  None == owner /
        # unscoped == no filter.  Filtering the raw id list here flows through to
        # every downstream query (penalised tags, Picture.find).
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            filtered_ids = []
            for raw_id in ids:
                try:
                    if int(raw_id) in scope_allowed:
                        filtered_ids.append(raw_id)
                except (TypeError, ValueError):
                    continue
            ids = filtered_ids

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
            # Map a picture-space (source pixel) bbox into AR-BITMAP pixel space
            # (0..thumbnail_width × 0..thumbnail_height, origin top-left). The
            # bitmap is a uniform resize of the WHOLE frame, so the scale is
            # ``thumbnail_width / source_width``. The source dimensions are the
            # picture's, but ``picture.width``/``height`` are stored un-rotated:
            # for an EXIF-rotated (90°/270°) image the bitmap swaps them, so pick
            # whichever orientation shares the bitmap's aspect ratio.
            if not bbox or len(bbox) != 4:
                return bbox, False
            out_w = getattr(picture, "thumbnail_width", None)
            out_h = getattr(picture, "thumbnail_height", None)
            pic_w = getattr(picture, "width", None)
            pic_h = getattr(picture, "height", None)
            if not out_w or not out_h or not pic_w or not pic_h:
                return bbox, False
            try:
                target_ar = out_w / float(out_h)
                if abs((pic_w / float(pic_h)) - target_ar) <= abs(
                    (pic_h / float(pic_w)) - target_ar
                ):
                    src_w, src_h = float(pic_w), float(pic_h)
                else:
                    # EXIF 90°/270°: the stored dims are swapped vs the bitmap.
                    src_w, src_h = float(pic_h), float(pic_w)
                sx = out_w / src_w
                sy = out_h / src_h
                x1, y1, x2, y2 = bbox
                x1 = max(0.0, min(float(out_w), x1 * sx))
                y1 = max(0.0, min(float(out_h), y1 * sy))
                x2 = max(0.0, min(float(out_w), x2 * sx))
                y2 = max(0.0, min(float(out_h), y2 * sy))
                return (
                    [
                        int(round(x1)),
                        int(round(y1)),
                        int(round(x2)),
                        int(round(y2)),
                    ],
                    True,
                )
            except Exception as exc:
                logger.debug(
                    "Could not map bbox to thumbnail for picture %s (%s); "
                    "returning unscaled bbox.",
                    getattr(picture, "id", None),
                    exc,
                )
                return bbox, False

        pics = server.vault.db.run_task(
            lambda session: Picture.find(
                session,
                id=ids,
                select_fields=[
                    "id",
                    "file_path",
                    "faces",
                    "detections",
                    "width",
                    "height",
                    "thumbnail_width",
                    "thumbnail_height",
                    "square_crop_x",
                    "square_crop_y",
                    "square_crop_side",
                    "imported_at",
                    # Feeds `thumbnail_cache_version` below. `select_fields` is an
                    # allowlist, so anything absent is DEFERRED - and these rows
                    # outlive their session, which turns a deferred read into
                    # `DetachedInstanceError` for every picture in the batch.
                    # `getattr(pic, ..., None)` does not save you: SQLAlchemy
                    # raises that, not `AttributeError`, so the default never
                    # applies and the whole thumbnail request 500s.
                    "orientation",
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
                    mapped_bbox, _mapped = map_bbox_to_thumbnail(entry.get("bbox"), pic)
                    face_data.append({**entry, "bbox": mapped_bbox})

                # Object detections, mapped into AR-bitmap space exactly like
                # faces so the grid overlay renders them identically.
                detection_entries = []
                for det in getattr(pic, "detections", []):
                    bbox = getattr(det, "bbox", None)
                    if bbox and isinstance(bbox, str):
                        try:
                            bbox = ast.literal_eval(bbox)
                        except Exception:
                            bbox = None
                    if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                        detection_entries.append(
                            {
                                "id": det.id,
                                "bbox": list(bbox),
                                "label": det.label,
                                "score": det.score,
                                "frame_index": getattr(det, "frame_index", None),
                            }
                        )
                detection_data = []
                for entry in detection_entries:
                    mapped_bbox, _mapped = map_bbox_to_thumbnail(entry.get("bbox"), pic)
                    detection_data.append({**entry, "bbox": mapped_bbox})

                # Cache-buster keyed on the thumbnail bitmap itself. Regeneration
                # (the square-crop -> AR-bitmap rebuild on upgrade, or any later
                # rebuild) repopulates these dimensions, so the URL changes and the
                # browser refetches instead of serving the stale cached image. The
                # old key was imported_at, which never changed on regen -> the
                # browser kept painting the pre-upgrade square bitmap into the
                # justified layout's AR cell -> squashed thumbnails.
                v = ImageUtils.thumbnail_cache_version(
                    getattr(pic, "thumbnail_width", None),
                    getattr(pic, "thumbnail_height", None),
                    getattr(pic, "orientation", None),
                )
                thumbnail_url = f"/pictures/thumbnails/{pic.id}.webp?v={v}"
                # Whole-frame AR-bitmap dimensions and the face-weighted square-crop
                # rectangle (bitmap pixel space). All are NULL until the picture is
                # processed; the frontend falls back to object-fit until then.
                results[pic.id] = {
                    "thumbnail": thumbnail_url,
                    "faces": face_data,
                    "detections": detection_data,
                    "thumbnail_width": getattr(pic, "thumbnail_width", None),
                    "thumbnail_height": getattr(pic, "thumbnail_height", None),
                    "square_crop_x": getattr(pic, "square_crop_x", None),
                    "square_crop_y": getattr(pic, "square_crop_y", None),
                    "square_crop_side": getattr(pic, "square_crop_side", None),
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
                    "detections": [],
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
