"""Tests for TaggerPluginManager and first-party plugin schemas."""

import pytest

from pixlstash.tagger_plugins.registry import TaggerPluginManager


EXPECTED_PLUGINS = {"wd14", "pixlstash_tagger", "florence2", "joycaption"}
EXPECTED_SCHEMA_KEYS = {
    "name",
    "display_name",
    "description",
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


def test_joycaption_supports_both_capabilities(manager):
    plugin = manager.get_plugin("joycaption")
    assert plugin is not None
    assert plugin.supports_tags is True
    assert plugin.supports_descriptions is True


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
