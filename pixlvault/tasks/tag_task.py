from sqlmodel import Session, select, delete
import os
import threading
import time

from PIL import Image as PILImage

from pixlvault.database import DBPriority
from pixlvault.db_models import Face, Picture, Tag
from pixlvault.utils.image_processing.image_utils import ImageUtils
from pixlvault.utils.image_processing.video_utils import VideoUtils
from pixlvault.picture_tagger import PictureTagger, QUALITY_CROP_TAG_WHITELIST
from pixlvault.pixl_logging import get_logger
from pixlvault.tasks.base_task import BaseTask


logger = get_logger(__name__)


class TagTask(BaseTask):
    """Task that tags a batch of pictures and persists tag updates."""

    CPU_SPILLOVER_REUSE_GRACE_S = 8.0
    _cpu_spillover_tagger: PictureTagger | None = None
    _cpu_spillover_last_used_at: float = 0.0
    _cpu_spillover_lock = threading.Lock()

    def __init__(
        self,
        database,
        picture_tagger,
        pictures: list,
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
        self._picture_tagger = picture_tagger
        self._pictures = pictures or []
        self._preloaded_images: dict[str, PILImage.Image] = {}
        self._preload_lock = threading.Lock()
        self._preload_thread: threading.Thread | None = None
        self._preload_started_at: float | None = None
        self._preload_finished_at: float | None = None
        self._cpu_spillover_enabled = False

    def on_queued(self) -> None:
        if self._preload_thread is not None and self._preload_thread.is_alive():
            return
        self._preload_started_at = time.perf_counter()
        self._preload_finished_at = None
        self._preload_thread = threading.Thread(
            target=self._preload_images,
            name=f"TagTaskPreload-{self.id[:8]}",
            daemon=True,
        )
        self._preload_thread.start()

    def _preload_images(self) -> None:
        preloaded = {}
        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        for pic in self._pictures:
            try:
                file_path = ImageUtils.resolve_picture_path(
                    self._db.image_root, pic.file_path
                )
                ext = os.path.splitext(str(file_path))[1].lower()
                if ext in video_exts:
                    frames = VideoUtils.extract_representative_video_frames(
                        str(file_path),
                        count=1,
                    )
                    if not frames:
                        continue
                    preloaded[file_path] = frames[0].convert("RGB")
                    continue
                preloaded[file_path] = PILImage.open(file_path).convert("RGB")
            except Exception as exc:
                logger.debug(
                    "Preload failed for %s: %s", getattr(pic, "file_path", None), exc
                )
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

        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    def estimated_vram_mb(self) -> int:
        incremental_estimate_fn = getattr(
            self._picture_tagger,
            "estimate_task_incremental_vram_mb",
            None,
        )
        if callable(incremental_estimate_fn):
            try:
                return max(0, int(incremental_estimate_fn(len(self._pictures))))
            except Exception:
                return 0
        estimate_fn = getattr(self._picture_tagger, "estimate_task_vram_mb", None)
        if callable(estimate_fn):
            try:
                return max(0, int(estimate_fn(len(self._pictures))))
            except Exception:
                return 0
        return 0

    def allow_cpu_spillover(self) -> bool:
        return True

    def enable_cpu_spillover(self) -> None:
        self._cpu_spillover_enabled = True

    @classmethod
    def _acquire_cpu_spillover_tagger(cls, image_root: str):
        with cls._cpu_spillover_lock:
            if cls._cpu_spillover_tagger is None:
                cls._cpu_spillover_tagger = PictureTagger(
                    silent=True,
                    device="cpu",
                    image_root=image_root,
                )
            cls._cpu_spillover_last_used_at = time.perf_counter()
            return cls._cpu_spillover_tagger

    @classmethod
    def _release_idle_cpu_spillover_tagger(cls, force: bool = False) -> None:
        with cls._cpu_spillover_lock:
            tagger = cls._cpu_spillover_tagger
            if tagger is None:
                return
            if not force:
                idle_s = time.perf_counter() - cls._cpu_spillover_last_used_at
                if idle_s < cls.CPU_SPILLOVER_REUSE_GRACE_S:
                    return
            cls._cpu_spillover_tagger = None
        try:
            tagger.close()
        except Exception as exc:
            logger.debug("CPU spillover tagger close failed: %s", exc)

    @staticmethod
    def _add_tags_bulk(session: Session, updates: list[dict]):
        updated_ids = []
        for update in updates:
            pic_id = update.get("pic_id")
            if pic_id is None:
                continue
            tags = update.get("tags") or []

            existing_tags = session.exec(
                select(Tag.tag).where(Tag.picture_id == pic_id)
            ).all()
            existing_tag_set = {
                row[0] if isinstance(row, tuple) else row
                for row in existing_tags
                if row
            }
            if set(tags) == existing_tag_set:
                continue

            session.exec(delete(Tag).where(Tag.picture_id == pic_id))

            for tag_value in set(tags):
                session.add(Tag(picture_id=pic_id, tag=tag_value))

            updated_ids.append(pic_id)

        session.commit()
        return updated_ids

    @staticmethod
    def _fetch_faces_for_pictures(session: Session, picture_ids: list) -> dict:
        faces = session.exec(select(Face).where(Face.picture_id.in_(picture_ids))).all()
        result = {}
        for face in faces:
            result.setdefault(face.picture_id, []).append(face)
        return result

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
        self._release_idle_cpu_spillover_tagger(force=False)
        active_tagger = self._picture_tagger
        cpu_spillover_tagger = None
        if self._cpu_spillover_enabled:
            logger.info("TagTask %s using CPU spillover mode", self.id)
            cpu_spillover_tagger = self._acquire_cpu_spillover_tagger(
                self._db.image_root
            )
            active_tagger = cpu_spillover_tagger

        try:
            if image_paths:
                logger.debug("Tagging %s images", len(image_paths))
                logger.debug("Tagging image paths: %s", image_paths)
                tag_results = active_tagger.tag_images(
                    image_paths,
                    preloaded_images=preloaded_images,
                )
                logger.debug("Got tag results for %s images.", len(tag_results))

                # --- Quality crop pass ---
                # Fetch face bboxes and run the custom tagger on expanded crops so
                # that quality tags (e.g. "pixelated") that are invisible at full-
                # image resolution can still be detected.
                try:
                    pic_ids = [p.id for p in batch]
                    faces_by_pic = self._db.run_task(
                        lambda session: self._fetch_faces_for_pictures(
                            session, pic_ids
                        ),
                        priority=DBPriority.LOW,
                    )
                    target = active_tagger.custom_tagger_image_size_quality_crop()
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
                            expanded = PictureTagger._expand_bbox_to_square(
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
                        quality_results = active_tagger.tag_quality_crops(quality_items)
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
                except Exception as exc:
                    logger.warning("Quality crop pass failed: %s", exc)
                # --- end quality crop pass ---

                update_payloads = []
                for path, tags in tag_results.items():
                    pic = pic_by_path.get(path)
                    logger.debug(
                        "Processing tags for image at path: %s: %s", path, tags
                    )
                    if not tags or not pic:
                        continue

                    update_payloads.append(
                        {
                            "pic_id": pic.id,
                            "tags": tags,
                        }
                    )

                if update_payloads:
                    updated_ids = self._db.run_task(
                        self._add_tags_bulk,
                        update_payloads,
                        priority=DBPriority.LOW,
                    )
                    updated_set = set(updated_ids or [])
                    for update in update_payloads:
                        pic_id = update.get("pic_id")
                        if pic_id in updated_set:
                            tagged_pictures.append(
                                (Picture, pic_id, "tags", update.get("tags") or [])
                            )
        finally:
            if cpu_spillover_tagger is not None:
                with self._cpu_spillover_lock:
                    self._cpu_spillover_last_used_at = time.perf_counter()
                self._release_idle_cpu_spillover_tagger(force=False)

        return tagged_pictures
