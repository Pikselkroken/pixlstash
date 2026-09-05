"""The host must actually use the plugin lifecycle methods it declares (#967).

Two gaps this pins, both "the contract declares a method and nothing calls it":

* ``unload()`` is abstract on ``TaggerPlugin`` - every plugin implements it and
  nothing used to call it, so a plugin's model stayed resident for the life of
  the process and *Keep models in memory = off* could not free it. That is a
  multi-GB problem for a VLM captioner. The registry walk now runs from the
  vault's idle sweep, which is the process-wide decision; hanging it off
  ``InferenceEngine.close()`` would have made reaping a throwaway CPU spillover
  engine unload the *GPU* engine's plugins, because the registry is bound to
  the vault's engine and the spillover reap runs before every batch.
* ``estimated_vram_mb()`` existed and was never called: the description gate
  charged the Florence-2 figure whichever plugin was about to run, so it billed
  for a model that was not loading and nothing for the one that was.

Deliberately Server-free and model-free: stub plugins and a stub engine are
everything these paths touch (CLAUDE.md, "reuse the environment"), so the file
costs milliseconds.
"""

import types

import pytest

from pixlstash.inference.workflows.description import DescriptionWorkflow
from pixlstash.tagger_plugins import registry
from pixlstash.tagger_plugins.base import TaggerPlugin
from pixlstash.tagger_plugins.florence2 import FLORENCE_PER_IMAGE_VRAM_MB


class _StubPlugin(TaggerPlugin):
    """A plugin that records what the host asked of it."""

    def __init__(
        self,
        name,
        loaded=True,
        raises=False,
        vram=0,
        batch=1,
        supports_descriptions=True,
    ):
        self.name = name
        self.display_name = name
        self.supports_descriptions = supports_descriptions
        self.unload_calls = 0
        self._loaded = loaded
        self._raises = raises
        self._vram = vram
        self._batch = batch
        self.vram_asked_for = None

    def parameter_schema(self):
        return [
            {
                "name": "precision",
                "label": "Precision",
                "type": "string",
                "default": "nf4",
            }
        ]

    def needs_download(self, parameters=None):
        return False

    def init(self, parameters):
        self._loaded = True

    def unload(self):
        self.unload_calls += 1
        if self._raises:
            raise RuntimeError(f"{self.name} refuses to unload")
        self._loaded = False

    def is_loaded(self):
        return self._loaded

    def effective_batch_size(self, parameters=None):
        return self._batch

    def estimated_vram_mb(self, image_count, parameters=None):
        self.vram_asked_for = (image_count, parameters)
        if self._raises:
            raise RuntimeError(f"{self.name} cannot estimate")
        return self._vram

    def generate_descriptions(self, image_paths, parameters, stop_event=None):
        return {path: "a caption" for path in image_paths}


@pytest.fixture
def install_plugins(monkeypatch):
    """Install *plugins* as the process-wide registry for one test."""

    def _install(*plugins):
        manager = registry.TaggerPluginManager(user_dir=None, first_party=[])
        manager.reload()
        manager._plugins = {plugin.name: plugin for plugin in plugins}
        monkeypatch.setattr(registry, "_manager", manager)
        return manager

    return _install


# ----------------------------------------------------------------------
# 1. unload() reaches third-party plugins
# ----------------------------------------------------------------------


def test_the_idle_sweep_releases_a_loaded_plugin(install_plugins):
    """The blocker: a resident plugin model that nothing could ever free."""
    captioner = _StubPlugin("example_captioner")
    install_plugins(captioner)

    registry.unload_loaded_tagger_plugins()

    assert captioner.unload_calls == 1, (
        "the registry walk left the plugin resident; 'Keep models in memory = "
        "off' then cannot free a multi-GB VLM"
    )
    assert not captioner.is_loaded()


def test_a_plugin_that_holds_nothing_is_not_unloaded(install_plugins):
    """``is_loaded()`` gates the call, which is what keeps the walk off a
    built-in wrapper whose service the engine has already released - and off a
    wrapper with no service bound at all, whose ``unload()`` would raise."""
    cold = _StubPlugin("example_cold", loaded=False)
    install_plugins(cold)

    registry.unload_loaded_tagger_plugins()

    assert cold.unload_calls == 0


def test_an_unbound_builtin_wrapper_is_skipped_rather_than_raising(install_plugins):
    """The real shape of the case above: ``WD14Plugin.unload()`` goes through a
    ``service`` property that raises when nothing is bound, and ``is_loaded()``
    is what stops it being reached."""
    from pixlstash.tagger_plugins.wd14 import WD14Plugin

    wrapper = WD14Plugin()
    assert wrapper._service is None, "an unbound wrapper is the case under test"
    assert not wrapper.is_loaded()
    with pytest.raises(RuntimeError):
        wrapper.unload()  # what the walk must never reach

    install_plugins(wrapper)
    registry.unload_loaded_tagger_plugins()  # must not raise


def test_one_plugin_raising_does_not_strand_the_rest(install_plugins):
    """Guarded per plugin: one bad plugin must not keep the library resident."""
    bad = _StubPlugin("example_bad", raises=True)
    good = _StubPlugin("example_good")
    install_plugins(bad, good)

    registry.unload_loaded_tagger_plugins()

    assert bad.unload_calls == 1
    assert good.unload_calls == 1, "the plugin after the raising one was stranded"


def test_unload_never_builds_the_registry(monkeypatch):
    """An unload that imports every plugin module to find something to free is
    the opposite of the thing being asked for."""
    monkeypatch.setattr(registry, "_manager", None)

    def _explode(*args, **kwargs):
        raise AssertionError("the unload walk loaded the plugin registry")

    monkeypatch.setattr(registry, "TaggerPluginManager", _explode)

    registry.unload_loaded_tagger_plugins()

    assert registry._manager is None


def test_a_half_built_registry_is_left_alone(install_plugins):
    """``get_tagger_plugin_manager`` publishes the singleton *before* it loads,
    so there is a window where the manager exists and holds nothing. Walking it
    would block on the import lock and then re-enter ``reload()``."""
    manager = install_plugins(_StubPlugin("example_captioner"))
    manager._loaded = False

    def _explode():
        raise AssertionError("the unload walk re-entered reload()")

    manager.reload = _explode

    registry.unload_loaded_tagger_plugins()


def test_closing_a_spillover_engine_does_not_touch_the_registry(install_plugins):
    """``DescriptionTask``/``TagTask`` reap an idle CPU engine before every
    batch. The registry is bound to the *vault's* engine, so a per-engine
    unload walk would free the GPU model the batch is about to use."""
    from pixlstash.inference.model_lifecycle import ModelLifecycleManager

    captioner = _StubPlugin("example_captioner")
    install_plugins(captioner)

    ModelLifecycleManager(device="cpu").aggressive_unload()

    assert captioner.unload_calls == 0, (
        "closing one engine unloaded the process-wide registry; that is the "
        "GPU engine's Florence-2 and JoyCaption on the spillover reap path"
    )


# ----------------------------------------------------------------------
# 2. estimated_vram_mb() is consulted
# ----------------------------------------------------------------------


def _engine(active_plugin, florence_loaded=False):
    """A stub with the attributes DescriptionWorkflow.estimate_vram_mb reads."""
    florence = types.SimpleNamespace(
        set_model_variant=lambda variant: None,
        description_batch_size=lambda: 4,
        is_loaded=lambda: florence_loaded,
        base_vram_mb=900,
    )
    return types.SimpleNamespace(
        device="cuda",
        florence_service=florence,
        florence_model_variant="base",
        tagger_settings={
            "active_description_plugin": active_plugin,
            "plugins": {"example_captioner": {"params": {"precision": "bf16"}}},
        },
    )


_FLORENCE_COLD_MB = 900 + FLORENCE_PER_IMAGE_VRAM_MB * 4  # base + scratch * batch


def test_estimate_asks_the_active_description_plugin(install_plugins):
    """The blocker: charging the Florence figure for a run that never loads
    Florence lets the scheduler start a second model alongside and OOM."""
    plugin = _StubPlugin("example_captioner", vram=7000, batch=2)
    install_plugins(plugin)
    workflow = DescriptionWorkflow(_engine("example_captioner"), image_root=None)

    assert workflow.estimate_vram_mb(8) == 7000
    assert plugin.vram_asked_for[0] == 2, (
        "the estimate must be capped at the plugin's own batch size; only one "
        "batch is resident at a time"
    )
    assert plugin.vram_asked_for[1]["precision"] == "bf16", (
        "the plugin must be asked with the parameters it will actually run with"
    )


def test_estimate_follows_an_engine_override(install_plugins):
    """A batch dispatched to an overridden plugin is billed for that plugin."""
    plugin = _StubPlugin("example_captioner", vram=7000, batch=8)
    install_plugins(plugin)
    workflow = DescriptionWorkflow(_engine("florence2"), image_root=None)

    assert workflow.estimate_vram_mb(4, plugin_name="example_captioner") == 7000
    assert workflow.estimate_vram_mb(4) == _FLORENCE_COLD_MB, (
        "with no override the configured active plugin decides, and that is "
        "Florence-2 here"
    )


def test_detection_is_still_billed_for_florence(install_plugins):
    """DetectionTask borrows this estimate but always runs Florence-2, whatever
    plugin is configured to caption - so it names the plugin explicitly."""
    plugin = _StubPlugin("example_captioner", vram=7000, batch=8)
    install_plugins(plugin)
    workflow = DescriptionWorkflow(_engine("example_captioner"), image_root=None)

    assert workflow.estimate_vram_mb(8, plugin_name="florence2") == _FLORENCE_COLD_MB
    assert plugin.vram_asked_for is None


def test_a_plugin_that_cannot_caption_is_billed_for_florence(install_plugins):
    """``generate_batch`` falls back to Florence-2 for this plugin, so the
    Florence figure is the one that describes what will actually load."""
    install_plugins(
        _StubPlugin("example_captioner", vram=7000, supports_descriptions=False)
    )
    workflow = DescriptionWorkflow(_engine("example_captioner"), image_root=None)

    assert workflow.estimate_vram_mb(8) == _FLORENCE_COLD_MB


@pytest.mark.parametrize("declines", ["returns_zero", "raises"])
def test_a_plugin_that_declines_to_answer_leaves_the_old_estimate(
    install_plugins, declines
):
    """Unchanged behaviour, and deliberately so (#967): the plugin *does* run
    here, so the Florence figure is wrong - but the host cannot invent a number
    for a model it knows nothing about, and a plugin that overrides the method
    gets a correct budget. ``plugin_template.py`` says as much to authors."""
    plugin = _StubPlugin("example_captioner", raises=(declines == "raises"), vram=0)
    install_plugins(plugin)
    workflow = DescriptionWorkflow(_engine("example_captioner"), image_root=None)

    assert workflow.estimate_vram_mb(8) == _FLORENCE_COLD_MB
    assert plugin.vram_asked_for is not None, "the plugin must still be asked"


def test_estimate_stays_zero_on_cpu(install_plugins):
    """No VRAM to budget: the plugin must not be consulted at all."""
    plugin = _StubPlugin("example_captioner", vram=7000)
    install_plugins(plugin)
    engine = _engine("example_captioner")
    engine.device = "cpu"

    assert DescriptionWorkflow(engine, image_root=None).estimate_vram_mb(8) == 0
    assert plugin.vram_asked_for is None


def test_the_vault_idle_sweep_is_the_caller(install_plugins):
    """Wiring check: the walk is reached from the *Keep models in memory = off*
    sweep, and only once every worker is idle - which is what keeps a plugin's
    unload from landing on top of its own in-flight load."""
    from pixlstash.vault import Vault

    captioner = _StubPlugin("example_captioner")
    install_plugins(captioner)

    fake_vault = types.SimpleNamespace(
        _keep_models_in_memory=False,
        _engine=types.SimpleNamespace(aggressive_unload=lambda: None),
        _last_aggressive_unload_at=0.0,
        AGGRESSIVE_UNLOAD_INTERVAL=Vault.AGGRESSIVE_UNLOAD_INTERVAL,
        _disable_background_workers=True,
        _task_runner=types.SimpleNamespace(has_active_gpu_tasks=lambda: False),
    )
    busy = {"tagging": {"running": True, "status": "running", "remaining": 5}}

    Vault._maybe_aggressive_unload(fake_vault, busy)
    assert captioner.unload_calls == 0, "a busy worker must not lose its model"

    # An interactive task runs outside the planner's in-flight counts, so the
    # snapshot reads idle while a caption batch is executing; the runner's own
    # active set is what keeps the model under it (#1162).
    fake_vault._task_runner = types.SimpleNamespace(has_active_gpu_tasks=lambda: True)
    Vault._maybe_aggressive_unload(fake_vault, {})
    assert captioner.unload_calls == 0, "a running task must not lose its model"

    fake_vault._task_runner = types.SimpleNamespace(has_active_gpu_tasks=lambda: False)
    Vault._maybe_aggressive_unload(fake_vault, {})
    assert captioner.unload_calls == 1


def test_joycaption_charges_for_a_cold_start():
    """The shipped VLM is the case the budget exists for: it used to return 0
    while its weights were merely not loaded yet, which - now that the host
    reads 0 as "no answer" - would bill ~1 GB of Florence for the 8 GB it is
    about to allocate."""
    from pixlstash.tagger_plugins.joycaption import (
        _BASE_VRAM_MB,
        JoyCaptionPlugin,
    )

    cold = JoyCaptionPlugin()
    assert cold._service is None, "a plugin before setup() is the case under test"
    assert cold.estimated_vram_mb(1) >= _BASE_VRAM_MB, (
        "a cold JoyCaption billed nothing for the model it is about to load"
    )

    cold.setup(device="cpu")
    cold._service._model_device = "cpu"
    assert cold.estimated_vram_mb(1) == 0, (
        "a model already sitting on the CPU occupies no VRAM"
    )
