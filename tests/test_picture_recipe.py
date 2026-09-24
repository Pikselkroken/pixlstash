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
    _numeric,
    _resolve_against_shelf,
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

    def __init__(self, models=(), files=(), quants=None, names=None):
        self.models = models  # (id, filename, sha256)
        self.names = names or {}  # {model id: display_name}
        self.files = files  # (model_id, relpath)
        # {model id: the header's own dtype spelling}, for the rows that have
        # one. The real read filters `quant IS NOT NULL`, so a row absent here
        # is a row the column says nothing about.
        self.quants = quants or {}

    def fetchall(self, sql):
        if "FROM model_file" in sql:
            return [{"model_id": m, "relpath": r} for m, r in self.files]
        if "quant IS NOT NULL" in sql:
            return [{"id": i, "quant": q} for i, q in self.quants.items()]
        if "sha256 IS NOT NULL" in sql:
            return [{"id": i, "sha256": sha} for i, _name, sha in self.models if sha]
        return [
            {"id": i, "filename": name, "display_name": self.names.get(i)}
            for i, name, _sha in self.models
            if name
        ]


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
    """A number computed at run time is not a number to print beside the file.

    The reduction files ``["9", 0]`` as a link rather than as a widget, so this
    is the absent-key path;
    :func:`test_a_strength_that_is_not_a_number_is_reported_as_none` is the
    other one.
    """
    graph = {
        "5": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Style.safetensors", "strength_model": ["9", 0]},
        }
    }
    assert _model_slots(graph, ([], []))[0]["strength"] is None


@pytest.mark.parametrize("value", ["0.8", True, None, {"weight": 0.8}])
def test_a_strength_that_is_not_a_number_is_reported_as_none(value):
    """The widget is there and holds something that is not a strength.

    ``True`` is the one worth spelling out: Python reads a bool as an int, so
    without the explicit check a toggle beside a LoRA would print as ``1.00``.
    """
    graph = {
        "5": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Style.safetensors", "strength_model": value},
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


def test_a_resolved_slot_carries_the_shelf_rows_name():
    """The chip shows the name the owner gave the row; ``name`` stays the key."""
    hub = FakeHub(
        models=[(7, "style.safetensors", None), (8, "realvis.safetensors", None)],
        names={7: "Watercolour Style"},
    )
    slots = _named(_resolve_against_shelf(hub, _model_slots(GRAPH, ([], []))))
    assert slots["style.safetensors"]["display_name"] == "Watercolour Style"
    assert slots["realvis.safetensors"]["display_name"] is None


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
    # Non-empty first: "every slot is unmatched" is also true of no slots, and
    # an empty list would make the assertion below prove nothing.
    assert len(slots) == 3
    assert all("model_id" not in slot for slot in slots)


# ===========================================================================
# The precision each slot was stored at
# ===========================================================================


def test_a_slot_reads_its_precision_off_the_filename_when_nothing_else_can():
    """The only source a graph ever carries, and the only one a `.gguf` has."""
    graph = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "t5xxl_fp8_e4m3fn.safetensors"},
        },
        "5": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": "flux1-dev-Q4_K_M.gguf"},
        },
    }
    slots = _named(_model_slots(graph, ([], [])))
    assert slots["t5xxl_fp8_e4m3fn.safetensors"]["quant"] == "fp8_e4m3"
    assert slots["flux1-dev-q4_k_m.gguf"]["quant"] == "q4_k_m"


def test_a_slot_with_no_postfix_and_no_shelf_row_records_no_precision():
    # None, not a guess and not an empty string: the overlay draws no badge at
    # all for a file nothing has said anything about.
    assert _named(_model_slots(GRAPH, ([], [])))["realvis.safetensors"]["quant"] is None


def test_the_shelf_column_beats_the_filename_and_is_folded_on_the_way():
    """The header knows what the name only guesses, in the header's spelling.

    `style.safetensors` says nothing about its precision and the shelf row for
    it says `f8_e4m3` - which must reach the panel as `fp8_e4m3`, or one screen
    reads the dtype where the next reads the precision.
    """
    hub = FakeHub(
        models=[(7, "style.safetensors", OTHER_DIGEST)], quants={7: "F8_E4M3"}
    )
    slots = _named(_resolve_against_shelf(hub, _model_slots(GRAPH, ([], []))))
    assert slots["style.safetensors"]["quant"] == "fp8_e4m3"


def test_a_resolved_digest_takes_the_precision_of_the_row_it_resolved_to():
    """A digest slot has no filename to read until the shelf gives it one."""
    hub = FakeHub(models=[(7, "digest-lora.safetensors", DIGEST)], quants={7: "bf16"})
    slots = _named(_resolve_against_shelf(hub, _model_slots(GRAPH, ([], []))))
    assert slots["digest-lora.safetensors"]["quant"] == "bf16"


def test_a_shelf_row_that_records_no_precision_leaves_the_filenames_answer():
    """A row scanned before the column existed must not blank the badge."""
    hub = FakeHub(models=[(7, "style.safetensors", OTHER_DIGEST)])
    graph = {
        "5": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Style_bf16.safetensors", "strength_model": 0.8},
        }
    }
    slots = _named(_resolve_against_shelf(hub, _model_slots(graph, ([], []))))
    assert slots["style_bf16.safetensors"]["quant"] == "bf16"


# ===========================================================================
# Ordering
# ===========================================================================


def test_a_node_id_orders_as_a_number_not_as_text():
    """Node 10 comes after node 7. A subgraph path orders segment by segment."""
    assert sorted(["10", "7", "2", "75:61", "75:9"], key=_numeric) == [
        "2",
        "7",
        "10",
        "75:9",
        "75:61",
    ]
