"""Tests for ComfyUI workflow extraction utilities.

Reads all workflow JSON files from tests/comfyui_workflows/ and prints
extracted generation info as CSV. No hard assertions — this test documents
what each workflow yields and serves as a smoke test.

Run with: python -m pytest -s tests/test_comfyui_workflow_extraction.py
"""

import csv
import io
import json
import pathlib

import pytest

from pixlstash.utils.comfyui_utilities import extract_comfy_workflow_info, extract_generation_info

WORKFLOWS_DIR = pathlib.Path(__file__).parent / "comfyui_workflows"


def _workflow_files() -> list[pathlib.Path]:
    return sorted(WORKFLOWS_DIR.glob("*.json"))


@pytest.mark.parametrize("workflow_file", _workflow_files(), ids=lambda p: p.name)
def test_extract_generation_info(workflow_file: pathlib.Path) -> None:
    """Smoke test: extraction runs without errors and returns expected keys."""
    workflow = json.loads(workflow_file.read_text())
    result = extract_generation_info(workflow)

    assert isinstance(result["models"], list)
    assert isinstance(result["loras"], list)
    assert result["seed"] is None or isinstance(result["seed"], int)
    assert result["positive_prompt"] is None or isinstance(result["positive_prompt"], str)


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


def test_print_extraction_csv(capsys: pytest.CaptureFixture) -> None:
    """Print extraction results for all workflows as CSV to stdout.

    Run with pytest -s to see the output.
    """
    workflow_files = _workflow_files()
    assert workflow_files, f"No workflow JSON files found in {WORKFLOWS_DIR}"

    rows: list[dict] = []
    for wf_file in workflow_files:
        workflow = json.loads(wf_file.read_text())
        result = extract_generation_info(workflow)

        prompt = result["positive_prompt"] or ""
        prompt_short = prompt[:120].replace("\n", " ").replace("\r", "")

        rows.append(
            {
                "filename": wf_file.name,
                "models": "|".join(result["models"]),
                "loras": "|".join(result["loras"]),
                "seed": str(result["seed"]) if result["seed"] is not None else "",
                "positive_prompt": prompt_short,
            }
        )

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["filename", "models", "loras", "seed", "positive_prompt"],
        quoting=csv.QUOTE_ALL,
    )
    writer.writeheader()
    writer.writerows(rows)

    print("\n" + buf.getvalue())
