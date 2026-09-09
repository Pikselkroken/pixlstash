"""Apple Metal (MPS) support across device detection, OOM classification and startup.

No test here needs a Metal GPU: the detection and start-up paths are driven with
a stand-in torch module, following ``test_rocm_device_check.py``. The one test
that does need real hardware is skipped when MPS is absent, so the file runs the
same on a CI Linux runner as on an Apple laptop.
"""

import contextlib
import logging
import sys
import threading
import types

import numpy as np
import pytest

import pixlstash.startup_checks as sc
from pixlstash.startup_checks import StartupCheckOutcome, StartupChecks
from pixlstash.utils.device_utils import (
    detect_device,
    empty_device_cache,
    is_accelerator,
)
from pixlstash.utils.vram_utils import is_device_error, is_vram_oom

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


# --------------------------------------------------------------------------- #
# is_device_error: one predicate for "the accelerator failed, retry on CPU"
# --------------------------------------------------------------------------- #


def test_metal_oom_is_a_device_error():
    # The behaviour that matters, asserted on the real message. Two branches
    # can carry it (is_vram_oom's word list, and "mps backend"); the tests
    # below isolate each of them.
    assert is_device_error(RuntimeError(MPS_OOM_MESSAGE), "mps") is True


def test_cuda_oom_is_still_a_device_error():
    assert is_device_error(RuntimeError("CUDA out of memory"), "cuda") is True


def test_an_incompatible_build_is_a_device_error_without_naming_a_device():
    # Isolates "not compatible". A message that also said "cuda" would pass on
    # that word alone and prove nothing about this entry, which is the residue
    # of the three services' own matchers being unified.
    error = RuntimeError("the installed build is not compatible with this device")
    assert is_vram_oom(error) is False, "not an OOM, so nothing else catches it"
    assert is_device_error(error, "cuda") is True


def test_a_metal_failure_that_is_not_an_oom_is_a_device_error():
    # Isolates "mps backend": Metal's non-memory failures, which "cuda" cannot
    # cover and is_vram_oom does not, because they are not OOM.
    error = RuntimeError("MPS backend does not support this operation")
    assert is_vram_oom(error) is False
    assert is_device_error(error, "mps") is True


def test_the_onnxruntime_arena_message_is_a_device_error():
    # Isolates the is_vram_oom branch. ORT's BFC arena names no device and does
    # not say "out of memory", so none of the words below reach it.
    error = RuntimeError("Failed to allocate memory for requested buffer of size 12345")
    assert is_device_error(error, "cuda") is True


def test_a_driver_fault_is_a_device_error_from_its_type_alone():
    # Isolates the CudaError branch: the message is whatever the driver
    # returned, so type identity is the only thing that can classify it.
    torch = pytest.importorskip("torch")
    cuda_error = getattr(torch.cuda, "CudaError", None)
    if cuda_error is None:
        pytest.skip("this torch build has no torch.cuda.CudaError")
    # Built with __new__: CudaError.__init__ calls into the CUDA runtime, which
    # is absent on the machines this test is meant to run on.
    error = cuda_error.__new__(cuda_error)
    Exception.__init__(error, "device-side assert triggered")

    assert is_vram_oom(error) is False
    assert is_device_error(error, "cuda") is True


def test_an_ordinary_error_is_not_a_device_error():
    assert is_device_error(RuntimeError("shape mismatch"), "mps") is False


def test_nothing_is_a_device_error_while_running_on_the_cpu():
    # Without the device guard a CPU-side failure whose text happens to name a
    # backend would trigger a pointless reload onto the CPU it already runs on.
    assert is_device_error(RuntimeError(MPS_OOM_MESSAGE), "cpu") is False
    assert is_device_error(RuntimeError("CUDA out of memory"), "cpu") is False


# --------------------------------------------------------------------------- #
# The model services recover on Metal, not only on CUDA
# --------------------------------------------------------------------------- #


class _FakeTensor:
    """Enough of a tensor for the CLIP paths: moves, casts and normalises."""

    def __init__(self, value=1.0):
        self.value = value
        self.device = "cpu"

    def to(self, device):
        self.device = str(device)
        return self

    def half(self):
        return self

    def float(self):
        return self

    def cpu(self):
        return self

    def unsqueeze(self, _dim):
        return self

    def norm(self, dim=None, keepdim=False):
        return self

    def __truediv__(self, _other):
        return self

    def numpy(self):
        return np.array([[self.value]], dtype=np.float32)


def _tensor_torch(**availability):
    """A stand-in torch carrying the tensor operations CLIP calls."""
    torch = _fake_torch(**availability)
    torch.stack = lambda tensors: _FakeTensor()
    torch.no_grad = contextlib.nullcontext
    return torch


class _FakeClipModel:
    """Fails on the accelerator, succeeds once moved to the CPU."""

    def __init__(self, error):
        self._error = error
        self.device = "mps"
        self.calls = []

    def encode_image(self, tensors):
        self.calls.append(self.device)
        if self.device != "cpu":
            raise self._error
        return _FakeTensor()

    def float(self):
        return self

    def to(self, device):
        self.device = str(device)
        return self


def _loaded_clip(device, error):
    """A ClipService that is already "loaded", so nothing is downloaded."""
    from pixlstash.tagger_plugins.clip_service import ClipService

    service = ClipService(device=device)
    service._model = _FakeClipModel(error)
    service._preprocess = lambda img: _FakeTensor()
    service._tokenizer = lambda texts: _FakeTensor()
    return service


def test_clip_batch_falls_back_to_cpu_on_a_metal_oom(fake_torch):
    fake_torch(_tensor_torch(cuda=False, mps=True))
    service = _loaded_clip("mps", RuntimeError(MPS_OOM_MESSAGE))

    result = service.encode_image_batch([object()])

    assert result is not None, "a Metal OOM must retry on the CPU, not give up"
    assert service._device == "cpu"
    assert service._model.calls == ["mps", "cpu"]


def test_clip_crops_fall_back_to_cpu_on_a_metal_oom(fake_torch):
    fake_torch(_tensor_torch(cuda=False, mps=True))
    service = _loaded_clip("mps", RuntimeError(MPS_OOM_MESSAGE))

    results = service.encode_image_crops([object()], pic_desc="a picture")

    assert results[0] is not None
    assert service._device == "cpu"


def test_clip_still_falls_back_on_a_cuda_error(fake_torch):
    # Positive control: widening to Metal must not narrow the CUDA path.
    fake_torch(_tensor_torch(cuda=True, mps=False))
    service = _loaded_clip("cuda", RuntimeError("CUDA out of memory"))

    assert service.encode_image_batch([object()]) is not None
    assert service._device == "cpu"


def test_clip_does_not_retry_an_ordinary_failure(fake_torch):
    # Negative control: the retry must stay tied to device failures. An
    # everyday RuntimeError is reported and returns None, as it always has.
    fake_torch(_tensor_torch(cuda=False, mps=True))
    service = _loaded_clip("mps", RuntimeError("shape mismatch"))

    assert service.encode_image_batch([object()]) is None
    assert service._device == "mps", "an ordinary error must not move the model"


def test_clip_flushes_the_metal_cache_when_it_spills_to_the_cpu(fake_torch):
    flushed = []
    torch = _tensor_torch(cuda=False, mps=True)
    torch.mps.empty_cache = lambda: flushed.append("mps")
    torch.cuda.empty_cache = lambda: flushed.append("cuda")
    fake_torch(torch)

    _loaded_clip("mps", RuntimeError(MPS_OOM_MESSAGE)).encode_image_batch([object()])

    assert flushed == ["mps"], "the freed device is Metal, so Metal is flushed"


class _FakeSbertModel:
    def __init__(self, error, device):
        self._error = error
        self.device = device

    def encode(self, texts, show_progress_bar=False):
        if self.device != "cpu":
            raise self._error
        return np.zeros((len(texts), 3), dtype=np.float32)


def _loaded_sbert(monkeypatch, device, error):
    from pixlstash.tagger_plugins import sbert as sbert_module

    monkeypatch.setattr(
        sbert_module,
        "load_sentence_transformer",
        lambda *a, **kw: _FakeSbertModel(error, kw.get("device", "cpu")),
    )
    service = sbert_module.SBertService(device=device)
    service._model = _FakeSbertModel(error, device)
    return service


def test_sbert_falls_back_to_cpu_on_a_metal_oom(monkeypatch):
    service = _loaded_sbert(monkeypatch, "mps", RuntimeError(MPS_OOM_MESSAGE))

    embeddings = service.encode(["a caption"])

    assert len(embeddings) == 1
    assert service._device == "cpu"


def test_sbert_still_falls_back_on_a_cuda_error(monkeypatch):
    service = _loaded_sbert(monkeypatch, "cuda", RuntimeError("CUDA out of memory"))

    assert len(service.encode(["a caption"])) == 1
    assert service._device == "cpu"


def test_sbert_reraises_an_ordinary_failure(monkeypatch):
    service = _loaded_sbert(monkeypatch, "mps", RuntimeError("shape mismatch"))

    with pytest.raises(RuntimeError, match="shape mismatch"):
        service.encode(["a caption"])


def test_joycaption_unload_flushes_the_metal_cache(fake_torch, monkeypatch):
    # Imported before the stand-in is installed: joycaption binds torch at
    # module scope, so importing it under the fake would leave every later
    # test in the session holding a SimpleNamespace instead of torch.
    from pixlstash.tagger_plugins import joycaption as jc

    flushed = []
    torch = _fake_torch(cuda=False, mps=True)
    torch.mps.empty_cache = lambda: flushed.append("mps")
    torch.cuda.empty_cache = lambda: flushed.append("cuda")
    fake_torch(torch)

    service = jc.JoyCaptionService.__new__(jc.JoyCaptionService)
    service._load_lock = threading.RLock()
    service._model = object()
    service._processor = object()

    service.unload()

    assert flushed == ["mps"], "a Metal unload freed nothing before this"
    assert service._model is None


def test_joycaption_records_metal_as_the_model_device(monkeypatch):
    """The device it reports must be the one Accelerate actually loaded onto.

    ``device_map="auto"`` places the weights on whatever accelerator exists.
    Reading the device back off ``torch.cuda`` answered "cpu" on a Mac while
    the model sat on Metal, and every input was then sent to the CPU to meet
    a model that was not there.
    """
    import torch

    from pixlstash.tagger_plugins import joycaption as jc

    monkeypatch.setattr(jc, "detect_device", lambda: "mps")
    monkeypatch.setattr(
        jc,
        "from_pretrained_local_first",
        lambda cls, name, **kw: types.SimpleNamespace(
            eval=lambda: None,
            image_processor=None,
            tokenizer=None,
        ),
    )

    service = jc.JoyCaptionService(device="mps", precision="fp16")
    service._init()

    assert service._model_device == torch.device("mps")


def test_joycaption_still_records_cpu_when_the_cpu_was_asked_for(monkeypatch):
    # Positive control: an explicit cpu request is honoured, not overridden.
    import torch

    from pixlstash.tagger_plugins import joycaption as jc

    monkeypatch.setattr(jc, "detect_device", lambda: "mps")
    monkeypatch.setattr(
        jc,
        "from_pretrained_local_first",
        lambda cls, name, **kw: types.SimpleNamespace(
            eval=lambda: None,
            image_processor=None,
            tokenizer=None,
        ),
    )

    service = jc.JoyCaptionService(device="cpu", precision="fp16")
    service._init()

    assert service._model_device == torch.device("cpu")
