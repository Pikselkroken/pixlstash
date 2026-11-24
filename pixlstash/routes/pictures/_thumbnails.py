import ast
import asyncio
import json
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple, Optional

from PIL import Image
from fastapi import (
    Body,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import (
    delete,
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
from pixlstash.utils.image_processing.image_utils import (
    STORYBOARD_FRAMES,
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


# One playable copy at a time: each is a CPU-heavy H.264 encode and a second
# would only share the same cores. Its own pool, so a copy never queues behind
# thumbnail builds.
# ponytail: one transcode at a time; raise max_workers if users queue several
_copy_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-copy")
#: A copy estimated to take at least this long is offered, not started.
_COPY_START_THRESHOLD_S = 10.0
# ponytail: a guessed rate until one copy has been built here; the newest real build replaces it
_DEFAULT_COPY_RATE = 100e6
#: Codec tags that browsers refuse and the UI calls ProRes.
_PRORES_FOURCCS = frozenset({"apch", "apcn", "apcs", "apco", "ap4h", "ap4x"})


class _CopyRecord(NamedTuple):
    """The original's size and mtime as a copy's row recorded them."""

    source_bytes: int
    source_mtime: float


@dataclass
class _CopyJob:
    """A playable-copy build queued or running for one picture."""

    #: Seconds, or ``None`` when the header gives no frame count.
    estimate: Optional[float]
    future: Optional[Future] = None
    #: ``time.monotonic()`` when ffmpeg started; ``None`` while queued.
    started: Optional[float] = None


def _read_copy_facts(session: Session, picture_id: int):
    """Return ``(file_path, record, rate)`` for a picture, or ``None`` if absent.

    ``rate`` is pixel-frames per second of the newest copy built here, the
    divisor for the build-time estimate.
    """
    pics = Picture.find(
        session,
        id=picture_id,
        select_fields=["id", "file_path"],
        include_deleted=True,
        include_unimported=True,
    )
    if not pics or not pics[0].file_path:
        return None
    row = session.get(VideoCopy, picture_id)
    record = _CopyRecord(row.source_bytes, row.source_mtime) if row else None
    latest = session.exec(
        select(VideoCopy)
        .where(VideoCopy.pixel_frames > 0, VideoCopy.build_seconds > 0)
        .order_by(VideoCopy.created_at.desc())
        .limit(1)
    ).first()
    rate = _DEFAULT_COPY_RATE
    if latest is not None:
        rate = latest.pixel_frames / latest.build_seconds
    return pics[0].file_path, record, rate


def _copy_is_fresh(
    image_root: Optional[str],
    picture_id: int,
    file_path: str,
    record: Optional[_CopyRecord],
) -> bool:
    """A copy is fresh when its row, its file and the original all agree.

    The original's size and mtime must equal the row's. A row whose file is
    missing is no copy at all.
    """
    if record is None:
        return False
    dest = ImageUtils.video_copy_path(image_root, picture_id)
    source = ImageUtils.resolve_picture_path(image_root, file_path)
    if not dest or not source or not os.path.isfile(dest):
        return False
    try:
        stat = os.stat(source)
    except OSError as exc:
        logger.debug("Video copy freshness: cannot stat %s: %s", source, exc)
        return False
    return stat.st_size == record.source_bytes and stat.st_mtime == record.source_mtime


def _copy_format(file_path: str, fourcc: Optional[str]) -> Optional[str]:
    """The name the UI gives the original's format: "ProRes", else its extension."""
    if fourcc in _PRORES_FOURCCS:
        return "ProRes"
    return os.path.splitext(file_path)[1].lstrip(".").upper() or None


def _proxy_payload(
    state: str,
    estimate: Optional[float] = None,
    elapsed: Optional[float] = None,
    fmt: Optional[str] = None,
    duration: Optional[float] = None,
) -> dict:
    """The JSON the proxy status and start routes answer with."""
    return {
        "state": state,
        "estimate_seconds": estimate,
        "elapsed_seconds": elapsed,
        "format": fmt,
        "duration_seconds": duration,
    }


def _list_copy_files(image_root: Optional[str]) -> list[tuple[str, int]]:
    """Every finished copy file in this library's folder, as ``(path, bytes)``."""
    folder = ImageUtils.video_copy_folder(image_root)
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    for name in os.listdir(folder):
        if not name.endswith(".mp4") or name.startswith(".proxy-tmp-"):
            continue
        path = os.path.join(folder, name)
        try:
            found.append((path, os.path.getsize(path)))
        except OSError as exc:
            logger.warning("Video copy listing: cannot size %s: %s", path, exc)
    return found


def _build_video_copy(vault, picture_id: int, file_path: str, job: _CopyJob) -> None:
    """Make picture's playable copy and record it. Runs on ``_copy_executor``."""
    image_root = vault.image_root
    source = ImageUtils.resolve_picture_path(image_root, file_path)
    dest = ImageUtils.video_copy_path(image_root, picture_id)
    try:
        # Read the original BEFORE the encode: a rewrite during it then leaves
        # the row describing the old bytes, so the copy reads as stale.
        stat = os.stat(source)
        header = VideoUtils.video_header(source)
        if header is None:
            raise RuntimeError(f"cannot read the video header of {source}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        def mark_started(_proc) -> None:
            job.started = time.monotonic()

        ffmpeg_utils.make_playable_copy(source, dest, on_start=mark_started)
        build_seconds = time.monotonic() - job.started
        row = VideoCopy(
            picture_id=picture_id,
            copy_bytes=os.path.getsize(dest),
            source_bytes=stat.st_size,
            source_mtime=stat.st_mtime,
            pixel_frames=header[0],
            build_seconds=build_seconds,
        )

        def write_row(session: Session) -> None:
            session.merge(row)
            session.commit()

        vault.db.run_task(write_row)
        logger.info(
            "Playable copy made: picture id=%s build_seconds=%.1f copy_bytes=%s",
            picture_id,
            build_seconds,
            row.copy_bytes,
        )
    except Exception:
        logger.error(
            "Playable copy of picture id=%s (%s) failed",
            picture_id,
            source,
            exc_info=True,
        )
        raise


def register_routes(router, server):
    thumbnail_generation_locks: dict[int, asyncio.Lock] = {}
    storyboard_generation_locks: dict[int, asyncio.Lock] = {}
    waveform_generation_locks: dict[int, asyncio.Lock] = {}
    # Keyed by (library root, picture id): a library switch must not report
    # another library's job for a reused id.
    copy_jobs: dict[tuple[Optional[str], int], _CopyJob] = {}
    # The status and start routes run in worker threads (their header read
    # blocks), so the check-then-submit on copy_jobs holds this lock.
    copy_jobs_lock = threading.Lock()

    def drop_copy_jobs(image_root: Optional[str] = None) -> None:
        """Cancel the queued copy jobs (all, or one library's) and kill ffmpeg.

        Only one copy runs at a time, so killing every running ffmpeg stops
        exactly the job being dropped.
        """
        with copy_jobs_lock:
            for key in [k for k in copy_jobs if image_root in (None, k[0])]:
                copy_jobs.pop(key).future.cancel()
        ffmpeg_utils.terminate_running_processes()

    def clear_thumbnail_runtime_cache() -> None:
        # Keyed by picture id, and ids are reused across libraries.
        thumbnail_generation_locks.clear()
        storyboard_generation_locks.clear()
        waveform_generation_locks.clear()
        # The old library's copies are not wanted any more: drop the queued
        # ones and kill the running one, so the next library's copies do not
        # wait behind them on the single worker.
        drop_copy_jobs()

    # The locks are route-local, so expose their one lifecycle operation to the
    # server's library switch coordinator without exposing the storage itself.
    server._clear_thumbnail_runtime_cache = clear_thumbnail_runtime_cache

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

        def fetch_file_path(session: Session, picture_id: int):
            pics = Picture.find(
                session,
                id=picture_id,
                select_fields=["id", "file_path"],
                include_deleted=True,
                include_unimported=True,
            )
            return pics[0].file_path if pics else None

        file_path = vault.db.run_immediate_read_task(fetch_file_path, id)
        if not file_path:
            raise HTTPException(status_code=404, detail="Picture not found")
        if not VideoUtils.is_video_file(file_path):
            raise HTTPException(status_code=404, detail="Picture is not a video")

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

    def proxy_answer(picture_id: int, force_start: bool) -> dict:
        """The playable-copy state of a picture, starting a build when due.

        A build starts at once when its estimate is under the threshold, or
        when *force_start* (the POST route). Otherwise the answer is
        ``offered`` and nothing starts.
        """
        vault = server.vault
        image_root = vault.image_root
        facts = vault.db.run_immediate_read_task(_read_copy_facts, picture_id)
        if facts is None:
            raise HTTPException(status_code=404, detail="Picture not found")
        file_path, record, rate = facts
        source = ImageUtils.resolve_picture_path(image_root, file_path)
        header = None
        if VideoUtils.is_video_file(file_path) and source and os.path.isfile(source):
            header = VideoUtils.video_header(source)
        fmt = _copy_format(file_path, header[1] if header else None)
        duration = header[2] if header else None

        key = (image_root, picture_id)
        with copy_jobs_lock:
            job = copy_jobs.get(key)
            if job is not None and not job.future.done():
                elapsed = 0.0 if job.started is None else time.monotonic() - job.started
                return _proxy_payload("preparing", job.estimate, elapsed, fmt, duration)
            if job is not None:
                # Done: report a failure once, then drop it so a later call retries.
                del copy_jobs[key]
                if job.future.exception() is not None:
                    return _proxy_payload("failed", fmt=fmt, duration=duration)
            if _copy_is_fresh(image_root, picture_id, file_path, record):
                return _proxy_payload("ready", fmt=fmt, duration=duration)
            if header is None or ffmpeg_utils.ffmpeg_executable() is None:
                return _proxy_payload("unavailable", fmt=fmt, duration=duration)

            # Some containers report no frame count: the length is then unknown,
            # so the copy starts rather than offering a made-up estimate.
            estimate = header[0] / rate if header[0] > 0 else None
            if (
                not force_start
                and estimate is not None
                and estimate >= _COPY_START_THRESHOLD_S
            ):
                return _proxy_payload(
                    "offered", estimate=estimate, fmt=fmt, duration=duration
                )
            job = _CopyJob(estimate=estimate)
            job.future = _copy_executor.submit(
                _build_video_copy, vault, picture_id, file_path, job
            )
            copy_jobs[key] = job
            return _proxy_payload("preparing", estimate, 0.0, fmt, duration)

    @router.get(
        "/pictures/proxies/{id}/status",
        summary="Get a video's playable-copy state",
        description=(
            "Says whether this picture has a playable MP4 copy ready, being made, "
            "offered or failed. A copy whose estimate is under 10 s starts here; a "
            "longer one is only offered, and POST /pictures/proxies/{id}/start "
            "begins it. One copy is built at a time."
        ),
        response_model=dict,
    )
    async def get_proxy_status(request: Request, id: int):
        # A header read on a large clip, or one on a network share, must not
        # stall the event loop while the lightbox polls.
        return await asyncio.to_thread(proxy_answer, id, False)

    @router.post(
        "/pictures/proxies/{id}/start",
        summary="Start a video's playable copy",
        description="Starts the copy whatever its estimate, unless one is ready or running. Answers the same as the status route.",
        response_model=dict,
    )
    async def start_proxy(request: Request, id: int):
        return await asyncio.to_thread(proxy_answer, id, True)

    @router.get(
        "/pictures/proxies/{id}.mp4",
        summary="Get a video's playable MP4 copy",
        description="The fresh playable copy of a video, served with Range support so it seeks. 404 until one is ready.",
        response_class=FileResponse,
        responses={200: {"content": {"video/mp4": {}}}},
    )
    async def get_proxy_mp4(request: Request, id: int):
        vault = server.vault
        facts = vault.db.run_immediate_read_task(_read_copy_facts, id)
        if facts is not None:
            file_path, record, _rate = facts
            if _copy_is_fresh(vault.image_root, id, file_path, record):
                return FileResponse(
                    ImageUtils.video_copy_path(vault.image_root, id),
                    media_type="video/mp4",
                    headers=_THUMBNAIL_CACHE_HEADERS,
                )
        raise HTTPException(status_code=404, detail="Playable copy not found")

    @router.get(
        "/pictures/waveforms/{id}.json",
        summary="Get a video's audio waveform",
        description=(
            "The loudness of a clip's first audio track: 512 peaks in 0..1 over "
            "the audio's duration in seconds. A silent clip answers nulls, and "
            "that answer is cached too. 503 when no ffmpeg is available."
        ),
        response_class=Response,
        responses={200: {"content": {"application/json": {}}}},
    )
    async def get_waveform(request: Request, id: int):
        vault = server.vault
        generation = getattr(server, "library_generation", 0)

        def fetch_file_path(session: Session, picture_id: int):
            pics = Picture.find(
                session,
                id=picture_id,
                select_fields=["id", "file_path"],
                include_deleted=True,
                include_unimported=True,
            )
            return pics[0].file_path if pics else None

        file_path = vault.db.run_immediate_read_task(fetch_file_path, id)
        if not file_path:
            raise HTTPException(status_code=404, detail="Picture not found")
        if not VideoUtils.is_video_file(file_path):
            raise HTTPException(status_code=404, detail="Picture is not a video")
        if ffmpeg_utils.ffmpeg_executable() is None:
            raise HTTPException(status_code=503, detail="No ffmpeg binary is available")

        cached = ImageUtils.derived_media_path(vault.image_root, file_path, "waveform")

        def fresh_cache() -> bool:
            return bool(
                cached
                and os.path.isfile(cached)
                and not discard_stale_thumbnail(id, file_path, cached)
            )

        if fresh_cache():
            return FileResponse(
                cached, media_type="application/json", headers=_THUMBNAIL_CACHE_HEADERS
            )

        lock = waveform_generation_locks.setdefault(id, asyncio.Lock())
        async with lock:
            if fresh_cache():
                return FileResponse(
                    cached,
                    media_type="application/json",
                    headers=_THUMBNAIL_CACHE_HEADERS,
                )

            def build_waveform_blocking() -> bytes | None:
                source = ImageUtils.resolve_picture_path(vault.image_root, file_path)
                if not source or not os.path.exists(source):
                    logger.warning("Waveform: source file missing: %s", source)
                    return None
                try:
                    peaks = ffmpeg_utils.audio_peaks(source)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    logger.warning(
                        "Waveform: could not read the audio of %s: %s", source, exc
                    )
                    return None
                # No audio track, or an all-silent one: a real answer, cached.
                payload = {"duration": None, "peaks": None}
                if peaks is not None and any(peaks[1]):
                    duration, values = peaks
                    payload = {
                        "duration": round(duration, 3),
                        "peaks": [round(value, 4) for value in values],
                    }
                data = json.dumps(payload).encode()
                ImageUtils.write_derived_media_bytes(
                    vault.image_root, file_path, "waveform", data
                )
                return data

            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(
                _thumbnail_executor, build_waveform_blocking
            )
            if generation != getattr(server, "library_generation", 0):
                raise HTTPException(
                    status_code=503,
                    detail="The active library changed while the waveform was read.",
                )
        if data is None:
            # Already logged why: a missing file or an unreadable audio track.
            raise HTTPException(status_code=404, detail="Waveform not available")
        return Response(
            content=data,
            media_type="application/json",
            headers=_THUMBNAIL_CACHE_HEADERS,
        )

    @router.get(
        "/video-copies",
        summary="Count the playable copies kept for this library",
        description="The number of playable MP4 copies on disk and their total size. Owner only.",
        response_model=dict,
    )
    def get_video_copies(request: Request):
        found = _list_copy_files(server.vault.image_root)
        return {"count": len(found), "bytes": sum(size for _, size in found)}

    @router.delete(
        "/video-copies",
        summary="Delete every playable copy kept for this library",
        description=(
            "Removes the copy rows and every copy file in this library. A copy "
            "is made again when its clip is next opened. Owner only."
        ),
        response_model=dict,
    )
    def delete_video_copies(request: Request):
        vault = server.vault
        # A build still queued or running would write its copy back after the
        # listing below and report it as deleted.
        drop_copy_jobs(vault.image_root)

        def clear_rows(session: Session) -> None:
            session.execute(delete(VideoCopy))
            session.commit()

        vault.db.run_task(clear_rows)
        removed = 0
        freed = 0
        for path, size in _list_copy_files(vault.image_root):
            try:
                os.remove(path)
            except OSError as exc:
                logger.warning("Failed to delete video copy %s: %s", path, exc)
                continue
            removed += 1
            freed += size
        logger.info("Deleted %s playable copies (%s bytes)", removed, freed)
        return {"removed": removed, "bytes": freed}

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
