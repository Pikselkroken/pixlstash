import time
import os
import threading

import numpy as np
from sqlmodel import Session, select
from sqlalchemy import func

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, Quality
from pixlstash.utils.quality.quality_utils import QualityUtils
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask


logger = get_logger(__name__)


class QualityTask(BaseTask):
    """Task that calculates full-image quality metrics for one batch.

    Two tasks may be in-flight simultaneously (ping-pong I/O prefetch), but
    only one may execute compute at a time.  While task N runs compute, task
    N+1 preloads its images from disk in a background thread so that it is
    ready to compute the moment the semaphore is released.
    """

    BATCH_SIZE = 64
    FULL_IMAGE_MAX_SIDE = 512

    # Only one QualityTask may run compute at a time.  A second task may be
    # in-flight and preloading images concurrently (ping-pong pattern).
    _execution_semaphore = threading.Semaphore(1)

    def __init__(self, database, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="QualityTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._pictures = pictures or []
        self._preloaded_images: dict[str, np.ndarray | None] = {}
        self._preload_lock = threading.Lock()
        self._preload_thread: threading.Thread | None = None
        self._preload_started_at: float | None = None
        self._preload_finished_at: float | None = None

    def on_queued(self) -> None:
        """Start background I/O preload as soon as the task is queued."""
        if self._preload_thread is not None and self._preload_thread.is_alive():
            return
        self._preload_started_at = time.perf_counter()
        self._preload_thread = threading.Thread(
            target=self._preload_images,
            name=f"QualityTaskPreload-{self.id[:8]}",
            daemon=True,
        )
        self._preload_thread.start()

    def _preload_images(self) -> None:
        """Load every image in the batch from disk into memory (background thread)."""
        preloaded: dict[str, np.ndarray | None] = {}
        for pic in self._pictures:
            file_path = None
            try:
                file_path = str(
                    ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
                )
                img = ImageUtils.load_image_or_video(file_path)
                preloaded[file_path] = img
            except Exception as exc:
                logger.debug(
                    "Preload failed for %s: %s",
                    getattr(pic, "file_path", None),
                    exc,
                )
                if file_path is not None:
                    preloaded[file_path] = None
        with self._preload_lock:
            self._preloaded_images = preloaded
        self._preload_finished_at = time.perf_counter()
        started_at = self._preload_started_at
        if started_at is not None:
            logger.debug(
                "[QUALITY_PRELOAD] task_id=%s status=ready preloaded=%s preload_s=%.3f",
                self.id,
                len(preloaded),
                self._preload_finished_at - started_at,
            )

    def _wait_for_preload(self) -> dict[str, np.ndarray | None]:
        """Block until the preload thread finishes and return the image cache."""
        if self._preload_thread is not None:
            self._preload_thread.join()
        with self._preload_lock:
            return dict(self._preloaded_images)

    def _run_task(self):
        # Acquire the class-level semaphore so that only one QualityTask runs
        # compute at a time.  The second in-flight task has been preloading
        # images while this one ran, so it will be ready immediately after the
        # semaphore is released (ping-pong I/O pattern).
        self._execution_semaphore.acquire()
        try:
            return self._compute()
        finally:
            self._execution_semaphore.release()

    def _compute(self):
        start = time.time()
        quality_helper = QualityUtils(self._db)

        pics = self._pictures
        if not pics:
            return {"changed_count": 0, "changed": []}

        self._backfill_missing_picture_metadata(pics)

        # By the time we reach here the preload thread has had the full
        # duration of the previous task's compute to finish I/O.  join() will
        # return immediately in the common case.
        preloaded = self._wait_for_preload()

        grouped_full = quality_helper.group_pictures_by_format_and_size(pics)
        if not grouped_full:
            return {"changed_count": 0, "changed": []}

        changed = []
        for group_key, group in grouped_full.items():
            batch = group[: min(len(group), self.BATCH_SIZE)]
            expected_shape = (group_key[2], group_key[1], 3)
            valid_batch = []
            valid_loaded = []
            skipped = []

            for pic in batch:
                file_path = str(
                    ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
                )
                img = preloaded.get(file_path)
                if img is None:
                    # Fallback: not preloaded (added after on_queued, or preload failed).
                    img = ImageUtils.load_image_or_video(file_path)
                if img is None:
                    skipped.append(pic)
                    continue
                if img.shape == expected_shape:
                    valid_batch.append(pic)
                    valid_loaded.append(img)
                else:
                    skipped.append(pic)

            if valid_batch:
                qualities = quality_helper.calculate_quality(
                    valid_batch,
                    valid_loaded,
                    max_side=self.FULL_IMAGE_MAX_SIDE,
                )
                if qualities:
                    result = self._db.run_task(
                        quality_helper.update_quality,
                        valid_batch,
                        qualities,
                        priority=DBPriority.LOW,
                    )
                    changed.extend(result or [])

            if skipped:
                sentinel_qualities = [
                    Quality(
                        sharpness=-1.0,
                        edge_density=-1.0,
                        contrast=-1.0,
                        brightness=-1.0,
                        noise_level=-1.0,
                        colorfulness=-1.0,
                        luminance_entropy=-1.0,
                        dominant_hue=-1.0,
                        text_score=-1.0,
                        color_histogram=None,
                    )
                    for _ in skipped
                ]
                result = self._db.run_task(
                    quality_helper.update_quality,
                    skipped,
                    sentinel_qualities,
                    priority=DBPriority.LOW,
                )
                changed.extend(result or [])

        logger.debug(
            "QualityTask completed in %.2fs with %s updates",
            time.time() - start,
            len(changed),
        )
        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    @staticmethod
    def _find_pictures_missing_quality(session: Session, limit: int):
        return session.exec(
            select(Picture)
            .outerjoin(
                Quality,
                (Quality.picture_id == Picture.id) & (Quality.face_id.is_(None)),
            )
            .where(Quality.text_score.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
            .order_by(Picture.format, Picture.width, Picture.height)
            .limit(limit)
        ).all()

    @staticmethod
    def count_missing_quality(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .outerjoin(
                Quality,
                (Quality.picture_id == Picture.id) & (Quality.face_id.is_(None)),
            )
            .where(Quality.text_score.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    def _backfill_missing_picture_metadata(self, pictures: list[Picture]) -> None:
        to_update = []
        for pic in pictures:
            if (
                pic.format is not None
                and pic.width is not None
                and pic.height is not None
            ):
                continue

            file_path = ImageUtils.resolve_picture_path(
                self._db.image_root, pic.file_path
            )
            img = ImageUtils.load_image_or_video(file_path)
            if img is None:
                raise ValueError(
                    f"Cannot infer metadata for picture id={pic.id} path={pic.file_path}: file could not be loaded"
                )

            height, width = img.shape[:2]
            ext = os.path.splitext(pic.file_path or "")[1].lstrip(".").upper()
            fmt = pic.format if pic.format is not None else (ext or None)
            if fmt is None:
                raise ValueError(
                    f"Cannot infer format for picture id={pic.id} path={pic.file_path}: missing extension and format"
                )

            pic.format = fmt
            pic.width = int(width)
            pic.height = int(height)
            to_update.append((int(pic.id), fmt, int(width), int(height)))

        if not to_update:
            return

        def persist_metadata(
            session: Session, updates: list[tuple[int, str, int, int]]
        ):
            for pic_id, fmt, width, height in updates:
                db_pic = session.get(Picture, pic_id)
                if db_pic is None:
                    continue
                db_pic.format = fmt
                db_pic.width = width
                db_pic.height = height
                session.add(db_pic)
            session.commit()

        self._db.run_task(persist_metadata, to_update)
