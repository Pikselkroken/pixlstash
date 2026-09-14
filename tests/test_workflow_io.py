"""Detection of a workflow's save node, picture inputs and prompts (#1302)."""

import asyncio
import json
import pathlib
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import pixlstash.routes.comfyui as comfyui_module
from pixlstash.services.workflow_io import detect_workflow_io

BUILT_IN = (
    pathlib.Path(comfyui_module.__file__).parent.parent
    / "data"
    / "comfyui-workflows"
    / "built-in"
)
UI_FIXTURES = pathlib.Path(__file__).parent / "comfyui_workflows"


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _node(class_type: str, **inputs) -> dict:
    return {"class_type": class_type, "inputs": inputs}


def _t2i_graph() -> dict:
    return {
        "1": _node("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
        "2": _node("CLIPTextEncode", text="a cat", clip=["1", 1]),
        "3": _node("CLIPTextEncode", text="blurry", clip=["1", 1]),
        "4": _node(
            "KSampler", model=["1", 0], positive=["2", 0], negative=["3", 0], seed=1
        ),
        "5": _node("VAEDecode", samples=["4", 0], vae=["1", 2]),
        "6": _node("SaveImage", images=["5", 0], filename_prefix="x"),
    }


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Flux2-Klein-Image-Edit.json", (["9"], ["76"], ["75:74"], [])),
        ("Flux2-Klein-t2i.json", (["9"], [], ["75:74"], ["75:67"])),
        ("Upscale-2x-RealESRGAN.json", (["4"], ["3"], [], [])),
    ],
)
def test_built_in_api_workflows(name, expected):
    found = detect_workflow_io(_load(BUILT_IN / name))
    assert (
        found.save_nodes,
        found.picture_inputs,
        found.positive_prompts,
        found.negative_prompts,
    ) == expected
    assert found.ambiguities == []


@pytest.mark.parametrize(
    "name, positive, negative",
    [
        # Sampler, guider and encoders all live inside subgraph definitions.
        ("image_flux2_klein_t2i.json", ["75:74"], ["75:67"]),
        ("image_z_image.json", ["76:67"], ["76:71"]),
        # Negative runs through ConditioningZeroOut: no negative prompt.
        ("image_z_image_turbo.json", ["57:27"], []),
        # Two KSamplerAdvanced on the same prompts are not ambiguous.
        ("video_wan2_2_14B_t2v.json", ["89"], ["72"]),
    ],
)
def test_ui_workflows_through_subgraphs(name, positive, negative):
    found = detect_workflow_io(_load(UI_FIXTURES / name))
    assert found.save_nodes == ([] if name.startswith("video_") else ["9"])
    assert found.positive_prompts == positive
    assert found.negative_prompts == negative
    assert found.ambiguities == []


def test_video_save_is_not_a_picture_save_node():
    found = detect_workflow_io(_load(UI_FIXTURES / "video_wan2_2_14B_t2v.json"))
    assert found.save_nodes == []
    assert not found.valid


def test_two_save_nodes_are_reported():
    graph = _t2i_graph()
    graph["7"] = _node("SaveImage", images=["5", 0])
    found = detect_workflow_io(graph)
    assert found.save_nodes == ["6", "7"]
    assert found.ambiguities == ["2 save nodes: 6, 7"]


def test_websocket_save_is_not_collected_so_not_valid():
    graph = _t2i_graph()
    graph["6"]["class_type"] = "SaveImageWebsocket"
    assert not detect_workflow_io(graph).valid


def test_prompt_envelope_and_custom_loader():
    graph = _t2i_graph()
    graph["7"] = _node("LoadImageFromPath", image="{{image_path}}")
    found = detect_workflow_io({"prompt": graph, "pixlstash_output_nodes": ["6"]})
    assert (found.save_nodes, found.picture_inputs) == (["6"], ["7"])
    assert found.positive_prompts == ["2"]


def test_detection_never_writes_the_document():
    graph = _t2i_graph()
    before = json.dumps(graph, sort_keys=True)
    detect_workflow_io(graph)
    assert json.dumps(graph, sort_keys=True) == before


def test_two_image_loaders_are_reported_not_chosen():
    graph = _t2i_graph()
    graph["7"] = _node("LoadImage", image="a.png")
    graph["8"] = _node("LoadImage", image="b.png")
    found = detect_workflow_io(graph)
    assert found.picture_inputs == ["7", "8"]
    assert found.workflow_type == "i2i"
    assert found.ambiguities == ["2 picture inputs: 7, 8"]


def test_samplers_reading_different_prompts_leave_prompts_empty():
    graph = _t2i_graph()
    graph["7"] = _node("CLIPTextEncode", text="a dog", clip=["1", 1])
    graph["8"] = _node(
        "KSampler", model=["1", 0], positive=["7", 0], negative=["3", 0], seed=1
    )
    found = detect_workflow_io(graph)
    assert found.positive_prompts == []
    assert found.negative_prompts == []
    assert found.ambiguities == ["2 samplers read different prompts: 4, 8"]


def test_controlnet_sides_are_followed_by_output_slot():
    graph = _t2i_graph()
    graph["7"] = _node("ControlNetApplyAdvanced", positive=["2", 0], negative=["3", 0])
    graph["4"]["inputs"].update(positive=["7", 0], negative=["7", 1])
    found = detect_workflow_io(graph)
    assert (found.positive_prompts, found.negative_prompts) == (["2"], ["3"])
    assert found.ambiguities == []


def test_conditioning_nodes_taking_prompts_are_not_samplers():
    # The ControlNet reads only encoder 2 while the sampler reads 2 and 9;
    # counted as a sampler it would turn this into a disagreement.
    graph = _t2i_graph()
    graph["7"] = _node("ControlNetApplyAdvanced", positive=["2", 0], negative=["3", 0])
    graph["9"] = _node("CLIPTextEncode", text="a hat", clip=["1", 1])
    graph["10"] = _node(
        "ConditioningCombine", conditioning_1=["7", 0], conditioning_2=["9", 0]
    )
    graph["4"]["inputs"].update(positive=["10", 0], negative=["7", 1])
    found = detect_workflow_io(graph)
    assert (found.positive_prompts, found.negative_prompts) == (["2", "9"], ["3"])
    assert found.ambiguities == ["2 positive prompts: 2, 9"]


def test_basic_guider_conditioning_is_the_positive_prompt():
    graph = {
        "1": _node("CLIPTextEncode", text="a cat"),
        "2": _node("FluxGuidance", conditioning=["1", 0], guidance=3.5),
        "3": _node("BasicGuider", conditioning=["2", 0]),
        "4": _node("SamplerCustomAdvanced", guider=["3", 0]),
        "5": _node("SaveImage", images=["4", 0]),
    }
    found = detect_workflow_io(graph)
    assert (found.positive_prompts, found.negative_prompts) == (["1"], [])


def test_list_route_classifies_by_detection(tmp_path, monkeypatch):
    t2i = _t2i_graph()
    i2i = _t2i_graph()
    i2i["7"] = _node("LoadImage", image="a.png")
    no_save = _t2i_graph()
    del no_save["6"]
    for name, graph in (("t2i", t2i), ("i2i", i2i), ("nosave", no_save)):
        (tmp_path / f"{name}.json").write_text(json.dumps(graph), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    # Detection raises on this, but the placeholder scan it carries still holds.
    (tmp_path / "nograph.json").write_text(
        json.dumps({"note": "{{image_path}} {{caption}}"}), encoding="utf-8"
    )
    monkeypatch.setattr(comfyui_module, "_workflow_dirs", lambda: [("user", tmp_path)])

    router = comfyui_module.create_router(MagicMock())
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/comfyui/workflows"
        and "GET" in route.methods
    )
    listed = {
        item["name"]: (item["valid"], item["workflow_type"])
        for item in asyncio.run(endpoint())["workflows"]
    }
    missing = {
        item["name"]: item["missing_placeholders"]
        for item in asyncio.run(endpoint())["workflows"]
    }
    assert missing["nograph.json"] == []
    assert len(missing["broken.json"]) == 2
    # None of these carry a placeholder, so placeholder detection would have
    # called every one of them an invalid t2i.
    assert listed == {
        "t2i.json": (True, "t2i"),
        "i2i.json": (True, "i2i"),
        "nosave.json": (False, "t2i"),
        "broken.json": (False, "t2i"),
        "nograph.json": (False, "t2i"),
    }


def test_run_i2i_refuses_a_workflow_without_the_image_placeholder(
    tmp_path, monkeypatch
):
    # A fixed LoadImage now lists this as i2i; without {{image_path}} the
    # selected picture would never reach the graph.
    graph = _t2i_graph()
    graph["2"]["inputs"]["text"] = "{{caption}}"
    graph["7"] = _node("LoadImage", image="pose.png")
    (tmp_path / "fixed.json").write_text(json.dumps(graph), encoding="utf-8")
    monkeypatch.setattr(comfyui_module, "_workflow_dirs", lambda: [("user", tmp_path)])

    router = comfyui_module.create_router(MagicMock())
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/comfyui/run_i2i"
    )
    with pytest.raises(HTTPException) as refused:
        asyncio.run(
            endpoint(MagicMock(), {"workflow_name": "fixed", "picture_ids": [1]})
        )
    assert refused.value.status_code == 400
    assert "{{image_path}}" in refused.value.detail
