"""Apple Metal (MPS) support, and the device handling around it on every platform.

Stand-ins replace torch's device probes and the models, so these run on any
host. The tests that need a real Apple Metal GPU are marked and skip without one.
"""

import logging
import sys
import types

import pytest

from pixlstash.utils.device_utils import (
    detect_device,
    empty_device_cache,
    is_accelerator,
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
