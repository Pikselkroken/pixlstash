import os
import time
from collections import defaultdict
from typing import Optional

import numpy as np
import requests
import torch
import torch.nn as nn
from PIL import Image
from sqlalchemy import func, or_
from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.db_models import Picture, PictureLikenessQueue
from pixlvault.picture_tagger import CLIP_MODEL_NAME
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger
from pixlvault.tasks.base_task import BaseTask


logger = get_logger(__name__)


class ImageEmbeddingTask(BaseTask):
    """Task for generating image embeddings and aesthetic scores for a batch."""

    BATCH_SIZE = 32
    BACKEND_ERROR_LOG_INTERVAL_SECONDS = 60

    AESTHETIC_MODELS = {
        "ViT-L-14": {
            "url": "https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac%2Blogos%2Bava1-l14-linearMSE.pth",
            "path": "wd14_tagger_model/sac+logos+ava1-l14-linearMSE.pth",
            "dim": 768,
        },
        "ViT-B-32": {
            "url": "https://github.com/LAION-AI/aesthetic-predictor/blob/main/sa_0_4_vit_b_32_linear.pth?raw=true",
            "path": "wd14_tagger_model/sa_0_4_vit_b_32_linear.pth",
            "dim": 512,
        },
    }
    AESTHETIC_SUPPORTED_CLIP = set(AESTHETIC_MODELS.keys())

    _aesthetic_model = None
    _aesthetic_disabled: Optional[bool] = None

    def __init__(self, database, picture_tagger):
        super().__init__(
            task_type="ImageEmbeddingTask",
            params={
                "batch_size": self.BATCH_SIZE,
            },
        )
        self._db = database
        self._picture_tagger = picture_tagger
        self.model = None
        self._last_backend_error_log_at = 0.0

        if ImageEmbeddingTask._aesthetic_disabled is None:
            ImageEmbeddingTask._aesthetic_disabled = self._aesthetic_config() is None

    @classmethod
    def _aesthetic_config(cls):
        return cls.AESTHETIC_MODELS.get(CLIP_MODEL_NAME)

    @classmethod
    def _is_aesthetic_disabled(cls):
        if cls._aesthetic_disabled is None:
            cls._aesthetic_disabled = cls._aesthetic_config() is None
        return bool(cls._aesthetic_disabled)

    @classmethod
    def count_remaining(
        cls, session: Session, aesthetic_disabled: Optional[bool] = None
    ) -> int:
        """Count pictures needing image embedding or aesthetic score work."""
        if aesthetic_disabled is None:
            aesthetic_disabled = cls._is_aesthetic_disabled()

        missing_embedding = or_(
            Picture.image_embedding.is_(None),
            func.length(Picture.image_embedding) == 0,
        )
        if aesthetic_disabled:
            condition = missing_embedding
        else:
            condition = or_(
                missing_embedding,
                Picture.aesthetic_score.is_(None),
            )
        stmt = select(func.count()).select_from(Picture).where(condition)
        result = session.exec(stmt).one()
        if isinstance(result, tuple):
            return result[0]
        return result or 0

    @classmethod
    def fetch_work(
        cls,
        session: Session,
        aesthetic_disabled: Optional[bool] = None,
        limit: Optional[int] = None,
    ):
        """Fetch pictures needing image embedding or aesthetic score work."""
        if aesthetic_disabled is None:
            aesthetic_disabled = cls._is_aesthetic_disabled()

        missing_embedding = or_(
            Picture.image_embedding.is_(None),
            func.length(Picture.image_embedding) == 0,
        )
        if aesthetic_disabled:
            condition = missing_embedding
        else:
            condition = or_(
                missing_embedding,
                Picture.aesthetic_score.is_(None),
            )

        stmt = select(Picture.id, Picture.file_path).where(condition).limit(
            int(limit or cls.BATCH_SIZE)
        )
        return session.exec(stmt).all()

    @classmethod
    def release_models(cls):
        cls._aesthetic_model = None

    def _build_failure_updates(self, pids: set[int]):
        empty_emb = np.array([], dtype=np.float32).tobytes()
        score = None if self._is_aesthetic_disabled() else -1.0
        return [(pid, empty_emb, score, None) for pid in pids]

    @staticmethod
    def _compute_dhash(image: Image.Image, hash_size: int = 8) -> Optional[str]:
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

    def _ensure_clip_ready(self) -> bool:
        if self._picture_tagger is None:
            logger.error(
                "ImageEmbeddingTask: PictureTagger not available for CLIP embeddings."
            )
            return False

        for attempt in range(1, 4):
            try:
                self._picture_tagger._ensure_clip_ready()
                if (
                    getattr(self._picture_tagger, "_clip_model", None) is not None
                    and getattr(self._picture_tagger, "_clip_preprocess", None)
                    is not None
                ):
                    return True
            except Exception as exc:
                logger.warning(
                    "ImageEmbeddingTask: CLIP init attempt %s/3 failed: %s",
                    attempt,
                    exc,
                )
                if attempt < 3:
                    time.sleep(1.0)

        logger.error(
            "ImageEmbeddingTask: CLIP model unavailable after retries; embeddings cannot be generated."
        )
        return False

    def _ensure_model(self):
        if ImageEmbeddingTask._aesthetic_model is not None:
            return
        if self._is_aesthetic_disabled():
            return

        if CLIP_MODEL_NAME not in self.AESTHETIC_SUPPORTED_CLIP:
            logger.info(
                "ImageEmbeddingTask: Aesthetic model disabled for CLIP model '%s'.",
                CLIP_MODEL_NAME,
            )
            ImageEmbeddingTask._aesthetic_disabled = True
            return

        config = self._aesthetic_config()
        if not config:
            logger.info(
                "ImageEmbeddingTask: No aesthetic model config for CLIP model '%s'.",
                CLIP_MODEL_NAME,
            )
            ImageEmbeddingTask._aesthetic_disabled = True
            return

        try:
            model_path = config["path"]
            model_url = config["url"]
            model_dim = config["dim"]

            if not os.path.exists(model_path):
                logger.info("Downloading aesthetic model from %s...", model_url)
                response = requests.get(model_url, timeout=30)
                response.raise_for_status()
                os.makedirs(os.path.dirname(model_path), exist_ok=True)
                with open(model_path, "wb") as file_handle:
                    file_handle.write(response.content)

            state_dict = torch.load(model_path, map_location="cpu")
            model = nn.Linear(model_dim, 1)
            model.load_state_dict(state_dict)
            model.eval()

            if self._picture_tagger and getattr(self._picture_tagger, "_clip_device", None):
                model = model.to(self._picture_tagger._clip_device)

            ImageEmbeddingTask._aesthetic_model = model
            logger.info("ImageEmbeddingTask: Aesthetic model loaded.")

        except Exception as exc:
            logger.error("ImageEmbeddingTask: Failed to load aesthetic model: %s", exc)
            ImageEmbeddingTask._aesthetic_model = None
            ImageEmbeddingTask._aesthetic_disabled = True

    def _ensure_embedding_backend(self) -> bool:
        clip_model = getattr(self._picture_tagger, "_clip_model", None)
        clip_preprocess = getattr(self._picture_tagger, "_clip_preprocess", None)

        if self._picture_tagger and (clip_model is None or clip_preprocess is None):
            ensure_clip_ready = getattr(self._picture_tagger, "_ensure_clip_ready", None)
            if callable(ensure_clip_ready):
                try:
                    ensure_clip_ready()
                except Exception as exc:
                    now = time.time()
                    if (
                        now - self._last_backend_error_log_at
                        >= self.BACKEND_ERROR_LOG_INTERVAL_SECONDS
                    ):
                        logger.error(
                            "ImageEmbeddingTask: Failed to initialise CLIP backend: %s",
                            exc,
                        )
                        self._last_backend_error_log_at = now

        clip_ready = bool(
            getattr(self._picture_tagger, "_clip_model", None) is not None
            and getattr(self._picture_tagger, "_clip_preprocess", None) is not None
        )
        fallback_ready = self.model is not None

        if clip_ready or fallback_ready:
            return True

        now = time.time()
        if now - self._last_backend_error_log_at >= self.BACKEND_ERROR_LOG_INTERVAL_SECONDS:
            logger.error(
                "ImageEmbeddingTask: No embedding backend available (clip_ready=%s fallback_ready=%s).",
                clip_ready,
                fallback_ready,
            )
            self._last_backend_error_log_at = now
        return False

    def _run_task(self):
        batch = self._db.run_immediate_read_task(
            lambda session: self.fetch_work(session=session)
        )
        if not batch:
            return {"changed_count": 0, "changed": []}

        self._ensure_model()
        if not self._ensure_embedding_backend():
            return {"changed_count": 0, "changed": []}

        flat_images = []
        flat_pids = []
        flat_hashes = []
        failed_files = []
        batch_pids = {pid for pid, _ in batch}
        batch_files = {pid: file_path for pid, file_path in batch}

        for pid, file_path in batch:
            try:
                full_path = os.path.join(self._db.image_root, file_path)

                if PictureUtils.is_video_file(file_path):
                    pil_imgs = PictureUtils.extract_representative_video_frames(
                        full_path, count=3
                    )
                    if pil_imgs:
                        flat_images.extend(pil_imgs)
                        flat_hashes.extend([self._compute_dhash(img) for img in pil_imgs])
                        flat_pids.extend([pid] * len(pil_imgs))
                else:
                    try:
                        img = Image.open(full_path)
                    except Exception as exc:
                        logger.error(
                            "ImageEmbeddingTask: PIL failed to open %s: %s",
                            file_path,
                            exc,
                        )
                        failed_files.append(file_path)
                        continue

                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    flat_images.append(img)
                    flat_hashes.append(self._compute_dhash(img))
                    flat_pids.append(pid)

            except Exception as exc:
                logger.error("ImageEmbeddingTask: Error loading %s: %s", file_path, exc)
                failed_files.append(file_path)

        if not flat_images:
            if not failed_files:
                failed_files = [batch_files[pid] for pid in batch_pids]
            failure_updates = self._build_failure_updates(batch_pids)
            updated_ids = self._db.run_task(
                self._save_results, failure_updates, priority=DBPriority.LOW
            )
            changed = [(Picture, pid, "image_embedding", None) for pid in updated_ids]
            logger.warning(
                "ImageEmbeddingTask: No images loaded for batch. Marked %s pictures as failed.",
                len(batch_pids),
            )
            logger.warning(
                "ImageEmbeddingTask: Failed to process %s files in this batch: %s",
                len(failed_files),
                failed_files,
            )
            return {"changed_count": len(changed), "changed": changed}

        embeddings = None
        clip_ready = self._ensure_clip_ready()

        if clip_ready:
            try:
                preprocess = self._picture_tagger._clip_preprocess
                device = self._picture_tagger._clip_device

                image_tensors = torch.stack([preprocess(img) for img in flat_images]).to(
                    device
                )
                if device == "cuda":
                    image_tensors = image_tensors.half()

                with torch.no_grad():
                    features = self._picture_tagger._clip_model.encode_image(image_tensors)
                    features /= features.norm(dim=-1, keepdim=True)
                    embeddings = features.cpu().numpy()
            except Exception as exc:
                logger.error(
                    "ImageEmbeddingTask: Failed to use PictureTagger CLIP model: %s", exc
                )
                embeddings = None

        if embeddings is None and self.model:
            try:
                embeddings = self.model.encode(
                    flat_images,
                    batch_size=self.BATCH_SIZE,
                    convert_to_numpy=True,
                    _embeddings=True,
                )
            except Exception as exc:
                logger.error("ImageEmbeddingTask: Failed to use local CLIP model: %s", exc)

        if embeddings is None:
            logger.error(
                "ImageEmbeddingTask: No embeddings generated for batch of %s pictures (clip_ready=%s fallback_ready=%s).",
                len(batch_pids),
                bool(getattr(self._picture_tagger, "_clip_model", None)),
                bool(self.model),
            )
            logger.warning(
                "ImageEmbeddingTask: Failed to process %s files in this batch: %s",
                len(failed_files),
                failed_files,
            )
            return {"changed_count": 0, "changed": []}

        aesthetic_scores = []
        if ImageEmbeddingTask._aesthetic_model is not None:
            try:
                with torch.no_grad():
                    emb_tensor = torch.from_numpy(embeddings).float()
                    if next(ImageEmbeddingTask._aesthetic_model.parameters()).is_cuda:
                        emb_tensor = emb_tensor.to(
                            next(ImageEmbeddingTask._aesthetic_model.parameters()).device
                        )

                    scores = ImageEmbeddingTask._aesthetic_model(emb_tensor).squeeze()
                    if scores.ndim == 0:
                        scores = scores.unsqueeze(0)
                    scores = scores.cpu().numpy()

                    if scores.ndim == 0:
                        scores = [float(scores)]
                    aesthetic_scores = scores
            except Exception as exc:
                logger.error("ImageEmbeddingTask: Aesthetic scoring failed: %s", exc)
                aesthetic_scores = []

        pid_updates = defaultdict(lambda: {"embs": [], "scores": []})
        for pid, emb, score in zip(
            flat_pids,
            embeddings,
            aesthetic_scores if len(aesthetic_scores) else [None] * len(embeddings),
        ):
            pid_updates[pid]["embs"].append(emb)
            if score is not None:
                pid_updates[pid]["scores"].append(score)

        if flat_hashes:
            for pid, phash in zip(flat_pids, flat_hashes):
                if phash and pid_updates[pid].get("phash") is None:
                    pid_updates[pid]["phash"] = phash

        updates = []
        for pid, data in pid_updates.items():
            embs = data["embs"]
            scores = data["scores"]

            final_emb = embs[0] if len(embs) == 1 else np.mean(embs, axis=0)
            norm = np.linalg.norm(final_emb)
            if norm > 0:
                final_emb = final_emb / norm

            final_score = float(np.mean(scores)) if scores else None
            emb_bytes = np.asarray(final_emb, dtype=np.float32).tobytes()
            updates.append((pid, emb_bytes, final_score, data.get("phash")))

        processed_pids = set(pid_updates.keys())
        failed_pids = batch_pids - processed_pids
        if failed_pids:
            updates.extend(self._build_failure_updates(failed_pids))
            if not failed_files:
                failed_files = [batch_files[pid] for pid in failed_pids]

        updated_ids = self._db.run_task(self._save_results, updates, priority=DBPriority.LOW)
        changed = [(Picture, pid, "image_embedding", None) for pid in updated_ids]

        if failed_pids:
            logger.warning(
                "ImageEmbeddingTask: Marked %s pictures as failed.",
                len(failed_pids),
            )

        if failed_files:
            logger.warning(
                "ImageEmbeddingTask: Failed to process %s files in this batch: %s",
                len(failed_files),
                failed_files,
            )

        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    @staticmethod
    def _save_results(session: Session, updates):
        updated_ids = []
        for pid, emb_bytes, score, phash in updates:
            pic = session.get(Picture, pid)
            if pic:
                pic.image_embedding = emb_bytes
                if score is not None:
                    pic.aesthetic_score = score
                pic.perceptual_hash = phash
                updated_ids.append(pid)
        session.commit()
        if updated_ids:
            PictureLikenessQueue.enqueue(session, updated_ids)
            session.commit()
        return updated_ids
