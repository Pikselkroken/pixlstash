"""Detection of a workflow's save node, picture inputs and prompts (#1302)."""

import asyncio
import json
import pathlib
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import pixlstash.routes.comfyui as comfyui_module
from pixlstash.services.workflow_inputs import (
    FIXED,
    PICKER,
    SELECTION,
    resolve_input_modes,
    validate_requested_modes,
)
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
        ("Flux2-Klein-Image-Edit.json", (("9",), ("76",), ("75:74",), ())),
        ("Flux2-Klein-t2i.json", (("9",), (), ("75:74",), ("75:67",))),
        ("Upscale-2x-RealESRGAN.json", (("4",), ("3",), (), ())),
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
    assert found.ambiguities == ()


@pytest.mark.parametrize(
    "name, positive, negative",
    [
        # Sampler, guider and encoders all live inside subgraph definitions.
        ("image_flux2_klein_t2i.json", ("75:74",), ("75:67",)),
        ("image_z_image.json", ("76:67",), ("76:71",)),
        # Negative runs through ConditioningZeroOut: no negative prompt.
        ("image_z_image_turbo.json", ("57:27",), ()),
        # Two KSamplerAdvanced on the same prompts are not ambiguous.
        ("video_wan2_2_14B_t2v.json", ("89",), ("72",)),
    ],
)
def test_ui_workflows_through_subgraphs(name, positive, negative):
    found = detect_workflow_io(_load(UI_FIXTURES / name))
    assert found.save_nodes == (() if name.startswith("video_") else ("9",))
    assert found.positive_prompts == positive
    assert found.negative_prompts == negative
    assert found.ambiguities == ()


def test_video_save_is_not_a_picture_save_node():
    found = detect_workflow_io(_load(UI_FIXTURES / "video_wan2_2_14B_t2v.json"))
    assert found.save_nodes == ()
    assert not found.valid


def test_two_save_nodes_are_reported():
    graph = _t2i_graph()
    graph["7"] = _node("SaveImage", images=["5", 0])
    found = detect_workflow_io(graph)
    assert found.save_nodes == ("6", "7")
    assert found.ambiguities == ("2 save nodes: 6, 7",)


def test_websocket_save_is_not_collected_so_not_valid():
    graph = _t2i_graph()
    graph["6"]["class_type"] = "SaveImageWebsocket"
    assert not detect_workflow_io(graph).valid


def test_prompt_envelope_and_custom_loader():
    graph = _t2i_graph()
    graph["7"] = _node("LoadImageFromPath", image="{{image_path}}")
    found = detect_workflow_io({"prompt": graph, "pixlstash_output_nodes": ["6"]})
    assert (found.save_nodes, found.picture_inputs) == (("6",), ("7",))
    assert found.positive_prompts == ("2",)


@pytest.mark.parametrize(
    "loader", ["PixlStashPictureLoader", "easy loadImageBase64", "Image Load"]
)
def test_loaders_the_name_rule_used_to_miss(loader):
    graph = _t2i_graph()
    graph["7"] = _node(loader, image="{{image_path}}")
    assert detect_workflow_io(graph).workflow_type == "i2i"


def test_an_explicit_output_choice_is_the_save_node():
    # Output collection honours pixlstash_output_nodes whatever the class.
    graph = _t2i_graph()
    graph["6"]["class_type"] = "PreviewImage"
    assert not detect_workflow_io(graph).valid
    graph["pixlstash_output_nodes"] = ["6"]
    found = detect_workflow_io(graph)
    assert found.save_nodes == ("6",)
    assert found.valid


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
    assert found.picture_inputs == ("7", "8")
    assert found.workflow_type == "i2i"
    assert found.ambiguities == ("2 picture inputs: 7, 8",)


def test_samplers_reading_different_prompts_leave_prompts_empty():
    graph = _t2i_graph()
    graph["7"] = _node("CLIPTextEncode", text="a dog", clip=["1", 1])
    graph["8"] = _node(
        "KSampler", model=["1", 0], positive=["7", 0], negative=["3", 0], seed=1
    )
    found = detect_workflow_io(graph)
    assert found.positive_prompts == ()
    assert found.negative_prompts == ()
    assert found.ambiguities == ("2 samplers read different prompts: 4, 8",)


def test_controlnet_sides_are_followed_by_output_slot():
    graph = _t2i_graph()
    graph["7"] = _node("ControlNetApplyAdvanced", positive=["2", 0], negative=["3", 0])
    graph["4"]["inputs"].update(positive=["7", 0], negative=["7", 1])
    found = detect_workflow_io(graph)
    assert (found.positive_prompts, found.negative_prompts) == (("2",), ("3",))
    assert found.ambiguities == ()


def test_a_pack_sampler_emitting_conditioning_is_not_read_by_slot():
    # KSampler (Efficient) emits CONDITIONING+ on slot 1 and CONDITIONING- on
    # 2; read as a ControlNet, slot 1 would name the negative encoder.
    graph = _t2i_graph()
    graph["7"] = _node(
        "KSampler (Efficient)", model=["1", 0], positive=["2", 0], negative=["3", 0]
    )
    graph["4"]["inputs"].update(positive=["7", 1], negative=["7", 2])
    found = detect_workflow_io(graph)
    assert (found.positive_prompts, found.negative_prompts) == ((), ())
    assert found.ambiguities == (
        "positive prompt not found",
        "negative prompt not found",
    )


def test_a_positive_only_passthrough_is_followed():
    graph = _t2i_graph()
    graph["7"] = _node("HunyuanImageToVideo", positive=["2", 0], vae=["1", 2])
    graph["4"]["inputs"]["positive"] = ["7", 0]
    found = detect_workflow_io(graph)
    assert found.positive_prompts == ("2",)
    assert found.ambiguities == ()


def test_a_dead_end_is_not_found_rather_than_no_prompt():
    graph = _t2i_graph()
    graph["7"] = _node("ImpactWildcardEncode", wildcard_text="a cat", clip=["1", 1])
    graph["4"]["inputs"]["positive"] = ["7", 0]
    found = detect_workflow_io(graph)
    assert (found.positive_prompts, found.negative_prompts) == ((), ("3",))
    assert found.ambiguities == ("positive prompt not found",)


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
    assert (found.positive_prompts, found.negative_prompts) == (("2", "9"), ("3",))
    assert found.ambiguities == ("2 positive prompts: 2, 9",)


def test_basic_guider_conditioning_is_the_positive_prompt():
    graph = {
        "1": _node("CLIPTextEncode", text="a cat"),
        "2": _node("FluxGuidance", conditioning=["1", 0], guidance=3.5),
        "3": _node("BasicGuider", conditioning=["2", 0]),
        "4": _node("SamplerCustomAdvanced", guider=["3", 0]),
        "5": _node("SaveImage", images=["4", 0]),
    }
    found = detect_workflow_io(graph)
    assert (found.positive_prompts, found.negative_prompts) == (("1",), ())


def test_list_route_classifies_by_detection(tmp_path, monkeypatch, caplog):
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
    comfyui_module._describe_workflow.cache_clear()
    workflows = endpoint()["workflows"]
    listed = {
        item["name"]: (item["valid"], item["workflow_type"]) for item in workflows
    }
    missing = {item["name"]: item["missing_placeholders"] for item in workflows}
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
    # A second listing reuses the descriptions: a broken file is logged once.
    caplog.clear()
    assert endpoint()["workflows"] == workflows
    assert "broken.json" not in caplog.text
    (tmp_path / "broken.json").write_text(json.dumps(t2i), encoding="utf-8")
    relisted = {item["name"]: item["valid"] for item in endpoint()["workflows"]}
    assert relisted["broken.json"] is True


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


# ---------------------------------------------------------------------------
# How each picture input is filled (#1305)
# ---------------------------------------------------------------------------


def _two_input_graph(bound: str | None = "81") -> dict:
    """Flux2BasicEdit's shape: two inputs, both titled Load Image."""
    graph = _t2i_graph()
    for node_id in ("76", "81"):
        graph[node_id] = _node(
            "LoadImage", image="{{image_path}}" if node_id == bound else "Logo.png"
        )
        graph[node_id]["_meta"] = {"title": "Load Image"}
    return graph


def _inputs(graph: dict) -> dict[str, str]:
    found = detect_workflow_io(graph)
    return dict(zip(found.picture_inputs, found.picture_input_classes))


def _modes(resolved) -> dict[str, tuple]:
    return {item.node_id: (item.mode, item.pixel_sha) for item in resolved}


def test_the_placeholder_input_defaults_to_selection_and_the_rest_to_picker():
    graph = _two_input_graph(bound="81")
    resolved = resolve_input_modes(graph, _inputs(graph), [])
    assert _modes(resolved) == {"76": (PICKER, None), "81": (SELECTION, None)}
    assert [item.title for item in resolved] == ["Load Image", "Load Image"]


def test_without_a_placeholder_the_first_input_is_selection():
    graph = _two_input_graph(bound=None)
    del graph["81"]["_meta"]
    resolved = resolve_input_modes(graph, _inputs(graph), [])
    assert _modes(resolved) == {"76": (SELECTION, None), "81": (PICKER, None)}
    # No title in the file: the class names it.
    assert resolved[1].title == "LoadImage"


def test_stored_modes_win_and_a_new_input_is_never_a_second_selection():
    graph = _two_input_graph()
    stored = [{"node_id": "76", "mode": FIXED, "pixel_sha": "abc"}]
    assert _modes(resolve_input_modes(graph, _inputs(graph), stored)) == {
        "76": (FIXED, "abc"),
        "81": (PICKER, None),
    }
    stored = [
        {"node_id": "76", "mode": SELECTION, "pixel_sha": None},
        {"node_id": "81", "mode": PICKER, "pixel_sha": None},
    ]
    assert _modes(resolve_input_modes(graph, _inputs(graph), stored))["76"] == (
        SELECTION,
        None,
    )


def test_a_setup_naming_only_nodes_the_file_lost_falls_back_to_defaults():
    graph = _two_input_graph(bound="81")
    stored = [{"node_id": "999", "mode": PICKER, "pixel_sha": None}]
    assert _modes(resolve_input_modes(graph, _inputs(graph), stored))["81"] == (
        SELECTION,
        None,
    )


def test_a_replaced_file_that_lost_its_selection_input_gets_a_default_one():
    graph = _two_input_graph(bound="81")
    stored = [
        {"node_id": "76", "mode": PICKER, "pixel_sha": None},
        {"node_id": "99", "mode": SELECTION, "pixel_sha": None},
    ]
    assert _modes(resolve_input_modes(graph, _inputs(graph), stored)) == {
        "76": (PICKER, None),
        "81": (SELECTION, None),
    }


def test_a_valid_setup_is_returned_in_order():
    inputs = _inputs(_two_input_graph())
    assert validate_requested_modes(
        inputs,
        [
            {"node_id": "81", "mode": SELECTION},
            {"node_id": "76", "mode": FIXED, "picture_id": 5},
        ],
    ) == [("81", SELECTION, None), ("76", FIXED, 5)]
    # No Selection at all is a choice: the workflow leaves the selection pill.
    assert validate_requested_modes(
        inputs,
        [{"node_id": "81", "mode": PICKER}, {"node_id": "76", "mode": PICKER}],
    ) == [("81", PICKER, None), ("76", PICKER, None)]


@pytest.mark.parametrize(
    "requested, message",
    [
        (
            [
                {"node_id": "81", "mode": SELECTION},
                {"node_id": "76", "mode": SELECTION},
            ],
            "at most one",
        ),
        ([{"node_id": "81", "mode": SELECTION}], "exactly once"),
        (
            [
                {"node_id": "81", "mode": SELECTION},
                {"node_id": "81", "mode": PICKER},
                {"node_id": "76", "mode": PICKER},
            ],
            "exactly once",
        ),
        (
            [
                {"node_id": "81", "mode": SELECTION},
                {"node_id": "76", "mode": FIXED, "picture_id": True},
            ],
            "picture_id",
        ),
        (
            [{"node_id": "81", "mode": "grid"}, {"node_id": "76", "mode": PICKER}],
            "mode must be",
        ),
        ([{"node_id": 81, "mode": SELECTION}, {"node_id": "76"}], "node_id"),
        ({"81": SELECTION}, "list"),
    ],
)
def test_a_bad_setup_is_refused(requested, message):
    with pytest.raises(ValueError, match=message):
        validate_requested_modes(_inputs(_two_input_graph()), requested)


def _list_endpoint():
    router = comfyui_module.create_router(MagicMock())
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/comfyui/workflows"
        and "GET" in route.methods
    )


def test_the_list_says_which_workflows_the_selection_pill_may_offer(
    tmp_path, monkeypatch
):
    graph = _two_input_graph()
    graph["2"]["inputs"]["text"] = "{{caption}}"
    unrecognised = _t2i_graph()
    unrecognised["7"] = _node("MyPictureSource", image="{{image_path}}")
    unbound = _two_input_graph(bound=None)
    for name, document in (
        ("edit.json", graph),
        ("unset.json", graph),
        ("unbound.json", unbound),
        ("t2i.json", _t2i_graph()),
        ("custom.json", unrecognised),
    ):
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(comfyui_module, "_workflow_dirs", lambda: [("user", tmp_path)])
    stored = {
        "edit.json": [
            {"node_id": "76", "mode": PICKER, "pixel_sha": None},
            {"node_id": "81", "mode": FIXED, "pixel_sha": "abc"},
        ]
    }
    monkeypatch.setattr(comfyui_module, "input_modes_by_workflow", lambda *_: stored)
    comfyui_module._describe_workflow.cache_clear()

    listed = {
        item["name"]: item["has_selection_input"]
        for item in _list_endpoint()()["workflows"]
    }
    assert listed == {
        "edit.json": False,
        "unset.json": True,
        # run_i2i has no placeholder to fill until runs use the modes (#1307).
        "unbound.json": False,
        "t2i.json": False,
        # A loader detection misses keeps its placeholder binding.
        "custom.json": True,
    }


def test_run_i2i_refuses_a_workflow_with_no_selection_input(tmp_path, monkeypatch):
    (tmp_path / "edit.json").write_text(
        json.dumps(_two_input_graph()), encoding="utf-8"
    )
    monkeypatch.setattr(comfyui_module, "_workflow_dirs", lambda: [("user", tmp_path)])
    stored = {
        "edit.json": [
            {"node_id": "76", "mode": PICKER, "pixel_sha": None},
            {"node_id": "81", "mode": PICKER, "pixel_sha": None},
        ]
    }
    monkeypatch.setattr(comfyui_module, "input_modes_by_workflow", lambda *_: stored)
    router = comfyui_module.create_router(MagicMock())
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/comfyui/run_i2i"
    )
    with pytest.raises(HTTPException) as refused:
        asyncio.run(
            endpoint(MagicMock(), {"workflow_name": "edit", "picture_ids": [1]})
        )
    assert refused.value.status_code == 400
    assert "selection" in refused.value.detail
