"""The prompt assistant's presets, assembler and family read (backend §25).

Every shipped presets file is validated here, so a default that names an
untested option, render vocabulary in a preset, or a category order changed
by accident fails the build rather than reaching a run. Adding an option is a
data change: nothing below names how many a category has.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from pixlstash.hub.workflow_cards import run_family
from pixlstash.services import prompt_assist
from pixlstash.services.character_sheet import PHOTO_LOOK
from pixlstash.services.prompt_assist import (
    MAX_TEXT_LENGTH,
    PRESET_DIR,
    PromptAssistError,
    as_sentence,
    assemble,
    catalogue,
    is_layout_prompt,
    render_words,
    validate,
)

FAMILY = "minimax_h3_image"
BUILT_IN = (
    Path(__file__).resolve().parent.parent
    / "pixlstash"
    / "data"
    / "comfyui-workflows"
    / "built-in"
)


def _shipped() -> dict:
    with open(os.path.join(PRESET_DIR, f"{FAMILY}.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("name", sorted(os.listdir(PRESET_DIR)))
def test_every_shipped_presets_file_validates(name):
    """Named for its family, and every default is none or a tested option."""
    family = name.removesuffix(".json")
    assert prompt_assist.presets(family)["family"] == family
    with open(os.path.join(PRESET_DIR, name), encoding="utf-8") as fh:
        document = json.load(fh)
    for body in document["contexts"].values():
        for category in body["categories"]:
            tested = {
                option["id"]
                for option in category["options"]
                if option["evidence"] == "tested"
            }
            assert category["default"] in tested | {"none"}, category["id"]


def _camera(document: dict) -> dict:
    return document["contexts"]["photo"]["categories"][0]


@pytest.mark.parametrize(
    "break_it, complaint",
    [
        (
            lambda d: _camera(d).update(default="mirrorless"),
            "neither none nor a tested",
        ),
        (
            lambda d: _camera(d)["options"][0].update(evidence="untested"),
            "neither none",
        ),
        (lambda d: _camera(d)["options"][0].update(id="none"), "reserved"),
        (
            lambda d: d["contexts"]["photo"]["categories"][1].update(id="camera"),
            "used twice",
        ),
        (
            lambda d: _camera(d)["options"][0].update(text="A photorealistic photo."),
            "render vocabulary",
        ),
        (lambda d: _camera(d)["options"][0].update(text="no full stop"), "sentences"),
        (lambda d: _camera(d)["options"][0].update(text="x" * 400 + "."), "over 400"),
        (lambda d: _camera(d)["options"][0].update(colour="red"), "unknown keys"),
        (lambda d: d.update(family="flux2"), "is not"),
        (lambda d: d["contexts"]["photo"].pop("default_on"), "default_on"),
        (
            lambda d: (
                _camera(d)["options"][0].pop("text")
                and _camera(d)["options"][0].pop("finish")
            ),
            "neither text nor finish",
        ),
        (
            lambda d: _camera(d)["options"][0].update(finish="no full stop"),
            "finish is not",
        ),
        (lambda d: _camera(d)["options"].append("Phone"), "not an object"),
        (lambda d: d["contexts"].update(photo=[]), "photo: is not an object"),
        (
            lambda d: d["contexts"]["photo"]["categories"].append("Camera"),
            "a category is not an object",
        ),
        (lambda d: _camera(d)["options"][0].pop("id"), "reserved"),
    ],
)
def test_a_presets_file_that_breaks_a_rule_is_refused(break_it, complaint):
    document = deepcopy(_shipped())
    validate(deepcopy(document), FAMILY)
    break_it(document)
    with pytest.raises(ValueError, match=complaint):
        validate(document, FAMILY)


def test_the_defaults_assemble_in_the_tested_order():
    """Camera, light, skin, then the camera's finish: the tested camera, light,
    skin and closing-look lines, in that order."""
    stamp = assemble(FAMILY, "photo", {}, "A woman at a kitchen table.")
    assert [(p["category"], p["kind"], p["id"]) for p in stamp["parts"]] == [
        ("camera", "choice", "phone_snapshot"),
        ("light", "choice", "window_daylight"),
        ("skin", "choice", "smooth_youthful"),
        ("camera", "finish", "phone_snapshot"),
    ]
    assert stamp["parts"][-1]["text"].startswith(
        "It looks like an ordinary unedited iPhone photo:"
    )
    assert (stamp["v"], stamp["joiner"], stamp["presets"]) == (1, " ", FAMILY)
    assert stamp["preset_version"] == _shipped()["version"]
    camera = catalogue(FAMILY, "photo")["categories"][0]
    assert camera["options"][0] == {
        "id": "phone_snapshot",
        "label": "Phone snapshot",
        "text": stamp["parts"][0]["text"],
        "finish": stamp["parts"][-1]["text"],
    }


def test_choices_pick_skip_or_type_and_typed_text_is_only_flagged():
    stamp = assemble(
        FAMILY,
        "photo",
        {
            "camera": {"text": "  an  old   photorealistic film camera "},
            "light": {"id": "window_daylight"},
            "skin": {"text": "  "},
        },
        "A man.",
    )
    # Typed camera text carries no finish.
    assert stamp["parts"] == [
        {
            "category": "camera",
            "kind": "choice",
            "id": None,
            "text": "an old photorealistic film camera.",
        },
        {
            "category": "light",
            "kind": "choice",
            "id": "window_daylight",
            "text": stamp["parts"][1]["text"],
        },
    ]
    assert render_words(stamp["parts"][0]["text"]) == ["photorealistic"]
    # The cap holds for the written text, closing stop included.
    with pytest.raises(PromptAssistError):
        assemble(FAMILY, "photo", {"camera": {"text": "x" * MAX_TEXT_LENGTH}}, "A.")
    assert assemble(
        FAMILY, "photo", {"camera": {"text": "x" * (MAX_TEXT_LENGTH - 1)}}, "A."
    )["parts"][0]["text"].endswith("x.")
    assert as_sentence("Done!") == "Done!"
    # None for the camera drops its finish with it.
    stamp = assemble(FAMILY, "photo", {"camera": "none"}, "A man.")
    assert [p["category"] for p in stamp["parts"]] == ["light", "skin"]


@pytest.mark.parametrize(
    "choices, field",
    [
        ({"lens": "none"}, "prompt_assist.choices.lens"),
        ({"camera": {"id": "webcam"}}, "prompt_assist.choices.camera"),
    ],
)
def test_an_unknown_category_or_option_names_the_field(choices, field):
    with pytest.raises(PromptAssistError) as caught:
        assemble(FAMILY, "photo", choices, "A man.")
    assert caught.value.field == field


def test_a_sentence_the_prompt_already_holds_is_not_added_again():
    once = assemble(FAMILY, "photo", {}, "A woman.")
    full = "A woman. " + " ".join(p["text"] for p in once["parts"])
    assert assemble(FAMILY, "photo", {}, full)["parts"] == []


def test_the_sheet_default_is_the_2776_photo_look_word_for_word():
    for family in (FAMILY, None):
        stamp = assemble(family, "sheet", {}, "x")
        assert [p["text"] for p in stamp["parts"]] == [PHOTO_LOOK]


def test_a_family_with_no_file_of_its_own_gets_the_generic_presets_off():
    """Offered on every workflow; on by default only where a family needs it."""
    assert catalogue(FAMILY, "photo")["default_on"] is True
    for family in ("flux2", None, "../etc"):
        generic = catalogue(family, "photo")
        assert (generic["presets"], generic["default_on"]) == ("_default", False)
        # The sheet look is on wherever #2776 puts it on today.
        assert catalogue(family, "sheet")["default_on"] is True
    stamp = assemble("flux2", "photo", {}, "A man.")
    assert (stamp["family"], stamp["presets"]) == ("flux2", "_default")


def test_a_json_layout_prompt_is_told_from_prose():
    assert is_layout_prompt('{"summary": {"caption": "a sheet"}}')
    assert is_layout_prompt(" [1, 2] ")
    assert not is_layout_prompt("{a curly start} and prose")
    assert not is_layout_prompt(None)


def _graph(name: str = "MiniMax H3 Image T2I (PixlStash).json") -> dict:
    return json.loads((BUILT_IN / name).read_text(encoding="utf-8"))


def test_a_run_graph_is_of_one_family_or_of_none(hub):
    """The kept base-model loaders decide; two families or no model is none."""
    graph = _graph()
    assert run_family(hub, graph) == FAMILY
    graph["3"]["inputs"]["unet_name"] = "flux-2-klein-9b.safetensors"
    assert run_family(hub, graph) == "flux2"
    graph["3"]["inputs"]["unet_name"] = "mystery_model.safetensors"
    assert run_family(hub, graph) is None
    graph["3"]["inputs"]["unet_name"] = ""
    assert run_family(hub, graph) is None
    # Two models merged into the one the sampler reads: both are kept.
    graph = _graph()
    graph["30"] = {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "flux-2-klein-9b.safetensors",
            "weight_dtype": "default",
        },
    }
    graph["31"] = {
        "class_type": "ModelMergeSimple",
        "inputs": {"model1": ["3", 0], "model2": ["30", 0], "ratio": 0.5},
    }
    for node in graph.values():
        if node["inputs"].get("model") == ["3", 0]:
            node["inputs"]["model"] = ["31", 0]
    assert run_family(hub, graph) is None


def test_no_presets_installed_offers_nothing_and_assembles_nothing(monkeypatch):
    """A packaging fault (no family file, no generic file) fails no run."""
    monkeypatch.setattr(prompt_assist, "_load", lambda name: None)
    assert prompt_assist.catalogue("minimax_h3_image", "photo") is None
    with pytest.raises(prompt_assist.PromptAssistError):
        prompt_assist.assemble("minimax_h3_image", "photo", {}, "x")
