"""Apple Metal (MPS) support, and the device handling around it on every platform.

Stand-ins replace torch's device probes and the models, so these run on any
host. The tests that need a real Apple Metal GPU are marked and skip without one.
"""

import contextlib
import gc
import logging
import re
import sys
import threading
import types
import weakref

import numpy as np
import pytest
from PIL import Image

import pixlstash.inference.model_lifecycle as model_lifecycle_module
from pixlstash.inference.model_lifecycle import ModelLifecycleManager
from pixlstash.task_runner import (
    TaskRunner,
)
from pixlstash.tasks.base_task import (
    BaseTask,
    QueueType,
    TaskInterruptedError,
    TaskStatus,
)
from pixlstash.utils.device_utils import (
    ACCELERATORS,
    detect_device,
    empty_device_cache,
    is_accelerator,
)
from pixlstash.utils.vram_utils import _DEVICE_FAULTS, is_device_error, is_vram_oom

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


class _CyclicModel:
    """A stand-in model in a reference cycle, as the loaded LLaVA model is."""

    def __init__(self):
        self.self_reference = self


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
        "empty_cuda_cache",
        lambda: alive_at_flush.append(model_ref() is not None) or True,
    )

    with _only_explicit_collections():
        getattr(ModelLifecycleManager(device="mps"), unload)(clip_service=service)

    assert service.model is None
    assert alive_at_flush == [False], (
        f"{unload} flushed the device cache while the unloaded model was alive"
    )


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
