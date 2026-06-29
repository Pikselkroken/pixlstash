#!/usr/bin/env python3
"""Florence-2 object-detection tests (run on CPU with --force-cpu).

Exercises ``Florence2Service.detect_objects`` via the engine wrapper
``InferenceEngine.detect_objects``: dense ``<OD>`` detection, open-vocabulary
detection, original-pixel coordinate scaling, and graceful failure handling.
"""

import gc
import os
import pytest
import torch
from pathlib import Path

from pixlstash.inference.engine import InferenceEngine
from pixlstash.server import Server
from PIL import Image

# Florence-2's internal feed resize cap used by detect_objects (max_dim default).
DETECTION_MAX_DIM = 1024


@pytest.fixture(scope="module")
def engine():
    """Create an InferenceEngine with Florence-2 loaded (skips if HF throttles)."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    eng = InferenceEngine.create(fast_captions=Server.DEFAULT_FAST_CAPTIONS)
    try:
        eng._ensure_captioning_ready()
    except Exception as exc:
        exc_str = str(exc)
        if (
            "429" in exc_str
            or "rate limit" in exc_str.lower()
            or "too many requests" in exc_str.lower()
        ):
            pytest.skip(f"HuggingFace rate-limited during model download: {exc}")
        raise

    yield eng

    eng.close()
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


@pytest.fixture(scope="module")
def image_files():
    test_dir = Path(__file__).parent.parent / "pictures"
    if not test_dir.is_dir():
        pytest.fail(f"Test images directory not found: {test_dir}")
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files = [
        str(f)
        for f in sorted(test_dir.iterdir())
        if f.is_file() and f.suffix.lower() in exts
    ]
    if not files:
        pytest.fail(f"No image files found in {test_dir}")
    return files


def _assert_well_formed(detections, width, height):
    """Every detection is (label:str, [x1,y1,x2,y2] within bounds, score|None)."""
    for label, bbox, score in detections:
        assert isinstance(label, str)
        assert isinstance(bbox, list) and len(bbox) == 4
        x1, y1, x2, y2 = bbox
        assert 0 <= x1 < x2 <= width, (bbox, width, height)
        assert 0 <= y1 < y2 <= height, (bbox, width, height)
        assert score is None or isinstance(score, float)


def test_detect_objects_returns_boxes(engine, image_files):
    """Dense <OD> produces well-formed, in-bounds boxes on the sample images."""
    dataset = image_files[: (3 if os.getenv("GITHUB_ACTIONS") == "true" else 8)]
    results = engine.detect_objects(dataset)

    assert set(results.keys()) <= set(dataset)
    total_boxes = 0
    for path, detections in results.items():
        with Image.open(path) as im:
            width, height = im.size
        _assert_well_formed(detections, width, height)
        total_boxes += len(detections)
        # <OD> reports no per-box confidence.
        assert all(score is None for _, _, score in detections)

    assert total_boxes > 0, "Florence <OD> detected nothing across the sample set"


def test_detect_objects_coordinate_space(engine, image_files):
    """Boxes come back in ORIGINAL pixels, not the resized feed frame.

    The service resizes each image to a longest side of DETECTION_MAX_DIM before
    inference. If the boxes were left in that resized frame (a scaling bug), no
    coordinate could exceed DETECTION_MAX_DIM. Every sample image here is taller
    than that, and Florence reliably boxes the dominant subject spanning much of
    the frame, so at least one coordinate across the set must exceed the cap.
    """
    big = [p for p in image_files if max(Image.open(p).size) > DETECTION_MAX_DIM + 200]
    if not big:
        pytest.skip("No sample image large enough to probe coordinate scaling")
    big = big[: (3 if os.getenv("GITHUB_ACTIONS") == "true" else 6)]

    results = engine.detect_objects(big)
    max_coord = 0
    saw_detection = False
    for path, detections in results.items():
        with Image.open(path) as im:
            width, height = im.size
        _assert_well_formed(detections, width, height)
        for _, (x1, y1, x2, y2), _ in detections:
            saw_detection = True
            max_coord = max(max_coord, x2, y2)

    assert saw_detection, "No detections on the large sample images"
    assert max_coord > DETECTION_MAX_DIM, (
        f"Max box coordinate {max_coord} <= resize cap {DETECTION_MAX_DIM}: "
        "boxes appear to be in the resized frame, not original pixels"
    )


def test_detect_objects_grounding_is_well_formed(engine, image_files):
    """Open-vocabulary detection returns a well-formed (possibly empty) result."""
    sample = image_files[:2]
    results = engine.detect_objects(sample, prompt="a person")
    assert set(results.keys()) <= set(sample)
    for path, detections in results.items():
        with Image.open(path) as im:
            width, height = im.size
        _assert_well_formed(detections, width, height)


def test_detect_objects_missing_file_is_skipped(engine):
    """A path that cannot be loaded is omitted rather than raising."""
    results = engine.detect_objects(["/nonexistent/file.jpg"])
    assert results == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
