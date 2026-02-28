import cv2
import numpy as np
import os
import hashlib
import uuid
from fractions import Fraction

from datetime import datetime, timezone
from io import BytesIO
from typing import List, Optional
from PIL import Image, ImageOps

try:
    from PIL.TiffImagePlugin import IFDRational
except Exception:  # pragma: no cover - optional import
    IFDRational = None

from pixlvault.pixl_logging import get_logger
from pixlvault.db_models.picture import Picture

logger = get_logger(__name__)

THUMBNAIL_FORMAT = "WEBP"
THUMBNAIL_EXTENSION = ".webp"
THUMBNAIL_QUALITY = 80
THUMBNAIL_WEBP_METHOD = 2


class PictureUtils:
    @staticmethod
    def calculate_smart_score_batch_numpy(
        candidates: List[dict],
        good_anchors: List[dict],
        bad_anchors: List[dict],
        config: Optional[dict] = None,
    ) -> np.ndarray:
        """
        Calculate smart scores for a batch of candidates using numpy.

        Args:
            candidates: List of dicts with 'id', 'embedding' (numpy array), 'aesthetic_score'.
            good_anchors: List of dicts with 'embedding', 'score'.
            bad_anchors: List of dicts with 'embedding', 'score'.
            config: Config dict overrides.

        Returns:
            np.ndarray: Array of floating point scores corresponding to candidates.
        """

        # Default configuration
        cfg = {
            "w_good": 0.60,
            "w_bad": 0.20,
            "w_aest": 0.2,
            "w_resolution": 0.06,
            "w_noise": 0.08,
            "w_edge": 0.05,
            "w_tags": 0.08,
            "w_penalised_tag": 0.40,
            "penalised_tag_cap": 3.5,
            "tag_bonus_cap": 10,
            "topk": 3,
            "minSim": 0.75,
            "minBadSim": 0.88,
            # Normalization range for Neural Aesthetic Score
            # Adjusted based on user observation (Max ~4.5)
            # We set max to 5.0 to allow some headroom, but map 4.5 to a strong 0.75
            "aest_min": 2.0,
            "aest_max": 7.0,
            # Resolution scoring (megapixels)
            "res_min_mpx": 0.2,
            "res_max_mpx": 4.0,
            "res_use_log": True,
        }
        if config:
            cfg.update(config)

        if not candidates:
            return np.array([])

        # Extract candidate vectors with a consistent shape
        target_shape = None
        for c in candidates:
            emb = c.get("embedding")
            if isinstance(emb, np.ndarray) and emb.ndim == 1 and emb.size > 0:
                target_shape = emb.shape
                break

        if target_shape is None:
            return np.zeros(len(candidates))

        invalid_embeddings = 0
        cand_vecs = []
        for c in candidates:
            emb = c.get("embedding")
            if (
                isinstance(emb, np.ndarray)
                and emb.ndim == 1
                and emb.shape == target_shape
            ):
                cand_vecs.append(emb.astype(np.float32, copy=False))
            else:
                cand_vecs.append(np.zeros(target_shape, dtype=np.float32))
                invalid_embeddings += 1

        if invalid_embeddings:
            logger.warning(
                "[SMART SCORE] %s candidates had invalid/mismatched embeddings; using zeros.",
                invalid_embeddings,
            )

        # Aesthetic score:  using configured range (e.g. 3.0-6.5 -> 0-1)
        # DB stores raw outputs from aesthetic model (typically 1-10 scale)
        raw_aest = np.array(
            [float(c.get("aesthetic_score") or 5.0) for c in candidates]
        )

        a_min = cfg.get("aest_min", 3.0)
        a_max = cfg.get("aest_max", 7.0)

        # Avoid division by zero
        denom = max(0.1, a_max - a_min)

        cand_aest = np.clip((raw_aest - a_min) / denom, 0.0, 1.0)

        M_cand = np.stack(cand_vecs)
        scores = np.zeros(len(candidates))

        # Helper: Normalised weight from score (1..5 -> 0..1)
        def norm_weight(s):
            effective = s if s > 0 else 2.5
            return max(0, min(1, (effective - 1) / 4.0))

        # Dot product helper: 0.5 * (1 + sum(a*b))
        def sim01_batch(A, B):
            # A: (N, D), B: (M, D) -> (N, M)
            return 0.5 * (1 + np.dot(A, B.T))

        # 1. Character Similarity disabled (smart score independent of character likeness)

        # 2. Good Anchors
        good_component = np.zeros(len(candidates))
        if good_anchors:
            good_pairs = [
                (a["embedding"], a.get("score", 0))
                for a in good_anchors
                if isinstance(a.get("embedding"), np.ndarray)
                and a["embedding"].ndim == 1
                and a["embedding"].shape == target_shape
            ]
            if good_pairs:
                good_vecs = [p[0] for p in good_pairs]
                good_weights = np.array([norm_weight(p[1]) for p in good_pairs])
                M_good = np.stack(good_vecs)
            else:
                M_good = None

        if good_anchors and M_good is not None:
            sims = sim01_batch(M_cand, M_good)

            # Max raw sim for gating
            max_raw = np.max(sims, axis=1)
            mask_good = max_raw >= cfg["minSim"]

            # Weighted average of top K
            weighted = sims * good_weights
            K = min(cfg["topk"], weighted.shape[1])

            if K > 0:
                if K < weighted.shape[1]:
                    # -partition gives K largest
                    topk = -np.partition(-weighted, K - 1, axis=1)[:, :K]
                else:
                    topk = weighted

                # abs() because we negated to sort (though partitioning is not sorting, values are negative)
                # Actually, -weighted values are negative. partition keeps them negative.
                # Taking abs() restores them to positive.
                avg_good = np.mean(np.abs(topk), axis=1)
                good_component = cfg["w_good"] * (avg_good * mask_good)
                scores += good_component

        bad_component = np.zeros(len(candidates))
        mask_bad = np.zeros(len(candidates), dtype=bool)
        if bad_anchors:
            bad_pairs = [
                (a["embedding"], a.get("score", 0))
                for a in bad_anchors
                if isinstance(a.get("embedding"), np.ndarray)
                and a["embedding"].ndim == 1
                and a["embedding"].shape == target_shape
            ]
            if bad_pairs:
                bad_vecs = [p[0] for p in bad_pairs]
                # Bad weight is (1.0 - norm_score)
                bad_weights = np.array([1.0 - norm_weight(p[1]) for p in bad_pairs])
                M_bad = np.stack(bad_vecs)
            else:
                M_bad = None

        if bad_anchors and M_bad is not None:
            sims = sim01_batch(M_cand, M_bad)

            # Max raw sim for gating negative penalty
            max_raw_bad = np.max(sims, axis=1)
            mask_bad = max_raw_bad >= cfg["minBadSim"]

            weighted = sims * bad_weights

            K = min(cfg["topk"], weighted.shape[1])
            if K > 0:
                if K < weighted.shape[1]:
                    topk = -np.partition(-weighted, K - 1, axis=1)[:, :K]
                else:
                    topk = weighted
                avg_bad = np.mean(np.abs(topk), axis=1)

                # Apply bad anchor penalty only if similarity exceeds threshold
                bad_component = cfg["w_bad"] * (avg_bad * mask_bad)
                scores -= bad_component

        # 4. Aesthetic
        aest_component = cfg["w_aest"] * cand_aest
        scores += aest_component

        # 4b. Resolution (megapixels)
        widths = np.array([c.get("width") or 0 for c in candidates], dtype=np.float32)
        heights = np.array([c.get("height") or 0 for c in candidates], dtype=np.float32)
        mpx = (widths * heights) / 1_000_000.0
        res_min = float(cfg.get("res_min_mpx", 0.2) or 0.2)
        res_max = float(cfg.get("res_max_mpx", 4.0) or 4.0)
        res_min = max(0.01, res_min)
        res_max = max(res_min + 0.01, res_max)
        if cfg.get("res_use_log", True):
            res_vals = np.log10(np.clip(mpx, 1e-6, None))
            res_min_val = np.log10(res_min)
            res_max_val = np.log10(res_max)
        else:
            res_vals = mpx
            res_min_val = res_min
            res_max_val = res_max
        denom_res = max(0.001, res_max_val - res_min_val)
        res_norm = np.clip((res_vals - res_min_val) / denom_res, 0.0, 1.0)
        res_component = cfg["w_resolution"] * res_norm
        scores += res_component

        # 4c. Noise (prefer lower noise) and edge density
        noise_vals = np.array(
            [c.get("noise_level") for c in candidates], dtype=np.float32
        )
        noise_vals = np.where(np.isfinite(noise_vals), noise_vals, np.nan)
        noise_vals = np.where(noise_vals < 0, np.nan, noise_vals)
        noise_vals = np.where(np.isnan(noise_vals), 0.5, noise_vals)
        noise_vals = np.clip(noise_vals, 0.0, 1.0)
        noise_component = cfg["w_noise"] * (1.0 - noise_vals)
        scores += noise_component

        edge_vals = np.array(
            [c.get("edge_density") for c in candidates], dtype=np.float32
        )
        edge_vals = np.where(np.isfinite(edge_vals), edge_vals, np.nan)
        edge_vals = np.where(edge_vals < 0, np.nan, edge_vals)
        edge_vals = np.where(np.isnan(edge_vals), 0.5, edge_vals)
        edge_vals = np.clip(edge_vals, 0.0, 1.0)
        edge_component = cfg["w_edge"] * edge_vals
        scores += edge_component

        # 4d. Tag richness bonus (more tags = slight positive signal)
        tag_counts = np.array(
            [float(c.get("tag_count") or 0) for c in candidates], dtype=np.float32
        )
        tag_cap = max(1.0, float(cfg.get("tag_bonus_cap", 10) or 10))
        tag_norm = np.log1p(np.clip(tag_counts, 0.0, tag_cap)) / np.log1p(tag_cap)
        tag_component = cfg["w_tags"] * np.clip(tag_norm, 0.0, 1.0)
        scores += tag_component

        # 5. Penalised Tags
        penalised_counts = np.array(
            [float(c.get("penalised_tag_count") or 0) for c in candidates]
        )
        penalised_equivalent = np.clip(
            penalised_counts / 5.0, 0.0, float(cfg["penalised_tag_cap"])
        )
        penalised_component = cfg["w_penalised_tag"] * penalised_equivalent
        scores -= penalised_component

        # Rescale [0, 1] to [1, 5]
        clipped = np.clip(scores, 0.0, 1.0)
        final_scores = 1.0 + (clipped * 4.0)

        try:
            for idx, candidate in enumerate(candidates):
                penalised_count = float(penalised_counts[idx])
                final_score = float(final_scores[idx])
                if penalised_count <= 0 and final_score <= 1.5:
                    logger.info(
                        "[SMART SCORE][MIN] id=%s raw=%.4f clipped=%.4f final=%.4f "
                        "good=%.4f bad=%.4f aest=%.4f res=%.4f noise=%.4f "
                        "edge=%.4f tags=%.4f penalised=%.4f mpx=%.4f w=%s h=%s",
                        candidate.get("id"),
                        float(scores[idx]),
                        float(clipped[idx]),
                        final_score,
                        float(good_component[idx]),
                        float(bad_component[idx]),
                        float(aest_component[idx]),
                        float(res_component[idx]),
                        float(noise_component[idx]),
                        float(edge_component[idx]),
                        float(tag_component[idx]),
                        float(penalised_component[idx]),
                        float(mpx[idx]),
                        candidate.get("width"),
                        candidate.get("height"),
                    )
        except Exception as exc:
            logger.info("[SMART SCORE][MIN] logging failed: %s", exc)

        return final_scores

    @staticmethod
    def _coerce_metadata_value(value):
        if IFDRational is not None and isinstance(value, IFDRational):
            try:
                return float(value)
            except Exception:
                return str(value)
        if isinstance(value, Fraction):
            try:
                return float(value)
            except Exception:
                return str(value)
        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            try:
                return float(value)
            except Exception:
                return str(value)
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", errors="replace")
            except Exception:
                return repr(value)
        if isinstance(value, (list, tuple)):
            return [PictureUtils._coerce_metadata_value(v) for v in value]
        if isinstance(value, dict):
            return {
                str(k): PictureUtils._coerce_metadata_value(v) for k, v in value.items()
            }
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    @staticmethod
    def extract_embedded_metadata(file_path: str) -> dict:
        if not file_path or not os.path.exists(file_path):
            return {}
        metadata = {}
        try:
            from PIL import ExifTags

            with Image.open(file_path) as img:
                info = img.info or {}
                png_text = {}
                for key, value in info.items():
                    if key == "exif":
                        continue
                    png_text[str(key)] = PictureUtils._coerce_metadata_value(value)
                if png_text:
                    metadata["png"] = png_text

                try:
                    exif_data = img.getexif()
                    if exif_data:
                        exif_map = {}
                        for tag_id, value in exif_data.items():
                            tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                            exif_map[str(tag_name)] = (
                                PictureUtils._coerce_metadata_value(value)
                            )
                        if exif_map:
                            metadata["exif"] = exif_map
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Failed to extract embedded metadata: %s", exc)
        return metadata

    @staticmethod
    def resolve_picture_path(
        image_root: Optional[str], file_path: Optional[str]
    ) -> Optional[str]:
        """
        Resolve a stored picture path to an absolute file path.

        If file_path is already absolute, it is returned unchanged.
        If file_path is relative, it is joined with image_root.
        """
        if not file_path:
            return None
        if os.path.isabs(file_path):
            return file_path
        if not image_root:
            return file_path
        return os.path.join(image_root, file_path)

    @staticmethod
    def get_thumbnail_path(
        image_root: Optional[str], file_path: Optional[str]
    ) -> Optional[str]:
        resolved = PictureUtils.resolve_picture_path(image_root, file_path)
        if not resolved:
            return None
        base, _ = os.path.splitext(resolved)
        return f"{base}_thumb{THUMBNAIL_EXTENSION}"

    @staticmethod
    def read_thumbnail_bytes(
        image_root: Optional[str], file_path: Optional[str]
    ) -> Optional[bytes]:
        thumb_path = PictureUtils.get_thumbnail_path(image_root, file_path)
        if not thumb_path or not os.path.exists(thumb_path):
            return None
        try:
            with open(thumb_path, "rb") as handle:
                return handle.read()
        except Exception as exc:
            logger.warning("Failed to read thumbnail %s: %s", thumb_path, exc)
            return None

    @staticmethod
    def write_thumbnail_bytes(
        image_root: Optional[str], file_path: Optional[str], thumbnail: bytes
    ) -> Optional[str]:
        if not thumbnail:
            return None
        thumb_path = PictureUtils.get_thumbnail_path(image_root, file_path)
        if not thumb_path:
            return None
        try:
            os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
            with open(thumb_path, "wb") as handle:
                handle.write(thumbnail)
            return thumb_path
        except Exception as exc:
            logger.warning("Failed to write thumbnail %s: %s", thumb_path, exc)
            return None

    @staticmethod
    def _read_first_video_frame_bgr(file_path: str) -> Optional[np.ndarray]:
        cap = cv2.VideoCapture(file_path)
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            return frame
        return None

    @staticmethod
    def load_image_or_video_bgr(file_path: str) -> Optional[np.ndarray]:
        if not file_path or not os.path.exists(file_path):
            return None
        if PictureUtils.is_video_file(file_path):
            return PictureUtils._read_first_video_frame_bgr(file_path)

        img = cv2.imread(file_path)
        if img is not None:
            return img
        try:
            with Image.open(file_path) as pil_img:
                if pil_img.mode not in ("RGB", "L"):
                    pil_img = pil_img.convert("RGB")
                rgb = np.array(pil_img)
                return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except Exception:
            return None

    @staticmethod
    def _encode_thumbnail(pil_img: Image.Image) -> Optional[bytes]:
        buf = BytesIO()
        try:
            pil_img.save(
                buf,
                format=THUMBNAIL_FORMAT,
                quality=THUMBNAIL_QUALITY,
                method=THUMBNAIL_WEBP_METHOD,
            )
        except Exception as exc:
            logger.error("Error encoding thumbnail bytes: %s", exc)
            return None
        return buf.getvalue()

    @staticmethod
    def is_video_file(file_path: str) -> bool:
        """
        Returns True if the file is a supported video format.
        """
        ext = os.path.splitext(file_path)[1].lower()
        return ext in [".mp4", ".webm", ".avi", ".mov", ".mkv"]

    @staticmethod
    def clamp_bbox(bbox, width, height):
        """
        Clamp a bounding box [x_min, y_min, x_max, y_max] to image bounds.
        Returns a new list [x_min, y_min, x_max, y_max] or None if invalid.
        """
        if not bbox or len(bbox) != 4:
            return None
        x_min, y_min, x_max, y_max = [int(round(v)) for v in bbox]
        x_min = max(0, min(x_min, width - 1))
        y_min = max(0, min(y_min, height - 1))
        x_max = max(x_min + 1, min(x_max, width))
        y_max = max(y_min + 1, min(y_max, height))
        if x_max <= x_min or y_max <= y_min:
            return None
        return [x_min, y_min, x_max, y_max]

    @staticmethod
    def pad_image_to_square(pil_img: Image.Image, fill=0) -> Optional[Image.Image]:
        """Pad a PIL image to a square canvas while preserving content."""
        if pil_img is None:
            return None
        width, height = pil_img.size
        if width <= 0 or height <= 0:
            return None

        target = max(width, height)
        pad_x = max(0, target - width)
        pad_y = max(0, target - height)
        left = pad_x // 2
        right = pad_x - left
        top = pad_y // 2
        bottom = pad_y - top
        return ImageOps.expand(
            pil_img,
            border=(left, top, right, bottom),
            fill=fill,
        )

    @staticmethod
    def extract_created_at_from_metadata(
        image_bytes: bytes, fallback_file_path: str = None
    ) -> Optional[datetime]:
        """
        Try to extract the creation datetime from EXIF (for images), or from file metadata (for videos/filesystem).
        Returns a timezone-aware datetime in UTC, or None if not found.
        """
        from datetime import datetime, timezone
        import os

        try:
            from PIL import Image
            import piexif
        except ImportError:
            piexif = None
        # Try EXIF for images
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                exif_data = img.info.get("exif")
                if exif_data and piexif:
                    exif_dict = piexif.load(exif_data)
                    date_str = None
                    for tag in ("DateTimeOriginal", "DateTime", "DateTimeDigitised"):
                        val = exif_dict["0th"].get(
                            piexif.ImageIFD.__dict__.get(tag)
                        ) or exif_dict["Exif"].get(piexif.ExifIFD.__dict__.get(tag))
                        if val:
                            date_str = val.decode() if isinstance(val, bytes) else val
                            break
                    if date_str:

                        def _read_exif_offset() -> Optional[str]:
                            try:
                                exif_ifd = exif_dict.get("Exif") or {}
                                offset_tag_ids = [
                                    getattr(piexif.ExifIFD, "OffsetTimeOriginal", None),
                                    getattr(piexif.ExifIFD, "OffsetTime", None),
                                    getattr(
                                        piexif.ExifIFD, "OffsetTimeDigitized", None
                                    ),
                                    36880,
                                    36881,
                                    36882,
                                ]
                                for tag_id in offset_tag_ids:
                                    if tag_id is None:
                                        continue
                                    raw_val = exif_ifd.get(tag_id)
                                    if not raw_val:
                                        continue
                                    text = (
                                        raw_val.decode(errors="replace")
                                        if isinstance(raw_val, bytes)
                                        else str(raw_val)
                                    ).strip()
                                    if text:
                                        return text
                            except Exception:
                                return None
                            return None

                        # EXIF format: 'YYYY:MM:DD HH:MM:SS'
                        try:
                            offset_text = _read_exif_offset()
                            if offset_text:
                                normalized_offset = offset_text.replace(" ", "")
                                dt = datetime.strptime(
                                    f"{date_str} {normalized_offset}",
                                    "%Y:%m:%d %H:%M:%S %z",
                                )
                                return dt.astimezone(timezone.utc)

                            dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                            local_tz = (
                                datetime.now().astimezone().tzinfo or timezone.utc
                            )
                            return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
                        except Exception:
                            pass
        except Exception:
            pass
        # Try filesystem mtime/ctime if file path is available
        if fallback_file_path and os.path.exists(fallback_file_path):
            try:
                ts = os.path.getmtime(fallback_file_path)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return dt
            except Exception:
                pass
        # Could add video metadata extraction here if needed
        return None

    @staticmethod
    def crop_face_from_frame(frame, bbox):
        """
        Crop a face region from a video frame (numpy array) using bbox [x1, y1, x2, y2].
        Clamps bbox to frame bounds. Returns cropped region or None if invalid.
        """
        if frame is None or bbox is None or len(bbox) != 4:
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        x1 = int(max(0, min(w - 1, round(x1))))
        y1 = int(max(0, min(h - 1, round(y1))))
        x2 = int(max(0, min(w, round(x2))))
        y2 = int(max(0, min(h, round(y2))))
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0 or crop.shape[0] == 0 or crop.shape[1] == 0:
            return None
        return crop

    @staticmethod
    def extract_video_frames(file_path, frame_indices=None):
        """
        Extract frames from a video file and return them as PIL Images.
        Args:
            file_path (str): Path to video file.
            max_frames (int, optional): Maximum number of frames to extract.
            specific_frame (int, optional): If set, only extract this frame index (0-based).
            frame_indices (list[int], optional): Dictionary of specific frame indices to extract.
        Returns:
            List of PIL.Image objects.
        """
        import cv2
        from PIL import Image

        frames = []
        cap = cv2.VideoCapture(file_path)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if frame_indices is not None:
            # Extract specific listed frames
            sorted_indices = sorted(list(set(frame_indices)))
            for idx in sorted_indices:
                if 0 <= idx < frame_count:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        pil_img = Image.fromarray(frame_rgb)
                        frames.append(pil_img)
            cap.release()
            return frames

        count = 0
        for idx in range(frame_count):
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)
            frames.append(pil_img)
            count += 1
        cap.release()
        return frames

    @staticmethod
    def batch_facial_likeness(facial_features_list: list[np.ndarray]) -> np.ndarray:
        """
        Given a list of facial feature arrays (all same shape), compute a likeness matrix (cosine similarity).
        Each entry [i, j] is the cosine similarity between facial_features_list[i] and facial_features_list[j].
        Returns an (N, N) numpy array.
        """
        import numpy as np

        X = np.stack(facial_features_list, axis=0)  # shape (N, D)
        # Normalize each vector
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        X_norm = X / (norms + 1e-8)
        # Cosine similarity matrix
        likeness_matrix = np.dot(X_norm, X_norm.T)
        return likeness_matrix

    @staticmethod
    def extract_representative_video_frames(
        file_path: str, count: int = 3
    ) -> List[Image.Image]:
        """
        Extract 'count' evenly spaced frames from a video (e.g. start, middle, end).
        """
        import cv2

        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            return []

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        if total_frames <= 0:
            total_frames = 1

        # Calculate indices
        if count == 1:
            indices = [0]
        else:
            # e.g. count=3 -> 0, 50%, 100%
            step = (total_frames - 1) / (count - 1)
            indices = sorted(list(set([int(i * step) for i in range(count)])))

        return PictureUtils.extract_video_frames(file_path, frame_indices=indices)

    @staticmethod
    def load_metadata(file_path):
        """
        Efficiently return (height, width, channels) for image or video without loading full pixel data.
        """
        try:
            # Try image first
            with Image.open(file_path) as img:
                w, h = img.size
                mode = img.mode
                if mode == "RGB":
                    c = 3
                elif mode == "L":
                    c = 1
                else:
                    c = len(img.getbands())
                return (h, w, c)
        except Exception:
            pass
        # Try video
        try:
            import cv2

            cap = cv2.VideoCapture(file_path)
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                h, w = frame.shape[:2]
                c = frame.shape[2] if len(frame.shape) > 2 else 1
                return (h, w, c)
        except Exception:
            pass
        logger.error(f"Failed to read metadata for {file_path}")
        return None

    @staticmethod
    def load_image_or_video(file_path):
        try:
            # Try to open as image first
            from PIL import Image

            try:
                with Image.open(file_path) as img:
                    return np.array(img.convert("RGB"))
            except Exception:
                pass
            # If not an image, try as video (extract first frame)
            frame = PictureUtils._read_first_video_frame_bgr(file_path)
            if frame is not None:
                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return frame_rgb
            else:
                raise ValueError("Could not read image or first frame from video.")
        except Exception as e:
            logger.error(f"Failed to load image at {file_path} for quality worker: {e}")
            return None

    @staticmethod
    def generate_thumbnail_bytes(img, size=(384, 384)) -> Optional[bytes]:
        """
        Crop to square (bottom-cropped for tall images) and resize longest edge to 256px.
        Accepts either a PIL Image or a numpy array (OpenCV image).
        """
        try:
            if isinstance(img, Image.Image):
                pil_img = img.copy()
            else:
                # Assume numpy array (OpenCV image, BGR)
                pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            if pil_img.mode not in ("RGB", "L"):
                pil_img = pil_img.convert("RGB")
            if pil_img.width != pil_img.height:
                side = min(pil_img.width, pil_img.height)
                if pil_img.height > pil_img.width:
                    left = 0
                    top = 0
                else:
                    left = int(round((pil_img.width - side) / 2.0))
                    top = 0
                right = left + side
                bottom = top + side
                pil_img = pil_img.crop((left, top, right, bottom))
            max_edge = max(pil_img.width, pil_img.height)
            if max_edge > size[0]:
                scale = size[0] / max_edge
                new_w = int(round(pil_img.width * scale))
                new_h = int(round(pil_img.height * scale))
                pil_img = pil_img.resize((new_w, new_h), resample=Image.LANCZOS)
            return PictureUtils._encode_thumbnail(pil_img)
        except Exception as e:
            logger.error(f"Error generating thumbnail bytes: {e}")
            return None

    @staticmethod
    def generate_face_weighted_thumbnail_bytes(
        img,
        face_bboxes: List[List[int]],
        min_side: int = 256,
        output_size: tuple[int, int] = (256, 256),
    ) -> Optional[bytes]:
        thumbnail_bytes, _ = PictureUtils.generate_face_weighted_thumbnail_with_crop(
            img,
            face_bboxes,
            min_side=min_side,
            output_size=output_size,
        )
        return thumbnail_bytes

    @staticmethod
    def generate_face_weighted_thumbnail_with_crop(
        img,
        face_bboxes: List[List[int]],
        min_side: int = 256,
        output_size: tuple[int, int] = (256, 256),
    ) -> tuple[Optional[bytes], Optional[dict]]:
        if img is None or not face_bboxes:
            return None, None
        try:
            if isinstance(img, Image.Image):
                pil_img = img.copy()
            else:
                pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

            w, h = pil_img.size
            side_max = min(w, h)
            if side_max <= 0:
                return None, None

            clamped = []
            for bbox in face_bboxes:
                if not bbox or len(bbox) != 4:
                    continue
                x1, y1, x2, y2 = [int(round(v)) for v in bbox]
                x1 = max(0, min(w, x1))
                x2 = max(0, min(w, x2))
                y1 = max(0, min(h, y1))
                y2 = max(0, min(h, y2))
                if x2 <= x1 or y2 <= y1:
                    continue
                area = float((x2 - x1) * (y2 - y1))
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                clamped.append((x1, y1, x2, y2, area, cx, cy))

            if not clamped:
                return None, None

            min_x = min(b[0] for b in clamped)
            min_y = min(b[1] for b in clamped)
            max_x = max(b[2] for b in clamped)
            max_y = max(b[3] for b in clamped)

            total_area = sum(b[4] for b in clamped)
            if total_area > 0:
                cx = sum(b[4] * b[5] for b in clamped) / total_area
                cy = sum(b[4] * b[6] for b in clamped) / total_area
            else:
                cx = (min_x + max_x) / 2.0
                cy = (min_y + max_y) / 2.0

            side = side_max

            span_x = max_x - min_x
            span_y = max_y - min_y
            lower_left = max_x - side
            upper_left = min_x
            lower_top = max_y - side
            upper_top = min_y

            if span_x <= side:
                left = min(max(cx - side / 2.0, lower_left), upper_left)
            else:
                left = cx - side / 2.0
            if span_y <= side:
                top = min(max(cy - side / 2.0, lower_top), upper_top)
            else:
                top = cy - side / 2.0

            left = max(0, min(w - side, left))
            top = max(0, min(h - side, top))

            left = int(round(left))
            top = int(round(top))
            side = int(round(side))

            square_img = pil_img.crop((left, top, left + side, top + side))
            if output_size and square_img.size != output_size:
                square_img = square_img.resize(output_size, resample=Image.LANCZOS)
            crop = {
                "left": left,
                "top": top,
                "side": side,
            }
            thumbnail_bytes = PictureUtils._encode_thumbnail(square_img)
            if thumbnail_bytes is None:
                return None, None
            return thumbnail_bytes, crop
        except Exception as e:
            logger.error(f"Error generating face-weighted thumbnail: {e}")
            return None, None

    @staticmethod
    def load_and_crop_square_image_with_face(file_path, bbox):
        """
        Loads an image or video file, returns a square crop (as large as possible) that always includes the face bbox.
        The crop is not tight to the face, but always contains it.

        Args:
            file_path: Path to image or video file.
            bbox: [x1, y1, x2, y2]
        """
        x1, y1, x2, y2 = [int(round(v)) for v in bbox]
        img = None
        # Try image first
        try:
            from PIL import Image

            img = Image.open(file_path)
        except Exception:
            img = None
        # If not an image, try as video (extract first frame)
        if img is None:
            try:
                import cv2

                cap = cv2.VideoCapture(file_path)
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    img = frame
            except Exception:
                img = None
        if img is None:
            return None
        # PIL branch
        if hasattr(img, "size") and callable(getattr(img, "crop", None)):
            w, h = img.size
            # Clamp bbox to image
            x1c = max(0, min(w, x1))
            x2c = max(0, min(w, x2))
            y1c = max(0, min(h, y1))
            y2c = max(0, min(h, y2))
            # Find the minimal square that contains the bbox and is inside the image
            face_cx = (x1c + x2c) // 2
            face_cy = (y1c + y2c) // 2
            face_w = x2c - x1c
            face_h = y2c - y1c
            min_side = max(face_w, face_h)
            # Try to make the square as large as possible
            max_side = min(w, h)
            # The square must at least fit the face bbox
            side = max(min_side, min(max_side, max(w, h)))
            # Center the square on the face bbox center, but shift if needed to stay in bounds
            left = max(0, min(w - side, face_cx - side // 2))
            top = max(0, min(h - side, face_cy - side // 2))
            square_img = img.crop((left, top, left + side, top + side))
            return square_img
        else:
            # numpy array (OpenCV)
            h, w = img.shape[:2]
            x1c = max(0, min(w, x1))
            x2c = max(0, min(w, x2))
            y1c = max(0, min(h, y1))
            y2c = max(0, min(h, y2))
            face_cx = (x1c + x2c) // 2
            face_cy = (y1c + y2c) // 2
            face_w = x2c - x1c
            face_h = y2c - y1c
            min_side = max(face_w, face_h)
            max_side = min(w, h)
            side = max(min_side, min(max_side, max(w, h)))
            left = max(0, min(w - side, face_cx - side // 2))
            top = max(0, min(h - side, face_cy - side // 2))
            left = int(left)
            top = int(top)
            side = int(side)
            square_img = img[top : top + side, left : left + side]
            return square_img

    @staticmethod
    def _calculate_sha256_digest(
        file_size: int,
        read_chunk,
        source_label: Optional[str] = None,
    ) -> str:
        chunk_size = 8192
        sample_count = 8
        whole_file_threshold = 128 * 1024  # 128KB

        sha256 = hashlib.sha256()
        if file_size <= whole_file_threshold:
            for offset in range(0, file_size, chunk_size):
                chunk = read_chunk(offset, chunk_size)
                if chunk:
                    sha256.update(chunk)
            digest = sha256.hexdigest()
            if source_label:
                logger.debug(f"WHOLE: {source_label} size={file_size} hash={digest}")
            else:
                logger.debug(f"WHOLE: size={file_size} hash={digest}")
            return digest

        offsets = [
            int(i * (file_size - chunk_size) / (sample_count - 1))
            for i in range(sample_count)
        ]
        for offset in offsets:
            chunk = read_chunk(offset, chunk_size)
            if chunk:
                sha256.update(chunk)
        digest = sha256.hexdigest()
        if source_label:
            logger.debug(f"SAMPLED: {source_label} size={file_size} hash={digest}")
        else:
            logger.debug(f"SAMPLED: hash={digest}")
        return digest

    @staticmethod
    def calculate_hash_from_file_path(file_path: str) -> str:
        file_size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:

            def _read_chunk(offset, size):
                f.seek(offset)
                return f.read(size)

            return PictureUtils._calculate_sha256_digest(
                file_size=file_size,
                read_chunk=_read_chunk,
                source_label=file_path,
            )

    @staticmethod
    def calculate_hash_from_bytes(image_bytes: bytes) -> str:
        file_size = len(image_bytes)

        def _read_chunk(offset, size):
            return image_bytes[offset : offset + size]

        return PictureUtils._calculate_sha256_digest(
            file_size=file_size,
            read_chunk=_read_chunk,
        )

    @staticmethod
    def create_picture_from_file(
        image_root_path: str,
        source_file_path: str,
        picture_uuid: Optional[str] = None,
        pixel_sha: Optional[str] = None,
    ) -> Picture:
        """
        Create a Picture from a file path, using metadata for created_at if available.
        """
        if not os.path.exists(source_file_path):
            raise ValueError(f"Source file path does not exist: {source_file_path}")
        with open(source_file_path, "rb") as f:
            image_bytes = f.read()
        created_at = PictureUtils.extract_created_at_from_metadata(
            image_bytes, fallback_file_path=source_file_path
        )
        return PictureUtils.create_picture_from_bytes(
            image_root_path=image_root_path,
            image_bytes=image_bytes,
            picture_uuid=picture_uuid,
            pixel_sha=pixel_sha,
            created_at=created_at,
        )

    @staticmethod
    def create_picture_from_bytes(
        image_root_path: str,
        image_bytes: bytes,
        picture_uuid: Optional[str] = None,
        pixel_sha: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Picture:
        """
        Create a Picture from raw bytes. Uses created_at from metadata if provided, else falls back to now.
        """
        if not pixel_sha:
            pixel_sha = PictureUtils.calculate_hash_from_bytes(image_bytes)

        inferred_ext = ""
        if picture_uuid:
            inferred_ext = os.path.splitext(str(picture_uuid))[1].lower().lstrip(".")

        # Try to detect if this is a video or image
        img_format = None
        width = height = None
        thumbnail_bytes = None
        is_video = False
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                img_format = img.format or "PNG"
                width, height = img.size
                thumbnail_bytes = PictureUtils.generate_thumbnail_bytes(img)
        except Exception:
            is_video = True
        if not is_video and (
            thumbnail_bytes is None or width is None or height is None
        ):
            raise ValueError("Failed to generate thumbnail for image bytes")
        if is_video:
            import tempfile

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp.write(image_bytes)
                tmp_path = tmp.name
            cap = cv2.VideoCapture(tmp_path)
            ret, frame = cap.read()
            if not ret:
                logger.error("Could not read first frame from video for thumbnail.")
                raise ValueError("Failed to read first frame from video")
            else:
                height, width = frame.shape[:2]
                thumbnail_bytes = PictureUtils.generate_thumbnail_bytes(frame)
                if thumbnail_bytes is None:
                    raise ValueError("Failed to generate thumbnail for video")
            cap.release()
            if inferred_ext:
                img_format = inferred_ext.upper()
            else:
                img_format = "MP4"
            os.remove(tmp_path)

        if not picture_uuid:
            picture_uuid = str(uuid.uuid4()) + f".{img_format.lower()}"

        file_name = os.path.basename(picture_uuid)
        full_path = os.path.join(image_root_path, file_name)
        if os.path.exists(full_path):
            size_bytes = os.path.getsize(full_path)
        else:
            os.makedirs(image_root_path, exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(image_bytes)
            size_bytes = len(image_bytes)

        if thumbnail_bytes:
            saved_thumb = PictureUtils.write_thumbnail_bytes(
                image_root_path, file_name, thumbnail_bytes
            )
            if not saved_thumb:
                logger.warning("Failed to persist thumbnail for %s", file_name)

        if not created_at:
            created_at = PictureUtils.extract_created_at_from_metadata(
                image_bytes, fallback_file_path=full_path
            )
        if not created_at:
            created_at = datetime.now(timezone.utc)

        pic = Picture(
            file_path=file_name,
            format=img_format,
            width=width,
            height=height,
            size_bytes=size_bytes,
            created_at=created_at,
            pixel_sha=pixel_sha,
        )
        return pic

    @staticmethod
    def batch_face_likeness(face_crops: list[list[np.ndarray]]) -> np.ndarray:
        """
        Given a list of lists of face crops (as numpy arrays), compute a likeness matrix (cosine similarity).
        Each entry [i, j] is the best likeness between any crop in i and any crop in j.
        Returns an (N, N) numpy array.
        """
        import numpy as np

        N = len(face_crops)
        likeness_matrix = np.zeros((N, N), dtype=np.float32)
        # Flatten all crops and stack into tensors for batch ops
        flat_crops = [
            [
                crop.flatten().astype(np.float32)
                / (np.linalg.norm(crop.flatten().astype(np.float32)) + 1e-8)
                for crop in crops
            ]
            for crops in face_crops
        ]
        for i in range(N):
            for j in range(N):
                if i == j or not flat_crops[i] or not flat_crops[j]:
                    likeness_matrix[i, j] = 0.0
                else:
                    # Compute all pairwise cosine similarities, take max
                    sims = [
                        float(np.dot(c1, c2))
                        for c1 in flat_crops[i]
                        for c2 in flat_crops[j]
                    ]
                    likeness_matrix[i, j] = max(sims) if sims else 0.0
        return likeness_matrix

    @staticmethod
    def cosine_similarity(a: bytes, b: bytes) -> float:
        try:
            if a is None or b is None:
                return 0.0
            arr_a = (
                np.frombuffer(a, dtype=np.float32)
                if isinstance(a, bytes)
                else np.array(a, dtype=np.float32)
            )
            arr_b = (
                np.frombuffer(b, dtype=np.float32)
                if isinstance(b, bytes)
                else np.array(b, dtype=np.float32)
            )
            if arr_a.shape != arr_b.shape or arr_a.size == 0:
                return 0.0
            dot = np.dot(arr_a, arr_b)
            norm_a = np.linalg.norm(arr_a)
            norm_b = np.linalg.norm(arr_b)
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return float(dot / (norm_a * norm_b))
        except Exception as e:
            logger.warning(f"cosine_similarity error: {e}")
            return 0.0

    @classmethod
    def cosine_similarity_batch(cls, arr_a_list, arr_b_list):
        """
        Compute cosine similarity for two lists of np.ndarray feature vectors in batch.
        Returns a 1D np.ndarray of similarities scaled to [0, 1].
        """
        arr_a = np.stack(arr_a_list)
        arr_b = np.stack(arr_b_list)
        # Normalize
        arr_a_norm = arr_a / np.linalg.norm(arr_a, axis=1, keepdims=True)
        arr_b_norm = arr_b / np.linalg.norm(arr_b, axis=1, keepdims=True)
        # Compute dot products
        sims = np.sum(arr_a_norm * arr_b_norm, axis=1)
        sims = 0.5 * (sims + 1.0)  # Scale to [0, 1]
        return sims

    @staticmethod
    def crop_face_bbox_exact(file_path, bbox):
        """
        Loads an image or video file, returns a crop exactly matching the face bbox as a PIL Image.
        Args:
            file_path: Path to image or video file.
            bbox: [x1, y1, x2, y2]
        Returns:
            Cropped PIL Image, or None on error.
        """
        x1, y1, x2, y2 = [int(round(v)) for v in bbox]
        img = None
        from PIL import Image

        # Try image first
        try:
            img = Image.open(file_path)
        except Exception:
            logger.error(f"Failed to open image for cropping: {file_path}")
            img = None
        # If not an image, try as video (extract first frame)
        if img is None:
            try:
                import cv2

                cap = cv2.VideoCapture(file_path)
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    # Convert BGR (OpenCV) to RGB for PIL
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame_rgb)
            except Exception:
                img = None
        if img is None:
            return None
        w, h = img.size
        # Clamp bbox to image
        x1c = max(0, min(w, x1))
        x2c = max(0, min(w, x2))
        y1c = max(0, min(h, y1))
        y2c = max(0, min(h, y2))
        crop_img = img.crop((x1c, y1c, x2c, y2c))
        return crop_img

    @staticmethod
    def softmax_weighted_average(scores, alpha=5.0):
        """
        Compute a softmax-weighted average of likeness scores.
        Args:
            scores (list or np.ndarray): List of likeness scores (floats between 0 and 1).
            alpha (float): Controls sharpness; higher alpha makes the max more dominant.
        Returns:
            float: Softmax-weighted average likeness.
        """
        scores = np.array(scores)
        weights = np.exp(alpha * scores)
        return float(np.sum(weights * scores) / np.sum(weights))
