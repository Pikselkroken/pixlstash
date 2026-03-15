"""Image loading, thumbnail generation, metadata extraction, and picture creation utilities."""

import cv2
import hashlib
import os
import piexif
import tempfile
import uuid

from datetime import datetime, timezone
from fractions import Fraction
from io import BytesIO
from typing import Optional

import numpy as np
from PIL import ExifTags, Image, ImageOps

try:
    from PIL.TiffImagePlugin import IFDRational
except Exception:  # pragma: no cover - optional import
    IFDRational = None

from pixlstash.pixl_logging import get_logger
from pixlstash.db_models.picture import Picture
from pixlstash.utils.image_processing.video_utils import VideoUtils

logger = get_logger(__name__)

THUMBNAIL_FORMAT = "WEBP"
THUMBNAIL_EXTENSION = ".webp"
THUMBNAIL_QUALITY = 80
THUMBNAIL_WEBP_METHOD = 2


class ImageUtils:
    """Utility methods for image loading, thumbnails, metadata, and picture creation."""

    @staticmethod
    def _coerce_metadata_value(value):
        """Coerce a raw metadata value to a JSON-serialisable Python type."""
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
            return [ImageUtils._coerce_metadata_value(v) for v in value]
        if isinstance(value, dict):
            return {
                str(k): ImageUtils._coerce_metadata_value(v) for k, v in value.items()
            }
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    @staticmethod
    def extract_embedded_metadata(file_path: str) -> dict:
        """Extract embedded EXIF and PNG metadata from an image file."""
        if not file_path or not os.path.exists(file_path):
            return {}
        # Pillow cannot open video files — skip metadata extraction for them.
        if VideoUtils.is_video_file(file_path):
            return {}
        metadata = {}
        try:
            with Image.open(file_path) as img:
                info = img.info or {}
                png_text = {}
                for key, value in info.items():
                    if key == "exif":
                        continue
                    png_text[str(key)] = ImageUtils._coerce_metadata_value(value)
                if png_text:
                    metadata["png"] = png_text

                try:
                    exif_data = img.getexif()
                    if exif_data:
                        exif_map = {}
                        for tag_id, value in exif_data.items():
                            tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                            exif_map[str(tag_name)] = ImageUtils._coerce_metadata_value(
                                value
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

        If file_path is already absolute it is returned unchanged.
        If file_path is relative it is joined with image_root.
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
        """Return the expected thumbnail file path for a given picture path."""
        resolved = ImageUtils.resolve_picture_path(image_root, file_path)
        if not resolved:
            return None
        base, _ = os.path.splitext(resolved)
        return f"{base}_thumb{THUMBNAIL_EXTENSION}"

    @staticmethod
    def read_thumbnail_bytes(
        image_root: Optional[str], file_path: Optional[str]
    ) -> Optional[bytes]:
        """Read thumbnail bytes from disk, or return None if not found."""
        thumb_path = ImageUtils.get_thumbnail_path(image_root, file_path)
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
        """Write thumbnail bytes to disk and return the path, or None on failure."""
        if not thumbnail:
            return None
        thumb_path = ImageUtils.get_thumbnail_path(image_root, file_path)
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
    def load_image_or_video_bgr(file_path: str) -> Optional[np.ndarray]:
        """Load an image or the first video frame as a BGR numpy array."""
        if not file_path or not os.path.exists(file_path):
            return None
        if VideoUtils.is_video_file(file_path):
            return VideoUtils._read_first_video_frame_bgr(file_path)

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
        """Encode a PIL image as a WebP thumbnail and return the bytes."""
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
        Try to extract the creation datetime from EXIF (for images), or from file
        metadata (for videos / filesystem).

        Returns a timezone-aware datetime in UTC, or None if not found.
        """
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

        # Try to read creation time from the video container (MP4/MOV mvhd box)
        # before falling back to the filesystem mtime, which reflects the import
        # time rather than the original recording time for uploaded files.
        try:
            video_dt = VideoUtils.extract_created_at_from_bytes(image_bytes)
            if video_dt is not None:
                return video_dt
        except Exception:
            pass

        if fallback_file_path and os.path.exists(fallback_file_path):
            try:
                ts = os.path.getmtime(fallback_file_path)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return dt
            except Exception:
                pass
        return None

    @staticmethod
    def load_metadata(file_path):
        """
        Efficiently return ``(height, width, channels)`` for an image or video without
        loading full pixel data.
        """
        try:
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
        try:
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
        """Load an image or video (first frame) and return an RGB numpy array."""
        try:
            try:
                with Image.open(file_path) as img:
                    return np.array(img.convert("RGB"))
            except Exception:
                pass
            frame = VideoUtils._read_first_video_frame_bgr(file_path)
            if frame is not None:
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
        Crop to square (bottom-cropped for tall images) and resize longest edge.

        Accepts either a PIL Image or a numpy array (OpenCV BGR image).
        """
        try:
            if isinstance(img, Image.Image):
                pil_img = img.copy()
            else:
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
            return ImageUtils._encode_thumbnail(pil_img)
        except Exception as e:
            logger.error(f"Error generating thumbnail bytes: {e}")
            return None

    @staticmethod
    def _calculate_sha256_digest(
        file_size: int,
        read_chunk,
        source_label: Optional[str] = None,
    ) -> str:
        """Compute a SHA-256 digest by either reading the whole file or sampling it."""
        chunk_size = 8192
        sample_count = 8
        whole_file_threshold = 128 * 1024

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
        """Compute a content hash for a file on disk."""
        file_size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:

            def _read_chunk(offset, size):
                f.seek(offset)
                return f.read(size)

            return ImageUtils._calculate_sha256_digest(
                file_size=file_size,
                read_chunk=_read_chunk,
                source_label=file_path,
            )

    @staticmethod
    def calculate_hash_from_bytes(image_bytes: bytes) -> str:
        """Compute a content hash for raw image bytes."""
        file_size = len(image_bytes)

        def _read_chunk(offset, size):
            return image_bytes[offset : offset + size]

        return ImageUtils._calculate_sha256_digest(
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
        created_at = ImageUtils.extract_created_at_from_metadata(
            image_bytes, fallback_file_path=source_file_path
        )
        return ImageUtils.create_picture_from_bytes(
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
        """Create a Picture from raw bytes, deriving metadata and saving the file."""
        if not pixel_sha:
            pixel_sha = ImageUtils.calculate_hash_from_bytes(image_bytes)

        inferred_ext = ""
        if picture_uuid:
            inferred_ext = os.path.splitext(str(picture_uuid))[1].lower().lstrip(".")

        img_format = None
        width = height = None
        thumbnail_bytes = None
        is_video = False
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                img_format = img.format or "PNG"
                width, height = img.size
                thumbnail_bytes = ImageUtils.generate_thumbnail_bytes(img)
        except Exception:
            is_video = True

        if not is_video and (
            thumbnail_bytes is None or width is None or height is None
        ):
            raise ValueError("Failed to generate thumbnail for image bytes")

        if is_video:
            # Use the real extension as the temp-file suffix so that cv2 picks
            # the correct demuxer/codec (e.g. QuickTime for .mov).
            video_suffix = f".{inferred_ext}" if inferred_ext else ".mp4"
            tmp_path = None
            cap = None
            try:
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=video_suffix
                ) as tmp:
                    tmp.write(image_bytes)
                    tmp_path = tmp.name
                cap = cv2.VideoCapture(tmp_path)
                ret, frame = cap.read()
                if not ret:
                    logger.error("Could not read first frame from video for thumbnail.")
                    raise ValueError("Failed to read first frame from video")
                height, width = frame.shape[:2]
                thumbnail_bytes = ImageUtils.generate_thumbnail_bytes(frame)
                if thumbnail_bytes is None:
                    raise ValueError("Failed to generate thumbnail for video")
            finally:
                if cap is not None:
                    cap.release()
                if tmp_path is not None and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError as rm_err:
                        logger.warning(
                            "Failed to remove video temp file %s: %s", tmp_path, rm_err
                        )
            if inferred_ext:
                img_format = inferred_ext.upper()
            else:
                img_format = "MP4"

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
            saved_thumb = ImageUtils.write_thumbnail_bytes(
                image_root_path, file_name, thumbnail_bytes
            )
            if not saved_thumb:
                logger.warning("Failed to persist thumbnail for %s", file_name)

        if not created_at:
            created_at = ImageUtils.extract_created_at_from_metadata(
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
    def cosine_similarity(a: bytes, b: bytes) -> float:
        """Compute cosine similarity between two embedding byte-strings."""
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

        Returns a 1-D np.ndarray of similarities scaled to [0, 1].
        """
        arr_a = np.stack(arr_a_list)
        arr_b = np.stack(arr_b_list)
        arr_a_norm = arr_a / np.linalg.norm(arr_a, axis=1, keepdims=True)
        arr_b_norm = arr_b / np.linalg.norm(arr_b, axis=1, keepdims=True)
        sims = np.sum(arr_a_norm * arr_b_norm, axis=1)
        sims = 0.5 * (sims + 1.0)
        return sims
