"""Batched InsightFace inference utilities.

The InsightFace detector (RetinaFace/SCRFD) ONNX model has a fixed batch dimension
of 1, so each image must be processed individually for detection.  However, the
recognition model (ArcFace) exposes a dynamic batch dimension and its ``get_feat``
method already accepts a list of aligned crops.

``BatchedFaceRunner`` exploits this by:

1. Running the detector once per image (unavoidable - ONNX batch=1).
2. Collecting *all* aligned face crops from *all* images into a single list.
3. Calling the recogniser exactly **once** for the entire crop list.
4. Skipping the landmark (3D/2D) and genderage models that ``FaceAnalysis.get()``
   runs unconditionally but that ``FaceExtractionTask`` never uses.

For a batch of N images averaging F faces each the saving is:
  • ``FaceAnalysis.get()`` path: N×(1 detector + F×4 models) calls
  • ``BatchedFaceRunner.run_batch()`` path: N detector calls + 1 recogniser call
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class FaceResult:
    """Minimal face detection + recognition result.

    Attributes:
        bbox: ``[x1, y1, x2, y2]`` in the coordinate space of the input image.
        kps: ``(5, 2)`` keypoints array, or ``None`` when not available.
        embedding: Normalised 512-d ArcFace embedding, or ``None`` when no
            recognition model is present.
    """

    bbox: np.ndarray
    kps: np.ndarray | None = field(default=None)
    embedding: np.ndarray | None = field(default=None)


# Faces per recogniser call. The recogniser's activations are ~20 MB a face at
# 112 px (~650 MB for 16); per-face throughput was flat from 16 to 64, so a
# bigger chunk buys nothing, and a face-dense batch of stills would otherwise
# make one unchunked call whose size nobody chose. The session is uncapped
# (see ``ORT_ARENA_SHARE``); the chunk bounds its peak anyway.
RECOGNITION_CHUNK = 16


class BatchedFaceRunner:
    """Drop-in replacement for repeated ``FaceAnalysis.get()`` calls.

    Args:
        app: An already-prepared ``insightface.app.FaceAnalysis`` instance.
            The caller retains ownership; ``BatchedFaceRunner`` only reads
            ``app.det_model`` and ``app.models['recognition']``.

    Example::

        runner = BatchedFaceRunner(app)
        per_image_faces = runner.run_batch(images)   # list[list[FaceResult]]
    """

    def __init__(self, app) -> None:
        self._det_model = app.det_model
        self._rec_model = app.models.get("recognition")

    def run_batch(self, images: list[np.ndarray | None]) -> list[list[FaceResult]]:
        """Detect and recognise faces in a batch of images.

        Args:
            images: BGR ``np.ndarray`` frames (any size), or ``None`` for
                positions that should yield an empty result.

        Returns:
            A list with one inner list per input image.  Each inner list
            contains :class:`FaceResult` objects sorted by detection score
            (highest first, matching ``FaceAnalysis.get()`` behaviour).
        """
        # Local import: insightface drags in onnxruntime and costs seconds to
        # import. This module sits on the server's import path, and a caller
        # only reaches here with an already-prepared FaceAnalysis app - so
        # insightface is resident by then anyway.
        from insightface.utils import face_align

        # ── Phase 1: detection - one image at a time (ONNX batch dim = 1) ──
        detections: list[tuple[np.ndarray | None, np.ndarray, np.ndarray | None]] = []
        for img in images:
            if img is None:
                detections.append((None, np.empty((0, 5), dtype=np.float32), None))
                continue
            bboxes, kpss = self._det_model.detect(img)
            detections.append((img, bboxes, kpss))

        # ── Phase 2: collect aligned crops for batched recognition ──────────
        # crop_refs: (img_idx, face_i, aligned_crop)
        crop_refs: list[tuple[int, int, np.ndarray]] = []
        if self._rec_model is not None:
            rec_input_size: int = self._rec_model.input_size[0]
            for img_idx, (img, bboxes, kpss) in enumerate(detections):
                if img is None or bboxes.shape[0] == 0 or kpss is None:
                    continue
                for face_i in range(bboxes.shape[0]):
                    crop = face_align.norm_crop(
                        img,
                        landmark=kpss[face_i],
                        image_size=rec_input_size,
                    )
                    crop_refs.append((img_idx, face_i, crop))

        # ── Phase 3: single recogniser call for all crops ───────────────────
        embeddings_by_key: dict[tuple[int, int], np.ndarray] = {}
        if crop_refs:
            all_crops = [crop for _, _, crop in crop_refs]
            # get_feat() accepts a list of BGR crops and returns (M, 512)
            all_embeddings: np.ndarray = np.concatenate(
                [
                    self._rec_model.get_feat(all_crops[i : i + RECOGNITION_CHUNK])
                    for i in range(0, len(all_crops), RECOGNITION_CHUNK)
                ]
            )
            for (img_idx, face_i, _), emb in zip(crop_refs, all_embeddings):
                embeddings_by_key[(img_idx, face_i)] = emb

        # ── Phase 4: assemble per-image results ─────────────────────────────
        results: list[list[FaceResult]] = []
        for img_idx, (_, bboxes, kpss) in enumerate(detections):
            faces: list[FaceResult] = []
            for face_i in range(bboxes.shape[0]):
                faces.append(
                    FaceResult(
                        bbox=bboxes[face_i, :4],
                        kps=kpss[face_i] if kpss is not None else None,
                        embedding=embeddings_by_key.get((img_idx, face_i)),
                    )
                )
            results.append(faces)
        return results
