"""One answer to "what is this machine's accelerator, and how do I use it".

Every device decision in PixlStash routes through this module. Before it, the
answer was spelled ``torch.cuda.is_available()`` in seventeen places, which made
"has a GPU" and "has CUDA" the same sentence - so an Apple Silicon machine, whose
GPU is real and roughly twenty times faster than its CPU at tagging, was told it
had no GPU at all.

The rule this module exists to enforce: **no code path may infer "CPU" from "not
CUDA"**. CUDA is one accelerator. MPS is another. A third will be a change to
:data:`_ACCELERATOR_PROBES` and nothing else.

The corollary matters more than the speed. Every safety net CUDA has - spill to
CPU on out-of-memory, allocator cache release, memory-aware batch sizing,
start-up verification - has to exist for any accelerator this module is willing
to name, because a fast tagger that silently drops a batch is worse than a slow
one that does not. So the OOM classifier (:func:`is_device_error`), the cache
flush (:func:`empty_accelerator_cache`) and the memory budget
(:func:`accelerator_total_memory_mb`) all live here next to the detection, and
are answered per accelerator rather than for CUDA alone.

``torch`` is deliberately *not* imported at module scope. This module is reached
from ``server.py``, ``startup_checks.py`` and ``config_service.py``, all of which
sit on the API server's import path where importing torch costs seconds of
start-up for a question that is usually "is there an accelerator" and nothing
more. :func:`_torch` imports it on demand; the functions that can only have work
to do in a process that already loaded torch read :data:`sys.modules` instead and
never import it at all. This mirrors ``pixlstash.utils.vram_utils``, which
documents the same reasoning.
"""

import os
import platform
import sys
from typing import Any

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

#: Distinguishes "no handle was passed, go and find torch" from "this caller
#: has already established there is no torch". Without it the two cases both
#: look like ``None`` and a caller that legitimately means the second gets a
#: real import - which is how the simulated-host seam in
#: ``tests/test_rocm_device_check.py`` ended up probing the real machine.
_UNSET: Any = object()


def _resolve_torch(torch_module: Any = _UNSET, allow_import: bool = True):
    """Return the torch handle these probes should use.

    The injection seam. ``startup_checks`` caches its own torch behind
    ``_torch_mod`` so that a test can simulate a CUDA, ROCm or CPU-only host on
    hardware that is none of them, and it passes that handle down here. Without
    a way in, every probe would answer for the real machine and CI's CPU-only
    runners could not verify the CUDA path at all.

    Args:
        torch_module: An explicit handle, including ``None`` to mean "there is
            no torch". Omitted entirely means "find it".
        allow_import: When ``False``, only an already-imported torch is used -
            the cheap path for callers who cannot have device memory to ask
            about if torch was never loaded.

    Returns:
        A torch module, or ``None``.
    """
    if torch_module is not _UNSET:
        return torch_module
    return _torch() if allow_import else sys.modules.get("torch")


#: The CPU. Not an accelerator; the answer when there is none.
CPU = "cpu"

#: NVIDIA (and, through HIP, AMD ROCm - torch presents ROCm as ``cuda``).
CUDA = "cuda"

#: Apple Silicon's Metal Performance Shaders backend.
MPS = "mps"

#: Accelerators in preference order, each with the torch predicate that says
#: whether this host has it. Adding a fourth backend is an entry here plus a
#: dtype/cache/provider answer in the three functions that switch on the name -
#: which is the whole point of routing every caller through this module.
_ACCELERATOR_PROBES: tuple[tuple[str, str], ...] = (
    (CUDA, "cuda"),
    (MPS, "mps"),
)

#: Device strings the config's ``default_device``, or the
#: ``PIXLSTASH_DEFAULT_DEVICE`` override, may name. Kept as one set so the
#: config validator and the environment override cannot drift apart - they did,
#: and for a while a value the validator accepted was rejected by the override.
VALID_DEVICE_SETTINGS = frozenset({CPU, CUDA, MPS, "metal", "gpu", "auto"})

#: Spellings that are not devices. ``metal`` is what the desktop shell calls
#: Apple Silicon acceleration in its own UI (``electron/src/config.ts``), so an
#: owner who reads that label and types it into the config gets what they meant.
_DEVICE_ALIASES = {"metal": MPS}

#: Settings that name no particular device and are resolved against the host.
#: ``""`` is here but deliberately **not** in :data:`VALID_DEVICE_SETTINGS`: a
#: blank value reaching :func:`resolve_device` at runtime is a caller passing
#: nothing, which the host answers, while a blank ``default_device`` in the
#: config file is a mistake the validator should name rather than silently
#: resolve. The two sets differ by exactly that one member, on purpose.
#: ``gpu`` is one of them **on purpose**: it predates CUDA being named
#: explicitly, and reading it as "cuda" is the same mistake this module exists
#: to remove - on a Mac, ``default_device=gpu`` means the GPU the Mac has.
#: ``gpu`` and ``auto`` differ only in what a host with no accelerator does, and
#: that is the start-up check's decision, not this function's.
_HOST_RESOLVED_SETTINGS = frozenset({"gpu", "auto", ""})

#: Words that make an error message a *device* one, per accelerator. Used by
#: :func:`is_device_error` and by ``vram_utils.is_vram_oom``: the bare phrase
#: "out of memory" is ambiguous (``sqlite3.OperationalError`` says it too), so a
#: device word has to appear with it. MPS is here because its allocator says
#: "MPS backend out of memory" and matches none of CUDA's vocabulary - which is
#: exactly how a measured MPS OOM reached the logs with no fallback behind it.
DEVICE_ERROR_WORDS: dict[str, tuple[str, ...]] = {
    CUDA: ("cuda", "cudnn", "cublas", "hip", "vram", "gpu"),
    MPS: ("mps", "metal"),
}

#: Every device word, for callers classifying an error without knowing which
#: accelerator produced it.
ALL_DEVICE_ERROR_WORDS: tuple[str, ...] = tuple(
    sorted({word for words in DEVICE_ERROR_WORDS.values() for word in words})
)


def _torch():
    """Import torch, or return ``None`` if it cannot be reached.

    Deliberately catches more than ``ImportError``: a torch that is installed
    but cannot load its shared libraries raises ``OSError``, and a partial
    install can raise almost anything. All of them mean the same thing here -
    no accelerator can be proven - and all of them are logged rather than
    swallowed so the cause survives for whoever reads the log.

    Returns:
        The ``torch`` module, or ``None``.
    """
    torch = sys.modules.get("torch")
    if torch is not None:
        return torch
    try:
        import torch  # noqa: PLC0415 - see the module docstring

        return torch
    except Exception as exc:
        logger.warning(
            "Could not import torch to detect the accelerator (%s: %s); "
            "treating this host as CPU-only. If it has a GPU, this is why "
            "nothing is using it.",
            type(exc).__name__,
            exc,
        )
        return None


def _probe(torch, attribute: str) -> bool:
    """Return whether the backend named *attribute* is available.

    ``torch.cuda.is_available()`` and ``torch.backends.mps.is_available()`` are
    spelled differently, so the probe tries both shapes. Either can raise on a
    broken install (an unsupported ROCm gfx arch is the known case), which is
    logged and read as "not available" rather than crashing detection.

    Args:
        torch: The imported torch module.
        attribute: Backend name, e.g. ``"cuda"`` or ``"mps"``.

    Returns:
        ``True`` when torch says this host can use that backend.
    """
    try:
        backend = getattr(getattr(torch, "backends", None), attribute, None)
        if backend is not None and hasattr(backend, "is_available"):
            return bool(backend.is_available())
        module = getattr(torch, attribute, None)
        if module is not None and hasattr(module, "is_available"):
            return bool(module.is_available())
    except Exception as exc:
        logger.warning(
            "Probing torch for the %s backend failed (%s: %s); reading it as "
            "unavailable and continuing with the remaining accelerators.",
            attribute,
            type(exc).__name__,
            exc,
        )
    return False


def available_accelerator(torch_module: Any = _UNSET) -> str | None:
    """Return this host's accelerator, or ``None`` when it has none.

    Args:
        torch_module: Injected torch handle; see :func:`_resolve_torch`.

    Returns:
        :data:`CUDA`, :data:`MPS`, or ``None``. The first match in
        :data:`_ACCELERATOR_PROBES` wins, so a machine with both would use CUDA.
    """
    torch = _resolve_torch(torch_module)
    if torch is None:
        return None
    for name, attribute in _ACCELERATOR_PROBES:
        if _probe(torch, attribute):
            return name
    return None


def normalise_device(device) -> str:
    """Reduce any device spelling to a bare backend name.

    Accepts what the codebase actually passes around: a string (``"cuda"``,
    ``"cuda:0"``), a ``torch.device``, or ``None``.

    Args:
        device: Device string, ``torch.device``, or ``None``.

    Returns:
        ``"cuda"``, ``"mps"``, ``"cpu"``, or the lower-cased name given.
    """
    if device is None:
        return CPU
    device_type = getattr(device, "type", None)
    text = str(device_type if device_type is not None else device).strip().lower()
    name = text.split(":", 1)[0]
    return _DEVICE_ALIASES.get(name, name) or CPU


def resolve_device(
    requested=None, force_cpu: bool = False, torch_module: Any = _UNSET
) -> str:
    """Return the device inference should run on.

    This is the single place the question is answered. ``--force-cpu`` wins over
    everything, unconditionally and on every accelerator, because CI depends on
    it: the gate's runners have no GPU and a flag that only suppressed CUDA
    would quietly start using Metal on a developer's Mac.

    Args:
        requested: What the caller or the config asked for. ``None``, ``""``,
            ``"auto"`` and ``"gpu"`` all mean "whatever this host has"; a named
            backend is returned as given, so a caller that asks for a device it
            does not have still gets its own error rather than a silent
            downgrade.
        force_cpu: When ``True``, the answer is :data:`CPU`.
        torch_module: Injected torch handle; see :func:`_resolve_torch`.

    Returns:
        ``"cuda"``, ``"mps"`` or ``"cpu"``.
    """
    if force_cpu:
        return CPU
    # Blank means "unspecified", which is a question for the host - not a
    # request for the CPU. It cannot go through `normalise_device`, whose
    # trailing `or CPU` answers `None` and would silently turn an empty config
    # value into an explicit CPU choice. That is the exact silent downgrade this
    # module exists to stop, and it left the `""` member of
    # `_HOST_RESOLVED_SETTINGS` unreachable.
    if requested is None or (isinstance(requested, str) and not requested.strip()):
        name = ""
    else:
        name = normalise_device(requested)
    if name in _HOST_RESOLVED_SETTINGS:
        return available_accelerator(torch_module) or CPU
    return name


def is_accelerated(device) -> bool:
    """Return whether *device* is an accelerator rather than the CPU.

    The predicate the codebase used to spell ``device == "cuda"``, which is the
    exact expression that made every non-CUDA GPU invisible.

    Args:
        device: Device string or ``torch.device``.

    Returns:
        ``True`` for a known accelerator.
    """
    return normalise_device(device) in {name for name, _ in _ACCELERATOR_PROBES}


def supports_fp16(device) -> bool:
    """Return whether half precision is worth using on *device*.

    CUDA: long-standing behaviour, unchanged. MPS: adopted on measurement, not
    on principle. Over 3 fresh processes per dtype in alternating order, batch
    8, fp16 ran at 208 ms/image against fp32's 266 (**1.28x**) and held 1319 MB
    of device memory against 2347 MB (**-44 %**, byte-identical across every
    replicate). Accuracy was checked on raw scores rather than on thresholded
    tags, because confidences are stored and feed the anomaly penalty: over 291
    (image, label) pairs the largest fp16-vs-fp32 difference was 0.0019 and not
    one label moved as much as 0.01, so the thresholded tags are identical too.
    Both halves matter, but on a unified-memory machine the **headroom** is the
    one that decides whether the batch a machine can afford is the batch that
    OOMs it. CPU stays fp32; half precision there is slower, not faster.

    Args:
        device: Device string or ``torch.device``.

    Returns:
        ``True`` when the caller should run the model in fp16.
    """
    return normalise_device(device) in (CUDA, MPS)


def empty_accelerator_cache(device=None) -> bool:
    """Flush the accelerator allocator's cache back to the driver.

    ``torch`` is looked up in :data:`sys.modules` rather than imported: a
    process that never imported it cannot have allocated device memory, so
    there is nothing to flush, and importing torch here purely to discover that
    would cost seconds on paths (teardown, the idle sweep, most of the test
    suite) whose caller usually never touched a model.

    Args:
        device: Flush only this accelerator's cache. ``None`` flushes whichever
            accelerator this host has, which is what every teardown path wants.

    Returns:
        ``True`` if a cache was flushed; ``False`` when torch is not loaded or
        the host has no accelerator (callers use this to skip their own cache
        bookkeeping).
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    name = normalise_device(device) if device is not None else None
    flushed = False
    for accelerator, attribute in _ACCELERATOR_PROBES:
        if name is not None and name != accelerator:
            continue
        if not _probe(torch, attribute):
            continue
        module = getattr(torch, attribute, None)
        empty_cache = getattr(module, "empty_cache", None)
        if empty_cache is None:
            continue
        try:
            empty_cache()
            flushed = True
        except Exception as exc:
            # Best effort by definition - the caller is already recovering from
            # something. Logged with the backend name so a driver that refuses
            # to release memory is diagnosable rather than invisible.
            logger.warning(
                "Releasing the %s allocator cache failed (%s: %s); continuing "
                "without it, so the next allocation may still find the device "
                "full.",
                accelerator,
                type(exc).__name__,
                exc,
            )
    return flushed


#: Share of physical RAM that Metal publishes as its recommended working set.
#: Used only when torch is not loaded and cannot be asked directly:
#: ``torch.mps.recommended_max_memory()`` measured 5461 MiB on an 8 GiB machine,
#: which is this ratio.
_APPLE_UNIFIED_WORKING_SET_SHARE = 2.0 / 3.0


def is_apple_silicon() -> bool:
    """Return whether this is an Apple Silicon Mac.

    A platform test, not a capability test, and used only where the question is
    genuinely about the *memory model* - unified memory rather than a card.
    Never use it to decide whether to accelerate: that is
    :func:`available_accelerator`, which asks torch, so the same code path works
    on a CUDA box, a CPU-only runner and a Mac.

    Returns:
        ``True`` on arm64 macOS.
    """
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def physical_memory_mb() -> int:
    """Return this machine's installed RAM in MiB, or ``0`` when unreadable.

    ``sysconf`` rather than ``psutil``, even though psutil is a dependency: this
    module is imported by ``server.py``, ``startup_checks.py`` and
    ``config_service.py``, and two POSIX constants answer the question without
    putting another package on that path. The two agree on macOS anyway - both
    end up reading ``hw.memsize``.

    This is **physical RAM**, deliberately distinct from
    :func:`accelerator_total_memory_mb`. On unified memory the two are related
    but not interchangeable: the accelerator figure is what Metal will *hand
    out*, this is what the machine *has*, and the default memory budget is
    argued from the second because the OS and everything else the owner is
    running spend out of it too.

    Returns:
        Installed RAM in MiB, or ``0`` where the platform will not say
        (``os.sysconf`` does not exist on Windows) - callers read ``0`` as
        "unknown", never as "no memory".
    """
    try:
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) // (1024**2)
    except (AttributeError, ValueError, OSError) as exc:
        logger.warning(
            "Could not read physical memory (%s: %s); callers that size a "
            "default against RAM will fall back rather than guess.",
            type(exc).__name__,
            exc,
        )
        return 0


def accelerator_total_memory_mb(device=None, torch_module: Any = _UNSET) -> int:
    """Return the device memory a batch may be sized against, in MiB.

    The two accelerators mean different things by "total", and conflating them
    is how a hardcoded batch size ends up wrong in both directions:

    * CUDA reports installed VRAM, which is the card's and nobody else's.
    * MPS reports ``torch.mps.recommended_max_memory()`` - Metal's
      ``recommendedMaxWorkingSetSize``, roughly two thirds of system RAM
      (5461 MiB on an 8 GB machine) - because on Apple Silicon the GPU reads
      the same RAM as everything else. It is a recommendation about a shared
      pool, not a private allocation, which is exactly why it has to be read
      from the driver rather than assumed.

    ``torch`` is read from :data:`sys.modules` and never imported: this is
    reached from config validation long before any model loads, and paying
    seconds of torch import there to refine a figure would be a bad trade. On
    Apple Silicon without torch, a share of physical RAM is the same quantity
    computed the cheap way; there is no such shortcut for a card, so CUDA
    without torch answers 0.

    Args:
        device: Accelerator to measure. ``None`` measures whichever this host
            has, without importing torch to find out.
        torch_module: Injected torch handle; see :func:`_resolve_torch`.

    Returns:
        MiB available to the accelerator, or ``0`` when unknown - callers read
        ``0`` as "no budget", never as "no memory".
    """
    name = normalise_device(device) if device is not None else None
    if name is None:
        name = MPS if is_apple_silicon() else CUDA
    if not is_accelerated(name):
        return 0
    torch = _resolve_torch(torch_module, allow_import=False)
    try:
        if name == MPS:
            recommended = getattr(
                getattr(torch, "mps", None), "recommended_max_memory", None
            )
            if recommended is not None:
                measured = int(recommended())
                if measured > 0:
                    return measured // (1024**2)
            if not is_apple_silicon():
                return 0
            return int(physical_memory_mb() * _APPLE_UNIFIED_WORKING_SET_SHARE)
        if name == CUDA and torch is not None:
            total = 0
            for index in range(int(torch.cuda.device_count() or 0)):
                properties = torch.cuda.get_device_properties(index)
                total += int(getattr(properties, "total_memory", 0) or 0)
            return total // (1024**2)
    except Exception as exc:
        logger.warning(
            "Could not read %s total memory (%s: %s); treating the budget as "
            "unknown, so memory-aware batch sizing will not apply.",
            name,
            type(exc).__name__,
            exc,
        )
    return 0


def device_display_name(device) -> str:
    """Return what to call *device* in a log line or a start-up note.

    Args:
        device: Device string or ``torch.device``.

    Returns:
        ``"CUDA"``, ``"Metal (MPS)"`` or ``"CPU"``.
    """
    name = normalise_device(device)
    if name == CUDA:
        return "CUDA"
    if name == MPS:
        return "Metal (MPS)"
    return "CPU"


def is_device_error(error: BaseException, device=None) -> bool:
    """Return whether *error* came from the accelerator rather than the model.

    The check the CUDA paths spelled inline as ``"CUDA error" in str(exc)``,
    which on MPS matches nothing: its failures say "MPS backend", so an inline
    CUDA-worded check reads a genuine device failure as a model bug and gives
    up instead of retrying on the CPU.

    Args:
        error: The exception to classify.
        device: Restrict the vocabulary to one accelerator's words. ``None``
            accepts any accelerator's, which is what a caller that has already
            fallen back to the CPU wants.

    Returns:
        ``True`` when the accelerator is the plausible cause, which callers
        treat as "retry this on the CPU".
    """
    name = normalise_device(device) if device is not None else None
    if name is not None and not is_accelerated(name):
        return False
    words = DEVICE_ERROR_WORDS.get(name, ALL_DEVICE_ERROR_WORDS)
    torch = sys.modules.get("torch")
    if torch is not None:
        # PyTorch's typed OOM deliberately does not promise a backend name in
        # its message, so type identity is checked first and separately.
        oom_type = getattr(torch, "OutOfMemoryError", None)
        if isinstance(oom_type, type) and isinstance(error, oom_type):
            return True
        cuda_error = getattr(getattr(torch, "cuda", None), "CudaError", None)
        if (
            name in (None, CUDA)
            and isinstance(cuda_error, type)
            and isinstance(error, cuda_error)
        ):
            return True
    message = str(error).lower()
    if "not compatible" in message:
        # A dtype the backend will not run: not an OOM, same remedy.
        return True
    return any(word in message for word in words)


def coreml_cache_dir() -> str:
    """Return the directory CoreML compiles its cached models into.

    Without a cache directory ONNX Runtime recompiles the model on every
    session it builds and leaves the compiled bundle in a per-process temporary
    directory. Caching turns a 3.77 s cold session into a 1.64 s warm one, and
    the saving persists across processes - which matters more than "once per
    boot" suggests, because ``model_lifecycle`` idle-unloads the WD14 session
    and something has to rebuild it every time tagging resumes.

    It is **not free**: WD14's compiled form measured **754 MB** against a
    377 MB ``model.onnx``, roughly twice the source model, written as two
    partitioned subgraphs. That is the reason this is the platformdirs *cache*
    directory rather than the data directory - a user can delete it and lose
    nothing but the next start-up's 2 s, and a disk-space sweep knows to look
    there. Anything that wants caching has to be worth ~2x its own size.

    Returns:
        Absolute path; the caller creates it.
    """
    from platformdirs import user_cache_dir  # noqa: PLC0415 - see module docstring

    return os.path.join(user_cache_dir("pixlstash"), "coreml-cache")


def onnx_execution_providers(
    device,
    available_providers,
    cuda_options: dict | None = None,
    coreml_cache: str | None = None,
) -> list:
    """Return the ONNX Runtime provider list for *device*.

    Takes the available-provider list rather than calling
    ``ort.get_available_providers()`` itself, so the ladder can be tested on a
    machine that has none of these providers - which is the only way a CUDA
    branch gets exercised on a Mac, or a CoreML branch on a Linux runner.

    The CoreML options are not defaults and are not negotiable:

    * ``ModelFormat: MLProgram``. The provider's own default is
      ``NeuralNetwork``, which fails outright on ``wd-convnext-tagger-v3``
      ("error code: -1") rather than falling back.
    * ``MLComputeUnits: CPUAndGPU``. Measured against ``ALL`` at 182 vs 183
      ms/image - identical throughput - for under half the compile cost.
      ``ALL`` adds the Neural Engine and buys nothing here.

    Args:
        device: Device string or ``torch.device``. ``"cpu"`` pins the CPU
            provider, which is how ``--force-cpu`` reaches ONNX Runtime.
        available_providers: What this build advertises, from
            ``ort.get_available_providers()``.
        cuda_options: Provider options for ``CUDAExecutionProvider``, from
            ``VramBudget.ort_cuda_provider_options``. Ignored off CUDA.
        coreml_cache: Directory for CoreML's compiled-model cache.

    Returns:
        A ``providers`` list of names and ``(name, options)`` tuples.
    """
    name = normalise_device(device)
    if not is_accelerated(name):
        return ["CPUExecutionProvider"]
    available = set(available_providers or ())
    if name == MPS:
        if "CoreMLExecutionProvider" in available:
            options: dict[str, object] = {
                "ModelFormat": "MLProgram",
                "MLComputeUnits": "CPUAndGPU",
            }
            if coreml_cache:
                options["ModelCacheDirectory"] = coreml_cache
            return [("CoreMLExecutionProvider", options), "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]
    # Every accelerated branch ends in CPUExecutionProvider. ONNX Runtime
    # appends it implicitly anyway, so this changes no behaviour - it makes the
    # fallback visible at the call site instead of leaving a reader of the CUDA
    # branch to wonder what happens to an op the provider cannot run. The CUDA
    # and ROCm branches were the ones written without it.
    #
    # OpenVINO first on a CUDA-named device is long-standing behaviour: an Intel
    # build that advertises it has no CUDA provider to prefer over it.
    if "OpenVINOExecutionProvider" in available:
        return [
            ("OpenVINOExecutionProvider", {"device_type": "GPU", "precision": "FP32"}),
            "CPUExecutionProvider",
        ]
    if "CUDAExecutionProvider" in available:
        return [
            ("CUDAExecutionProvider", dict(cuda_options or {})),
            "CPUExecutionProvider",
        ]
    if "ROCMExecutionProvider" in available:
        return [("ROCMExecutionProvider", {}), "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


#: torch's own default MPS high-water mark, as a multiple of Metal's
#: recommended working set. It is deliberately *above* the recommendation, and
#: on an 8 GiB machine 1.7x lands above physical RAM.
MPS_DEFAULT_MEMORY_FRACTION = 1.7


def clamp_accelerator_memory(device, budget_mb: int | None) -> bool:
    """Hold the accelerator's allocator to *budget_mb*, where it can be held.

    This is a correctness measure before it is a tidiness one. Left on its
    default, torch's MPS allocator will keep handing out memory up to
    :data:`MPS_DEFAULT_MEMORY_FRACTION` of Metal's recommended working set -
    which on an 8 GB machine is more memory than the machine has - and the
    failure that eventually arrives is the *driver's*,
    ``kIOGPUCommandBufferCallbackErrorOutOfMemory``, which is not raised as a
    catchable Python exception and takes the batch with it. Clamped to the
    recommendation, the allocator raises a plain ``RuntimeError`` ("MPS backend
    out of memory") first, which :func:`is_device_error` classifies and the
    caller spills to the CPU. Measured: after that catch, CPU work completes
    *and* MPS is still usable in the same process, so the spill is a real
    recovery rather than a nominal one.

    Enforcement is approximate - torch reserves in coarse chunks and was
    observed holding 1024 MiB against a 273 MiB ceiling before it raised - so
    this is a backstop and never an input to batch sizing. The batch is sized
    from measured per-image coefficients; this only guarantees that being wrong
    about them is recoverable.

    CUDA is deliberately left alone. ``torch.cuda.set_per_process_memory_fraction``
    exists, but CUDA's ceiling is already enforced through the ORT arena limits
    and the VRAM-budget batch caps, its allocator already raises a catchable
    typed OOM, and narrowing it is a behaviour change on hardware this was not
    measured on.

    Args:
        device: Device string or ``torch.device``.
        budget_mb: Ceiling in MiB, or ``None`` to restore torch's default.

    Returns:
        ``True`` when a clamp was applied or restored.
    """
    if normalise_device(device) != MPS:
        return False
    torch = sys.modules.get("torch")
    set_fraction = getattr(
        getattr(torch, "mps", None), "set_per_process_memory_fraction", None
    )
    if set_fraction is None:
        return False
    try:
        if budget_mb is None:
            set_fraction(MPS_DEFAULT_MEMORY_FRACTION)
            return True
        recommended_mb = accelerator_total_memory_mb(MPS)
        if recommended_mb <= 0:
            return False
        fraction = min(MPS_DEFAULT_MEMORY_FRACTION, budget_mb / recommended_mb)
        if fraction <= 0:
            return False
        set_fraction(fraction)
        logger.info(
            "Metal allocator held to %d MiB (%.2f of the %d MiB recommended "
            "working set), so an over-large batch raises a catchable error and "
            "spills to the CPU instead of failing in the driver.",
            int(recommended_mb * fraction),
            fraction,
            recommended_mb,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Could not set the MPS memory fraction (%s: %s); the allocator "
            "keeps torch's default high-water mark, so an over-large batch may "
            "fail in the driver rather than raising.",
            type(exc).__name__,
            exc,
        )
        return False
