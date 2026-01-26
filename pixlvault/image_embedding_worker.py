import pickle
import time
import os
import requests
import torch
import torch.nn as nn
import numpy as np
from PIL import Image
from collections import defaultdict
from sqlmodel import Session, select
from sentence_transformers import SentenceTransformer

from pixlvault.database import DBPriority
from pixlvault.db_models import Picture
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
    
    # LAION Aesthetic Predictor weights for ViT-L/14 (V2)
    # Using the improved predictor trained on SAC+Logos+AVA
    AESTHETIC_URL = "https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac%2Blogos%2Bava1-l14-linearMSE.pth"
    AESTHETIC_MODEL_PATH = "wd14_tagger_model/sac+logos+ava1-l14-linearMSE.pth"
    AESTHETIC_SUPPORTED_CLIP = {"ViT-L-14"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = None
        self.aesthetic_model = None
        self._aesthetic_disabled = CLIP_MODEL_NAME not in self.AESTHETIC_SUPPORTED_CLIP

    def worker_type(self) -> WorkerType:
        return WorkerType.IMAGE_EMBEDDING

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

        try:
            # Download if missing
            if not os.path.exists(self.AESTHETIC_MODEL_PATH):
                logger.info(f"Downloading aesthetic model from {self.AESTHETIC_URL}...")
                response = requests.get(self.AESTHETIC_URL, timeout=30)
                response.raise_for_status()
                os.makedirs(os.path.dirname(self.AESTHETIC_MODEL_PATH), exist_ok=True)
                with open(self.AESTHETIC_MODEL_PATH, "wb") as f:
                    f.write(response.content)

            # Load weights
            state_dict = torch.load(self.AESTHETIC_MODEL_PATH, map_location="cpu")
            self.aesthetic_model = nn.Linear(512, 1)
            self.aesthetic_model.load_state_dict(state_dict)
            self.aesthetic_model.eval()
            
            # Move to same device as CLIP if possible
            if self._picture_tagger and getattr(self._picture_tagger, '_clip_device', None):
                self.aesthetic_model = self.aesthetic_model.to(self._picture_tagger._clip_device)
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
                # Find pictures without image_embedding OR without aesthetic_score
                batch = self._db.run_immediate_read_task(self._fetch_work)
                
                if not batch:
                    logger.debug("ImageEmbeddingWorker: No pictures need embeddings/aesthetic. Sleeping...")
                    self._wait()
                    continue

                self._ensure_model()
                
                logger.debug(f"ImageEmbeddingWorker: Processing {len(batch)} pictures.")
                
                flat_images = []
                flat_pids = []
                
                for pid, file_path in batch:
                    try:
                        full_path = os.path.join(self._db.image_root, file_path)
                        
                        if PictureUtils.is_video_file(file_path):
                            pil_imgs = PictureUtils.extract_representative_video_frames(full_path, count=3)
                            
                            if pil_imgs:
                                flat_images.extend(pil_imgs)
                                flat_pids.extend([pid] * len(pil_imgs))

                        else:
                            try:
                                img = Image.open(full_path)
                            except Exception as e:
                                logger.error(f"ImageEmbeddingWorker: PIL failed to open {file_path}: {e}")
                                continue                                

                            if img.mode != 'RGB':
                                img = img.convert('RGB')
                            flat_images.append(img)
                            flat_pids.append(pid)
                            
                    except Exception as e:
                        logger.error(f"ImageEmbeddingWorker: Error loading {file_path}: {e}")
                        pass

                if flat_images:
                    embeddings = None
                    
                    # 1. Try using PictureTagger's loaded CLIP model
                    if self._picture_tagger and getattr(self._picture_tagger, '_clip_model', None):
                        try:
                            import open_clip
                            # open_clip expects images as tensors
                            # We can use the preprocess from PictureTagger
                            preprocess = self._picture_tagger._clip_preprocess
                            device = self._picture_tagger._clip_device
                            
                            image_tensors = torch.stack([preprocess(img) for img in flat_images]).to(device)
                            # Convert to fp16 if using CUDA (to match model weights)
                            if device == "cuda":
                                image_tensors = image_tensors.half()
                            
                            with torch.no_grad():
                                features = self._picture_tagger._clip_model.encode_image(image_tensors)
                                features /= features.norm(dim=-1, keepdim=True)
                                embeddings = features.cpu().numpy()
                        except Exception as e:
                            logger.error(f"ImageEmbeddingWorker: Failed to use PictureTagger CLIP model: {e}")
                            embeddings = None

                    # 2. Fallback to local SentenceTransformer model
                    if embeddings is None and self.model:
                        try:
                            embeddings = self.model.encode(flat_images, batch_size=self.BATCH_SIZE, convert_to_numpy=True, normalize_embeddings=True)
                        except Exception as e:
                            logger.error(f"ImageEmbeddingWorker: Failed to use local CLIP model: {e}")

                    if embeddings is not None:
                        # Calculate Aesthetic Scores
                        aesthetic_scores = []
                        if self.aesthetic_model is not None:
                            try:
                                with torch.no_grad():
                                    emb_tensor = torch.from_numpy(embeddings).float()
                                    
                                    # Ensure tensor is on same device as aesthetic model
                                    if next(self.aesthetic_model.parameters()).is_cuda:
                                         emb_tensor = emb_tensor.to(next(self.aesthetic_model.parameters()).device)

                                    scores = self.aesthetic_model(emb_tensor).squeeze()
                                    if scores.ndim == 0:
                                        scores = scores.unsqueeze(0)
                                    scores = scores.cpu().numpy()
                                    
                                    # Handle single item case (scalar)
                                    if scores.ndim == 0:
                                        scores = [float(scores)]
                                    aesthetic_scores = scores
                            except Exception as e:
                                logger.error(f"ImageEmbeddingWorker: Aesthetic scoring failed: {e}")
                                aesthetic_scores = []
                        
                        # Group by PID and average
                        pid_updates = defaultdict(lambda: {"embs": [], "scores": []})
                        for pid, emb, score in zip(flat_pids, embeddings, aesthetic_scores if len(aesthetic_scores) else [None]*len(embeddings)):
                            pid_updates[pid]["embs"].append(emb)
                            if score is not None:
                                pid_updates[pid]["scores"].append(score)
                            
                        updates = []
                        for pid, data in pid_updates.items():
                            embs = data["embs"]
                            scores = data["scores"]
                            
                            if len(embs) == 1:
                                final_emb = embs[0]
                            else:
                                avg = np.mean(embs, axis=0)
                                norm = np.linalg.norm(avg)
                                final_emb = avg / norm if norm > 0 else avg
                            
                            final_score = None
                            if scores:
                                 final_score = float(np.mean(scores))
                            
                            updates.append((pid, pickle.dumps(final_emb), final_score))
                        
                        self._db.run_task(self._save_results, updates, priority=DBPriority.LOW)
                        
                        logger.info(
                            "ImageEmbeddingWorker: Processed %s pictures (embeddings%s).",
                            len(updates),
                            " + aesthetic" if self.aesthetic_model is not None else "",
                        )

            except Exception as e:
                logger.error(f"ImageEmbeddingWorker: Error in loop: {e}")
                time.sleep(5)
                
        logger.info("ImageEmbeddingWorker: Stopped.")

    def _fetch_work(self, session: Session):
        """Fetch a batch of pictures that need embeddings (and aesthetics when enabled)."""
        filters = [Picture.image_embedding.is_(None)]
        if not self._aesthetic_disabled:
            filters.append(Picture.aesthetic_score.is_(None))

        stmt = select(Picture.id, Picture.file_path).where(*filters).limit(
            self.BATCH_SIZE
        )
        results = session.exec(stmt).all()
        return results

    def _save_results(self, session: Session, updates):
        for pid, emb_bytes, score in updates:
            pic = session.get(Picture, pid)
            if pic:
                pic.image_embedding = emb_bytes
                if score is not None:
                    pic.aesthetic_score = score
        session.commit()
