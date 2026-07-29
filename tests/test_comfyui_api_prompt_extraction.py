"""Tests for the embedded API-format ``prompt`` chunk reader (Remix v1.9).

The distinction under test is the whole point of recipe mode: ComfyUI embeds a
UI ``workflow`` chunk (not submittable) and an API ``prompt`` chunk (the graph
the server actually executed). Only the latter may be replayed, and the UI
graph must never be converted or substituted for it.
"""

import json

import pytest

from pixlstash.utils.comfyui_utilities import (
    collect_seed_inputs,
    find_comfy_api_prompt,
    find_comfy_workflow,
    is_api_format,
)

API_GRAPH = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 12345, "steps": 20, "model": ["4", 0]},
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "ComfyUI", "images": ["3", 0]},
    },
}

UI_GRAPH = {
    "last_node_id": 9,
    "last_link_id": 9,
    "nodes": [
        {"id": 3, "type": "KSampler", "widgets_values": [12345, "randomize", 20]},
        {
            "id": 4,
            "type": "CheckpointLoaderSimple",
            "widgets_values": ["sd_xl_base_1.0.safetensors"],
        },
    ],
    "links": [[1, 4, 0, 3, 0, "MODEL"]],
}


class TestIsApiFormat:
    def test_api_graph(self):
        assert is_api_format(API_GRAPH) is True

    def test_ui_graph(self):
        assert is_api_format(UI_GRAPH) is False

    @pytest.mark.parametrize(
        "hint", [{"nodes": []}, {"links": []}, {"last_node_id": 1}, {"last_link_id": 1}]
    )
    def test_any_ui_hint_disqualifies(self, hint):
        assert is_api_format(hint) is False

    def test_non_dict(self):
        assert is_api_format(None) is False
        assert is_api_format("{}") is False


class TestFindComfyApiPrompt:
    def test_reads_the_png_prompt_chunk(self):
        metadata = {"png": {"prompt": json.dumps(API_GRAPH)}}
        assert find_comfy_api_prompt(metadata) == API_GRAPH

    def test_accepts_an_already_parsed_dict(self):
        assert find_comfy_api_prompt({"png": {"prompt": API_GRAPH}}) == API_GRAPH

    def test_reads_top_level_and_comfyui_block(self):
        assert find_comfy_api_prompt({"prompt": json.dumps(API_GRAPH)}) == API_GRAPH
        assert (
            find_comfy_api_prompt({"comfyui": {"prompt": json.dumps(API_GRAPH)}})
            == API_GRAPH
        )

    def test_never_falls_back_to_the_ui_workflow_chunk(self):
        # The whole safety property: a UI-graph-only file has NO executable
        # recipe, even though find_comfy_workflow happily returns the UI graph.
        metadata = {"png": {"workflow": json.dumps(UI_GRAPH)}}
        assert find_comfy_workflow(metadata) == UI_GRAPH
        assert find_comfy_api_prompt(metadata) is None

    def test_rejects_a_ui_graph_stored_under_the_prompt_key(self):
        assert find_comfy_api_prompt({"png": {"prompt": json.dumps(UI_GRAPH)}}) is None

    def test_prefers_the_prompt_chunk_when_both_are_present(self):
        metadata = {
            "png": {
                "prompt": json.dumps(API_GRAPH),
                "workflow": json.dumps(UI_GRAPH),
            }
        }
        assert find_comfy_api_prompt(metadata) == API_GRAPH

    def test_a1111_metadata_yields_nothing(self):
        metadata = {
            "png": {"parameters": "a cat, Steps: 20, Sampler: Euler a, Seed: 12345"}
        }
        assert find_comfy_api_prompt(metadata) is None

    @pytest.mark.parametrize(
        "metadata", [None, {}, {"png": {}}, {"exif": {"Make": "x"}}]
    )
    def test_stripped_or_absent_metadata(self, metadata):
        assert find_comfy_api_prompt(metadata) is None

    def test_unparseable_prompt_chunk(self):
        assert find_comfy_api_prompt({"png": {"prompt": "not json {{{"}}) is None


class TestCollectSeedInputs:
    def test_finds_a_ksampler_seed(self):
        found = collect_seed_inputs(API_GRAPH)
        assert found == [
            {"node_id": "3", "class_type": "KSampler", "field": "seed", "value": 12345}
        ]

    def test_finds_random_noise_noise_seed(self):
        graph = {"7": {"class_type": "RandomNoise", "inputs": {"noise_seed": 99}}}
        assert collect_seed_inputs(graph) == [
            {
                "node_id": "7",
                "class_type": "RandomNoise",
                "field": "noise_seed",
                "value": 99,
            }
        ]

    def test_ignores_a_linked_seed_input(self):
        # ["5", 0] is a node reference, not a value we may overwrite.
        graph = {"3": {"class_type": "KSampler", "inputs": {"seed": ["5", 0]}}}
        assert collect_seed_inputs(graph) == []

    def test_empty_for_a_graph_with_no_known_seed_node(self):
        graph = {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}}}
        assert collect_seed_inputs(graph) == []

    def test_tolerates_junk(self):
        assert collect_seed_inputs({}) == []
        assert collect_seed_inputs(None) == []
        assert collect_seed_inputs({"3": "not a node"}) == []
