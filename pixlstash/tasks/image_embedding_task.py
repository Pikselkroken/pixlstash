import os
import threading
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

from platformdirs import user_data_dir

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureLikenessQueue
from pixlstash.tagger_plugins.clip_service import CLIP_MODEL_NAME

_MODEL_DIR = os.path.join(user_data_dir("pixlstash"), "downloaded_models")
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority


logger = get_logger(__name__)


class ImageEmbeddingTask(BaseTask):
    """Task for generating image embeddings and aesthetic scores for one batch."""

    BATCH_SIZE = 128
    BACKEND_ERROR_LOG_INTERVAL_SECONDS = 60

    AESTHETIC_MODELS = {
        "ViT-L-14": {
            "url": "https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac%2Blogos%2Bava1-l14-linearMSE.pth",
            "path": os.path.join(_MODEL_DIR, "sac+logos+ava1-l14-linearMSE.pth"),
            "dim": 768,
        },
        "ViT-B-32": {
            "url": "https://raw.githubusercontent.com/LAION-AI/aesthetic-predictor/main/sa_0_4_vit_b_32_linear.pth",
            "path": os.path.join(_MODEL_DIR, "sa_0_4_vit_b_32_linear.pth"),
            "dim": 512,
        },
    }
    AESTHETIC_SUPPORTED_CLIP = set(AESTHETIC_MODELS.keys())

    _aesthetic_model = None
    _aesthetic_disabled: Optional[bool] = None

    def __init__(self, database, clip_workflow, batch: list):
        """
        Args:
            clip_workflow: A :class:`~pixlstash.inference.workflows.clip_embedding.ClipEmbeddingWorkflow`
                instance used for CLIP inference, or ``None`` when no tagger is
                available.
            batch: List of ``(picture_id, file_path)`` pairs pre-fetched by the
                finder.  Images are loaded from disk in ``on_queued()`` so that
                I/O overlaps with the previous task's GPU inference.
        """
        picture_ids = [pid for pid, _ in (batch or [])]
        super().__init__(
            task_type="ImageEmbeddingTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._clip_workflow = clip_workflow
        self._batch = batch or []
        self.model = None
        self._last_backend_error_log_at = 0.0

        # Preloading state — images loaded from disk in on_queued() so I/O
        # overlaps with the previous task's GPU inference.
        self._preloaded_images: list = []  # list of (pid, file_path, PIL.Image)
        self._preload_lock = threading.Lock()
        self._preload_thread: threading.Thread | None = None
        self._preload_cancel = threading.Event()

        if ImageEmbeddingTask._aesthetic_disabled is None:
            ImageEmbeddingTask._aesthetic_disabled = self._aesthetic_config() is None

    def on_queued(self) -> None:
        if self._preload_thread is not None and self._preload_thread.is_alive():
            return
        self._preload_cancel.clear()
        self._preload_thread = threading.Thread(
            target=self._preload_images_task,
            name=f"EmbedPreload-{self.id[:8]}",
            daemon=True,
        )
        self._preload_thread.start()

    def on_cancel(self) -> None:
        self._preload_cancel.set()
        if self._preload_thread is not None:
            self._preload_thread.join(timeout=10)

    def _preload_images_task(self) -> None:
        preloaded = []
        for pid, file_path in self._batch:
            if self._preload_cancel.is_set():
                break
            try:
                full_path = os.path.join(self._db.image_root, file_path)
                if VideoUtils.is_video_file(file_path):
                    frames = VideoUtils.extract_representative_video_frames(
                        full_path, count=3
                    )
                    for frame in frames:
                        preloaded.append((pid, file_path, frame.convert("RGB")))
                else:
                    img = Image.open(full_path).convert("RGB")
                    preloaded.append((pid, file_path, img))
            except Exception as exc:
                logger.debug("EmbedPreload: failed to load %s: %s", file_path, exc)
                preloaded.append((pid, file_path, None))
        with self._preload_lock:
            self._preloaded_images = preloaded
        logger.debug(
            "[EMBED_PRELOAD] task_id=%s preloaded=%d/%d",
            self.id,
            sum(1 for _, _, img in preloaded if img is not None),
            len(self._batch),
        )

    def _wait_for_preload(self) -> list:
        if self._preload_thread is not None:
            self._preload_thread.join()
        with self._preload_lock:
            return list(self._preloaded_images)

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.MEDIUM

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def estimated_vram_mb(self) -> int:
        if self._clip_workflow is None:
            return 0
        try:
            return max(0, self._clip_workflow.estimated_vram_mb(len(self._batch)))
        except Exception:
            return 0

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

        stmt = (
            select(Picture.id, Picture.file_path)
            .where(condition)
            .limit(int(limit or cls.BATCH_SIZE))
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
        if self._clip_workflow is None:
            logger.error(
                "ImageEmbeddingTask: ClipEmbeddingWorkflow not available for CLIP embeddings."
            )
            return False

        for attempt in range(1, 4):
            try:
                self._clip_workflow.ensure_ready()
                if self._clip_workflow.is_ready():
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
                os.makedirs(os.path.dirname(model_path), exist_ok=True)
                response = requests.get(model_url, timeout=30)
                response.raise_for_status()
                with open(model_path, "wb") as file_handle:
                    file_handle.write(response.content)

            state_dict = torch.load(model_path, map_location="cpu")
            model = nn.Linear(model_dim, 1)
            model.load_state_dict(state_dict)
            model.eval()

            if self._clip_workflow is not None:
                model = model.to(self._clip_workflow.device)

            ImageEmbeddingTask._aesthetic_model = model
            logger.info("ImageEmbeddingTask: Aesthetic model loaded.")

        except Exception as exc:
            logger.error("ImageEmbeddingTask: Failed to load aesthetic model: %s", exc)
            ImageEmbeddingTask._aesthetic_model = None
            ImageEmbeddingTask._aesthetic_disabled = True

    def _ensure_embedding_backend(self) -> bool:
        if self._clip_workflow is not None and not self._clip_workflow.is_ready():
            try:
                self._clip_workflow.ensure_ready()
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

        clip_ready = bool(self._clip_workflow is not None and self._clip_workflow.is_ready())
        fallback_ready = self.model is not None

        if clip_ready or fallback_ready:
            return True

        now = time.time()
        if (
            now - self._last_backend_error_log_at
            >= self.BACKEND_ERROR_LOG_INTERVAL_SECONDS
        ):
            logger.error(
                "ImageEmbeddingTask: No embedding backend available (clip_ready=%s fallback_ready=%s).",
                clip_ready,
                fallback_ready,
            )
            self._last_backend_error_log_at = now
        return False

    def _run_task(self):
        self._ensure_model()
        if not self._ensure_embedding_backend():
            return {"changed_count": 0, "changed": []}

        if not self._batch:
            return {"changed_count": 0, "changed": []}

        preloaded = self._wait_for_preload()
        changed = self._process_preloaded(preloaded)
        return {"changed_count": len(changed), "changed": changed}

    def _process_preloaded(self, preloaded: list) -> list:
        """Process a list of ``(pid, file_path, PIL.Image | None)`` triples.

        Returns a list of (model, pic_id, field, value) change tuples.
        """
        flat_images = []
        flat_pids = []
        flat_hashes = []
        failed_pids = set()
        batch_pids = {pid for pid, _, _ in preloaded}
        batch_files = {pid: fp for pid, fp, _ in preloaded}

        for pid, file_path, img in preloaded:
            if img is None:
                failed_pids.add(pid)
                continue
            flat_images.append(img)
            flat_hashes.append(self._compute_dhash(img))
            flat_pids.append(pid)

        if not flat_images:
            failure_updates = self._build_failure_updates(batch_pids)
            updated_ids = self._db.run_task(
                self._save_results, failure_updates, priority=DBPriority.LOW
            )
            changed = [(Picture, pid, "image_embedding", None) for pid in updated_ids]
            logger.warning(
                "ImageEmbeddingTask: No images loaded for batch. Marked %s pictures as failed.",
                len(batch_pids),
            )
            if failed_pids:
                logger.warning(
                    "ImageEmbeddingTask: Failed to load %d pictures: %s",
                    len(failed_pids),
                    [batch_files.get(pid) for pid in failed_pids],
                )
            return changed

        embeddings = None
        clip_ready = self._ensure_clip_ready()

        if clip_ready:
            try:
                embeddings = self._clip_workflow.encode_images(flat_images)
            except Exception as exc:
                logger.error(
                    "ImageEmbeddingTask: Failed to use CLIP workflow model: %s",
                    exc,
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
                logger.error(
                    "ImageEmbeddingTask: Failed to use local CLIP model: %s", exc
                )

        aesthetic_scores = []
        if ImageEmbeddingTask._aesthetic_model is not None and embeddings is not None:
            try:
                with torch.no_grad():
                    model_param = next(ImageEmbeddingTask._aesthetic_model.parameters())
                    emb_tensor = torch.from_numpy(embeddings).to(
                        dtype=model_param.dtype,
                        device=model_param.device,
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

        if embeddings is None:
            logger.error(
                "ImageEmbeddingTask: No embeddings generated for batch of %s pictures (clip_ready=%s fallback_ready=%s).",
                len(batch_pids),
                bool(self._clip_workflow is not None and self._clip_workflow.is_ready()),
                bool(self.model),
            )
            return []

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

        updated_ids = self._db.run_task(
            self._save_results, updates, priority=DBPriority.LOW
        )
        changed = [(Picture, pid, "image_embedding", None) for pid in updated_ids]

        if failed_pids:
            failed_files = [batch_files.get(pid) for pid in failed_pids]
            logger.warning(
                "ImageEmbeddingTask: Marked %s pictures as failed: %s",
                len(failed_pids),
                failed_files,
            )

        return changed

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
