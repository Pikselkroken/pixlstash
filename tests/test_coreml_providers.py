"""WD14's ONNX Runtime provider ladder, including Apple's CoreML provider.

The ladder is asserted with a stand-in ``onnxruntime`` rather than real
hardware, so a Linux CI runner exercises the same branches a Mac does. The
CoreML measurements that justify the branch are recorded in the comment on
``_COREML_PROVIDERS``.
"""

import os
import types

import pytest

from pixlstash.tagger_plugins import wd14
from pixlstash.tagger_plugins.wd14 import WD14Service


@pytest.fixture
def captured_session(monkeypatch, tmp_path):
    """Stand in for ort, capturing the providers each session is built with.

    ``InferenceSession`` is replaced rather than mocked at a lower level: the
    provider list is the whole subject here, and building a real session would
    need the 400 MB checkpoint that CI does not have.
    """
    captured = {}

    class _FakeSession:
        def __init__(self, path, providers=None, **kwargs):
            captured["providers"] = providers
            self.path = path

        def get_inputs(self):
            return [types.SimpleNamespace(name="input", shape=["batch", 448, 448, 3])]

        def get_providers(self):
            """Report the providers as loaded, which here is what was asked for.

            ``_warn_if_the_session_fell_back_to_cpu`` asks the session what it
            actually got, so the stand-in has to answer. Echoing the request
            keeps that check live in these tests - a fake returning a fixed
            list would make every one of them exercise the warning branch
            instead of the ladder they are about.
            """
            return _names(captured["providers"])

    def make(available):
        monkeypatch.setattr(
            wd14.ort, "get_available_providers", lambda: list(available)
        )
        monkeypatch.setattr(wd14.ort, "InferenceSession", _FakeSession)
        # The session is only built if the checkpoint appears to exist, and the
        # capacity probe would otherwise run a real inference.
        monkeypatch.setattr(os.path, "exists", lambda _p: True)
        monkeypatch.setattr(WD14Service, "_resolve_batch_capacity", lambda self: 8)
        return captured

    return make


def _service(device, tmp_path):
    return WD14Service(device=device, model_dir=str(tmp_path), batch_size_fn=lambda: 8)


def _names(providers):
    """Provider names only — entries may be a bare string or (name, options)."""
    return [p[0] if isinstance(p, tuple) else p for p in providers]


def test_coreml_is_used_when_it_is_the_only_accelerator(captured_session, tmp_path):
    captured = captured_session(["CoreMLExecutionProvider", "CPUExecutionProvider"])
    _service("mps", tmp_path)._init_onnx_session()

    assert _names(captured["providers"]) == [
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    ]


def test_coreml_is_paired_with_a_cpu_fallback(captured_session, tmp_path):
    """CoreML declines operators it cannot run; they need somewhere to go."""
    captured = captured_session(["CoreMLExecutionProvider", "CPUExecutionProvider"])
    _service("mps", tmp_path)._init_onnx_session()

    assert "CPUExecutionProvider" in _names(captured["providers"])


def test_coreml_carries_the_mlprogram_options(captured_session, tmp_path):
    captured = captured_session(["CoreMLExecutionProvider", "CPUExecutionProvider"])
    _service("mps", tmp_path)._init_onnx_session()

    # The list mixes ``(name, options)`` tuples with bare provider names, so it
    # is not a mapping — pick the entry out rather than calling dict() on it.
    options = next(
        opts
        for entry in captured["providers"]
        if isinstance(entry, tuple)
        for name, opts in [entry]
        if name == "CoreMLExecutionProvider"
    )
    assert options["ModelFormat"] == "MLProgram"
    assert options["MLComputeUnits"] == "ALL"


def test_cuda_still_wins_over_coreml(captured_session, tmp_path):
    # Never both in practice; asserted so the ordering stays deliberate.
    captured = captured_session(
        ["CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
    )
    _service("cuda", tmp_path)._init_onnx_session()

    assert _names(captured["providers"])[0] == "CUDAExecutionProvider"


def test_rocm_still_wins_over_coreml(captured_session, tmp_path):
    captured = captured_session(
        ["ROCMExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
    )
    _service("cuda", tmp_path)._init_onnx_session()

    assert _names(captured["providers"])[0] == "ROCMExecutionProvider"


def test_an_explicit_cpu_device_does_not_get_coreml(captured_session, tmp_path):
    """`cpu` is a request, not a fallback — honour it even on Apple hardware."""
    captured = captured_session(["CoreMLExecutionProvider", "CPUExecutionProvider"])
    _service("cpu", tmp_path)._init_onnx_session()

    assert _names(captured["providers"]) == ["CPUExecutionProvider"]


def test_cpu_only_host_is_unchanged(captured_session, tmp_path):
    captured = captured_session(["CPUExecutionProvider"])
    _service("mps", tmp_path)._init_onnx_session()

    assert _names(captured["providers"]) == ["CPUExecutionProvider"]
