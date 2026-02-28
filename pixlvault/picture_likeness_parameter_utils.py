import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
from sqlalchemy import func
from sqlmodel import Session, delete, select

from pixlvault.database import DBPriority
from pixlvault.db_models.picture import (
    LIKENESS_PARAMETER_SENTINEL,
    LikenessParameter,
    Picture,
)
from pixlvault.db_models.picture_likeness import PictureLikeness, PictureLikenessQueue
from pixlvault.db_models.quality import Quality
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger

logger = get_logger(__name__)

QUALITY_PARAM_FIELDS = {
    LikenessParameter.BRIGHTNESS: "brightness",
    LikenessParameter.CONTRAST: "contrast",
    LikenessParameter.EDGE_DENSITY: "edge_density",
    LikenessParameter.NOISE_LEVEL: "noise_level",
    LikenessParameter.COLORFULNESS: "colorfulness",
    LikenessParameter.LUMINANCE_ENTROPY: "luminance_entropy",
    LikenessParameter.DOMINANT_HUE: "dominant_hue",
}

PICTURE_PARAM_FIELDS = {
    LikenessParameter.ASPECT_RATIO: "aspect_ratio",
    LikenessParameter.PHASH_PREFIX: "phash_prefix",
    LikenessParameter.DATE: "created_at",
}

PHASH_BITS = 64
PHASH_HEX_LEN = PHASH_BITS // 4


class PictureLikenessParameterUtils:
    """Compute likeness parameter vectors in size-binned batches."""

    BATCH_SIZE = 128
    SCAN_LIMIT = 2048

    def __init__(self, database):
        self._db = database

    @staticmethod
    def find_next_work(
        session: Session, batch_size: int, scan_limit: int
    ) -> Optional[Tuple[LikenessParameter, Optional[int], Tuple]]:
        for param in LikenessParameter:
            if param == LikenessParameter.SIZE_BIN:
                size_bin = PictureLikenessParameterUtils._find_size_bin_batch(
                    session, batch_size
                )
                if size_bin:
                    width, height, ids = size_bin
                    return param, None, (width, height, ids)
                continue

            param_batch = PictureLikenessParameterUtils._find_parameter_batch(
                session, param, batch_size, scan_limit
            )
            if param_batch:
                size_bin_index, ids, remaining_in_bin = param_batch
                return param, size_bin_index, (ids, remaining_in_bin)

        return None

    @staticmethod
    def count_pending_parameters(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(
                (Picture.likeness_parameters.is_(None))
                | (Picture.size_bin_index.is_(None))
            )
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _find_size_bin_batch(
        session: Session, batch_size: int
    ) -> Optional[Tuple[int, int, List[int]]]:
        row = session.exec(
            select(Picture.width, Picture.height)
            .where(
                Picture.size_bin_index.is_(None)
                & (Picture.width.is_not(None))
                & (Picture.height.is_not(None))
            )
            .order_by(Picture.width, Picture.height)
            .limit(1)
        ).first()
        if not row:
            return None
        width, height = row
        ids = session.exec(
            select(Picture.id)
            .where(
                (Picture.size_bin_index.is_(None))
                & (Picture.width == width)
                & (Picture.height == height)
            )
            .order_by(Picture.id)
            .limit(batch_size)
        ).all()
        if not ids:
            return None
        return int(width), int(height), [int(pid) for pid in ids]

    @staticmethod
    def _find_parameter_batch(
        session: Session,
        param: LikenessParameter,
        batch_size: int,
        scan_limit: int,
    ) -> Optional[Tuple[int, List[int], int]]:
        offset = 0
        while True:
            rows = session.exec(
                select(Picture.id, Picture.size_bin_index, Picture.likeness_parameters)
                .where(Picture.size_bin_index.is_not(None))
                .order_by(Picture.size_bin_index, Picture.id)
                .limit(scan_limit)
                .offset(offset)
            ).all()
            if not rows:
                return None

            quality_ids: set[int] = set()
            if param in QUALITY_PARAM_FIELDS:
                pic_ids = [int(row[0]) for row in rows]
                if pic_ids:
                    quality_rows = session.exec(
                        select(Quality.picture_id).where(
                            Quality.face_id.is_(None),
                            Quality.picture_id.in_(pic_ids),
                        )
                    ).all()
                    quality_ids = {
                        int(row[0]) if isinstance(row, (tuple, list)) else int(row)
                        for row in quality_rows
                    }

            missing_by_bin: Dict[int, List[int]] = {}
            quality_param_indices = {
                int(param_key) for param_key in QUALITY_PARAM_FIELDS.keys()
            }
            picture_param_indices = {
                int(param_key) for param_key in PICTURE_PARAM_FIELDS.keys()
            }

            for pic_id, size_bin_index, param_blob in rows:
                size_bin = int(size_bin_index)
                vec = PictureLikenessParameterUtils.decode_parameters(
                    param_blob, len(LikenessParameter)
                )
                if param in QUALITY_PARAM_FIELDS:
                    missing_quality = any(
                        vec[idx] == LIKENESS_PARAMETER_SENTINEL
                        for idx in quality_param_indices
                    )
                    if missing_quality and int(pic_id) in quality_ids:
                        missing_by_bin.setdefault(size_bin, []).append(int(pic_id))
                    continue
                if param in PICTURE_PARAM_FIELDS:
                    missing_picture = any(
                        vec[idx] == LIKENESS_PARAMETER_SENTINEL
                        for idx in picture_param_indices
                    )
                    if missing_picture:
                        missing_by_bin.setdefault(size_bin, []).append(int(pic_id))
                    continue
                if vec[int(param)] == LIKENESS_PARAMETER_SENTINEL:
                    missing_by_bin.setdefault(size_bin, []).append(int(pic_id))

            if missing_by_bin:
                size_bin, ids = max(
                    missing_by_bin.items(), key=lambda item: len(item[1])
                )
                remaining_in_bin = len(ids)
                return size_bin, ids[:batch_size], remaining_in_bin

            offset += scan_limit

    @staticmethod
    def update_size_bin(
        session: Session,
        ids: List[int],
        size_bin_index: int,
        vector_length: int,
    ) -> None:
        pics = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
        for pic in pics:
            vec = PictureLikenessParameterUtils.decode_parameters(
                pic.likeness_parameters, vector_length
            )
            vec[int(LikenessParameter.SIZE_BIN)] = float(size_bin_index)
            pic.likeness_parameters = vec
            pic.size_bin_index = size_bin_index
            session.add(pic)
        session.commit()

    @staticmethod
    def update_parameter_values(
        session: Session,
        ids: List[int],
        param_index: int,
        values: List[float],
        vector_length: int,
    ) -> None:
        if not ids:
            return
        pics = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
        values_by_id = dict(zip(ids, values))
        for pic in pics:
            vec = PictureLikenessParameterUtils.decode_parameters(
                pic.likeness_parameters, vector_length
            )
            value = values_by_id.get(int(pic.id), 0.0)
            vec[param_index] = float(value)
            pic.likeness_parameters = vec
            session.add(pic)
        session.commit()
        PictureLikenessParameterUtils.reset_likeness_for_pictures(session, ids)

    def fetch_quality_for_ids(self, ids: List[int]) -> Dict[int, Dict[str, float]]:
        def fetch_quality(session: Session, ids: List[int]):
            return session.exec(
                select(
                    Quality.picture_id,
                    Quality.brightness,
                    Quality.contrast,
                    Quality.edge_density,
                    Quality.noise_level,
                    Quality.colorfulness,
                    Quality.luminance_entropy,
                    Quality.dominant_hue,
                ).where(
                    Quality.face_id.is_(None),
                    Quality.picture_id.in_(ids),
                )
            ).all()

        rows = self._db.result_or_throw(
            self._db.submit_task(fetch_quality, ids, priority=DBPriority.LOW)
        )
        quality_by_id: Dict[int, Dict[str, float]] = {}
        for (
            pic_id,
            brightness,
            contrast,
            edge_density,
            noise_level,
            colorfulness,
            luminance_entropy,
            dominant_hue,
        ) in rows:
            quality_by_id[int(pic_id)] = {
                "brightness": float(brightness)
                if brightness is not None
                else LIKENESS_PARAMETER_SENTINEL,
                "contrast": float(contrast)
                if contrast is not None
                else LIKENESS_PARAMETER_SENTINEL,
                "edge_density": float(edge_density)
                if edge_density is not None
                else LIKENESS_PARAMETER_SENTINEL,
                "noise_level": float(noise_level)
                if noise_level is not None
                else LIKENESS_PARAMETER_SENTINEL,
                "colorfulness": float(colorfulness)
                if colorfulness is not None
                else LIKENESS_PARAMETER_SENTINEL,
                "luminance_entropy": float(luminance_entropy)
                if luminance_entropy is not None
                else LIKENESS_PARAMETER_SENTINEL,
                "dominant_hue": float(dominant_hue)
                if dominant_hue is not None
                else LIKENESS_PARAMETER_SENTINEL,
            }
        return quality_by_id

    def fetch_picture_params_for_ids(
        self, ids: List[int]
    ) -> Tuple[Dict[int, Dict[str, float]], Dict[int, Dict[str, object]]]:
        def fetch_picture_params(session: Session, ids: List[int]):
            return session.exec(
                select(
                    Picture.id,
                    Picture.width,
                    Picture.height,
                    Picture.created_at,
                    Picture.perceptual_hash,
                    Picture.file_path,
                )
                .where(Picture.id.in_(ids))
                .group_by(
                    Picture.id,
                    Picture.width,
                    Picture.height,
                    Picture.created_at,
                    Picture.perceptual_hash,
                    Picture.file_path,
                )
            ).all()

        rows = self._db.result_or_throw(
            self._db.submit_task(fetch_picture_params, ids, priority=DBPriority.LOW)
        )
        params_by_id: Dict[int, Dict[str, float]] = {}
        updates_by_id: Dict[int, Dict[str, object]] = {}
        for (
            pic_id,
            width,
            height,
            created_at,
            phash,
            file_path,
        ) in rows:
            created_at_value = created_at
            phash_value = phash
            full_path = None
            if file_path and (created_at_value is None or not phash_value):
                full_path = PictureUtils.resolve_picture_path(
                    self._db.image_root, file_path
                )
                if full_path and os.path.exists(full_path):
                    if created_at_value is None:
                        created_at_value = self.compute_created_at_from_file(
                            full_path, file_path
                        )
                        if created_at_value is not None:
                            updates_by_id.setdefault(int(pic_id), {})["created_at"] = (
                                created_at_value
                            )
                    if not phash_value:
                        phash_value = self.compute_phash_from_file(full_path, file_path)
                        if phash_value:
                            updates_by_id.setdefault(int(pic_id), {})[
                                "perceptual_hash"
                            ] = phash_value
            aspect_ratio = (
                float(width) / float(height)
                if width and height
                else LIKENESS_PARAMETER_SENTINEL
            )
            if (
                phash_value
                and isinstance(phash_value, str)
                and len(phash_value) >= PHASH_HEX_LEN
            ):
                try:
                    full_value = int(phash_value[:PHASH_HEX_LEN], 16)
                    max_value = float((2**PHASH_BITS) - 1)
                    phash_prefix = full_value / max_value if max_value else 0.0
                except ValueError:
                    phash_prefix = LIKENESS_PARAMETER_SENTINEL
            else:
                phash_prefix = LIKENESS_PARAMETER_SENTINEL
            date_value = (
                float(created_at_value.timestamp())
                if created_at_value is not None
                else LIKENESS_PARAMETER_SENTINEL
            )
            params_by_id[int(pic_id)] = {
                "aspect_ratio": aspect_ratio,
                "phash_prefix": phash_prefix,
                "created_at": date_value,
            }
        return params_by_id, updates_by_id

    @staticmethod
    def update_picture_metadata(
        session: Session,
        updates_by_id: Dict[int, Dict[str, object]],
    ) -> None:
        if not updates_by_id:
            return
        ids = list(updates_by_id.keys())
        pics = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
        for pic in pics:
            updates = updates_by_id.get(int(pic.id), {})
            if "created_at" in updates and pic.created_at is None:
                pic.created_at = updates["created_at"]
            if "perceptual_hash" in updates and not pic.perceptual_hash:
                pic.perceptual_hash = updates["perceptual_hash"]
            session.add(pic)
        session.commit()

    @staticmethod
    def compute_dhash(image: Image.Image, hash_size: int = 8) -> Optional[str]:
        try:
            resample = getattr(Image, "Resampling", Image).LANCZOS
            img = image.convert("L").resize((hash_size + 1, hash_size), resample)
            pixels = np.asarray(img, dtype=np.int16)
            diff = pixels[:, 1:] > pixels[:, :-1]
            bits = diff.flatten()
            value = 0
            for bit in bits:
                value = (value << 1) | int(bit)
            return f"{value:0{hash_size * hash_size // 4}x}"
        except Exception:
            return None

    def compute_phash_from_file(
        self, full_path: str, rel_path: Optional[str]
    ) -> Optional[str]:
        try:
            if PictureUtils.is_video_file(rel_path or full_path):
                frames = PictureUtils.extract_representative_video_frames(
                    full_path, count=3
                )
                for frame in frames:
                    phash = self.compute_dhash(frame)
                    if phash:
                        return phash
                return None
            with Image.open(full_path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return self.compute_dhash(img)
        except Exception as exc:
            logger.warning(
                "PictureLikenessParameterUtils: Failed to compute phash for %s (%s)",
                full_path,
                exc,
            )
            return None

    def compute_created_at_from_file(
        self, full_path: str, rel_path: Optional[str]
    ) -> Optional[datetime]:
        try:
            if PictureUtils.is_video_file(rel_path or full_path):
                return PictureUtils.extract_created_at_from_metadata(
                    b"", fallback_file_path=full_path
                )
            with open(full_path, "rb") as handle:
                image_bytes = handle.read()
            return PictureUtils.extract_created_at_from_metadata(
                image_bytes, fallback_file_path=full_path
            )
        except Exception as exc:
            logger.warning(
                "PictureLikenessParameterUtils: Failed to compute created_at for %s (%s)",
                full_path,
                exc,
            )
            return None

    @staticmethod
    def update_quality_values(
        session: Session,
        ids: List[int],
        quality_by_id: Dict[int, Dict[str, float]],
        vector_length: int,
    ) -> None:
        if not ids:
            return
        pics = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
        for pic in pics:
            vec = PictureLikenessParameterUtils.decode_parameters(
                pic.likeness_parameters, vector_length
            )
            quality_values = quality_by_id.get(int(pic.id), {})
            for param, field in QUALITY_PARAM_FIELDS.items():
                value = quality_values.get(field, LIKENESS_PARAMETER_SENTINEL)
                vec[int(param)] = float(value)
            pic.likeness_parameters = vec
            session.add(pic)
        session.commit()
        PictureLikenessParameterUtils.reset_likeness_for_pictures(session, ids)

    @staticmethod
    def update_picture_values(
        session: Session,
        ids: List[int],
        picture_by_id: Dict[int, Dict[str, float]],
        vector_length: int,
    ) -> None:
        if not ids:
            return
        pics = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
        for pic in pics:
            vec = PictureLikenessParameterUtils.decode_parameters(
                pic.likeness_parameters, vector_length
            )
            values = picture_by_id.get(int(pic.id), {})
            for param, field in PICTURE_PARAM_FIELDS.items():
                value = values.get(field, LIKENESS_PARAMETER_SENTINEL)
                vec[int(param)] = float(value)
            pic.likeness_parameters = vec
            session.add(pic)
        session.commit()
        PictureLikenessParameterUtils.reset_likeness_for_pictures(session, ids)

    @staticmethod
    def reset_likeness_for_pictures(session: Session, ids: List[int]) -> None:
        if not ids:
            return
        unique_ids = sorted({int(pid) for pid in ids})
        session.exec(
            delete(PictureLikeness).where(
                (PictureLikeness.picture_id_a.in_(unique_ids))
                | (PictureLikeness.picture_id_b.in_(unique_ids))
            )
        )
        PictureLikenessQueue.enqueue(session, unique_ids)
        session.commit()

    @staticmethod
    def size_bin_index(width: int, height: int) -> int:
        return (int(width) << 32) + int(height)

    @staticmethod
    def decode_parameters(blob: Optional[object], length: int) -> np.ndarray:
        if blob is None:
            return np.full(length, LIKENESS_PARAMETER_SENTINEL, dtype=np.float32)
        if isinstance(blob, np.ndarray):
            if blob.size == length:
                return blob.astype(np.float32, copy=False)
            return np.full(length, LIKENESS_PARAMETER_SENTINEL, dtype=np.float32)
        if isinstance(blob, (bytes, bytearray, memoryview)):
            data = np.frombuffer(blob, dtype=np.float32)
            if data.size == length:
                return data.copy()
            return np.full(length, LIKENESS_PARAMETER_SENTINEL, dtype=np.float32)
        return np.full(length, LIKENESS_PARAMETER_SENTINEL, dtype=np.float32)
