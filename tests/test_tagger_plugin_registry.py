"""Tests for TaggerPluginManager and first-party plugin schemas."""

from types import SimpleNamespace

import pytest

from pixlstash.db_models.tag_prediction import feeds_anomaly_score
from pixlstash.inference.workflows.tagging import TaggingWorkflow
from pixlstash.tagger_plugins.base import TaggerPlugin, TagResult
from pixlstash.tagger_plugins.registry import TaggerPluginManager


EXPECTED_PLUGINS = {"wd14", "pixlstash_tagger", "florence2", "joycaption"}
EXPECTED_SCHEMA_KEYS = {
    "name",
    "display_name",
    "description",
    "author",
    "license",
    "models",
    "supports_tags",
    "supports_descriptions",
    "requires_download",
    "parameters",
    "downloaded_artifacts",
    "is_loaded",
}


@pytest.fixture()
def manager():
    mgr = TaggerPluginManager()
    mgr.reload()
    return mgr


def test_all_three_plugins_registered(manager):
    assert set(manager.plugin_names()) == EXPECTED_PLUGINS


def test_plugin_schemas_have_required_keys(manager):
    for schema in manager.list_plugins():
        assert EXPECTED_SCHEMA_KEYS.issubset(schema.keys()), (
            f"Plugin '{schema.get('name')}' schema missing keys: "
            f"{EXPECTED_SCHEMA_KEYS - schema.keys()}"
        )


def test_plugin_schema_fields_are_correct_types(manager):
    for schema in manager.list_plugins():
        assert isinstance(schema["name"], str) and schema["name"]
        assert isinstance(schema["display_name"], str)
        assert isinstance(schema["author"], str) and schema["author"]
        assert isinstance(schema["license"], str) and schema["license"]
        assert isinstance(schema["models"], list)
        assert isinstance(schema["supports_tags"], bool)
        assert isinstance(schema["supports_descriptions"], bool)
        assert isinstance(schema["requires_download"], bool)
        assert isinstance(schema["parameters"], list)
        assert isinstance(schema["downloaded_artifacts"], list)
        assert isinstance(schema["is_loaded"], bool)


def test_each_parameter_has_required_keys(manager):
    for schema in manager.list_plugins():
        for param in schema["parameters"]:
            for required_key in ("name", "label", "type", "default"):
                assert required_key in param, (
                    f"Plugin '{schema['name']}' param '{param.get('name')}' "
                    f"missing key '{required_key}'"
                )


def test_wd14_supports_tags_not_descriptions(manager):
    plugin = manager.get_plugin("wd14")
    assert plugin is not None
    assert plugin.supports_tags is True
    assert plugin.supports_descriptions is False


def test_pixlstash_tagger_supports_tags_not_descriptions(manager):
    plugin = manager.get_plugin("pixlstash_tagger")
    assert plugin is not None
    assert plugin.supports_tags is True
    assert plugin.supports_descriptions is False


def test_florence2_supports_descriptions_not_tags(manager):
    plugin = manager.get_plugin("florence2")
    assert plugin is not None
    assert plugin.supports_tags is False
    assert plugin.supports_descriptions is True


def test_is_loaded_false_before_init(manager):
    """Plugins must return False from is_loaded() before setup() is called."""
    for name in manager.plugin_names():
        plugin = manager.get_plugin(name)
        assert plugin.is_loaded() is False, (
            f"Plugin '{name}' unexpectedly reports is_loaded=True before init"
        )


def test_default_params_match_schema_defaults(manager):
    for name in manager.plugin_names():
        plugin = manager.get_plugin(name)
        defaults = plugin.default_params()
        for field in plugin.parameter_schema():
            assert field["name"] in defaults
            assert defaults[field["name"]] == field["default"]


def test_default_tagger_settings_structure(manager):
    settings = manager.default_tagger_settings()
    assert "active_description_plugin" in settings
    assert "plugins" in settings
    # Florence-2 should be set as the default active description plugin.
    assert settings["active_description_plugin"] == "florence2"
    # All registered plugins appear in the plugins dict.
    assert set(settings["plugins"].keys()) == EXPECTED_PLUGINS
    # Tag plugins have an "enabled" key; description-only plugins do not.
    for name, entry in settings["plugins"].items():
        plugin = manager.get_plugin(name)
        if plugin.supports_tags:
            assert "enabled" in entry
        if not plugin.supports_tags:
            assert "enabled" not in entry


def test_fill_defaults_adds_missing_plugin(manager):
    partial = {"active_description_plugin": "florence2", "plugins": {}}
    filled = manager.fill_defaults(partial)
    assert set(filled["plugins"].keys()) == EXPECTED_PLUGINS


def test_fill_defaults_preserves_existing_values(manager):
    partial = {
        "active_description_plugin": "florence2",
        "plugins": {
            "wd14": {"enabled": True, "params": {"threshold": 0.99}},
        },
    }
    filled = manager.fill_defaults(partial)
    assert filled["plugins"]["wd14"]["enabled"] is True
    assert filled["plugins"]["wd14"]["params"]["threshold"] == 0.99


def test_fill_defaults_preserves_unknown_plugin_names(manager):
    """Downgrade safety: unknown plugin entries must survive fill_defaults."""
    partial = {
        "active_description_plugin": None,
        "plugins": {
            "legacy_tagger": {"enabled": False, "params": {}},
        },
    }
    filled = manager.fill_defaults(partial)
    assert "legacy_tagger" in filled["plugins"]


def test_joycaption_supports_both_capabilities(manager):
    plugin = manager.get_plugin("joycaption")
    assert plugin is not None
    assert plugin.supports_tags is True
    assert plugin.supports_descriptions is True


def test_joycaption_schema_has_precision_parameter(manager):
    plugin = manager.get_plugin("joycaption")
    names = [f["name"] for f in plugin.parameter_schema()]
    assert "precision" in names
    assert "temperature" in names
    assert "description_prompt" in names
    assert "tag_prompt" in names


def test_tag_plugin_names_contains_tag_capable(manager):
    tag_names = manager.tag_plugin_names()
    assert "wd14" in tag_names
    assert "pixlstash_tagger" in tag_names
    assert "joycaption" in tag_names
    assert "florence2" not in tag_names


def test_description_plugin_names_contains_florence2(manager):
    desc_names = manager.description_plugin_names()
    assert "florence2" in desc_names
    assert "joycaption" in desc_names
    assert "wd14" not in desc_names
    assert "pixlstash_tagger" not in desc_names


def test_list_errors_is_empty_when_all_loaded(manager):
    assert manager.list_errors() == []


def test_joycaption_schema_has_required_parameters(manager):
    plugin = manager.get_plugin("joycaption")
    names = [f["name"] for f in plugin.parameter_schema()]
    assert "precision" in names
    assert "temperature" in names
    assert "description_prompt" in names
    assert "tag_prompt" in names


def test_joycaption_is_not_loaded_before_init(manager):
    plugin = manager.get_plugin("joycaption")
    assert plugin.is_loaded() is False


# --- Plugin-sourced tag predictions ----------------------------------------------
# No shipped plugin exercises this path: wd14 and pixlstash_tagger both take
# built-in routes through TaggingWorkflow, and the description plugins produce no
# tags.  A stub stands in for the third-party plugin the path exists for.


class _StubTagger(TaggerPlugin):
    """Minimal third-party tag plugin: two scored tags and one unscored."""

    name = "stub_tagger"
    supports_tags = True
    requires_download = False

    def __init__(self, version: str = "2026-01") -> None:
        self._version = version

    def model_version(self) -> str:
        return self._version

    def parameter_schema(self):
        return []

    def needs_download(self, parameters=None) -> bool:
        return False

    def init(self, parameters) -> None:
        pass

    def unload(self) -> None:
        pass

    def is_loaded(self) -> bool:
        return True

    def tag_images(self, image_paths, parameters, preloaded=None, stop_event=None):
        return {
            path: [
                TagResult("watermark", 0.8),
                TagResult("sunset", 0.25),
                TagResult("unscored", None),
            ]
            for path in image_paths
        }


def _workflow(monkeypatch, plugin, active="stub_tagger", tagger_version=43):
    """A TaggingWorkflow whose registry resolves *plugin* by name."""
    manager = SimpleNamespace(get_plugin=lambda n: plugin if n == plugin.name else None)
    monkeypatch.setattr(
        "pixlstash.tagger_plugins.registry.get_tagger_plugin_manager",
        lambda: manager,
    )
    engine = SimpleNamespace(
        device="cpu", pixlstash_tagger_version=lambda: tagger_version
    )
    return TaggingWorkflow(
        engine=engine,
        use_wd14=False,
        use_pixlstash_tagger=(active == "pixlstash_tagger"),
        tagger_settings={"active_tag_plugin": active},
    )


def test_plugin_confidences_reach_the_caller(monkeypatch):
    """The workflow used to reduce results to bare tags and drop every score."""
    workflow = _workflow(monkeypatch, _StubTagger())
    scores = {}

    tags = workflow.tag_images(["/tmp/a.png"], out_raw_scores=scores)

    assert tags["/tmp/a.png"] == ["sunset", "unscored", "watermark"]
    # A tag the plugin declined to score contributes nothing, rather than a 0.0
    # the caller would be unable to tell from a real zero.
    assert scores["/tmp/a.png"] == {"watermark": 0.8, "sunset": 0.25}


def test_plugin_predictions_are_stamped_with_the_plugin_and_version(monkeypatch):
    workflow = _workflow(monkeypatch, _StubTagger("2026-01"))
    assert workflow.active_model_version() == "stub_tagger@2026-01"


def test_a_plugin_that_declares_no_version_is_stamped_unknown(monkeypatch):
    """The default from the ABC. Such rows never go stale - documented, not silent."""
    workflow = _workflow(monkeypatch, _StubTagger(""))
    assert workflow.active_model_version() == "stub_tagger@unknown"


def test_a_plugin_whose_version_raises_does_not_break_tagging(monkeypatch):
    plugin = _StubTagger()
    monkeypatch.setattr(plugin, "model_version", lambda: 1 / 0, raising=False)
    workflow = _workflow(monkeypatch, plugin)
    assert workflow.active_model_version() == "stub_tagger@unknown"


def test_the_built_in_tagger_keeps_its_bare_version(monkeypatch):
    """Unqualified on purpose: it is what feeds_anomaly_score() lets through, and
    changing it would orphan every prediction row already in a vault."""
    workflow = _workflow(
        monkeypatch, _StubTagger(), active="pixlstash_tagger", tagger_version=43
    )
    assert workflow.active_model_version() == "v43"
    assert feeds_anomaly_score(workflow.active_model_version())
