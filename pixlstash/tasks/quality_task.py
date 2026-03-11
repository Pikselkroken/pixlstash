import time
import os

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
    """Task that calculates full-image quality metrics for one batch."""

    BATCH_SIZE = 64
    FULL_IMAGE_MAX_SIDE = 512

    def __init__(self, database):
        super().__init__(
            task_type="QualityTask",
            params={
                "batch_size": self.BATCH_SIZE,
            },
        )
        self._db = database

    def _run_task(self):
        start = time.time()
        quality_helper = QualityUtils(self._db)

        pics_missing_quality = self._db.run_task(self._find_pictures_missing_quality)
        if not pics_missing_quality:
            return {"changed_count": 0, "changed": []}

        self._backfill_missing_picture_metadata(pics_missing_quality)

        grouped_full = quality_helper.group_pictures_by_format_and_size(
            pics_missing_quality
        )
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
                file_path = ImageUtils.resolve_picture_path(
                    self._db.image_root, pic.file_path
                )
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
    def _find_pictures_missing_quality(session: Session):
        return session.exec(
            select(Picture)
            .outerjoin(
                Quality,
                (Quality.picture_id == Picture.id) & (Quality.face_id.is_(None)),
            )
            .where(Quality.id.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
            .order_by(Picture.format, Picture.width, Picture.height)
            .limit(QualityTask.BATCH_SIZE * 8)
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
            .where(Quality.id.is_(None))
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
