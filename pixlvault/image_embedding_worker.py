import time
import os
import requests
import torch
import torch.nn as nn
import numpy as np
from PIL import Image
from collections import defaultdict
from sqlalchemy import or_, func
from sqlmodel import Session, select
from typing import Optional

from pixlvault.database import DBPriority
from pixlvault.db_models import Picture, PictureLikenessQueue
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.pixl_logging import get_logger
from pixlvault.picture_utils import PictureUtils
from pixlvault.picture_tagger import CLIP_MODEL_NAME

logger = get_logger(__name__)


class ImageEmbeddingWorker(BaseWorker):
    """
    Worker for generating image embeddings (CLIP) for pictures,
    and calculating aesthetic scores.
    """

    BATCH_SIZE = 32

    # LAION Aesthetic Predictor weights (V2)
    # Using the improved predictors trained on SAC+Logos+AVA
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = None
        self.aesthetic_model = None
        self._aesthetic_config = self.AESTHETIC_MODELS.get(CLIP_MODEL_NAME)
        self._aesthetic_disabled = self._aesthetic_config is None

    def worker_type(self) -> WorkerType:
        return WorkerType.IMAGE_EMBEDDING

    def _count_remaining(self, session: Session):
        """Count all pictures needing embeddings or aesthetic scores."""
        if self._aesthetic_disabled:
            condition = Picture.image_embedding.is_(None)
        else:
            from sqlalchemy import or_

            condition = or_(
                Picture.image_embedding.is_(None),
                Picture.aesthetic_score.is_(None),
            )
        stmt = select(func.count()).select_from(Picture).where(condition)
        result = session.exec(stmt).one()
        if isinstance(result, tuple):
            return result[0]
        return result or 0

    @staticmethod
    def _count_total_pictures(session: Session) -> int:
        result = session.exec(select(func.count()).select_from(Picture)).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    def _build_failure_updates(self, pids: set[int]):
        empty_emb = np.array([], dtype=np.float32).tobytes()
        score = None if self._aesthetic_disabled else -1.0
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

    def _ensure_model(self):
        if self.aesthetic_model is not None:
            return
        if self._aesthetic_disabled:
            return

        if CLIP_MODEL_NAME not in self.AESTHETIC_SUPPORTED_CLIP:
            logger.info(
                "ImageEmbeddingWorker: Aesthetic model disabled for CLIP model '%s'.",
                CLIP_MODEL_NAME,
            )
            self._aesthetic_disabled = True
            return

        config = self._aesthetic_config
        if not config:
            logger.info(
                "ImageEmbeddingWorker: No aesthetic model config for CLIP model '%s'.",
                CLIP_MODEL_NAME,
            )
            self._aesthetic_disabled = True
            return

        try:
            # Download if missing
            model_path = config["path"]
            model_url = config["url"]
            model_dim = config["dim"]

            if not os.path.exists(model_path):
                logger.info("Downloading aesthetic model from %s...", model_url)
                response = requests.get(model_url, timeout=30)
                response.raise_for_status()
                os.makedirs(os.path.dirname(model_path), exist_ok=True)
                with open(model_path, "wb") as f:
                    f.write(response.content)

            # Load weights
            state_dict = torch.load(model_path, map_location="cpu")
            self.aesthetic_model = nn.Linear(model_dim, 1)
            self.aesthetic_model.load_state_dict(state_dict)
            self.aesthetic_model.eval()

            # Move to same device as CLIP if possible
            if self._picture_tagger and getattr(
                self._picture_tagger, "_clip_device", None
            ):
                self.aesthetic_model = self.aesthetic_model.to(
                    self._picture_tagger._clip_device
                )
            # If using local model, it's usually on CPU for SentenceTransformer by default unless configured otherwise

            logger.info("ImageEmbeddingWorker: Aesthetic model loaded.")

        except Exception as e:
            logger.error(f"ImageEmbeddingWorker: Failed to load aesthetic model: {e}")
            self.aesthetic_model = None
            self._aesthetic_disabled = True

    def _run(self):
        logger.info("ImageEmbeddingWorker: Started.")

        while not self._stop.is_set():
            try:
                # Log how many images remain to be processed
                remaining = self._db.run_immediate_read_task(self._count_remaining)
                total_pics = self._db.run_immediate_read_task(
                    self._count_total_pictures
                )
                total = max(int(total_pics or 0), 0)
                remaining_count = max(int(remaining or 0), 0)
                self._set_progress(
                    label="image_embeddings",
                    current=max(total - remaining_count, 0),
                    total=total,
                )
                logger.debug(
                    f"ImageEmbeddingWorker: {remaining} images remain to be processed."
                )

                # Find pictures without image_embedding OR without aesthetic_score
                batch = self._db.run_immediate_read_task(self._fetch_work)

                if not batch:
                    logger.debug(
                        "ImageEmbeddingWorker: No pictures need embeddings/aesthetic. Sleeping..."
                    )
                    self._wait()
                    continue

                self._ensure_model()

                logger.debug(f"ImageEmbeddingWorker: Processing {len(batch)} pictures.")

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
                                flat_hashes.extend(
                                    [self._compute_dhash(img) for img in pil_imgs]
                                )
                                flat_pids.extend([pid] * len(pil_imgs))

                        else:
                            try:
                                img = Image.open(full_path)
                            except Exception as e:
                                logger.error(
                                    f"ImageEmbeddingWorker: PIL failed to open {file_path}: {e}"
                                )
                                failed_files.append(file_path)
                                continue

                            if img.mode != "RGB":
                                img = img.convert("RGB")
                            flat_images.append(img)
                            flat_hashes.append(self._compute_dhash(img))
                            flat_pids.append(pid)

                    except Exception as e:
                        logger.error(
                            f"ImageEmbeddingWorker: Error loading {file_path}: {e}"
                        )
                        failed_files.append(file_path)

                if not flat_images:
                    if not failed_files:
                        failed_files = [batch_files[pid] for pid in batch_pids]
                    failure_updates = self._build_failure_updates(batch_pids)
                    updated_ids = self._db.run_task(
                        self._save_results, failure_updates, priority=DBPriority.LOW
                    )
                    if updated_ids:
                        self._notify_ids_processed(
                            [
                                (Picture, pid, "image_embedding", None)
                                for pid in updated_ids
                            ]
                        )
                    logger.warning(
                        "ImageEmbeddingWorker: No images loaded for batch. Marked %s pictures as failed.",
                        len(batch_pids),
                    )
                    logger.warning(
                        f"ImageEmbeddingWorker: Failed to process {len(failed_files)} files in this batch: {failed_files}"
                    )
                    continue

                embeddings = None

                # 1. Try using PictureTagger's loaded CLIP model
                if self._picture_tagger and getattr(
                    self._picture_tagger, "_clip_model", None
                ):
                    try:
                        # open_clip expects images as tensors
                        # We can use the preprocess from PictureTagger
                        preprocess = self._picture_tagger._clip_preprocess
                        device = self._picture_tagger._clip_device

                        image_tensors = torch.stack(
                            [preprocess(img) for img in flat_images]
                        ).to(device)
                        # Convert to fp16 if using CUDA (to match model weights)
                        if device == "cuda":
                            image_tensors = image_tensors.half()

                        with torch.no_grad():
                            features = self._picture_tagger._clip_model.encode_image(
                                image_tensors
                            )
                            features /= features.norm(dim=-1, keepdim=True)
                            embeddings = features.cpu().numpy()
                    except Exception as e:
                        logger.error(
                            f"ImageEmbeddingWorker: Failed to use PictureTagger CLIP model: {e}"
                        )
                        embeddings = None

                # 2. Fallback to local SentenceTransformer model
                if embeddings is None and self.model:
                    try:
                        embeddings = self.model.encode(
                            flat_images,
                            batch_size=self.BATCH_SIZE,
                            convert_to_numpy=True,
                            _embeddings=True,
                        )
                    except Exception as e:
                        logger.error(
                            f"ImageEmbeddingWorker: Failed to use local CLIP model: {e}"
                        )

                if embeddings is None:
                    if not failed_files:
                        failed_files = [batch_files[pid] for pid in batch_pids]
                    failure_updates = self._build_failure_updates(batch_pids)
                    updated_ids = self._db.run_task(
                        self._save_results, failure_updates, priority=DBPriority.LOW
                    )
                    if updated_ids:
                        self._notify_ids_processed(
                            [
                                (Picture, pid, "image_embedding", None)
                                for pid in updated_ids
                            ]
                        )
                    logger.error(
                        "ImageEmbeddingWorker: No embeddings generated. Marked %s pictures as failed.",
                        len(batch_pids),
                    )
                    logger.warning(
                        f"ImageEmbeddingWorker: Failed to process {len(failed_files)} files in this batch: {failed_files}"
                    )
                    continue

                # Calculate Aesthetic Scores
                aesthetic_scores = []
                if self.aesthetic_model is not None:
                    try:
                        with torch.no_grad():
                            emb_tensor = torch.from_numpy(embeddings).float()

                            # Ensure tensor is on same device as aesthetic model
                            if next(self.aesthetic_model.parameters()).is_cuda:
                                emb_tensor = emb_tensor.to(
                                    next(self.aesthetic_model.parameters()).device
                                )

                            scores = self.aesthetic_model(emb_tensor).squeeze()
                            if scores.ndim == 0:
                                scores = scores.unsqueeze(0)
                            scores = scores.cpu().numpy()

                            # Handle single item case (scalar)
                            if scores.ndim == 0:
                                scores = [float(scores)]
                            aesthetic_scores = scores
                    except Exception as e:
                        logger.error(
                            f"ImageEmbeddingWorker: Aesthetic scoring failed: {e}"
                        )
                        aesthetic_scores = []

                # Group by PID and average
                pid_updates = defaultdict(lambda: {"embs": [], "scores": []})
                for pid, emb, score in zip(
                    flat_pids,
                    embeddings,
                    aesthetic_scores
                    if len(aesthetic_scores)
                    else [None] * len(embeddings),
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

                    if len(embs) == 1:
                        final_emb = embs[0]
                    else:
                        avg = np.mean(embs, axis=0)
                        final_emb = avg

                    norm = np.linalg.norm(final_emb)
                    if norm > 0:
                        final_emb = final_emb / norm

                    final_score = None
                    if scores:
                        final_score = float(np.mean(scores))

                    emb_bytes = np.asarray(final_emb, dtype=np.float32).tobytes()
                    updates.append((pid, emb_bytes, final_score, data.get("phash")))

                processed_pids = set(pid_updates.keys())
                failed_pids = batch_pids - processed_pids
                if failed_pids:
                    updates.extend(self._build_failure_updates(failed_pids))
                    if not failed_files:
                        failed_files = [batch_files[pid] for pid in failed_pids]

                updated_ids = self._db.run_task(
                    self._save_results, updates, priority=DBPriority.LOW
                )
                if updated_ids:
                    self._notify_ids_processed(
                        [(Picture, pid, "image_embedding", None) for pid in updated_ids]
                    )

                logger.debug(
                    "ImageEmbeddingWorker: Processed %s pictures (embeddings%s).",
                    len(pid_updates),
                    " + aesthetic" if self.aesthetic_model is not None else "",
                )

                if failed_pids:
                    logger.warning(
                        "ImageEmbeddingWorker: Marked %s pictures as failed.",
                        len(failed_pids),
                    )

                if failed_files:
                    logger.warning(
                        f"ImageEmbeddingWorker: Failed to process {len(failed_files)} files in this batch: {failed_files}"
                    )

            except Exception as e:
                logger.error(f"ImageEmbeddingWorker: Error in loop: {e}")
                time.sleep(5)

        logger.info("ImageEmbeddingWorker: Stopped.")

    def _fetch_work(self, session: Session):
        """Fetch a batch of pictures that need embeddings or aesthetics."""
        if self._aesthetic_disabled:
            condition = Picture.image_embedding.is_(None)
        else:
            condition = or_(
                Picture.image_embedding.is_(None),
                Picture.aesthetic_score.is_(None),
            )

        stmt = (
            select(Picture.id, Picture.file_path)
            .where(condition)
            .limit(self.BATCH_SIZE)
        )
        results = session.exec(stmt).all()
        return results

    def _save_results(self, session: Session, updates):
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
