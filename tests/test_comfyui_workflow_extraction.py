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
