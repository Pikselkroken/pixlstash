import time
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

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

    Multiple tasks may be in-flight simultaneously.  Each task starts an I/O
    preload thread as soon as it is queued so that image data is ready by the
    time compute begins.
    """

    BATCH_SIZE = 32
    FULL_IMAGE_MAX_SIDE = 512
    # Number of threads used to decode images during preload.
    # PIL's JPEG decoder releases the GIL so threads run concurrently.  With
    # QUALITY_MAX_INFLIGHT=2 only one task preloads at a time, so this means
    # at most _PRELOAD_WORKERS concurrent disk reads — manageable and necessary
    # to decode 512px images fast enough to hide behind the previous task's
    # compute time.  Sequential (1 thread) is too slow for large batches.
    _PRELOAD_WORKERS = 4
    # Ensures only one QualityTask executes _compute() at a time so that
    # numpy/OpenCV operations don't compete for CPU cores.  The second inflight
    # task preloads images while the first computes, then acquires the semaphore
    # immediately (preload_wait ≈ 0) when the first finishes.
    _compute_semaphore = threading.Semaphore(1)

    # Timing feedback shared across all instances so the finder can size the
    # next batch to match the observed preload rate.
    # Written by _compute() under _feedback_lock; read by the finder.
    _feedback_lock = threading.Lock()
    _last_preload_s: float = 0.0  # wall time the preload thread ran
    _last_batch_size: int = 0  # number of pictures in that task

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
        self._preload_cancel = threading.Event()
        self._preload_started_at: float | None = None
        self._preload_finished_at: float | None = None

    def on_queued(self) -> None:
        """Start background I/O preload as soon as the task is queued."""
        if self._preload_thread is not None and self._preload_thread.is_alive():
            return
        self._preload_cancel.clear()
        self._preload_started_at = time.perf_counter()
        self._preload_thread = threading.Thread(
            target=self._preload_images,
            name=f"QualityTaskPreload-{self.id[:8]}",
        )
        self._preload_thread.start()

    def _preload_images(self) -> None:
        """Load every image in the batch from disk into memory (background thread).

        Images are immediately downscaled to FULL_IMAGE_MAX_SIDE during preload
        so that only small (≤512px) arrays are held in memory, never the
        full-resolution originals.  Loading is parallelised across
        _PRELOAD_WORKERS threads: PIL's JPEG decoder releases the GIL so
        multiple images are decoded concurrently on separate CPU cores,
        reducing per-task preload time by up to _PRELOAD_WORKERS×.
        """

        def _load_one(pic):
            if self._preload_cancel.is_set():
                return None, None
            file_path = None
            try:
                file_path = str(
                    ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
                )
                img = ImageUtils.load_image_reduced(file_path, self.FULL_IMAGE_MAX_SIDE)
                return file_path, img
            except Exception as exc:
                logger.debug(
                    "Preload failed for %s: %s",
                    getattr(pic, "file_path", None),
                    exc,
                )
                return file_path, None

        preloaded: dict[str, np.ndarray | None] = {}
        n_workers = min(self._PRELOAD_WORKERS, len(self._pictures))
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_load_one, pic): pic for pic in self._pictures}
            for future in as_completed(futures):
                file_path, img = future.result()
                if file_path is not None:
                    preloaded[file_path] = img
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

    def on_cancel(self) -> None:
        self._preload_cancel.set()
        if self._preload_thread is None:
            return
        self._preload_thread.join(timeout=10)
        if self._preload_thread.is_alive():
            logger.warning(
                "QualityTask preload thread did not stop in time for task %s",
                self.id,
            )

    def _wait_for_preload(self) -> dict[str, np.ndarray | None]:
        """Block until the preload thread finishes and return the image cache."""
        if self._preload_thread is not None:
            self._preload_thread.join()
        with self._preload_lock:
            return dict(self._preloaded_images)

    def _run_task(self):
        # Wait for preload to finish BEFORE acquiring the compute semaphore.
        # This way a slow-preloading task (e.g. large PNGs) does not block
        # a fully-loaded task from computing — whichever task finishes
        # preloading first will win the semaphore and run next.
        self._wait_for_preload()
        QualityTask._compute_semaphore.acquire()
        try:
            return self._compute()
        finally:
            QualityTask._compute_semaphore.release()

    def _compute(self):
        start = time.time()
        quality_helper = QualityUtils(self._db)

        pics = self._pictures
        if not pics:
            return {"changed_count": 0, "changed": []}

        self._backfill_missing_picture_metadata(pics)

        t_preload_wait = time.perf_counter()
        preloaded = self._wait_for_preload()  # instant — already joined in _run_task
        t_preload_done = time.perf_counter()

        # Group by ACTUAL post-downscale shape so that images with different
        # original resolutions that map to the same downscaled shape are batched
        # together.  This avoids the 256-calls-of-1 pattern that occurs with
        # diverse-resolution photo collections.
        shape_groups: dict = defaultdict(list)  # shape → [(pic, img), ...]
        skipped_pics: list = []

        for pic in pics:
            file_path = str(
                ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
            )
            img = preloaded.get(file_path)
            if img is None:
                # Fallback: not preloaded (added after on_queued, or preload failed).
                img = ImageUtils.load_image_reduced(file_path, self.FULL_IMAGE_MAX_SIDE)
            if img is None:
                skipped_pics.append(pic)
            else:
                shape_groups[img.shape].append((pic, img))

        if not shape_groups and not skipped_pics:
            return {"changed_count": 0, "changed": []}

        # Accumulate all (pics, qualities) pairs across every shape-group and
        # sub-batch so the write queue is hit exactly once per task.
        all_write_pics: list = []
        all_write_qualities: list = []

        for shape, pic_img_pairs in shape_groups.items():
            group_pics = [p for p, _ in pic_img_pairs]
            group_imgs = [img for _, img in pic_img_pairs]
            for batch_start in range(0, len(group_pics), self.BATCH_SIZE):
                b_pics = group_pics[batch_start : batch_start + self.BATCH_SIZE]
                b_imgs = group_imgs[batch_start : batch_start + self.BATCH_SIZE]
                qualities = quality_helper.calculate_quality(b_pics, b_imgs)
                if qualities:
                    all_write_pics.extend(b_pics)
                    all_write_qualities.extend(qualities)

        if skipped_pics:
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
                )
                for _ in skipped_pics
            ]
            all_write_pics.extend(skipped_pics)
            all_write_qualities.extend(sentinel_qualities)

        # Single write-queue call for the entire task — one commit, one lock acquisition.
        changed = []
        t_compute_done = time.perf_counter()
        if all_write_pics:
            result = self._db.run_task(
                quality_helper.update_quality,
                all_write_pics,
                all_write_qualities,
                priority=DBPriority.LOW,
            )
            changed.extend(result or [])
        t_write_done = time.perf_counter()

        preload_duration = (
            self._preload_finished_at - self._preload_started_at
            if self._preload_started_at is not None
            and self._preload_finished_at is not None
            else 0.0
        )
        compute_duration = t_compute_done - t_preload_done
        with QualityTask._feedback_lock:
            QualityTask._last_preload_s = preload_duration
            QualityTask._last_batch_size = len(pics)

        logger.info(
            "QualityTask completed in %.2fs — preload_wait=%.3fs compute=%.3fs write=%.3fs updates=%s",
            time.time() - start,
            t_preload_done - t_preload_wait,
            compute_duration,
            t_write_done - t_compute_done,
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
                Quality.picture_id == Picture.id,
            )
            .where(Quality.sharpness.is_(None))
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
                Quality.picture_id == Picture.id,
            )
            .where(Quality.sharpness.is_(None))
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
