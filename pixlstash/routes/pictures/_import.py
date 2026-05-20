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

from ._helpers import (
    _create_picture_imports,
    _normalise_sidecar_stem,
    _parse_sidecar_tags,
)


logger = get_logger(__name__)


def register_routes(router, server):
    @router.post(
        "/pictures/import",
        summary="Import media files",
        description="Starts an asynchronous import of uploaded image/video files (or zip contents) and returns a task id.",
    )
    async def import_pictures(
        file: list[UploadFile] = File(None),
        project_id: int | None = Form(None),
    ):
        _MAX_UPLOAD_BYTES = 20 * 1024**3  # 20 GB per uploaded file / zip
        _MAX_ZIP_ENTRIES = 50_000  # max files inside a zip
        _MAX_ZIP_DECOMPRESSED_BYTES = 50 * 1024**3  # 50 GB total decompressed

        if not server.vault.is_worker_running(TaskType.FACE_EXTRACTION):
            raise HTTPException(
                status_code=400,
                detail="Face worker is not running. Start it before import.",
            )

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

        def run_import_task(server):
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

                shas, existing_map, new_pictures = _create_picture_imports(
                    server,
                    uploaded_files,
                    dest_folder,
                    progress_callback=_on_picture_written,
                )

                # Duplicates are instantly "processed" — credit them now so that
                # the progress bar stays accurate even when most files are dupes.
                duplicate_count_initial = sum(1 for sha in shas if sha in existing_map)
                task["processed"] = len(new_pictures) + duplicate_count_initial
                task["last_update_epoch_ms"] = int(time.time() * 1000)

                _mark_stage(
                    "deduplicated",
                    duplicate_count_initial=duplicate_count_initial,
                    new_count=len(new_pictures),
                )

                logger.debug(
                    f"Importing {len(new_pictures)} new pictures out of {len(uploaded_files)} uploaded."
                )

                if new_pictures:
                    _mark_stage("persisting_new_pictures")

                    def import_task(session):
                        session.add_all(new_pictures)
                        session.commit()
                        for pic in new_pictures:
                            session.refresh(pic)
                        return new_pictures

                    new_pictures = server.vault.db.run_task(import_task)
                    logger.debug(
                        f"Queuing likeness calculation for {len(new_pictures)} new pictures."
                    )
                else:
                    logger.warning("No new pictures to import; all are duplicates.")
                    new_pictures = []

                _mark_stage("building_results")
                results = []
                duplicate_count = 0
                index = 0
                picture_id_sidecar_tags: dict[int, set[str]] = defaultdict(set)
                duplicate_picture_id_set: set[int] = set()
                for stem, _, sha in zip(uploaded_file_stems, uploaded_files, shas):
                    if sha in existing_map:
                        pic = existing_map[sha]
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
                    else:
                        pic = new_pictures[index]
                        results.append(
                            {
                                "status": "success",
                                "picture_id": pic.id,
                                "file": pic.file_path,
                            }
                        )
                        index += 1

                    if (
                        pic.id is not None
                        and stem in sidecar_tags_by_stem
                        and sidecar_tags_by_stem[stem]
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
                server.import_tasks[task_id]["results"] = results
                server.import_tasks[task_id]["processed"] = len(uploaded_files)
                server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                    time.time() * 1000
                )
                # Only apply import context to pictures that were actually
                # touched by this request (one results row per uploaded file).
                all_imported_ids = list(
                    dict.fromkeys(
                        entry.get("picture_id")
                        for entry in results
                        if entry.get("picture_id") is not None
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
                                if TAG_EMPTY_SENTINEL in existing_values:
                                    session.exec(
                                        delete(Tag).where(
                                            Tag.picture_id == pic.id,
                                            Tag.tag == TAG_EMPTY_SENTINEL,
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

                    tagged_ids = server.vault.db.run_task(
                        apply_sidecar_tags,
                        picture_id_sidecar_tags,
                        duplicate_picture_id_set,
                    )
                    if tagged_ids:
                        server.vault.notify(EventType.CHANGED_TAGS)

                if all_imported_ids:
                    _mark_stage("finalizing_import_context")
                    # Queue face extraction asynchronously — do not block on it.
                    for pic in new_pictures:
                        server.vault.get_worker_future(
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

                    imported_ids = server.vault.db.run_task(
                        apply_import_context,
                        all_imported_ids,
                        project_id,
                    )
                    server.import_tasks[task_id]["status"] = "completed"
                    server.import_tasks[task_id]["stage"] = "completed"
                    server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                        time.time() * 1000
                    )
                    server.vault.notify(EventType.CHANGED_PICTURES)
                    if imported_ids:
                        server.vault.notify(
                            EventType.PICTURE_IMPORTED,
                            imported_ids,
                        )
                else:
                    server.import_tasks[task_id]["status"] = "completed"
                    server.import_tasks[task_id]["stage"] = "completed"
                    server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                        time.time() * 1000
                    )
                    server.vault.notify(EventType.CHANGED_PICTURES)
                logger.info("Import task completed: task_id=%s", task_id)
            except Exception as exc:
                server.import_tasks[task_id]["status"] = "failed"
                server.import_tasks[task_id]["stage"] = "failed"
                server.import_tasks[task_id]["error"] = str(exc)
                server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                    time.time() * 1000
                )
                logger.error(f"Import task {task_id} failed: {exc}")

        async def run_import_task_async(server):
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, run_import_task, server)

        # Schedule as an independent asyncio Task, detached from the ASGI
        # request lifecycle.  Using BackgroundTasks would tie the task to the
        # response, preventing uvicorn's h11 handler from accepting the next
        # request on the keep-alive connection until the full 0.7-1 s SHA-
        # hashing pass completes — causing TCP back-pressure that stalls the
        # browser's upload for large multi-batch imports.
        asyncio.create_task(run_import_task_async(server))
        return {"task_id": task_id}

    @router.get(
        "/pictures/import/status",
        summary="Get import job status",
        description="Returns progress and result information for a previously started import task.",
    )
    def import_status(task_id: str):
        task = server.import_tasks.get(task_id)
        if not task:
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
        if task["status"] == "failed":
            payload["error"] = task.get("error")
        return payload

