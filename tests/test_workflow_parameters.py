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
