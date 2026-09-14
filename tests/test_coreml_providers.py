"""WD14's ONNX Runtime provider ladder, including Apple's CoreML provider.

The ladder is asserted with a stand-in ``onnxruntime`` rather than real
hardware, so a Linux CI runner exercises the same branches a Mac does. Two
tests at the end use the installed onnxruntime and are skipped where it offers
no CoreML provider. The CoreML measurements behind these choices are recorded on
``_COREML_PROVIDERS``.
"""

import logging
import os
import types

import numpy as np
import onnxruntime
import pytest
from onnxruntime.capi.onnxruntime_pybind11_state import EPFail, InvalidArgument

from pixlstash.tagger_plugins import wd14
from pixlstash.tagger_plugins.wd14 import WD14Service

#: What onnxruntime 1.29 raises for a CoreML option it does not know.
_COREML_OPTION_ERROR = (
    "coreml_options.cc:68 void onnxruntime::CoreMLOptions::"
    "ValidateAndParseProviderOption(const ProviderOptions &) "
    "Unknown option: ModelFormats"
)


def _names(providers):
    """Provider names only - entries may be a bare string or (name, options)."""
    return [p[0] if isinstance(p, tuple) else p for p in providers]


@pytest.fixture
def fake_ort(monkeypatch):
    """Stand in for ort, recording every session WD14 asks it to build.

    ``InferenceSession`` is replaced rather than mocked at a lower level: the
    provider list is the whole subject here, and building a real session would
    need the 400 MB checkpoint that CI does not have.

    The stand-in follows onnxruntime's own rules for a provider that fails, so
    a test cannot pass on a run onnxruntime would have handled differently:

    * ``start_errors`` maps a provider name to the error it raises at session
      start. A ``ValueError`` or ``RuntimeError`` is swallowed unless
      ``enable_fallback=0`` was passed: the error is printed and the session
      comes back on the CPU provider. Any other type is raised either way.
    * ``dropped`` names providers that start without an error and are missing
      from the session, as the CUDA provider is when ``libcublasLt`` is.
    * ``run_errors`` maps a provider name to the error ``run()`` raises while
      that provider leads the session. Only ``EPFail`` is retried by
      onnxruntime, once, on the CPU provider, and only unless
      ``enable_fallback=0`` was passed; a failure on that retry is raised.

    ``fake_ort.runs`` records the leading provider of every ``run()`` call.
    """
    calls = []
    runs = []
    behaviour = {"start_errors": {}, "dropped": set(), "run_errors": {}}

    class _FakeSession:
        def __init__(self, path, providers=None, **kwargs):
            calls.append({"path": path, "providers": providers, "kwargs": kwargs})
            names = _names(providers or [])
            for name in names:
                error = behaviour["start_errors"].get(name)
                if error is None:
                    continue
                fallback = int(kwargs.get("enable_fallback", 1)) == 1
                if not (fallback and isinstance(error, (ValueError, RuntimeError))):
                    raise error
                print(f"EP Error {error} when using {providers}")
                names = ["CPUExecutionProvider"]
                break
            names = [n for n in names if n not in behaviour["dropped"]]
            # onnxruntime always keeps the CPU provider in a session.
            if "CPUExecutionProvider" not in names:
                names.append("CPUExecutionProvider")
            self._providers = names
            self._fallback = int(kwargs.get("enable_fallback", 1)) == 1

        def run(self, output_names, input_feed):
            batch = len(next(iter(input_feed.values())))
            runs.append(self._providers[0])
            error = behaviour["run_errors"].get(self._providers[0])
            if error is not None:
                if not (self._fallback and isinstance(error, EPFail)):
                    raise error
                print(f"EP Error: {error} using {self._providers}")
                self._providers = ["CPUExecutionProvider"]
                self._fallback = False
                return self.run(output_names, input_feed)
            # Four rating columns, then one general tag scored 0.9.
            return [np.full((batch, 5), 0.9, dtype=np.float32)]

        def get_inputs(self):
            return [types.SimpleNamespace(name="input", shape=["batch", 448, 448, 3])]

        def get_providers(self):
            """The providers the session loaded, which
            ``_warn_if_the_session_fell_back_to_cpu`` reads."""
            return list(self._providers)

    def make(available, start_errors=None, dropped=(), run_errors=None):
        behaviour["start_errors"] = dict(start_errors or {})
        behaviour["dropped"] = set(dropped)
        behaviour["run_errors"] = dict(run_errors or {})
        monkeypatch.setattr(
            wd14.ort, "get_available_providers", lambda: list(available)
        )
        monkeypatch.setattr(wd14.ort, "InferenceSession", _FakeSession)
        # The session is only built if the checkpoint appears to exist, and the
        # capacity probe would otherwise run a real inference.
        monkeypatch.setattr(os.path, "exists", lambda _p: True)
        monkeypatch.setattr(WD14Service, "_resolve_batch_capacity", lambda self: 8)
        return calls

    make.runs = runs
    return make


class _NativeFail(Exception):
    """Stands in for ``onnxruntime.capi.onnxruntime_pybind11_state.Fail``,
    which is not a ``RuntimeError`` and so is never retried by onnxruntime."""


def _service(device, tmp_path):
    return WD14Service(device=device, model_dir=str(tmp_path), batch_size_fn=lambda: 8)


def _tag_batch(service):
    """Tag two images through ``_run_batch``; the fake scores ``cat`` 0.9."""
    service._general_tags = ["cat"]
    service._rating_tags = []
    images = [(f"img{i}.png", np.zeros((448, 448, 3), np.float32)) for i in (0, 1)]
    return service._run_batch(images, set())


def _warnings(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == wd14.logger.name and r.levelno == logging.WARNING
    ]


def test_coreml_is_used_when_it_is_the_only_accelerator(fake_ort, tmp_path, caplog):
    calls = fake_ort(["CoreMLExecutionProvider", "CPUExecutionProvider"])
    service = _service("mps", tmp_path)
    with caplog.at_level(logging.DEBUG, logger=wd14.logger.name):
        service._init_onnx_session()

    assert len(calls) == 1
    assert service._ort_sess.get_providers()[0] == "CoreMLExecutionProvider"
    # The session got CoreML, so there is nothing to warn about - a warning
    # here is noise every Mac learns to ignore.
    assert _warnings(caplog) == []
    # And the debug line names the options in use, not the CUDA ones.
    assert "WD14 CUDA provider options" not in caplog.text
    assert "WD14 CoreML provider options" in caplog.text


def test_coreml_is_listed_ahead_of_the_cpu_provider(fake_ort, tmp_path):
    """The request is exactly ``[CoreML, CPU]``, in that order.

    The CPU entry does not decide where CoreML's declined operators run:
    onnxruntime puts them on the CPU provider whether or not it is listed.
    Listing it keeps onnxruntime's ``VerifyEachNodeIsAssignedToAnEp``
    warnings out of the log, and the order keeps CoreML first choice for
    every node it accepts.
    """
    calls = fake_ort(["CoreMLExecutionProvider", "CPUExecutionProvider"])
    _service("mps", tmp_path)._init_onnx_session()

    assert _names(calls[0]["providers"]) == [
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    ]


def test_coreml_carries_the_mlprogram_options(fake_ort, tmp_path):
    calls = fake_ort(["CoreMLExecutionProvider", "CPUExecutionProvider"])
    _service("mps", tmp_path)._init_onnx_session()

    # The list mixes ``(name, options)`` tuples with bare provider names, so it
    # is not a mapping - pick the entry out rather than calling dict() on it.
    options = next(
        opts
        for entry in calls[0]["providers"]
        if isinstance(entry, tuple)
        for name, opts in [entry]
        if name == "CoreMLExecutionProvider"
    )
    assert options["ModelFormat"] == "MLProgram"
    assert options["MLComputeUnits"] == "ALL"


def test_a_coreml_session_turns_off_onnxruntimes_silent_cpu_fallback(
    fake_ort, tmp_path
):
    calls = fake_ort(["CoreMLExecutionProvider", "CPUExecutionProvider"])
    _service("mps", tmp_path)._init_onnx_session()

    assert calls[0]["kwargs"].get("enable_fallback") == 0


def test_a_coreml_start_failure_is_logged_and_the_session_starts_on_the_cpu(
    fake_ort, tmp_path, caplog, capsys
):
    """CoreML listed but failing to start: the cause reaches the log.

    onnxruntime's own fallback reports the error with ``print()`` and hands
    back a CPU session, so the log shows the CPU session and no reason. The
    failure has to be caught, logged with the model path, the options and the
    error, and followed by an explicitly created CPU session.
    """
    calls = fake_ort(
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        start_errors={"CoreMLExecutionProvider": RuntimeError(_COREML_OPTION_ERROR)},
    )
    service = _service("mps", tmp_path)
    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        service._init_onnx_session()

    onnx_path = os.path.join(service._model_location, "model.onnx")
    # The CoreML attempt, then a separate CPU-only session.
    assert [_names(c["providers"]) for c in calls] == [
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        ["CPUExecutionProvider"],
    ]
    assert calls[1]["path"] == onnx_path
    assert service._ort_sess.get_providers() == ["CPUExecutionProvider"]
    assert service._input_name == "input"
    # onnxruntime's stdout report never ran.
    assert "EP Error" not in capsys.readouterr().out

    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    message = warnings[0]
    assert "Unknown option: ModelFormats" in message
    assert onnx_path in message
    assert "'ModelFormat': 'MLProgram'" in message
    assert "CPU" in message
    # Neither the CUDA advice nor the "no CoreML in this build" diagnosis
    # applies to a provider that is present and failed.
    assert "onnxruntime-gpu" not in message
    assert "libcublasLt" not in message
    assert "not CoreMLExecutionProvider" not in message


def test_a_coreml_error_onnxruntime_would_not_retry_also_starts_on_the_cpu(
    fake_ort, tmp_path, caplog
):
    """A native ``Fail`` from CoreML is a start failure like any other."""
    calls = fake_ort(
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        start_errors={"CoreMLExecutionProvider": _NativeFail("Error compiling model")},
    )
    service = _service("mps", tmp_path)
    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        service._init_onnx_session()

    assert _names(calls[-1]["providers"]) == ["CPUExecutionProvider"]
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "_NativeFail: Error compiling model" in warnings[0]


def test_a_cuda_start_failure_keeps_onnxruntimes_own_fallback_and_advice(
    fake_ort, tmp_path, caplog
):
    """CUDA is not given CoreML's handling: onnxruntime retries on the CPU,
    WD14 builds no second session, and the warning keeps the CUDA advice."""
    calls = fake_ort(
        ["CUDAExecutionProvider", "CPUExecutionProvider"],
        start_errors={"CUDAExecutionProvider": RuntimeError("CUDA failure 100")},
    )
    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        _service("cuda", tmp_path)._init_onnx_session()

    assert len(calls) == 1
    assert calls[0]["kwargs"].get("enable_fallback", 1) == 1
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "pip install onnxruntime-gpu" in warnings[0]


def test_a_cuda_error_onnxruntime_would_not_retry_still_propagates(fake_ort, tmp_path):
    fake_ort(
        ["CUDAExecutionProvider", "CPUExecutionProvider"],
        start_errors={"CUDAExecutionProvider": _NativeFail("CUDA failure 100")},
    )
    with pytest.raises(_NativeFail, match="CUDA failure 100"):
        _service("cuda", tmp_path)._init_onnx_session()


def test_cuda_still_wins_over_coreml(fake_ort, tmp_path):
    # Never both in practice; asserted so the ordering stays deliberate.
    calls = fake_ort(
        ["CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
    )
    _service("cuda", tmp_path)._init_onnx_session()

    assert len(calls) == 1
    assert _names(calls[0]["providers"]) == ["CUDAExecutionProvider"]
    # CUDA keeps onnxruntime's default fallback.
    assert calls[0]["kwargs"].get("enable_fallback", 1) == 1


def test_rocm_still_wins_over_coreml(fake_ort, tmp_path):
    calls = fake_ort(
        ["ROCMExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
    )
    _service("cuda", tmp_path)._init_onnx_session()

    assert len(calls) == 1
    assert calls[0]["providers"] == [("ROCMExecutionProvider", {})]
    assert calls[0]["kwargs"].get("enable_fallback", 1) == 1


def test_an_explicit_cpu_device_does_not_get_coreml(fake_ort, tmp_path):
    """`cpu` is a request, not a fallback - honour it even on Apple hardware."""
    calls = fake_ort(["CoreMLExecutionProvider", "CPUExecutionProvider"])
    _service("cpu", tmp_path)._init_onnx_session()

    assert _names(calls[0]["providers"]) == ["CPUExecutionProvider"]


def test_a_metal_host_without_coreml_is_told_which_providers_are_offered(
    fake_ort, tmp_path, caplog
):
    """CoreML not offered by this build: say what is, and how to get CoreML.

    ``onnxruntime-gpu`` is the CUDA build and has no macOS wheels, so the CUDA
    remediation is wrong on a Mac.
    """
    calls = fake_ort(["AzureExecutionProvider", "CPUExecutionProvider"])
    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        _service("mps", tmp_path)._init_onnx_session()

    assert _names(calls[0]["providers"]) == ["CPUExecutionProvider"]
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    message = warnings[0]
    assert "offers AzureExecutionProvider, CPUExecutionProvider" in message
    assert "not CoreMLExecutionProvider" in message
    assert "Fix with: pip uninstall -y onnxruntime && pip install onnxruntime" in (
        message
    )
    assert "onnxruntime-gpu" not in message
    assert "libcublasLt" not in message


def test_a_metal_session_that_lost_an_offered_coreml_does_not_blame_the_build(
    fake_ort, tmp_path, caplog
):
    """CoreML offered, started without an error, and absent from the session:
    the build is not the problem, so there is no reinstall advice."""
    fake_ort(
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        dropped={"CoreMLExecutionProvider"},
    )
    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        _service("mps", tmp_path)._init_onnx_session()

    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "offers CoreMLExecutionProvider, but the session started" in warnings[0]
    assert "pip" not in warnings[0]


def test_a_cuda_fallback_still_gets_the_onnxruntime_gpu_remediation(
    fake_ort, tmp_path, caplog
):
    # Positive control: the Metal messages must not take the CUDA advice away
    # from the case #1206 added this warning for, a CUDA provider that is
    # requested and silently missing from the session.
    fake_ort(
        ["CUDAExecutionProvider", "CPUExecutionProvider"],
        dropped={"CUDAExecutionProvider"},
    )
    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        _service("cuda", tmp_path)._init_onnx_session()

    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "pip install onnxruntime-gpu" in warnings[0]
    assert "libcublasLt" in warnings[0]


def test_a_coreml_provider_failure_during_a_run_moves_the_service_to_the_cpu(
    fake_ort, tmp_path, caplog
):
    """EPFail from CoreML during a run: log it, switch to a CPU session, retry
    the batch there once, and keep the CPU session for later batches."""
    calls = fake_ort(
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        run_errors={"CoreMLExecutionProvider": EPFail("Error executing model: -1")},
    )
    service = _service("mps", tmp_path)
    service._init_onnx_session()
    onnx_path = os.path.join(service._model_location, "model.onnx")

    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        first = _tag_batch(service)
        second = _tag_batch(service)

    expected = {"img0.png": ["cat"], "img1.png": ["cat"]}
    assert first == expected
    assert second == expected
    # One failed CoreML run, its retry on the CPU, then the next batch on the
    # CPU without trying CoreML again.
    assert fake_ort.runs == [
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert [_names(c["providers"]) for c in calls] == [
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        ["CPUExecutionProvider"],
    ]
    assert service._ort_sess.get_providers() == ["CPUExecutionProvider"]

    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    message = warnings[0]
    assert "failed during a run" in message
    assert onnx_path in message
    assert "EPFail: Error executing model: -1" in message
    assert "CPUExecutionProvider session" in message


def test_after_a_coreml_run_failure_a_reload_stays_on_the_cpu(
    fake_ort, tmp_path, caplog
):
    """The idle sweep unloads WD14; the next load must not go back to CoreML."""
    calls = fake_ort(
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        run_errors={"CoreMLExecutionProvider": EPFail("Error executing model: -1")},
    )
    service = _service("mps", tmp_path)
    service._init_onnx_session()
    _tag_batch(service)

    service.unload()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=wd14.logger.name):
        service._init_onnx_session()
        result = _tag_batch(service)

    assert result == {"img0.png": ["cat"], "img1.png": ["cat"]}
    assert [_names(c["providers"]) for c in calls][1:] == [
        ["CPUExecutionProvider"],
        ["CPUExecutionProvider"],
    ]
    assert fake_ort.runs[-1] == "CPUExecutionProvider"
    # The reload says why it is on the CPU, without a second warning.
    assert _warnings(caplog) == []
    assert "CoreMLExecutionProvider failed earlier" in caplog.text


def test_a_non_provider_error_during_a_coreml_run_does_not_switch_to_the_cpu(
    fake_ort, tmp_path, caplog
):
    """A bad input is not CoreML failing: the batch fails and the session
    stays on CoreML."""
    calls = fake_ort(
        ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        run_errors={
            "CoreMLExecutionProvider": InvalidArgument("Got invalid dimensions")
        },
    )
    service = _service("mps", tmp_path)
    service._init_onnx_session()

    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        result = _tag_batch(service)

    assert result is None
    assert "Got invalid dimensions" in caplog.text
    assert len(calls) == 1
    assert fake_ort.runs == ["CoreMLExecutionProvider"]
    assert service._ort_sess.get_providers()[0] == "CoreMLExecutionProvider"
    assert _warnings(caplog) == []


def test_a_cuda_epfail_during_a_run_is_left_to_onnxruntime(fake_ort, tmp_path):
    """CUDA keeps onnxruntime's own run() retry, and WD14 adds nothing."""
    calls = fake_ort(
        ["CUDAExecutionProvider", "CPUExecutionProvider"],
        run_errors={"CUDAExecutionProvider": EPFail("CUDA failure 700")},
    )
    service = _service("cuda", tmp_path)
    service._init_onnx_session()

    assert _tag_batch(service) == {"img0.png": ["cat"], "img1.png": ["cat"]}
    assert len(calls) == 1
    assert fake_ort.runs == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_an_epfail_that_reaches_wd14_from_a_cuda_session_is_not_switched(
    fake_ort, tmp_path
):
    """onnxruntime retries only once; an EPFail on that retry reaches WD14,
    which moves only a CoreML session to the CPU."""
    calls = fake_ort(
        ["CUDAExecutionProvider", "CPUExecutionProvider"],
        run_errors={
            "CUDAExecutionProvider": EPFail("CUDA failure 700"),
            "CPUExecutionProvider": EPFail("CPU failure"),
        },
    )
    service = _service("cuda", tmp_path)
    service._init_onnx_session()

    assert _tag_batch(service) is None
    assert len(calls) == 1


def _one_node_model(tmp_path):
    """Write a Relu graph where WD14 looks for ``model.onnx``."""
    onnx = pytest.importorskip("onnx")
    helper = onnx.helper
    model_dir = tmp_path / wd14.WD14_HF_REPO.replace("/", "_")
    model_dir.mkdir()
    graph = helper.make_graph(
        [helper.make_node("Relu", ["x"], ["y"])],
        "tiny",
        [helper.make_tensor_value_info("x", onnx.TensorProto.FLOAT, [1, 4])],
        [helper.make_tensor_value_info("y", onnx.TensorProto.FLOAT, [1, 4])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    onnx.save(model, str(model_dir / "model.onnx"))


_needs_coreml = pytest.mark.skipif(
    "CoreMLExecutionProvider" not in onnxruntime.get_available_providers(),
    reason="this onnxruntime build offers no CoreML provider",
)


@_needs_coreml
def test_the_installed_onnxruntime_starts_coreml_with_the_shipped_options(
    tmp_path, caplog, capsys
):
    """Positive control for the test below: the real options, with
    ``enable_fallback=0``, give a CoreML session that computes correctly."""
    _one_node_model(tmp_path)
    service = _service("mps", tmp_path)
    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        service._init_onnx_session()
        x = np.array([[-1.0, 2.0, -3.0, 4.0]], dtype=np.float32)
        y = service._run_session(x)

    assert service._ort_sess.get_providers() == [
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert _warnings(caplog) == []
    assert "EP Error" not in capsys.readouterr().out
    np.testing.assert_array_equal(y, np.maximum(x, 0.0))


@_needs_coreml
def test_the_installed_onnxruntime_raises_instead_of_falling_back(
    tmp_path, monkeypatch, caplog, capsys
):
    """The real onnxruntime, a one-node graph and a CoreML option it rejects.

    Proves ``enable_fallback=0`` reaches the installed onnxruntime: its own
    fallback would print "EP Error" and return a CPU session with nothing
    logged. The rejected option fails before CoreML compiles anything.
    """
    _one_node_model(tmp_path)
    bad_options = {"ModelFormats": "MLProgram"}
    monkeypatch.setattr(wd14, "_COREML_OPTIONS", bad_options)
    monkeypatch.setattr(
        wd14,
        "_COREML_PROVIDERS",
        (("CoreMLExecutionProvider", bad_options), "CPUExecutionProvider"),
    )
    service = _service("mps", tmp_path)
    with caplog.at_level(logging.WARNING, logger=wd14.logger.name):
        service._init_onnx_session()

    assert "EP Error" not in capsys.readouterr().out
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "Unknown option: ModelFormats" in warnings[0]
    assert service._ort_sess.get_providers() == ["CPUExecutionProvider"]
    x = np.array([[-1.0, 2.0, -3.0, 4.0]], dtype=np.float32)
    y = service._ort_sess.run(None, {service._input_name: x})[0]
    np.testing.assert_array_equal(y, np.maximum(x, 0.0))
