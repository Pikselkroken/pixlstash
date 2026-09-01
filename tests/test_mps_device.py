"""Apple Metal (MPS) support across device detection, OOM classification and startup.

No test here needs a Metal GPU: the detection and start-up paths are driven with
a stand-in torch module, following ``test_rocm_device_check.py``. The one test
that does need real hardware is skipped when MPS is absent, so the file runs the
same on a CI Linux runner as on an Apple laptop.
"""

import logging
import sys
import types

import pytest

import pixlstash.startup_checks as sc
from pixlstash.startup_checks import StartupCheckOutcome, StartupChecks
from pixlstash.utils.device_utils import (
    detect_device,
    empty_device_cache,
    is_accelerator,
)
from pixlstash.utils.vram_utils import is_vram_oom

#: The exact text PyTorch raises when Metal runs out of memory, captured from
#: torch 2.13 on an M1 Pro. Load-bearing: it contains "out of memory" but none
#: of "cuda"/"gpu"/"hip"/"vram", which is why classifying it needs "mps".
MPS_OOM_MESSAGE = (
    "MPS backend out of memory (MPS allocated: 1024.00 MiB, other allocations: "
    "384.00 KiB, max allowed: 511.18 MiB). Tried to allocate 256.00 MiB on "
    "shared pool. Use PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0 to disable upper "
    "limit for memory allocations (may cause system failure)."
)


def _fake_torch(*, cuda=False, mps=False, cuda_raises=None, mps_raises=None):
    """A stand-in torch exposing only the two availability probes."""

    def cuda_available():
        if cuda_raises is not None:
            raise cuda_raises
        return cuda

    def mps_available():
        if mps_raises is not None:
            raise mps_raises
        return mps

    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=cuda_available,
            empty_cache=lambda: None,
        ),
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=mps_available)
        ),
        mps=types.SimpleNamespace(empty_cache=lambda: None),
    )


@pytest.fixture
def fake_torch(monkeypatch):
    """Install a stand-in torch in ``sys.modules``.

    Both ``detect_device`` (which imports torch) and ``empty_device_cache``
    (which reads ``sys.modules``) resolve it from there, so seeding the module
    table covers both without either touching real hardware.
    """

    def apply(mod):
        monkeypatch.setitem(sys.modules, "torch", mod)
        return mod

    return apply


# --------------------------------------------------------------------------- #
# detect_device
# --------------------------------------------------------------------------- #


def test_detects_mps_when_no_cuda(fake_torch):
    fake_torch(_fake_torch(cuda=False, mps=True))
    assert detect_device() == "mps"


def test_cuda_wins_over_mps(fake_torch):
    # Never both in practice; the order is asserted so it stays deliberate.
    fake_torch(_fake_torch(cuda=True, mps=True))
    assert detect_device() == "cuda"


def test_cpu_when_neither_available(fake_torch):
    fake_torch(_fake_torch(cuda=False, mps=False))
    assert detect_device() == "cpu"


def test_cuda_probe_failure_falls_through_to_mps(fake_torch):
    # A broken CUDA install must degrade to the next device, not propagate.
    fake_torch(_fake_torch(cuda_raises=RuntimeError("no CUDA driver"), mps=True))
    assert detect_device() == "mps"


def test_mps_probe_failure_falls_back_to_cpu(fake_torch):
    fake_torch(_fake_torch(cuda=False, mps_raises=RuntimeError("Metal is broken")))
    assert detect_device() == "cpu"


def test_missing_torch_reports_cpu(monkeypatch):
    # An import that raises must answer "cpu", not abort the caller.
    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def boom(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr("builtins.__import__", boom)
    assert detect_device() == "cpu"


# --------------------------------------------------------------------------- #
# is_accelerator
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "device,expected",
    [
        ("cuda", True),
        ("mps", True),
        ("cuda:0", True),
        ("mps:0", True),
        ("MPS", True),
        ("cpu", False),
        (None, False),
    ],
)
def test_is_accelerator(device, expected):
    assert is_accelerator(device) is expected


def test_is_accelerator_accepts_torch_device_objects():
    # Call sites hold both a string and a torch.device; both must answer.
    fake_device = types.SimpleNamespace(type="mps")
    assert is_accelerator(fake_device) is True
    assert is_accelerator(types.SimpleNamespace(type="cpu")) is False


# --------------------------------------------------------------------------- #
# OOM classification
# --------------------------------------------------------------------------- #


def test_mps_oom_message_is_classified_as_device_oom():
    # Regression: Metal names itself and nothing else, so before "mps" joined
    # the device words this returned False and the CPU-spillover path was dead.
    assert is_vram_oom(RuntimeError(MPS_OOM_MESSAGE)) is True


def test_mps_oom_is_found_through_a_wrapped_cause():
    inner = RuntimeError(MPS_OOM_MESSAGE)
    outer = RuntimeError("tagging failed")
    outer.__cause__ = inner
    assert is_vram_oom(outer) is True


def test_sqlite_out_of_memory_is_still_not_a_device_oom():
    # The reason the device-word list exists at all; adding "mps" must not
    # loosen it into matching every "out of memory" string.
    assert is_vram_oom(RuntimeError("database or disk is full: out of memory")) is False


# --------------------------------------------------------------------------- #
# empty_device_cache
# --------------------------------------------------------------------------- #


def test_empty_device_cache_flushes_mps(fake_torch):
    calls = []
    mod = _fake_torch(cuda=False, mps=True)
    mod.mps.empty_cache = lambda: calls.append("mps")
    mod.cuda.empty_cache = lambda: calls.append("cuda")
    fake_torch(mod)

    assert empty_device_cache() is True
    assert calls == ["mps"]


def test_empty_device_cache_is_a_noop_without_torch(monkeypatch):
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert empty_device_cache() is False


# --------------------------------------------------------------------------- #
# Start-up device check
# --------------------------------------------------------------------------- #


def _checks(device):
    return StartupChecks(
        {"default_device": device}, "/tmp/server_config.json", logging.getLogger("test")
    )


@pytest.fixture
def patch_runtime(monkeypatch):
    """Seed ``startup_checks``' cached torch/ort accessors with stand-ins."""

    def apply(torch_mod, ort_mod=None):
        monkeypatch.setattr(sc, "_torch_mod", torch_mod)
        monkeypatch.setattr(
            sc,
            "_ort_mod",
            ort_mod
            or types.SimpleNamespace(
                get_available_providers=lambda: ["CPUExecutionProvider"]
            ),
        )

    return apply


def test_auto_mode_accepts_mps_instead_of_forcing_cpu(patch_runtime):
    # The bug this whole change exists for: on Apple Silicon torch.cuda is
    # False, which auto mode used to read as "no GPU" and answer with CPU.
    patch_runtime(_fake_torch(cuda=False, mps=True))
    outcome = StartupCheckOutcome()
    _checks("auto")._check_device_and_vram(outcome)

    assert not outcome.forced_cpu
    assert not outcome.hard_failures
    assert "Metal" in " ".join(outcome.notes)


def test_explicit_mps_is_accepted(patch_runtime):
    patch_runtime(_fake_torch(cuda=False, mps=True))
    outcome = StartupCheckOutcome()
    _checks("mps")._check_device_and_vram(outcome)

    assert not outcome.forced_cpu
    assert not outcome.hard_failures


def test_explicit_mps_without_metal_falls_back_to_cpu(patch_runtime):
    patch_runtime(_fake_torch(cuda=False, mps=False))
    outcome = StartupCheckOutcome()
    _checks("mps")._check_device_and_vram(outcome)

    assert outcome.forced_cpu
    assert not outcome.hard_failures
    assert any("Metal (MPS) is unavailable" in w for w in outcome.warnings)


def test_auto_without_any_gpu_still_forces_cpu(patch_runtime):
    # Regression guard: adding the Metal branch must not stop a plain CPU host
    # from being reported as forced-CPU.
    patch_runtime(_fake_torch(cuda=False, mps=False))
    outcome = StartupCheckOutcome()
    _checks("auto")._check_device_and_vram(outcome)

    assert outcome.forced_cpu


def test_mps_is_a_valid_configured_device():
    outcome = StartupCheckOutcome()
    checks = _checks("mps")
    checks._check_config_sanity(outcome)
    assert not any("default_device must be one of" in f for f in outcome.hard_failures)


# --------------------------------------------------------------------------- #
# Real hardware
# --------------------------------------------------------------------------- #


def _mps_present() -> bool:
    try:
        import torch

        return bool(torch.backends.mps.is_available())
    except Exception:
        return False


@pytest.mark.skipif(not _mps_present(), reason="requires an Apple Metal GPU")
def test_tagger_promotes_to_fp16_on_real_metal(tmp_path):
    """The tagger loads onto Metal in fp16, the same as it does on CUDA."""
    import json

    import torch
    from safetensors.torch import save_file
    from torchvision.models import convnext_tiny

    from pixlstash.tagger_plugins.pixlstash_tagger import (
        PIXLSTASH_TAGGER_FILENAME,
        PIXLSTASH_TAGGER_META_FILENAME,
        PixlStashTaggerService,
    )

    labels = ["blocky", "noisy"]
    model = convnext_tiny(weights=None)
    model.classifier[2] = torch.nn.Linear(model.classifier[2].in_features, len(labels))
    save_file(model.state_dict(), str(tmp_path / PIXLSTASH_TAGGER_FILENAME))
    (tmp_path / PIXLSTASH_TAGGER_META_FILENAME).write_text(
        json.dumps({"labels": labels, "arch": "convnext_tiny", "version": 1})
    )

    service = PixlStashTaggerService(
        device="mps", model_dir=str(tmp_path), batch_size_fn=lambda: 2
    )
    service.init()
    try:
        param = next(service._model.parameters())
        assert param.device.type == "mps"
        assert param.dtype is torch.float16
        assert service._dtype is torch.float16
    finally:
        service.unload()


# --------------------------------------------------------------------------- #
# Florence-2 stays on the CPU under Metal
# --------------------------------------------------------------------------- #


def test_florence_loads_on_cpu_when_engine_is_on_metal(monkeypatch):
    """Florence-2 must not be handed to Metal, even when the engine is on it.

    Its weights are materialised by transformers' threaded loader, which
    segfaults writing into Metal tensors (see the comment in ``_init``). This
    pins the exemption: every other model follows the engine's device, so a
    future refactor that "tidies" this branch away would reintroduce a crash
    that only shows up on Apple hardware, and only some of the time.
    """
    from pixlstash.tagger_plugins.florence2 import (
        FLORENCE_BATCH_SIZE_CPU,
        Florence2Service,
    )

    loaded_on = []
    service = Florence2Service(device="mps")
    monkeypatch.setattr(
        service,
        "_load_model",
        lambda device, dtype: loaded_on.append((str(device), dtype)),
    )
    service._init()

    assert loaded_on, "_init did not attempt to load the model at all"
    device, dtype = loaded_on[-1]
    assert device == "cpu"
    assert str(dtype) == "torch.float32"
    assert service._batch_size == FLORENCE_BATCH_SIZE_CPU


def test_florence_still_uses_cuda_when_available(monkeypatch):
    # Regression guard for the branch above: the Metal exemption must not
    # capture CUDA on the way past.
    import torch

    from pixlstash.tagger_plugins.florence2 import (
        FLORENCE_BATCH_SIZE_GPU,
        Florence2Service,
    )

    if not torch.cuda.is_available():
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    loaded_on = []
    service = Florence2Service(device="cuda")
    monkeypatch.setattr(
        service,
        "_load_model",
        lambda device, dtype: loaded_on.append((str(device), dtype)),
    )
    service._init()

    assert loaded_on[-1][0] == "cuda"
    assert service._batch_size == FLORENCE_BATCH_SIZE_GPU
