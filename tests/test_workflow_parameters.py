"""Which ``batch_size`` is how many pictures one submission makes.

Pure: no server, no ComfyUI. ``object_info`` is a hand-written excerpt.
"""

from __future__ import annotations

import pytest

from pixlstash.services import workflow_parameters as wp
from pixlstash.services import workflow_run_service as run_service

_LATENT_OUT = {"input": {"required": {}}, "output": ["LATENT"]}


@pytest.mark.parametrize(
    "class_type, name, object_info, expected",
    [
        # Offline, the name rule: an empty latent or a latent-carrying loader.
        ("SDXL Empty Latent Image (rgthree)", "batch_size", None, True),
        ("Efficient Loader", "batch_size", None, True),
        ("SomePackNode", "batch_size", None, False),
        ("WanImageToVideo", "batch_size", None, False),
        # With ComfyUI's answer, any node that outputs a LATENT.
        ("WanImageToVideo", "batch_size", {"WanImageToVideo": _LATENT_OUT}, True),
        (
            "EmptyLatentImage",
            "batch_size",
            {"EmptyLatentImage": {"input": {}, "output": ["IMAGE"]}},
            False,
        ),
        # A chunk size, not a picture count, whatever it outputs.
        ("RebatchLatents", "batch_size", {"RebatchLatents": _LATENT_OUT}, False),
        # Only batch_size counts.
        ("EmptyLatentImage", "width", None, False),
    ],
)
def test_a_picture_batch_is_a_latents_batch_size(
    class_type, name, object_info, expected
):
    assert wp.is_picture_batch(class_type, name, object_info) is expected


def test_api_graph_stays_importable_for_the_comfyui_routes():
    """``routes/comfyui.py`` reads ``workflow_parameters.api_graph``."""
    assert wp.api_graph({"1": {"class_type": "X", "inputs": {}}}) is not None


def _selector(aspect_ratio="16:9 (Widescreen)", megapixels=1.0, multiple=8):
    return {
        "class_type": "ResolutionSelector",
        "inputs": {
            "aspect_ratio": aspect_ratio,
            "megapixels": megapixels,
            "multiple": multiple,
        },
    }


@pytest.mark.parametrize(
    "source, output, expected",
    [
        # What ComfyUI's own ResolutionSelector.execute returns for these.
        (_selector(), 0, 1368),
        (_selector(), 1, 768),
        (_selector("1:1 (Square)"), 0, 1024),
        (_selector("2:3 (Portrait Photo)", 2.0, 64), 1, 1792),
        ({"class_type": "PrimitiveInt", "inputs": {"value": 832}}, 0, 832),
        # Nothing to read: an unknown node, a selector whose ratio is wired.
        ({"class_type": "SomePackSize", "inputs": {"w": 640}}, 0, None),
        # A node that computes from its `value` is not a passthrough.
        ({"class_type": "IntMultiply", "inputs": {"value": 832}}, 0, None),
        (_selector(aspect_ratio=["9", 0]), 0, None),
    ],
)
def test_a_wired_size_reads_as_the_number_its_source_works_out_to(
    source, output, expected
):
    assert wp.linked_size({"49": source}, ["49", output]) == expected


def _sized_graph():
    return {
        "49": _selector(),
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": ["49", 0], "height": ["49", 1], "batch_size": 1},
        },
        # Sized from the same selector, as an upscale target might be.
        "11": {"class_type": "ImageScale", "inputs": {"width": ["49", 0]}},
    }


def test_an_empty_latent_s_size_is_the_run_s_size_over_another_node_s():
    assert wp.size_inputs(_sized_graph()) == {("5", "width"), ("5", "height")}


def _video_graph(latent_input="latent_image"):
    return {
        "49": _selector(),
        "7": {
            "class_type": "WanImageToVideo",
            "inputs": {"width": ["49", 0], "height": 480, "length": 81},
        },
        # Sizes the first frame, not the video.
        "11": {"class_type": "ImageScale", "inputs": {"width": 512}},
        "9": {
            "class_type": "KSampler",
            "inputs": {"steps": 20, latent_input: ["7", 2]},
        },
        "bad": "not a node",
    }


def test_with_no_empty_latent_the_size_is_the_node_s_that_hands_out_the_latent():
    """An image-to-video node sizes the latent it hands out (#1833)."""
    assert wp.size_inputs(_video_graph()) == {("7", "width"), ("7", "height")}


def test_with_nothing_feeding_a_latent_the_size_is_matched_by_input_name():
    """By name alone: not only a video class, and not only the first node met."""
    graph = _video_graph(latent_input="image_embeds")
    assert wp.size_inputs(graph) == {("7", "width"), ("7", "height"), ("11", "width")}


def test_a_run_at_the_selector_s_own_size_leaves_the_wires_alone():
    graph = _sized_graph()
    wp.set_latent_size(graph, graph["5"], "width", 1368)
    assert graph == _sized_graph()


def test_a_run_at_another_size_cuts_every_reader_of_that_output():
    graph = _sized_graph()
    wp.set_latent_size(graph, graph["5"], "width", 1024)
    assert graph["5"]["inputs"]["width"] == 1024
    assert graph["11"]["inputs"]["width"] == 1024
    # The selector's other output still drives the height.
    assert graph["5"]["inputs"]["height"] == ["49", 1]


def test_a_square_primitive_keeps_driving_the_height_when_only_width_changes():
    graph = {
        "7": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": ["7", 0], "height": ["7", 0], "batch_size": 1},
        },
        "3": {"class_type": "KSampler", "inputs": {"seed": ["7", 0]}},
    }
    # Height first, unchanged, so its wire stays; then a new width.
    wp.set_latent_size(graph, graph["5"], "height", 1024)
    wp.set_latent_size(graph, graph["5"], "width", 1216)
    assert graph["5"]["inputs"] == {"width": 1216, "height": ["7", 0], "batch_size": 1}
    assert graph["3"]["inputs"]["seed"] == ["7", 0]


# --- the inputs a parameter can be made of ---------------------------------

_FORM_GRAPH = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "Base.safetensors"},
    },
    "2": {
        "class_type": "LoraLoader",
        "inputs": {"lora_name": "Style.safetensors", "strength_model": 0.8},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 7,
            "steps": 20,
            "model": ["2", 0],
            "positive": ["6", 0],
            "latent_image": ["5", 0],
        },
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 512, "height": 512, "batch_size": 4},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a cat", "clip": ["2", 1]},
    },
    "8": {
        "class_type": "ImageScaleBy",
        "_meta": {"title": "Final upscale"},
        "inputs": {
            "upscale_method": "lanczos",
            "scale_by": 1.5,
            "image": ["3", 0],
        },
    },
    "9": {
        "class_type": "SomePackNode",
        "inputs": {
            "api_key": "example-not-a-key",
            "max_tokens": 77,
            "notes": "x" * 50,
            "mode": {"nested": True},
        },
    },
    "4": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "P", "images": ["8", 0]},
    },
}
_FORM_INFO = {
    "KSampler": {
        "input": {
            "required": {
                "seed": ["INT", {"control_after_generate": True}],
                "steps": ["INT", {}],
            }
        }
    },
    "ImageScaleBy": {
        "input": {
            "required": {
                "upscale_method": [["nearest-exact", "lanczos"], {}],
                "scale_by": ["FLOAT", {}],
            }
        }
    },
}


def _form(object_info=_FORM_INFO, exposed=(), core=None, max_text=2000):
    labels = {node_id: f"label-{node_id}" for node_id in _FORM_GRAPH}
    nodes = run_service.form_inputs(
        _FORM_GRAPH, labels, core or {}, object_info, set(exposed), max_text
    )
    return {
        (node["node_id"], row["input_name"]): {**row, "title": node["title"]}
        for node in nodes
        for row in node["inputs"]
    }


def test_form_inputs_leave_out_what_pixlstash_already_sets():
    """Wrong if a prompt, seed, batch, model, LoRA, saver or key is offered."""
    rows = _form()
    assert set(rows) == {
        ("3", "steps"),
        ("5", "width"),
        ("5", "height"),
        ("8", "upscale_method"),
        ("8", "scale_by"),
        ("9", "max_tokens"),
        ("9", "notes"),
    }
    # Text longer than a default may hold could not be stored, so is not offered.
    assert ("9", "notes") not in _form(max_text=10)


def test_form_inputs_are_typed_and_listed_from_object_info():
    rows = _form()
    method = rows[("8", "upscale_method")]
    assert (method["kind"], method["options"], method["value"]) == (
        "choice",
        ["nearest-exact", "lanczos"],
        "lanczos",
    )
    assert method["title"] == "Final upscale"
    assert rows[("8", "scale_by")]["kind"] == "number"
    # A node ComfyUI does not declare, and any node when it did not answer.
    assert rows[("9", "max_tokens")]["kind"] is None
    offline = _form(object_info=None)[("8", "upscale_method")]
    assert (offline["kind"], offline["options"]) == (None, None)


def test_form_inputs_are_addressed_as_their_row_is_else_by_core():
    core = {"8": "c8", "3": "c3"}
    rows = _form(core=core, exposed={("label-3", "steps"), ("core:c8", "scale_by")})
    # A row keeps the address it was written at, either spelling.
    assert (rows[("3", "steps")]["slot_label"], rows[("3", "steps")]["exposed"]) == (
        "label-3",
        True,
    )
    assert rows[("8", "scale_by")]["exposed"] is True
    # A new one takes the core address where the node has one.
    method = rows[("8", "upscale_method")]
    assert (method["slot_label"], method["exposed"]) == ("core:c8", False)
    assert rows[("9", "max_tokens")]["slot_label"] == "label-9"
