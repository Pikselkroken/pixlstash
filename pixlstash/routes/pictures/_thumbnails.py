import ast
import asyncio
import json
import os
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

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
    VideoCopy,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.scoring import (
    get_smart_score_penalised_tags_from_request,
)
from pixlstash.utils.image_processing import ffmpeg_utils
from pixlstash.utils.image_processing.ffmpeg_utils import ffmpeg_executable
from pixlstash.utils.image_processing.image_utils import (
    STORYBOARD_FRAMES,
    VIDEO_COPY_DIR,
    VIDEO_COPY_NAME,
    ImageUtils,
)
from pixlstash.utils.image_processing.video_utils import VideoUtils

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


#: A playable copy estimated to take longer than this is offered, not started.
COPY_OFFER_SECONDS = 10.0
# ponytail: a guess for a machine that has made no copy yet (pixel-frames per
# second, below the 2-thread rate measured on 1080p ProRes); the first copy's
# own rate replaces it, so it is only ever wrong once.
DEFAULT_COPY_RATE = 50_000_000

# ponytail: one transcode at a time; raise max_workers if users queue several.
# ponytail: copies are kept for the picture's lifetime; add an LRU cap if
# libraries hold many large ProRes clips.
_copy_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-copy")


@dataclass
class _CopyJob:
    """A playable copy queued or being made."""

    info: dict
    estimate: Optional[float]
    future: Optional[Future] = None
    #: ``time.monotonic()`` once ffmpeg runs; ``None`` while queued.
    started: Optional[float] = None

    def remaining(self) -> Optional[float]:
        if self.estimate is None or self.started is None:
            return self.estimate
        # ponytail: estimate minus elapsed; read ffmpeg's -progress if long
        # copies sit at 0 s left for a noticeable while.
        return max(0.0, self.estimate - (time.monotonic() - self.started))


def _copy_answer(
    state: str,
    info: Optional[dict] = None,
    estimate: Optional[float] = None,
    remaining: Optional[float] = None,
    detail: Optional[str] = None,
) -> dict:
    """The status object every copy route answers with (#2807)."""
    info = info or {}
    return {
        "state": state,
        "format": info.get("format"),
        "estimate_seconds": None if estimate is None else round(estimate, 1),
        "remaining_seconds": None if remaining is None else round(remaining, 1),
        "duration_seconds": info.get("duration"),
        "detail": detail,
    }


def register_routes(router, server):
    thumbnail_generation_locks: dict[int, asyncio.Lock] = {}
    storyboard_generation_locks: dict[int, asyncio.Lock] = {}
    waveform_generation_locks: dict[int, asyncio.Lock] = {}
    # (library root, picture id) -> the copy queued or being made for it.
    copy_jobs: dict[tuple[str, int], _CopyJob] = {}
    # Makes "is one running? if not, start one" one step for concurrent POSTs.
    copy_jobs_lock = threading.Lock()

    def clear_thumbnail_runtime_cache() -> None:
        # Keyed by picture id, and ids are reused across libraries.
        thumbnail_generation_locks.clear()
        storyboard_generation_locks.clear()
        waveform_generation_locks.clear()
        # The old library's copy jobs stop, and are never reported as this
        # library's: queued ones are cancelled before they start, the running
        # ffmpeg is terminated, and the map forgets them all.
        with copy_jobs_lock:
            for job in copy_jobs.values():
                job.future.cancel()
            copy_jobs.clear()
        ffmpeg_utils.stop_running_copy()

    # The locks are route-local, so expose their one lifecycle operation to the
    # server's library switch coordinator without exposing the storage itself.
    server._clear_thumbnail_runtime_cache = clear_thumbnail_runtime_cache

    def picture_file_path(picture_id: int) -> str:
        """The picture's stored path, or 404."""

        def fetch(session: Session):
            pics = Picture.find(
                session,
                id=picture_id,
                select_fields=["id", "file_path"],
                include_deleted=True,
                include_unimported=True,
            )
            return pics[0].file_path if pics else None

        file_path = server.vault.db.run_immediate_read_task(fetch)
        if not file_path:
            raise HTTPException(status_code=404, detail="Picture not found")
        return file_path

    def video_file_path(picture_id: int) -> str:
        """The picture's stored path, or 404 when it is not a video."""
        file_path = picture_file_path(picture_id)
        if not VideoUtils.is_video_file(file_path):
            raise HTTPException(status_code=404, detail="Picture is not a video")
        return file_path

    def get_thumbnail_lock(picture_id: int) -> asyncio.Lock:
        lock = thumbnail_generation_locks.get(picture_id)
        if lock is None:
            lock = asyncio.Lock()
            thumbnail_generation_locks[picture_id] = lock
        return lock

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
        try:
            os.remove(thumb_path)
        except OSError as exc:
            logger.warning("Failed to remove stale thumbnail %s: %s", thumb_path, exc)
        return True

    @router.get(
        "/pictures/thumbnails/{id}.webp",
        summary="Get picture thumbnail image",
        description="Returns a WebP thumbnail for a picture id, generating and caching it on demand when needed.",
        response_class=FileResponse,
        responses={200: {"content": {"image/webp": {}}}},
    )
    async def get_thumbnail(request: Request, id: int):
        started_at = datetime.now(timezone.utc)
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
                elapsed_ms = (
                    datetime.now(timezone.utc) - started_at
                ).total_seconds() * 1000.0
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

        lock = get_thumbnail_lock(id)
        async with lock:
            if thumb_path and os.path.exists(thumb_path):
                # Re-check staleness inside the lock: another request may have
                # regenerated it while this one waited.
                if not discard_stale_thumbnail(id, pic.file_path, thumb_path):
                    elapsed_ms = (
                        datetime.now(timezone.utc) - started_at
                    ).total_seconds() * 1000.0
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
                elapsed_ms = (
                    datetime.now(timezone.utc) - started_at
                ).total_seconds() * 1000.0
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
                elapsed_ms = (
                    datetime.now(timezone.utc) - started_at
                ).total_seconds() * 1000.0
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

        elapsed_ms = (datetime.now(timezone.utc) - started_at).total_seconds() * 1000.0
        logger.warning(
            "Thumbnail GET failed: id=%s elapsed_ms=%.1f",
            id,
            elapsed_ms,
        )

        raise HTTPException(status_code=404, detail="Thumbnail not found")

    @router.get(
        "/pictures/storyboards/{id}.webp",
        summary="Get a video's storyboard",
        description=(
            f"Returns {STORYBOARD_FRAMES} evenly spaced frames of a video, first "
            "to last, laid left to right in one WebP. Generated and cached on "
            "demand; 404 for a picture that is not a video or whose frames "
            "cannot all be read."
        ),
        response_class=FileResponse,
        responses={200: {"content": {"image/webp": {}}}},
    )
    async def get_storyboard(request: Request, id: int):
        vault = server.vault
        generation = getattr(server, "library_generation", 0)
        file_path = video_file_path(id)
        cached = ImageUtils.derived_media_path(
            vault.image_root, file_path, "storyboard"
        )

        def fresh_cache() -> bool:
            return bool(
                cached
                and os.path.isfile(cached)
                and not discard_stale_thumbnail(id, file_path, cached)
            )

        if fresh_cache():
            return FileResponse(
                cached, media_type="image/webp", headers=_THUMBNAIL_CACHE_HEADERS
            )

        lock = storyboard_generation_locks.setdefault(id, asyncio.Lock())
        async with lock:
            # Another request may have made it while this one waited.
            if fresh_cache():
                return FileResponse(
                    cached, media_type="image/webp", headers=_THUMBNAIL_CACHE_HEADERS
                )

            def build_storyboard_blocking() -> bytes | None:
                source = ImageUtils.resolve_picture_path(vault.image_root, file_path)
                if not source or not os.path.exists(source):
                    logger.warning("Storyboard: source file missing: %s", source)
                    return None
                frames = VideoUtils.extract_storyboard_frames(source, STORYBOARD_FRAMES)
                data = ImageUtils.render_storyboard(frames)
                if data:
                    ImageUtils.write_derived_media_bytes(
                        vault.image_root, file_path, "storyboard", data
                    )
                return data

            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(
                _thumbnail_executor, build_storyboard_blocking
            )
            if generation != getattr(server, "library_generation", 0):
                raise HTTPException(
                    status_code=503,
                    detail="The active library changed while the storyboard was made.",
                )
        if not data:
            # Already logged why: an unreadable frame or a missing file.
            raise HTTPException(status_code=404, detail="Storyboard not available")
        return Response(
            content=data, media_type="image/webp", headers=_THUMBNAIL_CACHE_HEADERS
        )

    @router.get(
        "/pictures/waveforms/{id}.json",
        summary="Get a video's audio waveform",
        description=(
            "The peaks of a video's first audio track: "
            '`{"duration": seconds, "peaks": [512 values in 0..1]}`, or both '
            "null for a clip with no sound. Read from the original file, so a "
            "clip the browser cannot play still has one. Cached on demand; 404 "
            "for a picture that is not a video, 503 with no ffmpeg."
        ),
        response_model=dict,
    )
    async def get_waveform(id: int):
        if not ffmpeg_executable():
            raise HTTPException(
                status_code=503, detail="No ffmpeg on this install, so no waveform."
            )
        vault = server.vault
        generation = getattr(server, "library_generation", 0)
        file_path = video_file_path(id)
        cached = ImageUtils.derived_media_path(vault.image_root, file_path, "waveform")

        def read_cached() -> Optional[dict]:
            if not cached or not os.path.isfile(cached):
                return None
            if discard_stale_thumbnail(id, file_path, cached):
                return None
            try:
                with open(cached, encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, ValueError) as exc:
                logger.warning(
                    "Waveform cache %s unreadable (%s); reading the audio again",
                    cached,
                    exc,
                )
                return None

        body = read_cached()
        if body is not None:
            return body
        lock = waveform_generation_locks.setdefault(id, asyncio.Lock())
        async with lock:
            body = read_cached()
            if body is not None:
                return body

            def build_waveform_blocking() -> Optional[dict]:
                source = ImageUtils.resolve_picture_path(vault.image_root, file_path)
                if not source or not os.path.exists(source):
                    logger.warning("Waveform: source file missing: %s", source)
                    return None
                try:
                    peaks = ffmpeg_utils.audio_peaks(source)
                except Exception as exc:
                    # Cached as no lane, like silence, so a clip ffmpeg cannot
                    # read is not decoded again on every open. A changed
                    # original is newer than the cache and is read again.
                    logger.warning(
                        "Waveform: could not read the audio of %s (%s: %s); "
                        "caching it as no waveform",
                        source,
                        type(exc).__name__,
                        exc,
                    )
                    peaks = None
                # A silent clip's answer is cached too, so it is probed once.
                built = {
                    "duration": peaks[0] if peaks else None,
                    "peaks": peaks[1] if peaks else None,
                }
                ImageUtils.write_derived_media_bytes(
                    vault.image_root,
                    file_path,
                    "waveform",
                    json.dumps(built).encode("utf-8"),
                )
                return built

            loop = asyncio.get_running_loop()
            body = await loop.run_in_executor(
                _thumbnail_executor, build_waveform_blocking
            )
            if generation != getattr(server, "library_generation", 0):
                raise HTTPException(
                    status_code=503,
                    detail="The active library changed while the waveform was read.",
                )
        if body is None:
            raise HTTPException(status_code=404, detail="Waveform not available")
        return body

    # -- Playable copies of videos the browser refuses (#2807) ---------------

    def fresh_copy(vault, picture_id: int, source: Optional[str]) -> Optional[str]:
        """The copy's path if it is still a copy of *source*, else ``None``.

        A row whose file is gone, or whose original changed size or modified
        time since, is removed with its file: it counts as no copy.
        """
        row = vault.db.run_immediate_read_task(
            lambda session: session.get(VideoCopy, picture_id)
        )
        if row is None:
            return None
        path = ImageUtils.video_copy_path(vault.image_root, picture_id)
        try:
            stat = os.stat(source) if source else None
        except OSError:
            stat = None
        if (
            stat is not None
            and os.path.isfile(path)
            and (stat.st_size, stat.st_mtime) == (row.source_bytes, row.source_mtime)
        ):
            return path
        logger.info("Video copy of picture %s is stale; removing it", picture_id)
        delete_copy_rows(vault, [picture_id])
        ImageUtils.remove_video_copy(vault.image_root, picture_id)
        return None

    def delete_copy_rows(vault, picture_ids: Optional[list[int]]) -> None:
        """Delete these pictures' ``video_copy`` rows; every row for ``None``."""

        def delete_rows(session: Session):
            query = select(VideoCopy)
            if picture_ids is not None:
                query = query.where(VideoCopy.picture_id.in_(picture_ids))
            for row in session.exec(query).all():
                session.delete(row)
            session.commit()

        vault.db.run_task(delete_rows)

    def copy_rate(vault) -> float:
        """Pixel-frames per second this machine made its latest copy at."""

        def latest(session: Session):
            # A copy of a clip whose size could not be read (0) says nothing
            # about speed, and would make the rate 0.
            return session.exec(
                select(VideoCopy)
                .where(VideoCopy.pixel_frames > 0)
                .order_by(VideoCopy.made_at.desc())
                .limit(1)
            ).first()

        row = vault.db.run_immediate_read_task(latest)
        if row is None:
            return DEFAULT_COPY_RATE
        return row.pixel_frames / max(row.seconds, 0.01)

    def make_copy(vault, generation, picture_id, source, job: _CopyJob) -> None:
        """Run on ``_copy_executor``: make the copy, then record its row."""
        if generation != getattr(server, "library_generation", 0):
            raise RuntimeError("The library changed before the copy started.")
        dest = ImageUtils.video_copy_path(vault.image_root, picture_id)
        try:
            # Read before the copy, so a change made during it reads as stale.
            stat = os.stat(source)
            began = time.monotonic()

            def on_start():
                job.started = time.monotonic()

            ffmpeg_utils.make_playable_copy(source, dest, on_start=on_start)
            row = VideoCopy(
                picture_id=picture_id,
                bytes=os.path.getsize(dest),
                source_bytes=stat.st_size,
                source_mtime=stat.st_mtime,
                pixel_frames=job.info.get("pixel_frames") or 0,
                seconds=time.monotonic() - began,
                made_at=datetime.now(timezone.utc),
            )

            def record(session: Session):
                session.merge(row)
                session.commit()

            vault.db.run_task(record)
        except Exception as exc:
            logger.warning(
                "Playable copy of picture %s (%s -> %s) failed: %s: %s",
                picture_id,
                source,
                dest,
                type(exc).__name__,
                exc,
            )
            raise
        logger.info(
            "Made a playable copy of picture %s in %.1f s (%s)",
            picture_id,
            row.seconds,
            dest,
        )

    def job_answer(key: tuple[str, int]) -> Optional[dict]:
        """What the copy job for *key* says, or ``None`` when it says nothing.

        A finished job is forgotten here, under the lock, so a failure is
        reported exactly once. Only the map is touched under the lock.
        """
        with copy_jobs_lock:
            job = copy_jobs.get(key)
            if job is None:
                return None
            if not job.future.done():
                return _copy_answer(
                    "preparing", job.info, job.estimate, job.remaining()
                )
            del copy_jobs[key]
        if job.future.exception() is not None:
            return _copy_answer(
                "failed",
                job.info,
                detail="PixlStash could not make a playable copy of this video.",
            )
        return None

    def copy_status(picture_id: int) -> tuple[dict, Optional[str], dict]:
        """The copy status, plus the original's path and OpenCV facts for "none"."""
        vault = server.vault
        file_path = picture_file_path(picture_id)
        if not VideoUtils.is_video_file(file_path):
            return _copy_answer("unavailable"), None, {}
        if not ffmpeg_executable():
            detail = "PixlStash found no ffmpeg on this computer to make the copy with."
            return _copy_answer("unavailable", detail=detail), None, {}
        if not ffmpeg_utils.h264_encoder():
            detail = "This computer's ffmpeg cannot write H.264 video, so no copy can be made."
            return _copy_answer("unavailable", detail=detail), None, {}
        answer = job_answer((vault.image_root, picture_id))
        if answer is not None:
            return answer, None, {}
        source = ImageUtils.resolve_picture_path(vault.image_root, file_path)
        if fresh_copy(vault, picture_id, source):
            return _copy_answer("ready"), None, {}
        if not source or not os.path.isfile(source):
            return (
                _copy_answer("unavailable", detail="The original file is missing."),
                None,
                {},
            )
        info = VideoUtils.describe(source) or {}
        pixel_frames = info.get("pixel_frames")
        estimate = pixel_frames / copy_rate(vault) if pixel_frames else None
        return _copy_answer("none", info, estimate), source, info

    @router.get(
        "/pictures/video-copies/{id}/status",
        summary="Get the playable copy status of a video",
        description=(
            "Whether a browser-playable MP4 copy of this video exists, is being "
            "made, or could be made, with an estimate. Never starts one."
        ),
        response_model=dict,
    )
    def get_video_copy_status(id: int):
        return copy_status(id)[0]

    @router.post(
        "/pictures/video-copies/{id}",
        summary="Make a playable copy of a video",
        description=(
            "Starts an H.264/AAC MP4 copy of a video the browser refuses. With "
            "`confirm: false` a copy estimated over "
            f"{COPY_OFFER_SECONDS:g} s is only offered (`offered`); "
            "`confirm: true` starts it regardless. One copy runs at a time; a "
            "copy already made or being made is answered, not restarted."
        ),
        response_model=dict,
    )
    def start_video_copy(id: int, confirm: bool = Body(False, embed=True)):
        vault = server.vault
        generation = getattr(server, "library_generation", 0)
        status, source, info = copy_status(id)
        if status["state"] != "none":
            return status
        estimate = status["estimate_seconds"]
        if not confirm and estimate is not None and estimate > COPY_OFFER_SECONDS:
            return {**status, "state": "offered"}
        key = (vault.image_root, id)
        with copy_jobs_lock:
            # Another request may have started one since the status was read.
            running = copy_jobs.get(key)
            if running is not None and not running.future.done():
                job = running
            else:
                job = _CopyJob(info=info, estimate=estimate)
                job.future = _copy_executor.submit(
                    make_copy, vault, generation, id, source, job
                )
                copy_jobs[key] = job
        return _copy_answer("preparing", job.info, job.estimate, job.remaining())

    @router.get(
        "/pictures/video-copies/{id}.mp4",
        summary="Get the playable copy of a video",
        description="The MP4 copy made by `POST /pictures/video-copies/{id}`; 404 when there is none.",
        response_class=FileResponse,
        responses={200: {"content": {"video/mp4": {}}}},
    )
    def get_video_copy(id: int):
        vault = server.vault
        source = ImageUtils.resolve_picture_path(vault.image_root, video_file_path(id))
        with copy_jobs_lock:
            job = copy_jobs.get((vault.image_root, id))
            preparing = job is not None and not job.future.done()
        path = None if preparing else fresh_copy(vault, id, source)
        if not path:
            raise HTTPException(status_code=404, detail="No playable copy")
        return FileResponse(
            path, media_type="video/mp4", headers=_THUMBNAIL_CACHE_HEADERS
        )

    def copy_files(vault) -> list[str]:
        """Every finished copy file of the open library (``VIDEO_COPY_NAME``)."""
        folder = os.path.join(vault.image_root, VIDEO_COPY_DIR)
        if not os.path.isdir(folder):
            return []
        return [
            os.path.join(folder, name)
            for name in os.listdir(folder)
            if VIDEO_COPY_NAME.match(name)
        ]

    @router.get(
        "/library/video-copies",
        summary="Count the playable video copies",
        description="How many playable video copies the open library keeps, and their size.",
        response_model=dict,
    )
    def get_video_copies():
        files = copy_files(server.vault)
        return {
            "count": len(files),
            "bytes": sum(os.path.getsize(path) for path in files),
        }

    @router.delete(
        "/library/video-copies",
        summary="Delete every playable video copy",
        description=(
            "Removes every finished playable copy of the open library, file "
            "and row. A deleted copy is made again when its clip is next opened."
        ),
        response_model=dict,
    )
    def delete_video_copies():
        vault = server.vault
        deleted = 0
        freed = 0
        delete_copy_rows(vault, None)
        for path in copy_files(vault):
            try:
                size = os.path.getsize(path)
                os.remove(path)
            except OSError as exc:
                logger.warning("Could not delete video copy %s: %s", path, exc)
                continue
            deleted += 1
            freed += size
        logger.info("Deleted %s video copies (%s bytes)", deleted, freed)
        return {"deleted": deleted, "bytes": freed}

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
                    file_path=pic.file_path,
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
