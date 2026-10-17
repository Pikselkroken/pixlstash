"""Pins for the base-model folding table.

The table is data, so most of it needs no test. These cover the four ways it can
break silently: an alias collision, the normaliser being bypassed, the
containment trap folding FLUX.2 into FLUX.1, and a user's own value being either
lost or duplicated in tab-completion.
"""

import re

import pytest

from pixlstash.services.workflow_card_service import _mark_fields
from pixlstash.utils.known_base_models import (
    COMPANION_LAYOUTS,
    FAMILY_ICONS,
    FAMILY_LABELS,
    KNOWN_BASE_MODELS,
    KNOWN_FINE_TUNES,
    MAKERS,
    _norm,
    completions,
    family_named,
    family_of,
    fold,
    modality_of,
    model_tile,
    suggest,
)


def test_no_alias_collisions():
    # _build_index raises on collision at import; this asserts it stays that way
    # if the table is edited, and that every entry is reachable.
    seen: dict[str, str] = {}
    for canonical, info in KNOWN_BASE_MODELS.items():
        for candidate in (canonical, *info["aliases"]):
            key = _norm(candidate)
            assert seen.get(key, canonical) == canonical, (
                f"{candidate!r} claimed by {seen[key]!r} and {canonical!r}"
            )
            seen[key] = canonical
        assert fold(canonical) == canonical


def test_spacing_and_case_collapse():
    for spelling in (
        "Z-Image Turbo",
        "z image turbo",
        "Z_Image_Turbo",
        "ZIMAGETURBO",
        "z.image.turbo",
        "  Z-Image  Turbo  ",
    ):
        assert fold(spelling) == "Z-Image Turbo", spelling


def test_real_trainer_strings_fold():
    # kohya ss_base_model_version, Civitai baseModel labels, HF repo ids.
    cases = {
        "sdxl_base_v1-0": "SDXL 1.0",
        "sd_v1-5": "SD 1.5",
        "Flux.1 D": "FLUX.1 dev",
        "Pony": "Pony Diffusion V6 XL",
        "black-forest-labs/FLUX.1-dev": "FLUX.1 dev",
        "Tongyi-MAI/Z-Image-Turbo": "Z-Image Turbo",
        "zimage": "Z-Image Base",
    }
    for raw, expected in cases.items():
        assert fold(raw) == expected, raw


def test_unknown_never_folds():
    assert fold("SomeGuysMergeV4") is None
    assert fold("") is None
    assert fold(None) is None


def test_containment_is_offered_never_applied():
    # The trap: 'flux' is a substring of 'flux2'. Silent containment would file
    # every FLUX.2 adapter under FLUX.1, so an inexact match must not fold...
    assert fold("myflux2lora") is None
    # ...but must be offered, longest alias winning.
    assert suggest("myflux2lora")[0] == "FLUX.2"
    # An exact hit has nothing to ask about.
    assert suggest("sdxl") == []


def test_typos_are_suggested():
    assert "Illustrious XL" in suggest("ilustrious")


def test_completions_seed_from_the_known_table():
    assert "SDXL 1.0" in completions()
    # Prefix matches lead, so typing narrows instead of reshuffling.
    assert completions("flux")[0].startswith("FLUX")


def test_completions_merge_user_values_without_duplicating():
    user_values = ["SomeGuysMergeV4", "sdxl", "  ", "Z_Image_Turbo"]
    result = completions(extra=user_values)
    # The user's own vocabulary is a completion target immediately.
    assert "SomeGuysMergeV4" in result
    # Values that fold to something known are not shown a second time.
    assert "sdxl" not in result
    assert "Z_Image_Turbo" not in result
    assert result.count("SDXL 1.0") == 1
    assert result.count("Z-Image Turbo") == 1


def test_family_is_architecture_not_name():
    # The reason family is stored rather than derived from the label: Pony V6 is
    # SDXL-architecture, Pony V7 is not, despite sharing a name.
    assert KNOWN_BASE_MODELS["Pony Diffusion V6 XL"]["family"] == "sdxl"
    assert KNOWN_BASE_MODELS["Pony V7"]["family"] == "auraflow"


def test_closed_models_are_marked():
    # UI that implies local loading filters on this.
    assert KNOWN_BASE_MODELS["Midjourney V8.1"]["family"] == "closed"
    assert {"Wan 2.2", "LTXV 13B", "HunyuanVideo 1.5"} <= {
        name for name, i in KNOWN_BASE_MODELS.items() if i["modality"] == "video"
    }


def test_minimax_h3_has_a_family():
    """A workflow on it said its base model was not known, though the shelf
    named it."""
    assert family_of("MiniMax H3") == family_of("minimax_h3") == "minimax_h3"
    assert modality_of("MiniMax H3") == "video"


def test_companion_layouts_join_the_two_family_vocabularies():
    """Keyed by a base-model family, valued in `family_from_header`'s layouts:
    a key the table above never produces, or a layout the scanner never stores,
    is a declaration that can never fire."""
    families = {info["family"] for info in KNOWN_BASE_MODELS.values()}
    for family, kinds in COMPANION_LAYOUTS.items():
        assert family in families, family
        assert kinds and set(kinds) <= {"vae", "text_encoder"}, family
        for layout in set().union(*kinds.values()):
            assert re.fullmatch(r"vae_\d+ch|clip_[lhg]|(um)?t5_xxl", layout), layout


def test_every_family_has_one_label_that_names_it_back():
    """A family with no label is a dropdown entry nobody can read, and a label
    some other family's alias folds to would narrow to the wrong one."""
    families = {info["family"] for info in KNOWN_BASE_MODELS.values()}
    assert set(FAMILY_LABELS) == families
    assert len({_norm(label) for label in FAMILY_LABELS.values()}) == len(families)
    for family, label in FAMILY_LABELS.items():
        assert family_of(label) in (None, family), label
        assert family_named(label) == family_named(label.upper()) == family
        assert family_named(family) == family
    for name, info in KNOWN_BASE_MODELS.items():
        assert family_named(name) == info["family"], name
    assert family_named("not a family") is None


# --- Tiles (#1951) -----------------------------------------------------------


def test_every_open_base_model_has_a_tile_and_no_closed_one_does():
    """A closed model never exists as a file, so nothing draws it."""
    for canonical, info in KNOWN_BASE_MODELS.items():
        if info["family"] == "closed":
            assert "mark" not in info, canonical
            continue
        assert 1 <= len(info["mark"]) <= 2, canonical
        assert all(line and line.strip() == line for line in info["mark"])
        assert info.get("maker") is None or info["maker"] in MAKERS, canonical
        assert info.get("icon") is None or info["icon"] in FAMILY_ICONS, canonical


def _contrast_with_white(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    luminance = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return 1.05 / (luminance + 0.05)


def test_every_tile_colour_carries_white_text_at_four_to_one():
    stops = [
        *(stop for colours in MAKERS.values() for stop in colours),
        *FAMILY_ICONS.values(),
        *(stop for tile in KNOWN_FINE_TUNES.values() for stop in tile["colors"]),
    ]
    for stop in stops:
        assert re.fullmatch(r"#[0-9a-f]{6}", stop), stop
        assert _contrast_with_white(stop) >= 4.0, stop


SDXL = ["#9d39ff", "#e80000"]
GRAPHITE = ["#484c53"]


@pytest.mark.parametrize(
    ("canonical", "filename", "name", "checkpoint", "mark", "tab", "colours", "icon"),
    [
        # The release, told from its refiner by the role word in its filename.
        (
            "SDXL 1.0",
            "sd_xl_base_1.0.safetensors",
            None,
            True,
            ["SDXL", "1.0"],
            "BASE",
            SDXL,
            None,
        ),
        (
            "SDXL 1.0",
            "sd_xl_refiner_1.0.safetensors",
            None,
            True,
            ["SDXL", "1.0"],
            "REFI",
            SDXL,
            None,
        ),
        # A fine-tune run together with its base's name is still a fine-tune.
        (
            "SDXL 1.0",
            "sdxlUnstableDiffusers_v11.safetensors",
            None,
            True,
            ["SDXL", "UN11"],
            None,
            SDXL,
            None,
        ),
        # Juggernaut XL has a mark of its own, base model identified or not;
        # a LoRA of that name is only named like it.
        (
            "SDXL 1.0",
            "juggernautXL_v9.safetensors",
            None,
            True,
            ["J"],
            None,
            ["#6b46c1"],
            None,
        ),
        (
            None,
            "juggernautXL_v9.safetensors",
            None,
            True,
            ["J"],
            None,
            ["#6b46c1"],
            None,
        ),
        (
            "SDXL 1.0",
            "jugg.safetensors",
            "Juggernaut XL v9",
            False,
            ["SDXL", "JU9"],
            None,
            SDXL,
            None,
        ),
        # The version comes from the file; the role from the entry.
        (
            "Qwen-Image-Edit",
            "qwen_image_edit_2511_bf16.safetensors",
            None,
            True,
            ["Qwen", "2511"],
            "EDIT",
            ["#615ced"],
            None,
        ),
        # A speed LoRA: its own initials, and its step count beside the bolt.
        (
            "Qwen-Image-Edit",
            "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
            None,
            False,
            ["Qwen", "LI"],
            "⚡4",
            ["#615ced"],
            None,
        ),
        (
            "Qwen-Image-Edit",
            "qwen_edit_2511_multiple_angles.safetensors",
            None,
            False,
            ["Qwen", "MA"],
            None,
            ["#615ced"],
            None,
        ),
        (
            "FLUX.1 dev",
            "flux1-dev-fp8.safetensors",
            None,
            True,
            ["F1D"],
            None,
            GRAPHITE,
            None,
        ),
        (
            "FLUX.1 schnell",
            "flux1-schnell.safetensors",
            None,
            True,
            ["F1S"],
            "⚡",
            GRAPHITE,
            None,
        ),
        (
            "Z-Image Turbo",
            "z_image_turbo_bf16.safetensors",
            None,
            True,
            ["ZIT"],
            "⚡",
            ["#dc5100"],
            None,
        ),
        # The base's own words, across the family, are not the LoRA's name;
        # nor does its base being few-step make a LoRA one.
        (
            "Z-Image Turbo",
            "zimage_turbo_clementine.safetensors",
            None,
            False,
            ["ZIT", "CL"],
            None,
            ["#dc5100"],
            None,
        ),
        # A role word that is the base model's own name is no tab.
        (
            "Z-Image Base",
            "z_image_base_bf16.safetensors",
            None,
            True,
            ["ZIB"],
            None,
            ["#dc5100"],
            None,
        ),
        (
            "Krea 2 Raw",
            "krea2_raw.safetensors",
            None,
            True,
            ["Krea", "2"],
            "RAW",
            GRAPHITE,
            None,
        ),
        (
            "Wan 2.2",
            "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
            None,
            True,
            ["Wan", "2.2"],
            None,
            ["#dc5100"],
            None,
        ),
        # Only the base checkpoint wears the family icon; its LoRA is a text
        # tile in the icon's colour.
        (
            "Chroma1 HD",
            "Chroma1-HD.safetensors",
            None,
            True,
            ["CHRO"],
            "HD",
            ["#7a3b12"],
            "chroma",
        ),
        (
            "Chroma1 HD",
            "chroma_ada.safetensors",
            "Ada v3",
            False,
            ["CHRO", "AD3"],
            None,
            ["#7a3b12"],
            None,
        ),
        # A fine-tune checkpoint is not one of the base's releases, so the
        # role that tells those apart is not its tab.
        (
            "Chroma1 HD",
            "chroma1_hd_myanime.safetensors",
            None,
            True,
            ["CHRO", "MY"],
            None,
            ["#7a3b12"],
            None,
        ),
        (
            "Pony Diffusion V6 XL",
            "ponyDiffusionV6XL_v6StartWithThisOne.safetensors",
            None,
            True,
            ["PDXL"],
            None,
            ["#4b2373"],
            "pony",
        ),
        (
            "Pony V7",
            "pony_v7_base.safetensors",
            None,
            True,
            ["Pony"],
            "V7",
            ["#4b2373"],
            "pony",
        ),
        (
            "Illustrious XL",
            "waiNSFWIllustrious_v140.safetensors",
            None,
            True,
            ["ILXL", "WA14"],
            None,
            ["#0c183e"],
            None,
        ),
        # Packaging and file-kind words are nobody's initials.
        (
            "Wan 2.2",
            "wan22_style_high.safetensors",
            None,
            False,
            ["Wan", "ST"],
            None,
            ["#dc5100"],
            None,
        ),
        (
            "SDXL 1.0",
            "my_style_lora.safetensors",
            None,
            False,
            ["SDXL", "MS"],
            None,
            SDXL,
            None,
        ),
        # A family with no maker colour keeps the client's hashed hue.
        (
            "NoobAI-XL",
            "noobai_style.safetensors",
            "Style",
            False,
            ["Noob", "ST"],
            None,
            None,
            None,
        ),
        # A precision mid-name is packaging too.
        (
            "SeedVR2",
            "seedvr2_7b_int8_convrot.safetensors",
            None,
            True,
            ["Seed", "VR2"],
            None,
            None,
            None,
        ),
        # Klein's distilled build is few-step; its base says so on the tab.
        (
            "FLUX.2 Klein",
            "flux-2-klein-9b-fp8.safetensors",
            None,
            True,
            ["Flux", "Klein"],
            "\u26a1",
            GRAPHITE,
            None,
        ),
        (
            "FLUX.2 Klein",
            "flux-2-klein-base-9b.safetensors",
            None,
            True,
            ["Flux", "Klein"],
            "BASE",
            GRAPHITE,
            None,
        ),
        (
            "SeedVR2",
            "seedvr2_ema_7b_fp16.safetensors",
            None,
            True,
            ["Seed", "VR2"],
            None,
            None,
            None,
        ),
        (
            "Ideogram 4",
            "ideogram4_fp8.safetensors",
            None,
            True,
            ["IDEO", "4"],
            None,
            GRAPHITE,
            None,
        ),
    ],
)
def test_a_model_is_drawn_with_the_name_people_write(
    canonical, filename, name, checkpoint, mark, tab, colours, icon
):
    assert model_tile(canonical, filename, name, checkpoint=checkpoint) == {
        "mark": mark,
        "mark_tab": tab,
        "mark_colors": colours,
        "mark_icon": icon,
    }


def test_an_unknown_or_closed_base_model_keeps_the_generated_mark():
    assert model_tile(None, "portrait_mix_v2.safetensors", checkpoint=True) is None
    assert model_tile("my private base", "x.safetensors") is None
    assert model_tile("GPT Image 2", "x.safetensors", checkpoint=True) is None


def test_a_card_slot_the_shelf_does_not_hold_is_named_from_its_filename():
    """A workflow's file outside the shelf's folders still draws its tile."""
    fields = _mark_fields(None, "flux1-dev.sft", "unet")
    assert (fields["mark"], fields["mark_colors"]) == (["F1D"], ["#484c53"])
    assert (
        _mark_fields(None, "z_image_turbo_bf16.safetensors", "unet")["mark_tab"]
        == "\u26a1"
    )
    # Not a base-model slot, so not the release: no few-step bolt.
    assert (
        _mark_fields(None, "z_image_turbo_bf16.safetensors", "vae")["mark_tab"] is None
    )
    assert "mark" not in _mark_fields(None, "add_detail.safetensors", "lora")
    # A slot whose name was never recorded names no tile, and does not fail.
    assert _mark_fields(None, None, "unet") == {"quant": None}
