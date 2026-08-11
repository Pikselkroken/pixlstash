"""Pins for the base-model folding table.

The table is data, so most of it needs no test. These cover the four ways it can
break silently: an alias collision, the normaliser being bypassed, the
containment trap folding FLUX.2 into FLUX.1, and a user's own value being either
lost or duplicated in tab-completion.
"""

from pixlstash.utils.known_base_models import (
    KNOWN_BASE_MODELS,
    _norm,
    completions,
    fold,
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
