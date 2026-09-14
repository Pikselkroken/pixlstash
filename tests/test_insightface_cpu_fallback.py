"""InsightFace on CUDA says so when onnxruntime runs it on the CPU instead.

InsightFace asks onnxruntime for ``[CUDAExecutionProvider, CPUExecutionProvider]``
and onnxruntime can hand back a CPU session without raising: at load, when the
CUDA provider's libraries are missing, and during a run, when the provider
fails and onnxruntime's own fallback retries on the CPU, reporting that only
with ``print()``. Either way face detection carried on at a fraction of the
speed with nothing in the log.

No ``Server`` and no models: ``FaceAnalysis`` is replaced by a stand-in whose
sessions report providers the way onnxruntime's do.
"""

import logging
import types
from unittest import mock

import numpy as np
import pytest

from pixlstash.inference.vram_budget import VramBudget
from pixlstash.tasks import face_extraction_task
from pixlstash.tasks.face_extraction_task import FaceExtractionTask

CUDA_SESSION = ["CUDAExecutionProvider", "CPUExecutionProvider"]
CPU_SESSION = ["CPUExecutionProvider"]


class _FakeSession:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_providers(self):
        return list(self.providers)


class _FakeApp:
    """``FaceAnalysis`` as the face code sees it: models with sessions."""

    def __init__(self, detection, recognition):
        self.models = {
            "detection": types.SimpleNamespace(session=_FakeSession(detection)),
            "recognition": types.SimpleNamespace(session=_FakeSession(recognition)),
        }

    def prepare(self, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _reset_face_globals():
    FaceExtractionTask.release_detection_models()
    yield
    FaceExtractionTask.release_detection_models()


def _engine():
    budget = VramBudget.__new__(VramBudget)
    budget._device = "cuda"
    budget._max_vram_usage_mb = 8192
    return types.SimpleNamespace(
        insightface_model_pack="buffalo_l",
        force_cpu=False,
        keep_models_in_memory=True,
        vram_budget=budget,
    )


def _load(app, cuda_available=True):
    with (
        mock.patch.object(face_extraction_task, "ensure_model_pack_available"),
        mock.patch("insightface.app.FaceAnalysis", return_value=app),
        mock.patch("torch.cuda.is_available", return_value=cuda_available),
    ):
        return FaceExtractionTask.get_or_init_insightface(_engine())


def _warnings(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == face_extraction_task.logger.name and r.levelno == logging.WARNING
    ]


class _RunnerThatFallsBack:
    """A batch run during which onnxruntime moves the detector to the CPU."""

    def __init__(self, app):
        self._app = app

    def run_batch(self, images):
        self._app.models["detection"].session.providers = list(CPU_SESSION)
        return [[] for _ in images]


def _detect_twice(app):
    image = np.zeros((64, 64, 3), dtype=np.uint8)
    with mock.patch.object(
        face_extraction_task, "BatchedFaceRunner", _RunnerThatFallsBack
    ):
        FaceExtractionTask.detect_faces_in_images(app, [image])
        FaceExtractionTask.detect_faces_in_images(app, [image])


def test_a_session_that_loads_on_the_cpu_despite_cuda_is_logged(caplog):
    app = _FakeApp(detection=CPU_SESSION, recognition=CUDA_SESSION)
    with caplog.at_level(logging.WARNING, logger=face_extraction_task.logger.name):
        assert _load(app) is app

    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "detection" in warnings[0]
    assert "recognition" not in warnings[0]
    assert "pip install onnxruntime-gpu" in warnings[0]


def test_sessions_that_load_on_cuda_log_nothing(caplog):
    # Control for the test above.
    with caplog.at_level(logging.WARNING, logger=face_extraction_task.logger.name):
        _load(_FakeApp(detection=CUDA_SESSION, recognition=CUDA_SESSION))

    assert _warnings(caplog) == []


def test_a_host_without_cuda_is_not_warned_about_its_cpu_sessions(caplog):
    """No CUDA means InsightFace asked for the CPU: that is the plan, on a Mac
    included, not a fallback.

    The batches matter as much as the load: the run-time check reads the same
    CPU providers a fallback would leave behind, so only the recorded "this app
    asked for CUDA" tells the two apart.
    """
    app = _FakeApp(detection=CPU_SESSION, recognition=CPU_SESSION)
    with caplog.at_level(logging.WARNING, logger=face_extraction_task.logger.name):
        assert _load(app, False) is app
        _detect_twice(app)

    assert _warnings(caplog) == []


def test_a_rocm_host_is_not_warned_about_its_cpu_sessions(caplog):
    """ROCm reports CUDA as available and ships the CPU onnxruntime with it.

    Its sessions are CPU ones by arrangement (``startup_checks`` says so), and
    the advice the warning carries - install ``onnxruntime-gpu`` - is for the
    CUDA build, so nothing here is worth telling the owner.
    """
    app = _FakeApp(detection=CPU_SESSION, recognition=CPU_SESSION)
    with (
        mock.patch("torch.version.hip", "6.2.0", create=True),
        caplog.at_level(logging.WARNING, logger=face_extraction_task.logger.name),
    ):
        assert _load(app) is app
        _detect_twice(app)

    assert _warnings(caplog) == []


def test_a_cuda_session_moved_to_the_cpu_during_a_run_is_logged_once(caplog):
    app = _load(_FakeApp(detection=CUDA_SESSION, recognition=CUDA_SESSION))

    with caplog.at_level(logging.WARNING, logger=face_extraction_task.logger.name):
        _detect_twice(app)

    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "detection" in warnings[0]
    assert "during a run" in warnings[0]


def test_a_run_on_an_app_that_was_not_loaded_for_cuda_is_not_checked(caplog):
    # A CPU app, or one a test built by hand, has nothing to fall back from.
    app = _FakeApp(detection=CUDA_SESSION, recognition=CUDA_SESSION)

    with caplog.at_level(logging.WARNING, logger=face_extraction_task.logger.name):
        _detect_twice(app)

    assert _warnings(caplog) == []


def test_a_reload_after_a_release_reports_its_own_fallback(caplog):
    with caplog.at_level(logging.WARNING, logger=face_extraction_task.logger.name):
        _load(_FakeApp(detection=CPU_SESSION, recognition=CUDA_SESSION))
        assert len(_warnings(caplog)) == 1
        FaceExtractionTask.release_detection_models()
        caplog.clear()
        _load(_FakeApp(detection=CPU_SESSION, recognition=CUDA_SESSION))

    assert len(_warnings(caplog)) == 1
