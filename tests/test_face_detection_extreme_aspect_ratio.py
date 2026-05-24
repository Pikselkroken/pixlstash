"""Regression test: InsightFace crashes on images with extreme aspect ratios.

RetinaFace's detect() computes:
    new_width = int(det_size / aspect_ratio)
When aspect_ratio > det_size (256 here), new_width rounds to 0 and
cv2.resize raises an assertion failure:
    error: (-215:Assertion failed) inv_scale_x > 0

This test calls FaceExtractionTask.detect_faces_in_images() directly so it
exercises the real InsightFace pipeline without a database or Picture objects.
It should FAIL on the unfixed code and PASS after the fix is applied.
"""

import numpy as np
import pytest
from insightface.app import FaceAnalysis

from pixlstash.tasks.face_extraction_task import FaceExtractionTask


@pytest.fixture(scope="module")
def insightface_app_cpu():
    """A CPU InsightFace app shared across all test cases in this module."""
    app = FaceAnalysis(providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_thresh=0.25, det_size=(256, 256))
    return app


@pytest.mark.parametrize(
    "width,height,description",
    [
        (1, 512, "1×512 portrait — ratio 512, new_width = int(256/512) = 0"),
        (512, 1, "512×1 landscape — ratio 1/512, new_height = int(256/512) = 0"),
        (1, 300, "1×300 portrait — ratio 300, new_width = int(256/300) = 0"),
        (1, 257, "1×257 — minimum ratio to trigger the bug"),
    ],
)
def test_detect_faces_does_not_crash_on_extreme_aspect_ratio(
    insightface_app_cpu, width, height, description
):
    """detect_faces_in_images must not raise for extreme-aspect-ratio images."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    # Should return a list with one (empty) inner list — no exception.
    results = FaceExtractionTask.detect_faces_in_images(insightface_app_cpu, [img])
    assert len(results) == 1, f"Expected one result list for: {description}"
    assert isinstance(results[0], list), f"Expected a list of faces for: {description}"
