"""Apple Metal (MPS) support, and the device handling around it on every platform.

Stand-ins replace torch's device probes and the models, so these run on any
host. The tests that need a real Apple Metal GPU are marked and skip without one.
"""

import contextlib
import functools
import gc
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import types
import warnings
import weakref

import numpy as np
import pytest
from PIL import Image

import pixlstash.inference.model_lifecycle as model_lifecycle_module
import pixlstash.startup_checks as sc
import pixlstash.task_runner as task_runner_module
from pixlstash.database import VaultDatabase
from pixlstash.image_plugins.base import ImagePlugin
from pixlstash.image_plugins.service import _run_plugin
from pixlstash.inference.engine import InferenceEngine
from pixlstash.inference.model_lifecycle import ModelLifecycleManager
from pixlstash.inference.vram_budget import MAX_CONCURRENT_GPU_IMAGES, VramBudget
from pixlstash.inference.workflows.tagging import (
    _MAX_CONCURRENT_CPU as TAGGING_MAX_CONCURRENT_CPU,
    TaggingWorkflow,
)
from pixlstash.services import library_switch_service
from pixlstash.startup_checks import StartupCheckOutcome, StartupChecks
from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService
from pixlstash.task_runner import (
    TaskCancelledError,
    TaskRunner,
    TaskRunnerNotRunningError,
)
from pixlstash.tasks.base_task import (
    BaseTask,
    QueueType,
    TaskInterruptedError,
    TaskPriority,
    TaskStatus,
)
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.gpu_call_task import GpuCallTask
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.tag_task import TagTask
from pixlstash.utils import device_utils
from pixlstash.utils.device_utils import (
    ACCELERATORS,
    HF_ASYNC_LOAD_ENV,
    USE_GPU_ADVICE,
    configure_metal_model_loading,
    detect_device,
    empty_device_cache,
    ensure_metal_thread,
    is_accelerator,
    register_metal_thread,
    registered_metal_thread,
)
from pixlstash.utils.vram_utils import _DEVICE_FAULTS, is_device_error, is_vram_oom
from pixlstash.vault import Vault

# The MPS_* messages are what torch 2.13 raises on Apple Metal; sizes vary.
MPS_UNIMPLEMENTED_OP_MESSAGE = (
    "The operator 'aten::linalg_eig' is not currently implemented for the MPS "
    "device. If you want this op to be considered for addition please comment "
    "on https://github.com/pytorch/pytorch/issues/141287 and mention use-case, "
    "that resulted in missing op as well as commit hash "
    "cf30153c4c131c8164ee7798e5022d810682e2cb. As a temporary fix, you can set "
    "the environment variable `PYTORCH_ENABLE_MPS_FALLBACK=1` to use the CPU as "
    "a fallback for this op. WARNING: this will be slower than running natively "
    "on MPS."
)

MPS_FLOAT64_MESSAGE = (
    "Cannot convert a MPS Tensor to float64 dtype as the MPS framework doesn't "
    "support float64. Please use float32 instead."
)

MPS_DEVICE_MISMATCH_MESSAGE = (
    "Expected all tensors to be on the same device, but found at least two "
    "devices, mps:0 and cpu!"
)

MPS_ARGUMENT_ON_CPU_MESSAGE = "Tensor for argument weight is on cpu but expected on mps"

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


@pytest.fixture(autouse=True)
def no_metal_thread_from_elsewhere(monkeypatch):
    """Start every test with no GPU worker registered as the Metal thread.

    The registration is process-wide, and a runner another module left running
    would make every ``mps`` service call here fail the thread guard. Tests
    that need a registration start a runner of their own, and the value that
    was there before is put back afterwards.
    """
    monkeypatch.setattr(device_utils, "_metal_thread", None)


# --------------------------------------------------------------------------- #
# detect_device
# --------------------------------------------------------------------------- #


def test_cpu_when_neither_available(fake_torch):
    fake_torch(_fake_torch(cuda=False, mps=False))
    assert detect_device() == "cpu"


@pytest.mark.parametrize(
    "failure",
    [ImportError("no torch"), OSError("dlopen(libtorch_cpu.dylib): image not found")],
    ids=["import-error", "shared-library"],
)
def test_missing_torch_reports_cpu(monkeypatch, failure):
    # An import that raises must answer "cpu", not abort the caller. A torch
    # whose shared libraries will not load raises OSError, not ImportError.
    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def boom(name, *args, **kwargs):
        if name == "torch":
            raise failure
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr("builtins.__import__", boom)
    assert detect_device() == "cpu"


def _device_warnings(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "pixlstash.utils.device_utils"
        and record.levelno == logging.WARNING
    ]


@pytest.mark.parametrize(
    "torch_mod,expected,named",
    [
        (None, "cpu", "torch could not be imported"),
    ],
    ids=["import"],
)
def test_a_broken_torch_is_logged_at_warning(
    monkeypatch, caplog, torch_mod, expected, named
):
    """A device lost to a broken install has to say why at the default level.

    detect_device never raises, so its callers - the engine, JoyCaption,
    ``plugins test`` - cannot report the reason themselves. ``None`` in
    ``sys.modules`` makes ``import torch`` raise.
    """
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    with caplog.at_level(logging.WARNING):
        assert detect_device() == expected

    warnings_logged = _device_warnings(caplog)
    assert len(warnings_logged) == 1, warnings_logged
    assert named in warnings_logged[0]


@pytest.mark.parametrize("cuda,mps", [(False, False), (True, False), (False, True)])
def test_a_working_torch_detects_without_a_warning(fake_torch, caplog, cuda, mps):
    # The control for the test above: a CPU-only host answers False from both
    # probes, so the warnings must not fire on every ordinary machine.
    fake_torch(_fake_torch(cuda=cuda, mps=mps))
    with caplog.at_level(logging.WARNING):
        detect_device()

    assert _device_warnings(caplog) == []


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
    # Building a torch.device touches no hardware.
    import torch

    assert is_accelerator(torch.device("mps")) is True
    assert is_accelerator(torch.device("cuda", 1)) is True
    assert is_accelerator(torch.device("cpu")) is False


@pytest.mark.parametrize("device", ["mps:0", "cuda:1", "MPS:0"])
def test_is_accelerator_ignores_the_device_index(device):
    """``str(torch.device("mps:0"))`` carries an index; the type alone does not.

    Both spellings reach these call sites, so the index has to be stripped
    before the lookup. Without this the string form of an indexed device
    reads as "not an accelerator" and skips the retry path entirely.
    """
    assert is_accelerator(device) is True


# --------------------------------------------------------------------------- #
# OOM classification
# --------------------------------------------------------------------------- #


def test_mps_oom_message_is_classified_as_device_oom():
    # Metal's OOM text carries none of the other device words; "mps" is what
    # makes it read as a device OOM.
    assert is_vram_oom(RuntimeError(MPS_OOM_MESSAGE)) is True


def test_mps_oom_is_found_through_a_wrapped_cause():
    inner = RuntimeError(MPS_OOM_MESSAGE)
    outer = RuntimeError("tagging failed")
    outer.__cause__ = inner
    assert is_vram_oom(outer) is True


@pytest.mark.parametrize(
    "message",
    [
        "out of memory while sizing the clamps table",
        "sqlite3.OperationalError: out of memory [SQL: SELECT created_timestamps]",
        "json.dumps failed: out of memory",
    ],
    ids=["clamps", "timestamps", "dumps"],
)
def test_a_word_ending_in_mps_is_not_a_device_oom_either(message):
    """is_vram_oom reads "mps" as a word, never as a substring.

    As a substring, any "out of memory" whose text contained
    clamps/temps/timestamps/dumps would read as GPU pressure - and is_vram_oom
    gates TaskRunner's three VRAM-OOM retries and, through is_device_error,
    every service's CPU retry.
    """
    error = RuntimeError(message)
    assert is_vram_oom(error) is False
    assert is_device_error(error, "mps") is False


def test_sqlite_out_of_memory_is_still_not_a_device_oom():
    # The reason the device-word list exists at all: "mps" among the device
    # words must not make every "out of memory" string match.
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


@pytest.mark.parametrize(
    "device,expected",
    [("mps", ["mps"]), ("cuda", ["cuda"]), (None, ["cuda", "mps"])],
    ids=["mps-only", "cuda-only", "both"],
)
def test_empty_device_cache_flushes_only_the_named_backend(
    fake_torch, device, expected
):
    """A named device flushes that backend alone; no device flushes both.

    Both backends are available here, so a filter that let every backend
    through would flush both for a named device.
    """
    flushed = []
    torch = _fake_torch(cuda=True, mps=True)
    torch.cuda.empty_cache = lambda: flushed.append("cuda")
    torch.mps.empty_cache = lambda: flushed.append("mps")
    fake_torch(torch)

    assert empty_device_cache(device) is True
    assert flushed == expected


def test_empty_cuda_cache_flushes_metal_despite_its_name(fake_torch):
    """The alias the teardown paths call flushes Metal too.

    task_runner and model_lifecycle call this name, not empty_device_cache.
    """
    from pixlstash.utils.vram_utils import empty_cuda_cache

    flushed = []
    torch = _fake_torch(cuda=False, mps=True)
    torch.mps.empty_cache = lambda: flushed.append("mps")
    fake_torch(torch)

    assert empty_cuda_cache() is True
    assert flushed == ["mps"]


@pytest.mark.parametrize(
    "cuda,mps,expected",
    [(True, False, ["cuda"]), (False, False, [])],
    ids=["cuda-only-host", "cpu-only-host"],
)
def test_empty_device_cache_skips_a_backend_that_is_not_available(
    fake_torch, cuda, mps, expected
):
    """A host without Metal never calls torch.mps.empty_cache, and the reverse.

    Flushing an absent backend raises, and every teardown on a CUDA host would
    then log a flush failure.
    """
    flushed = []
    torch = _fake_torch(cuda=cuda, mps=mps)
    torch.cuda.empty_cache = lambda: flushed.append("cuda")
    torch.mps.empty_cache = lambda: flushed.append("mps")
    fake_torch(torch)

    assert empty_device_cache() is bool(expected)
    assert flushed == expected


def test_empty_device_cache_is_a_noop_without_torch(monkeypatch):
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert empty_device_cache() is False


@pytest.mark.parametrize(
    "backend,label",
    [("cuda", "CUDA"), ("mps", "Apple Metal")],
    ids=["cuda", "mps"],
)
def test_a_failing_flush_is_logged_at_warning(fake_torch, caplog, backend, label):
    """A flush that raises has to be visible at the default log level.

    task_runner wraps every flush in a WARNING handler, but the helper catches
    the exception itself, so that handler never fires; this log line is the
    only place the failure can show.
    """
    torch = _fake_torch(cuda=backend == "cuda", mps=backend == "mps")

    def _raise():
        raise RuntimeError("allocator is wedged")

    getattr(torch, backend).empty_cache = _raise
    fake_torch(torch)

    with caplog.at_level(logging.WARNING, logger="pixlstash.utils.device_utils"):
        assert empty_device_cache(backend) is False

    warnings_logged = _device_warnings(caplog)
    assert len(warnings_logged) == 1, caplog.text
    assert label in warnings_logged[0]
    assert "allocator is wedged" in warnings_logged[0]


# --------------------------------------------------------------------------- #
# The device is announced once, where it is actually resolved
# --------------------------------------------------------------------------- #


def _engine_device_log(
    monkeypatch, caplog, *, detected, force_cpu=False, device=None, cpu_spillover=False
):
    """Capture what InferenceEngine.create says about the device it chose."""
    from pixlstash.inference import engine as engine_mod

    monkeypatch.setattr(engine_mod, "builtin_model_dir", lambda: "/nonexistent")
    monkeypatch.setattr(engine_mod, "detect_device", lambda: detected)
    # Stop at the first service construction: the device is already resolved
    # and logged by then, and building the rest would load models.
    monkeypatch.setattr(
        "pixlstash.tagger_plugins.clip_service.ClipService",
        lambda **kw: (_ for _ in ()).throw(_StopEarly()),
    )
    with caplog.at_level("INFO"):
        try:
            engine_mod.InferenceEngine.create(
                image_root="/nonexistent",
                force_cpu=force_cpu,
                device=device,
                cpu_spillover=cpu_spillover,
            )
        except _StopEarly:
            pass
    return caplog.text


class _StopEarly(Exception):
    """Marker: the device has been resolved, no need to build the engine."""


def test_metal_is_announced_at_info(monkeypatch, caplog):
    """A Mac owner has to be able to tell the GPU is in use.

    The other device telemetry at the default log level reads nvidia-smi, so
    this line is what tells a working Metal install from a CPU one. The
    start-up check's note is logged at DEBUG.
    """
    text = _engine_device_log(monkeypatch, caplog, detected="mps")
    assert "Inference device: Apple Metal (mps) (detected)" in text
    assert "inference will be slow" not in text


def test_cpu_says_why_it_is_on_the_cpu(monkeypatch, caplog):
    # "CPU with a GPU sitting unused" and "CPU because there is no GPU" are
    # different problems, and only one of them is worth investigating.
    forced = _engine_device_log(monkeypatch, caplog, detected="mps", force_cpu=True)
    assert "forced; apple metal (mps) is present but not used" in forced.lower()

    caplog.clear()
    absent = _engine_device_log(monkeypatch, caplog, detected="cpu")
    assert "no supported gpu" in absent.lower()
    assert "inference will be slow" in absent


def test_a_forced_cpu_without_a_gpu_is_not_called_forced(monkeypatch, caplog):
    """The server always arrives with force_cpu, including on a CPU-only host.

    The start-up checks set forced_cpu when auto mode finds no GPU, and the
    vault hands that on as force_cpu=True, so a plain CPU machine reaches the
    forced branch. There, "forced" would send the owner looking for a setting
    they never made.
    """
    text = _engine_device_log(monkeypatch, caplog, detected="cpu", force_cpu=True)

    assert "no supported gpu" in text.lower()
    assert "forced" not in text.lower()


def test_a_cpu_spillover_engine_is_not_read_as_a_machine_on_the_CPU(
    monkeypatch, caplog
):
    """A GPU host builds these beside its GPU engine, which keeps the GPU.

    The line every owner greps for is this one, so a spillover engine
    announcing "CPU (configured) - inference will be slow" on a CUDA machine
    reads as the GPU having been lost.
    """
    text = _engine_device_log(
        monkeypatch, caplog, detected="cuda", device="cpu", cpu_spillover=True
    )

    assert "CPU spillover" in text
    assert "inference will be slow" not in text
    assert "(configured)" not in text


def test_both_spillover_factories_say_what_they_are_building(monkeypatch, caplog):
    """The announcement above is only right if the call sites pass the flag."""
    from pixlstash.inference import engine as engine_mod
    from pixlstash.tasks.description_task import DescriptionTask
    from pixlstash.tasks.tag_task import TagTask

    monkeypatch.setattr(engine_mod, "builtin_model_dir", lambda: "/nonexistent")
    monkeypatch.setattr(engine_mod, "detect_device", lambda: "cuda")
    monkeypatch.setattr(
        "pixlstash.tagger_plugins.clip_service.ClipService",
        lambda **kw: (_ for _ in ()).throw(_StopEarly()),
    )

    for owner in (TagTask, DescriptionTask):
        monkeypatch.setattr(owner, "_cpu_spillover_engine", None)
        caplog.clear()
        with caplog.at_level("INFO"), pytest.raises(_StopEarly):
            owner._acquire_cpu_spillover_engine("/nonexistent")

        assert "CPU spillover" in caplog.text, owner.__name__
        assert "inference will be slow" not in caplog.text, owner.__name__


def test_cuda_is_announced_too(monkeypatch, caplog):
    # Positive control: this is not a Metal-only courtesy.
    text = _engine_device_log(monkeypatch, caplog, detected="cuda")
    assert "Inference device: CUDA (detected)" in text
    assert "no supported GPU" not in text
    assert "inference will be slow" not in text


def test_a_device_passed_in_is_announced_as_configured(monkeypatch, caplog):
    # Named by the caller rather than detected, so the log must not claim it
    # was found on the machine.
    text = _engine_device_log(monkeypatch, caplog, detected="cpu", device="mps")
    assert "Inference device: Apple Metal (mps) (configured)" in text


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


def test_explicit_cuda_without_torch_still_names_cuda(patch_runtime):
    # An unimportable torch is a reason to refuse a named device, not to
    # rewrite the config to cpu behind the owner's back.
    patch_runtime(None)
    outcome = StartupCheckOutcome()
    _checks("cuda")._check_device_and_vram(outcome)

    assert any("default_device is set to cuda" in f for f in outcome.hard_failures)


@pytest.mark.parametrize("configured", ["cuda", "gpu"])
def test_an_explicit_cuda_on_a_mac_with_metal_still_refuses(patch_runtime, configured):
    """Metal is accepted in auto mode only.

    The owner named CUDA, which a Mac does not have. Starting on Metal instead
    would be the same silent swap as starting on the CPU.
    """
    patch_runtime(_fake_torch(cuda=False, mps=True))
    outcome = StartupCheckOutcome()
    _checks(configured)._check_device_and_vram(outcome)

    assert any("default_device is set to cuda" in f for f in outcome.hard_failures), (
        outcome.hard_failures
    )
    assert not outcome.forced_cpu
    assert "Metal" not in " ".join(outcome.notes)


def test_auto_without_torch_still_forces_cpu_without_refusing(patch_runtime):
    # The other positive control: auto mode named no device, so it must keep
    # falling back quietly rather than refusing to boot.
    patch_runtime(None)
    outcome = StartupCheckOutcome()
    _checks("auto")._check_device_and_vram(outcome)

    assert outcome.forced_cpu
    assert not outcome.hard_failures


def test_auto_without_any_gpu_still_forces_cpu(patch_runtime):
    # A host with neither CUDA nor Metal is still reported as forced-CPU.
    patch_runtime(_fake_torch(cuda=False, mps=False))
    outcome = StartupCheckOutcome()
    _checks("auto")._check_device_and_vram(outcome)

    assert outcome.forced_cpu


@pytest.mark.parametrize("configured", ["auto", "cpu"])
def test_metal_does_not_warn_about_a_missing_nvidia_smi(
    patch_runtime, monkeypatch, configured
):
    """nvidia-smi will never exist on a Mac, so warning about it is noise.

    The warning guards VRAM telemetry, which is read exclusively through
    nvidia-smi and so is CUDA-only. On Metal it names a tool the owner cannot
    install, for a feature that was never going to run - directly above the
    line saying the GPU is in use.
    """
    patch_runtime(_fake_torch(cuda=False, mps=True))
    monkeypatch.setattr(sc.shutil, "which", lambda _name: None)
    outcome = StartupCheckOutcome()
    _checks(configured)._check_device_and_vram(outcome)
    _checks(configured)._check_optional_dependencies(outcome)

    assert not any("nvidia-smi" in w for w in outcome.warnings), outcome.warnings


@pytest.mark.parametrize(
    "torch_mod,configured",
    [
        (_fake_torch(cuda=True), "cuda"),
        (_fake_torch(cuda=True), "cpu"),
        (_fake_torch(cuda=False, mps=False), "auto"),
        (_fake_torch(cuda=False, mps=False), "cpu"),
        (None, "auto"),
    ],
    ids=[
        "cuda-host",
        "cuda-host-set-to-cpu",
        "cpu-only-host",
        "cpu-only-host-set-to-cpu",
        "no-torch",
    ],
)
def test_every_host_but_metal_warns_about_a_missing_nvidia_smi(
    patch_runtime, monkeypatch, torch_mod, configured
):
    # Positive control for the test above: skipping the warning is for Metal
    # alone, whatever default_device says and whether or not a card is found.
    patch_runtime(torch_mod)
    monkeypatch.setattr(sc.shutil, "which", lambda _name: None)
    outcome = StartupCheckOutcome()
    _checks(configured)._check_optional_dependencies(outcome)

    assert any("nvidia-smi" in w for w in outcome.warnings), outcome.warnings


def test_mps_is_not_a_configurable_device():
    """`auto` is the only way to ask for Metal, deliberately.

    An explicit `mps` was identical to `auto` on a Mac, meaningless on CUDA,
    and unreachable from the desktop app, which maps its Metal choice to
    `auto`. Its one distinct behaviour was refusing to boot when Metal was
    missing - a new way to fail, for a signal the start-up device log already
    gives. Rejecting it here is what keeps it from drifting back in.
    """
    config = {"default_device": "mps"}
    checks = StartupChecks(config, "/tmp/server_config.json", logging.getLogger("test"))
    outcome = StartupCheckOutcome()
    checks._check_config_sanity(outcome)

    assert any("default_device must be one of" in f for f in outcome.hard_failures)
    assert not any("mps" in f for f in outcome.hard_failures), (
        "the message lists the devices that work, so it must not offer mps"
    )


# --------------------------------------------------------------------------- #
# Real hardware
# --------------------------------------------------------------------------- #


def _mps_present() -> bool:
    try:
        import torch

        return bool(torch.backends.mps.is_available())
    except Exception as exc:  # pragma: no cover - depends on the host
        warnings.warn(f"MPS availability probe failed, skipping: {exc}")
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
# Florence-2 loads straight onto Metal in fp16, and caps its batch by memory
# --------------------------------------------------------------------------- #


def test_florence_loads_straight_onto_metal_in_fp16(monkeypatch):
    """Metal gets the CUDA treatment: one fp16 load onto the device, GPU batch.

    What this asserts is the device and dtype ``_load_model`` is asked for.
    Whether that load survives on real Metal, where it relies on
    configure_metal_model_loading keeping transformers' loader on one thread,
    is test_florence_loads_onto_real_metal_in_fp16's to show.
    """
    import torch

    from pixlstash.tagger_plugins.florence2 import (
        FLORENCE_BATCH_SIZE_CPU,
        FLORENCE_BATCH_SIZE_GPU,
        Florence2Service,
    )

    loaded_on = []
    service = Florence2Service(device="mps")
    # As an earlier load on the CPU leaves it, so the batch asserted below is
    # the one this load sets rather than the one __init__ seeds.
    service._batch_size = FLORENCE_BATCH_SIZE_CPU
    monkeypatch.setattr(
        service,
        "_load_model",
        lambda device, dtype: loaded_on.append((str(device), dtype)),
    )
    service._init()

    assert loaded_on == [("mps", torch.float16)]
    assert service._batch_size == FLORENCE_BATCH_SIZE_GPU
    assert service._last_fallback_reason is None


def test_a_failed_metal_load_falls_back_to_the_cpu(monkeypatch, caplog):
    """A Metal load that raises is a fallback, exactly as on CUDA.

    The reachable case is an MPS OOM while large-ft's weights are placed.
    """
    import torch

    from pixlstash.tagger_plugins.florence2 import (
        FLORENCE_BATCH_SIZE_CPU,
        Florence2Service,
    )

    loaded_on = []
    service = Florence2Service(device="mps")

    def _fake_load(device, dtype):
        # Recorded, not asserted: _init catches Exception, so an assert in
        # here would be swallowed and resurface as a confusing failure below.
        loaded_on.append((str(device), dtype))
        if device.type == "mps":
            raise RuntimeError(MPS_OOM_MESSAGE)
        service._model = object()
        service._processor = object()
        service._model_device = device
        service._dtype = dtype

    monkeypatch.setattr(service, "_load_model", _fake_load)
    with caplog.at_level("WARNING"):
        service._init()

    assert loaded_on == [("mps", torch.float16), ("cpu", torch.float32)]
    assert service.is_loaded()
    assert service._model_device == torch.device("cpu")
    assert service._batch_size == FLORENCE_BATCH_SIZE_CPU, (
        "the CPU constant, not the GPU one __init__ seeds for mps"
    )
    assert service._last_fallback_reason.startswith("init_metal_load_failed:")
    assert "Failed to load Florence-2" not in caplog.text


def test_a_failed_metal_load_is_released_before_the_cpu_load(monkeypatch):
    """What a failed Metal load placed must be gone before the CPU copy loads.

    The caught exception's traceback keeps the failed load's frames, and with
    them the weights already on Metal - on Apple Silicon, the same memory the
    CPU copy loads into.
    """
    from pixlstash.tagger_plugins.florence2 import Florence2Service

    class _PlacedWeights:
        """Stands in for the tensors the failed load had put on Metal."""

    placed = []
    alive_at_cpu_load = []
    service = Florence2Service(device="mps")

    def _fake_load(device, dtype):
        if device.type == "mps":
            weights = _PlacedWeights()
            placed.append(weakref.ref(weights))
            raise RuntimeError(MPS_OOM_MESSAGE)
        alive_at_cpu_load.append(placed[0]() is not None)
        service._model = object()
        service._processor = object()
        service._model_device = device

    monkeypatch.setattr(service, "_load_model", _fake_load)
    service._init()

    assert placed, "the Metal load was never attempted"
    assert alive_at_cpu_load == [False], (
        "the failed Metal load's weights were still referenced when the CPU "
        "copy started loading"
    )


def _florence_failing_mid_pass_on_metal(monkeypatch, tmp_path):
    """A Florence service on Metal whose forward pass fails with an MPS OOM.

    Its CPU load records whether the failed pass's model and device inputs
    were still alive when it started, and how many flushes reaching Metal ran
    before it, then loads a working stand-in so the retry completes.
    """
    import torch

    from pixlstash.tagger_plugins import florence2 as fl

    image_path = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(image_path)

    class _DeviceTensor:
        """Stands in for an input tensor on Metal."""

    class _Processor:
        tokenizer = types.SimpleNamespace(pad_token_id=0)

        def __call__(self, **kwargs):
            return {"input_ids": None, "pixel_values": None}

        def batch_decode(self, ids, skip_special_tokens=False):
            return ["<text>"]

    class _MetalModel:
        def generate(self, **kwargs):
            raise RuntimeError(MPS_OOM_MESSAGE)

    class _CpuModel:
        def generate(self, **kwargs):
            return torch.zeros(1, 2, dtype=torch.long)

    input_refs = []

    def _move(inputs, device, dtype):
        tensor = _DeviceTensor()
        input_refs.append(weakref.ref(tensor))
        return {"input_ids": tensor, "pixel_values": tensor}

    monkeypatch.setattr(fl, "_move_inputs_to_device", _move)

    metal_flushes = []

    def _flush(device=None):
        # Not the real flush, which would call into Metal on a Mac.
        if device is None or str(device).split(":", 1)[0] == "mps":
            metal_flushes.append(device)
        return False

    monkeypatch.setattr(fl, "empty_device_cache", _flush)

    service = fl.Florence2Service(device="mps")
    service._model = _MetalModel()
    service._processor = _Processor()
    service._model_device = torch.device("mps")
    service._dtype = torch.float16
    model_ref = weakref.ref(service._model)
    monkeypatch.setattr(service, "_parse_caption", lambda text: "a caption")
    monkeypatch.setattr(
        service,
        "_parse_detections",
        lambda text, token, size: [("cat", [0, 0, 1, 1], None)],
    )

    alive_at_cpu_load = []

    def _fake_load(device, dtype):
        alive_at_cpu_load.append(
            {
                "model": model_ref() is not None,
                "metal_inputs": [ref() is not None for ref in input_refs],
                "metal_flushes": len(metal_flushes),
            }
        )
        service._model = _CpuModel()
        service._processor = _Processor()
        service._model_device = device
        service._dtype = dtype

    monkeypatch.setattr(service, "_load_model", _fake_load)
    return service, str(image_path), alive_at_cpu_load


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda s, p: {p: s.generate_caption(p)}, id="caption"),
        pytest.param(lambda s, p: s.generate_captions_batch([p]), id="captions_batch"),
        pytest.param(lambda s, p: s.detect_objects([p]), id="detect_objects"),
    ],
)
def test_the_failed_metal_pass_is_released_before_the_cpu_reload(
    monkeypatch, tmp_path, call
):
    """_reload_on_cpu must not load the CPU copy beside the Metal model.

    Setting ``_model`` to None is not enough: the exception being handled
    keeps every frame of the failed pass, and with them the Metal model and
    its inputs, for as long as the CPU copy takes to load. Measured with base
    in fp32 failing mid-batch: 1.37 GiB still allocated on Metal as the CPU
    load began, 0 once the frames are cleared. The captions and detection
    batch frames are still running when the reload starts, so their own
    inputs have to be dropped by hand.
    """
    service, path, alive_at_cpu_load = _florence_failing_mid_pass_on_metal(
        monkeypatch, tmp_path
    )

    results = call(service, path)

    assert results.get(path), "the CPU retry has to produce the result"
    assert service._last_fallback_reason.startswith("runtime_gpu_inference_failed:")
    assert len(alive_at_cpu_load) == 1, alive_at_cpu_load
    assert alive_at_cpu_load[0]["model"] is False, (
        "the Metal model was still referenced when the CPU copy started loading"
    )
    assert alive_at_cpu_load[0]["metal_inputs"] == [False], (
        "the failed pass's Metal inputs were still referenced when the CPU copy "
        "started loading"
    )
    assert alive_at_cpu_load[0]["metal_flushes"] == 1, (
        "the Metal allocator cache was not flushed once before the CPU copy loaded"
    )


#: What torch.mps.recommended_max_memory() reports on the 32 GB M1 Pro the
#: Metal figures were measured on, and an arbitrary smaller working set.
M1_PRO_32GB_WORKING_SET = 26_800_603_136
SMALLER_WORKING_SET = 11_453_251_584


@pytest.mark.parametrize(
    "variant, working_set, expected",
    [
        # (25559 MiB * 0.5 - 1787) // 240 = 45, above the 32 of the GPU batch.
        ("base", M1_PRO_32GB_WORKING_SET, 32),
        # (25559 * 0.5 - 3049) // 469 = 20: large-ft's fp16 batch of 32 peaked
        # at 14.5 GiB there, and 16 ran it within 2% as fast.
        ("large-ft", M1_PRO_32GB_WORKING_SET, 20),
        # (10922 * 0.5 - 1787) // 240 = 15.
        ("base", SMALLER_WORKING_SET, 15),
        # (10922 * 0.5 - 3049) // 469 = 5.
        ("large-ft", SMALLER_WORKING_SET, 5),
        # A budget smaller than the model still captions, one image at a time.
        ("large-ft", 4 * 1024**3, 1),
    ],
)
def test_florence_caps_its_metal_batch_by_memory(
    monkeypatch, variant, working_set, expected
):
    """Metal's batch comes from its working set, not the CUDA constant.

    At 32 images fp32 peaked at 13.9 GiB for base and 26.2 GiB for large-ft on
    a 24.96 GiB working set, and the machine swapped. The expected values are
    written out, not recomputed, so a change to the formula or its figures
    has to change them on purpose.
    """
    import torch

    from pixlstash.tagger_plugins.florence2 import Florence2Service

    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    monkeypatch.setattr(
        torch.mps, "recommended_max_memory", lambda: working_set, raising=False
    )
    service = Florence2Service(device="mps", max_concurrent_fn=lambda: 64)
    service.set_model_variant(variant)

    assert service.description_batch_size() == expected


@pytest.mark.parametrize("device", ["cuda", "cpu"])
def test_florence_never_asks_metal_for_memory_off_metal(monkeypatch, device):
    """CUDA and CPU hosts must not call into torch.mps at all."""
    import torch

    from pixlstash.tagger_plugins.florence2 import Florence2Service

    metal_queries = []
    monkeypatch.setattr(torch.cuda, "is_available", lambda: device == "cuda")
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    monkeypatch.setattr(
        torch.mps,
        "recommended_max_memory",
        lambda: metal_queries.append(device) or M1_PRO_32GB_WORKING_SET,
        raising=False,
    )
    service = Florence2Service(device=device)
    monkeypatch.setattr(service, "_load_model", lambda _device, _dtype: None)
    service._init()
    service.description_batch_size()

    assert metal_queries == []


def test_florence_still_uses_cuda_when_available(monkeypatch):
    """CUDA loads in fp16 with the GPU batch, capped by the VRAM budget.

    The dtype, the batch and the cap are asserted beside the device: each can
    change while the device stays cuda.
    """
    import torch

    from pixlstash.tagger_plugins.florence2 import (
        FLORENCE_BASE_VRAM_MB,
        FLORENCE_BATCH_SIZE_CPU,
        FLORENCE_BATCH_SIZE_GPU,
        FLORENCE_PER_IMAGE_VRAM_MB,
        Florence2Service,
    )

    if not torch.cuda.is_available():
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    loaded_on = []
    vram_caps = []
    service = Florence2Service(
        device="cuda",
        max_concurrent_fn=lambda: 64,
        vram_cap_fn=lambda base_mb, per_item_mb: (
            vram_caps.append((base_mb, per_item_mb)) or 7
        ),
    )
    # As an earlier load on the CPU leaves it; see the Metal test above.
    service._batch_size = FLORENCE_BATCH_SIZE_CPU
    monkeypatch.setattr(
        service,
        "_load_model",
        lambda device, dtype: loaded_on.append((str(device), dtype)),
    )
    service._init()

    assert loaded_on == [("cuda", torch.float16)]
    assert service._batch_size == FLORENCE_BATCH_SIZE_GPU
    assert service._last_fallback_reason is None
    assert service.description_batch_size() == 7
    assert vram_caps == [(FLORENCE_BASE_VRAM_MB, FLORENCE_PER_IMAGE_VRAM_MB)]


_FLORENCE_ON_METAL_SCRIPT = """
import json

from pixlstash.utils.device_utils import configure_metal_model_loading

configure_metal_model_loading()

from pixlstash.tagger_plugins.florence2 import Florence2Service

service = Florence2Service(device="mps")
service._init()
model = service._model
tensors = [] if model is None else [*model.parameters(), *model.buffers()]
print(
    json.dumps(
        {
            "loaded": service.is_loaded(),
            "fallback": service._last_fallback_reason,
            "model_device": str(service._model_device),
            "dtype": str(service._dtype),
            "tensors": sorted({f"{t.device.type}:{t.dtype}" for t in tensors}),
        }
    )
)
"""


@pytest.mark.skipif(not _mps_present(), reason="requires an Apple Metal GPU")
def test_florence_loads_onto_real_metal_in_fp16():
    """The real load, in its own process: a Metal crash fails this test
    instead of taking pytest down with it."""
    import pixlstash

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(pixlstash.__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (repo_root, env.get("PYTHONPATH")) if path
    )
    proc = subprocess.run(
        [sys.executable, "-c", _FLORENCE_ON_METAL_SCRIPT],
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )

    assert proc.returncode == 0, (
        f"the Florence-2 load exited with {proc.returncode}:\n{proc.stderr[-4000:]}"
    )
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["loaded"], proc.stderr[-4000:]
    assert report["fallback"] is None
    assert report["model_device"] == "mps"
    assert report["dtype"] == "torch.float16"
    assert report["tensors"] == ["mps:torch.float16"]


@pytest.mark.parametrize(
    "device, fp16", [("mps", True), ("cuda", True), ("cpu", False)]
)
def test_the_tagger_loads_in_fp16_on_every_accelerator(
    tmp_path, monkeypatch, device, fp16
):
    """Metal gets the fp16 promotion CUDA gets; the CPU stays in fp32.

    ``test_tagger_promotes_to_fp16_on_real_metal`` is the only other guard, and
    it skips on CI. The checkpoint and the network are stand-ins that record
    what the load does.
    """
    import json

    import safetensors.torch
    import torch

    from pixlstash.tagger_plugins import pixlstash_tagger as pt

    (tmp_path / pt.PIXLSTASH_TAGGER_FILENAME).write_bytes(b"")
    (tmp_path / pt.PIXLSTASH_TAGGER_META_FILENAME).write_text(
        json.dumps({"labels": ["blocky", "noisy"], "arch": "convnext_tiny"})
    )
    monkeypatch.setattr(safetensors.torch, "load_file", lambda path, device=None: {})
    calls = []

    class _Network:
        def float(self):
            calls.append("float")
            return self

        def load_state_dict(self, state_dict):
            calls.append("load")

        def to(self, target):
            calls.append(f"to {target}")
            return self

        def half(self):
            calls.append("half")
            return self

        def eval(self):
            return self

    service = pt.PixlStashTaggerService(
        device=device, model_dir=str(tmp_path), batch_size_fn=lambda: 1
    )
    monkeypatch.setattr(service, "_build_model", lambda arch, count: _Network())
    monkeypatch.setattr(service, "_build_transform", lambda size: None)

    service.init()

    expected = ["float", "load", f"to {device}"] + (["half"] if fp16 else [])
    assert calls == expected
    assert service._dtype is (torch.float16 if fp16 else torch.float32)


@pytest.mark.parametrize("onnx_capacity", [1024, 1])
@pytest.mark.parametrize(
    "device,limit",
    [
        ("mps", 8),
        ("cuda", MAX_CONCURRENT_GPU_IMAGES),
        ("cpu", TAGGING_MAX_CONCURRENT_CPU),
    ],
)
def test_tag_tasks_and_tagger_batches_hold_at_most_eight_on_metal(
    device, limit, onnx_capacity
):
    """Metal caps a tag task, and the taggers' batches, at eight images.

    No VRAM budget bounds a Metal batch (``VramBudget`` is CUDA-only), and every
    image in it comes out of the machine's one pool of memory, so Metal gets
    its own small limit. CUDA keeps the GPU size and the CPU its own. The
    taggers' batch is also bounded by WD14's ONNX capacity: far above every
    limit once a session has loaded, and 1 until one has, which is where the
    built-in tagger's batch stays when WD14 never loads. The task size does not
    read the capacity.
    """
    engine = types.SimpleNamespace(
        device=device,
        vram_budget=VramBudget(device),
        wd14_service=types.SimpleNamespace(batch_capacity=lambda: onnx_capacity),
    )
    workflow = TaggingWorkflow(engine=engine, use_wd14=True, use_pixlstash_tagger=True)

    assert workflow.effective_wd14_batch_size() == min(limit, onnx_capacity)
    assert workflow.effective_pixlstash_tagger_batch_size() == min(limit, onnx_capacity)
    assert workflow.suggested_task_size() == limit


def test_the_metal_tagging_limit_leaves_the_engines_concurrency_alone():
    # Florence-2 sizes its Metal batch from the engine's concurrency and its own
    # memory cap; the taggers' Metal limit must not reach it.
    engine = InferenceEngine.__new__(InferenceEngine)
    engine.device = "mps"

    assert engine.max_concurrent_images() == MAX_CONCURRENT_GPU_IMAGES


# --------------------------------------------------------------------------- #
# is_device_error: one predicate for "the accelerator failed, retry on CPU"
# --------------------------------------------------------------------------- #


#: Metal refusing one allocation larger than its maximum buffer (torch 2.13).
#: A Metal fault, not an OOM: the same allocation is refused on every attempt.
MPS_INVALID_BUFFER_MESSAGE = "Invalid buffer size: 512.00 GiB"

#: The CUDA texts follow PyTorch's source at v2.13.0, and the runtime's own
#: strings ("no kernel image") as PyTorch's issues quote them: no runner has a card.
CUDA_OOM_MESSAGE = (
    "CUDA out of memory. Tried to allocate 20.00 MiB. GPU 0 has a total capacity "
    "of 23.55 GiB of which 3.12 MiB is free."
)

CUDA_ILLEGAL_ACCESS_MESSAGE = "CUDA error: an illegal memory access was encountered"

CUDA_UNKNOWN_ERROR_MESSAGE = (
    "CUDA unknown error - this may be due to an incorrectly set up environment, "
    "e.g. changing env variable CUDA_VISIBLE_DEVICES after program start. "
    "Setting the available devices to be zero."
)

CUDNN_VERSION_MESSAGE = (
    "cuDNN version incompatibility: PyTorch was compiled  against (9, 10, 2) but "
    "found runtime version (9, 1, 0). PyTorch already comes bundled with cuDNN. "
    "One option to resolving this error is to ensure PyTorch can find the "
    "bundled cuDNN. one possibility is that there is a conflicting cuDNN in "
    "LD_LIBRARY_PATH."
)

RESHAPE_BUG_MESSAGE = (
    "view size is not compatible with input tensor's size and stride (at least "
    "one dimension spans across two contiguous subspaces). Use .reshape(...) "
    "instead."
)


def _raised_from(fault):
    """What ``raise RuntimeError("plugin failed") from fault`` hands a caller."""
    try:
        try:
            raise fault
        except BaseException as cause:
            raise RuntimeError("plugin failed") from cause
    except RuntimeError as wrapper:
        return wrapper


def _raised_while_handling(fault):
    """What a handler raising its own error without ``from`` hands a caller.

    *fault* is then only the wrapper's ``__context__``; ``__cause__`` is None.
    """
    try:
        try:
            raise fault
        except BaseException:
            raise RuntimeError("plugin failed")
    except RuntimeError as wrapper:
        return wrapper


#: Faults the Metal set names. None is an OOM, so only that set can carry them.
METAL_FAULTS = {
    "invalid-buffer": RuntimeError(MPS_INVALID_BUFFER_MESSAGE),
    "unimplemented-op": NotImplementedError(MPS_UNIMPLEMENTED_OP_MESSAGE),
    "float64": TypeError(MPS_FLOAT64_MESSAGE),
    "wrapped-unimplemented-op": _raised_from(
        NotImplementedError(MPS_UNIMPLEMENTED_OP_MESSAGE)
    ),
    "context-unimplemented-op": _raised_while_handling(
        NotImplementedError(MPS_UNIMPLEMENTED_OP_MESSAGE)
    ),
    # Refusals torch 2.13's MPS backend raises for work the CPU can run.
    "float64-refusal": RuntimeError("float64 is not supported on MPS"),
    "dtype-conversion": TypeError(
        "Trying to convert BFloat16 to the MPS backend but it does not have "
        "support for that dtype."
    ),
    "half-softmax": RuntimeError(
        "softmax with half to float conversion is not supported on MPS"
    ),
    "matmul-size": RuntimeError(
        "Output dim sizes larger than 2**32 elements for matmul not supported on "
        "MPS device."
    ),
    "output-channels": RuntimeError(
        "Output channels > 65536 not supported at the MPS device. "
    ),
    "graph-dims": RuntimeError(
        "MPSGraph does not support tensor dims larger than INT_MAX"
    ),
    "adaptive-pool": RuntimeError(
        "Adaptive pool MPS: input sizes must be divisible by output sizes. "
        "Non-divisible input sizes are not implemented on MPS device yet. For now, "
        "you can manually transfer tensor to cpu in this case."
    ),
    "sdpa-dropout": RuntimeError(
        "scaled_dot_product_attention for MPS does not support dropout."
    ),
}

#: Faults the CUDA set names, none of them an OOM either.
CUDA_FAULTS = {
    "illegal-access": RuntimeError(CUDA_ILLEGAL_ACCESS_MESSAGE),
    "no-kernel-image": RuntimeError(
        "CUDA error: no kernel image is available for execution on the device"
    ),
    "cublas": RuntimeError(
        "CUDA error: CUBLAS_STATUS_ALLOC_FAILED when calling `cublasCreate(handle)`"
    ),
    "driver": RuntimeError(
        "CUDA driver initialization failed, you might not have a CUDA gpu."
    ),
    "unknown-error": RuntimeError(CUDA_UNKNOWN_ERROR_MESSAGE),
    "cudnn-error": RuntimeError("cuDNN error: CUDNN_STATUS_EXECUTION_FAILED"),
    "cudnn-version": RuntimeError(CUDNN_VERSION_MESSAGE),
    "cudnn-no-algorithm": RuntimeError(
        "Unable to find a valid cuDNN algorithm to run convolution"
    ),
    "cudnn-no-engine-find": RuntimeError(
        "FIND was unable to find an engine to execute this computation"
    ),
    # What the default heuristic mode says; FIND is the benchmarking one.
    "cudnn-no-engine-get": RuntimeError(
        "GET was unable to find an engine to execute this computation"
    ),
    "cudnn-frontend": RuntimeError(
        "cuDNN Frontend error: [cudnn_frontend] Error: No execution plans support "
        "the graph."
    ),
    "cudnn-plan-not-supported": RuntimeError(
        "CUDNN_BACKEND_EXECUTION_PLAN_DESCRIPTOR: cudnnFinalize Descriptor Failed "
        "cudnn_status: CUDNN_STATUS_NOT_SUPPORTED"
    ),
    "cudnn-no-convolution-algorithms": RuntimeError(
        "no valid convolution algorithms available in CuDNN"
    ),
    "allocator-nvml-assert": RuntimeError(
        'NVML_SUCCESS == r INTERNAL ASSERT FAILED at "../c10/cuda/'
        'CUDACachingAllocator.cpp":1154, please report a bug to PyTorch.'
    ),
    "triton": RuntimeError(
        "Triton Error [CUDA]: an illegal memory access was encountered"
    ),
    "wrapped-illegal-access": _raised_from(RuntimeError(CUDA_ILLEGAL_ACCESS_MESSAGE)),
}

#: Out-of-memory failures and the device each happened on.
OOMS = {
    "metal": (RuntimeError(MPS_OOM_MESSAGE), "mps"),
    "cuda": (RuntimeError(CUDA_OOM_MESSAGE), "cuda"),
    "onnxruntime-arena": (
        RuntimeError(
            "bfc_arena.cc:359 void* onnxruntime::BFCArena::AllocateRawInternal("
            "size_t, bool, onnxruntime::Stream*) Failed to allocate memory for "
            "requested buffer of size 90063104"
        ),
        "cuda",
    ),
    "wrapped-metal": (_raised_from(RuntimeError(MPS_OOM_MESSAGE)), "mps"),
}

#: Bugs whose text names a device or one of its libraries. Each raises in our
#: code, and a CPU retry would hide it while leaving the GPU idle for good.
DEVICE_NAMING_BUGS = {
    "metal-two-devices": RuntimeError(MPS_DEVICE_MISMATCH_MESSAGE),
    "metal-argument-on-cpu": RuntimeError(MPS_ARGUMENT_ON_CPU_MESSAGE),
    "metal-conv-input-type": RuntimeError(
        "Input type (MPSFloatType) and weight type (torch.FloatTensor) should be "
        "the same"
    ),
    "metal-placeholder-storage": RuntimeError(
        "Placeholder storage has not been allocated on MPS device!"
    ),
    # Metal refusals the CPU would refuse too: a non-float tensor fed to a layer.
    "metal-linear-non-float": RuntimeError(
        "MPS device does not support linear for non-float inputs"
    ),
    "metal-long-batch-norm": RuntimeError("Long batch norm is not supported with MPS"),
    "cuda-two-devices": RuntimeError(
        "Expected all tensors to be on the same device, but found at least two "
        "devices, cuda:0 and cpu!"
    ),
    "cuda-conv-input-type": RuntimeError(
        "Input type (torch.cuda.FloatTensor) and weight type (torch.FloatTensor) "
        "should be the same"
    ),
    "cudnn-argument-on-cpu": RuntimeError(
        "Tensor for argument #2 'weight' is on CPU, but expected it to be on GPU "
        "(while checking arguments for cudnn_batch_norm)"
    ),
    "cudnn-argument-type": RuntimeError(
        "Expected tensor for argument #1 'input' to have the same type as tensor "
        "for argument #2 'weight'; but type torch.cuda.HalfTensor does not equal "
        "torch.cuda.FloatTensor (while checking arguments for cudnn_batch_norm)"
    ),
    "cublas-tf32-api-mix": RuntimeError(
        "PyTorch is checking whether allow_tf32_new is enabled for cuBlas matmul,"
        "Current status indicate that you have used mix of the legacy and new "
        "APIs to set the TF32 status for cublas matmul. "
    ),
}

#: torch only ever warns this (warnings.warn in torch/cuda/__init__.py); what
#: such a card then raises is "CUDA error: no kernel image is available", which
#: CUDA_FAULTS covers.
CUDA_CAPABILITY_WARNING = (
    "NVIDIA GeForce RTX 5090 with CUDA capability sm_120 is not compatible "
    "with the current PyTorch installation."
)

#: Failures that name no device at all.
ORDINARY_FAILURES = {
    "reshape": RuntimeError(RESHAPE_BUG_MESSAGE),
    "wrapped-reshape": _raised_from(RuntimeError(RESHAPE_BUG_MESSAGE)),
    "shape-mismatch": RuntimeError("shape mismatch"),
    "clamps-amps": RuntimeError("Voltage across the clamps exceeded 20 amps"),
    "temps": RuntimeError("temps rising while the batch ran"),
}

#: The whole contract as (error, device) -> "retry on the CPU?".
DEVICE_ERROR_CASES = [
    *(
        pytest.param(error, device, device == "mps", id=f"metal-{name}-on-{device}")
        for name, error in METAL_FAULTS.items()
        for device in ("mps", "cuda", "cpu")
    ),
    *(
        pytest.param(error, device, device == "cuda", id=f"cuda-{name}-on-{device}")
        for name, error in CUDA_FAULTS.items()
        for device in ("cuda", "mps", "cpu")
    ),
    *(
        pytest.param(error, device, True, id=f"{name}-oom-on-{device}")
        for name, (error, device) in OOMS.items()
    ),
    *(
        pytest.param(error, device, False, id=f"bug-{name}-on-{device}")
        for name, error in DEVICE_NAMING_BUGS.items()
        for device in ("mps", "cuda")
    ),
    *(
        pytest.param(error, device, False, id=f"ordinary-{name}-on-{device}")
        for name, error in ORDINARY_FAILURES.items()
        for device in ("mps", "cuda")
    ),
    pytest.param(
        RuntimeError(CUDA_CAPABILITY_WARNING),
        "cuda",
        False,
        id="cuda-capability-warning",
    ),
    pytest.param(RuntimeError(MPS_OOM_MESSAGE), "cpu", False, id="metal-oom-on-cpu"),
    pytest.param(RuntimeError(CUDA_OOM_MESSAGE), "cpu", False, id="cuda-oom-on-cpu"),
    pytest.param(RuntimeError(MPS_OOM_MESSAGE), None, False, id="metal-oom-on-none"),
]


@pytest.mark.parametrize("error, device, expected", DEVICE_ERROR_CASES)
def test_is_device_error(error, device, expected):
    """A device's own faults retry on the CPU; nothing else does.

    Metal's faults and capability gaps retry on Metal, CUDA's on CUDA, and an
    OOM on the device that ran out. A bug raises on every device, including the
    ones whose text names the device: a CPU retry "fixes" a misplaced tensor by
    putting everything on one device, and the GPU then sits idle for the rest
    of the process with nothing in the log to say why.
    """
    assert is_device_error(error, device) is expected


@pytest.mark.parametrize(
    "error",
    [*METAL_FAULTS.values(), *CUDA_FAULTS.values()],
    ids=[*(f"metal-{n}" for n in METAL_FAULTS), *(f"cuda-{n}" for n in CUDA_FAULTS)],
)
def test_a_fault_row_is_carried_by_its_phrase_not_by_is_vram_oom(error):
    # Anti-vacuity: an OOM would reach True through is_vram_oom, and then the
    # rows above would say nothing about the phrase sets.
    assert is_vram_oom(error) is False


@pytest.mark.parametrize(
    "error", DEVICE_NAMING_BUGS.values(), ids=list(DEVICE_NAMING_BUGS)
)
def test_a_device_naming_bug_really_names_the_device(error):
    # Anti-vacuity: these rows only guard against matching on a device's name
    # if the name is really there, so a reworded fixture cannot become a free
    # pass.
    assert re.search(r"mps|cuda|gpu|cudnn|cublas", str(error).lower())


def test_every_accelerator_has_a_fault_phrase_set():
    # is_device_error looks the set up by device type, so an accelerator
    # without one would raise KeyError inside a service's except block.
    assert set(_DEVICE_FAULTS) == ACCELERATORS


def test_the_device_is_read_from_a_torch_device_or_an_indexed_string():
    """Call sites hold ``torch.device("mps:0")`` as well as ``"mps"``.

    The phrase set is picked by device type, so an index or a device object
    left unnormalised would pick none, or the wrong one.
    """
    import torch

    metal_fault = NotImplementedError(MPS_UNIMPLEMENTED_OP_MESSAGE)
    cuda_fault = RuntimeError(CUDA_ILLEGAL_ACCESS_MESSAGE)
    for device in (torch.device("mps:0"), "mps:0", "MPS:0"):
        assert is_device_error(metal_fault, device) is True, device
        assert is_device_error(cuda_fault, device) is False, device
    for device in (torch.device("cuda:1"), "cuda:1"):
        assert is_device_error(cuda_fault, device) is True, device
        assert is_device_error(metal_fault, device) is False, device


def test_the_cause_walk_gives_up_at_a_bounded_depth():
    """A fault under four wrappers is found; under five it is not.

    The bound is what makes a long chain a miss rather than a hang. Both depths
    are literals rather than derived from _CAUSE_DEPTH, so a change to the
    bound in either direction fails here.
    """

    def wrapped(times):
        error: BaseException = RuntimeError(MPS_UNIMPLEMENTED_OP_MESSAGE)
        for _ in range(times):
            wrapper = ValueError("wrapped again")
            wrapper.__cause__ = error
            error = wrapper
        return error

    assert is_device_error(wrapped(4), "mps") is True
    assert is_device_error(wrapped(5), "mps") is False


def test_a_cyclic_cause_chain_terminates():
    """Two handlers re-raising at each other make ``__context__`` a loop.

    The depth bound is what ends the walk; there is no cycle detection.
    """
    first = ValueError("first")
    second = ValueError("second")
    first.__context__ = second
    second.__context__ = first

    assert is_device_error(first, "mps") is False


def test_a_driver_fault_is_a_cuda_error_from_its_type_alone():
    # Isolates the CudaError branch: the message is whatever the driver
    # returned, so type identity is the only thing that can classify it - on
    # CUDA, the only device that raises it.
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
    assert is_device_error(error, "mps") is False


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
    # Positive control: a CUDA service retries a CUDA error on the CPU as well.
    fake_torch(_tensor_torch(cuda=True, mps=False))
    service = _loaded_clip("cuda", RuntimeError("CUDA out of memory"))

    assert service.encode_image_batch([object()]) is not None
    assert service._device == "cpu"


def test_clip_falls_back_on_a_device_error_that_is_not_a_runtimeerror(fake_torch):
    """Metal's dtype refusal is a TypeError, not a RuntimeError.

    torch raises "Cannot convert a MPS Tensor to float64" through
    TORCH_CHECK_TYPE, i.e. c10::TypeError, so the handler catches every
    exception and the predicate, not the exception class, decides.
    """
    fake_torch(_tensor_torch(cuda=False, mps=True))
    service = _loaded_clip("mps", TypeError(MPS_FLOAT64_MESSAGE))

    assert service.encode_image_batch([object()]) is not None
    assert service._device == "cpu"


def test_clip_crops_also_fall_back_on_a_non_runtimeerror(fake_torch):
    """The crops handler also lets the predicate decide, not the exception class.

    encode_image_crops has its own try/except, so the batch test above says
    nothing about it. Face crops run through it, one crop at a time.
    """
    fake_torch(_tensor_torch(cuda=False, mps=True))
    service = _loaded_clip("mps", TypeError(MPS_FLOAT64_MESSAGE))

    results = service.encode_image_crops([object()], pic_desc="a picture")

    assert results[0] is not None, "a Metal dtype refusal must retry on the CPU"
    assert service._device == "cpu"


def test_clip_crops_do_not_retry_an_ordinary_failure(fake_torch):
    # Negative control for the crops handler, matching the batch one: catching
    # every exception must not turn every error into a CPU demotion.
    fake_torch(_tensor_torch(cuda=False, mps=True))
    service = _loaded_clip("mps", RuntimeError("shape mismatch"))

    results = service.encode_image_crops([object()], pic_desc="a picture")

    assert results[0] is None
    assert service._device == "mps", "an ordinary error must not move the model"


def test_clip_does_not_retry_an_ordinary_failure(fake_torch, caplog):
    # Negative control: the retry must stay tied to device failures. An
    # everyday RuntimeError is logged at ERROR and the batch returns None.
    fake_torch(_tensor_torch(cuda=False, mps=True))
    service = _loaded_clip("mps", RuntimeError("shape mismatch"))

    with caplog.at_level(logging.ERROR, logger="pixlstash.tagger_plugins.clip_service"):
        assert service.encode_image_batch([object()]) is None
    assert service._device == "mps", "an ordinary error must not move the model"
    assert any(
        record.levelno == logging.ERROR
        and "RuntimeError: shape mismatch" in record.getMessage()
        for record in caplog.records
    ), caplog.text


@pytest.mark.parametrize(
    "encode",
    [
        pytest.param(lambda s: s.encode_image_batch([object()]), id="batch"),
        pytest.param(
            lambda s: s.encode_image_crops([object()], pic_desc="a picture"),
            id="crops",
        ),
    ],
)
def test_clip_flushes_the_metal_cache_when_it_spills_to_the_cpu(fake_torch, encode):
    """Each retry flushes the device the model left, and only that one.

    Both backends are available here, so flushing every backend, or the
    wrong one, differs from ``["mps"]``.
    """
    flushed = []
    torch = _tensor_torch(cuda=True, mps=True)
    torch.mps.empty_cache = lambda: flushed.append("mps")
    torch.cuda.empty_cache = lambda: flushed.append("cuda")
    fake_torch(torch)
    service = _loaded_clip("mps", RuntimeError(MPS_OOM_MESSAGE))

    encode(service)

    assert service._device == "cpu"
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


def test_sbert_falls_back_on_a_device_error_that_is_not_a_runtimeerror(monkeypatch):
    # As for CLIP: Metal's dtype refusal is a TypeError, not a RuntimeError.
    service = _loaded_sbert(monkeypatch, "mps", TypeError(MPS_FLOAT64_MESSAGE))

    assert len(service.encode(["a caption"])) == 1
    assert service._device == "cpu"


def test_sbert_reraises_an_ordinary_failure(monkeypatch):
    service = _loaded_sbert(monkeypatch, "mps", RuntimeError("shape mismatch"))

    with pytest.raises(RuntimeError, match="shape mismatch"):
        service.encode(["a caption"])


@pytest.mark.parametrize(
    "device, flushed", [("mps", ["mps"]), ("cuda", ["cuda"]), ("cpu", ["cuda"])]
)
def test_joycaption_unload_flushes_metal_only_on_metal(fake_torch, device, flushed):
    """Metal's cache is flushed only for a service on Metal.

    Off Metal the idle unload runs on the progress poll, so a Mac forced onto
    the CPU would flush Metal off the GPU worker. The CUDA flush stays for
    everything else.
    """
    # Imported before the stand-in is installed: joycaption binds torch at
    # module scope, so importing it under the fake would leave every later
    # test in the session holding a SimpleNamespace instead of torch.
    from pixlstash.tagger_plugins import joycaption as jc

    calls = []
    torch = _fake_torch(cuda=True, mps=True)
    torch.mps.empty_cache = lambda: calls.append("mps")
    torch.cuda.empty_cache = lambda: calls.append("cuda")
    fake_torch(torch)

    service = jc.JoyCaptionService.__new__(jc.JoyCaptionService)
    service._load_lock = threading.RLock()
    service._device = device
    service._model = object()
    service._processor = object()

    service.unload()

    assert calls == flushed
    assert service._model is None


class _CyclicModel:
    """A stand-in model in a reference cycle, as the loaded LLaVA model is."""

    def __init__(self):
        self.self_reference = self


def test_joycaption_unload_collects_the_model_before_flushing(fake_torch):
    """The flush runs after the collection that frees the model.

    A model in a reference cycle is not freed when ``unload`` clears the
    attribute, only when ``gc.collect()`` runs, so a flush ahead of the
    collection hands nothing back to the driver. Automatic collection is off
    for the call, so the only collection that can free the stand-in is
    ``unload``'s own.
    """
    from pixlstash.tagger_plugins import joycaption as jc

    model = _CyclicModel()
    model_ref = weakref.ref(model)
    alive_at_flush = []
    torch = _fake_torch(cuda=False, mps=True)
    torch.mps.empty_cache = lambda: alive_at_flush.append(model_ref() is not None)
    fake_torch(torch)

    service = jc.JoyCaptionService.__new__(jc.JoyCaptionService)
    service._load_lock = threading.RLock()
    service._device = "mps"
    service._model = model
    service._processor = object()
    del model

    collecting = gc.isenabled()
    gc.disable()
    try:
        probe = _CyclicModel()
        probe_ref = weakref.ref(probe)
        del probe
        assert probe_ref() is not None, (
            "the stand-in must need a collection to be freed, or this test "
            "cannot tell a flush before the collection from one after it"
        )
        service.unload()
    finally:
        if collecting:
            gc.enable()

    assert alive_at_flush == [False], (
        "the Metal cache was flushed while the unloaded model was still alive; "
        "the tensors it held were not yet free to hand back"
    )


@contextlib.contextmanager
def _only_explicit_collections():
    """Turn automatic garbage collection off, so only an explicit one frees a cycle.

    Proves it took effect first: a cycle dropped inside must still be alive.
    """
    collecting = gc.isenabled()
    gc.disable()
    try:
        probe = _CyclicModel()
        probe_ref = weakref.ref(probe)
        del probe
        assert probe_ref() is not None, (
            "a dropped cycle was freed without a collection, so a test cannot tell "
            "a flush before the collection from one after it"
        )
        yield
    finally:
        if collecting:
            gc.enable()


class _CyclicModelService:
    """A service whose ``unload`` drops a model held in a reference cycle."""

    def __init__(self):
        self.model = _CyclicModel()

    def unload(self):
        self.model = None


@pytest.mark.parametrize("unload", ["aggressive_unload", "safe_idle_unload"])
def test_a_lifecycle_unload_collects_the_models_before_flushing(unload, monkeypatch):
    """The engine's unloads flush after the collection that frees the models.

    Same reason as JoyCaption's unload above: an unloaded model in a reference
    cycle is freed only by ``gc.collect()``.
    """
    service = _CyclicModelService()
    model_ref = weakref.ref(service.model)
    alive_at_flush = []
    monkeypatch.setattr(
        model_lifecycle_module,
        "empty_device_cache",
        lambda device=None: alive_at_flush.append(model_ref() is not None) or True,
    )

    with _only_explicit_collections():
        getattr(ModelLifecycleManager(device="mps"), unload)(clip_service=service)

    assert service.model is None
    assert alive_at_flush == [False], (
        f"{unload} flushed the device cache while the unloaded model was alive"
    )


@pytest.mark.parametrize("unload", ["aggressive_unload", "safe_idle_unload"])
@pytest.mark.parametrize(
    "device, flushed", [("mps", ["mps"]), ("cuda", ["cuda"]), ("cpu", ["cuda"])]
)
def test_a_lifecycle_unload_flushes_metal_only_for_an_engine_on_metal(
    fake_torch, unload, device, flushed
):
    """A Mac forced onto the CPU unloads on the progress poll, off the GPU worker.

    Flushing Metal there would be Metal work on a second thread. Every engine
    off Metal still flushes CUDA's cache, as before Metal support.
    """
    calls = []
    torch = _fake_torch(cuda=True, mps=True)
    torch.cuda.empty_cache = lambda: calls.append("cuda")
    torch.mps.empty_cache = lambda: calls.append("mps")
    fake_torch(torch)

    getattr(ModelLifecycleManager(device=device), unload)()

    assert calls == flushed


def test_joycaption_records_metal_as_the_model_device(monkeypatch):
    """The recorded device is the one the service was built for.

    ``generate_caption`` moves every input to ``_model_device``, so on Metal it
    must be ``mps``. Nothing is loaded here: the load is stubbed, and the
    device probe raises to show the recorded device is not asked of it again.
    """
    import torch

    from pixlstash.tagger_plugins import joycaption as jc

    def _probe():
        raise AssertionError("the load re-derived the device instead of using its own")

    monkeypatch.setattr(jc, "detect_device", _probe)
    monkeypatch.setattr(
        jc,
        "from_pretrained_local_first",
        lambda cls, name, **kw: types.SimpleNamespace(
            eval=lambda: None,
            image_processor=None,
            tokenizer=None,
        ),
    )

    _stub_metal_budget(monkeypatch, jc)
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


def test_joycaption_on_the_cpu_gives_the_use_gpu_advice(monkeypatch, caplog):
    # The advice itself is checked against start-up above; this pins that the
    # warning carries it rather than a device list of its own.
    from pixlstash.tagger_plugins import joycaption as jc

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
    with caplog.at_level("WARNING"):
        service._init()

    assert USE_GPU_ADVICE in caplog.text


# --------------------------------------------------------------------------- #
# JoyCaption on Metal: memory budget and device-appropriate guidance
# --------------------------------------------------------------------------- #


#: Stands in for torch.mps.recommended_max_memory(), which calls
#: torch._C._mps_recommendedMaxMemory() - compiled in only with USE_MPS, so
#: calling it on the Linux and Windows gate runners raises. The value is
#: arbitrary; what these tests assert is that it is the number actually used.
FAKE_METAL_BUDGET = 26_000_000_000


def _stub_metal_budget(monkeypatch, jc):
    """Give every Metal JoyCaption test a budget the gate runners can answer.

    ``torch.mps.recommended_max_memory`` is compiled in only with USE_MPS, so
    on the Linux and Windows runners the real call raises AttributeError and
    _init reports a failed load instead of the device under test. Any test that
    drives JoyCaption with device "mps" needs this, not only the ones reading
    load kwargs back.
    """
    monkeypatch.setattr(
        jc.torch.mps, "recommended_max_memory", lambda: FAKE_METAL_BUDGET, raising=False
    )


def _joycaption_load_kwargs(monkeypatch, device, precision):
    """Run _init far enough to capture what it passes to from_pretrained."""
    from pixlstash.tagger_plugins import joycaption as jc

    captured = {}

    def _fake_from_pretrained(cls, name, **kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            eval=lambda: None, image_processor=None, tokenizer=None
        )

    monkeypatch.setattr(jc, "from_pretrained_local_first", _fake_from_pretrained)
    monkeypatch.setattr(jc, "detect_device", lambda: device)
    _stub_metal_budget(monkeypatch, jc)
    jc.JoyCaptionService(device=device, precision=precision)._init()
    return captured


def test_metal_gets_an_explicit_memory_budget(monkeypatch):
    """Accelerate sizes the Metal budget from free RAM, not from capacity.

    ``device_map="auto"`` asks Accelerate to plan the layout, and on MPS its
    budget is ``psutil.virtual_memory().available`` - whatever happens to be
    free this second. On a busy machine that makes it plan a CPU/disk split,
    which bitsandbytes then refuses outright, so a 5 GB model fails to load on
    a 32 GB machine depending on what else is open. Measured: refused at
    9.4 GB free, loaded at 12.5 GB.
    """
    kwargs = _joycaption_load_kwargs(monkeypatch, "mps", "nf4")

    assert "max_memory" in kwargs, "Metal must not be left to Accelerate's guess"
    assert kwargs["max_memory"]["mps"] == FAKE_METAL_BUDGET


def test_cuda_is_left_to_accelerate(monkeypatch):
    # Positive control: the guess is correct on CUDA, where the budget comes
    # from the card rather than from free system RAM.
    kwargs = _joycaption_load_kwargs(monkeypatch, "cuda", "nf4")
    assert "max_memory" not in kwargs


@pytest.mark.parametrize("precision", ["nf4", "int8", "bf16", "fp16"])
def test_every_auto_mapped_precision_gets_the_budget_on_metal(monkeypatch, precision):
    """The budget follows device_map="auto", not quantisation.

    An unquantised load needs it as much as a bitsandbytes one: on a Mac
    Accelerate's ``get_max_memory`` fills in ``mps`` *or* ``cpu``, never both,
    so an unquantised overflow is planned onto ``disk`` and the load dies with
    "We need an `offload_dir`". All four precisions set device_map="auto"
    whenever the device is not cpu, so all four must get the budget.
    """
    kwargs = _joycaption_load_kwargs(monkeypatch, "mps", precision)
    assert kwargs["max_memory"] == {"mps": FAKE_METAL_BUDGET}


def test_an_explicit_cpu_device_gets_no_budget(monkeypatch):
    # device_map is "cpu" there, not "auto", so there is nothing to plan.
    kwargs = _joycaption_load_kwargs(monkeypatch, "cpu", "bf16")
    assert "max_memory" not in kwargs


def _precision_field(monkeypatch, device):
    from pixlstash.tagger_plugins import joycaption as jc

    monkeypatch.setattr(jc, "detect_device", lambda: device)
    schema = jc.JoyCaptionPlugin().parameter_schema()
    return next(f for f in schema if f["name"] == "precision")


def test_precision_guidance_follows_the_device(monkeypatch):
    """The stock text is true on CUDA and wrong on Metal."""
    metal = _precision_field(monkeypatch, "mps")
    cuda = _precision_field(monkeypatch, "cuda")

    assert metal["description"] != cuda["description"]
    assert "faster" in cuda["description"], "CUDA keeps the original guidance"
    assert "Apple" in metal["description"] or "Metal" in metal["description"]


def test_the_saved_precision_values_never_vary_by_device(monkeypatch):
    """Labels and help may vary; values may not.

    A stored ``tagger_settings`` holds the value, so a machine-dependent one
    would stop resolving the moment the config moved between machines - or the
    moment the same machine reported a different device.
    """
    metal = _precision_field(monkeypatch, "mps")
    cuda = _precision_field(monkeypatch, "cuda")

    assert [o["value"] for o in metal["options"]] == [
        o["value"] for o in cuda["options"]
    ]
    assert metal["default"] == cuda["default"] == "nf4"


def test_metal_precision_help_gives_memory_as_the_reason(monkeypatch):
    """BF16/FP16 run natively on Metal; what rules them out is their size."""
    metal = _precision_field(monkeypatch, "mps")
    help_text = metal["description"]

    assert "no accelerated Metal support" not in help_text
    assert "run natively on Metal" in help_text, "BF16/FP16 are native Metal ops"
    for figure in ("16 GB", "6.4 GB", "8.7 GB"):
        assert figure in help_text, f"the Metal help must state {figure}"
    assert "INT8 has no optimised Metal kernel in bitsandbytes" in help_text
    assert [o["label"] for o in metal["options"]] == [
        "NF4 (~6.4 GB, recommended)",
        "INT8 (~8 GB, unoptimised on Metal)",
        "BF16 (~16 GB, needs a large Mac)",
        "FP16 (~16 GB, needs a large Mac)",
    ]


@pytest.mark.parametrize(
    "service_device,host_device",
    [("cpu", "mps"), ("mps", "cuda")],
    ids=["cpu-plugin-on-a-mac", "metal-plugin-probe-says-cuda"],
)
def test_precision_field_follows_the_device_the_plugin_was_set_up_for(
    monkeypatch, service_device, host_device
):
    """Once set up, the field describes the plugin's device, not a fresh probe.

    A forced-CPU engine on a Mac sets JoyCaption up for the CPU while
    ``detect_device()`` still answers ``mps``.
    """
    from pixlstash.tagger_plugins import joycaption as jc

    monkeypatch.setattr(jc, "detect_device", lambda: host_device)
    plugin = jc.JoyCaptionPlugin()
    plugin.setup(service_device)
    field = next(f for f in plugin.parameter_schema() if f["name"] == "precision")

    assert field == _precision_field(monkeypatch, service_device)
    assert field != _precision_field(monkeypatch, host_device)


@pytest.mark.parametrize("device", ["cuda", "cpu"])
def test_precision_field_off_metal_is_the_stock_one(monkeypatch, device):
    """Off Metal the field reads exactly as the CUDA guidance always has."""
    assert _precision_field(monkeypatch, device) == {
        "name": "precision",
        "label": "Precision",
        "type": "select",
        "default": "nf4",
        "description": (
            "Quantisation precision. NF4/INT8 use bitsandbytes and "
            "require less VRAM; BF16/FP16 require more but are faster."
        ),
        "options": [
            {"value": "nf4", "label": "NF4 (~5 GB VRAM)"},
            {"value": "int8", "label": "INT8 (~8 GB VRAM)"},
            {"value": "bf16", "label": "BF16 (~16 GB VRAM)"},
            {"value": "fp16", "label": "FP16 (~16 GB VRAM)"},
        ],
    }


@pytest.mark.parametrize("device,expected", [("mps", 1), ("cuda", None), ("cpu", None)])
def test_joycaption_asks_for_one_image_per_description_task_on_metal(device, expected):
    """One caption takes 20 s or more on Metal, and a description task holds
    the single GPU worker until it returns; elsewhere the host's size stands."""
    from pixlstash.tagger_plugins.joycaption import JoyCaptionPlugin

    assert JoyCaptionPlugin().description_task_size(device) == expected


# --------------------------------------------------------------------------- #
# The retry predicates themselves: Florence-2 and the built-in tagger
# --------------------------------------------------------------------------- #


def _florence_on_metal(monkeypatch, error):
    """A Florence2Service that believes it is on Metal and fails once.

    Returns:
        ``(service, reloaded, passes)``: *reloaded* records each CPU reload,
        *passes* the device of each inference pass. The first pass raises
        *error*; later ones caption "a caption".
    """
    import torch

    from pixlstash.tagger_plugins import florence2 as fl

    service = fl.Florence2Service(device="mps")
    service._model_device = torch.device("mps")
    service._model = object()
    service._processor = object()

    reloaded = []

    def _reload(cause=None):
        reloaded.append(cause)
        service._model_device = torch.device("cpu")
        return True

    passes = []

    def _infer(*_args, **_kwargs):
        passes.append(str(service._model_device))
        if len(passes) == 1:
            raise error
        return "a caption"

    monkeypatch.setattr(service, "_reload_on_cpu", _reload)
    monkeypatch.setattr(service, "_infer_single", _infer)
    return service, reloaded, passes


@pytest.mark.parametrize(
    "message",
    [MPS_UNIMPLEMENTED_OP_MESSAGE, MPS_OOM_MESSAGE],
    ids=["unimplemented-op", "oom"],
)
def test_florence_retries_on_the_cpu_after_a_metal_failure(
    monkeypatch, tmp_path, message
):
    """A Metal failure reloads on the CPU and returns the CPU pass's caption.

    This is generate_caption; the batch and detection paths have their own
    test below.
    """
    img = tmp_path / "x.jpg"
    Image.new("RGB", (32, 32), "red").save(img)
    service, reloaded, passes = _florence_on_metal(monkeypatch, RuntimeError(message))

    caption = service.generate_caption(str(img))

    assert len(reloaded) == 1, "a Metal failure must reload on the CPU"
    assert passes == ["mps", "cpu"], passes
    assert caption == "a caption", "the retry's caption has to be returned"


@pytest.mark.parametrize(
    "message",
    ["shape mismatch in the head", MPS_DEVICE_MISMATCH_MESSAGE],
    ids=["shape-mismatch", "device-mismatch"],
)
def test_florence_does_not_retry_a_bug(monkeypatch, tmp_path, message):
    # Negative control: the retry stays tied to device failures. A tensor left
    # on the CPU names Metal, and is still a bug that a reload would hide.
    img = tmp_path / "x.jpg"
    Image.new("RGB", (32, 32), "red").save(img)
    service, reloaded, passes = _florence_on_metal(monkeypatch, RuntimeError(message))

    assert service.generate_caption(str(img)) is None
    assert not reloaded, "a bug must not trigger a device reload"
    assert passes == ["mps"], passes


def _assert_tagger_logged_the_metal_failure(caplog):
    """The CPU-fallback warning names the device and the error, and no OOM."""
    messages = [
        r.getMessage()
        for r in caplog.records
        if r.name == "pixlstash.tagger_plugins.pixlstash_tagger"
        and r.levelno == logging.WARNING
    ]
    failure = [m for m in messages if "not currently implemented" in m]
    assert len(failure) == 1, messages
    assert "mps" in failure[0].replace(MPS_UNIMPLEMENTED_OP_MESSAGE, "")
    assert not any("OOM" in m for m in messages), messages


def test_the_tagger_spills_to_the_cpu_on_a_metal_oom(monkeypatch):
    """A Metal OOM while the built-in tagger loads retries the load on the CPU.

    This covers ``init_or_cpu_fallback`` only; the inference retries and the
    reload's cache flush have their own tests below.
    """
    from pixlstash.tagger_plugins import pixlstash_tagger as pt

    svc = pt.PixlStashTaggerService.__new__(pt.PixlStashTaggerService)
    svc._device = "mps"
    svc._lock = threading.RLock()

    attempts = []

    def _init(self):
        attempts.append(self._device)
        if self._device != "cpu":
            raise RuntimeError(MPS_OOM_MESSAGE)

    monkeypatch.setattr(pt.PixlStashTaggerService, "init", _init)
    monkeypatch.setattr(pt.PixlStashTaggerService, "is_loaded", lambda self: False)

    assert svc.init_or_cpu_fallback() is True
    assert attempts == ["mps", "cpu"], "a Metal OOM at load must retry on the CPU"
    assert svc._device == "cpu"


def test_the_tagger_spills_on_a_metal_failure_that_is_not_an_oom(monkeypatch, caplog):
    """A Metal load failure that is not an OOM also retries on the CPU.

    The CPU runs an operator Metal does not implement as readily as it takes a
    model that did not fit, so gated on is_vram_oom the commonest Metal failure
    would leave the tagger unloaded. The log names the device and the error
    rather than calling every device failure an OOM.
    """
    from pixlstash.tagger_plugins import pixlstash_tagger as pt

    svc = pt.PixlStashTaggerService.__new__(pt.PixlStashTaggerService)
    svc._device = "mps"
    svc._lock = threading.RLock()

    attempts = []

    def _init(self):
        attempts.append(self._device)
        if self._device != "cpu":
            raise RuntimeError(MPS_UNIMPLEMENTED_OP_MESSAGE)

    monkeypatch.setattr(pt.PixlStashTaggerService, "init", _init)
    monkeypatch.setattr(pt.PixlStashTaggerService, "is_loaded", lambda self: False)

    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert svc.init_or_cpu_fallback() is True
    assert attempts == ["mps", "cpu"]
    _assert_tagger_logged_the_metal_failure(caplog)


def test_the_tagger_does_not_spill_on_an_ordinary_failure(monkeypatch):
    from pixlstash.tagger_plugins import pixlstash_tagger as pt

    svc = pt.PixlStashTaggerService.__new__(pt.PixlStashTaggerService)
    svc._device = "mps"
    svc._lock = threading.RLock()

    attempts = []

    def _init(self):
        attempts.append(self._device)
        raise RuntimeError("shape mismatch")

    monkeypatch.setattr(pt.PixlStashTaggerService, "init", _init)
    monkeypatch.setattr(pt.PixlStashTaggerService, "is_loaded", lambda self: False)

    assert svc.init_or_cpu_fallback() is False
    assert attempts == ["mps"], "an ordinary error must not retry on the CPU"


# --------------------------------------------------------------------------- #
# The built-in tagger's three inference retries
#
# tag_items, tag_and_score_items and score_items each driven through a failed
# Metal pass.
# --------------------------------------------------------------------------- #


class _Batch:
    """Stands in for the stacked input tensor.

    A real tensor cannot be used. These methods do ``inputs.to(device="mps")``,
    which raises on a runner with no Metal, so the test would be decided by
    that error instead of the failure it injects.
    """

    def __init__(self, count, device="cpu"):
        self.shape = (count,)
        self.device = device

    def to(self, device=None, dtype=None):
        return _Batch(self.shape[0], str(device))


def _tagger_on_metal(monkeypatch, error, device="mps"):
    """A tagger on Metal, or *device*, whose first forward pass raises *error*.

    Built with ``__new__``: ``__init__`` resolves a checkpoint and a device,
    and these paths need neither - only labels, a transform and a model that
    fails once.
    """
    import torch

    from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService

    service = PixlStashTaggerService.__new__(PixlStashTaggerService)
    service._labels = ["alpha", "beta"]
    service._label_thresholds = {"alpha": 0.01, "beta": 0.01}
    service._image_size_full = 8
    service._batch_size_fn = lambda: 4
    service._device = device
    service._dtype = torch.float32
    service._get_transform = lambda size: lambda image: torch.zeros(3, size, size)

    monkeypatch.setattr(torch, "stack", lambda tensors: _Batch(len(tensors)))

    seen = []

    def _model(inputs):
        seen.append(str(inputs.device))
        if len(seen) == 1:
            raise error
        return torch.tensor([[2.0, -2.0]] * inputs.shape[0])

    service._model = _model

    reloaded = []

    def _reload():
        reloaded.append(True)
        service._device = "cpu"
        service._dtype = torch.float32
        return True

    service.reload_on_cpu = _reload
    return service, seen, reloaded


#: The three call sites, each reduced to "run it and hand back the keyed tags".
_TAGGER_ENTRY_POINTS = [
    pytest.param(lambda s, items: s.tag_items(items), id="tag_items"),
    pytest.param(lambda s, items: s.tag_and_score_items(items)[0], id="tag_and_score"),
    pytest.param(lambda s, items: s.score_items(items), id="score_items"),
]


@pytest.mark.parametrize("call", _TAGGER_ENTRY_POINTS)
def test_a_metal_failure_retries_the_batch_on_the_cpu(monkeypatch, caplog, call):
    """The unimplemented-operator failure has to reach the CPU retry.

    Asserts the results, not just the reload: without the retry the method
    ``break``s and the batch is silently dropped, so a test that only checked
    the reload happened would still pass if the second pass were removed.
    """
    service, seen, reloaded = _tagger_on_metal(
        monkeypatch, RuntimeError(MPS_UNIMPLEMENTED_OP_MESSAGE)
    )

    with caplog.at_level(
        logging.WARNING, logger="pixlstash.tagger_plugins.pixlstash_tagger"
    ):
        results = call(service, [("a.png", object())])

    assert reloaded == [True], "a Metal failure must trigger the CPU reload"
    assert seen == ["mps", "cpu"], f"expected a Metal pass then a CPU pass, got {seen}"
    assert results, "the retry has to produce the tags the failed pass lost"
    _assert_tagger_logged_the_metal_failure(caplog)


@pytest.mark.parametrize("call", _TAGGER_ENTRY_POINTS)
def test_a_cuda_fault_retries_the_batch_on_the_cpu(monkeypatch, call):
    """On CUDA a fault retries on the CPU, not only an out-of-memory error.

    The built-in tagger reads the same phrase set as Florence-2, CLIP and SBERT.
    """
    service, seen, reloaded = _tagger_on_metal(
        monkeypatch, RuntimeError(CUDA_ILLEGAL_ACCESS_MESSAGE), device="cuda"
    )

    results = call(service, [("a.png", object())])

    assert reloaded == [True], "a CUDA fault must trigger the CPU reload"
    assert seen == ["cuda", "cpu"], f"expected a CUDA pass then a CPU pass, got {seen}"
    assert results, "the retry has to produce the tags the failed pass lost"


@pytest.mark.parametrize("call", _TAGGER_ENTRY_POINTS)
def test_a_misplaced_tensor_on_cuda_is_not_retried_on_the_cpu(monkeypatch, call):
    # The bug names CUDA too, and must still raise rather than demote the tagger.
    service, seen, reloaded = _tagger_on_metal(
        monkeypatch, DEVICE_NAMING_BUGS["cuda-two-devices"], device="cuda"
    )

    results = call(service, [("a.png", object())])

    assert reloaded == [], "a misplaced tensor must not reload anything"
    assert seen == ["cuda"], "the batch must not be retried"
    assert not results


@pytest.mark.parametrize("call", _TAGGER_ENTRY_POINTS)
def test_an_ordinary_failure_is_not_retried_on_the_cpu(monkeypatch, call):
    # Positive control for the negative direction: the retry must stay specific
    # to device failures, or every bug in a forward pass silently demotes the
    # machine to CPU for the rest of the process.
    service, seen, reloaded = _tagger_on_metal(
        monkeypatch, RuntimeError("shape mismatch")
    )

    results = call(service, [("a.png", object())])

    assert reloaded == [], "a non-device failure must not reload anything"
    assert seen == ["mps"], "the batch must not be retried"
    assert not results


def test_the_tagger_names_the_device_it_leaves_for_the_cpu(monkeypatch, caplog):
    # reload_on_cpu is not handed the exception - its callers log that just
    # before - so what it owes the log is the device it is leaving. It also
    # flushes the allocator cache once the model is off that device.
    from pixlstash.tagger_plugins import pixlstash_tagger as pt

    class _Model:
        location = "mps"

        def to(self, target):
            self.location = str(target)
            return self

        def float(self):
            return self

    model = _Model()
    service = pt.PixlStashTaggerService.__new__(pt.PixlStashTaggerService)
    service._device = "mps"
    service._model = model
    service._load_lock = threading.RLock()
    # Not the real flush: it would call into Metal on a Mac.
    flushes = []
    monkeypatch.setattr(
        pt,
        "empty_device_cache",
        lambda device=None: flushes.append((model.location, device)) or False,
    )

    with caplog.at_level(logging.WARNING, logger=pt.logger.name):
        assert service.reload_on_cpu() is True

    assert service._device == "cpu"
    assert len(flushes) == 1, f"the reload must flush once, not {flushes}"
    location, flushed = flushes[0]
    assert location == "cpu", "the flush must come after the model left Metal"
    assert flushed in (None, "mps"), f"the flush must cover Metal, not {flushed!r}"
    messages = [
        r.getMessage()
        for r in caplog.records
        if r.name == pt.logger.name and r.levelno == logging.WARNING
    ]
    assert any("mps" in m for m in messages), messages
    assert not any("OOM" in m for m in messages), messages


# --------------------------------------------------------------------------- #
# Florence-2's batch and detection retries, beside generate_caption's above
# --------------------------------------------------------------------------- #


def _florence_batch_on_metal(monkeypatch, tmp_path, error):
    """A Florence service on Metal whose first processor call raises *error*.

    The processor is the injection point because it is the first thing inside
    both methods' try blocks, so one harness drives the captioning and the
    detection retry alike.
    """
    import torch

    from pixlstash.tagger_plugins import florence2 as fl

    image_path = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(image_path)

    service = fl.Florence2Service(device="mps")
    service._model_device = torch.device("mps")
    service._dtype = torch.float32
    service._max_tokens = 8

    # The real mover puts tensors on Metal, which raises for an unrelated
    # reason on a runner without it. The move is not what these tests are
    # about, and leaving it in would decide them on the wrong error.
    monkeypatch.setattr(
        fl, "_move_inputs_to_device", lambda inputs, device, dtype: inputs
    )

    seen = []

    class _Processor:
        tokenizer = types.SimpleNamespace(pad_token_id=0)

        def __init__(self):
            # (device, whether the call padded a batch), per call.
            self.calls = []

        def __call__(self, **kwargs):
            seen.append(str(service._model_device))
            self.calls.append((str(service._model_device), kwargs.get("padding")))
            if len(seen) == 1:
                raise error
            return {
                "input_ids": torch.zeros(1, 2, dtype=torch.long),
                "pixel_values": torch.zeros(1, 3, 8, 8),
            }

        def batch_decode(self, ids, skip_special_tokens=False):
            return ["<text>"]

        def post_process_generation(self, text, task, image_size):
            return {task: {"quad_boxes": [[0, 0, 4, 0, 4, 4, 0, 4]], "labels": ["hi"]}}

    service._processor = _Processor()
    service._model = types.SimpleNamespace(
        generate=lambda **kw: torch.zeros(1, 2, dtype=torch.long)
    )
    monkeypatch.setattr(service, "_parse_caption", lambda text: "a caption")
    monkeypatch.setattr(
        service,
        "_parse_detections",
        lambda text, token, size: [("cat", [0, 0, 1, 1], None)],
    )

    reloaded = []

    def _reload(cause=None):
        reloaded.append(cause)
        service._model_device = torch.device("cpu")
        return True

    monkeypatch.setattr(service, "_reload_on_cpu", _reload)
    return service, str(image_path), seen, reloaded


_FLORENCE_BATCH_ENTRY_POINTS = [
    pytest.param(lambda s, p: s.generate_captions_batch([p]), id="captions_batch"),
    pytest.param(lambda s, p: s.detect_objects([p]), id="detect_objects"),
    pytest.param(lambda s, p: s.read_text([p]), id="read_text"),
]


@pytest.mark.parametrize("call", _FLORENCE_BATCH_ENTRY_POINTS)
def test_a_metal_failure_retries_the_florence_batch_on_the_cpu(
    monkeypatch, tmp_path, call
):
    """Both batch paths have to reach the CPU retry, as generate_caption does.

    Asserts the returned work, not just the reload: the retry is a recursive
    call with ``_retry_on_cpu=False``, so a reload that happened but produced
    nothing would still leave the batch silently dropped.
    """
    service, path, seen, reloaded = _florence_batch_on_metal(
        monkeypatch, tmp_path, RuntimeError(MPS_UNIMPLEMENTED_OP_MESSAGE)
    )

    results = call(service, path)

    assert reloaded, "a Metal failure must trigger the CPU reload"
    assert seen == ["mps", "cpu"], f"expected a Metal pass then a CPU pass, got {seen}"
    assert results.get(path), "the retry has to produce what the failed pass lost"


def test_text_reading_leaves_an_out_of_memory_error_to_the_task_runner(
    monkeypatch, tmp_path
):
    """The runner retries an OOM once memory is freed; a CPU reload would move
    captioning, which shares the model, to the CPU for good."""
    service, path, seen, reloaded = _florence_batch_on_metal(
        monkeypatch, tmp_path, RuntimeError(MPS_OOM_MESSAGE)
    )

    with pytest.raises(RuntimeError, match="MPS backend out of memory"):
        service.read_text([path])

    assert reloaded == []
    assert seen == ["mps"]


def test_a_failed_caption_batch_is_retried_on_the_cpu_as_a_batch(monkeypatch, tmp_path):
    """The CPU retry captions the batch again, not one image at a time.

    After the reload the per-image fallback would produce the same captions on
    the CPU, one processor call per image, so only the call shape tells the
    batched retry from it.
    """
    service, path, _seen, reloaded = _florence_batch_on_metal(
        monkeypatch, tmp_path, RuntimeError(MPS_UNIMPLEMENTED_OP_MESSAGE)
    )

    results = service.generate_captions_batch([path])

    assert reloaded
    assert results.get(path)
    assert service._processor.calls == [("mps", True), ("cpu", True)]


@pytest.mark.parametrize("call", _FLORENCE_BATCH_ENTRY_POINTS)
def test_an_ordinary_florence_batch_failure_is_not_retried(monkeypatch, tmp_path, call):
    """A non-device failure must not demote the model to the CPU.

    Asserted on the device, not on the returned captions: batch captioning
    answers *any* failure with a per-image fallback pass, so a caption comes
    back either way and a results-based assertion would prove nothing. The
    reload is the thing that must not happen - it lasts the life of the
    service, so a plain bug in a forward pass would cost the GPU for the rest
    of the run.
    """
    service, path, seen, reloaded = _florence_batch_on_metal(
        monkeypatch, tmp_path, RuntimeError("shape mismatch")
    )

    call(service, path)

    assert reloaded == [], "a non-device failure must not reload anything"
    assert "cpu" not in seen, f"nothing may run on the CPU here, got {seen}"


# --------------------------------------------------------------------------- #
# transformers loads weights on one thread wherever Metal exists
# --------------------------------------------------------------------------- #


@pytest.fixture
def async_load_env(monkeypatch):
    """Start without HF_DEACTIVATE_ASYNC_LOAD; restore the real value after.

    Set, then deleted: ``delenv`` on an absent variable records nothing, so a
    value the code under test sets would otherwise outlive the test.
    """
    monkeypatch.setenv(HF_ASYNC_LOAD_ENV, "placeholder")
    monkeypatch.delenv(HF_ASYNC_LOAD_ENV)


def test_metal_turns_off_threaded_weight_loading_once(
    fake_torch, async_load_env, caplog
):
    fake_torch(_fake_torch(mps=True))

    with caplog.at_level("INFO"):
        assert configure_metal_model_loading() is True
        assert configure_metal_model_loading() is False

    assert os.environ[HF_ASYNC_LOAD_ENV] == "1"
    # A second engine (CPU spillover builds its own) must not say it again.
    announced = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO and HF_ASYNC_LOAD_ENV in r.getMessage()
    ]
    assert len(announced) == 1
    assert "docs/apple-metal-thread-safety.md" in announced[0]


def test_threaded_weight_loading_is_left_alone_without_metal(
    fake_torch, async_load_env
):
    fake_torch(_fake_torch(cuda=True, mps=False))

    assert configure_metal_model_loading() is False
    assert HF_ASYNC_LOAD_ENV not in os.environ


@pytest.mark.parametrize("value", ["0", "false", "", "no", "1", "TRUE", "yes", "on"])
def test_an_owners_own_async_load_setting_is_kept_and_a_threaded_one_warned(
    fake_torch, async_load_env, monkeypatch, caplog, value
):
    """The owner's value stands, and a warning says when it brings back the
    threaded loader that crashes on Metal.

    Whether it does is transformers' own reading of the variable, asked
    directly, so the warning cannot drift from what transformers does.
    """
    # Local, as in the loader-pool test below: transformers imports torch,
    # which fake_torch stands in for.
    from transformers.utils.import_utils import is_env_variable_true

    fake_torch(_fake_torch(mps=True))
    monkeypatch.setenv(HF_ASYNC_LOAD_ENV, value)

    with caplog.at_level(logging.WARNING, logger="pixlstash.utils.device_utils"):
        assert configure_metal_model_loading() is False

    assert os.environ[HF_ASYNC_LOAD_ENV] == value
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.name == "pixlstash.utils.device_utils" and r.levelno == logging.WARNING
    ]
    if is_env_variable_true(HF_ASYNC_LOAD_ENV):
        assert warnings == []
    else:
        assert len(warnings) == 1, warnings
        assert f"{HF_ASYNC_LOAD_ENV}={value!r}" in warnings[0]
        assert "docs/apple-metal-thread-safety.md" in warnings[0]


def test_the_variable_stops_transformers_building_its_loader_pool(
    monkeypatch, tmp_path, async_load_env
):
    """What transformers does with the variable, not that the variable is set.

    The control loads the same checkpoint without it first and has to see the
    pool built; otherwise the patched name is not the one transformers uses
    and the second assertion would pass for nothing. Metal is faked on the
    real torch only around the configure call, so both loads stay on the CPU
    of a CI runner that has no Metal.
    """
    import torch
    import transformers.core_model_loading as core_model_loading
    from transformers import BertConfig, BertModel

    config = BertConfig(
        vocab_size=16,
        hidden_size=8,
        num_hidden_layers=1,
        num_attention_heads=2,
        intermediate_size=16,
        max_position_embeddings=8,
    )
    BertModel(config).save_pretrained(tmp_path)

    pools = []
    real_pool = core_model_loading.ThreadPoolExecutor

    class _RecordingPool(real_pool):
        def __init__(self, *args, **kwargs):
            pools.append(kwargs.get("max_workers"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(core_model_loading, "ThreadPoolExecutor", _RecordingPool)

    BertModel.from_pretrained(tmp_path, device_map="cpu")
    assert len(pools) == 1, "the control load built no pool; wrong patch point"

    pools.clear()
    with monkeypatch.context() as metal:
        metal.setattr(torch.backends.mps, "is_available", lambda: True)
        assert configure_metal_model_loading() is True
    BertModel.from_pretrained(tmp_path, device_map="cpu")
    assert pools == []


def test_the_engine_configures_weight_loading_before_any_service(monkeypatch):
    """Forced onto the CPU too: device_map="auto" still finds Metal there."""
    from pixlstash.inference import engine as engine_mod

    order = []

    def _first_service(**kwargs):
        order.append("service")
        raise _StopEarly()

    monkeypatch.setattr(
        engine_mod,
        "configure_metal_model_loading",
        lambda: order.append("configure"),
    )
    monkeypatch.setattr(engine_mod, "builtin_model_dir", lambda: "/nonexistent")
    monkeypatch.setattr(
        "pixlstash.tagger_plugins.clip_service.ClipService", _first_service
    )

    with pytest.raises(_StopEarly):
        engine_mod.InferenceEngine.create(image_root="/nonexistent", force_cpu=True)

    assert order == ["configure", "service"]


# --------------------------------------------------------------------------- #
# Metal work runs on the GPU worker thread
# --------------------------------------------------------------------------- #


@pytest.fixture
def gpu_runner():
    """A started ``TaskRunner``, stopped when the test ends.

    Per test, not per module: start plus stop costs about 0.1 ms, and
    ``stop()`` joins the GPU worker. Its post-task cache flush reads
    ``sys.modules["torch"]``, so a runner outliving its test could flush
    against the stand-in torch a later test installs.
    """
    runner = TaskRunner(name="gpu-call-test")
    runner.start()
    try:
        yield runner
    finally:
        runner.stop()


class _ThreadProbeTask(BaseTask):
    """An ordinary task that returns the thread it ran on.

    Args:
        queue: The queue it runs on, the GPU queue unless given.
    """

    def __init__(self, queue=QueueType.GPU):
        super().__init__(task_type="ThreadProbeTask")
        self._queue = queue

    @property
    def queue_type(self) -> QueueType:
        return self._queue

    def _run_task(self):
        return threading.current_thread()


def _gpu_worker_of(runner):
    """The GPU worker thread, found the way every GPU task finds it."""
    return runner.submit_and_wait(_ThreadProbeTask(), timeout_s=10)


def _hold_the_gpu_worker(runner):
    """Occupy the GPU worker until the returned event is set.

    Returns:
        ``(release, holder)``: set *release*, then join *holder*.
    """
    release = threading.Event()
    started = threading.Event()

    def hold():
        started.set()
        release.wait(10)

    holder = threading.Thread(
        target=runner.run_on_gpu_worker, args=(hold,), kwargs={"timeout_s": 20}
    )
    holder.start()
    assert started.wait(10), "the holding call never reached the GPU worker"
    return release, holder


class _WorkerEndingTask(BaseTask):
    """A GPU task whose own ``run`` ends the GPU worker thread with ``SystemExit``.

    ``BaseTask.run`` records a ``SystemExit`` from ``_run_task`` as the task's
    failure, so this overrides ``run`` itself: the runner's loop catches only
    ``Exception``, and one that is not, raised outside that handling, is how a
    worker can still die.

    Args:
        release: When given, the task holds the worker until it is set.
    """

    def __init__(self, release=None):
        super().__init__(task_type="WorkerEndingTask")
        self.release = release
        self.reached = threading.Event()

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def run(self, on_vram_oom=None):
        self.reached.set()
        if self.release is not None:
            self.release.wait(10)
        sys.exit()

    def _run_task(self):
        raise AssertionError("run() is overridden, so _run_task never runs")


def _end_the_gpu_worker(runner):
    """End *runner*'s GPU worker and return the thread, once it has exited."""
    worker = _gpu_worker_of(runner)
    runner.submit(_WorkerEndingTask())
    worker.join(10)
    assert not worker.is_alive(), "the GPU worker did not end"
    return worker


def test_a_call_runs_on_the_gpu_worker_thread(gpu_runner):
    worker = _gpu_worker_of(gpu_runner)

    ran_on, total = gpu_runner.run_on_gpu_worker(
        lambda a, b=0: (threading.current_thread(), a + b), 2, b=3, timeout_s=10
    )

    assert ran_on is worker
    assert ran_on is not threading.current_thread()
    assert total == 5
    assert gpu_runner.is_gpu_worker_thread() is False


def test_a_call_made_on_the_gpu_worker_runs_inline(gpu_runner):
    """A GPU task using a routed helper must not queue behind itself.

    Queued, the inner call waits on the worker that is busy waiting for it,
    and times out.
    """
    worker = _gpu_worker_of(gpu_runner)

    def nested():
        assert gpu_runner.is_gpu_worker_thread()
        return gpu_runner.run_on_gpu_worker(threading.current_thread, timeout_s=2)

    assert gpu_runner.run_on_gpu_worker(nested, timeout_s=10) is worker


def test_an_exception_from_the_call_reaches_the_caller_unwrapped(gpu_runner):
    """The caller gets what *fn* raised, not a RuntimeError carrying its text.

    A route that called an encoder directly keeps its ``except`` clauses.
    """

    class _BadQuery(ValueError):
        pass

    error = _BadQuery("query is empty")

    def encode():
        raise error

    with pytest.raises(_BadQuery) as raised:
        gpu_runner.run_on_gpu_worker(encode, timeout_s=10)
    assert raised.value is error


def test_a_gpu_oom_in_a_call_is_retried(gpu_runner, monkeypatch):
    monkeypatch.setattr(TaskRunner, "VRAM_OOM_RETRY_PAUSE_S", 0.0)
    calls = []

    def encode():
        calls.append(threading.current_thread())
        if len(calls) == 1:
            raise RuntimeError(MPS_OOM_MESSAGE)
        return "embedding"

    assert gpu_runner.run_on_gpu_worker(encode, timeout_s=10) == "embedding"
    assert len(calls) == 2


def test_a_lasting_gpu_oom_reaches_the_caller_as_itself(gpu_runner, monkeypatch):
    """After the last attempt the OOM is still classifiable by the caller."""
    monkeypatch.setattr(TaskRunner, "VRAM_OOM_RETRY_PAUSE_S", 0.0)
    oom = RuntimeError(MPS_OOM_MESSAGE)
    calls = []

    def encode():
        calls.append(1)
        raise oom

    with pytest.raises(RuntimeError) as raised:
        gpu_runner.run_on_gpu_worker(encode, timeout_s=10)

    assert raised.value is oom
    assert len(calls) == GpuCallTask.VRAM_OOM_ATTEMPTS == 3


def test_a_call_that_opts_out_of_the_oom_retry_is_called_once(gpu_runner, monkeypatch):
    monkeypatch.setattr(TaskRunner, "VRAM_OOM_RETRY_PAUSE_S", 0.0)
    oom = RuntimeError(MPS_OOM_MESSAGE)
    calls = []

    def run():
        calls.append(1)
        raise oom

    with pytest.raises(RuntimeError) as raised:
        gpu_runner.run_on_gpu_worker(run, timeout_s=10, retry_vram_oom=False)

    assert raised.value is oom
    assert len(calls) == 1


def test_a_stopped_runner_raises_instead_of_calling_inline():
    runner = TaskRunner(name="gpu-call-stopped")
    runner.start()
    runner.stop()
    ran = []

    with pytest.raises(RuntimeError, match="stopped"):
        runner.run_on_gpu_worker(ran.append, "ran", timeout_s=1)
    assert ran == []


def test_a_call_still_queued_when_the_runner_stops_is_cancelled(gpu_runner):
    release, holder = _hold_the_gpu_worker(gpu_runner)
    outcome = []

    def queue_a_call():
        try:
            outcome.append(gpu_runner.run_on_gpu_worker(lambda: "ran", timeout_s=10))
        except Exception as exc:
            outcome.append(exc)

    caller = threading.Thread(target=queue_a_call)
    stopper = threading.Thread(target=gpu_runner.stop)
    try:
        caller.start()
        deadline = time.monotonic() + 10
        while gpu_runner._gpu_queue.qsize() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        # stop() drains the queue before it joins the worker, so the queued
        # call is settled while the worker is still held.
        stopper.start()
        caller.join(10)
    finally:
        release.set()
        holder.join(10)
        if stopper.ident is not None:
            stopper.join(10)

    assert len(outcome) == 1
    assert isinstance(outcome[0], TaskCancelledError)


def test_a_call_that_times_out_raises_and_never_runs(gpu_runner):
    release, holder = _hold_the_gpu_worker(gpu_runner)
    ran = []
    try:
        with pytest.raises(TimeoutError):
            gpu_runner.run_on_gpu_worker(ran.append, "late", timeout_s=0.2)
    finally:
        release.set()
        holder.join(10)

    # Queued after the abandoned call, so its return means the worker got past it.
    gpu_runner.run_on_gpu_worker(lambda: None, timeout_s=10)
    assert ran == [], "a call its caller gave up on must not run later"


@pytest.mark.parametrize("interrupt", [SystemExit(3), KeyboardInterrupt()])
def test_a_call_that_raises_an_exit_reaches_the_caller_and_the_worker_runs_on(
    gpu_runner, interrupt
):
    """A plugin's ``sys.exit()`` must not end the one thread GPU work runs on.

    A ``KeyboardInterrupt`` raised on the worker is code raising it, never the
    owner's Ctrl+C: only the main thread receives ``SIGINT``.
    """
    worker = _gpu_worker_of(gpu_runner)

    def end_the_thread():
        raise interrupt

    with pytest.raises(type(interrupt)) as raised:
        gpu_runner.run_on_gpu_worker(end_the_thread, timeout_s=10)

    assert raised.value is interrupt
    assert worker.is_alive(), "the GPU worker thread ended"
    assert gpu_runner.run_on_gpu_worker(threading.current_thread, timeout_s=10) is (
        worker
    ), "the next call did not run on the same GPU worker"


class _ExitingTask(BaseTask):
    """A task whose ``_run_task`` raises *interrupt* on the queue named.

    Args:
        queue: The queue the task runs on.
        interrupt: The ``BaseException`` to raise.

    Attributes:
        ran_on: The worker thread that ran it.
    """

    def __init__(self, queue, interrupt):
        super().__init__(task_type="ExitingTask")
        self._queue = queue
        self._interrupt = interrupt
        self.ran_on = None

    @property
    def queue_type(self) -> QueueType:
        return self._queue

    def _run_task(self):
        self.ran_on = threading.current_thread()
        raise self._interrupt


@pytest.mark.parametrize("queue", [QueueType.CPU, QueueType.GPU], ids=["cpu", "gpu"])
@pytest.mark.parametrize(
    "interrupt", [SystemExit(3), KeyboardInterrupt()], ids=["exit", "interrupt"]
)
def test_a_task_that_raises_an_exit_fails_and_its_worker_runs_on(
    gpu_runner, caplog, queue, interrupt
):
    """A ``sys.exit()`` inside a task fails that task and ends no worker.

    A third-party tagger plugin runs inside ``TagTask``, an ordinary GPU task.
    Each queue of this runner has one worker, so the next task on the queue
    running on the same thread is what shows the thread survived.
    """
    settled = threading.Event()
    errors = []
    task = _ExitingTask(queue, interrupt)

    def on_complete(completed, error):
        if completed is task:
            errors.append(error)
            settled.set()

    gpu_runner.add_task_complete_callback(on_complete)
    with caplog.at_level(logging.WARNING, logger="pixlstash.tasks.base_task"):
        with pytest.raises(RuntimeError, match="failed"):
            gpu_runner.submit_and_wait(task, timeout_s=10)
        assert settled.wait(10), "the runner never reported the task settled"

    assert task.status == TaskStatus.FAILED
    assert len(errors) == 1
    assert isinstance(errors[0], TaskInterruptedError), errors
    assert errors[0].__cause__ is interrupt
    assert gpu_runner.submit_and_wait(_ThreadProbeTask(queue), timeout_s=5) is (
        task.ran_on
    ), "the next task did not run on the same worker"
    logged = [
        record.getMessage()
        for record in caplog.records
        if record.name == "pixlstash.tasks.base_task"
        and record.levelno == logging.WARNING
    ]
    assert len(logged) == 1, logged
    assert task.id in logged[0]
    assert "ExitingTask" in logged[0]
    assert type(interrupt).__name__ in logged[0]


def test_a_call_task_names_its_function_without_rendering_its_arguments():
    """Naming the call must not touch what the call carries.

    ``Vault._run_on_device_thread`` wraps every routed call in a
    ``functools.partial``, and the task is built on the calling thread. A
    ``repr`` of the bound arguments would run there, and a Metal tensor's
    copies it to the CPU - the off-worker Metal use this task exists to keep
    out.
    """

    class _UnrenderableTensor:
        def __repr__(self):
            raise AssertionError("the task rendered one of the call's arguments")

    def encode_query(_tensor):
        return None

    task = GpuCallTask(functools.partial(encode_query, _UnrenderableTensor()))

    assert task.params["fn"].endswith("encode_query")


def test_a_call_task_is_never_held_by_the_vram_gate(monkeypatch):
    """Nothing may park a call on its way to the worker or send it elsewhere.

    ``TaskRunner._run`` skips the VRAM gate for GPU-queue tasks. Were the gate
    consulted anyway, a call must still pass at once: the budget here is
    exceeded and other work holds a reservation, so a task with any VRAM
    estimate would wait for as long as the runner runs.
    """
    task = GpuCallTask(lambda: None)
    assert task.queue_type == QueueType.GPU
    assert task.priority == TaskPriority.URGENT
    assert task.allow_cpu_spillover() is False

    runner = TaskRunner(name="gpu-call-gate")
    runner._max_vram_usage_mb = 1024
    runner._vram_reserved_mb = 512
    monkeypatch.setattr(
        TaskRunner, "_get_process_vram_mb", classmethod(lambda cls: 4096)
    )
    reserved = []
    gate = threading.Thread(
        target=lambda: reserved.append(runner._wait_for_vram_budget(task)),
        daemon=True,
    )
    gate.start()
    gate.join(2)
    held = gate.is_alive()
    runner._stop.set()  # lets a gate that did hold the call give up
    gate.join(2)

    assert not held, "the VRAM gate held a GPU call back"
    assert reserved == [0]


def test_a_runner_that_was_never_started_raises_at_once():
    """No worker is coming, so the call must not wait out its timeout first."""
    runner = TaskRunner(name="gpu-call-unstarted")
    ran = []

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="never started"):
        runner.run_on_gpu_worker(ran.append, "ran", timeout_s=2)

    assert time.monotonic() - started < 1.0
    assert ran == []


def test_a_call_task_skips_the_flush_an_ordinary_gpu_task_gets(gpu_runner, monkeypatch):
    """The post-task cache flush is for batches, not for a query encode.

    Each flush is counted once the worker has finished the task's ``finally``:
    a call queued after it returns only once the worker has dequeued it, which
    is after that ``finally`` ran. The calls used as barriers are call tasks
    themselves, so they add nothing to the count.
    """
    flushes = []
    monkeypatch.setattr(
        task_runner_module,
        "empty_cuda_cache",
        lambda: flushes.append(threading.current_thread()) or False,
    )

    worker = _gpu_worker_of(gpu_runner)
    gpu_runner.run_on_gpu_worker(lambda: None, timeout_s=10)
    assert flushes == [worker], "an ordinary GPU task must still flush after it"

    for _ in range(3):
        gpu_runner.run_on_gpu_worker(lambda: None, timeout_s=10)
    assert flushes == [worker], "a call task must not flush after it runs"


class _CyclicGarbageTask(BaseTask):
    """An ordinary GPU task that leaves a reference cycle behind as garbage."""

    def __init__(self):
        super().__init__(task_type="CyclicGarbageTask")
        self.garbage_ref = None

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def _run_task(self):
        garbage = _CyclicModel()
        self.garbage_ref = weakref.ref(garbage)


def test_the_runner_collects_a_gpu_tasks_garbage_before_flushing(
    gpu_runner, monkeypatch
):
    """Tensors a task left in reference cycles are freed before the flush.

    The call queued after the task returns once the worker has run the task's
    ``finally``, where the collection and the flush are.
    """
    task = _CyclicGarbageTask()
    alive_at_flush = []
    monkeypatch.setattr(
        task_runner_module,
        "empty_cuda_cache",
        lambda: alive_at_flush.append(task.garbage_ref() is not None) or False,
    )

    with _only_explicit_collections():
        gpu_runner.submit_and_wait(task, timeout_s=10)
        gpu_runner.run_on_gpu_worker(lambda: None, timeout_s=10)

    assert alive_at_flush == [False], (
        "the runner flushed the device cache while the task's garbage was alive"
    )


class _FreedOn:
    """Stands in for a Metal tensor: records the thread that frees it."""

    def __init__(self, freed_on):
        self._freed_on = freed_on

    def __del__(self):
        self._freed_on.append(threading.current_thread())


class _CyclicFreedOn(_FreedOn):
    """A :class:`_FreedOn` in a reference cycle, as a loaded model often is."""

    def __init__(self, freed_on):
        super().__init__(freed_on)
        self.self_reference = self


def _fail_holding(held_type, freed_on):
    held = held_type(freed_on)  # noqa: F841 - what the failed pass's frame holds
    raise RuntimeError("the pass failed")


def _fail_holding_under_a_wrapper(held_type, freed_on):
    """Fail the way a plugin that reports failures as its own error does."""
    try:
        _fail_holding(held_type, freed_on)
    except RuntimeError as exc:
        raise ValueError("the plugin failed") from exc


@pytest.mark.parametrize("held_type", [_FreedOn, _CyclicFreedOn])
@pytest.mark.parametrize("fail", [_fail_holding, _fail_holding_under_a_wrapper])
def test_what_a_failed_call_held_is_freed_on_the_gpu_worker(
    gpu_runner, fail, held_type
):
    """The caller keeps the error, and would free its frames' tensors itself.

    Freed before the caller wakes, so the error is still alive when this checks.
    Automatic collection is off, so a cycle is freed only by the worker's own
    collection.
    """
    worker = _gpu_worker_of(gpu_runner)
    vault = _routing_vault(gpu_runner, "mps")
    freed_on = []

    with _only_explicit_collections():
        with pytest.raises((RuntimeError, ValueError), match="failed") as raised:
            vault.run_inference(fail, held_type, freed_on)
        assert freed_on == [worker], (
            f"freed on {freed_on} while the caller held {raised.value!r}"
        )


def _run_a_cyclic_model(freed_on):
    model = _CyclicFreedOn(freed_on)  # noqa: F841 - garbage once this returns


def test_a_long_calls_garbage_is_collected_on_the_gpu_worker(gpu_runner):
    """A plugin run whose model sits in a reference cycle leaves it as garbage."""
    worker = _gpu_worker_of(gpu_runner)
    vault = _routing_vault(gpu_runner, "mps")
    freed_on = []

    with _only_explicit_collections():
        vault.run_long_inference(_run_a_cyclic_model, freed_on)
        assert freed_on == [worker]


# --------------------------------------------------------------------------- #
# The Metal thread guard
# --------------------------------------------------------------------------- #


def test_the_guard_refuses_metal_off_the_gpu_worker(gpu_runner):
    worker = _gpu_worker_of(gpu_runner)
    assert registered_metal_thread() is worker

    for device in ("mps", "mps:0", types.SimpleNamespace(type="mps")):
        with pytest.raises(RuntimeError) as raised:
            ensure_metal_thread(device)
        message = str(raised.value)
        assert repr(threading.current_thread().name) in message
        assert repr(worker.name) in message
        assert "Vault.run_inference" in message


def test_the_guard_allows_metal_on_the_gpu_worker(gpu_runner):
    outcome = gpu_runner.run_on_gpu_worker(ensure_metal_thread, "mps", timeout_s=10)
    assert outcome is None


def test_the_guard_ignores_every_other_device(gpu_runner):
    for device in ("cuda", "cuda:0", "cpu", None):
        ensure_metal_thread(device)
    # Positive control: the same thread, with the same runner, is refused Metal.
    with pytest.raises(RuntimeError, match="Vault.run_inference"):
        ensure_metal_thread("mps")


def test_the_guard_is_silent_with_no_running_task_runner():
    """The CLI tools and tests without a runner have no worker to race."""
    ensure_metal_thread("mps")

    runner = TaskRunner(name="guard-lifecycle")
    runner.start()
    try:
        with pytest.raises(RuntimeError, match="Vault.run_inference"):
            ensure_metal_thread("mps")
    finally:
        runner.stop()

    assert registered_metal_thread() is None
    ensure_metal_thread("mps")


def test_a_stopping_runner_leaves_a_newer_registration_alone():
    first = TaskRunner(name="guard-first")
    second = TaskRunner(name="guard-second")
    first.start()
    try:
        second.start()
        try:
            newer = second._gpu_worker_thread
            assert registered_metal_thread() is newer
            first.stop()
            assert registered_metal_thread() is newer
        finally:
            second.stop()
    finally:
        first.stop()
    assert registered_metal_thread() is None


def test_metal_present_reads_the_torch_already_loaded(monkeypatch, fake_torch, caplog):
    import builtins

    # With no torch loaded it must not import one: an import would answer from
    # the real torch, which on a Mac says Metal is there and elsewhere happens
    # to agree with "no".
    real_import = builtins.__import__
    imported = []

    def record_torch_imports(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr(builtins, "__import__", record_torch_imports)
    assert device_utils.metal_present() is False
    assert imported == []
    monkeypatch.setattr(builtins, "__import__", real_import)

    fake_torch(_fake_torch(mps=True))
    assert device_utils.metal_present() is True

    fake_torch(_fake_torch(mps_raises=RuntimeError("Metal is broken")))
    with caplog.at_level(logging.WARNING):
        assert device_utils.metal_present() is False
    assert any("Metal is broken" in message for message in _device_warnings(caplog))


@pytest.mark.parametrize("metal", [True, False])
def test_a_replaced_gpu_worker_warns_about_metal_only_where_it_exists(
    fake_torch, caplog, metal
):
    """Off Metal the registration guards nothing, so replacing it says nothing."""
    fake_torch(_fake_torch(mps=metal))
    release = threading.Event()
    previous = threading.Thread(target=release.wait, args=(10,), name="old-gpu")
    incoming = threading.Thread(target=lambda: None, name="new-gpu")
    previous.start()
    try:
        with caplog.at_level(logging.WARNING):
            register_metal_thread(previous)
            register_metal_thread(incoming)
    finally:
        release.set()
        previous.join(10)

    assert registered_metal_thread() is incoming
    replaced = [m for m in _device_warnings(caplog) if "replaces" in m]
    assert len(replaced) == (1 if metal else 0), replaced


class _NeverUnloaded:
    """A service whose unload must not be reached."""

    def __init__(self):
        self.unloads = 0

    def unload(self):
        self.unloads += 1


def _metal_entry_points(monkeypatch):
    """``name -> (call, reached)`` for every entry point that carries the guard.

    *call* uses the service on ``mps``; *reached* says whether it got past the
    guard to its model, its load or its unloads. SBERT and CLIP start
    unloaded, so a guard placed after their ``ensure_ready`` shows up as a
    load; the tagger's model is present, since ``localize_anomaly`` refuses an
    unloaded one rather than loading it.
    """
    from pixlstash.tagger_plugins import sbert as sbert_module

    clip = _loaded_clip("mps", RuntimeError("unused"))
    sbert = _loaded_sbert(monkeypatch, "mps", RuntimeError("unused"))
    unloaded = _NeverUnloaded()
    lifecycle = ModelLifecycleManager(device="mps")
    tagger = PixlStashTaggerService.__new__(PixlStashTaggerService)
    tagger._device = "mps"
    tagger._model = object()
    tagger._label_to_idx = {}
    sbert_calls = []
    sbert_model = sbert._model
    real_sbert_encode = sbert_model.encode
    sbert_model.encode = lambda texts, **kw: (
        sbert_calls.append(texts) or real_sbert_encode(texts, **kw)
    )
    tokens = []
    clip._tokenizer = lambda texts: tokens.append(texts) or _FakeTensor()
    clip_model = clip._model
    clip_parts = (clip._model, clip._preprocess, clip._tokenizer)
    loads = []

    def load_clip():
        loads.append("clip")
        clip._model, clip._preprocess, clip._tokenizer = clip_parts

    clip._load = load_clip
    clip.unload()
    monkeypatch.setattr(
        sbert_module,
        "load_sentence_transformer",
        lambda *a, **kw: loads.append("sbert") or sbert_model,
    )
    sbert.unload()
    tagger_calls = []
    tagger.resolve_label_index = lambda label: tagger_calls.append(label)
    return {
        "sbert.encode": (
            lambda: sbert.encode(["a caption"]),
            lambda: loads or sbert_calls,
        ),
        "clip.encode_text": (
            lambda: clip.encode_text("a query"),
            lambda: loads or tokens,
        ),
        "clip.encode_image_batch": (
            lambda: clip.encode_image_batch([object()]),
            lambda: loads or clip_model.calls,
        ),
        "clip.encode_image_crops": (
            lambda: clip.encode_image_crops([object()]),
            lambda: loads or clip_model.calls,
        ),
        "lifecycle.aggressive_unload": (
            lambda: lifecycle.aggressive_unload(clip_service=unloaded),
            lambda: unloaded.unloads,
        ),
        "lifecycle.safe_idle_unload": (
            lambda: lifecycle.safe_idle_unload(clip_service=unloaded),
            lambda: unloaded.unloads,
        ),
        "tagger.localize_anomaly": (
            lambda: tagger.localize_anomaly(Image.new("RGB", (4, 4)), "hand"),
            lambda: tagger_calls,
        ),
    }


@pytest.mark.parametrize(
    "entry_point",
    [
        "sbert.encode",
        "clip.encode_text",
        "clip.encode_image_batch",
        "clip.encode_image_crops",
        "lifecycle.aggressive_unload",
        "lifecycle.safe_idle_unload",
        "tagger.localize_anomaly",
    ],
)
def test_a_metal_entry_point_refuses_a_thread_that_is_not_the_gpu_worker(
    entry_point, gpu_runner, fake_torch, monkeypatch
):
    """The guard fires before a model loads or runs, or anything is unloaded."""
    fake_torch(_tensor_torch(cuda=False, mps=True))
    call, reached = _metal_entry_points(monkeypatch)[entry_point]

    with pytest.raises(RuntimeError, match="Vault.run_inference"):
        call()
    assert not reached(), f"{entry_point} used the model before refusing"

    # Positive control: on the worker the guard lets it through to the model.
    # What the fake model does next is not the point, so an error it raises
    # is fine as long as it is not the guard's.
    def on_the_worker():
        try:
            call()
        except Exception as exc:
            assert "Vault.run_inference" not in str(exc), exc

    gpu_runner.run_on_gpu_worker(on_the_worker, timeout_s=10)
    assert reached(), f"{entry_point} did not reach its model on the GPU worker"


class _RecordingEngine:
    """An engine carrying only the device the routing reads."""

    def __init__(self, device):
        self.device = device


def _routing_vault(runner, device):
    """A Vault carrying only what the routing reads: a runner and an engine.

    Built without ``__init__``, which opens a database and plans work; the
    methods under test are the real ones.
    """
    vault = Vault.__new__(Vault)
    vault._task_runner = runner
    vault._engine = _RecordingEngine(device)
    return vault


def test_run_inference_keeps_keyword_arguments_for_the_call(gpu_runner):
    """A ``timeout_s`` meant for *fn* is not taken for the runner's own."""
    vault = _routing_vault(gpu_runner, "mps")

    assert vault.run_inference(lambda timeout_s: timeout_s, timeout_s=0.001) == 0.001


class _BlockingUnloadEngine:
    """An engine whose idle unload waits until the test lets it finish."""

    def __init__(self, device):
        self.device = device
        self.release = threading.Event()
        self.started = threading.Event()
        self.finished = threading.Event()
        self.thread = None

    def aggressive_unload(self):
        self.thread = threading.current_thread()
        self.started.set()
        self.release.wait(5)
        self.finished.set()


def _wait_until_the_worker_is_idle(runner):
    """Wait for the worker to finish the ``finally`` of the task it last ran.

    A waiter wakes when the task settles, slightly before the runner drops it
    from its active set, and the idle unload skips itself while that set holds
    a GPU task.
    """
    deadline = time.monotonic() + 10
    while runner.has_active_gpu_tasks():
        assert time.monotonic() < deadline, "the GPU worker never went idle"
        time.sleep(0.01)


def _idle_vault(runner, device):
    vault = _routing_vault(runner, device)
    vault._engine = _BlockingUnloadEngine(device)
    vault._keep_models_in_memory = False
    vault._last_aggressive_unload_at = 0.0
    return vault


def test_the_idle_unload_is_queued_to_the_gpu_worker_on_metal(gpu_runner):
    """It frees models and flushes Metal, from a thread answering a poll."""
    worker = _gpu_worker_of(gpu_runner)
    _wait_until_the_worker_is_idle(gpu_runner)
    vault = _idle_vault(gpu_runner, "mps")
    engine = vault._engine

    try:
        vault._maybe_aggressive_unload({})
        assert not engine.finished.is_set(), (
            "the progress poll waited for the unload instead of queueing it"
        )
        assert engine.started.wait(10), "the queued unload never ran"
    finally:
        engine.release.set()
    assert engine.finished.wait(10)
    assert engine.thread is worker, f"the unload ran on {engine.thread.name}"
    assert vault._last_aggressive_unload_at > 0.0


@pytest.mark.parametrize("device", ["cuda", "cpu"])
def test_the_idle_unload_stays_inline_off_metal(gpu_runner, device):
    vault = _idle_vault(gpu_runner, device)
    engine = vault._engine
    engine.release.set()

    vault._maybe_aggressive_unload({})

    assert engine.finished.is_set()
    assert engine.thread is threading.current_thread()


def test_the_idle_unload_is_not_run_inline_when_the_runner_is_stopped(caplog):
    runner = TaskRunner(name="idle-unload-stopped")
    runner.start()
    runner.stop()
    vault = _idle_vault(runner, "mps")
    engine = vault._engine
    engine.release.set()

    with caplog.at_level(logging.WARNING):
        vault._maybe_aggressive_unload({})

    assert engine.thread is None, "the unload ran with no GPU worker to run it"
    assert vault._last_aggressive_unload_at == 0.0
    assert any("task runner is stopped" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# Background work that loaded or flushed off the GPU worker
# --------------------------------------------------------------------------- #


class _PreloadRecordingWorkflow:
    def __init__(self, device):
        self._engine = types.SimpleNamespace(device=device)
        self.preloads = []

    def ensure_active_plugin_ready(self, engine_override=None):
        self.preloads.append(threading.current_thread())


def test_a_tag_task_on_metal_leaves_its_model_load_to_the_gpu_worker():
    workflow = _PreloadRecordingWorkflow("mps")
    task = TagTask(database=None, tagging_workflow=workflow, pictures=[])

    task.on_queued()
    task.on_cancel()

    assert workflow.preloads == []
    assert task._model_preload_thread is None


def test_a_tag_task_off_metal_still_preloads_its_model_on_queue():
    """Positive control: the queue-time preload that keeps CUDA's worker busy."""
    workflow = _PreloadRecordingWorkflow("cuda")
    task = TagTask(database=None, tagging_workflow=workflow, pictures=[])

    task.on_queued()
    task.on_cancel()

    assert len(workflow.preloads) == 1
    assert workflow.preloads[0] is not threading.current_thread()


def test_releasing_insightface_flushes_cuda_but_never_metal(fake_torch):
    """The release also runs on the planner thread, from the finder's drain."""
    flushed = []
    torch = fake_torch(_fake_torch(cuda=True, mps=True))
    torch.cuda.empty_cache = lambda: flushed.append("cuda")
    torch.mps.empty_cache = lambda: flushed.append("mps")

    FaceExtractionTask.release_detection_models()

    assert flushed == ["cuda"]


# --------------------------------------------------------------------------- #
# A GPU worker that dies, or outlives its runner's stop
# --------------------------------------------------------------------------- #


# The worker thread dying of the SystemExit is the situation under test.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_call_to_a_dead_gpu_worker_raises_at_once(gpu_runner):
    """Queued, the call would wait out its whole timeout for nobody."""
    worker = _end_the_gpu_worker(gpu_runner)
    ran = []

    started = time.monotonic()
    with pytest.raises(TaskRunnerNotRunningError) as raised:
        gpu_runner.run_on_gpu_worker(ran.append, "ran", timeout_s=5)

    assert time.monotonic() - started < 1.0
    assert repr(worker.name) in str(raised.value)
    assert ran == []
    assert gpu_runner._gpu_queue.qsize() == 0, (
        "the call was queued for a worker that will never take it"
    )


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_call_waiting_without_a_timeout_ends_when_the_worker_dies(gpu_runner):
    """The call queued behind the task that kills the worker must not wait for ever."""
    release = threading.Event()
    ending = _WorkerEndingTask(release)
    gpu_runner.submit(ending)
    assert ending.reached.wait(10), "the ending task never reached the GPU worker"
    ran = []
    outcome = []

    def wait_with_no_timeout():
        try:
            outcome.append(
                gpu_runner.run_on_gpu_worker(ran.append, "ran", timeout_s=None)
            )
        except Exception as exc:
            outcome.append(exc)

    waiter = threading.Thread(target=wait_with_no_timeout, daemon=True)
    waiter.start()
    deadline = time.monotonic() + 10
    while gpu_runner._gpu_queue.qsize() == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    release.set()
    waiter.join(10)

    assert not waiter.is_alive(), "the call kept waiting on a GPU worker that died"
    assert len(outcome) == 1
    assert isinstance(outcome[0], TaskRunnerNotRunningError), outcome
    assert ran == []


def test_the_guard_says_so_when_the_registered_worker_has_died():
    """A dead worker still refuses Metal, rather than letting every thread in."""
    dead = threading.Thread(target=lambda: None, name="dead-gpu-worker")
    dead.start()
    dead.join()
    register_metal_thread(dead)

    with pytest.raises(RuntimeError) as raised:
        ensure_metal_thread("mps")

    message = str(raised.value)
    assert "no longer running" in message
    assert repr("dead-gpu-worker") in message
    ensure_metal_thread("cpu")


def test_a_gpu_worker_that_outlives_stop_stays_the_metal_thread(monkeypatch):
    """Clearing it would let the stopping thread unload models under it."""
    monkeypatch.setattr(TaskRunner, "STOP_JOIN_TIMEOUT_S", 0.2)
    runner = TaskRunner(name="guard-lingering")
    runner.start()
    worker = _gpu_worker_of(runner)
    release, holder = _hold_the_gpu_worker(runner)
    unloaded = _NeverUnloaded()
    try:
        runner.stop()

        assert worker.is_alive()
        assert registered_metal_thread() is worker
        with pytest.raises(RuntimeError, match="Vault.run_inference"):
            ModelLifecycleManager(device="mps").aggressive_unload(clip_service=unloaded)
        assert unloaded.unloads == 0
        with pytest.raises(TaskRunnerNotRunningError):
            runner.run_on_gpu_worker(lambda: None, timeout_s=1)
    finally:
        release.set()
        holder.join(10)
    worker.join(10)

    assert not worker.is_alive()
    assert registered_metal_thread() is None, (
        "the worker did not clear its registration when it finally exited"
    )


@pytest.mark.parametrize("metal", [True, False])
def test_a_gpu_worker_that_outlives_stop_names_metal_only_where_it_exists(
    monkeypatch, fake_torch, caplog, metal
):
    monkeypatch.setattr(TaskRunner, "STOP_JOIN_TIMEOUT_S", 0.2)
    runner = TaskRunner(name="guard-lingering-log")
    runner.start()
    worker = _gpu_worker_of(runner)
    release, holder = _hold_the_gpu_worker(runner)
    fake_torch(_fake_torch(mps=metal))
    try:
        with caplog.at_level(logging.WARNING, logger=task_runner_module.__name__):
            runner.stop()
        assert worker.is_alive()
    finally:
        release.set()
        holder.join(10)
    worker.join(10)

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == task_runner_module.__name__
    ]
    assert any(
        worker.name in m and "did not stop within timeout" in m for m in messages
    ), messages
    named = [m for m in messages if "Apple Metal" in m]
    assert len(named) == (1 if metal else 0), named


class _ClosableEngine:
    """An engine that records the thread each close ran on."""

    def __init__(self, device):
        self.device = device
        self.closes: list[threading.Thread] = []

    def close(self):
        self.closes.append(threading.current_thread())


class _ClosableDb:
    def __init__(self):
        self.closes = 0
        self.collected: list[bool] = []

    def close(self, collect_garbage=True):
        self.closes += 1
        self.collected.append(collect_garbage)


class _RecordingPart:
    """A planner or watcher stand-in that counts its starts and stops.

    Args:
        start_error: Raised by ``start`` when given.
        stop_needs_start: Makes ``stop`` raise when ``start`` never ran, as a
            watchdog observer does.
    """

    def __init__(self, start_error=None, stop_needs_start=False):
        self.start_error = start_error
        self.stop_needs_start = stop_needs_start
        self.starts = 0
        self.stops = 0

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.starts += 1

    def stop(self):
        self.stops += 1
        if self.stop_needs_start and not self.starts:
            raise RuntimeError("cannot join thread before it is started")


def _lifecycle_vault(runner, device, *, started):
    """A Vault carrying only what ``start`` and ``stop`` read.

    Built without ``__init__``, which opens a database and plans work; the
    methods under test are the real ones.
    """
    vault = Vault.__new__(Vault)
    vault.image_root = "library-under-test"
    vault._changed_tags_notify_lock = threading.Lock()
    vault._changed_tags_flush_timer = None
    vault._changed_tags_pending_ids = set()
    vault._closed = False
    vault._started = started
    vault._disable_background_workers = False
    vault._task_runner = runner
    vault._work_planner = _RecordingPart()
    vault._ref_folder_watcher = _RecordingPart(stop_needs_start=True)
    vault._start_existing_folder_watches = lambda: None
    vault._engine = _ClosableEngine(device)
    vault.db = _ClosableDb()
    return vault


@pytest.fixture
def model_releases(monkeypatch):
    """Record the class-level model releases ``Vault.stop`` makes."""
    releases = []
    monkeypatch.setattr(
        FaceExtractionTask,
        "release_detection_models",
        classmethod(lambda cls: releases.append("insightface")),
    )
    monkeypatch.setattr(
        ImageEmbeddingTask,
        "release_models",
        classmethod(lambda cls: releases.append("aesthetic")),
    )
    monkeypatch.setattr(Vault, "_engines_left_loaded", [])
    return releases


@pytest.mark.parametrize("device", ["mps", "cpu"])
def test_vault_stop_leaves_models_loaded_while_a_metal_worker_runs(
    device, model_releases, monkeypatch, caplog
):
    """Unloading from the stopping thread would race the worker on Metal."""
    monkeypatch.setattr(TaskRunner, "STOP_JOIN_TIMEOUT_S", 0.2)
    runner = TaskRunner(name="vault-lingering")
    runner.start()
    worker = _gpu_worker_of(runner)
    release, holder = _hold_the_gpu_worker(runner)
    vault = _lifecycle_vault(runner, device, started=True)
    vault._work_planner.start()
    vault._ref_folder_watcher.start()
    engine, db = vault._engine, vault.db
    try:
        with caplog.at_level(logging.WARNING):
            vault.stop()
        assert worker.is_alive()
    finally:
        release.set()
        holder.join(10)
        worker.join(10)

    assert db.closes == 1
    assert vault._engine is None
    if device == "mps":
        assert engine.closes == [], "the engine was closed beside a Metal worker"
        assert db.collected == [False], "the close collected garbage beside the worker"
        assert model_releases == []
        assert Vault._engines_left_loaded == [(engine, worker)]
        assert any(
            worker.name in r.getMessage()
            and "leaves its models loaded" in r.getMessage()
            for r in caplog.records
        )
    else:
        # Off Metal nothing changes: the close runs here, as it always did.
        assert engine.closes == [threading.current_thread()]
        assert db.collected == [True]
        assert model_releases == ["insightface", "aesthetic"]
        assert Vault._engines_left_loaded == []

    # Positive control: once the worker has gone, a Metal engine closes here.
    later = _lifecycle_vault(None, "mps", started=False)
    later_engine, later_db = later._engine, later.db
    later.stop()
    assert later_engine.closes == [threading.current_thread()]
    assert later_db.collected == [True]


@pytest.mark.parametrize("collect", [True, False])
def test_the_database_close_collects_garbage_only_when_asked(
    tmp_path, monkeypatch, collect
):
    """``Vault.stop`` asks it not to while a GPU worker still runs on Metal."""
    db = VaultDatabase(str(tmp_path / "vault.db"))
    here = threading.current_thread()
    collections = []
    real_collect = gc.collect
    monkeypatch.setattr(
        gc,
        "collect",
        lambda *a, **k: (
            collections.append(threading.current_thread()) or real_collect(*a, **k)
        ),
    )

    db.close(collect_garbage=collect)

    assert (here in collections) is collect


def test_a_later_start_closes_an_engine_left_loaded_once_its_worker_exited(
    model_releases,
):
    """Only on the new GPU worker, and only for an engine whose worker is gone.

    An engine whose worker still runs stays held: closing it would use Metal
    beside that worker.
    """
    exited = threading.Thread(target=lambda: None, name="exited-gpu-worker")
    exited.start()
    exited.join()
    closable = _ClosableEngine("mps")
    still_running = threading.Event()
    running = threading.Thread(
        target=still_running.wait, args=(10,), name="running-gpu-worker", daemon=True
    )
    running.start()
    kept = _ClosableEngine("mps")
    Vault._engines_left_loaded.extend([(closable, exited), (kept, running)])
    runner = TaskRunner(name="vault-closes-left-engines")
    vault = _lifecycle_vault(runner, "mps", started=False)
    try:
        vault.start()
        worker = runner._gpu_worker_thread
        # Queued after the close, so its return means the close has run.
        runner.run_on_gpu_worker(lambda: None, timeout_s=10)

        assert closable.closes == [worker]
        assert kept.closes == []
        assert Vault._engines_left_loaded == [(kept, running)]
        assert model_releases == [], "class-level models this vault uses were released"
    finally:
        still_running.set()
        running.join(10)
        runner.stop()


def test_a_left_engine_whose_close_was_cancelled_is_still_held(model_releases):
    """Dropped when the close was queued, the engine would be freed by whoever
    drained the queue, with its models still on Metal."""
    exited = threading.Thread(target=lambda: None, name="exited-gpu-worker")
    exited.start()
    exited.join()
    engine = _ClosableEngine("mps")
    Vault._engines_left_loaded.append((engine, exited))
    runner = TaskRunner(name="vault-left-engine-cancelled")
    holding = threading.Event()
    release = threading.Event()

    def hold():
        holding.set()
        release.wait(10)

    # Queued before the runner starts and ahead of the close, so the worker is
    # busy with it when the close is queued.
    runner.submit(GpuCallTask(hold))
    vault = _lifecycle_vault(runner, "mps", started=False)
    try:
        vault.start()
        assert holding.wait(10), "the holding call never reached the GPU worker"
        assert runner.cancel_pending_tasks() == 1
        release.set()
        runner.run_on_gpu_worker(lambda: None, timeout_s=10)

        assert engine.closes == []
        assert Vault._engines_left_loaded == [(engine, exited)]
    finally:
        release.set()
        runner.stop()


class _FailingCloseEngine(_ClosableEngine):
    def close(self):
        super().close()
        raise RuntimeError("the engine would not close")


def test_vault_stop_closes_the_database_even_when_the_engine_close_fails(
    model_releases,
):
    vault = _lifecycle_vault(None, "cpu", started=False)
    vault._engine = _FailingCloseEngine("cpu")
    db = vault.db

    with pytest.raises(RuntimeError, match="would not close"):
        vault.stop()

    assert db.closes == 1
    assert vault.db is None


def test_a_vault_on_metal_will_not_start_beside_a_gpu_worker_still_running(
    model_releases, monkeypatch, fake_torch
):
    """Two GPU workers would use Metal at once; after the wait it refuses."""
    fake_torch(_fake_torch(mps=True))
    monkeypatch.setattr(Vault, "PREVIOUS_METAL_WORKER_WAIT_S", 0.2)
    stuck = threading.Event()
    previous = threading.Thread(
        target=stuck.wait, args=(10,), name="earlier-runner-gpu", daemon=True
    )
    previous.start()
    register_metal_thread(previous)
    runner = TaskRunner(name="vault-refused")
    vault = _lifecycle_vault(runner, "mps", started=False)
    engine, db = vault._engine, vault.db
    try:
        with pytest.raises(RuntimeError, match="earlier-runner-gpu"):
            vault.start()

        assert not runner.is_running(), "a second GPU worker was started"
        assert registered_metal_thread() is previous
        assert vault._work_planner.starts == 0
        # The failed start closes the vault, still without unloading beside it.
        assert db.closes == 1
        assert engine.closes == []
    finally:
        stuck.set()
        previous.join(10)
        runner.stop()


def test_a_vault_on_metal_starts_once_the_previous_gpu_worker_exits(
    monkeypatch, fake_torch
):
    fake_torch(_fake_torch(mps=True))
    monkeypatch.setattr(Vault, "PREVIOUS_METAL_WORKER_WAIT_S", 10.0)
    previous = threading.Thread(
        target=time.sleep, args=(0.3,), name="earlier-runner-gpu", daemon=True
    )
    previous.start()
    register_metal_thread(previous)
    runner = TaskRunner(name="vault-after-wait")
    vault = _lifecycle_vault(runner, "mps", started=False)
    try:
        vault.start()

        assert not previous.is_alive()
        assert runner.is_running()
        assert registered_metal_thread() is runner._gpu_worker_thread
        assert vault._started is True
    finally:
        runner.stop()


def test_a_vault_off_metal_does_not_wait_for_an_earlier_gpu_worker(
    monkeypatch, fake_torch
):
    """A host without Metal starts as it always did."""
    fake_torch(_fake_torch(cuda=True, mps=False))
    monkeypatch.setattr(Vault, "PREVIOUS_METAL_WORKER_WAIT_S", 10.0)
    stuck = threading.Event()
    previous = threading.Thread(
        target=stuck.wait, args=(10,), name="earlier-runner-gpu", daemon=True
    )
    previous.start()
    register_metal_thread(previous)
    runner = TaskRunner(name="vault-cuda")
    vault = _lifecycle_vault(runner, "cuda", started=False)
    try:
        started = time.monotonic()
        vault.start()

        assert time.monotonic() - started < 2.0
        assert runner.is_running()
    finally:
        runner.stop()
        stuck.set()
        previous.join(10)


@pytest.mark.parametrize("engine_device", [None, "cpu"])
def test_a_vault_waits_for_an_earlier_gpu_worker_wherever_metal_exists(
    model_releases, monkeypatch, fake_torch, engine_device
):
    """Not only a vault whose engine is on Metal.

    An engine whose build failed can be built later, and an image plugin run
    before it exists uses Metal on its own; a Mac forced onto the CPU still has
    Metal for a plugin to reach.
    """
    fake_torch(_fake_torch(mps=True))
    monkeypatch.setattr(Vault, "PREVIOUS_METAL_WORKER_WAIT_S", 0.2)
    stuck = threading.Event()
    previous = threading.Thread(
        target=stuck.wait, args=(10,), name="earlier-runner-gpu", daemon=True
    )
    previous.start()
    register_metal_thread(previous)
    runner = TaskRunner(name="vault-no-metal-engine")
    vault = _lifecycle_vault(runner, "cpu", started=False)
    if engine_device is None:
        vault._engine = None
    try:
        with pytest.raises(RuntimeError, match="earlier-runner-gpu"):
            vault.start()

        assert not runner.is_running(), "a second GPU worker was started"
        assert registered_metal_thread() is previous
    finally:
        stuck.set()
        previous.join(10)
        runner.stop()


def test_a_library_switch_waits_for_an_earlier_gpu_worker_before_building_the_engine():
    """Building the engine reads Metal's working set beside that worker."""
    calls = []

    class _Vault:
        def wait_for_previous_metal_worker(self):
            calls.append("wait")

        def ensure_ready(self):
            calls.append("build")

        def start(self):
            calls.append("start")

    library_switch_service._bring_up(_Vault(), "incoming")

    assert calls == ["wait", "build", "start"]

    class _StuckVault(_Vault):
        def wait_for_previous_metal_worker(self):
            calls.append("wait")
            raise RuntimeError("GPU worker 'earlier-runner-gpu' is still running")

    calls.clear()
    with pytest.raises(RuntimeError, match="earlier-runner-gpu"):
        library_switch_service._bring_up(_StuckVault(), "incoming")
    assert calls == ["wait"], "the engine was built beside the earlier worker"


def test_a_vault_that_fails_to_start_stops_its_runner_and_closes_its_database(
    model_releases, caplog
):
    """Left running, the runner keeps its worker registered and the close trips
    the Metal guard before the database is reached."""
    runner = TaskRunner(name="vault-failed-start")
    vault = _lifecycle_vault(runner, "mps", started=False)
    planner = vault._work_planner
    watcher = _RecordingPart(
        start_error=RuntimeError("watcher would not start"), stop_needs_start=True
    )
    vault._ref_folder_watcher = watcher
    engine, db = vault._engine, vault.db
    try:
        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError, match="watcher would not start"):
                vault.start()

        assert not runner.is_running(), "the task runner was left running"
        assert registered_metal_thread() is None
        assert planner.stops == 1, "the work planner was left running"
        assert watcher.stops == 0, "a watcher that never started was stopped"
        assert db.closes == 1
        assert vault.db is None
        assert engine.closes == [threading.current_thread()]
        assert vault._started is False
        assert any("failed to start" in r.getMessage() for r in caplog.records)
    finally:
        runner.stop()


# --------------------------------------------------------------------------- #
# Image plugins run on the GPU worker on Metal
# --------------------------------------------------------------------------- #


class _ThreadProbePlugin(ImagePlugin):
    """Records the thread ``run`` and ``run_video`` ran on, and reports once.

    Each takes *delay_s*.
    """

    name = "thread_probe"
    supports_videos = True

    def __init__(self, delay_s=0.0):
        self.delay_s = delay_s
        self.threads: dict[str, threading.Thread] = {}

    def parameter_schema(self):
        return []

    def run(
        self,
        images,
        parameters=None,
        progress_callback=None,
        error_callback=None,
        captions=None,
    ):
        self.threads["run"] = threading.current_thread()
        time.sleep(self.delay_s)
        self.report_progress(progress_callback, current=1, total=1, message="probed")
        self.report_error(error_callback, index=0, message="probe error")
        return [image.copy() for image in images]

    def run_video(
        self, source_path, parameters=None, progress_callback=None, error_callback=None
    ):
        self.threads["run_video"] = threading.current_thread()
        time.sleep(self.delay_s)
        return b"video", ".mp4"


def _run_probe_plugin(vault, plugin):
    """Run *plugin* over one still and one video; return what the callbacks saw."""
    image = Image.new("RGB", (4, 4))
    loaded = [
        (None, image, "PNG", "still.png"),
        (None, image, "MP4", "clip.mp4"),
    ]
    reported = []
    outputs = _run_plugin(
        types.SimpleNamespace(vault=vault),
        plugin,
        loaded,
        {},
        ["", ""],
        lambda payload: reported.append(("progress", threading.current_thread())),
        lambda payload: reported.append(("error", threading.current_thread())),
    )
    assert len(outputs) == 2
    assert outputs[1] == (b"video", ".mp4")
    return reported


def test_an_image_plugin_runs_on_the_gpu_worker_on_metal(gpu_runner):
    """An upscaler built on torch would otherwise use Metal off the worker."""
    worker = _gpu_worker_of(gpu_runner)
    plugin = _ThreadProbePlugin()

    reported = _run_probe_plugin(_routing_vault(gpu_runner, "mps"), plugin)

    assert plugin.threads == {"run": worker, "run_video": worker}
    assert reported == [("progress", worker), ("error", worker)]


@pytest.mark.parametrize("metal", [True, False])
def test_an_image_plugin_with_no_engine_runs_on_the_gpu_worker_where_metal_exists(
    gpu_runner, fake_torch, metal
):
    """A vault whose engine build failed has no device to read.

    The plugin picks its own device, Metal on a Mac, so the plugin has to
    reach the worker there; a host without Metal keeps it inline.
    """
    fake_torch(_fake_torch(mps=metal))
    worker = _gpu_worker_of(gpu_runner)
    vault = _routing_vault(gpu_runner, "mps")
    vault._engine = None
    plugin = _ThreadProbePlugin()

    _run_probe_plugin(vault, plugin)

    expected = worker if metal else threading.current_thread()
    assert plugin.threads == {"run": expected, "run_video": expected}


@pytest.mark.parametrize("device", ["cuda", "cpu"])
def test_an_image_plugin_stays_on_the_calling_thread_off_metal(gpu_runner, device):
    plugin = _ThreadProbePlugin()
    here = threading.current_thread()

    reported = _run_probe_plugin(_routing_vault(gpu_runner, device), plugin)

    assert plugin.threads == {"run": here, "run_video": here}
    assert reported == [("progress", here), ("error", here)]


class _OutOfMemoryPlugin(_ThreadProbePlugin):
    """Reports progress, then runs out of GPU memory, on every run of either kind."""

    name = "out_of_memory"

    def __init__(self):
        super().__init__()
        self.runs = 0

    def run(
        self,
        images,
        parameters=None,
        progress_callback=None,
        error_callback=None,
        captions=None,
    ):
        self.runs += 1
        self.report_progress(progress_callback, current=1, total=2, message="half")
        raise RuntimeError(MPS_OOM_MESSAGE)

    def run_video(
        self, source_path, parameters=None, progress_callback=None, error_callback=None
    ):
        return self.run([source_path], parameters, progress_callback, error_callback)


#: One input of each kind the plugin service routes separately.
_PLUGIN_INPUTS = [
    pytest.param(("PNG", "still.png"), id="still"),
    pytest.param(("MP4", "clip.mp4"), id="video"),
]


@pytest.mark.parametrize("source", _PLUGIN_INPUTS)
def test_an_image_plugin_run_is_not_retried_on_a_gpu_oom(
    gpu_runner, monkeypatch, source
):
    """A retry would run the whole plugin again and report its progress twice."""
    monkeypatch.setattr(TaskRunner, "VRAM_OOM_RETRY_PAUSE_S", 0.0)
    plugin = _OutOfMemoryPlugin()
    reported = []

    with pytest.raises(RuntimeError, match="MPS backend out of memory"):
        _run_plugin(
            types.SimpleNamespace(vault=_routing_vault(gpu_runner, "mps")),
            plugin,
            [(None, Image.new("RGB", (4, 4)), *source)],
            {},
            [""],
            reported.append,
            reported.append,
        )

    assert plugin.runs == 1, f"the plugin ran {plugin.runs} times"
    assert len(reported) == 1


def test_an_image_plugin_run_is_not_cut_off_by_the_call_timeout(
    gpu_runner, monkeypatch
):
    """A plugin over many pictures outlasts the timeout a query encode gets.

    The still and the video each take longer than the timeout, so either call
    routed with one fails here.
    """
    monkeypatch.setattr(TaskRunner, "GPU_CALL_TIMEOUT_S", 0.2)
    vault = _routing_vault(gpu_runner, "mps")
    # Positive control: the same wait through run_inference does time out.
    with pytest.raises(TimeoutError):
        vault.run_inference(time.sleep, 0.6)
    gpu_runner.run_on_gpu_worker(lambda: None, timeout_s=10)
    plugin = _ThreadProbePlugin(delay_s=0.6)

    reported = _run_probe_plugin(vault, plugin)

    assert set(plugin.threads) == {"run", "run_video"}
    assert [kind for kind, _thread in reported] == ["progress", "error"]
