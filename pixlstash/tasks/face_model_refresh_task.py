"""Refresh face embeddings in place when the InsightFace model pack changes.

When a user switches ``insightface_model_pack`` (e.g. ``buffalo_l`` → ``auraface``)
the existing :class:`~pixlstash.db_models.face.Face` rows still hold embeddings
from the old pack. The detector (SCRFD-10G) is the **same** in both supported
packs, so the detections are essentially identical and only the recognition
embedding changes.

This task therefore refreshes ``features`` + ``model_pack`` **in place** on the
existing rows, preserving each row's ``character_id`` (the manual identity
assignment) and the row identity. It does NOT delete-and-reinsert as the default
path, because that would wipe manual character assignments.

Matching strategy (per picture):

1. Match new detections to existing rows by ``(frame_index, face_index)`` — the
   extractor assigns ``face_index`` deterministically by sorted bbox position, so
   with an identical detector the indices line up.
2. Any remaining unmatched pairs are matched by bbox IoU (handles a rare ordering
   or count delta between runs).
3. Genuinely new detections are inserted; existing rows with no match are removed
   (logged), respecting ``UniqueConstraint(picture_id, frame_index, face_index)``.
"""

from __future__ import annotations

import os
import threading

import cv2
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models.face import Face
from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority
from pixlstash.tasks.face_extraction_task import CROP_EXPAND_SCALE, FaceExtractionTask
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.insightface_model_utils import DEFAULT_MODEL_PACK

logger = get_logger(__name__)

# Minimum IoU for the fallback bbox match to be accepted as the same face.
_IOU_MATCH_THRESHOLD = 0.3


def _bbox_iou(a, b) -> float:
    """Return the intersection-over-union of two ``[x1, y1, x2, y2]`` boxes."""
    if not a or not b or len(a) != 4 or len(b) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


class FaceModelRefreshTask(BaseTask):
    """Re-compute face embeddings for a batch of pictures and update in place.

    Args:
        database: Vault database instance.
        engine: :class:`~pixlstash.inference.engine.InferenceEngine` providing the
            currently configured ``insightface_model_pack``.
        pictures: Pictures whose faces should be refreshed to the current pack.
    """

    _IMAGE_EXTS = FaceExtractionTask._IMAGE_EXTS
    _VIDEO_EXTS = FaceExtractionTask._VIDEO_EXTS

    def __init__(self, database, engine, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="FaceModelRefreshTask",
            params={"picture_ids": picture_ids, "batch_size": len(picture_ids)},
        )
        self._db = database
        self._engine = engine
        self._pictures = pictures or []
        self._insightface_app = None
        self._cpu_spillover_enabled = False
        self._stop_event = threading.Event()

    @property
    def priority(self) -> TaskPriority:
        # Lower than initial FACE_EXTRACTION (HIGH) so refresh never preempts the
        # processing of brand-new pictures.
        return TaskPriority.MEDIUM

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def allow_cpu_spillover(self) -> bool:
        return True

    def enable_cpu_spillover(self) -> None:
        self._cpu_spillover_enabled = True

    def on_cancel(self) -> None:
        self._stop_event.set()

    def _init_insightface_app(self) -> None:
        if self._insightface_app is None:
            self._insightface_app = FaceExtractionTask.get_or_init_insightface(
                self._engine, cpu_spillover=self._cpu_spillover_enabled
            )

    def _detect_for_picture(self, pic) -> list:
        """Detect faces for one picture, returning Face objects (not persisted).

        The returned faces carry ``features``, ``frame_index``, ``model_pack`` and
        an expanded ``bbox`` in original-pixel space, with ``face_index`` assigned
        by the same sorted-bbox rule the extractor uses.
        """
        model_pack = getattr(self._engine, "insightface_model_pack", DEFAULT_MODEL_PACK)
        file_path = str(
            ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
        )
        ext = os.path.splitext(file_path)[1].lower()
        face_expand_fraction = max(0.0, CROP_EXPAND_SCALE - 1.0)
        faces: list[Face] = []

        if ext in self._IMAGE_EXTS:
            img, inv_scale = ImageUtils.load_image_bgr_reduced(
                file_path, FaceExtractionTask.INFERENCE_MAX_SIDE
            )
            if img is None:
                logger.warning(
                    "FaceModelRefreshTask: could not load image %s (picture %s); "
                    "skipping.",
                    file_path,
                    pic.id,
                )
                return []
            detections = FaceExtractionTask.detect_faces_in_images(
                self._insightface_app, [img]
            )[0]
            for det in detections:
                expanded = Face.expand_face_bbox(
                    det.bbox, img.shape[1], img.shape[0], face_expand_fraction
                )
                if inv_scale != 1.0 and expanded:
                    expanded = [v * inv_scale for v in expanded]
                features_bytes = None
                if getattr(det, "embedding", None) is not None:
                    features_bytes = det.embedding.astype("float32").tobytes()
                faces.append(
                    Face(
                        picture_id=pic.id,
                        face_index=-1,
                        bbox=expanded,
                        character_id=None,
                        frame_index=0,
                        features=features_bytes,
                        model_pack=model_pack,
                    )
                )
        elif ext in self._VIDEO_EXTS:
            cap = cv2.VideoCapture(file_path)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if frame_count < 1:
                logger.warning(
                    "FaceModelRefreshTask: no frames in video %s (picture %s).",
                    file_path,
                    pic.id,
                )
                cap.release()
                return []
            sampled = [0] + list(
                range(max(1, frame_count // 3), frame_count, max(1, frame_count // 3))
            )
            for frame_index in sampled:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                detections = FaceExtractionTask.detect_faces_in_images(
                    self._insightface_app, [frame]
                )[0]
                for det in detections:
                    expanded = Face.expand_face_bbox(
                        det.bbox, frame.shape[1], frame.shape[0], face_expand_fraction
                    )
                    features_bytes = None
                    if getattr(det, "embedding", None) is not None:
                        features_bytes = det.embedding.astype("float32").tobytes()
                    faces.append(
                        Face(
                            picture_id=pic.id,
                            face_index=-1,
                            bbox=expanded,
                            character_id=None,
                            frame_index=frame_index,
                            features=features_bytes,
                            model_pack=model_pack,
                        )
                    )
            cap.release()
        else:
            logger.warning(
                "FaceModelRefreshTask: unsupported extension for %s (picture %s).",
                file_path,
                pic.id,
            )
            return []

        # Assign face_index per frame by sorted bbox position (same rule as the
        # extractor) so (frame_index, face_index) lines up across packs.
        by_frame: dict[int, list[Face]] = {}
        for f in faces:
            by_frame.setdefault(f.frame_index, []).append(f)
        for frame_faces in by_frame.values():
            frame_faces.sort(
                key=lambda f: (
                    (f.bbox[1], f.bbox[0], f.bbox[3], f.bbox[2])
                    if f.bbox
                    else (0, 0, 0, 0)
                )
            )
            for idx, f in enumerate(frame_faces):
                f.face_index = idx
        return faces

    def _run_task(self) -> dict:
        if not self._pictures:
            return {"changed_count": 0, "picture_ids": []}

        model_pack = getattr(self._engine, "insightface_model_pack", DEFAULT_MODEL_PACK)
        self._init_insightface_app()

        changed_ids: list[int] = []
        for pic in self._pictures:
            if self._stop_event.is_set():
                break
            if pic.id is None:
                continue
            try:
                new_faces = self._detect_for_picture(pic)
            except Exception as exc:
                logger.warning(
                    "FaceModelRefreshTask: detection failed for picture %s: %s",
                    pic.id,
                    exc,
                )
                continue
            self._db.submit_task(
                self._refresh_picture_faces,
                pic.id,
                new_faces,
                model_pack,
                priority=DBPriority.HIGH,
            )
            changed_ids.append(pic.id)

        return {"changed_count": len(changed_ids), "picture_ids": sorted(changed_ids)}

    @staticmethod
    def _match_existing(existing: list[Face], new_faces: list[Face]):
        """Pair existing rows with new detections.

        Returns ``(pairs, unmatched_new, unmatched_existing)`` where ``pairs`` is a
        list of ``(existing_face, new_face)``. Matching is by
        ``(frame_index, face_index)`` first, then by bbox IoU for the leftovers.
        """
        existing_by_key = {(f.frame_index, f.face_index): f for f in existing}
        pairs: list[tuple[Face, Face]] = []
        unmatched_new: list[Face] = []
        matched_existing: set[int] = set()

        for nf in new_faces:
            key = (nf.frame_index, nf.face_index)
            ef = existing_by_key.get(key)
            if ef is not None and id(ef) not in matched_existing:
                pairs.append((ef, nf))
                matched_existing.add(id(ef))
            else:
                unmatched_new.append(nf)

        leftover_existing = [f for f in existing if id(f) not in matched_existing]

        # Fallback: greedily match leftovers by IoU within the same frame.
        still_unmatched_new: list[Face] = []
        for nf in unmatched_new:
            best_ef = None
            best_iou = _IOU_MATCH_THRESHOLD
            for ef in leftover_existing:
                if ef.frame_index != nf.frame_index or id(ef) in matched_existing:
                    continue
                iou = _bbox_iou(ef.bbox, nf.bbox)
                if iou >= best_iou:
                    best_iou = iou
                    best_ef = ef
            if best_ef is not None:
                pairs.append((best_ef, nf))
                matched_existing.add(id(best_ef))
            else:
                still_unmatched_new.append(nf)

        unmatched_existing = [f for f in existing if id(f) not in matched_existing]
        return pairs, still_unmatched_new, unmatched_existing

    @classmethod
    def _refresh_picture_faces(
        cls, session, picture_id: int, new_faces: list[Face], model_pack: str
    ) -> None:
        """Update one picture's face rows in place, preserving character_id."""
        existing = session.exec(select(Face).where(Face.picture_id == picture_id)).all()

        # Drop pre-existing sentinel rows (face_index == -1) from the match set —
        # they carry no real detection. If the new run also found nothing, we
        # re-insert a sentinel below.
        real_existing = [f for f in existing if f.face_index != -1]
        sentinels = [f for f in existing if f.face_index == -1]

        if not new_faces:
            # No faces detected now. Refresh sentinel(s) so the picture is not
            # re-selected forever; preserve nothing to preserve (no embeddings).
            if real_existing:
                for f in real_existing:
                    logger.info(
                        "FaceModelRefreshTask: face %s on picture %s no longer "
                        "detected by pack %s; removing.",
                        f.face_index,
                        picture_id,
                        model_pack,
                    )
                    session.delete(f)
            if sentinels:
                for s in sentinels:
                    s.model_pack = model_pack
                    session.add(s)
            else:
                session.add(
                    Face(
                        picture_id=picture_id,
                        face_index=-1,
                        character_id=None,
                        bbox=None,
                        model_pack=model_pack,
                    )
                )
            cls._commit(session, picture_id)
            return

        # Real detections exist now — any old sentinel is obsolete.
        for s in sentinels:
            session.delete(s)

        pairs, unmatched_new, unmatched_existing = cls._match_existing(
            real_existing, new_faces
        )

        for ef, nf in pairs:
            # Update embedding + pack IN PLACE; preserve character_id and identity.
            ef.features = nf.features
            ef.model_pack = model_pack
            ef.bbox = nf.bbox
            ef.frame_index = nf.frame_index
            ef.face_index = nf.face_index
            session.add(ef)

        for f in unmatched_existing:
            logger.info(
                "FaceModelRefreshTask: face %s (char=%s) on picture %s no longer "
                "detected by pack %s; removing.",
                f.face_index,
                f.character_id,
                picture_id,
                model_pack,
            )
            session.delete(f)

        # Flush deletes/updates before inserting new rows so the unique
        # constraint on (picture_id, frame_index, face_index) does not collide
        # with a row that is about to be deleted or re-indexed.
        session.flush()

        for nf in unmatched_new:
            session.add(nf)

        cls._commit(session, picture_id)

    @staticmethod
    def _commit(session, picture_id: int) -> None:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            logger.warning(
                "FaceModelRefreshTask: in-place refresh failed for picture %s "
                "(IntegrityError: %s); skipping.",
                picture_id,
                exc,
            )
        except Exception as exc:
            session.rollback()
            logger.warning(
                "FaceModelRefreshTask: in-place refresh failed for picture %s: %s",
                picture_id,
                exc,
            )
            # Re-attach the picture so the work column logic still fires.
            picture = session.get(Picture, picture_id)
            if picture is not None:
                _ = picture.id
