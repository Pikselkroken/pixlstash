"""Which ``batch_size`` is how many pictures one submission makes.

Pure: no server, no ComfyUI. ``object_info`` is a hand-written excerpt.
"""

from __future__ import annotations

import pytest

from pixlstash.services import workflow_parameters as wp

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
