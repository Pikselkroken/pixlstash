"""Tests for the training recipes and the memory-profile resolver."""

from __future__ import annotations

import copy
import os

import pytest

from pixlstash.training import presets
from pixlstash.training.config_render import TUNABLE_KEYS, TunableOverrideError

_ZIT = presets.get_preset("zimage-turbo")

# What the arbiter gives a run on a 24 GB and a 16 GB card: the card less the
# safety margin and a desktop's few hundred MB.
_24GB = 22_800
_16GB = 14_900


def _resolve(preset=_ZIT, **kwargs):
    args = {
        "subject": "person",
        "effort": "standard",
        "fit": "auto",
        "picture_count": 24,
        "vram_mb": _24GB,
        "ram_mb": 60_000,
    }
    args.update(kwargs)
    return presets.resolve(preset, **args)


def test_every_shipped_preset_passes_its_schema():
    """The loader validated them; list them so a broken file fails here."""
    ids = [p["id"] for p in presets.list_presets()]
    assert "zimage-turbo" in ids
    for preset in presets.list_presets():
        assert os.path.isfile(presets.template_path(preset))


def test_table_keys_are_tunable_and_locked_keys_are_not():
    for preset in presets.list_presets():
        assert not set(preset["locked"]) & set(TUNABLE_KEYS)
        tables = [
            preset["defaults"],
            *preset["preview"].values(),
            *preset["subject"].values(),
            *preset["effort"].values(),
            *(p["settings"] for p in preset["hardware_profiles"].values()),
        ]
        for table in tables:
            for key in table:
                if key not in ("steps_per_picture", "min_steps", "max_steps"):
                    assert key in TUNABLE_KEYS, key


@pytest.mark.parametrize(
    "breakage, message",
    [
        (lambda p: p["locked"].update({"train.steps": 1}), "locked key"),
        (lambda p: p["defaults"].update({"model.arch": "flux"}), "not a tunable"),
        (lambda p: p["effort"].pop("strong"), "effort must hold"),
        (lambda p: p["fit"]["auto"].append("nope"), "unknown profile"),
        (
            lambda p: p["hardware_profiles"]["q8"].update({"evidence": "vibes"}),
            "evidence",
        ),
        (
            lambda p: p["hardware_profiles"]["q8"]["settings"].pop("model.quantize"),
            "different keys",
        ),
        (
            lambda p: p["defaults"].update({"datasets[*].resolution": [520]}),
            "multiple of 16",
        ),
        (
            lambda p: p["effort"]["gentle"].update({"min_steps": 9_000}),
            "above max_steps",
        ),
        (lambda p: p["locked"].pop("model.name_or_path"), "model.name_or_path"),
        (lambda p: p["hardware_profiles"]["q8"].pop("max_rank"), "max_rank"),
        (lambda p: p["hardware_profiles"].clear(), "at least one profile"),
        (lambda p: p["fit"]["save_memory"].clear(), "names no profile"),
    ],
)
def test_a_broken_preset_is_refused(breakage, message):
    broken = copy.deepcopy(_ZIT)
    breakage(broken)
    with pytest.raises(presets.PresetError, match=message):
        presets.validate_preset(broken)


def test_a_24gb_card_resolves_to_8_bit_with_the_text_encoder_on_the_gpu():
    result = _resolve(vram_mb=_24GB)
    assert result["profile"] == "q8"
    assert result["preset"] == "zimage-turbo@1"
    assert result["vram_mb"] == 16_000
    values = result["values"]
    assert values["model.quantize"] == {"value": True, "source": "gpu"}
    assert values["train.cache_text_embeddings"] == {"value": False, "source": "gpu"}


def test_a_16gb_card_resolves_to_8_bit_with_captions_encoded_once():
    result = _resolve(vram_mb=_16GB)
    assert result["profile"] == "q8_cached"
    assert result["values"]["train.cache_text_embeddings"]["value"] is True
    assert result["values"]["model.layer_offloading"]["value"] is False


def test_fit_choices_pick_within_their_own_profiles():
    assert _resolve(vram_mb=_24GB, fit="save_memory")["profile"] == "q8_offload"
    assert _resolve(vram_mb=_24GB, fit="best_quality")["profile"] == "bf16_cached"
    assert _resolve(vram_mb=30_000, fit="best_quality")["profile"] == "bf16"


def test_nothing_fitting_says_which_budget_is_short():
    with pytest.raises(presets.NoProfileFits) as vram:
        _resolve(vram_mb=7_000)
    assert vram.value.budget == "vram"
    assert vram.value.need_mb == 10_800
    assert "GPU memory" in str(vram.value)

    with pytest.raises(presets.NoProfileFits) as ram:
        _resolve(vram_mb=_16GB, ram_mb=8_000)
    assert ram.value.budget == "ram"
    assert ram.value.free_mb == 8_000

    # Best quality on a 16 GB card is refused, never quietly made 8-bit.
    with pytest.raises(presets.NoProfileFits) as best:
        _resolve(vram_mb=_16GB, fit="best_quality")
    assert best.value.budget == "vram"


def test_unknown_ram_skips_the_ram_check():
    assert _resolve(vram_mb=_16GB, ram_mb=None)["profile"] == "q8_cached"


def test_a_tier_without_evidence_is_never_offered():
    preset = copy.deepcopy(_ZIT)
    preset["hardware_profiles"]["q8"]["evidence"] = "none"
    assert _resolve(preset, vram_mb=_24GB)["profile"] == "q8_cached"


def test_resolution_rank_and_batch_are_never_lowered_to_fit():
    """A pinned size, rank or batch past every profile's figures is refused,
    not shrunk, however much memory there is."""
    for pins in (
        {"datasets[*].resolution": [1024, 1536]},
        {"network.linear": 64},
        {"train.batch_size": 4},
    ):
        with pytest.raises(presets.NoProfileFits) as exc:
            _resolve(vram_mb=80_000, pins=pins)
        assert exc.value.budget == "profile", pins
    with pytest.raises(TunableOverrideError, match="multiple of 16"):
        _resolve(pins={"datasets[*].resolution": [1000]})


def test_steps_scale_with_the_picture_count_and_are_clamped():
    def steps(**kwargs):
        return _resolve(**kwargs)["values"]["train.steps"]

    assert steps(picture_count=24) == {"value": 2400, "source": "pictures"}
    assert steps(picture_count=3)["value"] == 1200  # standard's floor
    assert steps(picture_count=112)["value"] == 3000  # standard's cap
    assert steps(picture_count=10, effort="strong")["value"] == 1800
    assert steps(picture_count=10, effort="gentle")["value"] == 800


def test_rank_sets_alpha_and_each_value_names_its_source():
    values = _resolve(effort="gentle")["values"]
    assert values["network.linear"] == {"value": 16, "source": "effort"}
    assert values["network.linear_alpha"] == {"value": 16, "source": "effort"}
    assert values["train.lr"]["source"] == "effort"
    assert values["datasets[*].caption_dropout_rate"]["source"] == "subject"
    assert values["datasets[*].resolution"]["source"] == "model"
    assert values["sample.sample_steps"] == {"value": 9, "source": "model"}
    assert presets.PROMPTS_KEY not in values


def test_prompts_written_from_the_captions_come_from_captions_unless_pinned():
    written = ["{{TRIGGER_WORD}} at a cafe", "an old man at a cafe"]
    values = _resolve(prompts=written)["values"]
    assert values[presets.PROMPTS_KEY] == {"value": written, "source": "captions"}
    with pytest.raises(TunableOverrideError, match="prompt"):
        _resolve(prompts=["x" * 2001])
    pinned = _resolve(prompts=written, pins={presets.PROMPTS_KEY: ["only this"]})
    assert pinned["values"][presets.PROMPTS_KEY] == {
        "value": ["only this"],
        "source": "pinned",
    }


def test_pins_win_over_every_derived_value():
    values = _resolve(pins={"network.linear": 8, "train.steps": 777})["values"]
    assert values["train.steps"] == {"value": 777, "source": "pinned"}
    assert values["network.linear"] == {"value": 8, "source": "pinned"}
    # Alpha follows a pinned rank unless it is pinned itself.
    assert values["network.linear_alpha"]["value"] == 8


def test_a_pinned_memory_setting_picks_a_profile_that_has_it():
    """Turning 8-bit off under Auto leases for a full-precision profile, never
    for the 8-bit one Auto would have picked."""
    result = _resolve(vram_mb=_24GB, pins={"model.quantize": False})
    assert result["profile"] == "bf16_cached"
    assert result["vram_mb"] == 16_000
    assert result["values"]["model.quantize"] == {"value": False, "source": "pinned"}
    with pytest.raises(presets.NoProfileFits) as exc:
        _resolve(vram_mb=_16GB, pins={"model.quantize": False})
    assert exc.value.budget == "vram"
    # An offload share no profile was sized for has no known need.
    with pytest.raises(presets.NoProfileFits) as exc:
        _resolve(pins={"model.layer_offloading_transformer_percent": 0.4})
    assert exc.value.budget == "profile"


def test_captions_encoded_once_report_no_caption_dropout():
    """ai-toolkit drops no caption it has cached, so the record says 0."""
    cached = _resolve(vram_mb=_16GB)["values"]
    assert cached["datasets[*].caption_dropout_rate"] == {
        "value": 0.0,
        "source": "gpu",
    }
    resident = _resolve(vram_mb=_24GB)["values"]
    assert resident["datasets[*].caption_dropout_rate"] == {
        "value": 0.05,
        "source": "subject",
    }


def test_pins_refuse_locked_and_unknown_keys_by_name():
    with pytest.raises(TunableOverrideError, match="'model.arch' is fixed"):
        presets.validate_pins(_ZIT, {"model.arch": "flux"})
    with pytest.raises(TunableOverrideError, match="'device'"):
        presets.validate_pins(_ZIT, {"device": "cuda:1"})
    with pytest.raises(TunableOverrideError, match="train.steps"):
        presets.validate_pins(_ZIT, {"train.steps": -1})
    with pytest.raises(TunableOverrideError, match="prompts"):
        presets.validate_pins(_ZIT, {presets.PROMPTS_KEY: []})
    for not_an_object in ([], "", 0):
        with pytest.raises(TunableOverrideError, match="must be an object"):
            presets.validate_pins(_ZIT, not_an_object)
    assert presets.validate_pins(_ZIT, None) == {}
    assert presets.validate_pins(_ZIT, {}) == {}
    assert presets.validate_pins(_ZIT, {"train.steps": 10}) == {"train.steps": 10}


def test_to_patch_nests_flat_keys_under_process_zero():
    patch = presets.to_patch(
        {
            "train.steps": 5,
            "datasets[*].resolution": [512],
            "network.network_kwargs.ignore_if_contains": [],
            presets.PROMPTS_KEY: ["a", "b"],
        }
    )
    process = patch["config"]["process"][0]
    assert process["train"] == {"steps": 5}
    assert process["datasets"] == [{"resolution": [512]}]
    assert process["network"] == {"network_kwargs": {"ignore_if_contains": []}}
    assert process["sample"] == {"samples": [{"prompt": "a"}, {"prompt": "b"}]}


def test_checkout_architectures_reads_the_model_code_folders(tmp_path):
    root = tmp_path / "extensions_built_in" / "diffusion_models"
    for name in ("z_image", "flux2", "__pycache__"):
        (root / name).mkdir(parents=True)
    (root / "__init__.py").write_text("")
    archs = presets.checkout_architectures(str(tmp_path))
    assert archs == ["flux2", "z_image"]
    assert presets.offered(_ZIT, archs)
    assert not presets.offered(_ZIT, ["flux2"])
    assert presets.checkout_architectures(str(tmp_path / "missing")) == []
    assert presets.checkout_architectures(None) == []


# ---------------------------------------------------------------------------
# Measured on this machine (#2120)
# ---------------------------------------------------------------------------


def _profile_of(values):
    """The hardware profile whose settings *values* carry."""
    return next(
        name
        for name, profile in _ZIT["hardware_profiles"].items()
        if all(values.get(k) == v for k, v in profile["settings"].items())
    )


def _lookup(measured: dict):
    """A lookup answering *measured*[profile] as that profile's measurement."""

    def lookup(values):
        name = _profile_of(values)
        return f"fp-{name}", measured.get(name)

    return lookup


def _measurement(training_mb, previews_mb=None, per_update=None):
    peaks = {"training": {"vram_mb": training_mb, "ram_mb": 10_000}}
    if previews_mb is not None:
        peaks["previews"] = {"vram_mb": previews_mb, "ram_mb": None}
    return {
        "phase_peaks": peaks,
        "seconds_per_update": per_update,
        "setup_seconds": 100.0,
        "seconds_per_preview": 4.0,
    }


def test_a_measured_profile_is_admitted_on_what_it_measured():
    """On a 16 GB card the 16 GB seed of q8 does not fit; measured at 12 GB, it
    does, and says so."""
    assert _resolve(vram_mb=_16GB, lookup=_lookup({}))["profile"] == "q8_cached"
    out = _resolve(vram_mb=_16GB, lookup=_lookup({"q8": _measurement(12_000)}))
    assert out["profile"] == "q8"
    assert (out["evidence"], out["vram_mb"]) == ("measured", 13_200)
    assert out["fingerprint"] == "fp-q8"
    assert out["probe"] is False and out["previews_deferred"] is False


def test_a_measured_peak_above_the_seed_moves_to_the_next_profile():
    out = _resolve(lookup=_lookup({"q8": _measurement(21_000)}))
    assert out["profile"] == "q8_cached"
    # Never measured here: the run checks memory and speed first, and a probe
    # that runs out of memory steps to the profiles after it.
    assert out["probe"] is True
    assert out["fallbacks"] == ["q8_offload"]
    assert out["estimate"] == {"seconds": None, "basis": "none"}


def test_without_measurements_nothing_is_probed():
    out = _resolve()
    assert (out["probe"], out["fingerprint"]) == (False, None)
    assert out["fallbacks"] == ["q8_cached", "q8_offload"]


def test_previews_that_do_not_fit_wait_until_training_is_done():
    out = _resolve(
        vram_mb=_16GB,
        lookup=_lookup(
            {"q8": _measurement(12_000, previews_mb=16_000, per_update=2.0)}
        ),
    )
    assert out["profile"] == "q8" and out["previews_deferred"] is True
    assert out["values"]["train.disable_sampling"] == {"value": True, "source": "gpu"}
    assert out["preview_vram_mb"] == 17_600
    steps = out["values"]["train.steps"]["value"]
    assert out["estimate"]["basis"] == "measured"
    assert out["estimate"]["seconds"] > 100 + steps * 2.0
    fits = _resolve(
        lookup=_lookup({"q8": _measurement(12_000, previews_mb=16_000, per_update=2.0)})
    )
    assert fits["previews_deferred"] is False
    assert "train.disable_sampling" not in fits["values"]
    # The owner's pin stands either way: previews kept in training, or put off.
    kept = _resolve(
        vram_mb=_16GB,
        pins={"train.disable_sampling": False},
        lookup=_lookup({"q8": _measurement(12_000, previews_mb=16_000)}),
    )
    assert kept["previews_deferred"] is False
    assert kept["values"]["train.disable_sampling"]["source"] == "pinned"
    put_off = _resolve(pins={"train.disable_sampling": True}, lookup=_lookup({}))
    assert put_off["previews_deferred"] is True


def test_a_forced_profile_is_resolved_alone():
    out = _resolve(profile="q8_offload")
    assert out["profile"] == "q8_offload" and out["fallbacks"] == []
    with pytest.raises(ValueError):
        _resolve(profile="no-such-profile")
