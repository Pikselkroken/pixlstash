import cv2
from PIL import Image
import numpy as np
import os
import torch

from pixlvault.pixl_logging import get_logger

IMAGE_SIZE = 448

logger = get_logger(__name__)


class ImageLoadingDatasetPrepper(torch.utils.data.Dataset):
    def __init__(self, image_paths):
        self.images = image_paths

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):
        img_path = str(self.images[idx])
        ext = os.path.splitext(img_path)[1].lower()
        if ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"]:
            try:
                image = Image.open(img_path).convert("RGB")
                image = self._preprocess_image(image)
            except Exception as e:
                logger.error(f"Could not load image path: {img_path}, error: {e}")
                return None
            return (image, img_path)
        elif ext in [".mp4", ".avi", ".mov", ".mkv"]:
            # Extract only the first frame from video and treat it as one image
            try:
                import cv2

                cap = cv2.VideoCapture(img_path)
                if not cap.isOpened():
                    logger.error(f"Could not open video file: {img_path}")
                    return None
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if frame_count < 1:
                    logger.error(f"No frames found in video: {img_path}")
                    cap.release()
                    return None
                frame_indices = [0]
                images = []
                for idx_frame in frame_indices:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, idx_frame)
                    ret, frame = cap.read()
                    if not ret:
                        logger.error(
                            f"Could not read frame {idx_frame} from video: {img_path}"
                        )
                        continue
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil_img = Image.fromarray(frame_rgb)
                    prepped = self._preprocess_image(pil_img)
                    images.append((prepped, f"{img_path}#frame{idx_frame}"))
                cap.release()
                if not images:
                    logger.error(f"No frames extracted from video: {img_path}")
                    return None
                # Return a single (image, img_path#frameX) tuple list for compatibility
                return images
            except Exception as e:
                logger.error(f"Could not process video file: {img_path}, error: {e}")
                return None
        else:
            logger.error(f"Unsupported file extension for tagging: {img_path}")
            return None

    @staticmethod
    def _preprocess_image(image, image_size=IMAGE_SIZE):
        image = np.array(image)
        image = image[:, :, ::-1]  # RGB->BGR

        # pad to square
        size = max(image.shape[0:2])
        pad_x = size - image.shape[1]
        pad_y = size - image.shape[0]
        pad_l = pad_x // 2
        pad_t = pad_y // 2
        image = np.pad(
            image,
            ((pad_t, pad_y - pad_t), (pad_l, pad_x - pad_l), (0, 0)),
            mode="constant",
            constant_values=255,
        )

        image = cv2.resize(
            image, (image_size, image_size), interpolation=cv2.INTER_AREA
        )

        image = image.astype(np.float32)
        return image
