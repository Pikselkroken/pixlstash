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
    # The default leads, because the Train dialog preselects the first.
    assert ids == [
        "zimage-turbo",
        "chroma1-base",
        "flux2-klein-9b-base",
        "krea2-raw",
        "minimax-h3-image",
        "minimax-h3-motion",
        "moss-image-base",
    ]
    for preset in presets.list_presets():
        assert os.path.isfile(presets.template_path(preset))


def test_only_the_h3_motion_recipe_trains_on_clips():
    """The dataset builder hands a recipe the set's clips only when ai-toolkit
    reads its dataset as video; every still-picture recipe would drop them."""
    clip_recipes = [p["id"] for p in presets.list_presets() if presets.trains_clips(p)]
    assert clip_recipes == ["minimax-h3-motion"]
    motion = presets.get_preset("minimax-h3-motion")
    # Clips are read at 24 fps and snapped to the VAE's 17n + 5 frames.
    assert motion["locked"]["datasets[*].fps"] == 24
    assert motion["locked"]["datasets[*].auto_frame_count"] is True


def test_h3_keeps_its_pre_quantised_formats_and_its_distillation_handling():
    """H3 ships int8 ConvRot and nvfp4 weights; any other qtype re-quantises
    them on load. It is guidance-distilled, so both recipes train with
    ai-toolkit's v3 adapter, which needs no contrastive guidance (#2295), and
    sound is a choice only for clips."""
    for pid in ("minimax-h3-image", "minimax-h3-motion"):
        preset = presets.get_preset(pid)
        values = presets.resolved_values(_resolve(preset, vram_mb=100_000, ram_mb=None))
        assert (values["model.qtype"], values["model.qtype_te"]) == (
            "convrot8",
            "nvfp4",
        )
        locked = preset["locked"]
        assert "train.do_guidance_loss" not in locked
        assert "train.guidance_loss_target" not in locked
        assert locked["model.assistant_lora_path"] == (
            "ostris/minimax_h3_training_adapter/"
            "minimax_h3_training_adapter_v3.safetensors"
        )
        assert ("datasets[*].do_audio" in values) == (pid == "minimax-h3-motion")
    motion = presets.get_preset("minimax-h3-motion")
    for sound in (True, False):
        pins = presets.validate_pins(motion, {"datasets[*].do_audio": sound})
        values = presets.resolved_values(
            _resolve(motion, vram_mb=100_000, ram_mb=None, pins=pins)
        )
        assert values["datasets[*].do_audio"] is sound


def test_chroma_has_no_profile_below_8_bit():
    """ai-toolkit's Chroma loads its bf16 transformer onto the GPU to quantise
    it and cannot offload layers, so saving memory cannot go under q8."""
    chroma = presets.get_preset("chroma1-base")
    with pytest.raises(presets.NoProfileFits) as raised:
        _resolve(chroma, fit="save_memory", vram_mb=_16GB)
    assert raised.value.budget == "vram"
    assert raised.value.need_mb == chroma["hardware_profiles"]["q8"]["vram_mb"]
    assert _resolve(chroma, fit="save_memory")["profile"] == "q8"
    # Pinning an offload on is refused, not reported as applied and ignored.
    for pin in ({"model.layer_offloading": True}, {"model.low_vram": True}):
        with pytest.raises(presets.NoProfileFits) as raised:
            _resolve(chroma, pins=pin)
        assert raised.value.budget == "profile"


def test_moss_is_offered_only_by_a_checkout_with_its_model_code(tmp_path):
    """ai-toolkit 0.12.26 has no moss_image folder, so the recipe stays listed
    but not offered until the checkout is updated to one that has it."""
    moss = presets.get_preset("moss-image-base")
    root = tmp_path / "extensions_built_in" / "diffusion_models"
    (root / "z_image").mkdir(parents=True)
    archs = presets.checkout_architectures(str(tmp_path))
    assert not presets.offered(moss, archs, "0.13.28")
    (root / "moss_image").mkdir()
    archs = presets.checkout_architectures(str(tmp_path))
    assert presets.offered(moss, archs, "0.13.28")


def test_moss_needs_the_ai_toolkit_that_trains_its_base_model():
    """#2295: moss_image arrived in 0.13.27, but the Base checkpoint and the
    fresh-cache tokenizer fix in 0.13.28, so the folder alone is not enough."""
    moss = presets.get_preset("moss-image-base")
    archs = ["moss_image"]
    assert presets.offered(moss, archs, "0.13.28")
    assert presets.offered(moss, archs, "0.14.0")
    assert presets.offered(moss, archs, "1.0")
    for older in ("0.13.27", "0.12.99", None, "dev"):
        assert not presets.offered(moss, archs, older), older
    reason = presets.not_offered_because(moss, archs, "0.13.27")
    assert "0.13.28" in reason and "0.13.27" in reason
    # A recipe with no minimum still asks only for its model folder.
    zimage = presets.get_preset("zimage-turbo")
    assert presets.offered(zimage, ["z_image"], None)


def test_a_recipe_minimum_must_be_a_version():
    preset = copy.deepcopy(presets.get_preset("moss-image-base"))
    preset["min_ai_toolkit"] = "latest"
    with pytest.raises(presets.PresetError, match="min_ai_toolkit"):
        presets.validate_preset(preset)


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


def test_a_recipe_cannot_keep_enough_checkpoints_to_fill_the_disk():
    """#2295: an unsigned update could set save_every=1 with a huge keep count;
    the keep count is what bounds the disk, so it has a small ceiling."""
    check_tunable = presets.check_tunable
    check_tunable("save.max_step_saves_to_keep", 24)
    with pytest.raises(TunableOverrideError):
        check_tunable("save.max_step_saves_to_keep", 25)
    with pytest.raises(TunableOverrideError):
        check_tunable("save.max_step_saves_to_keep", 1000)
