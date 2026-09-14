"""Detection of a workflow's inputs and outputs (#1302), and import as-is (#1303)."""

import asyncio
import json
import pathlib
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import pixlstash.routes.comfyui as comfyui_module
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflows import topology_exists
from pixlstash.services import workflow_bindings
from pixlstash.services.workflow_hash import topology_hash
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
    # A token is no longer a target: only a binding or detection is.
    assert len(missing["nograph.json"]) == 2
    assert len(missing["broken.json"]) == 2
    assert missing["i2i.json"] == []
    assert missing["t2i.json"] == ["{{image_path}}"]
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


def _route(router, path, method="POST"):
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == path and method in route.methods
    )


def test_run_i2i_refuses_a_migrated_workflow_without_an_image_binding(
    tmp_path, monkeypatch
):
    # The #1350 case, kept by its bindings: a caption-only workflow with a fixed
    # reference LoadImage. Detection would fill that LoadImage; the migrated
    # bindings say the selected picture never had a place in this graph.
    graph = _t2i_graph()
    graph["2"]["inputs"]["text"] = "{{caption}}"
    graph["7"] = _node("LoadImage", image="pose.png")
    migrated, changed = workflow_bindings.migrate_placeholders(graph)
    assert changed
    (tmp_path / "fixed.json").write_text(json.dumps(migrated), encoding="utf-8")
    monkeypatch.setattr(comfyui_module, "_workflow_dirs", lambda: [("user", tmp_path)])

    endpoint = _route(comfyui_module.create_router(MagicMock()), "/comfyui/run_i2i")
    with pytest.raises(HTTPException) as refused:
        asyncio.run(
            endpoint(MagicMock(), {"workflow_name": "fixed", "picture_ids": [1]})
        )
    assert refused.value.status_code == 400
    assert "no picture input" in refused.value.detail


def test_migration_binds_tokens_and_restores_neutral_values():
    graph = _t2i_graph()
    graph["2"]["inputs"]["text"] = "{{caption}}"
    graph["3"]["inputs"]["text"] = "photo of {{caption}}, sharp"
    graph["7"] = _node("LoadImage", image="{{image_path}}")
    # A title is not an input, whatever it says.
    graph["7"]["_meta"] = {"title": "{{image_path}} loader"}
    original = json.dumps(graph, sort_keys=True)

    migrated, changed = workflow_bindings.migrate_placeholders(graph)

    assert changed
    assert json.dumps(graph, sort_keys=True) == original
    body = {k: v for k, v in migrated.items() if k != workflow_bindings.BINDINGS_KEY}
    assert "{{" not in json.dumps(body).replace("{{image_path}} loader", "")
    assert migrated["2"]["inputs"]["text"] == ""
    assert migrated["7"]["inputs"]["image"] == "example.png"
    assert migrated["3"]["inputs"]["text"] == "photo of , sharp"
    bindings = {
        (b["role"], b["node"], tuple(b["path"]), b["recovered"])
        for b in migrated[workflow_bindings.BINDINGS_KEY]
    }
    assert bindings == {
        ("caption", "2", ("2", "inputs", "text"), True),
        ("caption", "3", ("3", "inputs", "text"), False),
        ("image", "7", ("7", "inputs", "image"), True),
    }
    # The embedded token keeps its string as a template, and a run fills it
    # the way the old substitution did.
    (template,) = [
        b for b in migrated[workflow_bindings.BINDINGS_KEY] if not b["recovered"]
    ]
    assert template["template"] == "photo of {{caption}}, sharp"
    filled = comfyui_module._fill_run_inputs(migrated, "up.png", "a cat")
    assert filled["3"]["inputs"]["text"] == "photo of a cat, sharp"
    assert filled["2"]["inputs"]["text"] == "a cat"
    assert filled["7"]["inputs"]["image"] == "up.png"
    # What the string said before it became a template is gone: flagged.
    assert workflow_bindings.is_flagged(migrated)
    # Nothing left to migrate.
    assert workflow_bindings.migrate_placeholders(migrated) == (migrated, False)


def test_migration_names_the_node_in_ui_and_envelope_formats():
    ui = {
        "nodes": [
            {"id": 12, "type": "LoadImage", "widgets_values": ["{{image_path}}"]}
        ],
        "links": [],
    }
    migrated, _ = workflow_bindings.migrate_placeholders(ui)
    (binding,) = migrated[workflow_bindings.BINDINGS_KEY]
    assert (binding["node"], binding["path"]) == (
        "12",
        ["nodes", 0, "widgets_values", 0],
    )
    assert not workflow_bindings.is_flagged(migrated)

    envelope = {"prompt": {"5": _node("CLIPTextEncode", text="{{caption}}")}}
    migrated, _ = workflow_bindings.migrate_placeholders(envelope)
    (binding,) = migrated[workflow_bindings.BINDINGS_KEY]
    assert binding["node"] == "5"
    assert migrated["prompt"]["5"]["inputs"]["text"] == ""


def test_folder_migration_runs_once_backs_up_and_skips_unreadable_files(
    tmp_path, caplog
):
    graph = _t2i_graph()
    graph["7"] = _node("LoadImage", image="{{image_path}}")
    (tmp_path / "tokened.json").write_text(json.dumps(graph), encoding="utf-8")
    # The old dialog's "None (text-to-image)" with a fixed reference loader:
    # no token, and detection would otherwise start filling that loader.
    legacy = _t2i_graph()
    legacy["7"] = _node("LoadImage", image="pose.png")
    (tmp_path / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    (tmp_path / "deep.json").write_text("[" * 100000 + "]" * 100000, encoding="utf-8")

    assert workflow_bindings.migrate_workflow_folder(str(tmp_path)) == 2
    assert "broken.json" in caplog.text and "deep.json" in caplog.text
    stored = _load(tmp_path / "tokened.json")
    assert stored[workflow_bindings.BINDINGS_KEY][0]["path"] == ["7", "inputs", "image"]
    assert _load(tmp_path / "tokened.json.pre-bindings") == graph
    assert _load(tmp_path / "legacy.json")[workflow_bindings.BINDINGS_KEY] == []
    assert comfyui_module._missing_placeholders(_load(tmp_path / "legacy.json")) == [
        "{{image_path}}",
        "{{caption}}",
    ]

    # A workflow imported as-is after the pass keeps detection.
    (tmp_path / "later.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert workflow_bindings.migrate_workflow_folder(str(tmp_path)) == 0
    assert workflow_bindings.BINDINGS_KEY not in _load(tmp_path / "later.json")


def test_an_unlistable_folder_does_not_stop_start_up(tmp_path, monkeypatch, caplog):
    def refuse(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(workflow_bindings.os, "listdir", refuse)
    assert workflow_bindings.migrate_workflow_folder(str(tmp_path)) == 0
    assert "denied" in caplog.text
    assert not (tmp_path / workflow_bindings.MIGRATION_MARKER).exists()


@pytest.mark.parametrize(
    "name, image, caption",
    [
        # Exactly where the shipped files carried their tokens.
        (
            "Flux2-Klein-Image-Edit.json",
            [["76", "inputs", "image"]],
            [["75:74", "inputs", "text"]],
        ),
        # The encoder's text is wired from a primitive; the run fills that.
        ("Flux2-Klein-t2i.json", [], [["76", "inputs", "value"]]),
        ("Upscale-2x-RealESRGAN.json", [["3", "inputs", "image"]], []),
    ],
)
def test_built_ins_carry_no_token_and_are_filled_by_detection(name, image, caption):
    document = _load(BUILT_IN / name)
    assert "{{" not in json.dumps(document)
    assert workflow_bindings.BINDINGS_KEY not in document
    targets = workflow_bindings.run_targets(document)
    paths = {role: [t["path"] for t in found] for role, found in targets.items()}
    assert paths == {"image": image, "caption": caption}


def test_two_picture_inputs_fill_nothing_and_ui_files_are_not_filled():
    graph = _t2i_graph()
    graph["7"] = _node("LoadImage", image="a.png")
    graph["8"] = _node("LoadImage", image="b.png")
    assert workflow_bindings.run_targets(graph)["image"] == []
    ui = _load(UI_FIXTURES / "image_z_image.json")
    assert workflow_bindings.run_targets(ui) == {"image": [], "caption": []}


def test_run_fill_keeps_the_prompt_when_no_caption_is_given():
    graph = _t2i_graph()
    graph["7"] = _node("LoadImage", image="a.png")
    filled = comfyui_module._fill_run_inputs(graph, "upload.png", "")
    assert filled["7"]["inputs"]["image"] == "upload.png"
    assert filled["2"]["inputs"]["text"] == "a cat"
    assert graph["7"]["inputs"]["image"] == "a.png"
    filled = comfyui_module._fill_run_inputs(graph, None, "a dog")
    assert filled["2"]["inputs"]["text"] == "a dog"
    assert filled["3"]["inputs"]["text"] == "blurry"


def test_a_binding_that_no_longer_resolves_is_refused():
    graph = _t2i_graph()
    graph[workflow_bindings.BINDINGS_KEY] = [
        {"role": "image", "node": "9", "path": ["9", "inputs", "image"]}
    ]
    with pytest.raises(HTTPException) as refused:
        comfyui_module._fill_run_inputs(graph, "upload.png", "")
    assert refused.value.status_code == 400
    # A path that is not a list must not index the document as a key.
    graph[workflow_bindings.BINDINGS_KEY] = [{"role": "image", "path": "1"}]
    with pytest.raises(HTTPException):
        comfyui_module._fill_run_inputs(graph, "upload.png", "")
    assert graph["1"]["class_type"] == "CheckpointLoaderSimple"


@pytest.fixture
def import_route(tmp_path, monkeypatch):
    user_dir = tmp_path / "user"
    built_in = tmp_path / "built-in"
    built_in.mkdir()
    monkeypatch.setattr(comfyui_module, "_workflow_user_dir", lambda: str(user_dir))
    monkeypatch.setattr(
        comfyui_module,
        "_workflow_dirs",
        lambda: [("user", str(user_dir)), ("built-in", str(built_in))],
    )
    hub = HubDatabase(str(tmp_path / "hub.db"))
    server = MagicMock()
    server.hub = hub
    endpoint = _route(comfyui_module.create_router(server), "/comfyui/workflows/import")

    def call(**payload):
        return endpoint(payload)

    try:
        yield call, user_dir, built_in, hub
    finally:
        hub.close()


def test_import_stores_the_file_unchanged_and_files_it_in_the_library(import_route):
    call, user_dir, _built_in, hub = import_route
    graph = _t2i_graph()
    graph["7"] = _node("LoadImage", image="a.png")

    body = call(name="flow", workflow=graph)

    assert (body["name"], body["matched"]) == ("flow.json", False)
    assert _load(user_dir / "flow.json") == graph
    assert body["topology_hash"] == topology_hash(graph)
    assert topology_exists(hub, body["topology_hash"])

    ui = _load(UI_FIXTURES / "image_z_image.json")
    body = call(name="ui", workflow=ui)
    assert _load(user_dir / "ui.json") == ui
    assert topology_exists(hub, body["topology_hash"])


def test_a_dropped_copy_matches_the_stored_workflow(import_route):
    call, user_dir, built_in, _hub = import_route
    graph = _t2i_graph()
    call(name="flow", workflow=graph)
    copy = json.loads(json.dumps(graph, indent=4))

    body = call(name="renamed copy", workflow=copy, keep_both=True)
    assert (body["name"], body["matched"]) == ("flow.json", True)
    assert sorted(p.name for p in user_dir.iterdir()) == ["flow.json"]

    # PixlStash's own keys are not part of the workflow ComfyUI sees.
    stored = _t2i_graph()
    stored["pixlstash_output_nodes"] = ["6"]
    stored["2"]["inputs"]["text"] = "a fox"
    (user_dir / "chosen.json").write_text(json.dumps(stored), encoding="utf-8")
    plain = _t2i_graph()
    plain["2"]["inputs"]["text"] = "a fox"
    assert call(name="plain", workflow=plain)["name"] == "chosen.json"

    shipped = {"1": _node("SaveImage")}
    (built_in / "Shipped.json").write_text(json.dumps(shipped), encoding="utf-8")
    body = call(name="shipped", workflow=shipped)
    assert (body["name"], body["matched"]) == ("Shipped.json", True)


def test_a_taken_name_is_refused_or_kept_beside(import_route):
    call, user_dir, _built_in, _hub = import_route
    call(name="flow", workflow=_t2i_graph())
    other = _t2i_graph()
    other["2"]["inputs"]["text"] = "a dog"

    with pytest.raises(HTTPException) as refused:
        call(name="flow", workflow=other)
    assert refused.value.status_code == 409

    assert call(name="flow", workflow=other, keep_both=True)["name"] == "flow (2).json"
    assert _load(user_dir / "flow (2).json") == other
    assert _load(user_dir / "flow.json") == _t2i_graph()


def test_an_imported_tokened_file_is_stored_migrated(import_route):
    call, user_dir, _built_in, _hub = import_route
    graph = _t2i_graph()
    graph["2"]["inputs"]["text"] = "{{caption}}"
    call(name="old", workflow=graph)
    stored = _load(user_dir / "old.json")
    assert stored[workflow_bindings.BINDINGS_KEY][0]["path"] == ["2", "inputs", "text"]
    # Re-dropping the same old export is a copy of what was stored.
    assert call(name="again", workflow=graph)["matched"] is True
