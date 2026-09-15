"""A workflow's parameters, typed for a form (#1306).

Pure: no server, no ComfyUI. ``object_info`` is a hand-written excerpt in
both combo serialisations ComfyUI emits.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

from pixlstash.services import workflow_bindings
from pixlstash.services import workflow_parameters as wp


def _graph() -> dict:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 42,
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sdxl_base.safetensors"},
            "_meta": {"title": "Base model"},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": ["10", 0], "height": 1024, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a cat", "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry", "clip": ["4", 1]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0], "filename_prefix": "ComfyUI"},
        },
        "10": {"class_type": "PrimitiveInt", "inputs": {"value": 768}},
        "11": {
            "class_type": "ThirdPartyUpscale",
            "inputs": {"api_key": "dummy-key", "resolution": [2, 2], "scale": 2},
        },
    }


_INT_SIZE = ["INT", {"min": 16, "max": 16384, "step": 8}]

_OBJECT_INFO = {
    "KSampler": {
        "input": {
            "required": {
                "seed": [
                    "INT",
                    {"min": 0, "max": 2**64 - 1, "control_after_generate": True},
                ],
                "steps": ["INT", {"min": 1, "max": 10000}],
                "cfg": ["FLOAT", {"min": 0.0, "max": 100.0, "step": 0.1}],
                "sampler_name": [["euler", "dpmpp_2m"], {}],
                "scheduler": ["COMBO", {"options": ["normal", "karras"]}],
                "denoise": ["FLOAT", {"min": 0.0, "max": 1.0, "step": 0.01}],
            }
        }
    },
    "CheckpointLoaderSimple": {
        "input": {
            "required": {"ckpt_name": [["sdxl_base.safetensors", "flux.safetensors"]]}
        }
    },
    "EmptyLatentImage": {
        "input": {
            "required": {
                "width": _INT_SIZE,
                "height": _INT_SIZE,
                "batch_size": ["INT", {"min": 1, "max": 4096}],
            }
        }
    },
    "CLIPTextEncode": {
        "input": {"required": {"text": ["STRING", {"multiline": True}]}}
    },
    "SaveImage": {"input": {"required": {"filename_prefix": ["STRING", {}]}}},
    # ComfyUI flags every PrimitiveInt as re-rollable, including one driving a width.
    "PrimitiveInt": {
        "input": {"required": {"value": ["INT", {"control_after_generate": True}]}}
    },
}


def _by_key(parameters) -> dict:
    return {p.key: p for p in parameters}


def test_typed_parameters_carry_comfyuis_ranges_and_options():
    found = _by_key(wp.describe_parameters(_graph(), _OBJECT_INFO))
    assert found[("3", "seed")].kind == wp.SEED
    assert found[("3", "steps")].kind == wp.INT
    assert (found[("3", "steps")].minimum, found[("3", "steps")].maximum) == (1, 10000)
    assert found[("3", "cfg")].kind == wp.FLOAT
    assert found[("3", "cfg")].step == 0.1
    assert found[("3", "sampler_name")].options == ("euler", "dpmpp_2m")
    assert found[("3", "scheduler")].options == ("normal", "karras")
    model = found[("4", "ckpt_name")]
    assert model.kind == wp.MODEL and model.node_title == "Base model"
    assert model.options == ("sdxl_base.safetensors", "flux.safetensors")
    assert found[("9", "filename_prefix")].kind == wp.STRING


def test_connected_bound_secret_and_structured_inputs_are_not_parameters():
    keys = {p.key for p in wp.describe_parameters(_graph(), _OBJECT_INFO)}
    # Connected: the width comes from the primitive, which is its own parameter.
    assert ("5", "width") not in keys and ("10", "value") in keys
    assert ("3", "model") not in keys
    # Bound: the positive prompt is what a run fills; the negative is a setting.
    assert ("6", "text") not in keys and ("7", "text") in keys
    assert ("11", "api_key") not in keys
    assert ("11", "resolution") not in keys and ("11", "scale") in keys


def test_every_picture_input_is_bound_not_only_the_one_a_run_fills():
    graph = _graph()
    graph["20"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
    graph["21"] = {"class_type": "LoadImage", "inputs": {"image": "b.png"}}
    keys = {p.key for p in wp.describe_parameters(graph)}
    assert ("20", "image") not in keys and ("21", "image") not in keys


def test_a_primitive_is_a_seed_only_when_it_feeds_one():
    found = _by_key(wp.describe_parameters(_graph(), _OBJECT_INFO))
    assert found[("10", "value")].kind == wp.INT

    graph = _graph()
    graph["3"]["inputs"]["seed"] = ["10", 0]
    for info in (_OBJECT_INFO, None):
        found = _by_key(wp.describe_parameters(graph, info))
        assert found[("10", "value")].kind == wp.SEED


def test_offline_the_recorded_values_are_described_without_ranges():
    found = _by_key(wp.describe_parameters(_graph(), None))
    steps = found[("3", "steps")]
    assert (steps.kind, steps.value, steps.minimum, steps.maximum) == (
        wp.INT,
        20,
        None,
        None,
    )
    assert found[("3", "seed")].kind == wp.SEED
    assert found[("3", "cfg")].kind == wp.FLOAT
    assert found[("3", "sampler_name")].kind == wp.STRING
    assert found[("3", "sampler_name")].options is None
    assert found[("4", "ckpt_name")].kind == wp.MODEL


def test_a_ui_format_file_has_no_readable_parameters():
    document = {"nodes": [{"id": 1, "type": "KSampler", "widgets_values": [42]}]}
    assert wp.api_graph(document) is None
    assert wp.describe_parameters(document, _OBJECT_INFO) == []


def test_the_default_pins_are_models_seeds_and_sampler_settings():
    pins = wp.default_pins(wp.describe_parameters(_graph(), _OBJECT_INFO))
    assert pins == [
        ("3", "seed"),
        ("3", "steps"),
        ("3", "cfg"),
        ("3", "sampler_name"),
        ("3", "scheduler"),
        ("3", "denoise"),
        ("4", "ckpt_name"),
        ("5", "height"),
        # The primitive that sets the width is the width control.
        ("10", "value"),
    ]


def test_nodes_are_in_number_order():
    graph = {
        "10": {"class_type": "A", "inputs": {"x": 1}},
        "9": {"class_type": "A", "inputs": {"x": 1}},
        "75:61": {"class_type": "A", "inputs": {"x": 1}},
    }
    order = [p.node_id for p in wp.describe_parameters(graph)]
    assert order == ["9", "10", "75:61"]


def test_pins_must_name_parameters_once():
    parameters = wp.describe_parameters(_graph(), _OBJECT_INFO)
    assert wp.validate_pins(parameters, [{"node_id": "3", "name": "steps"}]) == [
        ("3", "steps")
    ]
    with pytest.raises(ValueError, match="not a parameter"):
        wp.validate_pins(parameters, [{"node_id": "6", "name": "text"}])
    with pytest.raises(ValueError, match="twice"):
        wp.validate_pins(
            parameters,
            [{"node_id": "3", "name": "steps"}, {"node_id": "3", "name": "steps"}],
        )
    with pytest.raises(ValueError):
        wp.validate_pins(parameters, {"node_id": "3"})


def test_values_are_written_into_a_copy_when_they_fit():
    graph = _graph()
    parameters = wp.describe_parameters(graph, _OBJECT_INFO)
    updated = wp.apply_values(
        graph,
        parameters,
        [
            {"node_id": "3", "name": "steps", "value": 4},
            {"node_id": "3", "name": "sampler_name", "value": "dpmpp_2m"},
        ],
    )
    assert updated["3"]["inputs"]["steps"] == 4
    assert updated["3"]["inputs"]["sampler_name"] == "dpmpp_2m"
    assert graph["3"]["inputs"]["steps"] == 20


def test_the_envelope_format_is_read_and_written_under_prompt():
    document = {"prompt": _graph()}
    parameters = wp.describe_parameters(document, _OBJECT_INFO)
    assert ("3", "steps") in {p.key for p in parameters}
    updated = wp.apply_values(
        document, parameters, [{"node_id": "3", "name": "steps", "value": 8}]
    )
    assert updated["prompt"]["3"]["inputs"]["steps"] == 8


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("steps", 0, "at least"),
        ("steps", 20000, "at most"),
        ("steps", 2.5, "whole number"),
        ("steps", True, "whole number"),
        ("sampler_name", "unknown", "options"),
        ("cfg", "7", "number"),
    ],
)
def test_a_value_that_does_not_fit_is_refused(name, value, message):
    graph = _graph()
    parameters = wp.describe_parameters(graph, _OBJECT_INFO)
    with pytest.raises(ValueError, match=message):
        wp.apply_values(
            graph, parameters, [{"node_id": "3", "name": name, "value": value}]
        )


def test_a_bound_input_cannot_be_set_as_a_value():
    graph = _graph()
    parameters = wp.describe_parameters(graph, _OBJECT_INFO)
    with pytest.raises(ValueError, match="not a parameter"):
        wp.apply_values(
            graph, parameters, [{"node_id": "6", "name": "text", "value": "x"}]
        )


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "auth_token",
        "password",
        "secret_key",
        "access_key",
        "private_key",
        "auth",
        "authorization",
        "hfToken",
        "authToken",
    ],
)
def test_a_credential_name_is_never_a_parameter(name):
    graph = {"1": {"class_type": "Custom", "inputs": {name: "dummy-value"}}}
    assert wp.describe_parameters(graph) == []


def test_settings_that_only_contain_a_credential_word_stay():
    graph = {
        "1": {
            "class_type": "Custom",
            "inputs": {
                "token_normalization": "mean",
                "max_tokens": 256,
                "tokenizer": "clip",
                "author_name": "someone",
            },
        }
    }
    names = {p.name for p in wp.describe_parameters(graph)}
    assert names == {"token_normalization", "max_tokens", "tokenizer", "author_name"}


def test_a_numeric_combo_keeps_its_options_and_takes_one_of_them():
    graph = {"1": {"class_type": "Batcher", "inputs": {"batch": 2, "mode": "a"}}}
    info = {
        "Batcher": {
            "input": {
                "required": {
                    "batch": [[1, 2, 4, 8], {}],
                    "mode": ["COMBO", {"options": ["a", "b"], "remote": {"x": 1}}],
                }
            }
        }
    }
    parameters = wp.describe_parameters(graph, info)
    found = _by_key(parameters)
    assert found[("1", "batch")].options == (1, 2, 4, 8)
    # A remote combo's list is filled later, so it is not offered as the truth.
    assert found[("1", "mode")].options is None
    updated = wp.apply_values(
        graph, parameters, [{"node_id": "1", "name": "batch", "value": 8}]
    )
    assert updated["1"]["inputs"]["batch"] == 8
    with pytest.raises(ValueError, match="options"):
        wp.apply_values(
            graph, parameters, [{"node_id": "1", "name": "batch", "value": 3}]
        )


def test_a_bound_template_prompt_is_not_a_parameter():
    graph, _changed = workflow_bindings.migrate_placeholders(
        {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "photo of {{caption}}, sharp"},
            },
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}},
            "3": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
        }
    )
    keys = {p.key for p in wp.describe_parameters(graph)}
    assert ("1", "text") not in keys
    assert {("2", "text"), ("3", "filename_prefix")} <= keys


def test_a_loaders_own_picture_field_is_bound_whatever_it_is_called():
    graph = {
        "1": {
            "class_type": "PixlStashPictureLoader",
            "inputs": {"picture_ids": "12,13"},
        },
        "2": {"class_type": "Image Load", "inputs": {"image_path": "/home/me/a.png"}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    keys = {p.key for p in wp.describe_parameters(graph)}
    assert ("1", "picture_ids") not in keys and ("2", "image_path") not in keys


def test_a_primitive_is_a_seed_when_its_consumer_rerolls_the_input():
    """Decided as the run's seed detection decides it, not by the input's name."""
    graph = {
        "1": {"class_type": "PrimitiveInt", "inputs": {"value": 5}},
        "2": {"class_type": "CustomSampler", "inputs": {"rng": ["1", 0], "steps": 4}},
    }
    info = {
        "CustomSampler": {
            "input": {"required": {"rng": ["INT", {"control_after_generate": True}]}}
        },
        "PrimitiveInt": {
            "input": {"required": {"value": ["INT", {"control_after_generate": True}]}}
        },
    }
    found = _by_key(wp.describe_parameters(graph, info))
    assert found[("1", "value")].kind == wp.SEED
    assert ("1", "value") in wp.default_pins(list(found.values()))


def test_flux2_klein_pins_its_size_primitives_by_default():
    path = (
        pathlib.Path(__file__).parent.parent
        / "pixlstash/data/comfyui-workflows/built-in/Flux2-Klein-t2i.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    pins = wp.default_pins(wp.describe_parameters(document))
    assert ("75:68", "value") in pins and ("75:69", "value") in pins


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_number_is_refused_typed_or_not(value):
    graph = _graph()
    for info in (_OBJECT_INFO, None):
        parameters = wp.describe_parameters(graph, info)
        with pytest.raises(ValueError):
            wp.apply_values(
                graph, parameters, [{"node_id": "3", "name": "cfg", "value": value}]
            )


def test_an_untyped_number_takes_any_number_so_an_integer_cfg_takes_a_fraction():
    graph = _graph()
    graph["3"]["inputs"]["cfg"] = 1
    parameters = wp.describe_parameters(graph)
    updated = wp.apply_values(
        graph, parameters, [{"node_id": "3", "name": "cfg", "value": 1.5}]
    )
    assert updated["3"]["inputs"]["cfg"] == 1.5
    with pytest.raises(ValueError):
        wp.apply_values(
            graph, parameters, [{"node_id": "3", "name": "cfg", "value": "1.5"}]
        )


def test_a_node_id_that_only_looks_numeric_sorts_without_raising():
    graph = {
        "\u00b2": {"class_type": "A", "inputs": {"x": 1}},
        "2": {"class_type": "A", "inputs": {"x": 1}},
    }
    assert [p.node_id for p in wp.describe_parameters(graph)] == ["2", "\u00b2"]
