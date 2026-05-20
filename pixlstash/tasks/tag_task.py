from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from sqlalchemy import func
from sqlmodel import Session, select, delete
import os
import threading
import time
from sqlalchemy.exc import IntegrityError

from PIL import Image as PILImage

from pixlstash.database import DBPriority
from pixlstash.db_models import Face, Picture, Tag, TAG_EMPTY_SENTINEL
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.image_processing.face_utils import expand_bbox_to_square
from pixlstash.utils.service.tag_prediction_utils import _PENALISED_TAG_SET
from pixlstash.inference.workflows.tagging import TaggingWorkflow
from pixlstash.inference.engine import InferenceEngine
from pixlstash.tagger_plugins.pixlstash_tagger import QUALITY_CROP_TAG_WHITELIST
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority


logger = get_logger(__name__)


class TagTask(BaseTask):
    """Task that tags a batch of pictures and persists tag updates."""

    CPU_SPILLOVER_REUSE_GRACE_S = 8.0
    _cpu_spillover_engine: InferenceEngine | None = None
    _cpu_spillover_last_used_at: float = 0.0
    _cpu_spillover_lock = threading.Lock()

    # Tagging is low-priority relative to face extraction.  Uses the shared
    # GPU queue: serialised by the single GPU worker.
    # Face extraction (HIGH) always precedes tagging in the queue.

    def __init__(
        self,
        database,
        tagging_workflow: TaggingWorkflow,
        pictures: list,
        interactive: bool = False,
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="TagTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._tagging_workflow = tagging_workflow
        self._pictures = pictures or []
        self._interactive = interactive
        self._preloaded_images: dict[str, PILImage.Image] = {}
        self._preload_lock = threading.Lock()
        self._preload_thread: threading.Thread | None = None
        self._preload_cancel = threading.Event()
        self._preload_started_at: float | None = None
        self._preload_finished_at: float | None = None
        self._cpu_spillover_enabled = False

    def on_queued(self) -> None:
        if self._preload_thread is not None and self._preload_thread.is_alive():
            return
        self._preload_cancel.clear()
        self._preload_started_at = time.perf_counter()
        self._preload_finished_at = None
        self._preload_thread = threading.Thread(
            target=self._preload_images,
            name=f"TagTaskPreload-{self.id[:8]}",
            daemon=True,
        )
        self._preload_thread.start()

    def on_cancel(self) -> None:
        self._preload_cancel.set()
        if self._preload_thread is None:
            return
        self._preload_thread.join(timeout=10)
        if self._preload_thread.is_alive():
            logger.warning(
                "TagTask preload thread did not stop in time for task %s",
                self.id,
            )

    _PRELOAD_WORKERS = 4

    def _preload_images(self) -> None:
        preloaded = {}
        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}

        def _load_one(pic):
            if self._preload_cancel.is_set():
                return None, None
            try:
                file_path = ImageUtils.resolve_picture_path(
                    self._db.image_root, pic.file_path
                )
                ext = os.path.splitext(str(file_path))[1].lower()
                if ext in video_exts:
                    frames = VideoUtils.extract_representative_video_frames(
                        str(file_path), count=1
                    )
                    if not frames:
                        return None, None
                    return file_path, frames[0].convert("RGB")
                return file_path, PILImage.open(file_path).convert("RGB")
            except Exception as exc:
                logger.debug(
                    "Preload failed for %s: %s", getattr(pic, "file_path", None), exc
                )
                return None, None

        n_workers = min(self._PRELOAD_WORKERS, max(1, len(self._pictures)))
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_load_one, pic): pic for pic in self._pictures}
            for future in as_completed(futures):
                if self._preload_cancel.is_set():
                    break
                file_path, img = future.result()
                if file_path is not None and img is not None:
                    preloaded[file_path] = img
        with self._preload_lock:
            self._preloaded_images = preloaded
        self._preload_finished_at = time.perf_counter()
        started_at = self._preload_started_at
        if started_at is not None:
            logger.debug(
                "[TAG_PRELOAD] task_id=%s status=ready preloaded=%s preload_s=%.3f",
                self.id,
                len(preloaded),
                self._preload_finished_at - started_at,
            )

    def _run_task(self):
        if not self._pictures:
            return {"changed_count": 0, "changed": []}

        changed = self._tag_pictures_batch()
        # Release preloaded PIL Images immediately after inference.  Without
        # this the images stay alive until the task object is garbage collected
        # (one full task cycle later), which can hold several hundred MB of RAM.
        self._preloaded_images = {}
        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    def estimated_vram_mb(self) -> int:
        try:
            return max(
                0,
                self._tagging_workflow.estimated_incremental_vram_mb(
                    len(self._pictures)
                ),
            )
        except Exception:
            return 0

    @property
    def priority(self) -> TaskPriority:
        # Interactive (user-triggered) tasks jump ahead of everything including
        # face extraction.  Background tagging takes priority over embeddings/descriptions.
        return TaskPriority.URGENT if self._interactive else TaskPriority.MEDIUM

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def allow_cpu_spillover(self) -> bool:
        return True

    def enable_cpu_spillover(self) -> None:
        self._cpu_spillover_enabled = True

    @classmethod
    def _acquire_cpu_spillover_engine(cls, image_root: str) -> InferenceEngine:
        with cls._cpu_spillover_lock:
            if cls._cpu_spillover_engine is None:
                cls._cpu_spillover_engine = InferenceEngine.create(
                    device="cpu",
                    image_root=image_root,
                )
            cls._cpu_spillover_last_used_at = time.perf_counter()
            return cls._cpu_spillover_engine

    @classmethod
    def _release_idle_cpu_spillover_engine(cls, force: bool = False) -> None:
        with cls._cpu_spillover_lock:
            engine = cls._cpu_spillover_engine
            if engine is None:
                return
            if not force:
                idle_s = time.perf_counter() - cls._cpu_spillover_last_used_at
                if idle_s < cls.CPU_SPILLOVER_REUSE_GRACE_S:
                    return
            cls._cpu_spillover_engine = None
        try:
            engine.close()
        except Exception as exc:
            logger.debug("CPU spillover engine close failed: %s", exc)

    @staticmethod
    def _add_tags_bulk(session: Session, updates: list[dict]):
        updated_ids = []
        candidate_ids = {
            int(update.get("pic_id"))
            for update in (updates or [])
            if update.get("pic_id") is not None
        }
        if not candidate_ids:
            return updated_ids

        existing_picture_ids = set(
            session.exec(
                select(Picture.id).where(Picture.id.in_(list(candidate_ids)))
            ).all()
        )

        # Bulk-fetch existing tags for all pictures in the batch at once.
        existing_tags_rows = session.exec(
            select(Tag.picture_id, Tag.tag).where(
                Tag.picture_id.in_(list(existing_picture_ids))
            )
        ).all()
        existing_tags_map: dict[int, set] = {}
        for row in existing_tags_rows:
            pid = row[0] if isinstance(row, tuple) else row.picture_id
            tag_val = row[1] if isinstance(row, tuple) else row.tag
            if tag_val is not None:
                existing_tags_map.setdefault(pid, set()).add(tag_val)

        # Determine which pictures need updating and their new effective tags.
        pics_to_update: list[tuple[int, set]] = []
        for update in updates:
            pic_id = update.get("pic_id")
            if pic_id is None:
                continue
            if pic_id not in existing_picture_ids:
                logger.debug("Skipping tag update for missing picture_id=%s", pic_id)
                continue
            tags = update.get("tags") or []

            # When the tagger found no applicable tags, write the empty sentinel
            # so that TagPredictionTask can detect that TagTask has already run.
            effective_tags = set(tags) if tags else {TAG_EMPTY_SENTINEL}

            if effective_tags == existing_tags_map.get(pic_id, set()):
                continue

            pics_to_update.append((pic_id, effective_tags))

        if not pics_to_update:
            return updated_ids

        # Bulk delete old tags and insert new ones in a single transaction.
        update_pic_ids = [pid for pid, _ in pics_to_update]
        try:
            session.exec(delete(Tag).where(Tag.picture_id.in_(update_pic_ids)))
            for pic_id, effective_tags in pics_to_update:
                for tag_value in effective_tags:
                    session.add(Tag(picture_id=pic_id, tag=tag_value))
            session.commit()
            updated_ids.extend(update_pic_ids)
        except IntegrityError as exc:
            session.rollback()
            logger.warning(
                "Bulk tag write failed for %d pictures, falling back to per-picture: %s",
                len(update_pic_ids),
                exc,
            )
            for pic_id, effective_tags in pics_to_update:
                try:
                    session.exec(delete(Tag).where(Tag.picture_id == pic_id))
                    for tag_value in effective_tags:
                        session.add(Tag(picture_id=pic_id, tag=tag_value))
                    session.commit()
                    updated_ids.append(pic_id)
                except IntegrityError as inner_exc:
                    session.rollback()
                    logger.warning(
                        "Skipping tag update for picture_id=%s due to concurrent delete or FK constraint: %s",
                        pic_id,
                        inner_exc,
                    )

        return updated_ids

    @staticmethod
    def _fetch_faces_for_pictures(session: Session, picture_ids: list) -> dict:
        faces = session.exec(select(Face).where(Face.picture_id.in_(picture_ids))).all()
        result = {}
        for face in faces:
            result.setdefault(face.picture_id, []).append(face)
        return result

    @staticmethod
    def _resolve_pending_predictions(session: Session, picture_ids: list) -> None:
        """Flip any PENDING tag predictions to CONFIRMED or REJECTED based on
        the tags that TagTask wrote for these pictures.
        Also reconciles CONFIRMED/REJECTED rows whose status no longer matches
        the current Tag table (e.g. TagPredictionTask ran before TagTask)."""
        if not picture_ids:
            return
        for picture_id in picture_ids:
            applied_tags = {
                row[0] if isinstance(row, tuple) else row
                for row in session.exec(
                    select(Tag.tag).where(
                        Tag.picture_id == picture_id,
                        Tag.tag.is_not(None),
                        Tag.tag != TAG_EMPTY_SENTINEL,
                    )
                ).all()
            }
            all_preds = session.exec(
                select(TagPrediction).where(
                    TagPrediction.picture_id == picture_id,
                    TagPrediction.status.in_(["PENDING", "CONFIRMED", "REJECTED"]),
                )
            ).all()
            for pred in all_preds:
                correct_status = "CONFIRMED" if pred.tag in applied_tags else "REJECTED"
                if pred.status != correct_status:
                    pred.status = correct_status
        session.commit()

    def _tag_pictures_batch(self) -> list:
        assert self._pictures is not None

        if self._preload_thread is None:
            self.on_queued()

        task_start_at = time.perf_counter()
        preload_started_at = self._preload_started_at
        preload_headstart_s = (
            max(0.0, task_start_at - preload_started_at)
            if preload_started_at is not None
            else 0.0
        )

        preload_wait_start = time.perf_counter()
        if self._preload_thread is not None:
            self._preload_thread.join()
        preload_wait_s = time.perf_counter() - preload_wait_start

        preload_finished_at = self._preload_finished_at
        preload_remaining_at_start_s = (
            max(0.0, preload_finished_at - task_start_at)
            if preload_finished_at is not None
            else preload_wait_s
        )

        with self._preload_lock:
            preloaded_images = dict(self._preloaded_images)

        logger.debug(
            "[TAG_PRELOAD] task_id=%s headstart_s=%.3f wait_block_s=%.3f "
            "remaining_at_start_s=%.3f preloaded=%s",
            self.id,
            preload_headstart_s,
            preload_wait_s,
            preload_remaining_at_start_s,
            len(preloaded_images),
        )

        batch = self._pictures
        image_paths = []
        pic_by_path = {}
        for pic in batch:
            file_path = ImageUtils.resolve_picture_path(
                self._db.image_root, pic.file_path
            )
            image_paths.append(file_path)
            pic_by_path[file_path] = pic

        tagged_pictures = []
        self._release_idle_cpu_spillover_engine(force=False)
        active_workflow: TaggingWorkflow = self._tagging_workflow
        cpu_spillover_engine = None
        if self._cpu_spillover_enabled:
            logger.debug("TagTask %s using CPU spillover mode", self.id)
            cpu_spillover_engine = self._acquire_cpu_spillover_engine(
                self._db.image_root
            )
            active_workflow = cpu_spillover_engine.tagging_workflow

        try:
            if image_paths:
                logger.debug("Tagging %s images", len(image_paths))
                logger.debug("Tagging image paths: %s", image_paths)
                # Collect raw confidence scores in the same GPU pass as tagging.
                full_scores_by_path: dict = {}
                use_pixlstash_tagger = active_workflow.is_pixlstash_tagger_enabled
                inference_start = time.perf_counter()
                tag_results = active_workflow.tag_images(
                    image_paths,
                    preloaded_images=preloaded_images,
                    out_raw_pixlstash_scores=full_scores_by_path if use_pixlstash_tagger else None,
                )
                inference_s = time.perf_counter() - inference_start
                logger.debug("Got tag results for %s images.", len(tag_results))

                # --- Quality crop pass ---
                # Fetch face bboxes and run the custom tagger on expanded crops so
                # that quality tags (e.g. "pixelated") that are invisible at full-
                # image resolution can still be detected.
                crop_inference_s = 0.0
                crop_fetch_s = 0.0
                try:
                    crop_fetch_start = time.perf_counter()
                    pic_ids = [p.id for p in batch]
                    faces_by_pic = self._db.run_immediate_read_task(
                        lambda session: self._fetch_faces_for_pictures(
                            session, pic_ids
                        ),
                    )
                    crop_fetch_s = time.perf_counter() - crop_fetch_start
                    target = active_workflow.pixlstash_tagger_image_size_quality_crop()
                    quality_items = []
                    key_to_path = {}
                    for pic in batch:
                        file_path = ImageUtils.resolve_picture_path(
                            self._db.image_root, pic.file_path
                        )
                        faces = faces_by_pic.get(pic.id, [])
                        valid_faces = [
                            face
                            for face in faces
                            if face.bbox and getattr(face, "face_index", 0) >= 0
                        ]
                        if not valid_faces:
                            continue
                        try:
                            img = preloaded_images.get(file_path)
                            if img is None:
                                ext = os.path.splitext(str(file_path))[1].lower()
                                if ext in {
                                    ".mp4",
                                    ".avi",
                                    ".mov",
                                    ".mkv",
                                    ".webm",
                                    ".flv",
                                    ".wmv",
                                }:
                                    frames = (
                                        VideoUtils.extract_representative_video_frames(
                                            str(file_path),
                                            count=1,
                                        )
                                    )
                                    if not frames:
                                        continue
                                    img = frames[0].convert("RGB")
                                else:
                                    img = PILImage.open(file_path).convert("RGB")
                                preloaded_images[file_path] = img
                            w, h = img.size
                            largest_face = max(
                                valid_faces,
                                key=lambda face: max(
                                    0,
                                    (float(face.bbox[2]) - float(face.bbox[0]))
                                    * (float(face.bbox[3]) - float(face.bbox[1])),
                                ),
                            )
                            expanded = expand_bbox_to_square(
                                largest_face.bbox, w, h, target
                            )
                            crop = img.crop(expanded)
                            key = f"{file_path}#face{largest_face.id}"
                            quality_items.append((key, crop))
                            key_to_path[key] = file_path
                        except Exception as exc:
                            logger.warning(
                                "Could not load %s for quality crop pass: %s",
                                file_path,
                                exc,
                            )
                    if quality_items:
                        # Single GPU pass: get quality tags AND raw scores for predictions.
                        crop_raw_scores: dict = {}
                        crop_inf_start = time.perf_counter()
                        quality_results = active_workflow.tag_quality_crops(
                            quality_items,
                            out_raw_scores=crop_raw_scores if use_pixlstash_tagger else None,
                        )
                        crop_inference_s = time.perf_counter() - crop_inf_start
                        # Accumulate quality tags found across all crops per picture path.
                        quality_tags_by_path = {}
                        for key, quality_tags in quality_results.items():
                            path = key_to_path.get(key)
                            if path:
                                quality_tags_by_path.setdefault(path, set()).update(
                                    quality_tags
                                )
                        # Crops are ground truth for whitelist tags: strip any whitelist
                        # tags the full-image pass may have produced, then add only what
                        # the crops confirmed.  Only applies to pictures that had at least
                        # one crop — pictures without faces are left untouched.
                        for path, crop_quality in quality_tags_by_path.items():
                            if path not in tag_results:
                                continue
                            stripped = [
                                t
                                for t in tag_results[path]
                                if t not in QUALITY_CROP_TAG_WHITELIST
                            ]
                            tag_results[path] = stripped + list(crop_quality)
                            if crop_quality:
                                logger.debug(
                                    "Quality crop tags for %s: %s", path, crop_quality
                                )
                        # Boost whitelist-tag prediction scores using crop confidence.
                        if full_scores_by_path and crop_raw_scores:
                            for key, tag_scores in crop_raw_scores.items():
                                path = key_to_path.get(key)
                                if path is None:
                                    continue
                                merged = full_scores_by_path.setdefault(path, {})
                                for tag, conf in tag_scores.items():
                                    if tag not in QUALITY_CROP_TAG_WHITELIST:
                                        continue
                                    if conf > merged.get(tag, 0.0):
                                        merged[tag] = conf
                except Exception as exc:
                    logger.warning("Quality crop pass failed: %s", exc)
                # --- end quality crop pass ---

                update_payloads = []
                for path, tags in tag_results.items():
                    pic = pic_by_path.get(path)
                    if not pic:
                        continue
                    logger.debug(
                        "Processing tags for image at path: %s: %s", path, tags
                    )
                    update_payloads.append(
                        {
                            "pic_id": pic.id,
                            "tags": tags or [],
                        }
                    )

                if update_payloads:
                    db_tags_start = time.perf_counter()
                    updated_ids = self._db.run_task(
                        self._add_tags_bulk,
                        update_payloads,
                        priority=DBPriority.LOW,
                    )
                    db_tags_s = time.perf_counter() - db_tags_start
                    updated_set = set(updated_ids or [])
                    for update in update_payloads:
                        pic_id = update.get("pic_id")
                        if pic_id in updated_set:
                            tagged_pictures.append(
                                (Picture, pic_id, "tags", update.get("tags") or [])
                            )

                    # Flip any PENDING predictions to CONFIRMED/REJECTED now that
                    # TagTask has made its decision for all processed pictures.
                    all_pic_ids = [u["pic_id"] for u in update_payloads]
                    db_resolve_start = time.perf_counter()
                    self._db.run_task(
                        self._resolve_pending_predictions,
                        all_pic_ids,
                        priority=DBPriority.LOW,
                    )
                    db_resolve_s = time.perf_counter() - db_resolve_start

                    # Write TagPrediction rows for this batch alongside the tags.
                    db_predictions_s = 0.0
                    if full_scores_by_path:
                        label_scores_by_pic_id: dict = {}
                        for path, scores in full_scores_by_path.items():
                            pic = pic_by_path.get(path)
                            if pic is not None and scores:
                                label_scores_by_pic_id[pic.id] = scores
                        if label_scores_by_pic_id:
                            tags_by_pic_id = {
                                u["pic_id"]: set(u.get("tags") or [])
                                for u in update_payloads
                            }
                            model_version = "unknown"
                            try:
                                version_fn = getattr(
                                    active_workflow._engine, "pixlstash_tagger_version", None
                                )
                                if callable(version_fn):
                                    model_version = f"v{version_fn()}"
                            except Exception:
                                logger.warning(
                                    "pixlstash_tagger_version() failed, using 'unknown' model version",
                                    exc_info=True,
                                )
                            db_pred_start = time.perf_counter()
                            self._db.run_task(
                                self._write_predictions_from_tags,
                                label_scores_by_pic_id,
                                tags_by_pic_id,
                                model_version,
                                priority=DBPriority.LOW,
                            )
                            db_predictions_s = time.perf_counter() - db_pred_start

                    n = len(update_payloads)
                    total_s = time.perf_counter() - task_start_at
                    gpu_s = inference_s + crop_inference_s
                    gpu_throughput = n / gpu_s if gpu_s > 0 else 0.0
                    wall_throughput = n / total_s if total_s > 0 else 0.0
                    logger.info(
                        "[TAG_TIMING] task_id=%s n=%d "
                        "preload_wait_s=%.3f inference_s=%.3f "
                        "crop_fetch_s=%.3f crop_inference_s=%.3f "
                        "db_tags_s=%.3f db_resolve_s=%.3f db_pred_s=%.3f "
                        "total_s=%.3f gpu_throughput=%.1f/s wall_throughput=%.1f/s",
                        self.id,
                        n,
                        preload_wait_s,
                        inference_s,
                        crop_fetch_s,
                        crop_inference_s,
                        db_tags_s,
                        db_resolve_s,
                        db_predictions_s,
                        total_s,
                        gpu_throughput,
                        wall_throughput,
                    )
        finally:
            if cpu_spillover_engine is not None:
                with self._cpu_spillover_lock:
                    self._cpu_spillover_last_used_at = time.perf_counter()
                self._release_idle_cpu_spillover_engine(force=False)

        return tagged_pictures

    @staticmethod
    def _write_predictions_from_tags(
        session: Session,
        label_scores_by_pic_id: dict,
        tags_by_pic_id: dict,
        model_version: str,
    ) -> int:
        """Persist raw confidence scores to TagPrediction alongside tag writes.

        Called from _tag_pictures_batch so CONFIRMED/REJECTED status is resolved
        immediately, without needing a separate TagPredictionTask pass.

        Uses bulk queries to avoid per-row SELECTs across the batch.

        Args:
            session: Database session.
            label_scores_by_pic_id: Mapping of picture_id to {tag: confidence}.
            tags_by_pic_id: Mapping of picture_id to the set of applied tag strings.
            model_version: Custom tagger model version string (e.g. "v42").

        Returns:
            Number of TagPrediction rows written or updated.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        picture_ids = [pid for pid, scores in label_scores_by_pic_id.items() if scores]
        if not picture_ids:
            return 0

        # Filter to pictures that still exist — a reference folder removal can
        # delete pictures while a tag task is already in flight, causing FK
        # violations when TagPrediction rows are flushed for a gone picture.
        existing_picture_ids: set[int] = set(
            session.exec(select(Picture.id).where(Picture.id.in_(picture_ids))).all()
        )
        picture_ids = [pid for pid in picture_ids if pid in existing_picture_ids]
        if not picture_ids:
            return 0
        label_scores_by_pic_id = {
            pid: scores
            for pid, scores in label_scores_by_pic_id.items()
            if pid in existing_picture_ids
        }
        tags_by_pic_id = {
            pid: tags
            for pid, tags in tags_by_pic_id.items()
            if pid in existing_picture_ids
        }

        # --- Single bulk fetch of all existing TagPrediction rows for the batch ---
        existing_rows = session.exec(
            select(TagPrediction).where(TagPrediction.picture_id.in_(picture_ids))
        ).all()
        existing_map: dict[tuple[int, str], TagPrediction] = {
            (row.picture_id, row.tag): row for row in existing_rows
        }

        # --- Bulk delete stale model-version rows ---
        stale_ids = [
            row.id
            for row in existing_rows
            if row.model_version != model_version and row.model_version != "manual"
        ]
        if stale_ids:
            session.exec(delete(TagPrediction).where(TagPrediction.id.in_(stale_ids)))
            # Remove from map so they are not treated as existing below.
            for row in existing_rows:
                if row.id in set(stale_ids):
                    existing_map.pop((row.picture_id, row.tag), None)

        # --- Bulk fetch applied tags for anomaly uncertainty computation ---
        tag_rows = session.exec(
            select(Tag.picture_id, Tag.tag).where(
                Tag.picture_id.in_(picture_ids),
                Tag.tag.is_not(None),
                Tag.tag != TAG_EMPTY_SENTINEL,
            )
        ).all()
        applied_tags_by_pic: dict[int, set[str]] = {}
        for pid, tag in tag_rows:
            applied_tags_by_pic.setdefault(pid, set()).add(tag)

        written = 0
        for picture_id, label_scores in label_scores_by_pic_id.items():
            if not label_scores:
                continue
            applied_tags = tags_by_pic_id.get(picture_id, set())

            for tag, confidence in label_scores.items():
                status = "CONFIRMED" if tag in applied_tags else "REJECTED"
                existing = existing_map.get((picture_id, tag))
                if existing is None:
                    session.add(
                        TagPrediction(
                            picture_id=picture_id,
                            tag=tag,
                            confidence=confidence,
                            model_version=model_version,
                            status=status,
                            predicted_at=now,
                        )
                    )
                    written += 1
                elif existing.status != status or existing.confidence != confidence:
                    existing.confidence = confidence
                    existing.model_version = model_version
                    existing.status = status
                    existing.predicted_at = now
                    written += 1

            # Ensure every applied tag has a prediction row even if the model
            # didn't score it (confidence=0.0 so the UI can still show a tooltip
            # for manually-added or low-scoring tags).
            label_score_tags = set(label_scores.keys())
            for tag in applied_tags:
                if tag in label_score_tags:
                    continue
                existing = existing_map.get((picture_id, tag))
                if existing is None:
                    session.add(
                        TagPrediction(
                            picture_id=picture_id,
                            tag=tag,
                            confidence=0.0,
                            model_version=model_version,
                            status="CONFIRMED",
                            predicted_at=now,
                        )
                    )
                    written += 1

            # Compute tag_uncertainty from model confidences.
            confs = list(label_scores.values())
            uncertainty = float(max(min(c, 1.0 - c) for c in confs)) if confs else 0.0
            pic = session.get(Picture, picture_id)
            if pic is not None:
                pic.tag_uncertainty = uncertainty

            # Compute anomaly_tag_uncertainty using the already-fetched applied tags
            # (avoids a redundant SELECT per picture inside recompute_anomaly_tag_uncertainty).
            pic_applied = applied_tags_by_pic.get(picture_id, set())
            anomaly_scores: list[float] = []
            for tag, confidence in label_scores.items():
                if tag is None or tag.strip().lower() not in _PENALISED_TAG_SET:
                    continue
                if tag in pic_applied:
                    anomaly_scores.append(1.0 - float(confidence))
                else:
                    anomaly_scores.append(float(confidence))
            if pic is not None:
                pic.anomaly_tag_uncertainty = (
                    max(anomaly_scores) if anomaly_scores else 0.0
                )

        session.commit()
        return written

    @staticmethod
    def count_missing_tags(session: Session) -> int:
        """Count pictures that have no real tags yet (excluding the empty sentinel)."""
        has_real_tag = (Tag.tag.is_not(None)) & (Tag.tag != TAG_EMPTY_SENTINEL)
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(~Picture.tags.any(has_real_tag))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0
