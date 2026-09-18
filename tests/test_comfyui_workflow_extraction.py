"""Tests for ComfyUI workflow extraction utilities.

Reads all workflow JSON files from tests/comfyui_workflows/ and compares
extracted generation info against tests/comfyui_workflows/expected_results.csv.

Run with: python -m pytest -s tests/test_comfyui_workflow_extraction.py
"""

import csv
import json
import pathlib

import pytest

from pixlstash.utils.comfyui_utilities import (
    extract_comfy_workflow_info,
    extract_generation_info,
    find_comfy_workflow,
)

WORKFLOWS_DIR = pathlib.Path(__file__).parent / "comfyui_workflows"
EXPECTED_CSV = WORKFLOWS_DIR / "expected_results.csv"


def _workflow_files() -> list[pathlib.Path]:
    return sorted(WORKFLOWS_DIR.glob("*.json"))


def _load_expected() -> dict[str, dict]:
    """Return expected results keyed by filename."""
    with EXPECTED_CSV.open(newline="") as f:
        return {row["filename"]: row for row in csv.DictReader(f)}


@pytest.mark.parametrize("workflow_file", _workflow_files(), ids=lambda p: p.name)
def test_extract_generation_info(workflow_file: pathlib.Path) -> None:
    """Compare extraction output against expected_results.csv."""
    expected_all = _load_expected()
    expected = expected_all.get(workflow_file.name)
    assert expected is not None, (
        f"{workflow_file.name} has no entry in expected_results.csv - "
        "run the extraction, verify the output, and add a row to the CSV."
    )

    workflow = json.loads(workflow_file.read_text())
    result = extract_generation_info(workflow)

    actual_models = "|".join(result["models"])
    actual_loras = "|".join(result["loras"])
    actual_seed = str(result["seed"]) if result["seed"] is not None else ""
    actual_prompt = (
        (result["positive_prompt"] or "").replace("\n", " ").replace("\r", "")
    )

    assert actual_models == expected["models"], (
        f"models mismatch for {workflow_file.name}"
    )
    assert actual_loras == expected["loras"], f"loras mismatch for {workflow_file.name}"
    assert actual_seed == expected["seed"], f"seed mismatch for {workflow_file.name}"
    assert actual_prompt == expected["positive_prompt"], (
        f"positive_prompt mismatch for {workflow_file.name}"
    )


@pytest.mark.parametrize("workflow_file", _workflow_files(), ids=lambda p: p.name)
def test_extract_comfy_workflow_info(workflow_file: pathlib.Path) -> None:
    """Smoke test: top-level extraction runs without errors and returns expected keys."""
    metadata = {"workflow": workflow_file.read_text()}
    result = extract_comfy_workflow_info(metadata)

    assert "workflow" in result
    assert "is_api_format" in result
    assert "summary" in result
    assert "models" in result
    assert "loras" in result
    assert "positive_prompt" in result
    assert "seed" in result


# Minimal API-format graph, as ComfyUI writes into the PNG ``prompt`` chunk.
_API_GRAPH = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 12345, "steps": 20, "model": ["4", 0]},
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
}

# Minimal UI-format graph, as the ComfyUI frontend writes into the
# ``workflow`` chunk.
_UI_GRAPH = {
    "last_node_id": 4,
    "last_link_id": 1,
    "nodes": [
        {"id": 3, "type": "KSampler", "widgets_values": [12345, "randomize", 20]},
    ],
    "links": [[1, 4, 0, 3, 0, "MODEL"]],
}


class TestFindComfyWorkflowPromptFallback:
    """Display fallback to the PNG ``prompt`` chunk (issue #628).

    PixlStash-generated PNGs no longer carry a ``workflow`` chunk, so the
    ``prompt`` chunk ComfyUI writes is the only displayable graph left. It
    must be picked up, but only at lower priority than any real ``workflow``
    candidate, and never when the ``prompt`` value is plain text.
    """

    def test_falls_back_to_the_png_prompt_chunk(self):
        # A newly generated PixlStash file: prompt chunk only, no workflow.
        metadata = {"png": {"prompt": json.dumps(_API_GRAPH)}}
        assert find_comfy_workflow(metadata) == _API_GRAPH

    def test_falls_back_to_top_level_and_comfyui_block_prompt(self):
        assert find_comfy_workflow({"prompt": json.dumps(_API_GRAPH)}) == _API_GRAPH
        assert (
            find_comfy_workflow({"comfyui": {"prompt": json.dumps(_API_GRAPH)}})
            == _API_GRAPH
        )

    def test_a_genuine_ui_workflow_chunk_still_wins(self):
        # A normal ComfyUI-frontend PNG has both chunks; the UI graph is the
        # one meant for display and must keep priority.
        metadata = {
            "png": {
                "workflow": json.dumps(_UI_GRAPH),
                "prompt": json.dumps(_API_GRAPH),
            }
        }
        assert find_comfy_workflow(metadata) == _UI_GRAPH

    def test_old_pixlstash_files_with_api_graph_in_workflow_chunk_still_resolve(self):
        # Files generated before issue #628 embedded the API graph in the
        # workflow chunk; they must continue to display.
        metadata = {"png": {"workflow": json.dumps(_API_GRAPH)}}
        assert find_comfy_workflow(metadata) == _API_GRAPH

    def test_plain_text_prompt_is_not_misdetected_as_a_workflow(self):
        # Other tools store the literal text prompt under "prompt".
        text = "a cat riding a bicycle, masterpiece, 8k"
        assert find_comfy_workflow({"png": {"prompt": text}}) is None
        assert find_comfy_workflow({"prompt": text}) is None

    def test_json_but_non_workflow_prompt_value_is_rejected(self):
        # Even valid JSON under "prompt" is not a workflow unless it passes
        # is_comfy_workflow.
        metadata = {"png": {"prompt": json.dumps({"text": "a cat", "steps": 20})}}
        assert find_comfy_workflow(metadata) is None


# ===========================================================================
# What `extract_comfy_workflow_info` surfaces for the Recipe tab (#1313)
#
# The negative prompt itself is `extract_recipe_extras`' job, added and unit
# tested by B5 (`tests/test_comfyui_recipe_preflight.py`). What is asserted here
# is only what this branch added on top: that the workflow read SURFACES it, and
# that the seed is carried as text so a 64-bit one survives a JS `Number`.
# ===========================================================================

_SAMPLER_GRAPH = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 18446744073709551615,
            "positive": ["6", 0],
            "negative": ["7", 0],
        },
    },
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a castle on a hill"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, watermark"}},
}


def test_the_workflow_read_surfaces_both_prompts():
    info = extract_comfy_workflow_info({"prompt": json.dumps(_SAMPLER_GRAPH)})
    assert info["positive_prompt"] == "a castle on a hill"
    assert info["negative_prompt"] == "blurry, watermark"


def test_a_ui_only_workflow_surfaces_no_negative_prompt():
    """`extract_recipe_extras` reads the API format only, so a UI graph has to
    report None rather than reach it with the wrong shape."""
    ui = {"nodes": [], "links": [], "last_node_id": 1}
    info = extract_comfy_workflow_info({"workflow": json.dumps(ui)})
    assert info["is_api_format"] is False
    assert info["negative_prompt"] is None


def test_the_seed_is_carried_as_text_so_a_64_bit_one_survives_javascript():
    """`2**64-1` through a JS Number comes back 18446744073709552000."""
    info = extract_comfy_workflow_info({"prompt": json.dumps(_SAMPLER_GRAPH)})
    assert info["seed_text"] == "18446744073709551615"
    assert int(info["seed_text"]) == info["seed"]


def test_a_graph_with_no_seed_has_no_seed_text_rather_than_the_string_none():
    graph = {"9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}}}
    info = extract_comfy_workflow_info({"prompt": json.dumps(graph)})
    assert info["seed"] is None and info["seed_text"] is None


# ===========================================================================
# The facts come from the graph that RAN, not the editor's view
#
# Reported against the shipped Recipe tab: "the prompt has nothing to do with
# reality, but Generate variants shows the correct one". Generate variants reads
# `GET /comfyui/pictures/{id}/recipe`, which uses the API `prompt` chunk; this
# read preferred the UI `workflow` chunk, whose text is recovered by mapping
# named inputs onto positional `widgets_values` and, failing that, taking the
# longest string in the node. A custom prompt-builder feeding the encoder is
# enough to make the two disagree completely.
# ===========================================================================

_EXECUTED = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 777,
            "positive": ["6", 0],
            "negative": ["7", 0],
            "model": ["4", 0],
        },
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "realvisXL_v5.safetensors"},
    },
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a castle on a hill"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}},
}

# The same graph as the editor stores it: the encoder's text arrives over a link
# from a builder node, so the encoder's own widget is empty and the only string
# to be found on the builder is its template.
_EDITOR_VIEW = {
    "last_node_id": 9,
    "last_link_id": 5,
    "nodes": [
        {
            "id": 3,
            "type": "KSampler",
            "inputs": [{"name": "positive", "link": 2, "type": "CONDITIONING"}],
            "widgets_values": [777, "randomize", 25],
        },
        {
            "id": 6,
            "type": "CLIPTextEncode",
            "inputs": [
                {"name": "clip", "link": 4},
                {"name": "text", "link": 5, "type": "STRING"},
            ],
            "widgets_values": [""],
        },
        {
            "id": 9,
            "type": "LoRACharacterPromptBuilder",
            "inputs": [],
            "widgets_values": ["wildcards_v3", "a template, not the prompt", "off"],
        },
    ],
    "links": [[5, 9, 0, 6, 1, "STRING"], [2, 6, 0, 3, 1, "CONDITIONING"]],
}

_BOTH_CHUNKS = {
    "png": {
        "workflow": json.dumps(_EDITOR_VIEW),
        "prompt": json.dumps(_EXECUTED),
    }
}


def test_the_prompt_is_the_one_that_ran_not_the_editors_longest_string():
    info = extract_comfy_workflow_info(_BOTH_CHUNKS)
    assert info["positive_prompt"] == "a castle on a hill"
    assert info["negative_prompt"] == "blurry"


def test_the_models_and_seed_come_from_the_executed_graph_too():
    """The same defect, and the reason the fix is not scoped to the prompt: the
    editor's view surrenders these entirely."""
    info = extract_comfy_workflow_info(_BOTH_CHUNKS)
    assert info["models"] == ["realvisXL_v5.safetensors"]
    assert info["seed"] == 777


def test_the_graph_shown_is_still_the_editors_view():
    """Copy and Download exist so a workflow can be pasted back into ComfyUI,
    and the API graph is not what the editor opens."""
    info = extract_comfy_workflow_info(_BOTH_CHUNKS)
    assert info["is_api_format"] is False
    assert info["workflow"] == _EDITOR_VIEW


def test_a_file_with_only_an_editor_view_still_reads_what_it_can():
    """No `prompt` chunk: the editor's view is genuinely all there is, so the
    read falls back to it rather than reporting nothing.

    Drawn with the text on the encoder's own widget, which is where the UI walk
    is reliable - the point is that the fallback still runs, not that the
    heuristic behind it is good.
    """
    ui_only = {
        "last_node_id": 6,
        "last_link_id": 2,
        "nodes": [
            {
                "id": 3,
                "type": "KSampler",
                "inputs": [{"name": "positive", "link": 2, "type": "CONDITIONING"}],
                "widgets_values": [4242, "randomize", 25],
            },
            {
                "id": 6,
                "type": "CLIPTextEncode",
                "inputs": [{"name": "clip", "link": 1, "widget": {"name": "text"}}],
                "widgets_values": ["a lighthouse in fog"],
            },
        ],
        "links": [[2, 6, 0, 3, 1, "CONDITIONING"]],
    }
    info = extract_comfy_workflow_info({"png": {"workflow": json.dumps(ui_only)}})
    assert info["workflow"] == ui_only
    assert info["positive_prompt"] == "a lighthouse in fog"
    # `extract_recipe_extras` reads the API format only, so there is none.
    assert info["negative_prompt"] is None
