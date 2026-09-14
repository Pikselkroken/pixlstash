"""Apple Silicon: that PixlStash reaches the GPU, and survives running out of it.

The bug these guard is not "MPS is slow", it is **"MPS is invisible"**. Every
device decision in the codebase was spelled ``torch.cuda.is_available()``, which
makes "has a GPU" and "has CUDA" the same sentence, so a Mac was told it had no
GPU and ran the default tagger on the CPU at 5,939 ms per image instead of 251.

Two halves, and the second is the one worth the file:

* **Reaching it.** ``StartupChecks._check_device_and_vram`` is the real gate -
  not ``InferenceEngine.create``. Forcing CPU there sets ``forced_cpu``, which
  ``Server.build_vault`` turns into ``force_cpu=True``, which pins the device
  before any engine-side probe runs. A CUDA-only check here made every other
  accelerator change dead code, which is exactly what it did on this hardware.
* **Surviving it.** Unified memory is shared with the OS, torch's default MPS
  high-water mark is above physical RAM on an 8 GB machine, and an MPS OOM says
  none of CUDA's words - so every "free memory and retry on the CPU" path in
  the codebase was inert on a Mac. A fast tagger that silently drops a batch is
  worse than a slow one that does not.

The host is simulated throughout, the same way ``test_rocm_device_check.py``
simulates ROCm: CI's runners are CPU-only Linux, and a test that only ran on an
Apple machine would guard nothing on the branch that has to stay green.
"""

import logging
import types

import pytest

import pixlstash.startup_checks as sc
from pixlstash.inference.engine import (
    _MAX_CONCURRENT_CPU,
    _MAX_CONCURRENT_GPU,
    _MAX_CONCURRENT_MPS,
    InferenceEngine,
)
from pixlstash.inference.vram_budget import (
    PIXLSTASH_TAGGER_CUDA_BASE_MB,
    PIXLSTASH_TAGGER_CUDA_PER_ITEM_MB,
    PIXLSTASH_TAGGER_MPS_BASE_MB,
    PIXLSTASH_TAGGER_MPS_PER_ITEM_FP16_MB,
    PIXLSTASH_TAGGER_MPS_PER_ITEM_MB,
    VramBudget,
)
from pixlstash.inference.workflows.tagging import TaggingWorkflow
import pixlstash.tagger_plugins.joycaption as jc
import pixlstash.utils.system_utils as su
from pixlstash.startup_checks import StartupCheckOutcome, StartupChecks
from pixlstash.utils import accelerator
from pixlstash.utils.accelerator import (
    CPU,
    CUDA,
    MPS,
    VALID_DEVICE_SETTINGS,
    available_accelerator,
    normalise_device,
    onnx_execution_providers,
    resolve_device,
)
from pixlstash.utils.vram_utils import is_vram_oom, vram_limited_batch_cap

#: Metal's recommended working set on the 8 GB machine every figure in this
#: change was measured on. Two thirds of physical RAM, which is the ratio the
#: driver publishes rather than one we chose.
RECOMMENDED_WORKING_SET_BYTES = 5461 * 1024**2

#: The two strings Apple actually produces, captured from a reproduced OOM at
#: batch 32. Neither contains "cuda" or "gpu", which is the whole reason
#: ``is_vram_oom`` could not see them.
MPS_ALLOCATOR_OOM = (
    "MPS backend out of memory (MPS allocated: 8.00 GiB, other allocations: "
    "1.02 GiB, max allowed: 9.07 GiB). Tried to allocate 512.00 MiB on private "
    "pool."
)
METAL_DRIVER_OOM = (
    "Error Domain=MTLCommandBufferErrorDomain Code=8 "
    'Caused GPU Timeout Error (kIOGPUCommandBufferCallbackErrorOutOfMemory)"'
)


def _apple_torch(*, mps=True, recommended=RECOMMENDED_WORKING_SET_BYTES, allocated=0):
    """Build a fake torch that looks like an Apple Silicon build.

    Shaped like the real thing rather than like the probe: ``torch.backends.mps``
    carries ``is_available``, while CUDA's lives on ``torch.cuda``. Getting that
    wrong would let a probe pass here that cannot pass on real torch.
    """
    backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: mps)
    )
    return types.SimpleNamespace(
        version=types.SimpleNamespace(hip=None, cuda=None),
        backends=backends,
        cuda=types.SimpleNamespace(is_available=lambda: False),
        mps=types.SimpleNamespace(
            recommended_max_memory=lambda: recommended,
            driver_allocated_memory=lambda: allocated,
            empty_cache=lambda: None,
        ),
    )


def _cuda_torch():
    """A fake CUDA build, for the "CUDA still wins" controls."""
    return types.SimpleNamespace(
        version=types.SimpleNamespace(hip=None, cuda="12.8"),
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: False)
        ),
        cuda=types.SimpleNamespace(
            is_available=lambda: True,
            mem_get_info=lambda: (8_000 * 1024**2, 16_000 * 1024**2),
            get_device_capability=lambda i=0: (9, 0),
            get_device_name=lambda i=0: "Fake GPU",
        ),
    )


def _ort(providers):
    return types.SimpleNamespace(get_available_providers=lambda: list(providers))


@pytest.fixture
def patch_runtime(monkeypatch):
    """Seed ``startup_checks``' cached torch/ort handles with fakes.

    Same seam as the ROCm suite: the caches are what stop the accessors ever
    attempting the real import, so seeding them both substitutes the fake and
    keeps the test off this machine's actual hardware.
    """

    def apply(torch_mod, ort_mod):
        monkeypatch.setattr(sc, "_torch_mod", torch_mod)
        monkeypatch.setattr(sc, "_ort_mod", ort_mod)

    return apply


def _checks(device, config_path="/tmp/server_config.json"):
    return StartupChecks(
        {"default_device": device}, config_path, logging.getLogger("test")
    )


# ---------------------------------------------------------------------------
# Reaching the GPU: the start-up gate
# ---------------------------------------------------------------------------


def test_missing_coreml_is_a_warning_not_a_downgrade(patch_runtime):
    """CoreML is to ONNX what MPS is to torch, and its absence is partial.

    torch keeps the GPU; only the ONNX models lose it. Forcing CPU for the
    whole server over that would throw away the 23.7x on the default tagger.
    """
    patch_runtime(_apple_torch(), _ort(["CPUExecutionProvider"]))
    outcome = StartupCheckOutcome()
    _checks("auto")._check_device_and_vram(outcome)

    assert not outcome.forced_cpu
    assert any("CoreMLExecutionProvider unavailable" in w for w in outcome.warnings)


def test_nvidia_smi_is_not_missing_on_a_mac(patch_runtime, monkeypatch):
    """Warning about an NVIDIA tool on Apple hardware teaches owners to scroll past.

    It is not a missing dependency there, it is the machine.
    """
    patch_runtime(_apple_torch(), _ort(["CoreMLExecutionProvider"]))
    monkeypatch.setattr(accelerator, "_torch", lambda: _apple_torch())
    monkeypatch.setattr(sc.shutil, "which", lambda name: None)
    outcome = StartupCheckOutcome()
    _checks("auto")._check_optional_dependencies(outcome)

    assert not any("nvidia-smi" in w for w in outcome.warnings)


def test_the_two_device_allowlists_are_one_object():
    """server.py and startup_checks.py used to keep separate copies, and drifted."""
    from pixlstash import server as server_module

    assert server_module.VALID_DEVICE_SETTINGS is VALID_DEVICE_SETTINGS
    assert {CPU, CUDA, MPS, "metal", "gpu", "auto"} <= set(VALID_DEVICE_SETTINGS)


# ---------------------------------------------------------------------------
# Naming the device
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written, expected",
    [
        ("metal", MPS),  # what the desktop UI calls it
        ("MPS", MPS),
        ("mps:0", MPS),
        ("cuda:1", CUDA),
        (None, CPU),
    ],
)
def test_device_spellings_normalise(written, expected):
    assert normalise_device(written) == expected


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_device_is_a_question_for_the_host_not_a_cpu_request(blank):
    """A blank setting means "unspecified", which the host answers.

    ``_HOST_RESOLVED_SETTINGS`` lists ``""`` alongside ``gpu`` and ``auto``, and
    :func:`resolve_device` documents it - but the value used to be washed through
    ``normalise_device``, whose trailing ``or CPU`` turns an empty string into an
    explicit CPU choice. That made the ``""`` member unreachable and the
    documented contract false: an empty config value became a silent CPU
    downgrade, which is the exact failure mode this module exists to remove.
    """
    assert resolve_device(blank, torch_module=_apple_torch()) == MPS
    assert resolve_device(blank, torch_module=_cuda_torch()) == CUDA


def test_an_explicit_cpu_choice_is_honoured_on_an_accelerated_host():
    """The other direction of "never override the owner".

    Over-forcing the accelerator is its own regression. Someone who names the
    CPU on a machine with a GPU has a reason - thermal, a flaky driver, leaving
    the GPU for something else - and second-guessing it is the same silent
    override as ignoring Metal, pointed the other way.
    """
    assert resolve_device(CPU, torch_module=_apple_torch()) == CPU


def test_detection_survives_a_torch_that_cannot_be_reached():
    """No torch means "no accelerator proven", never a crash at start-up.

    Passing ``None`` explicitly is the caller saying so; it must not be confused
    with "go and find torch", which is what the ``_UNSET`` sentinel separates.
    """
    assert available_accelerator(None) is None
    assert resolve_device("auto", torch_module=None) == CPU


def test_cuda_wins_where_both_are_present():
    """Preference order is a fact about the probe list, so pin it."""
    both = types.SimpleNamespace(
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: True)
        ),
        cuda=types.SimpleNamespace(is_available=lambda: True),
    )
    assert available_accelerator(both) == CUDA


# ---------------------------------------------------------------------------
# Surviving it: OOM classification, batch caps, budgets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", [MPS_ALLOCATOR_OOM, METAL_DRIVER_OOM])
def test_apple_oom_messages_are_classified_as_device_pressure(message):
    """Both real strings, captured from a reproduced OOM.

    Misclassifying these is not cosmetic: ``is_vram_oom`` is what makes a batch
    retryable, so a False here is a tagging run that logs an error and gives up
    on every image it had loaded.
    """
    assert is_vram_oom(RuntimeError(message))


def test_a_wrapped_apple_oom_is_still_one():
    """``raise RuntimeError(...) from oom`` is how a plugin reports one."""
    try:
        try:
            raise RuntimeError(MPS_ALLOCATOR_OOM)
        except RuntimeError as oom:
            raise ValueError("tagger batch failed") from oom
    except ValueError as wrapped:
        assert is_vram_oom(wrapped)


def test_sqlite_out_of_memory_is_still_not_a_gpu_problem():
    """The control that keeps the vocabulary honest.

    SQLITE_NOMEM says "out of memory" too, and retrying it as transient GPU
    pressure would loop on a failure that has nothing to do with a device.
    """
    assert not is_vram_oom(RuntimeError("sqlite3.OperationalError: out of memory"))
    assert not is_vram_oom(RuntimeError("disk I/O error"))


@pytest.mark.parametrize(
    "device, expected",
    [
        (CUDA, _MAX_CONCURRENT_GPU),
        (MPS, _MAX_CONCURRENT_MPS),
        (CPU, _MAX_CONCURRENT_CPU),
    ],
)
def test_image_concurrency_is_named_per_device(device, expected):
    """The polarity bug, pinned.

    ``max_concurrent_images`` read "cpu or else", so ``"mps"`` inherited CUDA's
    64 - a batch the measured memory model puts near 10.8 GB, reproduced as a
    real OOM at 32 on an 8 GB machine. The sibling in the tagging workflow read
    "cuda or else", so the two disagreed about every device neither named.
    """
    engine = InferenceEngine.__new__(InferenceEngine)
    engine.device = device
    assert engine.max_concurrent_images() == expected


def test_the_batch_cap_applies_to_mps():
    """It read ``device != "cuda"`` and answered "unlimited" everywhere else.

    On unified memory that turned the budget off on the one memory model where
    an unbounded batch reaches the machine's own RAM.
    """
    capped = vram_limited_batch_cap(4096, MPS, base_mb=900, per_item_mb=220)
    assert 1 < capped < 10_000
    assert capped == vram_limited_batch_cap(4096, CUDA, base_mb=900, per_item_mb=220)
    # The CPU has no device memory to run out of, so it is still uncapped.
    assert vram_limited_batch_cap(4096, CPU, base_mb=900, per_item_mb=220) == 10_000


def test_an_unconfigured_mps_budget_defaults_to_the_working_set(monkeypatch):
    """``None`` must not mean "unlimited" on unified memory.

    Left alone, torch's MPS allocator hands out up to 1.7x Metal's
    recommendation, which on an 8 GB machine is more memory than the machine
    physically has - so "no budget configured" is the case that needs one most.
    """
    monkeypatch.setattr(accelerator, "_torch", lambda: _apple_torch())
    monkeypatch.setitem(
        __import__("sys").modules, "torch", _apple_torch()
    )  # accelerator_total_memory_mb reads sys.modules, never imports
    budget = VramBudget(MPS)
    budget.set_budget_gb(None)

    assert budget.max_vram_usage_mb == RECOMMENDED_WORKING_SET_BYTES // (1024**2)


def test_an_unconfigured_cuda_budget_is_still_unlimited(monkeypatch):
    """The control: CUDA's allocator already raises a catchable OOM at the
    card's real limit, so unlimited genuinely means unlimited there."""
    monkeypatch.setattr(accelerator, "_torch", lambda: _cuda_torch())
    budget = VramBudget(CUDA)
    budget.set_budget_gb(None)

    assert budget.max_vram_usage_mb is None


def test_a_cpu_budget_is_still_ignored():
    budget = VramBudget(CPU)
    budget.set_budget_gb(4.0)
    assert budget.max_vram_usage_mb is None


# ---------------------------------------------------------------------------
# ONNX Runtime: which provider each device gets
# ---------------------------------------------------------------------------


def test_coreml_is_requested_as_mlprogram_with_a_cpu_fallback():
    """``ModelFormat: MLProgram`` is mandatory, not a preference.

    The provider's own default is ``NeuralNetwork``, which does not fall back on
    ``wd-convnext-tagger-v3`` - it fails outright with "error code: -1". A
    regression to the default would therefore lose WD14 tagging entirely on
    every Mac, which is why the option is asserted rather than assumed.
    """
    providers = onnx_execution_providers(
        MPS,
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        coreml_cache="/tmp/pixlstash-coreml",
    )
    name, options = providers[0]
    assert name == "CoreMLExecutionProvider"
    assert options["ModelFormat"] == "MLProgram"
    # ALL and CPUAndGPU tie on throughput (183 vs 182 ms/image) but ALL costs
    # 7.1 s of first load against 2.6 s, so the Neural Engine buys nothing here.
    assert options["MLComputeUnits"] == "CPUAndGPU"
    assert options["ModelCacheDirectory"] == "/tmp/pixlstash-coreml"
    assert providers[-1] == "CPUExecutionProvider"


def test_coreml_without_a_cache_directory_omits_the_key():
    """An unset cache must not become the string "None" in a provider option."""
    providers = onnx_execution_providers(
        MPS, ["CoreMLExecutionProvider", "CPUExecutionProvider"], coreml_cache=None
    )
    assert "ModelCacheDirectory" not in providers[0][1]


def test_an_mps_host_without_coreml_gets_the_cpu_rather_than_nothing():
    providers = onnx_execution_providers(MPS, ["CPUExecutionProvider"])
    assert providers == ["CPUExecutionProvider"]


def test_cuda_options_are_never_handed_to_coreml():
    """``ort_cuda_provider_options`` returns CUDA-only keys.

    ``gpu_mem_limit`` and ``cudnn_conv_algo_search`` mean nothing to CoreML, and
    a provider that is handed an option it does not know can refuse the session.
    """
    providers = onnx_execution_providers(
        MPS,
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        cuda_options={"gpu_mem_limit": 1024, "cudnn_conv_algo_search": "HEURISTIC"},
    )
    assert "gpu_mem_limit" not in providers[0][1]
    assert "cudnn_conv_algo_search" not in providers[0][1]


def test_cuda_still_gets_its_options_and_now_a_stated_fallback():
    providers = onnx_execution_providers(
        CUDA,
        ["CUDAExecutionProvider", "CPUExecutionProvider"],
        cuda_options={"gpu_mem_limit": 1024},
    )
    assert providers[0][0] == "CUDAExecutionProvider"
    assert providers[0][1]["gpu_mem_limit"] == 1024
    # ORT appends the CPU provider implicitly anyway; stating it means a reader
    # of the CUDA branch can see what happens to an op the provider cannot run.
    assert providers[-1] == "CPUExecutionProvider"


def test_rocm_keeps_its_branch():
    providers = onnx_execution_providers(CUDA, ["ROCMExecutionProvider"])
    assert providers[0][0] == "ROCMExecutionProvider"


def test_forcing_cpu_pins_the_cpu_provider():
    """How --force-cpu reaches ONNX Runtime, on a host that advertises CoreML."""
    providers = onnx_execution_providers(
        CPU, ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    )
    assert providers == ["CPUExecutionProvider"]


def test_the_coreml_cache_lives_under_the_user_cache_directory():
    """Not temp (never reused, never reaped promptly) and not data.

    WD14's compiled form measured 754 MB against a 377 MB ``model.onnx``, so
    this has to be somewhere a user can delete and lose nothing but the next
    start-up's two seconds.
    """
    path = accelerator.coreml_cache_dir()
    assert "coreml-cache" in path
    assert "cache" in path.lower()


# ---------------------------------------------------------------------------
# The default memory budget on unified memory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ram_gb, expected",
    [
        (8, 4.0),  # the smallest Apple Silicon: half the machine
        (16, 8.0),  # the cap starts binding here
        (24, 8.0),
        (32, 8.0),
        (64, 8.0),
        (128, 8.0),  # and never stops
    ],
)
def test_the_unified_memory_default_budget_is_half_the_ram_capped_at_8gb(
    monkeypatch, ram_gb, expected
):
    """``min(RAM/2, 8 GB)`` - argued from physical RAM, not from Metal.

    The figure this replaces was half of Metal's *recommended working set*,
    which on an 8 GB machine is 2.67 GB. Both are defensible; the one chosen
    reflects that the memory being budgeted is the machine's only memory, so
    the question "how much may PixlStash claim" is asked of RAM rather than of
    what the driver would hand out.
    """
    monkeypatch.setattr(su, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(su, "physical_memory_mb", lambda: ram_gb * 1024)

    assert su.default_max_vram_gb() == expected


def test_the_default_budget_stays_under_the_slider_ceiling(monkeypatch):
    """A default the slider cannot show is a default that gets silently clamped.

    Metal's working set is ~2/3 of RAM and the default is RAM/2, so the default
    is below the ceiling at every machine size - asserted rather than assumed,
    because the two are computed from different sources.
    """
    monkeypatch.setattr(su, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(su, "physical_memory_mb", lambda: 8 * 1024)
    # 8 GB machine: Metal recommends ~5.33 GB.
    monkeypatch.setattr(su, "query_total_vram_mb", lambda: 5461)

    assert su.default_max_vram_gb() <= su.max_vram_budget_gb()


def test_the_card_path_default_is_untouched(monkeypatch):
    """The control. Changing the Mac's answer must not move anybody else's."""
    monkeypatch.setattr(su, "is_apple_silicon", lambda: False)
    monkeypatch.setattr(su, "query_total_vram_mb", lambda: 32 * 1024)

    assert su.default_max_vram_gb() == 16.0  # 50 % of a 32 GB card


def test_an_unreadable_ram_figure_falls_back_rather_than_guessing(monkeypatch):
    """``physical_memory_mb`` returns 0 for "unknown", never for "no memory"."""
    monkeypatch.setattr(su, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(su, "physical_memory_mb", lambda: 0)
    monkeypatch.setattr(su, "query_total_vram_mb", lambda: 5461)

    # Falls through to the card path - which is half the accelerator-reported
    # figure (2.67 GB) raised by that path's own 4 GB floor. The floor is
    # deliberately not applied to the RAM-derived answer above, because there it
    # would hand a small machine half its total RAM; on this fallback it is the
    # pre-existing behaviour and is left alone.
    assert su.default_max_vram_gb() == 4.0


# ---------------------------------------------------------------------------
# The tagger's memory model is per device AND per dtype
# ---------------------------------------------------------------------------


def _workflow_on(device):
    engine = InferenceEngine.__new__(InferenceEngine)
    engine.device = device
    workflow = TaggingWorkflow.__new__(TaggingWorkflow)
    workflow._engine = engine
    return workflow


def test_the_tagger_memory_model_follows_the_device():
    """700 + 90n is a CUDA measurement and was being charged on Apple Silicon.

    The MPS pair was measured as fresh-process peak device memory at batch 8
    (2347 MiB fp32, 1319 MiB fp16); 450 + 240n and 450 + 120n reproduce those
    slightly conservatively, which is the direction a memory gate should err.
    """
    assert _workflow_on(MPS)._pixlstash_tagger_memory_model() == (
        PIXLSTASH_TAGGER_MPS_BASE_MB,
        PIXLSTASH_TAGGER_MPS_PER_ITEM_FP16_MB,  # the tagger runs fp16 on MPS
    )
    assert _workflow_on(CUDA)._pixlstash_tagger_memory_model() == (
        PIXLSTASH_TAGGER_CUDA_BASE_MB,
        PIXLSTASH_TAGGER_CUDA_PER_ITEM_MB,
    )


def test_the_mps_model_halves_when_the_dtype_does(monkeypatch):
    """fp16 is most of why a unified-memory machine can afford the batch."""
    monkeypatch.setattr(
        "pixlstash.inference.workflows.tagging.supports_fp16", lambda device: False
    )
    assert _workflow_on(MPS)._pixlstash_tagger_memory_model() == (
        PIXLSTASH_TAGGER_MPS_BASE_MB,
        PIXLSTASH_TAGGER_MPS_PER_ITEM_MB,
    )
    assert PIXLSTASH_TAGGER_MPS_PER_ITEM_FP16_MB * 2 == PIXLSTASH_TAGGER_MPS_PER_ITEM_MB


def test_the_measured_model_predicts_the_measured_peak():
    """Anchored on the real numbers, so a future edit cannot drift silently."""
    fp16 = PIXLSTASH_TAGGER_MPS_BASE_MB + 8 * PIXLSTASH_TAGGER_MPS_PER_ITEM_FP16_MB
    fp32 = PIXLSTASH_TAGGER_MPS_BASE_MB + 8 * PIXLSTASH_TAGGER_MPS_PER_ITEM_MB
    assert 1319 <= fp16 <= 1319 * 1.15  # measured 1319 MiB, conservative
    assert 2347 <= fp32 <= 2347 * 1.15  # measured 2347 MiB, conservative


# ---------------------------------------------------------------------------
# JoyCaption: available, but loudly expensive
# ---------------------------------------------------------------------------


def test_joycaption_warns_where_device_memory_is_the_only_memory(monkeypatch):
    """Kept selectable on Apple Silicon, but the cost is said where it is chosen.

    The constraint is **memory**, not availability: bitsandbytes does support
    mps, so NF4 and INT8 load and run. What makes the plugin a bad bet on a
    small Mac is that its ~8 GB of weights come out of the machine's only RAM.
    The warning goes in the parameter's own help text because that is what the
    Auto-tagging screen renders.
    """
    monkeypatch.setattr(jc, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(jc, "physical_memory_mb", lambda: 8 * 1024)

    help_text = jc._precision_help()
    assert "system RAM" in help_text
    assert "8 GB machine" in help_text
    # The correction, pinned: this must never claim NF4/INT8 cannot load.
    assert "will not load" not in help_text
    assert "no Metal backend" not in help_text
    # The plugin is still offered - a warning, not a removal.
    assert jc.JoyCaptionPlugin.supports_descriptions


def test_joycaption_precision_help_is_plain_on_a_card(monkeypatch):
    """The control: a CUDA box gets the straight VRAM trade, no Metal talk."""
    monkeypatch.setattr(jc, "is_apple_silicon", lambda: False)

    help_text = jc._precision_help()
    assert "system RAM" not in help_text
    assert "bitsandbytes" in help_text


def test_the_joycaption_warning_reaches_the_rendered_schema(monkeypatch):
    """A helper nothing calls is not a warning. Pin it to the real schema."""
    monkeypatch.setattr(jc, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(jc, "physical_memory_mb", lambda: 8 * 1024)

    schema = jc.JoyCaptionPlugin().parameter_schema()
    precision = next(p for p in schema if p["name"] == "precision")
    assert "system RAM" in precision["description"]
