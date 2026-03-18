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
        f"{workflow_file.name} has no entry in expected_results.csv — "
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
