"""Pins for :func:`known_base_models.identify`, the matcher the shelf applies.

A wrong match does not error: it files a folder of adapters under a base model
they will not load on. So every threshold here has a case on each side of it,
and the tier order has a case where getting it backwards gives a different
label, not the same one by another route.
"""

from pixlstash.utils.known_base_models import (
    SOURCE_DECLARED,
    SOURCE_DECLARED_FUZZY,
    SOURCE_FILENAME,
    SOURCE_FILENAME_FUZZY,
    SOURCE_USER,
    identify,
    rank,
)


def test_containment_prefers_the_longest_alias():
    # `flux1` and `flux2` both contain the prefix `flux`; the longer alias is
    # the only thing keeping FLUX.2 adapters out of FLUX.1.
    assert identify([], ["myflux2lora.safetensors"]) == (
        "FLUX.2",
        SOURCE_FILENAME_FUZZY,
    )
    assert identify([], ["myflux1lora.safetensors"]) == (
        "FLUX.1 dev",
        SOURCE_FILENAME_FUZZY,
    )
    # `hunyuanimage3` inside the name must beat the `hunyuan` it contains.
    label, _ = identify([], ["myhunyuanimage3style.safetensors"])
    assert label == "HunyuanImage 3.0", label


def test_short_aliases_never_match_inside_a_word():
    # `sd3` is an alias, but three letters inside `xsd3y` are an accident.
    assert identify([], ["hamjam_xsd3y.safetensors"]) == (None, None)
    # Four letters is the floor, and it does fire.
    assert identify([], ["xsdxly.safetensors"]) == ("SDXL 1.0", SOURCE_FILENAME_FUZZY)


def test_ordinary_word_aliases_never_match_inside_a_word():
    for name in (
        "ponytail_girl.safetensors",
        "kurosawa_sanae.safetensors",
        "illumination_style.safetensors",
        "kreative_style.safetensors",
        "monochromatic.safetensors",
    ):
        assert identify([], [name]) == (None, None), name
    # As a whole filename token, or declared, they still count.
    assert identify([], ["pony_style.safetensors"]) == (
        "Pony Diffusion V6 XL",
        SOURCE_FILENAME,
    )
    assert identify(["sana"], []) == ("Sana", SOURCE_DECLARED)
    # Distinctive names are not ordinary words and still match inside one.
    assert identify([], ["myqwenstyle.safetensors"]) == (
        "Qwen-Image",
        SOURCE_FILENAME_FUZZY,
    )


def test_closed_models_are_never_an_answer():
    # `mj` as a whole token folds exactly, but nothing local trains on it.
    assert identify([], ["portrait_mj_style.safetensors"]) == (None, None)
    assert identify(["midjourney"], []) == (None, None)


def test_an_exact_filename_beats_a_fuzzy_declaration():
    # The declared value is a typo of Illustrious; the filename names Pony
    # exactly. Quality before provenance: the exact answer wins.
    assert identify(["ilustrius"], ["example_pony_v2.safetensors"]) == (
        "Pony Diffusion V6 XL",
        SOURCE_FILENAME,
    )


def test_an_exact_declaration_beats_an_exact_filename():
    assert identify(["flux2"], ["example_pony_v2.safetensors"]) == (
        "FLUX.2",
        SOURCE_DECLARED,
    )


def test_a_fuzzy_declaration_beats_filename_containment():
    assert identify(["ilustrius"], ["myflux2lora.safetensors"]) == (
        "Illustrious XL",
        SOURCE_DECLARED_FUZZY,
    )


def test_declared_edit_distance_cutoff_has_a_case_each_side():
    # 0.90 against `illustrious`: a typo, applied.
    assert identify(["ilustrius"], []) == ("Illustrious XL", SOURCE_DECLARED_FUZZY)
    # 0.86 against `noobaixl`: close, but a different string, not applied.
    assert identify(["noobxl"], []) == (None, None)


def test_a_tie_between_two_bases_is_not_a_typo_of_either():
    # `flux` is exactly as close to `flux1` as to `flux2`; picking one would
    # be a coin toss presented as an answer.
    assert identify(["flux"], []) == (None, None)


def test_kohya_sd_versions_are_exact_not_near_typos():
    # `sd_v1` is one edit from the SD 2.x spellings; it must fold, not guess.
    assert identify(["sd_v1"], []) == ("SD 1.5", SOURCE_DECLARED)
    assert identify(["sd_v2"], []) == ("SD 2.1", SOURCE_DECLARED)


def test_the_longest_base_model_token_wins_whatever_its_position():
    for name in (
        "sdxl_illustrious_char.safetensors",
        "illustrious_sdxl_char.safetensors",
    ):
        assert identify([], [name]) == ("Illustrious XL", SOURCE_FILENAME), name


def test_edit_distance_is_never_applied_to_a_filename():
    # `ilustrius` would be a fuzzy match as a declared value; as a filename it
    # contains no alias, so it matches nothing.
    assert identify([], ["ilustrius.safetensors"]) == (None, None)


def test_modelspec_architecture_suffix_is_dropped():
    assert identify(["stable-diffusion-xl-v1-base/lora"], []) == (
        "SDXL 1.0",
        SOURCE_DECLARED,
    )
    assert identify(["flux-1-dev/lora"], []) == ("FLUX.1 dev", SOURCE_DECLARED)


def test_mixed_case_evidence_folds():
    assert identify(["SDXL_Base_V1-0"], []) == ("SDXL 1.0", SOURCE_DECLARED)
    assert identify([], ["MyFlux2LoRA.SafeTensors"]) == (
        "FLUX.2",
        SOURCE_FILENAME_FUZZY,
    )
    assert identify([], ["Example_ILXL_v3_FP16-000012.safetensors"]) == (
        "Illustrious XL",
        SOURCE_FILENAME,
    )


def test_a_recorded_training_checkpoint_is_filename_evidence():
    # kohya's `ss_sd_model_name` is a filename, so it is matched as one.
    assert identify([], ["example.safetensors", "animagineXLV31_v31.safetensors"]) == (
        "Animagine XL",
        SOURCE_FILENAME_FUZZY,
    )


def test_nothing_matched_is_nothing():
    assert identify([], ["example_v3.safetensors"]) == (None, None)
    assert identify([None, ""], [None, ""]) == (None, None)


def test_a_person_outranks_every_scan():
    order = [
        SOURCE_USER,
        SOURCE_DECLARED,
        SOURCE_FILENAME,
        SOURCE_DECLARED_FUZZY,
        SOURCE_FILENAME_FUZZY,
        None,
    ]
    ranks = [rank(source) for source in order]
    assert ranks == sorted(ranks, reverse=True) and len(set(ranks)) == len(ranks), ranks
