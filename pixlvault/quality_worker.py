import sqlite3
import os
from typing import List
import cv2
import numpy as np

from time import time

from pixlvault.characters import Characters
from pixlvault.database import DBPriority
from pixlvault.logging import get_logger
import pixlvault.picture_db_tools as db_tools
from pixlvault.picture import PictureModel
from pixlvault.picture_quality import PictureQuality
from pixlvault.picture_tagger import PictureTagger
from pixlvault.picture_utils import PictureUtils
from pixlvault.worker_registry import BaseWorker, WorkerType

logger = get_logger(__name__)


class QualityWorker(BaseWorker):
    def __init__(
        self, db_connection, picture_tagger: PictureTagger, characters: Characters
    ):
        super().__init__(db_connection, picture_tagger, characters)

    def worker_type(self) -> WorkerType:
        return WorkerType.QUALITY

    def _run(self):
        # ...existing code...
        # Efficiently group pictures by size before batch quality calculation
        # (inside the main loop, after fetching pics)
        BATCH_SIZE = 8
        while not self._stop.is_set():
            start = time.time()
            logger.debug("[QUALITY] Starting iteration...")
            quality_updates = 0
            try:
                start_quality_fetch = time.time()
                # 1. Full image quality measures
                logger.debug(
                    "Searching for pictures needing full image quality calculation."
                )
                rows = self._db.execute_read(
                    lambda conn: conn.execute(
                        "SELECT * FROM pictures WHERE sharpness IS NULL OR edge_density IS NULL OR noise_level IS NULL OR contrast IS NULL OR brightness IS NULL",
                    ).fetchall()
                )

                pics_full = db_tools.from_batch_of_db_dicts(rows)

                start_quality_grouping = time.time()
                logger.debug(
                    "[QUALITY] It took %.2f seconds to fetch pictures needing quality calculation."
                    % (start_quality_grouping - start_quality_fetch)
                )
                logger.debug(
                    f"Read metadata for {len(pics_full)} pictures needing full image quality."
                )
                grouped_full = self._group_pictures_by_size(pics_full, region="full")
                logger.debug(
                    f"Grouped {len(grouped_full)} full image batches by size. Will calculate quality now."
                )
                start_quality_calculation = time.time()
                logger.debug(
                    "[QUALITY] It took %.2f seconds to group pictures by size."
                    % (start_quality_calculation - start_quality_grouping)
                )

                for group in grouped_full.values():
                    batch = group[:BATCH_SIZE]
                    if batch:
                        size = PictureUtils.load_metadata(batch[0].file_path)
                        logger.debug(
                            f"Processing batch of {len(batch)} images of size {size} out of a total of {len(group)}."
                        )
                        quality_updates = self._calculate_quality(batch)
                        if quality_updates:
                            self._db.submit_write(
                                self._update_quality,
                                quality_updates,
                                priority=DBPriority.LOW,
                            ).result()
                start_face_quality_fetch = time.time()
                logger.debug(
                    "[QUALITY] It took %.2f seconds to calculate full image quality."
                    % (start_face_quality_fetch - start_quality_calculation)
                )
                # 2. Face quality measures
                logger.debug("Searching for pictures needing face quality calculation.")
                face_rows = self._db.execute_read(
                    lambda conn: conn.execute(
                        "SELECT * FROM pictures WHERE face_sharpness IS NULL OR face_edge_density IS NULL OR face_noise_level IS NULL OR face_contrast IS NULL OR face_brightness IS NULL",
                    ).fetchall()
                )
                pics_face = db_tools.from_batch_of_db_dicts(face_rows)

                logger.debug(
                    "[QUALITY] It took %.2f seconds to fetch pictures needing face quality calculation."
                    % (time.time() - start_face_quality_fetch)
                )
                logger.debug(
                    f"Read metadata for {len(pics_face)} pictures needing face quality."
                )
                grouped_face = self._group_pictures_by_size(pics_face, region="face")
                logger.debug(
                    f"Grouped {len(grouped_face)} face crop batches by bbox size. Will calculate face quality now."
                )
                for group in grouped_face.values():
                    batch = group[:BATCH_SIZE]
                    if batch:
                        bbox_size = None
                        if (
                            batch[0].face_bbox is not None
                            and len(batch[0].face_bbox) == 4
                        ):
                            x1, y1, x2, y2 = batch[0].face_bbox
                            w = int(round((x2 - x1) / 64.0) * 64)
                            h = int(round((y2 - y1) / 64.0) * 64)
                            bbox_size = (h, w, 3)
                        logger.debug(
                            f"Processing face batch of {len(batch)} images of bbox size {bbox_size}."
                        )
                        quality_updates = self._calculate_quality(batch, True)
                        self._db.submit_write(
                            self._update_face_quality,
                            quality_updates,
                            priority=DBPriority.LOW,
                        ).result()

            except (sqlite3.OperationalError, OSError) as e:
                msg = str(e)
                if "no such column" in msg:
                    logger.error(
                        f"Quality worker exiting due to schema error (missing column): {e}"
                    )
                else:
                    logger.error(
                        f"Quality worker exiting due to DB error (likely shutdown): {e}"
                    )
                break
            except Exception as e:
                logger.error(f"Quality worker error: {e}")

            timing = time.time() - start
            if timing > 0.5:
                logger.info("[QUALITY] Done after %.2f seconds." % timing)
            if quality_updates == 0:
                self._stop.wait(QualityWorker.INTERVAL)

    def _group_pictures_by_size(self, pics: List[PictureModel], region: str = "full"):
        """
        Group pictures by region size (full image or face bbox).
        region: "full" for full image, "face" for face bbox.
        Returns a dict: {key: [PictureModel, ...]}, where key is (h, w, c) for images, or (h, w) for face crops, or file_path for videos.
        """
        from collections import defaultdict

        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        sizes = []
        pic_groups = []
        video_groups = defaultdict(list)
        for pic in pics:
            ext = os.path.splitext(pic.file_path)[1].lower()
            if ext in video_exts:
                video_groups[pic.file_path].append(pic)
            else:
                try:
                    if region == "full":
                        size = PictureUtils.load_metadata(pic.file_path)
                    elif (
                        region == "face"
                        and pic.face_bbox is not None
                        and len(pic.face_bbox) == 4
                    ):
                        x1, y1, x2, y2 = pic.face_bbox
                        w = int(round((x2 - x1) / 64.0) * 64)
                        h = int(round((y2 - y1) / 64.0) * 64)
                        # Assume 3 channels for face crops
                        size = (h, w, 3)
                    else:
                        size = None
                    if size:
                        sizes.append(size)
                        pic_groups.append((size, pic))
                except Exception as e:
                    logger.error(
                        f"Failed to read metadata for grouping: {pic.file_path}, error: {e}"
                    )
        all_groups = dict(video_groups)
        if sizes:
            unique_sizes = set(sizes)
            logger.debug(
                f"Read metadata for {len(pic_groups)} images. Found {len(unique_sizes)} unique sizes."
            )
            if len(unique_sizes) == 1:
                batch_size = len(pic_groups)
                logger.debug(
                    f"All images same size: batching {batch_size} images together."
                )
                all_groups[list(unique_sizes)[0]] = [pic for _, pic in pic_groups]
            else:
                image_groups = defaultdict(list)
                for size, pic in pic_groups:
                    image_groups[size].append(pic)
                for size, group in image_groups.items():
                    if region == "face":
                        logger.debug(
                            f"Face batch for bbox size {size}: {len(group)} images."
                        )
                    else:
                        logger.debug(f"Batch for size {size}: {len(group)} images.")
                all_groups.update(image_groups)
        if video_groups:
            for vkey, vgroup in video_groups.items():
                logger.debug(f"Video batch for {vkey}: {len(vgroup)} video(s).")
        return all_groups

    def _calculate_quality(
        self, pics: List[PictureModel], is_face: bool = False
    ) -> List[PictureModel]:
        try:
            images = []
            if not is_face:
                # Full image quality
                for pic in pics:
                    images.append(PictureUtils.load_image_or_video(pic.file_path))
                batch_array = np.stack(images, axis=0)
                qualities = PictureQuality.calculate_quality_batch(batch_array)
                for pic, q in zip(pics, qualities):
                    logger.debug(
                        "[QUALITY] Picture id %s calculated sharpness: %s edge_density: %s contrast: %s brightness: %s noise_level: %s",
                        pic.id,
                        q.sharpness,
                        q.edge_density,
                        q.contrast,
                        q.brightness,
                        q.noise_level,
                    )
                    pic.sharpness = q.sharpness
                    pic.edge_density = q.edge_density
                    pic.contrast = q.contrast
                    pic.brightness = q.brightness
                    pic.noise_level = q.noise_level
            else:
                # Face crop quality
                h, w, _ = None, None, None
                if (
                    pics
                    and pics[0].face_bbox is not None
                    and len(pics[0].face_bbox) == 4
                ):
                    x1, y1, x2, y2 = pics[0].face_bbox
                    w = int(round((x2 - x1) / 64.0) * 64)
                    h = int(round((y2 - y1) / 64.0) * 64)
                valid_images = []
                valid_pics = []
                for pic in pics:
                    # Only check if bbox is present and well-formed
                    if pic.face_bbox is not None and len(pic.face_bbox) == 4:
                        x1, y1, x2, y2 = pic.face_bbox
                        # Check for zero area or malformed bbox
                        if x2 > x1 and y2 > y1:
                            img = PictureUtils.load_image_or_video(pic.file_path)
                            if img is not None:
                                crop = img[int(y1) : int(y2), int(x1) : int(x2)]
                                if (
                                    h is not None
                                    and w is not None
                                    and (crop.shape[0] != h or crop.shape[1] != w)
                                ):
                                    try:
                                        crop = cv2.resize(crop, (w, h))
                                    except Exception:
                                        # Silently drop this bbox and skip picture
                                        pic.face_bbox = None
                                        continue
                                if crop.size > 0:
                                    valid_images.append(crop)
                                    valid_pics.append(pic)
                                    continue
                        # If bbox is present but invalid, set metrics to -1.0
                        pic.face_sharpness = -1.0
                        pic.face_edge_density = -1.0
                        pic.face_noise_level = -1.0
                        pic.face_contrast = -1.0
                        pic.face_brightness = -1.0
                if valid_images:
                    batch_array = np.stack(valid_images, axis=0)
                    qualities = PictureQuality.calculate_quality_batch(batch_array)
                    for pic, fq in zip(valid_pics, qualities):
                        pic.face_sharpness = fq.sharpness
                        pic.face_edge_density = fq.edge_density
                        pic.face_contrast = fq.contrast
                        pic.face_brightness = fq.brightness
                        pic.face_noise_level = fq.noise_level
            return pics
        except Exception as e:
            logger.error(f"Failed to calculate quality for batch: {e}")

    def _update_quality(self, conn, pics):
        cursor = conn.cursor()
        values = []
        for pic in pics:
            # Assert metrics are not bytes
            for metric_name in [
                "sharpness",
                "edge_density",
                "contrast",
                "brightness",
                "noise_level",
            ]:
                metric_value = getattr(pic, metric_name)
                assert not isinstance(metric_value, bytes), (
                    f"Corrupt metric: {metric_name} for picture {pic.id} is bytes: {metric_value!r}"
                )
            logger.debug(
                "[UPDATE] Picture id %s sharpness: %s edge_density: %s contrast: %s brightness: %s noise_level: %s",
                pic.id,
                pic.sharpness,
                pic.edge_density,
                pic.contrast,
                pic.brightness,
                pic.noise_level,
            )
            values.append(
                (
                    pic.sharpness,
                    pic.edge_density,
                    pic.contrast,
                    pic.brightness,
                    pic.noise_level,
                    pic.id,
                )
            )

        logger.debug(
            f"[UPDATE] Committing {len(values)} full image quality updates to database."
        )
        cursor.executemany(
            "UPDATE pictures SET sharpness = ?, edge_density = ?, contrast = ?, brightness = ?, noise_level = ? WHERE id = ?",
            values,
        )
        conn.commit()

    def _update_face_quality(self, conn, pics):
        cursor = conn.cursor()
        values = []
        for pic in pics:
            # Assert face metrics are not bytes
            for metric_name in [
                "face_sharpness",
                "face_edge_density",
                "face_contrast",
                "face_brightness",
                "face_noise_level",
            ]:
                metric_value = getattr(pic, metric_name)
                assert not isinstance(metric_value, bytes), (
                    f"Corrupt face metric: {metric_name} for picture {pic.id} is bytes: {metric_value!r}"
                )
            logger.debug(
                "[UPDATE] Picture id %s face_sharpness: %s face_edge_density: %s face_contrast: %s face_brightness: %s face_noise_level: %s",
                pic.id,
                pic.face_sharpness,
                pic.face_edge_density,
                pic.face_contrast,
                pic.face_brightness,
                pic.face_noise_level,
            )
            values.append(
                (
                    pic.face_sharpness,
                    pic.face_edge_density,
                    pic.face_contrast,
                    pic.face_brightness,
                    pic.face_noise_level,
                    pic.id,
                )
            )

        logger.debug(
            f"[UPDATE] Committing {len(values)} face quality updates to database."
        )
        cursor.executemany(
            "UPDATE pictures SET face_sharpness = ?, face_edge_density = ?, face_contrast = ?, face_brightness = ?, face_noise_level = ? WHERE id = ?",
            values,
        )
        conn.commit()
