"""The start-up gate: which device a Server actually ends up on.

The companion file ``test_mps_memory_and_providers.py`` covers surviving the GPU
once we are on it - memory budgets, OOM classification, ONNX providers. This one
covers *reaching* it, which is the half that was silently broken.

``InferenceEngine.create`` never reads the config: it auto-detects, and
``default_device`` only ever sets ``force_cpu``. So the real gate is
``StartupChecks._check_device_and_vram`` - it saw ``torch.cuda.is_available() ==
False`` on a Mac, called ``_force_cpu_with_warning``, and ``Server.build_vault``
turned that into ``force_cpu=True``, pinning the CPU **before any engine-side
probe ran**. Every other accelerator change in the codebase was therefore dead
code on Apple Silicon, and a benchmark that constructed a tagger service
directly could not see it: the service was handed ``"mps"`` by hand while the
real application was still being handed the CPU. These tests assert the chain,
not the component.

The host is simulated throughout, through the same injection seam
``test_rocm_device_check.py`` uses. That is load-bearing rather than tidy: CI's
runners are CPU-only Linux, so a test that asked the real machine could only ever
prove the CPU path, and the NVIDIA path would stop being verified at all.
Nothing here may gate on ``platform.system()`` - the question is always "what can
this host do", never "what is this host called".

Both directions are asserted throughout. An accelerator that is present must be
used, AND a host without one must still land on the CPU: over-forcing the
accelerator is its own regression, and ``--force-cpu`` is what keeps CI honest.
"""

import logging
import os
import types

import pytest

import pixlstash.startup_checks as sc
from pixlstash.startup_checks import StartupCheckOutcome, StartupChecks
from pixlstash.utils.accelerator import (
    CPU,
    CUDA,
    HF_ASYNC_LOAD_ENV,
    MPS,
    available_accelerator,
    configure_metal_model_loading,
    is_accelerated,
    normalise_device,
    resolve_device,
    supports_fp16,
)


def _torch(*, cuda=False, mps=False):
    """Build a torch stand-in for a host with the named backends.

    Shaped like real torch rather than like the probe: MPS answers through
    ``torch.backends.mps`` and CUDA through ``torch.cuda``. ``backends``
    deliberately carries no ``cuda`` attribute, so the CUDA probe falls through
    to the second spelling exactly as it does against the real module - getting
    that wrong would let a probe pass here that cannot pass in production.
    """
    return types.SimpleNamespace(
        version=types.SimpleNamespace(hip=None, cuda="12.8" if cuda else None),
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: mps)
        ),
        cuda=types.SimpleNamespace(
            is_available=lambda: cuda,
            mem_get_info=lambda: (8_000 * 1024**2, 16_000 * 1024**2),
            get_device_capability=lambda i=0: (9, 0),
            get_device_name=lambda i=0: "Fake GPU",
        ),
        mps=types.SimpleNamespace(
            recommended_max_memory=lambda: 5 * 1024**3,
            driver_allocated_memory=lambda: 0,
            empty_cache=lambda: None,
        ),
    )


def _ort(providers):
    return types.SimpleNamespace(get_available_providers=lambda: list(providers))


CUDA_HOST = dict(cuda=True, mps=False)
MPS_HOST = dict(cuda=False, mps=True)
BARE_HOST = dict(cuda=False, mps=False)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host, expected",
    [(CUDA_HOST, CUDA), (MPS_HOST, MPS), (BARE_HOST, None)],
    ids=["cuda-host", "mps-host", "no-accelerator"],
)
def test_detection_names_the_accelerator_the_host_has(host, expected):
    """Both directions: a host with an accelerator reports it, one without says so."""
    assert available_accelerator(_torch(**host)) == expected


def test_a_broken_probe_is_read_as_unavailable_rather_than_raising():
    """A broken install raises from the probe itself - the known case is an
    unsupported ROCm gfx arch. Detection has to survive it and keep looking,
    rather than taking start-up down with it."""
    torch = _torch(**BARE_HOST)
    torch.cuda.is_available = lambda: (_ for _ in ()).throw(
        RuntimeError("HIP error: no ROCm-capable device is detected")
    )
    assert available_accelerator(torch) is None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("requested", [None, "auto", "gpu"])
@pytest.mark.parametrize(
    "host, expected",
    [(CUDA_HOST, CUDA), (MPS_HOST, MPS), (BARE_HOST, CPU)],
    ids=["cuda-host", "mps-host", "no-accelerator"],
)
def test_host_resolved_settings_follow_the_hardware(requested, host, expected):
    """Everything meaning "whatever this host has" resolves to what it has.

    ``"gpu"`` is in here deliberately: it is a request for *an* accelerator, not
    for CUDA, and reading it as CUDA is precisely how a Mac ends up refused for
    the crime of not being an NVIDIA box.
    """
    assert resolve_device(requested, torch_module=_torch(**host)) == expected


@pytest.mark.parametrize(
    "host", [CUDA_HOST, MPS_HOST, BARE_HOST], ids=["cuda", "mps", "bare"]
)
def test_force_cpu_beats_every_accelerator(host):
    """``--force-cpu`` is the CI flag, and CI correctness depends on it.

    A flag that only suppressed CUDA would quietly start using Metal the moment
    the gate ran on a developer's Mac - the same silent device switch this whole
    change exists to stop, wearing the opposite hat.
    """
    assert resolve_device("auto", force_cpu=True, torch_module=_torch(**host)) == CPU


def test_an_explicitly_named_device_is_not_silently_downgraded():
    """Asking for CUDA on a Mac returns CUDA, so the caller gets its own error.

    A silent downgrade to the CPU is how a misconfigured host looks perfectly
    healthy while running twenty times slow - which is the bug, not the fix.
    """
    assert resolve_device(CUDA, torch_module=_torch(**MPS_HOST)) == CUDA


def test_metal_is_an_alias_for_mps_not_a_fourth_device():
    """The desktop shell's UI says "Metal"; torch says "mps". One device, two
    names, and an owner who types what the shell showed them gets what they
    meant."""
    assert normalise_device("metal") == MPS


@pytest.mark.parametrize(
    "device, accelerated",
    [(CUDA, True), (MPS, True), (CPU, False)],
    ids=["cuda", "mps", "cpu"],
)
def test_accelerator_capabilities_are_answered_per_device(device, accelerated):
    """fp16 is an accelerator property here; the CPU path stays fp32."""
    assert is_accelerated(device) is accelerated
    assert supports_fp16(device) is accelerated


# ---------------------------------------------------------------------------
# The start-up chain: the path a Server actually takes
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_runtime(monkeypatch):
    """Seed ``startup_checks``' cached torch/ort handles with fakes.

    The caches are what stop the accessors ever attempting the real import, so
    seeding them both substitutes the fake and keeps the test off this machine's
    actual hardware. ``startup_checks`` passes the handle down to the accelerator
    probes, which is what lets a CPU-only runner verify a CUDA or Metal host.
    """

    def apply(torch_mod, ort_mod):
        monkeypatch.setattr(sc, "_torch_mod", torch_mod)
        monkeypatch.setattr(sc, "_ort_mod", ort_mod)

    return apply


def _run_device_check(device):
    cfg = {"default_device": device}
    outcome = StartupCheckOutcome()
    checks = StartupChecks(cfg, "/tmp/server_config.json", logging.getLogger("test"))
    checks._check_device_and_vram(outcome)
    return cfg, outcome


def test_an_apple_silicon_host_is_not_quietly_pinned_to_the_cpu(patch_runtime):
    """**The regression this whole change exists to prevent.**

    Everything else is downstream of this one assertion: while the start-up
    check forced the CPU, no amount of accelerator support anywhere else in the
    codebase could be reached.
    """
    patch_runtime(_torch(**MPS_HOST), _ort(["CoreMLExecutionProvider"]))
    cfg, outcome = _run_device_check("auto")

    assert not outcome.forced_cpu, "an Apple Silicon host must not be forced to CPU"
    assert not outcome.hard_failures
    assert cfg["default_device"] != CPU


def test_a_host_with_no_accelerator_still_lands_on_the_cpu(patch_runtime):
    """The other direction: the CPU path must survive the change intact."""
    patch_runtime(_torch(**BARE_HOST), _ort(["CPUExecutionProvider"]))
    _, outcome = _run_device_check("auto")

    assert outcome.forced_cpu
    assert not outcome.hard_failures


def test_a_cuda_host_is_unaffected_by_the_apple_silicon_work(patch_runtime):
    """NVIDIA is the primary platform. Speeding up Macs at its expense - or by
    blinding CI to its regressions - is not a trade worth making."""
    patch_runtime(_torch(**CUDA_HOST), _ort(["CUDAExecutionProvider"]))
    _, outcome = _run_device_check("auto")

    assert not outcome.forced_cpu
    assert not outcome.hard_failures


def test_naming_an_absent_accelerator_refuses_loudly(patch_runtime):
    """An owner who asked for CUDA on a Mac gets a refusal that says so, rather
    than a silent CPU downgrade they would only ever notice as slowness."""
    patch_runtime(_torch(**MPS_HOST), _ort(["CoreMLExecutionProvider"]))
    _, outcome = _run_device_check(CUDA)

    assert outcome.hard_failures
    assert any("unavailable" in f for f in outcome.hard_failures)


def test_an_explicit_cpu_choice_is_honoured_at_start_up(patch_runtime):
    """Asking for the CPU on a machine with a GPU gets the CPU. The owner may
    have a reason - thermal, a flaky driver, leaving the GPU to something else -
    and overriding it is the same silent override pointed the other way."""
    patch_runtime(_torch(**MPS_HOST), _ort(["CoreMLExecutionProvider"]))
    _, outcome = _run_device_check(CPU)

    assert outcome.forced_cpu
    assert not outcome.hard_failures


# ---------------------------------------------------------------------------
# The transformers loader threads
# ---------------------------------------------------------------------------


@pytest.fixture
def no_async_load_env(monkeypatch):
    """Start each case with the variable unset, and never leak one to the next."""
    monkeypatch.delenv(HF_ASYNC_LOAD_ENV, raising=False)
    return monkeypatch


def test_metal_turns_off_the_transformers_loader_threads(no_async_load_env):
    """The whole point: where Metal exists, weights load on one thread.

    Unguarded, a transformers load on Metal failed 10 of 10 here (6 hangs, 3
    SIGSEGV, 1 SIGBUS) and 0 of 10 with this set - and none of them raise, so
    nothing downstream can recover from it.
    """
    assert configure_metal_model_loading(_torch(**MPS_HOST)) is True
    assert os.environ[HF_ASYNC_LOAD_ENV] == "1"


@pytest.mark.parametrize(
    "host", [CUDA_HOST, BARE_HOST], ids=["cuda-host", "no-accelerator"]
)
def test_a_host_without_metal_is_left_alone(host, no_async_load_env):
    """The other direction. The variable slows loading on a machine that cannot
    hit the race, and setting it everywhere would be an unexplained tax on every
    CUDA and CPU host."""
    assert configure_metal_model_loading(_torch(**host)) is False
    assert HF_ASYNC_LOAD_ENV not in os.environ


def test_a_host_with_no_torch_at_all_sets_nothing(no_async_load_env):
    """``_resolve_torch`` yields None when torch cannot be reached. There is no
    Metal to protect and no loader to configure, and it must not raise."""
    assert configure_metal_model_loading(None) is False
    assert HF_ASYNC_LOAD_ENV not in os.environ


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_an_owner_who_already_set_it_keeps_their_value(value, no_async_load_env):
    """Already true: nothing to do, and the owner's spelling survives."""
    no_async_load_env.setenv(HF_ASYNC_LOAD_ENV, value)

    assert configure_metal_model_loading(_torch(**MPS_HOST)) is False
    assert os.environ[HF_ASYNC_LOAD_ENV] == value


@pytest.mark.parametrize("value", ["0", "", "false", "no"])
def test_a_value_transformers_reads_as_false_is_kept_but_warned_about(
    value, no_async_load_env, caplog
):
    """``"0"`` and ``""`` both leave the loader threaded, so the host is exposed -
    but the value is the owner's and something may depend on it. Say so loudly
    rather than silently taking it away. A test that only checked the return
    value would pass with no warning at all, which is the failure this guards.
    """
    no_async_load_env.setenv(HF_ASYNC_LOAD_ENV, value)

    with caplog.at_level(logging.WARNING):
        assert configure_metal_model_loading(_torch(**MPS_HOST)) is False

    assert os.environ[HF_ASYNC_LOAD_ENV] == value
    assert any(
        record.levelno >= logging.WARNING and HF_ASYNC_LOAD_ENV in record.getMessage()
        for record in caplog.records
    ), (
        f"no warning named {HF_ASYNC_LOAD_ENV}; "
        f"records={[r.getMessage() for r in caplog.records]}"
    )
