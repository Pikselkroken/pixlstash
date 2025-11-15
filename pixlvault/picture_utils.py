import cv2
import numpy as np
import os
import hashlib
import uuid

from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from PIL import Image

from pixlvault.logging import get_logger
from pixlvault.picture import PictureModel

logger = get_logger(__name__)


class PictureUtils:
    @staticmethod
    def extract_video_frames(file_path, max_frames=None):
        """
        Extract frames from a video file and return them as PIL Images.
        Args:
            file_path (str): Path to video file.
            max_frames (int, optional): Maximum number of frames to extract.
        Returns:
            List of PIL.Image objects.
        """
        import cv2
        from PIL import Image

        frames = []
        cap = cv2.VideoCapture(file_path)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
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
    def load_and_crop_face_bbox(file_path, bbox):
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
        picture_id: Optional[str] = None,
        character_id: Optional[str] = None,
        pixel_sha: Optional[str] = None,
    ) -> PictureModel:
        """
        Create a Picture from a file path.
        Args:
            image_root_path (str): Root directory to store images.
            source_file_path (str): Path to the source image file.
            picture_id (str): Stable UUID for the picture.
            character_id (Optional[str]): Associated character ID.
            description (Optional[str]): Description of the picture.
        Returns:
            Picture: The created Picture object.
        """
        if not os.path.exists(source_file_path):
            raise ValueError(f"Source file path does not exist: {source_file_path}")
        with open(source_file_path, "rb") as f:
            image_bytes = f.read()
        return PictureUtils.create_picture_from_bytes(
            image_root_path=image_root_path,
            image_bytes=image_bytes,
            picture_id=picture_id,
            character_id=character_id,
            pixel_sha=pixel_sha,
        )

    @staticmethod
    def create_picture_from_bytes(
        image_root_path: str,
        image_bytes: bytes,
        picture_id: Optional[str] = None,
        character_id: Optional[str] = None,
        pixel_sha: Optional[str] = None,
    ) -> PictureModel:
        """
        Create a a Picture from raw bytes. Supports both images and videos.
        Args:
            image_root_path (str): Root directory to store images.
            image_bytes (bytes): Raw bytes of the image or video.
            picture_id (str): Stable UUID for the picture.
            character_id (Optional[str]): Associated character ID.
        Returns:
            Picture: The created Picture object.
        """

        if not pixel_sha:
            pixel_sha = PictureUtils.calculate_hash_from_bytes(image_bytes)

        # Try to detect if this is a video or image
        img_format = None
        width = height = None
        thumbnail_bytes = None
        is_video = False
        # Try image first
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                img_format = img.format or "PNG"
                width, height = img.size
                thumbnail_bytes = PictureUtils.generate_thumbnail_bytes(img)
        except Exception:
            # Not an image, try video
            is_video = True
        if is_video:
            # Write bytes to temp file to read with cv2
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
            img_format = "MP4"  # Default, could be improved by sniffing
            # Remove temp file
            os.remove(tmp_path)

        if not picture_id:
            picture_id = str(uuid.uuid4()) + f".{img_format.lower()}"

        file_path = os.path.join(image_root_path, picture_id)
        if os.path.exists(file_path):
            size_bytes = os.path.getsize(file_path)
        else:
            os.makedirs(image_root_path, exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(image_bytes)
            size_bytes = len(image_bytes)

        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        pic = PictureModel(
            id=picture_id,
            file_path=file_path,
            format=img_format,
            width=width,
            height=height,
            size_bytes=size_bytes,
            created_at=created_at,
            thumbnail=thumbnail_bytes,
            primary_character_id=character_id,
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
