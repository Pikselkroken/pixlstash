import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from typing import Optional

import numpy as np
import requests
from PIL import Image
from sqlalchemy import func, or_
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureLikenessQueue
from pixlstash.services.builtin_models import builtin_model_dir
from pixlstash.tagger_plugins.clip_service import CLIP_MODEL_NAME

from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority


logger = get_logger(__name__)


class ImageEmbeddingTask(BaseTask):
    """Task for generating image embeddings and aesthetic scores for one batch."""

    BATCH_SIZE = 128
    BACKEND_ERROR_LOG_INTERVAL_SECONDS = 60
    #: A batch slower than this logs its [EMBED_TIMING] line at INFO instead of
    #: DEBUG. Same threshold as FaceExtractionTask.SLOW_BATCH_LOG_S.
    SLOW_BATCH_LOG_S = 5.0

    # `filename`, not `path`. This table used to hold absolute paths built at
    # import time from a second copy of the download folder's location, which is
    # what stopped the folder from being relocatable at all: the shelf would have
    # declared the new location while this table kept naming the old one, so a
    # scorer that had just been moved would be downloaded again. The folder is
    # asked for at use time instead - see `_aesthetic_config`.
    AESTHETIC_MODELS = {
        "ViT-L-14": {
            "url": "https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac%2Blogos%2Bava1-l14-linearMSE.pth",
            "filename": "sac+logos+ava1-l14-linearMSE.pth",
            "dim": 768,
        },
        "ViT-B-32": {
            "url": "https://raw.githubusercontent.com/LAION-AI/aesthetic-predictor/main/sa_0_4_vit_b_32_linear.pth",
            "filename": "sa_0_4_vit_b_32_linear.pth",
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

        # Preloading state - images loaded from disk in on_queued() so I/O
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

    _PRELOAD_WORKERS = 4

    def _preload_images_task(self) -> None:
        """Decode, hash and preprocess the batch, off the GPU worker.

        Everything per-image that is CPU work happens here, in a small pool:
        the decode, the perceptual hash (a LANCZOS resample of the full
        frame), and CLIP's own preprocessing when the model is already loaded.
        Measured on the GPU worker instead, those were 4 s of a 4.2 s batch of
        128 - the forward pass itself is a tenth of a second - and the single
        GPU worker sat on CPU work while every other stage waited for it.
        """

        def _one(item):
            pid, file_path = item
            if self._preload_cancel.is_set():
                return []
            try:
                full_path = os.path.join(self._db.image_root, file_path)
                if VideoUtils.is_video_file(file_path):
                    images = [
                        frame.convert("RGB")
                        for frame in VideoUtils.extract_representative_video_frames(
                            full_path, count=3
                        )
                    ]
                else:
                    images = [Image.open(full_path).convert("RGB")]
            except Exception as exc:
                logger.debug("EmbedPreload: failed to load %s: %s", file_path, exc)
                return [(pid, file_path, None, None, None)]
            tensors = None
            if self._clip_workflow is not None:
                try:
                    tensors = self._clip_workflow.preprocess_images(images)
                except Exception as exc:
                    # The worker preprocesses anything not done here.
                    logger.debug(
                        "EmbedPreload: preprocess failed for %s, deferring to the "
                        "worker: %s",
                        file_path,
                        exc,
                    )
                    tensors = None
            return [
                (
                    pid,
                    file_path,
                    img,
                    self._compute_dhash(img),
                    tensors[i] if tensors is not None else None,
                )
                for i, img in enumerate(images)
            ]

        preloaded = []
        workers = min(self._PRELOAD_WORKERS, max(1, len(self._batch)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for entries in pool.map(_one, self._batch):
                preloaded.extend(entries)
        with self._preload_lock:
            self._preloaded_images = preloaded
        logger.debug(
            "[EMBED_PRELOAD] task_id=%s preloaded=%d/%d",
            self.id,
            sum(1 for entry in preloaded if entry[2] is not None),
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
        except Exception as exc:
            logger.debug(
                "ImageEmbeddingTask: VRAM estimate failed for %d image(s); "
                "assuming 0: %s",
                len(self._batch),
                exc,
            )
            return 0

    @classmethod
    def _aesthetic_config(cls):
        """The scorer for the active CLIP model, with its path resolved now.

        ``path`` is joined here rather than stored in the table so it follows a
        relocation of the download folder: every reader of the folder asks
        :func:`builtin_model_dir`, which is what makes the folder movable.
        """
        config = cls.AESTHETIC_MODELS.get(CLIP_MODEL_NAME)
        if config is None:
            return None
        return {
            **config,
            "path": os.path.join(builtin_model_dir(), config["filename"]),
        }

    @classmethod
    def _is_aesthetic_disabled(cls):
        if cls._aesthetic_disabled is None:
            cls._aesthetic_disabled = cls._aesthetic_config() is None
        return bool(cls._aesthetic_disabled)

    @classmethod
    def count_remaining(
        cls,
        session: Session,
        aesthetic_disabled: Optional[bool] = None,
        suppressed_ids: Optional[set] = None,
    ) -> int:
        """Count pictures needing image embedding or aesthetic score work.

        *suppressed_ids* - pictures whose file cannot be decoded (issue #585) -
        are excluded so progress does not stall at a non-zero "remaining" that can
        never drain.
        """
        if aesthetic_disabled is None:
            aesthetic_disabled = cls._is_aesthetic_disabled()

        # Each arm has its own partial index (ix_picture_image_embedding_missing,
        # ix_picture_aesthetic_score_missing); SQLite serves the OR from both.
        missing_embedding = Picture.image_embedding.is_(None)
        if aesthetic_disabled:
            condition = missing_embedding
        else:
            condition = or_(
                missing_embedding,
                Picture.aesthetic_score.is_(None),
            )
        stmt = select(func.count()).select_from(Picture).where(condition)
        if suppressed_ids:
            stmt = stmt.where(Picture.id.notin_(tuple(suppressed_ids)))
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
        suppressed_ids: Optional[set] = None,
    ):
        """Fetch pictures needing image embedding or aesthetic score work.

        *suppressed_ids* - undecodable pictures (issue #585) - are excluded from
        the candidate window so a handful of corrupt files cannot crowd out real
        work and stall the finder.
        """
        if aesthetic_disabled is None:
            aesthetic_disabled = cls._is_aesthetic_disabled()

        # Each arm has its own partial index (ix_picture_image_embedding_missing,
        # ix_picture_aesthetic_score_missing); SQLite serves the OR from both.
        missing_embedding = Picture.image_embedding.is_(None)
        if aesthetic_disabled:
            condition = missing_embedding
        else:
            condition = or_(
                missing_embedding,
                Picture.aesthetic_score.is_(None),
            )

        stmt = select(Picture.id, Picture.file_path).where(condition)
        if suppressed_ids:
            stmt = stmt.where(Picture.id.notin_(tuple(suppressed_ids)))
        stmt = stmt.limit(int(limit or cls.BATCH_SIZE))
        return session.exec(stmt).all()

    @classmethod
    def release_models(cls):
        cls._aesthetic_model = None

    def _build_failure_updates(self, pids: set[int]):
        # NULL embedding = "select me again next sweep"; the registry, not the
        # column, is what keeps an undecodable picture out of the finder.
        score = None if self._is_aesthetic_disabled() else -1.0
        return [(pid, None, score, None) for pid in pids]

    def _mark_decode_failures(self, pids: set[int], batch_files: dict) -> None:
        """Suppress pictures that genuinely could not be decoded (issue #585).

        Only the pids whose image failed to open/decode are passed here - never a
        transient inference failure (CLIP/GPU OOM), which must keep retrying. A
        failed picture keeps a NULL embedding, which ``fetch_work`` treats as
        still-missing, so without this a corrupt image is re-selected every sweep.
        """
        registry = getattr(self._db, "unprocessable_images", None)
        if registry is None or not pids:
            return
        image_root = getattr(self._db, "image_root", "") or ""
        for pid in pids:
            file_path = batch_files.get(pid)
            if not file_path:
                continue
            registry.mark_unprocessable(
                pid,
                ImageUtils.resolve_picture_path(image_root, str(file_path)),
                reason="image could not be decoded",
            )

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
        except Exception as exc:
            logger.debug("ImageEmbeddingTask: dhash computation failed: %s", exc)
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

        # Local import: torch costs seconds to import and this module is on the
        # server's import path. By the time an aesthetic model is being built,
        # the CLIP workflow has already loaded torch anyway.
        import torch
        import torch.nn as nn

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

        clip_ready = bool(
            self._clip_workflow is not None and self._clip_workflow.is_ready()
        )
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

        started_at = time.perf_counter()
        preloaded = self._wait_for_preload()
        preload_wait_s = time.perf_counter() - started_at
        changed = self._process_preloaded(
            preloaded, started_at=started_at, preload_wait_s=preload_wait_s
        )
        return {"changed_count": len(changed), "changed": changed}

    def _process_preloaded(
        self,
        preloaded: list,
        *,
        started_at: float | None = None,
        preload_wait_s: float = 0.0,
    ) -> list:
        """Process the preloaded batch.

        Args:
            preloaded: One entry per picture (per frame for a video):
                ``(pid, file_path, PIL.Image | None)``, optionally followed by
                the perceptual hash and the CLIP-preprocessed tensor the
                preload pool computed. Anything missing is computed here.
            started_at: ``time.perf_counter()`` when the task started running;
                ``total_s`` in the timing line is measured from it.
            preload_wait_s: How long the task blocked on the preload thread.

        Returns a list of (model, pic_id, field, value) change tuples.
        """
        if started_at is None:
            started_at = time.perf_counter()
        flat_images = []
        flat_pids = []
        flat_hashes = []
        flat_tensors = []
        decode_failed_pids = set()
        batch_pids = {entry[0] for entry in preloaded}
        batch_files = {entry[0]: entry[1] for entry in preloaded}

        for entry in preloaded:
            pid, file_path, img = entry[:3]
            if img is None:
                decode_failed_pids.add(pid)
                continue
            dhash = entry[3] if len(entry) > 3 else None
            tensor = entry[4] if len(entry) > 4 else None
            flat_images.append(img)
            flat_hashes.append(dhash if dhash is not None else self._compute_dhash(img))
            flat_tensors.append(tensor)
            flat_pids.append(pid)
        # All or nothing: a batch is one forward pass, and the service
        # preprocesses the whole list itself when any tensor is missing.
        if any(t is None for t in flat_tensors):
            flat_tensors = None

        # A None image means the file could not be decoded - suppress those
        # pictures (issue #585) so they are not re-selected every sweep. This is
        # the decode-failure path only; inference failures below still retry.
        self._mark_decode_failures(decode_failed_pids, batch_files)

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
            if decode_failed_pids:
                logger.warning(
                    "ImageEmbeddingTask: Failed to load %d pictures: %s",
                    len(decode_failed_pids),
                    [batch_files.get(pid) for pid in decode_failed_pids],
                )
            return changed

        embeddings = None
        inference_start = time.perf_counter()
        clip_ready = self._ensure_clip_ready()

        if clip_ready:
            try:
                embeddings = (
                    self._clip_workflow.encode_images(flat_images, tensors=flat_tensors)
                    if flat_tensors is not None
                    else self._clip_workflow.encode_images(flat_images)
                )
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
            # Local import (see _ensure_model): reaching here means the
            # aesthetic model is already built, so torch is resident.
            import torch

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
        inference_s = time.perf_counter() - inference_start

        if embeddings is None:
            logger.error(
                "ImageEmbeddingTask: No embeddings generated for batch of %s pictures (clip_ready=%s fallback_ready=%s).",
                len(batch_pids),
                bool(
                    self._clip_workflow is not None and self._clip_workflow.is_ready()
                ),
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

        db_start = time.perf_counter()
        updated_ids = self._db.run_task(
            self._save_results, updates, priority=DBPriority.LOW
        )
        db_s = time.perf_counter() - db_start
        changed = [(Picture, pid, "image_embedding", None) for pid in updated_ids]

        total_s = time.perf_counter() - started_at
        n = len(processed_pids)
        logger.log(
            logging.INFO if total_s >= self.SLOW_BATCH_LOG_S else logging.DEBUG,
            "[EMBED_TIMING] task_id=%s n=%d device=%s preload_wait_s=%.3f "
            "inference_s=%.3f db_s=%.3f total_s=%.3f throughput=%.1f/s",
            self.id,
            n,
            getattr(self._clip_workflow, "device", "unknown"),
            preload_wait_s,
            inference_s,
            db_s,
            total_s,
            n / total_s if total_s > 0 else 0.0,
        )

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
