"""Tests for the sample prompts written from a training set's captions."""

from __future__ import annotations

import pytest

from pixlstash.training import sample_prompts
from pixlstash.training.sample_prompts import TRIGGER

_GRANDAD = [
    "The image shows an elderly man sitting on a chair in front of a computer.",
    "A close-up of an elderly man with white hair.",
    "An old man smiling at the camera.",
]


def _texts(subject, captions=(), tags=()):
    return sample_prompts.prompt_texts(
        sample_prompts.generate(subject, list(captions), list(tags))
    )


def test_two_pairs_differ_only_by_the_trigger_word():
    result = sample_prompts.generate("person", _GRANDAD, [[]] * 3)
    assert result["description"] == "an elderly man"
    assert result["description_source"] == "captions"
    prompts = result["prompts"]
    assert len(prompts) == 4
    for with_word, without in (prompts[0:2], prompts[2:4]):
        assert with_word["prompt"] == with_word["with_trigger"]
        assert without["prompt"] == without["without_trigger"]
        assert with_word["with_trigger"] == without["with_trigger"]
        assert TRIGGER in with_word["prompt"] and TRIGGER not in without["prompt"]
        assert without["prompt"] == with_word["prompt"].replace(
            TRIGGER, "an elderly man"
        )
    assert prompts[0]["prompt"] != prompts[2]["prompt"]


def test_the_same_dataset_gives_the_same_prompts():
    assert _texts("person", _GRANDAD) == _texts("person", list(_GRANDAD))


def test_a_scene_the_captions_show_is_passed_over():
    plain = _texts("person", ["A man indoors."])
    assert "outdoors in soft daylight" in plain[0]
    shown = _texts("person", ["A man standing outdoors in a park."])
    assert not any("outdoors" in prompt for prompt in shown)
    # A set that shows every scene still gets two pairs.
    everything = " ".join(
        word for _, words in sample_prompts._SCENES["person"] for word in words
    )
    assert len(_texts("person", [everything])) == 4


def test_a_style_drops_its_suffix():
    prompts = _texts("style", ["A painting of a cat"])
    assert prompts[0] == f"a harbour at dusk, {TRIGGER} style"
    assert prompts[1] == "a harbour at dusk"
    result = sample_prompts.generate("style", [], [])
    assert result["description"] is None and result["description_source"] is None


def test_a_thing_is_named_by_what_its_captions_open_with():
    # Neither the framing ("a close-up") nor the person wearing it is the thing.
    captions = [
        "A close-up of a gold ring on a hand.",
        "A woman wearing a gold ring.",
        "The image shows a woman with a gold ring on her finger.",
    ]
    assert sample_prompts.describe("thing", captions, [[]] * 3) == (
        "a gold ring",
        "captions",
    )
    close_ups = ["A close-up of a red bicycle.", "A close-up of a red bicycle."]
    assert sample_prompts.describe("thing", close_ups, [[], []])[0] == "a red bicycle"


def test_no_captions_falls_back_to_tags_then_the_generic_noun():
    tags = [["old man", "man", "smile"], ["man", "old man", "beard"]]
    assert sample_prompts.describe("person", ["", ""], tags) == ("an old man", "tags")
    assert sample_prompts.describe("person", [], []) == ("a person", "generic")
    assert sample_prompts.describe("person", [""], [["outdoors"]]) == (
        "a person",
        "generic",
    )
    assert sample_prompts.describe("thing", [], [["bicycle"], ["bicycle", "red"]]) == (
        "a bicycle",
        "tags",
    )
    assert sample_prompts.describe("thing", [], []) == ("an object", "generic")


def test_an_unknown_subject_is_refused():
    with pytest.raises(ValueError, match="subject"):
        sample_prompts.generate("pet", [], [])
