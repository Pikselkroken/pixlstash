"""The fan-out rule on small hand-written graphs: no server, no ComfyUI.

Each consumer shape is one ``object_info`` entry copied from what this ComfyUI
advertises. The route-level tests in ``test_workflows_api.py`` check the same
rule end to end; these check the rule itself, including what it must refuse.
"""

import copy
import json
from pathlib import Path

import pytest

from pixlstash.services.workflow_fan_out import (
    FanOutError,
    fan_out_capacity,
    fan_out_picture_input,
    picture_tag,
)

# Autogrow inputs as ComfyUI's object_info declares them (COMFY_AUTOGROW_V3).
OBJECT_INFO = {
    "LoadImage": {"input": {"required": {"image": [["a.png"], {}]}}},
    "MiniMaxH3ImageConditioning": {
        "input": {
            "optional": {
                "images": [
                    "COMFY_AUTOGROW_V3",
                    {
                        "template": {
                            "input": {"required": {"image": ["IMAGE", {}]}},
                            "prefix": "image_",
                            "min": 0,
                            "max": 100,
                        }
                    },
                ]
            }
        }
    },
    "TextEncodeMOSSImageEdit": {
        "input": {
            "required": {
                "images": [
                    "COMFY_AUTOGROW_V3",
                    {
                        "template": {
                            "input": {"required": {"image": ["IMAGE", {}]}},
                            "prefix": "image_",
                            "min": 1,
                            "max": 10,
                        }
                    },
                ]
            }
        }
    },
    "ReferenceMultiLatent": {
        "input": {
            "optional": {
                "images": [
                    "COMFY_AUTOGROW_V3",
                    {
                        "template": {
                            "input": {"optional": {"image": ["IMAGE", {}]}},
                            "prefix": "image",
                            "min": 1,
                            "max": 100,
                        }
                    },
                ]
            }
        }
    },
    "TextEncodeQwenImageEditPlus": {
        "input": {
            "optional": {
                "image1": ["IMAGE", {}],
                "image2": ["IMAGE", {}],
                "image3": ["IMAGE", {}],
            }
        }
    },
    "TextEncodeQwenImage21": {
        "input": {
            "required": {
                "images": [
                    "COMFY_AUTOGROW_V3",
                    {
                        "template": {
                            "input": {"required": {"image": ["IMAGE", {}]}},
                            "names": [f"image_{i}" for i in range(1, 17)],
                            "min": 0,
                        }
                    },
                ]
            }
        }
    },
    "ImageScale": {"input": {"required": {"image": ["IMAGE", {}]}}},
}


def _loader(node_id: str = "1") -> dict:
    return {"class_type": "LoadImage", "inputs": {"image": "a.png"}}


def _target(node_id: str = "1") -> dict:
    return {"path": [node_id, "inputs", "image"], "template": None}


def _graph(consumer_class: str, key: str, node_id: str = "1") -> dict:
    """One loader feeding one consumer input."""
    return {
        node_id: _loader(node_id),
        "2": {"class_type": consumer_class, "inputs": {key: [node_id, 0]}},
    }


def _links_of(graph: dict, consumer: str) -> dict:
    """The image links into *consumer*, by input key, as ``node_id`` strings."""
    return {
        key: value[0]
        for key, value in graph[consumer]["inputs"].items()
        if isinstance(value, list)
    }


def test_an_autogrow_input_named_from_one_takes_the_next_names_in_order():
    """MiniMax wires ``images.image_1`` (a 1-based start): clones go to 2, 3, ..."""
    graph = _graph("MiniMaxH3ImageConditioning", "images.image_1")
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 98

    fanned = fan_out_picture_input(graph, [("1", _target())], [10, 11], OBJECT_INFO)

    assert [f.picture_id for f in fanned] == [10, 11]
    assert _links_of(graph, "2") == {
        "images.image_1": "1",
        "images.image_2": "1_pic1",
        "images.image_3": "1_pic2",
    }
    # The clone's target sits on the clone, at the same input field.
    assert fanned[0].targets == [
        {"path": ["1_pic1", "inputs", "image"], "template": None}
    ]


def test_an_autogrow_input_named_from_zero_takes_the_next_names_in_order():
    """User graphs wire ``images.image_0`` (MOSS): the next names are 1, 2, ..."""
    graph = _graph("TextEncodeMOSSImageEdit", "images.image_0")
    # range(max) names image_0..image_9, so image_0 leaves nine more.
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 9

    fan_out_picture_input(graph, [("1", _target())], [10, 11], OBJECT_INFO)

    assert _links_of(graph, "2") == {
        "images.image_0": "1",
        "images.image_1": "1_pic1",
        "images.image_2": "1_pic2",
    }


def test_a_fixed_slot_input_stops_at_its_last_declared_slot():
    """Qwen declares image1..image3: a loader on image1 takes two more."""
    graph = _graph("TextEncodeQwenImageEditPlus", "image1")
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 2

    fan_out_picture_input(graph, [("1", _target())], [10, 11], OBJECT_INFO)
    assert _links_of(graph, "2") == {
        "image1": "1",
        "image2": "1_pic1",
        "image3": "1_pic2",
    }

    over = _graph("TextEncodeQwenImageEditPlus", "image1")
    before = copy.deepcopy(over)
    with pytest.raises(FanOutError, match="at most 2 more pictures; 3 were sent"):
        fan_out_picture_input(over, [("1", _target())], [10, 11, 12], OBJECT_INFO)
    assert over == before, "a refused fan-out must not change the graph"


def test_a_template_of_names_is_followed_by_name_not_by_number():
    """Qwen 2.1 lists image_1..image_16 by name: image_1 takes image_2..16 only.

    Fifteen more, and an 17th name would not be listed, so it is not wired.
    """
    graph = _graph("TextEncodeQwenImage21", "images.image_1")
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 15

    fan_out_picture_input(graph, [("1", _target())], [10, 11], OBJECT_INFO)
    assert _links_of(graph, "2") == {
        "images.image_1": "1",
        "images.image_2": "1_pic1",
        "images.image_3": "1_pic2",
    }


def test_a_mixed_krea_shape_reaches_only_the_autogrow_past_the_fixed_slots():
    """Picture 4 onward reaches ReferenceMultiLatent alone; that is intended."""
    graph = {
        "1": _loader(),
        "2": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"image1": ["1", 0]},
        },
        "3": {
            "class_type": "ReferenceMultiLatent",
            "inputs": {"images.image1": ["1", 0]},
        },
    }
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 98

    fanned = fan_out_picture_input(
        graph, [("1", _target())], [10, 11, 12, 13], OBJECT_INFO
    )

    assert len(fanned) == 4
    assert _links_of(graph, "2") == {
        "image1": "1",
        "image2": "1_pic1",
        "image3": "1_pic2",
    }
    assert _links_of(graph, "3") == {
        "images.image1": "1",
        "images.image2": "1_pic1",
        "images.image3": "1_pic2",
        "images.image4": "1_pic3",
        "images.image5": "1_pic4",
    }
    assert "1_pic3" not in _links_of(graph, "2").values()


def test_a_slot_already_wired_is_never_written_over():
    """Another loader holds image2: the clone skips it, and the input is full."""
    graph = {
        "1": _loader(),
        "9": _loader("9"),
        "2": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"image1": ["1", 0], "image2": ["9", 0]},
        },
    }
    before = copy.deepcopy(graph)

    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 0
    with pytest.raises(FanOutError, match="at most 0 more pictures"):
        fan_out_picture_input(graph, [("1", _target())], [10], OBJECT_INFO)
    assert graph == before


def test_two_numbered_edges_from_one_loader_never_claim_the_same_slot():
    """``image_1`` and ``image_2`` both from loader 1: clone 2's ``image_3`` is
    clone 1's, so clone 2 takes ``image_4`` only, and nothing is overwritten."""
    graph = {
        "1": _loader(),
        "2": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {"images.image_1": ["1", 0], "images.image_2": ["1", 0]},
        },
    }

    fan_out_picture_input(graph, [("1", _target())], [10, 11], OBJECT_INFO)

    assert _links_of(graph, "2") == {
        "images.image_1": "1",
        "images.image_2": "1",
        "images.image_3": "1_pic1",
        "images.image_4": "1_pic2",
    }


def test_a_link_from_a_loaders_mask_is_not_a_picture_slot():
    """Only IMAGE (output 0) is cloned; a MASK link is not a consumer of it."""
    graph = {
        "1": _loader(),
        "2": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"image1": ["1", 1]},
        },
    }
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 0


def test_an_undeclared_or_unnumbered_consumer_input_takes_no_clone():
    """ImageScale's ``image`` has no number, so there is no next slot to take."""
    graph = _graph("ImageScale", "image")
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 0


def test_a_consumer_class_object_info_does_not_name_takes_nothing():
    graph = _graph("SomeUnknownNode", "image1")
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 0
    assert fan_out_capacity(graph, ["1"], None) == 0


def test_no_pictures_leaves_the_graph_exactly_as_it_was():
    graph = _graph("MiniMaxH3ImageConditioning", "images.image_1")
    before = copy.deepcopy(graph)
    assert fan_out_picture_input(graph, [("1", _target())], [], OBJECT_INFO) == []
    assert graph == before


def test_a_clone_id_never_collides_with_a_subgraph_style_id():
    """ComfyUI ids such as ``30:62`` are strings; a taken clone id gets a suffix."""
    graph = _graph("MiniMaxH3ImageConditioning", "images.image_1", node_id="30:62")
    graph["30:62_pic1"] = _loader("30:62_pic1")

    fanned = fan_out_picture_input(
        graph, [("30:62", _target("30:62"))], [10], OBJECT_INFO
    )

    clone = fanned[0].targets[0]["path"][0]
    assert clone not in {"30:62", "30:62_pic1"}
    assert graph[clone]["class_type"] == "LoadImage"
    assert _links_of(graph, "2")["images.image_2"] == clone


def test_a_target_not_on_its_own_loader_input_is_refused():
    graph = _graph("TextEncodeQwenImageEditPlus", "image1")
    with pytest.raises(FanOutError, match="cannot be cloned"):
        fan_out_picture_input(
            graph,
            [("1", {"path": ["5", "inputs", "image"], "template": None})],
            [10],
            OBJECT_INFO,
        )


def test_the_tag_names_pictures_for_a_minimax_consumer_only():
    minimax = _graph("MiniMaxH3ImageConditioning", "images.image_1")
    other = _graph("TextEncodeQwenImageEditPlus", "image1")
    assert picture_tag(minimax, ["1"]) == "<Picture {n}>"
    assert picture_tag(other, ["1"]) == "picture {n}"


# --- chains: an IMAGE-to-IMAGE node on the way to a numbered slot ------------

BUILT_IN = (
    Path(__file__).resolve().parents[1] / "pixlstash/data/comfyui-workflows/built-in"
)
OBJECT_INFO["ImageScaleToTotalPixels"] = {
    "input": {"required": {"image": ["IMAGE", {}], "megapixels": ["FLOAT", {}]}},
    "output": ["IMAGE"],
}


def test_a_resize_on_the_way_to_the_encoder_is_cloned_with_the_picture():
    """MOSS: loader -> resize -> two encoders on ``images.image_0``.

    The resize is shared by both encoders, so each picture gets one resize
    clone, fed by its own loader clone, and wired into both encoders' next slot.
    """
    graph = {
        "1": _loader(),
        "2": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["1", 0]}},
        "3": {
            "class_type": "TextEncodeMOSSImageEdit",
            "inputs": {"images.image_0": ["2", 0]},
        },
        "4": {
            "class_type": "TextEncodeMOSSImageEdit",
            "inputs": {"images.image_0": ["2", 0]},
        },
    }
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 9

    fanned = fan_out_picture_input(graph, [("1", _target())], [10, 11], OBJECT_INFO)

    assert graph["1_pic1"]["class_type"] == "LoadImage"
    assert graph["2_pic1"]["inputs"]["image"] == ["1_pic1", 0]
    assert graph["2_pic2"]["inputs"]["image"] == ["1_pic2", 0]
    for encoder in ("3", "4"):
        assert _links_of(graph, encoder) == {
            "images.image_0": "2",
            "images.image_1": "2_pic1",
            "images.image_2": "2_pic2",
        }, encoder
    # The originals are as they were: the resize still reads the loader.
    assert graph["2"]["inputs"]["image"] == ["1", 0]
    assert fanned[1].targets == [
        {"path": ["1_pic2", "inputs", "image"], "template": None}
    ]


def test_a_node_with_two_links_is_shared_and_its_path_is_not_cloned():
    """A second link into the resize makes it shared: no path through it."""
    graph = {
        "1": _loader(),
        "2": {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {"image": ["1", 0], "megapixels": ["9", 0]},
        },
        "3": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"image1": ["2", 0]},
        },
        "9": _loader("9"),
    }
    before = copy.deepcopy(graph)
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 0
    with pytest.raises(FanOutError, match="at most 0 more pictures"):
        fan_out_picture_input(graph, [("1", _target())], [10], OBJECT_INFO)
    assert graph == before


def test_a_branch_that_reaches_no_numbered_slot_is_not_cloned():
    """A save fed from the resize is not on any picture's path: it stays put."""
    graph = {
        "1": _loader(),
        "2": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["1", 0]}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        "4": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"image1": ["1", 0]},
        },
    }
    fan_out_picture_input(graph, [("1", _target())], [10], OBJECT_INFO)

    assert "2_pic1" not in graph and "3_pic1" not in graph
    assert graph["3"]["inputs"] == {"images": ["2", 0]}
    assert _links_of(graph, "4") == {"image1": "1", "image2": "1_pic1"}


def test_the_built_in_krea_scene_steps_each_edge_from_its_own_number():
    """Krea: ``image1`` on the encoder, ``images.image0`` on ReferenceMultiLatent.

    The autogrow edge reaches image0..image99, so 99 more; the encoder's three
    fixed slots run out after two. Picture 3 onward reaches the autogrow only.
    """
    graph = json.loads((BUILT_IN / "Krea 2 Scene (PixlStash).json").read_text())
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 99

    fan_out_picture_input(graph, [("1", _target())], [10, 11, 12], OBJECT_INFO)

    images = {k: v for k, v in _links_of(graph, "6").items() if k.startswith("image")}
    assert images == {"image1": "1", "image2": "1_pic1", "image3": "1_pic2"}
    autogrow = {
        k: v for k, v in _links_of(graph, "7").items() if k.startswith("images.")
    }
    assert autogrow == {
        "images.image0": "1",
        "images.image1": "1_pic1",
        "images.image2": "1_pic2",
        "images.image3": "1_pic3",
    }
    assert picture_tag(graph, ["1"]) == "picture {n}"


def test_the_built_in_minimax_scene_takes_98_more_pictures_with_its_tag():
    graph = json.loads(
        (BUILT_IN / "MiniMax H3 Image Scene (PixlStash).json").read_text()
    )
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 98
    assert picture_tag(graph, ["1"]) == "<Picture {n}>"


def test_the_built_in_moss_scene_takes_nine_more_pictures_into_both_encoders():
    """Whatever chain the file carries, each picture reaches both encoders."""
    graph = json.loads((BUILT_IN / "MOSS-Image Scene (PixlStash).json").read_text())
    assert fan_out_capacity(graph, ["1"], OBJECT_INFO) == 9

    fan_out_picture_input(graph, [("1", _target())], [10, 11], OBJECT_INFO)

    encoders = [
        node_id
        for node_id, node in graph.items()
        if node.get("class_type") == "TextEncodeMOSSImageEdit"
    ]
    for encoder in encoders:
        source = graph[encoder]["inputs"]["images.image_2"][0]
        assert source.endswith("_pic2"), (encoder, source)
        # The file scales each picture to 1 MP first: the picture's own copy.
        assert graph[source]["class_type"] == "ImageScaleToTotalPixels"
        assert graph[source]["inputs"]["image"] == ["1_pic2", 0]


def test_every_node_on_a_two_node_chain_reads_its_own_upstream():
    """loader -> resize -> resize -> encoder: the second resize's clone reads the
    first's clone, not the loader's, so each picture's chain stays its own."""
    graph = {
        "1": _loader(),
        "2": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["1", 0]}},
        "3": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["2", 0]}},
        "4": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"image1": ["3", 0]},
        },
    }

    fan_out_picture_input(graph, [("1", _target())], [10, 11], OBJECT_INFO)

    assert graph["2_pic2"]["inputs"]["image"] == ["1_pic2", 0]
    assert graph["3_pic2"]["inputs"]["image"] == ["2_pic2", 0]
    assert _links_of(graph, "4") == {
        "image1": "3",
        "image2": "3_pic1",
        "image3": "3_pic2",
    }
