"""ROCm-awareness of StartupChecks._check_device_and_vram.

We own no AMD hardware, so these tests simulate a PyTorch ROCm build (HIP exposed
through the torch.cuda API, torch.version.hip set) to verify the device check:
labels ROCm as experimental, frames ONNX-on-CPU as expected rather than an error,
and falls back to CPU cleanly when the GPU probe fails.
"""

import logging
import types

import pytest

import pixlstash.startup_checks as sc
from pixlstash.startup_checks import StartupCheckOutcome, StartupChecks


def _make_torch(*, hip, available, mem=(8_000 * 1024**2, 16_000 * 1024**2)):
    """Build a fake torch module. hip=None => CUDA build; a string => ROCm build."""
    version = types.SimpleNamespace(hip=hip, cuda=None if hip else "12.8")

    def is_available():
        if isinstance(available, Exception):
            raise available
        return available

    cuda = types.SimpleNamespace(
        is_available=is_available,
        mem_get_info=lambda: mem,
        get_device_capability=lambda i=0: (9, 0),
        get_device_name=lambda i=0: "Fake GPU",
    )
    return types.SimpleNamespace(version=version, cuda=cuda)


def _make_ort(providers):
    return types.SimpleNamespace(get_available_providers=lambda: list(providers))


def _checks(device="cuda"):
    cfg = {"default_device": device}
    return StartupChecks(cfg, "/tmp/server_config.json", logging.getLogger("test"))


@pytest.fixture
def patch_runtime(monkeypatch):
    def apply(torch_mod, ort_mod):
        monkeypatch.setattr(sc, "torch", torch_mod)
        monkeypatch.setattr(sc, "ort", ort_mod)

    return apply


def test_rocm_passes_and_labels_experimental(patch_runtime):
    # ROCm build: torch reports the GPU available; only CPU ONNX Runtime present.
    patch_runtime(_make_torch(hip="6.4.43482", available=True), _make_ort(["CPUExecutionProvider"]))
    outcome = StartupCheckOutcome()
    _checks("cuda")._check_device_and_vram(outcome)

    assert not outcome.forced_cpu
    assert not outcome.hard_failures
    notes = " ".join(outcome.notes)
    assert "ROCm (experimental, unverified) inference" in notes
    assert "ONNX face-extraction and WD14 tagger models run on CPU" in notes
    # ONNX-on-CPU is expected on ROCm: it must NOT be reported as a CUDA warning.
    assert not any("CUDAExecutionProvider unavailable" in w for w in outcome.warnings)


def test_rocm_probe_failure_falls_back_to_cpu(patch_runtime):
    # A broken ROCm install raises from is_available(); must fall back, not crash.
    boom = RuntimeError("HIP error: no ROCm-capable device is detected")
    patch_runtime(_make_torch(hip="6.4.43482", available=boom), _make_ort(["CPUExecutionProvider"]))
    outcome = StartupCheckOutcome()
    _checks("auto")._check_device_and_vram(outcome)  # auto => graceful CPU fallback

    assert outcome.forced_cpu
    assert not outcome.hard_failures
    assert any("ROCm is unavailable" in w for w in outcome.warnings)


def test_cuda_path_unchanged(patch_runtime):
    # Regression: a real CUDA build (no hip) still reports CUDA, not ROCm.
    patch_runtime(
        _make_torch(hip=None, available=True),
        _make_ort(["CUDAExecutionProvider", "CPUExecutionProvider"]),
    )
    outcome = StartupCheckOutcome()
    _checks("cuda")._check_device_and_vram(outcome)

    notes = " ".join(outcome.notes)
    assert "using CUDA inference" in notes
    assert "experimental" not in notes
