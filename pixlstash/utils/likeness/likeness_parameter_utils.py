"""Likeness parameter vector computation utilities."""

import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
from sqlalchemy import func, update as sa_update
from sqlmodel import Session, delete, select

from pixlstash.db_models.picture import (
    LIKENESS_PARAMETER_SENTINEL,
    LikenessParameter,
    Picture,
)
from pixlstash.db_models.picture_likeness import PictureLikeness, PictureLikenessQueue
from pixlstash.db_models.quality import Quality
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.pixl_logging import get_logger

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


class LikenessParameterUtils:
    """Compute likeness parameter vectors in size-binned batches."""

    def __init__(self, database):
        self._db = database

    @staticmethod
    def find_next_work(
        session: Session, scan_limit: int
    ) -> Optional[List[Tuple[int, int, int]]]:
        """Return up to *scan_limit* (id, width, height) tuples for pictures missing all params.

        All parameters are written atomically, so ``size_bin_index IS NULL`` is
        the sole pending-work indicator.  Returns ``None`` when idle.
        """
        return LikenessParameterUtils._find_size_bin_batch(session, scan_limit)

    @staticmethod
    def count_pending_parameters(session: Session) -> int:
        """Return a count of pictures missing likeness parameters.

        With all parameters written atomically, ``size_bin_index IS NULL`` is
        the single authoritative indicator.  Pictures without width/height are
        excluded because they can never receive a ``size_bin_index``.
        """
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.size_bin_index.is_(None))
            .where(Picture.width.is_not(None))
            .where(Picture.height.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def has_pending_work(session: Session) -> bool:
        """Return True if there are pictures with pending likeness parameters."""
        return LikenessParameterUtils.count_pending_parameters(session) > 0

    @staticmethod
    def fetch_stuck_pictures(session: Session, limit: int = 20) -> list:
        """Return diagnostic rows for pictures counted-as-pending but not being resolved."""
        rows = session.exec(
            select(
                Picture.id,
                Picture.width,
                Picture.height,
                Picture.size_bin_index,
                Picture.likeness_parameters,
            )
            .where(Picture.size_bin_index.is_(None))
            .where(Picture.width.is_not(None))
            .where(Picture.height.is_not(None))
            .limit(limit)
        ).all()
        return [
            {
                "id": int(r[0]),
                "width": r[1],
                "height": r[2],
                "size_bin_index": r[3],
                "likeness_parameters_null": r[4] is None,
            }
            for r in rows
        ]

    @staticmethod
    def _find_size_bin_batch(
        session: Session, max_ids: int
    ) -> Optional[List[Tuple[int, int, int]]]:
        """Return up to *max_ids* (id, width, height) tuples for pictures missing size_bin_index.

        Each picture gets its own size_bin_index computed from its dimensions,
        so there is no need to group by exact resolution.  Processing a flat
        batch of BATCH_SIZE pictures instead of one per unique resolution gives
        a 100-128x throughput improvement when pictures have diverse dimensions.
        """
        rows = session.exec(
            select(Picture.id, Picture.width, Picture.height)
            .where(
                Picture.size_bin_index.is_(None)
                & Picture.width.is_not(None)
                & Picture.height.is_not(None)
            )
            .order_by(Picture.id)
            .limit(max_ids)
        ).all()
        if not rows:
            return None
        return [(int(pid), int(w), int(h)) for pid, w, h in rows]

    @staticmethod
    def update_size_bin(
        session: Session,
        ids: List[int],
        size_bin_by_id: Dict[int, int],
        vector_length: int,
    ) -> None:
        """Assign a per-picture size-bin index to a batch of pictures.

        Each picture's ``size_bin_index`` is looked up from ``size_bin_by_id``
        so pictures with different resolutions can be processed in one batch.
        """
        logger.debug(
            "LikenessParams: assigning size_bin_index to %d pictures (ids=%s)",
            len(ids),
            ids[:10],
        )
        rows = session.exec(
            select(Picture.id, Picture.likeness_parameters).where(Picture.id.in_(ids))
        ).all()
        updates = []
        for pic_id, blob in rows:
            sbi = size_bin_by_id.get(int(pic_id))
            if sbi is None:
                continue
            vec = LikenessParameterUtils.decode_parameters(blob, vector_length)
            vec[int(LikenessParameter.SIZE_BIN)] = float(sbi)
            updates.append(
                {
                    "id": int(pic_id),
                    "likeness_parameters": vec.tobytes(),
                    "size_bin_index": sbi,
                }
            )
        if updates:
            session.execute(sa_update(Picture), updates)
            session.commit()

    @staticmethod
    def update_parameter_values(
        session: Session,
        ids: List[int],
        param_index: int,
        values: List[float],
        vector_length: int,
    ) -> None:
        """Update a single parameter dimension for a batch of pictures."""
        if not ids:
            return
        logger.debug(
            "LikenessParams: updating param_index=%d (sentinel fill) for %d pictures",
            param_index,
            len(ids),
        )
        values_by_id = dict(zip(ids, values))
        rows = session.exec(
            select(Picture.id, Picture.likeness_parameters).where(Picture.id.in_(ids))
        ).all()
        updates = []
        for pic_id, blob in rows:
            vec = LikenessParameterUtils.decode_parameters(blob, vector_length)
            vec[param_index] = float(values_by_id.get(int(pic_id), 0.0))
            updates.append({"id": int(pic_id), "likeness_parameters": vec.tobytes()})
        if updates:
            session.execute(sa_update(Picture), updates)
            session.commit()
        LikenessParameterUtils.reset_likeness_for_pictures(session, ids)

    @staticmethod
    def fetch_blobs_for_ids(
        session: Session, ids: List[int]
    ) -> Dict[int, Optional[bytes]]:
        """Fetch current ``likeness_parameters`` blobs for a list of IDs.

        Used to load blobs into a worker thread before compute so that the
        serialised write queue only has to run the bare bulk UPDATE.
        """
        rows = session.exec(
            select(Picture.id, Picture.likeness_parameters).where(Picture.id.in_(ids))
        ).all()
        result: Dict[int, Optional[bytes]] = {int(pid): blob for pid, blob in rows}
        for pid in ids:
            result.setdefault(pid, None)
        return result

    @staticmethod
    def compute_all_param_updates(
        ids: List[int],
        blobs_by_id: Dict[int, Optional[bytes]],
        size_bin_by_id: Dict[int, int],
        quality_by_id: Dict[int, Dict[str, float]],
        picture_by_id: Dict[int, Dict[str, float]],
        vector_length: int,
    ) -> List[dict]:
        """Compute a complete parameter blob for every picture in a single pass.

        Writes SIZE_BIN, all quality params, and all picture params atomically.
        Pictures without quality data receive sentinel values for quality slots.
        """
        updates = []
        for pid in ids:
            sbi = size_bin_by_id.get(pid)
            if sbi is None:
                continue
            vec = LikenessParameterUtils.decode_parameters(
                blobs_by_id.get(pid), vector_length
            )
            vec[int(LikenessParameter.SIZE_BIN)] = float(sbi)
            q = quality_by_id.get(pid, {})
            for param, field in QUALITY_PARAM_FIELDS.items():
                vec[int(param)] = float(q.get(field, LIKENESS_PARAMETER_SENTINEL))
            p = picture_by_id.get(pid, {})
            for param, field in PICTURE_PARAM_FIELDS.items():
                vec[int(param)] = float(p.get(field, LIKENESS_PARAMETER_SENTINEL))
            updates.append(
                {
                    "id": pid,
                    "likeness_parameters": vec.tobytes(),
                    "size_bin_index": sbi,
                }
            )
        return updates

    @staticmethod
    def write_blob_updates(session: Session, updates: List[dict]) -> None:
        """Write pre-computed parameter blob updates (pure SQL, no computation).

        Keeps the serialised write queue free from CPU work so all worker
        threads can compute blobs concurrently while only the SQL write is
        serialised.
        """
        if not updates:
            return
        session.execute(sa_update(Picture), updates)
        session.commit()

    @staticmethod
    def fetch_quality_for_ids(
        session: Session, ids: List[int]
    ) -> Dict[int, Dict[str, float]]:
        """Fetch quality metrics for a list of picture IDs."""
        rows = session.exec(
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
                Quality.picture_id.in_(ids),
            )
        ).all()
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

    @staticmethod
    def fetch_picture_params_for_ids(
        session: Session,
        ids: List[int],
        image_root: str,
    ) -> Tuple[Dict[int, Dict[str, float]], Dict[int, Dict[str, object]]]:
        """
        Fetch picture-level parameters (aspect ratio, phash, date) for a list of IDs.

        Returns a tuple of ``(params_by_id, updates_by_id)`` where ``updates_by_id``
        maps picture IDs to fields that were inferred on the fly and should be
        written back to the database.
        """
        rows = session.exec(
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
                full_path = ImageUtils.resolve_picture_path(image_root, file_path)
                if full_path and os.path.exists(full_path):
                    if created_at_value is None:
                        created_at_value = (
                            LikenessParameterUtils.compute_created_at_from_file_static(
                                full_path, file_path
                            )
                        )
                        if created_at_value is not None:
                            updates_by_id.setdefault(int(pic_id), {})["created_at"] = (
                                created_at_value
                            )
                    if not phash_value:
                        phash_value = (
                            LikenessParameterUtils.compute_phash_from_file_static(
                                full_path, file_path
                            )
                        )
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
        """Write inferred metadata (created_at, perceptual_hash) back to the database."""
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
        """Compute a difference hash (dHash) for a PIL image."""
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
        """Compute a perceptual hash for an image or video file."""
        return LikenessParameterUtils.compute_phash_from_file_static(
            full_path, rel_path
        )

    @staticmethod
    def compute_phash_from_file_static(
        full_path: str, rel_path: Optional[str]
    ) -> Optional[str]:
        """Compute a perceptual hash for an image or video file (static version)."""
        try:
            if VideoUtils.is_video_file(rel_path or full_path):
                frames = VideoUtils.extract_representative_video_frames(
                    full_path, count=3
                )
                for frame in frames:
                    phash = LikenessParameterUtils.compute_dhash(frame)
                    if phash:
                        return phash
                return None
            with Image.open(full_path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return LikenessParameterUtils.compute_dhash(img)
        except Exception as exc:
            logger.warning(
                "LikenessParameterUtils: Failed to compute phash for %s (%s)",
                full_path,
                exc,
            )
            return None

    def compute_created_at_from_file(
        self, full_path: str, rel_path: Optional[str]
    ) -> Optional[datetime]:
        """Extract (or infer) the creation datetime for an image or video file."""
        return LikenessParameterUtils.compute_created_at_from_file_static(
            full_path, rel_path
        )

    @staticmethod
    def compute_created_at_from_file_static(
        full_path: str, rel_path: Optional[str]
    ) -> Optional[datetime]:
        """Extract (or infer) the creation datetime for an image or video file (static version)."""
        try:
            if VideoUtils.is_video_file(rel_path or full_path):
                return ImageUtils.extract_created_at_from_metadata(
                    b"", fallback_file_path=full_path
                )
            with open(full_path, "rb") as handle:
                image_bytes = handle.read()
            return ImageUtils.extract_created_at_from_metadata(
                image_bytes, fallback_file_path=full_path
            )
        except Exception as exc:
            logger.warning(
                "LikenessParameterUtils: Failed to compute created_at for %s (%s)",
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
        """Update quality-derived parameter dimensions for a batch of pictures."""
        logger.debug(
            "LikenessParams: updating quality params for %d pictures (quality data for %d)",
            len(ids),
            len(quality_by_id),
        )
        LikenessParameterUtils._update_values_for_parameters(
            session=session,
            ids=ids,
            values_by_id=quality_by_id,
            parameter_fields=QUALITY_PARAM_FIELDS,
            vector_length=vector_length,
        )
        LikenessParameterUtils.reset_likeness_for_pictures(session, ids)

    @staticmethod
    def update_picture_values(
        session: Session,
        ids: List[int],
        picture_by_id: Dict[int, Dict[str, float]],
        vector_length: int,
    ) -> None:
        """Update picture-derived parameter dimensions for a batch of pictures."""
        logger.debug(
            "LikenessParams: updating picture params for %d pictures (data for %d)",
            len(ids),
            len(picture_by_id),
        )
        LikenessParameterUtils._update_values_for_parameters(
            session=session,
            ids=ids,
            values_by_id=picture_by_id,
            parameter_fields=PICTURE_PARAM_FIELDS,
            vector_length=vector_length,
        )
        LikenessParameterUtils.reset_likeness_for_pictures(session, ids)

    @staticmethod
    def _update_values_for_parameters(
        session: Session,
        ids: List[int],
        values_by_id: Dict[int, Dict[str, float]],
        parameter_fields: Dict[LikenessParameter, str],
        vector_length: int,
    ) -> None:
        if not ids:
            return
        rows = session.exec(
            select(Picture.id, Picture.likeness_parameters).where(Picture.id.in_(ids))
        ).all()
        updates = []
        for pic_id, blob in rows:
            vec = LikenessParameterUtils.decode_parameters(blob, vector_length)
            values = values_by_id.get(int(pic_id), {})
            for param, field in parameter_fields.items():
                vec[int(param)] = float(values.get(field, LIKENESS_PARAMETER_SENTINEL))
            updates.append({"id": int(pic_id), "likeness_parameters": vec.tobytes()})
        if updates:
            session.execute(sa_update(Picture), updates)
            session.commit()

    @staticmethod
    def reset_likeness_for_pictures(session: Session, ids: List[int]) -> None:
        """Delete existing likeness relations and re-queue the given pictures."""
        if not ids:
            return
        unique_ids = sorted({int(pid) for pid in ids})
        # Count pairs being deleted so we can observe churn
        deleted = session.exec(
            delete(PictureLikeness).where(
                (PictureLikeness.picture_id_a.in_(unique_ids))
                | (PictureLikeness.picture_id_b.in_(unique_ids))
            )
        )
        deleted_count = deleted.rowcount if hasattr(deleted, "rowcount") else "?"
        logger.debug(
            "LikenessParams: reset_likeness_for_pictures: deleted %s pairs, re-queuing %d pictures",
            deleted_count,
            len(unique_ids),
        )
        PictureLikenessQueue.enqueue(session, unique_ids)
        session.commit()

    @staticmethod
    def size_bin_index(width: int, height: int) -> int:
        """Compute a unique integer size-bin index from width and height."""
        return (int(width) << 32) + int(height)

    @staticmethod
    def decode_parameters(blob: Optional[object], length: int) -> np.ndarray:
        """
        Decode a raw parameter blob to a numpy float32 vector of the given length.

        Returns a sentinel-filled vector if the blob is missing or malformed.
        """
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
