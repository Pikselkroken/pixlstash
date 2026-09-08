"""StartupChecks._check_device_and_vram: which device the server ends up on.

We own no AMD hardware, so these tests simulate a PyTorch ROCm build (HIP exposed
through the torch.cuda API, torch.version.hip set) to verify the device check:
labels ROCm as experimental, frames ONNX-on-CPU as expected rather than an error,
and falls back to CPU cleanly when the GPU probe fails.

The same fakes cover the other half of the decision - what happens when the
device the owner *named* is not there. An unset default may fall back; a named
one may not be quietly swapped, and the refusal has to say how to boot.
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
    """Inject fake torch / onnxruntime modules into ``startup_checks``.

    The real imports are function-local and cached behind ``sc._torch()`` /
    ``sc._ort()`` so that importing the server does not drag in the ML stack
    (backend_architecture §3, "ML import discipline"). Seeding the caches here
    both substitutes the fakes and stops the accessors ever attempting the real
    import - which is exactly what these tests want.
    """

    def apply(torch_mod, ort_mod):
        monkeypatch.setattr(sc, "_torch_mod", torch_mod)
        monkeypatch.setattr(sc, "_ort_mod", ort_mod)

    return apply


def test_rocm_passes_and_labels_experimental(patch_runtime):
    # ROCm build: torch reports the GPU available; only CPU ONNX Runtime present.
    patch_runtime(
        _make_torch(hip="6.4.43482", available=True),
        _make_ort(["CPUExecutionProvider"]),
    )
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
    patch_runtime(
        _make_torch(hip="6.4.43482", available=boom),
        _make_ort(["CPUExecutionProvider"]),
    )
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


# ---------------------------------------------------------------------------
# An explicit device is a request, not a hint
# ---------------------------------------------------------------------------

CONFIG_PATH = "/tmp/server_config.json"


def _checks_with_config(cfg):
    return StartupChecks(cfg, CONFIG_PATH, logging.getLogger("test"))


@pytest.mark.parametrize("configured", ["cuda", "gpu"])
def test_an_explicit_gpu_is_never_rewritten_to_cpu(patch_runtime, configured):
    """The owner named a device; booting on a different one silently is a lie.

    ``gpu`` is the same request spelled differently - the check normalises it to
    ``cuda`` - so it has to refuse too, and the config must survive unchanged
    either way rather than being persisted as ``cpu``.
    """
    patch_runtime(
        _make_torch(hip=None, available=False), _make_ort(["CPUExecutionProvider"])
    )
    cfg = {"default_device": configured}
    outcome = StartupCheckOutcome()
    _checks_with_config(cfg)._check_device_and_vram(outcome)

    assert cfg["default_device"] == configured
    assert not outcome.forced_cpu
    assert outcome.hard_failures


@pytest.mark.parametrize("missing", ["no-torch", "no-cuda"])
def test_the_refusal_says_how_to_boot(patch_runtime, missing):
    """A refusal with no way out strands the owner with a server that will not start.

    Whichever half of the GPU stack is missing, the hard failure has to carry
    the file to edit and the values that work; otherwise the only remedy is
    reading this source.
    """
    torch_mod = (
        None if missing == "no-torch" else _make_torch(hip=None, available=False)
    )
    patch_runtime(torch_mod, _make_ort(["CPUExecutionProvider"]))
    outcome = StartupCheckOutcome()
    _checks_with_config({"default_device": "cuda"})._check_device_and_vram(outcome)

    assert len(outcome.hard_failures) == 1
    failure = outcome.hard_failures[0]
    assert CONFIG_PATH in failure
    assert "`default_device`" in failure
    assert "`cpu`" in failure and "`auto`" in failure


def test_an_unset_default_still_falls_back_quietly(patch_runtime):
    """The positive control: falling back for someone with no preference is the point.

    Without it the refusal above could be satisfied by refusing everything,
    which would strand every machine that has no GPU.
    """
    patch_runtime(
        _make_torch(hip=None, available=False), _make_ort(["CPUExecutionProvider"])
    )
    cfg = {"default_device": "auto"}
    outcome = StartupCheckOutcome()
    _checks_with_config(cfg)._check_device_and_vram(outcome)

    assert not outcome.hard_failures
    assert outcome.forced_cpu
    # Still "auto", so the next boot re-checks after a driver or CUDA upgrade.
    assert cfg["default_device"] == "auto"
