#!/usr/bin/env python3
"""Tests for the configurable Florence-2 checkpoint (issue #512).

These tests never load a model: they exercise the variant plumbing only —
the service's variant switch, the VRAM figure the gate charges, the plugin
parameter schema, and the engine property that reads the user's setting.
"""

from types import SimpleNamespace

import pytest

from pixlstash.inference.engine import InferenceEngine
from pixlstash.tagger_plugins.florence2 import (
    DEFAULT_FLORENCE_VARIANT,
    FLORENCE_BASE_VRAM_MB,
    FLORENCE_LARGE_FT_VRAM_MB,
    FLORENCE_MODEL_VARIANTS,
    Florence2Plugin,
    Florence2Service,
)


def _service(**kwargs) -> Florence2Service:
    return Florence2Service(device="cpu", **kwargs)


class TestVariantRegistry:
    def test_default_is_base(self):
        assert DEFAULT_FLORENCE_VARIANT == "base"
        assert FLORENCE_MODEL_VARIANTS[DEFAULT_FLORENCE_VARIANT]["model"].endswith(
            "Florence-2-base"
        )

    def test_every_variant_pins_a_revision(self):
        # An unpinned HuggingFace ref is a silent supply-chain change.
        for key, spec in FLORENCE_MODEL_VARIANTS.items():
            assert spec.get("revision"), f"variant {key!r} has no pinned revision"
            assert spec.get("model")
            assert isinstance(spec.get("vram_mb"), int)
            assert spec.get("label")


class TestServiceVariantSwitch:
    def test_defaults_to_base(self):
        svc = _service()
        assert svc.model_variant == "base"
        assert svc.base_vram_mb == FLORENCE_BASE_VRAM_MB
        assert svc._model_name == "florence-community/Florence-2-base"

    def test_switch_updates_model_revision_and_vram(self):
        svc = _service()
        svc.set_model_variant("large-ft")
        assert svc.model_variant == "large-ft"
        assert svc._model_name == "florence-community/Florence-2-large-ft"
        assert svc._model_revision == FLORENCE_MODEL_VARIANTS["large-ft"]["revision"]
        assert svc.base_vram_mb == FLORENCE_LARGE_FT_VRAM_MB

    def test_switch_unloads_a_resident_checkpoint(self):
        svc = _service()
        svc._model = object()
        svc._processor = object()
        assert svc.is_loaded()
        svc.set_model_variant("large-ft")
        assert not svc.is_loaded()
        assert svc._model is None
        assert svc._processor is None

    def test_switching_to_the_same_variant_keeps_the_model_loaded(self):
        svc = _service()
        svc._model = object()
        svc._processor = object()
        svc.set_model_variant("base")
        assert svc.is_loaded()

    def test_unknown_variant_is_ignored_not_applied(self):
        svc = _service()
        svc.set_model_variant("does-not-exist")
        assert svc.model_variant == "base"
        assert svc._model_name == "florence-community/Florence-2-base"

    def test_empty_variant_falls_back_to_default(self):
        svc = _service()
        svc.set_model_variant("large-ft")
        svc.set_model_variant("")
        assert svc.model_variant == DEFAULT_FLORENCE_VARIANT

    def test_state_info_reports_the_variant(self):
        svc = _service()
        svc.set_model_variant("large-ft")
        info = svc.state_info()
        assert info["florence_variant"] == "large-ft"
        assert info["florence_model"] == "florence-community/Florence-2-large-ft"


class TestVramGateFollowsTheVariant:
    @pytest.mark.parametrize(
        "variant,expected",
        [("base", FLORENCE_BASE_VRAM_MB), ("large-ft", FLORENCE_LARGE_FT_VRAM_MB)],
    )
    def test_description_batch_size_charges_the_selected_footprint(
        self, variant, expected
    ):
        seen: list[int] = []

        def vram_cap(base_mb, per_item_mb):
            seen.append(base_mb)
            return 8

        svc = Florence2Service(device="cuda", vram_cap_fn=vram_cap)
        svc.set_model_variant(variant)
        svc.description_batch_size()
        assert seen == [expected]


class TestPluginSchema:
    def test_model_variant_is_a_select_defaulting_to_base(self):
        plugin = Florence2Plugin()
        field = next(
            f for f in plugin.parameter_schema() if f["name"] == "model_variant"
        )
        assert field["type"] == "select"
        assert field["default"] == DEFAULT_FLORENCE_VARIANT
        values = [opt["value"] for opt in field["options"]]
        assert values == list(FLORENCE_MODEL_VARIANTS)

    def test_default_params_include_the_variant(self):
        assert Florence2Plugin().default_params()["model_variant"] == (
            DEFAULT_FLORENCE_VARIANT
        )


class TestEngineReadsTheSetting:
    @staticmethod
    def _variant_for(settings: dict) -> str:
        return InferenceEngine.florence_model_variant.fget(
            SimpleNamespace(_tagger_settings=settings)
        )

    def test_reads_the_configured_variant(self):
        settings = {"plugins": {"florence2": {"params": {"model_variant": "large-ft"}}}}
        assert self._variant_for(settings) == "large-ft"

    def test_defaults_when_unset(self):
        assert self._variant_for({}) == DEFAULT_FLORENCE_VARIANT
        assert self._variant_for({"plugins": {}}) == DEFAULT_FLORENCE_VARIANT
        assert (
            self._variant_for({"plugins": {"florence2": {"params": {}}}})
            == DEFAULT_FLORENCE_VARIANT
        )

    def test_defaults_on_a_malformed_settings_blob(self):
        assert self._variant_for({"plugins": {"florence2": None}}) == (
            DEFAULT_FLORENCE_VARIANT
        )
        assert self._variant_for({"plugins": {"florence2": {"params": None}}}) == (
            DEFAULT_FLORENCE_VARIANT
        )
        assert (
            self._variant_for(
                {"plugins": {"florence2": {"params": {"model_variant": None}}}}
            )
            == DEFAULT_FLORENCE_VARIANT
        )
