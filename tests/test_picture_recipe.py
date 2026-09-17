"""What the overlay's Recipe section is told about a picture (#1313).

No server: every assertion here is about reading one graph, so a ``Server`` would
cost 1.35 s to prove nothing extra. The two halves that need a live library - the
route's own shape and the resolution lock - are asserted in
``tests/test_comfyui_recipe_route.py``, against the picture that suite already
imports.

The rule the module exists to keep honest is the **verified tier**: a graph that
names a file by its digest names *that* file, and a graph that names
``style.safetensors`` names a file of that name, which is all it says. The same
split the model shelf counts its pictures by, so a badge here and a count there
cannot mean two different things.
"""

from __future__ import annotations

import pytest

from pixlstash.services.picture_recipe_service import (
    _model_slots,
    _resolve_against_shelf,
    _settings,
)

DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64

GRAPH = {
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "SDXL/RealVis.safetensors"},
    },
    "5": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "Style.safetensors",
            "strength_model": 0.8,
            "strength_clip": 1.0,
            "model": ["4", 0],
        },
    },
    "6": {
        "class_type": "PixlStashLoraLoader",
        "inputs": {"lora_sha256": DIGEST, "strength_model": 0.5},
    },
    "7": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}},
    "8": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 12345,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "model": ["5", 0],
        },
    },
}


class FakeHub:
    """The three reads ``recipe_asset_index`` makes, plus the filename lookup.

    Dispatched on the SQL rather than on call order, so a reordering in the
    service does not silently hand a test the wrong table.
    """

    def __init__(self, models=(), files=()):
        self.models = models  # (id, filename, sha256)
        self.files = files  # (model_id, relpath)

    def fetchall(self, sql):
        if "FROM model_file" in sql:
            return [{"model_id": m, "relpath": r} for m, r in self.files]
        if "sha256 IS NOT NULL" in sql:
            return [{"id": i, "sha256": sha} for i, _name, sha in self.models if sha]
        return [{"id": i, "filename": name} for i, name, _sha in self.models if name]


def _named(slots) -> dict:
    return {slot["name"]: slot for slot in slots}


# ===========================================================================
# What the graph says
# ===========================================================================


def test_every_model_the_graph_loads_is_a_slot_named_by_its_basename():
    slots = _named(_model_slots(GRAPH, ([], [])))
    assert set(slots) == {"realvis.safetensors", "style.safetensors", DIGEST}


def test_a_lora_carries_the_strength_it_was_loaded_at():
    slots = _named(_model_slots(GRAPH, ([], [])))
    assert slots["style.safetensors"]["strength"] == pytest.approx(0.8)
    assert slots[DIGEST]["strength"] == pytest.approx(0.5)


def test_a_checkpoint_has_no_strength_rather_than_a_made_up_one():
    assert (
        _named(_model_slots(GRAPH, ([], [])))["realvis.safetensors"]["strength"] is None
    )


def test_an_input_image_is_not_listed_as_a_model():
    """``LoadImage``'s file is an asset of the graph, but it is not a model.

    The reduction keeps image filenames beside model ones, so without the
    extension check the Models row would offer ``reference.png`` to open on the
    model shelf, where it has never been.
    """
    assert "reference.png" not in _named(_model_slots(GRAPH, ([], [])))


def test_a_strength_wired_from_another_node_is_reported_as_none():
    """A number computed at run time is not a number to print beside the file."""
    graph = {
        "5": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Style.safetensors", "strength_model": ["9", 0]},
        }
    }
    assert _model_slots(graph, ([], []))[0]["strength"] is None


def test_a_stacker_keeps_its_slots_and_their_own_strengths_apart():
    graph = {
        "5": {
            "class_type": "LoraStacker",
            "inputs": {
                "lora_name_1": "First.safetensors",
                "strength_1": 0.4,
                "lora_name_2": "Second.safetensors",
                "strength_2": 0.9,
            },
        }
    }
    slots = _named(_model_slots(graph, ([], [])))
    assert slots["first.safetensors"]["strength"] == pytest.approx(0.4)
    assert slots["second.safetensors"]["strength"] == pytest.approx(0.9)


def test_one_file_loaded_twice_at_one_strength_is_one_row():
    graph = {
        "5": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Style.safetensors", "strength_model": 0.8},
        },
        "6": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "style.safetensors", "strength_model": 0.8},
        },
    }
    assert len(_model_slots(graph, ([], []))) == 1


def test_a_ui_only_file_still_names_its_models_without_a_strength():
    """No API ``prompt`` chunk: names are all a UI graph gives, so that is all
    that is claimed."""
    slots = _named(
        _model_slots(None, (["SDXL/Base.safetensors"], ["Style.safetensors"]))
    )
    assert set(slots) == {"base.safetensors", "style.safetensors"}
    assert all(slot["strength"] is None for slot in slots.values())


def test_an_unreadable_graph_lists_no_models_rather_than_guessing():
    assert _model_slots({"4": "not a node"}, ([], [])) == []


# ===========================================================================
# The settings
# ===========================================================================


def test_the_settings_are_the_sampler_names_the_run_panel_pins():
    rows = {row["label"]: row["value"] for row in _settings(GRAPH)}
    assert rows == {"steps": 20, "cfg": 7.0, "sampler_name": "euler"}


def test_the_seed_is_not_a_setting():
    """A generation is an instance plus a seed, so it is not one of these."""
    assert "seed" not in {row["label"] for row in _settings(GRAPH)}


def test_a_primitive_is_labelled_by_the_setting_it_drives():
    """Its own widget is called ``value``, which names nothing to a reader."""
    graph = {
        "1": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
        "2": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": ["1", 0], "height": 512},
        },
    }
    rows = {row["label"]: row["value"] for row in _settings(graph)}
    assert rows == {"width": 1024, "height": 512}


def test_a_file_with_no_api_prompt_has_no_settings():
    assert _settings(None) == []


# ===========================================================================
# Which shelf model, and how certainly
# ===========================================================================


def test_a_digest_that_names_one_shelf_model_is_verified():
    # Named unlike the graph's other LoRA on purpose: a shelf row called
    # `style.safetensors` would let this pass on the FILENAME slot beside it.
    hub = FakeHub(models=[(7, "digest-lora.safetensors", DIGEST)])
    slots = _named(_resolve_against_shelf(hub, _model_slots(GRAPH, ([], []))))
    # Renamed to what the shelf calls it: a sha256 names nothing to a reader.
    assert DIGEST not in slots
    assert slots["digest-lora.safetensors"]["model_id"] == 7
    assert slots["digest-lora.safetensors"]["verified"] is True


def test_a_filename_match_is_a_shelf_row_but_never_verified():
    """A file of that name is all the graph said, so the badge stays off."""
    hub = FakeHub(models=[(7, "style.safetensors", OTHER_DIGEST)])
    slots = _named(_resolve_against_shelf(hub, _model_slots(GRAPH, ([], []))))
    assert slots["style.safetensors"]["model_id"] == 7
    assert slots["style.safetensors"]["verified"] is False


def test_a_name_two_shelf_rows_share_resolves_to_neither():
    hub = FakeHub(
        models=[(7, "style.safetensors", None), (8, "style.safetensors", None)]
    )
    slots = _named(_resolve_against_shelf(hub, _model_slots(GRAPH, ([], []))))
    assert slots["style.safetensors"]["model_id"] is None
    assert slots["style.safetensors"]["verified"] is False


def test_a_model_the_shelf_has_never_seen_is_reported_as_unmatched():
    hub = FakeHub(models=[(7, "something_else.safetensors", None)])
    slots = _named(_resolve_against_shelf(hub, _model_slots(GRAPH, ([], []))))
    assert slots["realvis.safetensors"]["model_id"] is None
    assert slots["realvis.safetensors"]["verified"] is False


def test_a_copys_path_reaches_the_same_shelf_row_as_its_name():
    """``model_file.relpath`` is the second name a shelf row answers to."""
    hub = FakeHub(models=[(7, None, None)], files=[(7, "loras/style.safetensors")])
    slots = _named(_resolve_against_shelf(hub, _model_slots(GRAPH, ([], []))))
    assert slots["style.safetensors"]["model_id"] == 7


def test_an_unresolved_digest_keeps_the_digest_and_no_shelf_row():
    """It really is all this machine knows about the file."""
    hub = FakeHub(models=[(7, "style.safetensors", OTHER_DIGEST)])
    slots = _named(_resolve_against_shelf(hub, _model_slots(GRAPH, ([], []))))
    assert slots[DIGEST]["model_id"] is None
    assert slots[DIGEST]["verified"] is False


def test_without_a_hub_no_slot_claims_a_shelf_row():
    slots = _resolve_against_shelf(None, _model_slots(GRAPH, ([], [])))
    assert all("model_id" not in slot for slot in slots)
