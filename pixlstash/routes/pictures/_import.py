import os
import shutil
import threading
import time
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace

from fastapi import (
    Body,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from sqlalchemy import (
    delete,
)
from pydantic import BaseModel, ConfigDict
from sqlmodel import select, Session
from typing import Optional

from pixlstash.database import DBPriority

from pixlstash.db_models import (
    Picture,
    PictureProjectMember,
    Tag,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks import TaskType
from pixlstash.tasks.picture_import_task import PictureImportTask
from pixlstash.db_models.tag import (
    TAG_PENDING_SENTINEL,
    is_tag_sentinel,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
)

from pixlstash.db_models.character import Character
from pixlstash.db_models.picture_set import PictureSet
from pixlstash.db_models.project import Project
from pixlstash.services.set_lock_service import locked_set_ids

from ._helpers import (
    _create_picture_imports,
    _normalise_sidecar_stem,
    _parse_sidecar_tags,
)


logger = get_logger(__name__)

# Media extensions accepted by both the one-shot upload import and the async
# streaming-staging import (#459). Kept module-level so both paths agree.
STAGING_ALLOWED_MEDIA_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".heic",
    ".heif",
    ".avif",
    ".mp4",
    ".webm",
    ".mov",
    ".avi",
    ".mkv",
}

# Caption sidecar extension (mirrors the one-shot import's allowed_caption_exts).
STAGING_ALLOWED_CAPTION_EXTS = {".txt"}

# Bounds for a single staging session, mirroring the one-shot import limits.
_STAGING_MAX_FILES = 50_000
_STAGING_MAX_FILE_BYTES = 20 * 1024**3  # 20 GB per streamed file / zip
_STAGING_MAX_ZIP_ENTRIES = 50_000  # max files inside a single zip
_STAGING_MAX_ZIP_DECOMPRESSED_BYTES = 50 * 1024**3  # 50 GB total decompressed

# Reaper bounds. An opened-but-never-committed session leaks its .staging/ dir
# and memory; a finished session is never popped otherwise. The reaper (run
# opportunistically from the staging routes) evicts both.
_STAGING_SESSION_TTL_S = 3600  # evict a session with no activity for this long
_STAGING_TERMINAL_GRACE_S = 300  # keep a finished session briefly for a last poll


def _reap_staging_sessions(server, now_ms: Optional[int] = None) -> None:
    """Evict stale/finished staging sessions and remove their staging dirs.

    * A session whose background import is still running is never touched.
    * A finished (completed/failed/cancelled) session is popped once it has been
      idle past ``_STAGING_TERMINAL_GRACE_S`` (leaving a window for a final
      status poll).
    * Any other session (e.g. opened but never committed) is popped once idle
      past ``_STAGING_SESSION_TTL_S``.

    Called opportunistically from the staging routes, so no background thread is
    needed. Safe to call frequently - it only touches disk when it evicts.
    """
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    for staging_id, session in list(server.staging_sessions.items()):
        task = session.get("task")
        status_value = (
            getattr(getattr(task, "status", None), "value", None)
            if task is not None
            else None
        )
        if status_value in ("pending", "running"):
            # Active import - never reap it out from under the running task.
            continue
        idle_ms = now_ms - int(session.get("last_update_epoch_ms") or 0)
        terminal = status_value in ("completed", "failed", "cancelled")
        if terminal and idle_ms > _STAGING_TERMINAL_GRACE_S * 1000:
            reason = f"finished import (status={status_value})"
        elif idle_ms > _STAGING_SESSION_TTL_S * 1000:
            reason = "idle TTL exceeded"
        else:
            continue
        staging_dir = session.get("staging_dir")
        if staging_dir and os.path.isdir(staging_dir):
            try:
                shutil.rmtree(staging_dir)
            except OSError as exc:
                logger.warning(
                    "Staging reaper: failed to remove dir %s for session %s: %s",
                    staging_dir,
                    staging_id,
                    exc,
                )
        server.staging_sessions.pop(staging_id, None)
        logger.info(
            "Staging reaper: evicted session %s (%s, idle=%.0fs)",
            staging_id,
            reason,
            idle_ms / 1000.0,
        )


class StagingOpenRequest(BaseModel):
    """Body for opening an async-import staging session."""

    model_config = ConfigDict(extra="allow")

    project_id: Optional[int] = None
    """Optional project every imported picture is added to on commit."""

    set_id: Optional[int] = None
    """Optional picture set every imported picture is added to on commit
    (a drop-to-set target). Independent of ``character_id``."""

    character_id: Optional[int] = None
    """Optional character every imported picture is associated with on commit
    (a drop-to-character target; deferred via ``pending_character_id`` until face
    extraction runs). Independent of ``set_id``."""

    total_files: Optional[int] = None
    """Client-declared file count (a hint for the progress UI / safe threshold);
    the actual import uses whatever was streamed before commit."""


class StagingOpenResponse(BaseModel):
    """Response for a newly-opened staging session."""

    model_config = ConfigDict(extra="allow")

    staging_id: str
    """Opaque id for the staging session; used on every subsequent call."""

    safe_threshold: int
    """The declared file count at which the client may hand off to the safe
    (background) window; ``0`` when the client did not declare a total."""


class StagingFilesResponse(BaseModel):
    """Response after streaming one batch of files into a staging session."""

    model_config = ConfigDict(extra="allow")

    staging_id: str
    staged: int
    """Total files staged in this session so far."""

    received: list[str]
    """Original filenames accepted in this request (media files staged, zips
    extracted, and caption sidecars stored)."""

    skipped: list[str]
    """Filenames rejected in this request (unsupported extension / empty /
    unreadable)."""

    sidecars: int
    """Total ``.txt`` caption sidecars stored in this session so far."""


class StagingCommitResponse(BaseModel):
    """Response for the safe handoff to the background import task."""

    model_config = ConfigDict(extra="allow")

    staging_id: str
    task_id: str
    """Id of the background ``PictureImportTask`` finishing the import."""

    staged_count: int


class StagingStatusResponse(BaseModel):
    """Progress/status for a staging session and its background import."""

    model_config = ConfigDict(extra="allow")

    staging_id: str
    stage: str
    """One of ``staging`` / ``importing`` / ``completed`` / ``failed`` /
    ``cancelled``."""

    staged: int
    total: int
    processed: int
    task_id: Optional[str] = None
    imported_count: Optional[int] = None
    """Staged files that became new pictures."""

    duplicate_count: Optional[int] = None
    """Staged files whose content is already in the vault as a LIVE picture
    (including a second copy inside this same batch)."""

    scrapheaped_count: Optional[int] = None
    """Staged files whose content matches a picture in the Scrapheap.

    Not imported again and not restored: the restore is *offered*, because the
    user scrapheapped those pictures on purpose. Counted per FILE, so
    ``imported + duplicate + scrapheaped + failed + cancelled == total``.
    """

    scrapheaped_picture_ids: Optional[list[int]] = None
    """Distinct scrapheaped pictures behind ``scrapheaped_count``, per PICTURE,
    so several incoming copies of one content name its id once. Feed these
    straight to ``POST /pictures/scrapheap/restore``."""

    failed_count: Optional[int] = None
    """Staged files that could not be hashed or ingested."""

    cancelled_count: Optional[int] = None
    """Staged files never reached because the import was cancelled. Present so
    the buckets still sum to ``total`` on a cancelled run."""

    error: Optional[str] = None


class ImportStartResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str


class ImportResultEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    """``success`` (a new picture), ``duplicate`` (content already live in the
    vault), or ``scrapheaped`` (content matches a picture in the Scrapheap: not
    imported again, offered for restore instead)."""

    picture_id: Optional[int] = None
    file: Optional[str] = None


class ImportStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    stage: str
    total: int
    processed: int
    progress: float
    results: Optional[list[ImportResultEntry]] = None
    imported_count: Optional[int] = None
    """Files that became new pictures. Set once the import completes."""

    duplicate_count: Optional[int] = None
    """Files whose content is already in the vault as a LIVE picture."""

    scrapheaped_count: Optional[int] = None
    """Files whose content matches a picture in the Scrapheap.

    Counted per FILE, so ``imported_count + duplicate_count +
    scrapheaped_count == total``. These files were deliberately not imported
    again (that would double the bytes on disk and refill the duplicate queue)
    and deliberately not restored either: the user scrapheapped them on
    purpose, so restoring is offered, not performed.
    """

    scrapheaped_picture_ids: Optional[list[int]] = None
    """Distinct scrapheaped pictures behind ``scrapheaped_count``.

    Per PICTURE, not per file: several incoming copies of the same content name
    one id once. Feed these straight to ``POST /pictures/scrapheap/restore``.
    """

    error: Optional[str] = None


def register_routes(router, server):
    @router.post(
        "/pictures/import",
        summary="Import media files",
        description="Starts an asynchronous import of uploaded image/video files (or zip contents) and returns a task id.",
        response_model=ImportStartResponse,
    )
    async def import_pictures(
        request: Request,
        file: list[UploadFile] = File(None),
        project_id: int | None = Form(None),
    ):
        # Capture the originating tab's client id synchronously, at request
        # entry. The import runs on a detached executor thread where the origin
        # contextvar is dead, so we stash this on the task record and carry it
        # explicitly into the PICTURE_IMPORTED event below.
        origin_client_id = getattr(request.state, "origin_client_id", None)
        _MAX_UPLOAD_BYTES = 20 * 1024**3  # 20 GB per uploaded file / zip
        _MAX_ZIP_ENTRIES = 50_000  # max files inside a zip
        _MAX_ZIP_DECOMPRESSED_BYTES = 50 * 1024**3  # 50 GB total decompressed

        face_worker_problem = server.vault.worker_unavailable_reason(
            TaskType.FACE_EXTRACTION
        )
        if face_worker_problem:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot import: {face_worker_problem}.",
            )

        # The same fail-fast the staging path runs. Without it a project id that
        # does not exist here reaches the membership INSERT *after* every file
        # has been imported and committed, where it surfaces as a raw SQLite
        # FOREIGN KEY error and the caller is told the whole import failed while
        # the pictures are in fact already in the vault. Ids go stale for real:
        # a ComfyUI graph stores the project as "<name> #<id>", so a replayed
        # workflow carries whatever id was current when it was authored.
        _validate_association_targets(None, None, project_id)

        dest_folder = server.vault.image_root
        logger.debug("Importing pictures to folder: " + str(dest_folder))
        os.makedirs(dest_folder, exist_ok=True)
        uploaded_files = []
        uploaded_file_stems: list[str] = []
        sidecar_text_by_stem: dict[str, str] = {}
        allowed_media_exts = {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp",
            ".tiff",
            ".tif",
            ".heic",
            ".heif",
            ".avif",
            ".mp4",
            ".webm",
            ".mov",
            ".avi",
            ".mkv",
        }
        allowed_caption_exts = {".txt"}
        if file is not None:
            for upload in file:
                if not upload.filename:
                    continue
                ext = os.path.splitext(upload.filename)[1].lower()
                if ext == ".zip":
                    # Work directly from the spooled temp file to avoid loading
                    # the entire archive as a bytes object in memory (which would
                    # require as much RAM as the zip is large, e.g. 16+ GB).
                    upload_file = upload.file
                    upload_file.seek(0, 2)
                    upload_size = upload_file.tell()
                    upload_file.seek(0)
                    if upload_size == 0:
                        continue
                    if upload_size > _MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"Uploaded file '{upload.filename}' exceeds the "
                                f"{_MAX_UPLOAD_BYTES // 1024**3} GB limit."
                            ),
                        )
                    try:
                        with zipfile.ZipFile(upload_file) as zip_file:
                            entries = [i for i in zip_file.infolist() if not i.is_dir()]
                            if len(entries) > _MAX_ZIP_ENTRIES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=f"Zip '{upload.filename}' contains too many files (max {_MAX_ZIP_ENTRIES:,}).",
                                )
                            total_decompressed = sum(i.file_size for i in entries)
                            if total_decompressed > _MAX_ZIP_DECOMPRESSED_BYTES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=(
                                        f"Zip '{upload.filename}' decompressed size exceeds the "
                                        f"{_MAX_ZIP_DECOMPRESSED_BYTES // 1024**3} GB limit."
                                    ),
                                )
                            added = 0
                            for info in entries:
                                inner_ext = os.path.splitext(info.filename)[1].lower()
                                if (
                                    inner_ext not in allowed_media_exts
                                    and inner_ext not in allowed_caption_exts
                                ):
                                    continue
                                with zip_file.open(info) as handle:
                                    data = handle.read()
                                if not data:
                                    continue
                                base_name = os.path.basename(info.filename)
                                stem = _normalise_sidecar_stem(base_name)
                                if inner_ext in allowed_caption_exts:
                                    sidecar_text_by_stem.setdefault(
                                        stem,
                                        data.decode("utf-8", errors="ignore"),
                                    )
                                    continue
                                uploaded_files.append((data, inner_ext, base_name))
                                uploaded_file_stems.append(stem)
                                added += 1
                            if added == 0:
                                logger.warning(
                                    "No valid media files found in zip: %s",
                                    upload.filename,
                                )
                    except zipfile.BadZipFile as exc:
                        logger.error("Invalid zip file: %s", upload.filename)
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid zip file",
                        ) from exc
                else:
                    # Non-zip files are typically small; buffer normally.
                    contents = await upload.read()
                    if not contents:
                        continue
                    if len(contents) > _MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"Uploaded file '{upload.filename}' exceeds the "
                                f"{_MAX_UPLOAD_BYTES // 1024**3} GB limit."
                            ),
                        )
                    if ext in allowed_caption_exts:
                        stem = _normalise_sidecar_stem(upload.filename)
                        sidecar_text_by_stem.setdefault(
                            stem,
                            contents.decode("utf-8", errors="ignore"),
                        )
                        continue
                    if ext not in allowed_media_exts:
                        logger.warning(
                            "Skipping file with unsupported extension: %s",
                            upload.filename,
                        )
                        continue
                    uploaded_files.append((contents, ext, upload.filename))
                    uploaded_file_stems.append(_normalise_sidecar_stem(upload.filename))
        else:
            logger.error("No files provided for import")
            raise HTTPException(status_code=400, detail="No image provided")

        if not uploaded_files:
            logger.error("No valid media files found for import")
            raise HTTPException(
                status_code=400,
                detail="No valid media files found for import",
            )

        total_import_bytes_log = sum(len(data) for data, *_ in uploaded_files)
        logger.info(
            "Import request received: files=%d, sidecar_txt=%d, project_id=%s, total_bytes=%d",
            len(uploaded_files),
            len(sidecar_text_by_stem),
            project_id,
            total_import_bytes_log,
        )

        sidecar_tags_by_stem: dict[str, list[str]] = {}
        if sidecar_text_by_stem:
            media_stem_set = set(uploaded_file_stems)
            for stem, raw_text in sidecar_text_by_stem.items():
                # Only consume caption sidecars that have a corresponding media file.
                if stem not in media_stem_set:
                    continue
                parsed_tags = _parse_sidecar_tags(raw_text)
                if parsed_tags:
                    sidecar_tags_by_stem[stem] = parsed_tags

        total_import_bytes = sum(len(data) for data, *_ in uploaded_files)
        free_bytes = shutil.disk_usage(dest_folder).free
        required_bytes = int(total_import_bytes * 1.1)  # 10% headroom
        if required_bytes > free_bytes:
            free_gb = free_bytes / 1024**3
            needed_gb = required_bytes / 1024**3
            raise HTTPException(
                status_code=507,
                detail=(
                    f"Not enough disk space. "
                    f"Import needs {needed_gb:.2f} GB (including 10% headroom) "
                    f"but only {free_gb:.2f} GB is available."
                ),
            )

        task_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        server.import_tasks[task_id] = {
            "status": "in_progress",
            "stage": "queued",
            "total": len(uploaded_files),
            "processed": 0,
            "results": None,
            "error": None,
            "created_epoch_ms": now_ms,
            "last_update_epoch_ms": now_ms,
            "last_poll_log_epoch_ms": 0,
        }

        logger.info(
            "Import task queued: task_id=%s total=%d project_id=%s",
            task_id,
            len(uploaded_files),
            project_id,
        )

        def run_import_task(server, vault, background_lease):
            try:
                task = server.import_tasks[task_id]

                def _mark_stage(stage: str, **extra):
                    task["stage"] = stage
                    task["last_update_epoch_ms"] = int(time.time() * 1000)
                    task.update(extra)
                    logger.info(
                        "Import task stage: task_id=%s stage=%s processed=%d/%d",
                        task_id,
                        stage,
                        int(task.get("processed") or 0),
                        int(task.get("total") or 0),
                    )

                _mark_stage("hash_and_write")

                def _on_picture_written():
                    task["processed"] = task.get("processed", 0) + 1
                    task["last_update_epoch_ms"] = int(time.time() * 1000)

                fingerprints, existing_map, scrapheaped_map, new_picture_map = (
                    _create_picture_imports(
                        SimpleNamespace(vault=vault),
                        uploaded_files,
                        dest_folder,
                        progress_callback=_on_picture_written,
                    )
                )

                # Duplicates are instantly "processed" - credit them now so that
                # the progress bar stays accurate even when most files are dupes.
                # Scrapheap matches are equally instant and are counted in their
                # OWN bucket: they are neither imported nor ordinary duplicates.
                # Both are counted directly (one pass over fingerprints), never derived
                # by subtracting from the total.
                fresh_seen = set()
                duplicate_count_initial = 0
                for fingerprint in fingerprints:
                    if fingerprint in existing_map or fingerprint in fresh_seen:
                        duplicate_count_initial += 1
                    elif fingerprint not in scrapheaped_map:
                        fresh_seen.add(fingerprint)
                scrapheaped_count_initial = sum(
                    1 for fingerprint in fingerprints if fingerprint in scrapheaped_map
                )
                new_pictures = list(new_picture_map.values())
                task["processed"] = (
                    len(new_pictures)
                    + duplicate_count_initial
                    + scrapheaped_count_initial
                )
                task["last_update_epoch_ms"] = int(time.time() * 1000)

                _mark_stage(
                    "deduplicated",
                    duplicate_count_initial=duplicate_count_initial,
                    scrapheaped_count_initial=scrapheaped_count_initial,
                    new_count=len(new_pictures),
                )

                logger.debug(
                    f"Importing {len(new_pictures)} new pictures out of {len(uploaded_files)} uploaded."
                )

                if new_pictures:
                    _mark_stage("persisting_new_pictures")

                    def import_task(session):
                        session.add_all(new_pictures)
                        session.flush()
                        for pic in new_pictures:
                            session.add(
                                Tag(tag=TAG_PENDING_SENTINEL, picture_id=pic.id)
                            )
                        session.commit()
                        for pic in new_pictures:
                            session.refresh(pic)
                        return new_pictures

                    new_pictures = vault.db.run_task(import_task)
                    logger.debug(
                        f"Queuing likeness calculation for {len(new_pictures)} new pictures."
                    )
                else:
                    logger.warning(
                        "No new pictures to import; every file already matches a "
                        "live or scrapheaped picture."
                    )
                    new_pictures = []

                _mark_stage("building_results")
                results = []
                imported_count = 0
                duplicate_count = 0
                scrapheaped_count = 0
                picture_id_sidecar_tags: dict[int, set[str]] = defaultdict(set)
                duplicate_picture_id_set: set[int] = set()
                # Distinct scrapheaped pictures the caller may restore. Several
                # incoming files can match the SAME scrapheaped picture: the
                # count is per FILE (so the buckets sum to the file total) while
                # this list is per PICTURE (so the restore offer is honest about
                # how many rows come back).
                scrapheaped_picture_ids: list[int] = []
                seen_scrapheaped_ids: set[int] = set()
                seen_new_fingerprints = set()
                for stem, _, fingerprint in zip(
                    uploaded_file_stems, uploaded_files, fingerprints
                ):
                    # Three disjoint outcomes per uploaded file. `existing_map`
                    # and `scrapheaped_map` are disjoint by construction (a live
                    # row outranks a soft-deleted one for the same hash), so the
                    # branches below cannot double-count.
                    if fingerprint in existing_map:
                        pic = existing_map[fingerprint]
                        results.append(
                            {
                                "status": "duplicate",
                                "picture_id": pic.id,
                                "file": pic.file_path,
                            }
                        )
                        duplicate_count += 1
                        if pic.id is not None:
                            duplicate_picture_id_set.add(pic.id)
                    elif fingerprint in scrapheaped_map:
                        pic = scrapheaped_map[fingerprint]
                        results.append(
                            {
                                "status": "scrapheaped",
                                "picture_id": pic.id,
                                "file": pic.file_path,
                            }
                        )
                        scrapheaped_count += 1
                        if pic.id is not None and pic.id not in seen_scrapheaped_ids:
                            seen_scrapheaped_ids.add(pic.id)
                            scrapheaped_picture_ids.append(pic.id)
                    elif fingerprint not in seen_new_fingerprints:
                        pic = new_picture_map[fingerprint]
                        results.append(
                            {
                                "status": "success",
                                "picture_id": pic.id,
                                "file": pic.file_path,
                            }
                        )
                        imported_count += 1
                        seen_new_fingerprints.add(fingerprint)
                    else:
                        # A byte-identical repeat inside this upload maps to the
                        # one row created for its first occurrence.
                        pic = new_picture_map[fingerprint]
                        results.append(
                            {
                                "status": "duplicate",
                                "picture_id": pic.id,
                                "file": pic.file_path,
                            }
                        )
                        duplicate_count += 1
                        if pic.id is not None:
                            duplicate_picture_id_set.add(pic.id)

                    if (
                        pic.id is not None
                        and stem in sidecar_tags_by_stem
                        and sidecar_tags_by_stem[stem]
                        # A scrapheaped match is not being imported and the user
                        # has not decided to restore it yet, so this import must
                        # not edit its tags behind its back.
                        and fingerprint not in scrapheaped_map
                    ):
                        picture_id_sidecar_tags[pic.id].update(
                            sidecar_tags_by_stem[stem]
                        )

                if duplicate_count:
                    logger.warning(
                        "Import completed with %d duplicate(s) out of %d file(s).",
                        duplicate_count,
                        len(uploaded_files),
                    )
                if scrapheaped_count:
                    logger.warning(
                        "Import completed with %d file(s) out of %d matching %d "
                        "scrapheaped picture(s) %s: NOT imported again, offered "
                        "for restore instead.",
                        scrapheaped_count,
                        len(uploaded_files),
                        len(scrapheaped_picture_ids),
                        scrapheaped_picture_ids,
                    )
                bucket_total = imported_count + duplicate_count + scrapheaped_count
                if bucket_total != len(uploaded_files):
                    # The three buckets are the whole story of this import; a
                    # mismatch means the summary the user reads is wrong, so say
                    # so loudly rather than let a silently-lossy count ship.
                    logger.error(
                        "Import task %s bucket arithmetic is inconsistent: "
                        "imported=%d + duplicate=%d + scrapheaped=%d = %d, but "
                        "%d file(s) were uploaded.",
                        task_id,
                        imported_count,
                        duplicate_count,
                        scrapheaped_count,
                        bucket_total,
                        len(uploaded_files),
                    )
                server.import_tasks[task_id]["results"] = results
                server.import_tasks[task_id]["processed"] = len(uploaded_files)
                server.import_tasks[task_id]["imported_count"] = imported_count
                server.import_tasks[task_id]["duplicate_count"] = duplicate_count
                server.import_tasks[task_id]["scrapheaped_count"] = scrapheaped_count
                server.import_tasks[task_id]["scrapheaped_picture_ids"] = (
                    scrapheaped_picture_ids
                )
                server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                    time.time() * 1000
                )
                # Only apply import context to pictures that were actually
                # touched by this request (one results row per uploaded file).
                # Scrapheaped matches are deliberately excluded: they were not
                # imported, and stamping `imported_at` / joining them to the
                # target project would quietly act on a picture the user has not
                # chosen to restore.
                all_imported_ids = list(
                    dict.fromkeys(
                        entry.get("picture_id")
                        for entry in results
                        if entry.get("picture_id") is not None
                        and entry.get("status") != "scrapheaped"
                    )
                )

                if picture_id_sidecar_tags:
                    _mark_stage("applying_sidecar_tags")

                    def apply_sidecar_tags(
                        session,
                        mapping: dict[int, set[str]],
                        replace_ids: set[int],
                    ):
                        if not mapping:
                            return []
                        pics = session.exec(
                            select(Picture)
                            .where(Picture.id.in_(list(mapping.keys())))
                            .where(Picture.deleted.is_(False))
                        ).all()
                        changed_ids = []
                        for pic in pics:
                            tag_values = mapping.get(pic.id) or set()
                            if not tag_values:
                                continue
                            existing_values = {
                                (row[0] if isinstance(row, tuple) else row or "")
                                .strip()
                                .lower()
                                for row in session.exec(
                                    select(Tag.tag).where(Tag.picture_id == pic.id)
                                ).all()
                            }
                            changed = False
                            if pic.id in replace_ids:
                                # For duplicate imports with sidecar captions,
                                # replace old tags with sidecar-provided tags.
                                session.exec(
                                    delete(Tag).where(Tag.picture_id == pic.id)
                                )
                                for tag_value in sorted(tag_values):
                                    session.add(Tag(tag=tag_value, picture_id=pic.id))
                                changed = True
                            else:
                                if any(is_tag_sentinel(v) for v in existing_values):
                                    session.exec(
                                        delete(Tag).where(
                                            Tag.picture_id == pic.id,
                                            Tag.tag.like(
                                                TAG_SENTINEL_LIKE_PATTERN,
                                                escape=TAG_SENTINEL_ESCAPE_CHAR,
                                            ),
                                        )
                                    )
                                    changed = True
                                for tag_value in sorted(tag_values):
                                    if tag_value in existing_values:
                                        continue
                                    session.add(Tag(tag=tag_value, picture_id=pic.id))
                                    changed = True
                            if changed:
                                changed_ids.append(pic.id)
                        session.commit()
                        return changed_ids

                    tagged_ids = vault.db.run_task(
                        apply_sidecar_tags,
                        picture_id_sidecar_tags,
                        duplicate_picture_id_set,
                    )
                    if tagged_ids:
                        vault.notify(EventType.CHANGED_TAGS, tagged_ids)

                if all_imported_ids:
                    _mark_stage("finalizing_import_context")
                    # Queue face extraction asynchronously - do not block on it.
                    for pic in new_pictures:
                        vault.get_worker_future(
                            TaskType.FACE_EXTRACTION, Picture, pic.id, "faces"
                        )

                    def apply_import_context(
                        session,
                        ids: list[int],
                        project_id_value: int | None,
                    ):
                        if not ids:
                            return []
                        now = datetime.utcnow()
                        pics = session.exec(
                            select(Picture).where(Picture.id.in_(ids))
                        ).all()
                        updated = []
                        for pic in pics:
                            if pic.imported_at is None:
                                pic.imported_at = now
                            if project_id_value is not None:
                                member = session.exec(
                                    select(PictureProjectMember).where(
                                        PictureProjectMember.picture_id == pic.id,
                                        PictureProjectMember.project_id
                                        == project_id_value,
                                    )
                                ).first()
                                if member is None:
                                    session.add(
                                        PictureProjectMember(
                                            picture_id=pic.id,
                                            project_id=project_id_value,
                                        )
                                    )
                                pic.project_id = project_id_value
                            session.add(pic)
                            updated.append(pic.id)
                        session.commit()
                        return updated

                    imported_ids = vault.db.run_task(
                        apply_import_context,
                        all_imported_ids,
                        project_id,
                    )
                    server.import_tasks[task_id]["status"] = "completed"
                    server.import_tasks[task_id]["stage"] = "completed"
                    server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                        time.time() * 1000
                    )
                    vault.notify(
                        EventType.CHANGED_PICTURES,
                        {
                            "picture_ids": imported_ids or [],
                            "source": "ui" if origin_client_id else "external",
                            "origin_client_id": origin_client_id,
                            "change_kind": "added",
                        },
                    )
                    if imported_ids:
                        # A genuine PixlStash tab attaches X-Client-Id (captured
                        # as origin_client_id); an external API client - e.g. a
                        # ComfyUI node POSTing generated output to
                        # /pictures/import - does not. Tag the former "ui" (slick
                        # in-place insert) and the latter "external" so an
                        # outside push raises the New-pictures pill instead of
                        # auto-inserting cards under the user.
                        import_source = "ui" if origin_client_id else "external"
                        vault.notify(
                            EventType.PICTURE_IMPORTED,
                            {
                                "ids": imported_ids,
                                "source": import_source,
                                "origin_client_id": origin_client_id,
                                "change_kind": "added",
                            },
                        )
                else:
                    server.import_tasks[task_id]["status"] = "completed"
                    server.import_tasks[task_id]["stage"] = "completed"
                    server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                        time.time() * 1000
                    )
                    vault.notify(
                        EventType.CHANGED_PICTURES,
                        {
                            "source": "ui" if origin_client_id else "external",
                            "origin_client_id": origin_client_id,
                            "change_kind": "updated",
                        },
                    )
                logger.info("Import task completed: task_id=%s", task_id)
            except Exception as exc:
                server.import_tasks[task_id]["status"] = "failed"
                server.import_tasks[task_id]["stage"] = "failed"
                server.import_tasks[task_id]["error"] = str(exc)
                server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                    time.time() * 1000
                )
                logger.error(f"Import task {task_id} failed: {exc}")
            finally:
                server.library_coordinator.release_read(background_lease)

        # Schedule independently from the ASGI/event-loop lifecycle. Using
        # BackgroundTasks would tie the work to the response and a default
        # executor Future can be cancelled while its thread keeps running at
        # event-loop shutdown. The worker itself owns and finally releases the
        # extra library lease, so a switch cannot retire its vault underneath
        # it.
        background_lease = server.library_coordinator.acquire_read()
        if background_lease is None:
            server.import_tasks[task_id]["status"] = "failed"
            server.import_tasks[task_id]["stage"] = "failed"
            server.import_tasks[task_id]["error"] = "Library is unavailable."
            raise HTTPException(
                status_code=503,
                detail="Library is switching or unavailable. Try again.",
            )
        server.import_tasks[task_id]["library_uuid"] = background_lease.library_uuid
        server.import_tasks[task_id]["generation"] = background_lease.generation
        try:
            worker = threading.Thread(
                target=run_import_task,
                args=(server, background_lease.vault, background_lease),
                daemon=True,
            )
            worker.start()
        except Exception:
            server.library_coordinator.release_read(background_lease)
            raise
        return {"task_id": task_id}

    @router.get(
        "/pictures/import/status",
        summary="Get import job status",
        description="Returns progress and result information for a previously started import task.",
        response_model=ImportStatusResponse,
    )
    def import_status(request: Request, task_id: str):
        task = server.import_tasks.get(task_id)
        lease = getattr(request.state, "library_lease", None)
        if (
            not task
            or lease is None
            or task.get("library_uuid") != lease.library_uuid
            or task.get("generation") != lease.generation
        ):
            raise HTTPException(status_code=404, detail="Task not found")

        now_ms = int(time.time() * 1000)
        last_poll_log_epoch_ms = int(task.get("last_poll_log_epoch_ms") or 0)
        if (
            task.get("status") == "in_progress"
            and now_ms - last_poll_log_epoch_ms >= 10_000
        ):
            task["last_poll_log_epoch_ms"] = now_ms
            created_ms = int(task.get("created_epoch_ms") or now_ms)
            elapsed_s = max(0.0, (now_ms - created_ms) / 1000.0)
            logger.info(
                "Import task heartbeat: task_id=%s stage=%s processed=%d/%d elapsed=%.1fs",
                task_id,
                task.get("stage", "unknown"),
                int(task.get("processed") or 0),
                int(task.get("total") or 0),
                elapsed_s,
            )

        total = task.get("total") or 0
        processed = task.get("processed") or 0
        progress = (processed / total * 100.0) if total else 0.0

        payload = {
            "status": task["status"],
            "stage": task.get("stage", "unknown"),
            "total": total,
            "processed": processed,
            "progress": progress,
        }
        if task["status"] == "completed":
            payload["results"] = task.get("results") or []
            # The three disjoint buckets, each counted directly while the results
            # were built: never derived from the total by subtraction.
            payload["imported_count"] = task.get("imported_count")
            payload["duplicate_count"] = task.get("duplicate_count")
            payload["scrapheaped_count"] = task.get("scrapheaped_count")
            payload["scrapheaped_picture_ids"] = (
                task.get("scrapheaped_picture_ids") or []
            )
        if task["status"] == "failed":
            payload["error"] = task.get("error")
        return payload

    # ------------------------------------------------------------------ #
    # Async streaming-staging import (#459)                               #
    #                                                                     #
    # Phase A (unsafe, tab must stay open): open a staging session, then  #
    # stream files into it. Phase B (safe, tab may close): commit hands   #
    # off to a background PictureImportTask on the shared TaskRunner,      #
    # whose progress surfaces in the task manager. All four mutating      #
    # routes are OWNER_ONLY (streaming client-provided bytes into the      #
    # vault - NOT a host-filesystem read), mirroring POST /pictures/import.#
    # ------------------------------------------------------------------ #

    def _staging_base_dir() -> str:
        path = os.path.join(server.vault.image_root, ".staging")
        if os.path.lexists(path) and (os.path.islink(path) or not os.path.isdir(path)):
            raise HTTPException(
                status_code=500,
                detail="The library staging path is not a trusted directory.",
            )
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
        return path

    def _get_session_or_404(request: Request, staging_id: str) -> dict:
        session = server.staging_sessions.get(staging_id)
        lease = getattr(request.state, "library_lease", None)
        if (
            session is None
            or lease is None
            or session.get("library_uuid") != lease.library_uuid
            or session.get("generation") != lease.generation
        ):
            raise HTTPException(status_code=404, detail="Staging session not found")
        return session

    def _validate_association_targets(set_id, character_id, project_id) -> None:
        """Fail fast if a drop-to-set / -character / -project target is invalid.

        A nonexistent set/character/project must error rather than silently
        no-op (or fail downstream after pictures are already imported), and a
        locked set must refuse new members (the same lock rule the add-to-set
        route enforces). Runs at open (on the drop) and again at commit.
        """

        def _check(session: Session):
            if set_id is not None:
                picture_set = session.get(PictureSet, set_id)
                if picture_set is None:
                    return ("set_missing", None)
                if set_id in locked_set_ids(session, [set_id]):
                    return ("set_locked", picture_set.name)
            if character_id is not None:
                if session.get(Character, character_id) is None:
                    return ("character_missing", None)
            if project_id is not None:
                if session.get(Project, project_id) is None:
                    return ("project_missing", None)
            return None

        outcome = server.vault.db.run_task(_check, priority=DBPriority.IMMEDIATE)
        if outcome is None:
            return
        kind, name = outcome
        if kind == "set_missing":
            raise HTTPException(
                status_code=404, detail=f"Picture set {set_id} not found"
            )
        if kind == "set_locked":
            raise HTTPException(
                status_code=409,
                detail=f"Picture set '{name}' is locked and cannot take new members",
            )
        if kind == "character_missing":
            raise HTTPException(
                status_code=404, detail=f"Character {character_id} not found"
            )
        if kind == "project_missing":
            raise HTTPException(
                status_code=404, detail=f"Project {project_id} not found"
            )

    @router.post(
        "/pictures/import/staging",
        summary="Open an async import staging session",
        description=(
            "Opens a staging session for the async streaming import (#459). "
            "Stream files into it with POST "
            "/pictures/import/staging/{staging_id}/files while the tab stays "
            "open, then hand off to the background import with "
            "POST /pictures/import/staging/{staging_id}/commit."
        ),
        response_model=StagingOpenResponse,
    )
    def open_staging_session(
        request: Request,
        payload: StagingOpenRequest = Body(default_factory=StagingOpenRequest),
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        lease = request.state.library_lease
        # Opportunistically evict stale/finished sessions before opening a new
        # one, so abandoned staging dirs never accumulate.
        _reap_staging_sessions(server)
        # Reject an invalid drop target up front (on the drop), before any files
        # are streamed - a nonexistent set/character/project must not silently
        # no-op or fail downstream after import.
        _validate_association_targets(
            payload.set_id, payload.character_id, payload.project_id
        )

        staging_id = str(uuid.uuid4())
        staging_dir = os.path.join(_staging_base_dir(), staging_id)
        try:
            os.makedirs(staging_dir, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create staging dir %s: %s", staging_dir, exc)
            raise HTTPException(
                status_code=500, detail="Could not create staging directory"
            ) from exc

        declared_total = int(payload.total_files or 0)
        now_ms = int(time.time() * 1000)
        server.staging_sessions[staging_id] = {
            "staging_id": staging_id,
            "stage": "staging",
            "staging_dir": staging_dir,
            "project_id": payload.project_id,
            "set_id": payload.set_id,
            "character_id": payload.character_id,
            "declared_total": declared_total,
            "staged_files": [],
            # stem -> raw sidecar text, from .txt uploads and zip .txt entries.
            "sidecar_text_by_stem": {},
            "task": None,
            "task_id": None,
            "origin_client_id": origin_client_id,
            "library_uuid": lease.library_uuid,
            "generation": lease.generation,
            "error": None,
            "created_epoch_ms": now_ms,
            "last_update_epoch_ms": now_ms,
        }
        logger.info(
            "Staging session opened: staging_id=%s declared_total=%d project_id=%s "
            "set_id=%s character_id=%s",
            staging_id,
            declared_total,
            payload.project_id,
            payload.set_id,
            payload.character_id,
        )
        return {"staging_id": staging_id, "safe_threshold": declared_total}

    @router.post(
        "/pictures/import/staging/{staging_id}/files",
        summary="Stream files into a staging session",
        description=(
            "Streams one batch of media files into an open staging session. "
            "May be called repeatedly during the unsafe (tab-open) window. "
            "Unsupported or empty files are skipped and reported."
        ),
        response_model=StagingFilesResponse,
    )
    async def stage_files(
        request: Request,
        staging_id: str,
        file: list[UploadFile] = File(None),
    ):
        session = _get_session_or_404(request, staging_id)
        if session["stage"] != "staging":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Staging session is no longer accepting files "
                    f"(stage={session['stage']})"
                ),
            )
        if not file:
            raise HTTPException(status_code=400, detail="No files provided")

        staging_dir = session["staging_dir"]
        staged_files: list[dict] = session["staged_files"]
        sidecar_text_by_stem: dict[str, str] = session["sidecar_text_by_stem"]
        received: list[str] = []
        skipped: list[str] = []

        def _stage_media_bytes(data: bytes, ext: str, original_name: str) -> bool:
            """Write already-read media bytes into the staging dir (zip entries)."""
            if not data:
                return False
            if len(staged_files) >= _STAGING_MAX_FILES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Staging session exceeds the maximum of "
                        f"{_STAGING_MAX_FILES:,} files"
                    ),
                )
            dest_path = os.path.join(staging_dir, f"{uuid.uuid4()}{ext}")
            try:
                with open(dest_path, "wb") as out:
                    out.write(data)
            except OSError as exc:
                logger.error(
                    "Staging %s: failed to write extracted file %s: %s",
                    staging_id,
                    original_name,
                    exc,
                )
                return False
            staged_files.append(
                {
                    "file_path": dest_path,
                    "original_file_name": os.path.basename(original_name),
                }
            )
            return True

        for upload in file:
            if not upload.filename:
                continue
            ext = os.path.splitext(upload.filename)[1].lower()

            # --- Caption sidecar (.txt): stored by stem, matched to an image at
            # commit; mirrors the one-shot import's sidecar_text_by_stem. ---
            if ext in STAGING_ALLOWED_CAPTION_EXTS:
                try:
                    await upload.seek(0)
                    raw = await upload.read()
                    sidecar_text_by_stem.setdefault(
                        _normalise_sidecar_stem(upload.filename),
                        raw.decode("utf-8", errors="ignore"),
                    )
                    received.append(upload.filename)
                except Exception as exc:
                    logger.warning(
                        "Staging %s: failed to read sidecar %s: %s",
                        staging_id,
                        upload.filename,
                        exc,
                    )
                    skipped.append(upload.filename)
                continue

            # --- Zip archive: extract server-side into the staging area,
            # mirroring the one-shot import's zip handling (media entries staged,
            # .txt entries stored as sidecars, same entry/size guards). ---
            if ext == ".zip":
                try:
                    await upload.seek(0)
                    upload_file = upload.file
                    upload_file.seek(0, 2)
                    upload_size = upload_file.tell()
                    upload_file.seek(0)
                    if upload_size == 0:
                        skipped.append(upload.filename)
                        continue
                    if upload_size > _STAGING_MAX_FILE_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"Zip '{upload.filename}' exceeds the "
                                f"{_STAGING_MAX_FILE_BYTES // 1024**3} GB limit"
                            ),
                        )
                    with zipfile.ZipFile(upload_file) as zip_file:
                        entries = [i for i in zip_file.infolist() if not i.is_dir()]
                        if len(entries) > _STAGING_MAX_ZIP_ENTRIES:
                            raise HTTPException(
                                status_code=413,
                                detail=(
                                    f"Zip '{upload.filename}' contains too many "
                                    f"files (max {_STAGING_MAX_ZIP_ENTRIES:,})."
                                ),
                            )
                        if (
                            sum(i.file_size for i in entries)
                            > _STAGING_MAX_ZIP_DECOMPRESSED_BYTES
                        ):
                            raise HTTPException(
                                status_code=413,
                                detail=(
                                    f"Zip '{upload.filename}' decompressed size "
                                    f"exceeds the "
                                    f"{_STAGING_MAX_ZIP_DECOMPRESSED_BYTES // 1024**3}"
                                    " GB limit."
                                ),
                            )
                        added = 0
                        for info in entries:
                            inner_ext = os.path.splitext(info.filename)[1].lower()
                            base_name = os.path.basename(info.filename)
                            if inner_ext in STAGING_ALLOWED_CAPTION_EXTS:
                                with zip_file.open(info) as handle:
                                    data = handle.read()
                                if data:
                                    sidecar_text_by_stem.setdefault(
                                        _normalise_sidecar_stem(base_name),
                                        data.decode("utf-8", errors="ignore"),
                                    )
                                continue
                            if inner_ext not in STAGING_ALLOWED_MEDIA_EXTS:
                                continue
                            with zip_file.open(info) as handle:
                                data = handle.read()
                            if _stage_media_bytes(data, inner_ext, base_name):
                                added += 1
                        if added:
                            received.append(upload.filename)
                        else:
                            logger.warning(
                                "Staging %s: no valid media in zip %s",
                                staging_id,
                                upload.filename,
                            )
                            skipped.append(upload.filename)
                except HTTPException:
                    raise
                except zipfile.BadZipFile:
                    logger.error(
                        "Staging %s: invalid zip file %s",
                        staging_id,
                        upload.filename,
                    )
                    skipped.append(upload.filename)
                continue

            # --- Plain media: stream to disk to avoid buffering a large file. ---
            if ext not in STAGING_ALLOWED_MEDIA_EXTS:
                logger.warning(
                    "Staging %s: skipping unsupported file %s",
                    staging_id,
                    upload.filename,
                )
                skipped.append(upload.filename)
                continue
            if len(staged_files) >= _STAGING_MAX_FILES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Staging session exceeds the maximum of "
                        f"{_STAGING_MAX_FILES:,} files"
                    ),
                )
            dest_name = f"{uuid.uuid4()}{ext}"
            dest_path = os.path.join(staging_dir, dest_name)
            written = 0
            try:
                await upload.seek(0)
                with open(dest_path, "wb") as out:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > _STAGING_MAX_FILE_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=(
                                    f"File '{upload.filename}' exceeds the "
                                    f"{_STAGING_MAX_FILE_BYTES // 1024**3} GB limit"
                                ),
                            )
                        out.write(chunk)
            except HTTPException:
                if os.path.isfile(dest_path):
                    try:
                        os.remove(dest_path)
                    except OSError as exc:
                        logger.warning(
                            "Staging %s: failed to clean up rejected file %s: %s",
                            staging_id,
                            dest_path,
                            exc,
                        )
                raise
            except Exception as exc:
                logger.error(
                    "Staging %s: failed to write %s to %s: %s",
                    staging_id,
                    upload.filename,
                    dest_path,
                    exc,
                )
                skipped.append(upload.filename)
                if os.path.isfile(dest_path):
                    try:
                        os.remove(dest_path)
                    except OSError as cleanup_exc:
                        logger.warning(
                            "Staging %s: failed to clean up partial file %s: %s",
                            staging_id,
                            dest_path,
                            cleanup_exc,
                        )
                continue
            if written == 0:
                skipped.append(upload.filename)
                if os.path.isfile(dest_path):
                    try:
                        os.remove(dest_path)
                    except OSError as exc:
                        logger.warning(
                            "Staging %s: failed to remove empty file %s: %s",
                            staging_id,
                            dest_path,
                            exc,
                        )
                continue
            staged_files.append(
                {
                    "file_path": dest_path,
                    "original_file_name": os.path.basename(upload.filename),
                }
            )
            received.append(upload.filename)

        session["last_update_epoch_ms"] = int(time.time() * 1000)
        logger.debug(
            "Staging %s: received %d, skipped %d, total staged %d, sidecars %d",
            staging_id,
            len(received),
            len(skipped),
            len(staged_files),
            len(sidecar_text_by_stem),
        )
        return {
            "staging_id": staging_id,
            "staged": len(staged_files),
            "received": received,
            "skipped": skipped,
            "sidecars": len(sidecar_text_by_stem),
        }

    @router.post(
        "/pictures/import/staging/{staging_id}/commit",
        summary="Hand off a staging session to the background import",
        description=(
            "Closes the unsafe window and hands the staged files off to a "
            "background PictureImportTask on the shared task runner. The import "
            "completes server-side (the safe window); the tab may now close. "
            "Progress is reported via GET "
            "/pictures/import/staging/{staging_id}/status and the task manager."
        ),
        response_model=StagingCommitResponse,
    )
    def commit_staging_session(request: Request, staging_id: str):
        session = _get_session_or_404(request, staging_id)
        if session["stage"] != "staging":
            raise HTTPException(
                status_code=409,
                detail=f"Staging session already handed off (stage={session['stage']})",
            )
        staged_files: list[dict] = session["staged_files"]
        if not staged_files:
            raise HTTPException(status_code=400, detail="No staged files to import")
        face_worker_problem = server.vault.worker_unavailable_reason(
            TaskType.FACE_EXTRACTION
        )
        if face_worker_problem:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot import: {face_worker_problem}.",
            )

        # Re-validate the drop target at handoff (it may have been deleted/locked
        # since open); still fail loudly rather than silently skip association.
        _validate_association_targets(
            session["set_id"], session["character_id"], session["project_id"]
        )

        # Resolve caption sidecars to their images by basename stem, parsing each
        # via the one-shot import's tag rule (comma-separated tag lists). Orphan
        # sidecars (no matching staged image) are logged and skipped, not applied.
        sidecar_text_by_stem: dict[str, str] = session["sidecar_text_by_stem"]
        sidecar_tags_by_stem: dict[str, list[str]] = {}
        if sidecar_text_by_stem:
            media_stems = {
                _normalise_sidecar_stem(entry.get("original_file_name") or "")
                for entry in staged_files
            }
            for stem, raw_text in sidecar_text_by_stem.items():
                if stem not in media_stems:
                    logger.info(
                        "Staging %s: caption sidecar '%s' has no matching image; "
                        "skipping",
                        staging_id,
                        stem,
                    )
                    continue
                parsed = _parse_sidecar_tags(raw_text)
                if parsed:
                    sidecar_tags_by_stem[stem] = parsed

        # Guard against committing more than the vault can hold: the background
        # import copies each staged file into the canonical vault location before
        # the staging dir is removed, so it briefly needs room for both.
        try:
            staged_bytes = sum(
                os.path.getsize(entry["file_path"])
                for entry in staged_files
                if entry.get("file_path") and os.path.isfile(entry["file_path"])
            )
            free_bytes = shutil.disk_usage(server.vault.image_root).free
            if int(staged_bytes * 1.1) > free_bytes:
                raise HTTPException(
                    status_code=507,
                    detail=(
                        "Not enough disk space to finish the import "
                        f"({staged_bytes / 1024**3:.2f} GB staged, "
                        f"{free_bytes / 1024**3:.2f} GB free)."
                    ),
                )
        except HTTPException:
            raise
        except OSError as exc:
            logger.warning(
                "Staging %s: disk-space check failed (proceeding): %s",
                staging_id,
                exc,
            )

        task = PictureImportTask(
            server.vault.db,
            list(staged_files),
            project_id=session["project_id"],
            set_id=session["set_id"],
            character_id=session["character_id"],
            sidecar_tags_by_stem=sidecar_tags_by_stem,
            staging_id=staging_id,
            staging_dir=session["staging_dir"],
            origin_client_id=session["origin_client_id"],
        )
        task_id = server.vault.submit_task(task)
        if task_id is None:
            raise HTTPException(status_code=503, detail="Task runner is not available")
        session["task"] = task
        session["task_id"] = task_id
        session["stage"] = "importing"
        session["last_update_epoch_ms"] = int(time.time() * 1000)
        logger.info(
            "Staging session committed: staging_id=%s task_id=%s staged_count=%d",
            staging_id,
            task_id,
            len(staged_files),
        )
        return {
            "staging_id": staging_id,
            "task_id": task_id,
            "staged_count": len(staged_files),
        }

    @router.delete(
        "/pictures/import/staging/{staging_id}",
        summary="Cancel a staging session",
        description=(
            "Cancels an async import staging session that has not yet been "
            "committed, discarding its streamed files. After commit the import "
            "runs to completion in the background and cannot be cancelled here."
        ),
        response_model=StagingStatusResponse,
    )
    def cancel_staging_session(request: Request, staging_id: str):
        session = _get_session_or_404(request, staging_id)
        if session["stage"] not in ("staging",):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot cancel a committed staging session "
                    f"(stage={session['stage']})"
                ),
            )
        staging_dir = session.get("staging_dir")
        if staging_dir and os.path.isdir(staging_dir):
            try:
                shutil.rmtree(staging_dir)
            except OSError as exc:
                logger.warning(
                    "Staging %s: failed to remove staging dir %s on cancel: %s",
                    staging_id,
                    staging_dir,
                    exc,
                )
        session["stage"] = "cancelled"
        session["last_update_epoch_ms"] = int(time.time() * 1000)
        server.staging_sessions.pop(staging_id, None)
        logger.info("Staging session cancelled: staging_id=%s", staging_id)
        return {
            "staging_id": staging_id,
            "stage": "cancelled",
            "staged": len(session.get("staged_files") or []),
            "total": len(session.get("staged_files") or []),
            "processed": 0,
        }

    @router.get(
        "/pictures/import/staging/{staging_id}/status",
        summary="Get async import staging status",
        description=(
            "Returns the stage and live progress of a staging session and its "
            "background import task."
        ),
        response_model=StagingStatusResponse,
    )
    def staging_status(request: Request, staging_id: str):
        session = _get_session_or_404(request, staging_id)
        staged_files: list[dict] = session.get("staged_files") or []
        task = session.get("task")
        stage = session["stage"]
        processed = 0
        total = len(staged_files)
        imported_count = duplicate_count = failed_count = None
        scrapheaped_count = cancelled_count = None
        scrapheaped_picture_ids: list[int] = []
        error = session.get("error")

        if task is not None:
            total = int(getattr(task, "_total_count", total) or total)
            processed = int(getattr(task, "_processed_count", 0) or 0)
            status = getattr(task, "status", None)
            status_value = getattr(status, "value", status)
            if status_value == "completed":
                stage = "completed"
                result = task.result if isinstance(task.result, dict) else {}
                imported_count = result.get("imported_count")
                duplicate_count = result.get("duplicate_count")
                scrapheaped_count = result.get("scrapheaped_count")
                scrapheaped_picture_ids = result.get("scrapheaped_picture_ids") or []
                failed_count = result.get("failed_count")
                cancelled_count = result.get("cancelled_count")
                # Terminal: drop the retained task/dir reference so the session
                # record no longer pins the finished task object.
                session["stage"] = "completed"
            elif status_value == "failed":
                stage = "failed"
                error = str(getattr(task, "error", None) or "Import failed")
                session["stage"] = "failed"
                session["error"] = error
            elif status_value == "cancelled":
                stage = "cancelled"
                session["stage"] = "cancelled"
            else:
                stage = "importing"
            if status_value in ("completed", "failed", "cancelled"):
                # Keep the reaper's terminal grace window counting from the last
                # poll, so an actively-polled finished session survives and a
                # forgotten one ages out.
                session["last_update_epoch_ms"] = int(time.time() * 1000)

        return {
            "staging_id": staging_id,
            "stage": stage,
            "staged": len(staged_files),
            "total": total,
            "processed": processed,
            "task_id": session.get("task_id"),
            "imported_count": imported_count,
            "duplicate_count": duplicate_count,
            "scrapheaped_count": scrapheaped_count,
            "scrapheaped_picture_ids": scrapheaped_picture_ids,
            "failed_count": failed_count,
            "cancelled_count": cancelled_count,
            "error": error,
        }
