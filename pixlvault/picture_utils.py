import cv2
import numpy as np
import os
import hashlib
import uuid

from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from PIL import Image

from pixlvault.pixl_logging import get_logger
from pixlvault.db_models.picture import Picture

logger = get_logger(__name__)


class PictureUtils:
    @staticmethod
    def is_video_file(file_path: str) -> bool:
        """
        Returns True if the file is a supported video format.
        """
        ext = os.path.splitext(file_path)[1].lower()
        return ext in [".mp4", ".webm", ".avi", ".mov", ".mkv"]

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
                    for tag in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
                        val = exif_dict["0th"].get(
                            piexif.ImageIFD.__dict__.get(tag)
                        ) or exif_dict["Exif"].get(piexif.ExifIFD.__dict__.get(tag))
                        if val:
                            date_str = val.decode() if isinstance(val, bytes) else val
                            break
                    if date_str:
                        # EXIF format: 'YYYY:MM:DD HH:MM:SS'
                        try:
                            dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                            return dt.replace(tzinfo=timezone.utc)
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
    def extract_video_frames(file_path, max_frames=None, specific_frame=None):
        """
        Extract frames from a video file and return them as PIL Images.
        Args:
            file_path (str): Path to video file.
            max_frames (int, optional): Maximum number of frames to extract.
            specific_frame (int, optional): If set, only extract this frame index (0-based).
        Returns:
            List of PIL.Image objects (or single image if specific_frame is set).
        """
        import cv2
        from PIL import Image

        frames = []
        cap = cv2.VideoCapture(file_path)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if specific_frame is not None:
            # Seek to specific frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, specific_frame)
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
            if max_frames is not None and count >= max_frames:
                break
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
            import cv2

            cap = cv2.VideoCapture(file_path)
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return frame_rgb
            else:
                raise ValueError("Could not read image or first frame from video.")
        except Exception as e:
            logger.error(f"Failed to load image at {file_path} for quality worker: {e}")
            return None

    @staticmethod
    def generate_thumbnail_bytes(img, size=(256, 256)) -> Optional[bytes]:
        """
        Resize image so the longest edge is 256px, preserve aspect ratio, no padding.
        Accepts either a PIL Image or a numpy array (OpenCV image).
        """
        try:
            if isinstance(img, Image.Image):
                pil_img = img.copy()
            else:
                # Assume numpy array (OpenCV image, BGR)
                pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            max_edge = max(pil_img.width, pil_img.height)
            if max_edge > size[0]:
                scale = size[0] / max_edge
                new_w = int(round(pil_img.width * scale))
                new_h = int(round(pil_img.height * scale))
                pil_img = pil_img.resize((new_w, new_h), resample=Image.LANCZOS)
            buf = BytesIO()
            pil_img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Error generating thumbnail bytes: {e}")
            return None

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
    def calculate_hash_from_file_path(file_path: str) -> str:
        CHUNK_SIZE = 8192
        N = 8
        WHOLE_FILE_THRESHOLD = 128 * 1024  # 128KB
        file_size = os.path.getsize(file_path)
        sha256 = hashlib.sha256()
        if file_size <= WHOLE_FILE_THRESHOLD:
            with open(file_path, "rb") as f:
                while chunk := f.read(CHUNK_SIZE):
                    sha256.update(chunk)
            digest = sha256.hexdigest()
            logger.debug(f"WHOLE: {file_path} size={file_size} hash={digest}")
            return digest
        # For larger files, sample N evenly spaced blocks
        offsets = [int(i * (file_size - CHUNK_SIZE) / (N - 1)) for i in range(N)]
        with open(file_path, "rb") as f:
            for offset in offsets:
                f.seek(offset)
                chunk = f.read(CHUNK_SIZE)
                if chunk:
                    sha256.update(chunk)
            digest = sha256.hexdigest()
            logger.debug(f"SAMPLED: {file_path} size={file_size} hash={digest}")
            return digest

    @staticmethod
    def calculate_hash_from_bytes(image_bytes: bytes) -> str:
        CHUNK_SIZE = 8192
        N = 8
        WHOLE_FILE_THRESHOLD = 128 * 1024  # 128KB
        file_size = len(image_bytes)
        sha256 = hashlib.sha256()
        if file_size <= WHOLE_FILE_THRESHOLD:
            for i in range(0, file_size, CHUNK_SIZE):
                chunk = image_bytes[i : i + CHUNK_SIZE]
                sha256.update(chunk)
            digest = sha256.hexdigest()
            logger.debug(f"WHOLE: size={file_size} hash={digest}")
            return digest
        # For larger files, sample N evenly spaced blocks
        offsets = [int(i * (file_size - CHUNK_SIZE) / (N - 1)) for i in range(N)]
        for offset in offsets:
            chunk = image_bytes[offset : offset + CHUNK_SIZE]
            if chunk:
                sha256.update(chunk)
        digest = sha256.hexdigest()
        logger.debug(f"SAMPLED: hash={digest}")
        return digest

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
        if is_video:
            import tempfile

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp.write(image_bytes)
                tmp_path = tmp.name
            cap = cv2.VideoCapture(tmp_path)
            ret, frame = cap.read()
            if not ret:
                logger.error("Could not read first frame from video for thumbnail.")
            else:
                height, width = frame.shape[:2]
                thumbnail_bytes = PictureUtils.generate_thumbnail_bytes(frame)
            cap.release()
            img_format = "MP4"
            os.remove(tmp_path)

        if not picture_uuid:
            picture_uuid = str(uuid.uuid4()) + f".{img_format.lower()}"

        file_path = os.path.join(image_root_path, picture_uuid)
        if os.path.exists(file_path):
            size_bytes = os.path.getsize(file_path)
        else:
            os.makedirs(image_root_path, exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(image_bytes)
            size_bytes = len(image_bytes)

        if not created_at:
            created_at = PictureUtils.extract_created_at_from_metadata(
                image_bytes, fallback_file_path=file_path
            )
        if not created_at:
            created_at = datetime.now(timezone.utc)

        pic = Picture(
            file_path=file_path,
            format=img_format,
            width=width,
            height=height,
            size_bytes=size_bytes,
            created_at=created_at,
            thumbnail=thumbnail_bytes,
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
