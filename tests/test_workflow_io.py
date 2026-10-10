"""Detection of a workflow's inputs and outputs (#1302), import as-is (#1303),
and the watched workflows folder (#1304)."""

import copy
import json
import sqlite3
import os
import pathlib
import shutil
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import pixlstash.routes.comfyui as comfyui_module
import pixlstash.routes.workflows as workflows_module
import pixlstash.server as server_module
from pixlstash.hub.db import HubDatabase
from pixlstash.hub import workflow_origin
from pixlstash.hub.workflow_card_reads import Workflow, manual_document
from pixlstash.hub.workflow_group_writes import create_manual_workflow
from pixlstash.services import workflow_bindings, workflow_inbox
from pixlstash.services.workflow_inputs import (
    FIXED,
    PICKER,
    SELECTION,
    CardInput,
    node_title,
    resolve_fills,
    resolve_input_modes,
)
from pixlstash.services.workflow_hash import (
    WorkflowGraphError,
    reduce_ui_graph,
)
from pixlstash.services.workflow_bindings import run_targets
from pixlstash.services.workflow_io import (
    api_graph,
    detect_workflow_io,
    with_converted_graph,
)
from pixlstash.utils.comfyui_utilities import (
    NotAWorkflowError,
    check_comfy_workflow,
    extract_generation_info,
    is_api_format,
    is_comfy_workflow,
    loaded_model_widgets,
)
from pixlstash.utils.workflow_ids import WORKFLOW_TAG_KEY, stamp_workflow_id

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
    assert found.save_nodes == (("80",) if name.startswith("video_") else ("9",))
    assert found.positive_prompts == positive
    assert found.negative_prompts == negative
    assert found.ambiguities == ()


def test_a_video_save_is_a_save_node():
    found = detect_workflow_io(_load(UI_FIXTURES / "video_wan2_2_14B_t2v.json"))
    assert found.save_nodes == ("80",)
    assert found.valid


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
    graph["7"] = _node("ConditioningFromNowhere", strength=1.0)
    graph["4"]["inputs"]["positive"] = ["7", 0]
    found = detect_workflow_io(graph)
    assert (found.positive_prompts, found.negative_prompts) == ((), ("3",))
    assert found.ambiguities == ("positive prompt not found",)


def _i2v_graph() -> dict:
    """MiniMax H3's shape: one node takes clip and text and makes the conditioning."""
    return {
        "1": _node("CLIPLoader", clip_name="clip.safetensors"),
        "2": _node("LoadImage", image="a.png"),
        "3": _node(
            "MiniMaxH3ImageToVideo", prompt="a cat", clip=["1", 0], first_frame=["2", 0]
        ),
        "4": _node("BasicGuider", conditioning=["3", 0]),
        "5": _node("SamplerCustomAdvanced", guider=["4", 0], latent_image=["3", 1]),
        "6": _node("SaveImage", images=["5", 0]),
    }


def test_a_node_taking_clip_and_no_conditioning_is_the_prompt_node():
    """Whatever its class is called (#1832)."""
    found = detect_workflow_io(_i2v_graph())
    assert (found.positive_prompts, found.ambiguities) == (("3",), ())
    assert run_targets(_i2v_graph())["caption"] == [
        {"path": ["3", "inputs", "prompt"], "template": None}
    ]


def test_a_node_taking_clip_and_conditioning_is_walked_through():
    """`GLIGENTextBoxApply` adds a box to a prompt; it is not the prompt."""
    graph = _t2i_graph()
    graph["7"] = _node(
        "GLIGENTextBoxApply", text="a hat", clip=["1", 1], conditioning_to=["2", 0]
    )
    graph["4"]["inputs"]["positive"] = ["7", 0]
    assert detect_workflow_io(graph).positive_prompts == ("2",)


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


def _request(client_id=None):
    """The one thing a handler called directly reads off the request.

    ``OriginClientMiddleware`` puts the originating tab's ``X-Client-Id`` on
    ``request.state``, and the import route threads it into the
    ``CHANGED_WORKFLOWS`` event so that tab can recognise its own change
    (v1.12 B4). Calling the endpoint as a function skips the middleware, so
    the state it would have set is supplied here.
    """
    return SimpleNamespace(state=SimpleNamespace(origin_client_id=client_id))


def _fill_run_inputs(workflow, image, caption):
    """What the retired run routes did with a graph's bindings (#1410).

    ``routes/comfyui._fill_run_inputs`` was deleted with ``run_i2i``/``run_t2i``;
    the behaviour under test here was always ``workflow_bindings``', so this is
    the two calls it wrapped and nothing else. A binding that does not resolve
    raises ``BindingError`` rather than the route's 400.
    """
    instance = copy.deepcopy(workflow)
    workflow_bindings.fill(
        instance,
        workflow_bindings.run_targets(workflow),
        {
            workflow_bindings.IMAGE: image,
            workflow_bindings.CAPTION: caption or None,
        },
    )
    return instance


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
    assert migrated["7"]["_meta"] == {"title": "{{image_path}} loader"}
    del migrated["7"]["_meta"]
    body = {k: v for k, v in migrated.items() if k != workflow_bindings.BINDINGS_KEY}
    assert "{{" not in json.dumps(body)
    migrated["7"]["_meta"] = {"title": "{{image_path}} loader"}
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
    filled = _fill_run_inputs(migrated, "up.png", "a cat")
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
    # A broken file cannot run either way, so it is not queued for a retry.
    assert _load(tmp_path / workflow_bindings.MIGRATION_MARKER) == {"retry": []}
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".migrating")]

    # A workflow imported as-is after the pass keeps detection.
    (tmp_path / "later.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert workflow_bindings.migrate_workflow_folder(str(tmp_path)) == 0
    assert workflow_bindings.BINDINGS_KEY not in _load(tmp_path / "later.json")


def test_a_fresh_install_takes_the_marker_so_an_as_is_import_keeps_detection(
    tmp_path, monkeypatch
):
    # Start-up on a machine with no workflow folder yet, then an as-is import,
    # then a restart: the import must not be given empty bindings.
    user_dir = tmp_path / "user"
    assert workflow_bindings.migrate_workflow_folder(str(user_dir)) == 0
    assert (user_dir / workflow_bindings.MIGRATION_MARKER).exists()

    monkeypatch.setattr(comfyui_module, "workflow_user_dir", lambda: str(user_dir))
    monkeypatch.setattr(
        comfyui_module, "_workflow_dirs", lambda: [("user", str(user_dir))]
    )
    hub = HubDatabase(str(tmp_path / "hub.db"))
    try:
        endpoint = _route(
            comfyui_module.create_router(MagicMock(hub=hub)),
            "/comfyui/workflows/import",
        )
        graph = _t2i_graph()
        graph["7"] = _node("LoadImage", image="a.png")
        body = endpoint(_request(), {"name": "mine", "workflow": graph})

        assert workflow_bindings.migrate_workflow_folder(str(user_dir)) == 0
        stored = manual_document(hub, body["workflow_id"])
        assert stored == graph
        assert comfyui_module._missing_placeholders(stored) == []
    finally:
        hub.close()


def test_a_file_that_could_not_be_written_is_retried_and_nothing_else(
    tmp_path, monkeypatch
):
    graph = _t2i_graph()
    graph["2"]["inputs"]["text"] = "{{caption}}"
    (tmp_path / "locked.json").write_text(json.dumps(graph), encoding="utf-8")
    (tmp_path / "fine.json").write_text(json.dumps(graph), encoding="utf-8")
    real_replace = workflow_bindings.os.replace

    def locked_replace(src, dst):
        if dst.endswith("locked.json"):
            raise PermissionError("held by another program")
        return real_replace(src, dst)

    monkeypatch.setattr(workflow_bindings.os, "replace", locked_replace)
    assert workflow_bindings.migrate_workflow_folder(str(tmp_path)) == 1
    assert _load(tmp_path / workflow_bindings.MIGRATION_MARKER) == {
        "retry": ["locked.json"]
    }
    assert "{{caption}}" in (tmp_path / "locked.json").read_text(encoding="utf-8")
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".migrating")]

    # Next start: only the stranded file is tried, and an as-is import made in
    # between is left alone.
    monkeypatch.setattr(workflow_bindings.os, "replace", real_replace)
    (tmp_path / "new.json").write_text(json.dumps(_t2i_graph()), encoding="utf-8")
    assert workflow_bindings.migrate_workflow_folder(str(tmp_path)) == 1
    assert workflow_bindings.BINDINGS_KEY in _load(tmp_path / "locked.json")
    assert workflow_bindings.BINDINGS_KEY not in _load(tmp_path / "new.json")
    assert _load(tmp_path / workflow_bindings.MIGRATION_MARKER) == {"retry": []}


def test_server_start_up_runs_the_migration_on_the_user_folder(tmp_path, monkeypatch):
    # The suite turns the migration off (conftest) because the real folder is
    # machine-global. Here it is a tmp folder, so the wiring itself is checked.
    user_dir = tmp_path / "workflows"
    user_dir.mkdir()
    graph = _t2i_graph()
    graph["2"]["inputs"]["text"] = "{{caption}}"
    (user_dir / "old.json").write_text(json.dumps(graph), encoding="utf-8")
    monkeypatch.setattr(server_module, "workflow_user_dir", lambda: str(user_dir))
    monkeypatch.setattr(server_module.Server, "DEFAULT_MIGRATE_WORKFLOW_TOKENS", True)
    # The inbox is reconciled after the migration and watched from start-up.
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "dropped.json").write_text(json.dumps(_t2i_graph()), encoding="utf-8")
    monkeypatch.setattr(server_module, "workflow_inbox_dir", lambda: str(inbox))
    monkeypatch.setattr(comfyui_module, "workflow_user_dir", lambda: str(user_dir))
    monkeypatch.setattr(
        comfyui_module, "_workflow_dirs", lambda: [("user", str(user_dir))]
    )
    monkeypatch.setattr(server_module.Server, "DEFAULT_WATCH_WORKFLOW_INBOX", True)
    config_path = tmp_path / "config" / "server-config.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps({"port": 8000}), encoding="utf-8")

    server = server_module.Server(str(config_path))
    try:
        assert workflow_bindings.BINDINGS_KEY in _load(user_dir / "old.json")
        assert (user_dir / workflow_bindings.MIGRATION_MARKER).exists()
        (stored,) = server.hub.fetchall("SELECT document FROM workflow_document")
        assert json.loads(stored["document"]) == _t2i_graph()
        assert server._workflow_inbox_watcher is not None
    finally:
        server.__exit__(None, None, None)


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs a user that a mode-000 folder refuses",
)
def test_an_unlistable_folder_does_not_stop_start_up(tmp_path, caplog):
    folder = tmp_path / "user"
    folder.mkdir()
    folder.chmod(0o300)  # writable, so the marker can be taken; not listable
    try:
        assert workflow_bindings.migrate_workflow_folder(str(folder)) == 0
    finally:
        folder.chmod(0o700)
    assert "could not list" in caplog.text.lower()
    # Released, so the next start tries the pass again.
    assert not (folder / workflow_bindings.MIGRATION_MARKER).exists()


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
    filled = _fill_run_inputs(graph, "upload.png", "")
    assert filled["7"]["inputs"]["image"] == "upload.png"
    assert filled["2"]["inputs"]["text"] == "a cat"
    assert graph["7"]["inputs"]["image"] == "a.png"
    filled = _fill_run_inputs(graph, None, "a dog")
    assert filled["2"]["inputs"]["text"] == "a dog"
    assert filled["3"]["inputs"]["text"] == "blurry"


def test_a_binding_that_no_longer_resolves_is_refused():
    graph = _t2i_graph()
    graph[workflow_bindings.BINDINGS_KEY] = [
        {"role": "image", "node": "9", "path": ["9", "inputs", "image"]}
    ]
    with pytest.raises(workflow_bindings.BindingError):
        _fill_run_inputs(graph, "upload.png", "")
    # A path that is not a list must not index the document as a key.
    targets = {"image": [{"path": "1", "template": None}], "caption": []}
    with pytest.raises(workflow_bindings.BindingError):
        workflow_bindings.fill(graph, targets, {"image": "upload.png"})
    assert graph["1"]["class_type"] == "CheckpointLoaderSimple"


def test_two_positive_prompts_fill_neither():
    # A fixed style prompt combined with the subject prompt: which one is "the"
    # prompt is ambiguous, and overwriting both destroys the style.
    graph = _t2i_graph()
    graph["8"] = _node("CLIPTextEncode", text="oil painting", clip=["1", 1])
    graph["9"] = _node(
        "ConditioningCombine", conditioning_1=["2", 0], conditioning_2=["8", 0]
    )
    graph["4"]["inputs"]["positive"] = ["9", 0]
    assert detect_workflow_io(graph).positive_prompts == ("2", "8")
    assert workflow_bindings.run_targets(graph)["caption"] == []
    filled = _fill_run_inputs(graph, None, "a dog")
    assert filled["8"]["inputs"]["text"] == "oil painting"


def test_a_string_holding_both_tokens_gets_both_values():
    graph = _t2i_graph()
    graph["2"]["inputs"]["text"] = "file {{image_path}} of {{caption}}"
    migrated, _ = workflow_bindings.migrate_placeholders(graph)
    filled = _fill_run_inputs(migrated, "up.png", "a cat")
    assert filled["2"]["inputs"]["text"] == "file up.png of a cat"
    filled = _fill_run_inputs(migrated, "up.png", "")
    assert filled["2"]["inputs"]["text"] == "file up.png of "


@pytest.fixture
def import_route(tmp_path, monkeypatch):
    user_dir = tmp_path / "user"
    built_in = tmp_path / "built-in"
    built_in.mkdir()
    monkeypatch.setattr(comfyui_module, "workflow_user_dir", lambda: str(user_dir))
    monkeypatch.setattr(
        comfyui_module,
        "_workflow_dirs",
        lambda: [("user", str(user_dir)), ("built-in", str(built_in))],
    )
    # Never the machine's own inbox or trash.
    inbox = tmp_path / "workflows"
    trash = tmp_path / "trash"
    trash.mkdir()

    def fake_trash(path):
        shutil.move(path, trash / os.path.basename(path))

    monkeypatch.setattr(workflow_inbox, "workflow_inbox_dir", lambda: str(inbox))
    monkeypatch.setattr(workflow_inbox, "send2trash", fake_trash)
    monkeypatch.setattr(comfyui_module, "send2trash", fake_trash)
    hub = HubDatabase(str(tmp_path / "hub.db"))
    server = MagicMock()
    server.hub = hub
    server.vault.library_uuid = None
    endpoint = _route(comfyui_module.create_router(server), "/comfyui/workflows/import")

    def call(**payload):
        return endpoint(_request(), payload)

    call.inbox = inbox
    call.trash = trash
    call.store = lambda name, workflow: comfyui_module.store_inbox_workflow(
        hub, name, workflow
    )
    call.delete = _route(
        comfyui_module.create_router(server),
        "/comfyui/workflows/{workflow_name}",
        "DELETE",
    )
    delete_workflow = _route(
        workflows_module.create_router(server), "/workflows/{workflow_id}", "DELETE"
    )
    call.delete_workflow = lambda workflow_id: delete_workflow(_request(), workflow_id)
    card = _route(
        comfyui_module.create_router(server), "/comfyui/workflows/{workflow_name}/card"
    )
    call.card = lambda name: card(_request(), name)
    try:
        yield call, user_dir, built_in, hub
    finally:
        hub.close()


def _manual(hub) -> dict:
    """``{workflow id: (name, origin, document)}`` of every manual workflow."""
    return {
        row["workflow_id"]: (row["name"], row["origin"], json.loads(row["document"]))
        for row in hub.fetchall(
            "SELECT d.workflow_id, d.origin, d.document, a.name "
            "FROM workflow_document d LEFT JOIN workflow_group_attr a "
            "ON a.workflow_id = d.workflow_id"
        )
    }


def test_an_import_is_a_manual_workflow_holding_the_document_unchanged(import_route):
    """No file, no filing: the document is the workflow, and joins no card.

    Unchanged but for the tag an editor document carries its own id in.
    """
    call, user_dir, _built_in, hub = import_route
    graph = _t2i_graph()
    graph["7"] = _node("LoadImage", image="a.png")

    body = call(name="flow", workflow=graph)

    assert (body["name"], body["matched"]) == ("flow", False)
    assert body["workflow_id"].startswith("manual:")
    assert manual_document(hub, body["workflow_id"]) == graph
    assert _manual(hub)[body["workflow_id"]] == ("flow", "import", graph)
    ui = _load(UI_FIXTURES / "image_z_image.json")
    # An editor document gains one key: its id, replacing a tag it came with.
    ui["extra"][WORKFLOW_TAG_KEY] = "manual:" + "f" * 32
    ui_id = call(name="ui", workflow=ui)["workflow_id"]
    assert manual_document(hub, ui_id) == stamp_workflow_id(ui, ui_id)
    assert manual_document(hub, ui_id)["extra"][WORKFLOW_TAG_KEY] == ui_id
    assert not user_dir.exists()
    for table in ("workflow_file", "workflow_variant", "workflow_recipe"):
        assert hub.fetchone(f"SELECT COUNT(*) FROM {table}")[0] == 0, table


def test_an_identical_import_under_a_taken_name_is_a_second_workflow(import_route):
    """Identical copies are allowed, and a name is no key."""
    call, _user_dir, _built_in, hub = import_route
    first = call(name="flow", workflow=_t2i_graph())["workflow_id"]
    second = call(name="flow", workflow=_t2i_graph())["workflow_id"]
    assert first != second
    assert [name for name, _o, _d in _manual(hub).values()] == ["flow", "flow"]


def test_an_unfileable_or_too_deep_import_is_handled(import_route):
    call, _user_dir, _built_in, hub = import_route
    # Reducing this raises AttributeError, not WorkflowGraphError: the
    # workflow is still stored, it just has no topology.
    odd = {
        "nodes": [{"id": 1, "type": "SaveImage"}],
        "links": [],
        "definitions": ["x"],
    }
    body = call(name="odd", workflow=odd)
    assert manual_document(hub, body["workflow_id"]) == stamp_workflow_id(
        odd, body["workflow_id"]
    )

    deep = current = {}
    for _ in range(5000):
        current["n"] = {}
        current = current["n"]
    # Inside a node, so it is a workflow until it is compared.
    deep = {"1": {"class_type": "SaveImage", "inputs": deep}}
    with pytest.raises(HTTPException) as refused:
        call(name="deep", workflow=deep)
    assert refused.value.status_code == 400
    assert refused.value.detail == "Workflow JSON nests too deeply"
    assert [name for name, _o, _d in _manual(hub).values()] == ["odd"]


@pytest.mark.parametrize(
    "document",
    [
        {"name": "package", "version": "1.0.0", "dependencies": {}},
        {},
        {"nodes": [], "links": []},
        {"nodes": [{"id": 1}], "links": []},
        {"nodes": [{"type": "SaveImage"}], "links": []},
        {"nodes": [{"id": 1, "type": "SaveImage"}], "links": {}},
        {"nodes": [{"id": 1, "type": "SaveImage"}], "links": None, "state": {}},
        {"nodes": ["SaveImage"], "links": []},
        # React Flow and n8n exports: nodes with an id and type, but no links.
        {"nodes": [{"id": "1", "type": "input"}], "edges": []},
        {"nodes": [{"id": "a", "type": "n8n-nodes-base.start"}], "connections": {}},
        {"1": {"class_type": "SaveImage", "inputs": {}}, "version": 2},
        {"1": {"class_type": "SaveImage"}},
        {"prompt": {"1": {"inputs": {}}}},
    ],
)
def test_a_document_that_is_not_a_workflow_is_refused(import_route, document):
    call, _user_dir, _built_in, hub = import_route
    with pytest.raises(HTTPException) as refused:
        call(name="unrelated", workflow=document)
    assert refused.value.status_code == 400
    assert "not a ComfyUI workflow" in refused.value.detail
    assert _manual(hub) == {}


def test_both_formats_are_accepted_with_pixlstash_keys_or_a_prompt_wrapper():
    graph = _t2i_graph()
    check_comfy_workflow(graph)
    check_comfy_workflow({**graph, "pixlstash_output_nodes": ["6"]})
    check_comfy_workflow({"prompt": graph, "extra_data": {}})
    # Schema version 1 leaves out an empty links list.
    node = {"id": 1, "type": "SaveImage"}
    check_comfy_workflow({"version": 1, "state": {"lastNodeId": 1}, "nodes": [node]})
    check_comfy_workflow({"last_node_id": 1, "nodes": [node]})


@pytest.mark.parametrize(
    "path",
    sorted(BUILT_IN.glob("*.json")) + sorted(UI_FIXTURES.glob("*.json")),
    ids=lambda path: path.name,
)
def test_every_shipped_and_fixture_workflow_is_accepted(path):
    check_comfy_workflow(_load(path))


def test_a_refusal_names_the_node_id_and_cuts_a_long_key_short():
    nodes = [{"id": 1, "type": "SaveImage"}, {"id": 7}]
    with pytest.raises(NotAWorkflowError, match="node '7' has no type"):
        check_comfy_workflow({"nodes": nodes, "links": []})
    graph = {**_t2i_graph(), "x" * 5000: 1}
    with pytest.raises(NotAWorkflowError) as refused:
        check_comfy_workflow(graph)
    assert len(str(refused.value)) < 200


def test_deleting_a_workflow_removes_its_migration_backup(import_route):
    call, user_dir, _built_in, _hub = import_route
    user_dir.mkdir()
    (user_dir / "old.json").write_text("{}", encoding="utf-8")
    (user_dir / "old.json.pre-bindings").write_text("{}", encoding="utf-8")
    call.delete("old")
    assert sorted(p.name for p in user_dir.iterdir()) == []


def test_an_imported_tokened_file_is_stored_migrated(import_route):
    call, _user_dir, _built_in, hub = import_route
    graph = _t2i_graph()
    graph["2"]["inputs"]["text"] = "{{caption}}"
    stored = manual_document(hub, call(name="old", workflow=graph)["workflow_id"])
    assert stored[workflow_bindings.BINDINGS_KEY][0]["path"] == ["2", "inputs", "text"]


def _inbox_names(call) -> list[str]:
    return sorted(p.name for p in call.inbox.iterdir())


def test_a_file_in_the_inbox_is_imported_and_named_by_its_content(import_route):
    call, user_dir, _built_in, hub = import_route
    call.inbox.mkdir()
    graph = _t2i_graph()
    (call.inbox / "flow.json").write_text(json.dumps(graph), encoding="utf-8")
    digest = workflow_inbox.content_hash(graph)

    assert workflow_inbox.reconcile(str(call.inbox), call.store) == 1
    assert list(_manual(hub).values()) == [("flow", "inbox", graph)]
    assert not user_dir.exists()
    assert _inbox_names(call) == [f"flow.{digest}.json"]

    # Idempotent - a restart reads every file again and imports nothing - and
    # a file already named by its hash is not renamed again.
    assert workflow_inbox.reconcile(str(call.inbox), call.store) == 0
    assert _inbox_names(call) == [f"flow.{digest}.json"]
    assert len(_manual(hub)) == 1

    # A different workflow under a taken name is a workflow of its own.
    other = _t2i_graph()
    other["2"]["inputs"]["text"] = "a dog"
    (call.inbox / "flow.json").write_text(json.dumps(other), encoding="utf-8")
    assert workflow_inbox.reconcile(str(call.inbox), call.store) == 1

    # An inbox: removing its files never deletes a workflow.
    for path in call.inbox.iterdir():
        path.unlink()
    assert workflow_inbox.reconcile(str(call.inbox), call.store) == 0
    assert sorted(d["2"]["inputs"]["text"] for _n, _o, d in _manual(hub).values()) == [
        "a cat",
        "a dog",
    ]


def test_a_broken_inbox_file_is_left_and_the_rest_imported(import_route, caplog):
    call, _user_dir, _built_in, hub = import_route
    call.inbox.mkdir()
    (call.inbox / "broken.json").write_text("{not json", encoding="utf-8")
    (call.inbox / "list.json").write_text("[]", encoding="utf-8")
    (call.inbox / "other.json").write_text('{"name": "x"}', encoding="utf-8")
    (call.inbox / "fine.json").write_text(json.dumps(_t2i_graph()), encoding="utf-8")
    assert workflow_inbox.reconcile(str(call.inbox), call.store) == 1
    for left in ("broken.json", "list.json", "other.json"):
        assert left in caplog.text and left in _inbox_names(call)
    assert [name for name, _o, _d in _manual(hub).values()] == ["fine"]


def test_deleting_a_workflow_trashes_it_by_way_of_the_inbox(import_route):
    call, _user_dir, _built_in, hub = import_route
    graph = _t2i_graph()
    call.inbox.mkdir()
    (call.inbox / "flow.json").write_text(json.dumps(graph), encoding="utf-8")
    workflow_inbox.reconcile(str(call.inbox), call.store)
    (workflow_id,) = _manual(hub)
    digest = workflow_inbox.content_hash(graph)
    # A copy dropped under another name carries the same hash and goes too, or
    # the next start would import the deleted workflow again: the inbox is
    # this workflow's.
    (call.inbox / f"copy.{digest}.json").write_text(json.dumps(graph), encoding="utf-8")
    # And one the watcher has not renamed yet.
    (call.inbox / "fresh.json").write_text(json.dumps(graph), encoding="utf-8")

    deleted = call.delete_workflow(workflow_id)
    assert (deleted.deleted, deleted.workflow_id) == ("flow", workflow_id)
    assert _manual(hub) == {}
    assert (
        hub.fetchone(
            "SELECT 1 FROM workflow_group_attr WHERE workflow_id = ?", (workflow_id,)
        )
        is None
    )
    assert _inbox_names(call) == []
    assert sorted(p.name for p in call.trash.iterdir()) == [
        f"copy.{digest}.json",
        f"flow (2).{digest}.json",
        f"flow.{digest}.json",
        "fresh.json",
    ]
    assert _load(call.trash / f"flow.{digest}.json") == graph

    # Restoring it from the trash puts it back in the inbox, under its name.
    shutil.move(call.trash / f"flow.{digest}.json", call.inbox)
    assert workflow_inbox.reconcile(str(call.inbox), call.store) == 1
    assert list(_manual(hub).values()) == [("flow", "inbox", graph)]


def test_deleting_a_workflow_leaves_another_live_copys_inbox_file(import_route):
    """Identical copies are allowed, so an inbox file of the same content can
    be another live workflow's: a delete sweeps only what it owns."""
    call, _user_dir, _built_in, hub = import_route
    graph = _t2i_graph()
    imported = call(name="flow", workflow=graph)["workflow_id"]
    call.inbox.mkdir()
    (call.inbox / "flow.json").write_text(json.dumps(graph), encoding="utf-8")
    workflow_inbox.reconcile(str(call.inbox), call.store)
    (dropped,) = set(_manual(hub)) - {imported}
    digest = workflow_inbox.content_hash(graph)

    call.delete_workflow(imported)

    assert _inbox_names(call) == [f"flow.{digest}.json"]
    assert [p.name for p in call.trash.iterdir()] == [f"flow (2).{digest}.json"]
    assert workflow_inbox.reconcile(str(call.inbox), call.store) == 0
    assert list(_manual(hub)) == [dropped]


def test_deleting_an_adopted_workflow_trashes_its_file_and_never_a_built_in(
    import_route,
):
    """A workflow data step 7 made of a user file takes the file with it, or
    the file would still list and could be made a workflow again; a built-in
    made a workflow is shipped data and its file is never touched."""
    call, user_dir, built_in, hub = import_route
    user_dir.mkdir()
    graph = _t2i_graph()
    (user_dir / "old.json").write_text(json.dumps(graph), encoding="utf-8")
    adopted = create_manual_workflow(hub, "old", graph, "import")
    with hub.transaction() as conn:
        workflow_origin.upsert(
            conn, workflow_origin.FILE_ORIGIN, "old.json", adopted, None, None
        )
    shipped = _t2i_graph()
    shipped["2"]["inputs"]["text"] = "shipped"
    (built_in / "Shipped.json").write_text(json.dumps(shipped), encoding="utf-8")

    # Built-ins only: a user file is not made a workflow by name.
    with pytest.raises(HTTPException) as refused:
        call.card("old.json")
    assert refused.value.status_code == 404
    builtin_id = call.card("Shipped.json")["workflow_id"]

    call.delete_workflow(adopted)
    assert not (user_dir / "old.json").exists()
    assert [_load(p) for p in call.trash.iterdir()] == [graph]
    call.delete_workflow(builtin_id)
    assert _load(built_in / "Shipped.json") == shipped
    assert _manual(hub) == {}


def test_a_built_in_made_a_workflow_is_matched_by_the_inbox(import_route):
    """Its origin row carries the content hash, so the same content dropped in
    the watched folder is that workflow, not a second one."""
    call, _user_dir, built_in, hub = import_route
    shipped = _t2i_graph()
    shipped["2"]["inputs"]["text"] = "shipped"
    (built_in / "Shipped.json").write_text(json.dumps(shipped), encoding="utf-8")
    builtin_id = call.card("Shipped.json")["workflow_id"]
    call.inbox.mkdir()
    (call.inbox / "copy.json").write_text(json.dumps(shipped), encoding="utf-8")
    workflow_inbox.reconcile(str(call.inbox), call.store)
    assert list(_manual(hub)) == [builtin_id]


def test_a_built_in_is_stored_with_its_origin_or_not_at_all(import_route, monkeypatch):
    """One transaction: a failed origin write leaves no workflow behind for
    the next ask to store a second time."""
    call, _user_dir, built_in, hub = import_route
    (built_in / "Shipped.json").write_text(json.dumps(_t2i_graph()), encoding="utf-8")

    def refuse(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(workflow_origin, "upsert", refuse)
    with pytest.raises(sqlite3.OperationalError):
        call.card("Shipped.json")
    assert _manual(hub) == {}


def test_a_document_past_the_size_cap_is_refused(import_route, monkeypatch):
    call, _user_dir, _built_in, hub = import_route
    monkeypatch.setattr(comfyui_module, "MAX_WORKFLOW_FILE_BYTES", 50)
    with pytest.raises(HTTPException) as refused:
        call(name="big", workflow=_t2i_graph())
    assert refused.value.status_code == 413
    assert _manual(hub) == {}


def test_a_workflow_deleted_twice_at_once_is_a_404(import_route, monkeypatch):
    """The second of two concurrent deletes finds the row gone: a 404, never
    a 500 out of reading a row that is not there."""
    call, _user_dir, _built_in, hub = import_route
    workflow_id = call(name="flow", workflow=_t2i_graph())["workflow_id"]
    call.delete_workflow(workflow_id)
    # As if this request had found it before the other one deleted it.
    monkeypatch.setattr(
        workflows_module, "find_workflow", lambda *a, **k: Workflow(workflow_id)
    )
    with pytest.raises(HTTPException) as gone:
        call.delete_workflow(workflow_id)
    assert gone.value.status_code == 404


def test_deleting_an_inbox_workflow_lets_its_content_back_in(import_route):
    """The inbox origin row goes with the workflow, so a restore re-imports."""
    call, _user_dir, _built_in, hub = import_route
    graph = _t2i_graph()
    call.inbox.mkdir()
    (call.inbox / "flow.json").write_text(json.dumps(graph), encoding="utf-8")
    workflow_inbox.reconcile(str(call.inbox), call.store)
    (workflow_id,) = _manual(hub)
    digest = workflow_inbox.content_hash(graph)
    # Edited in place: the name still carries the old hash.
    edited = _t2i_graph()
    edited["2"]["inputs"]["text"] = "an edited cat"
    (call.inbox / f"flow.{digest}.json").write_text(
        json.dumps(edited), encoding="utf-8"
    )

    call.delete_workflow(workflow_id)

    assert hub.fetchone("SELECT COUNT(*) FROM workflow_origin")[0] == 0
    assert _load(call.inbox / f"flow.{digest}.json") == edited
    assert [p.name for p in call.trash.iterdir()] == [f"flow (2).{digest}.json"]
    assert _load(call.trash / f"flow (2).{digest}.json") == graph
    assert workflow_inbox.reconcile(str(call.inbox), call.store) == 1
    assert [d for _n, _o, d in _manual(hub).values()] == [edited]
    shutil.move(call.trash / f"flow (2).{digest}.json", call.inbox)
    assert workflow_inbox.reconcile(str(call.inbox), call.store) == 1


def test_a_workflow_the_trash_refuses_is_kept(import_route, monkeypatch):
    call, _user_dir, _built_in, hub = import_route
    workflow_id = call(name="flow", workflow=_t2i_graph())["workflow_id"]

    def no_trash(path):
        raise OSError("no trash on this mount")

    monkeypatch.setattr(workflow_inbox, "send2trash", no_trash)
    with pytest.raises(HTTPException) as refused:
        call.delete_workflow(workflow_id)
    assert refused.value.status_code == 500
    assert manual_document(hub, workflow_id) == _t2i_graph()


def test_an_unreadable_stored_workflow_is_trashed_as_it_is(import_route):
    call, user_dir, _built_in, _hub = import_route
    user_dir.mkdir()
    (user_dir / "broken.json").write_text("{not json", encoding="utf-8")
    call.delete("broken")
    assert not (user_dir / "broken.json").exists()
    assert (call.trash / "broken.json").read_text(encoding="utf-8") == "{not json"


def test_the_watcher_imports_a_file_put_in_the_inbox(import_route, monkeypatch):
    call, _user_dir, _built_in, _hub = import_route
    monkeypatch.setattr(workflow_inbox, "_DEBOUNCE_S", 0.05)
    watcher = workflow_inbox.WorkflowInboxWatcher(str(call.inbox), call.store)
    watcher.start()
    try:
        (call.inbox / "dropped.json").write_text(
            json.dumps(_t2i_graph()), encoding="utf-8"
        )
        digest = workflow_inbox.content_hash(_t2i_graph())
        deadline = time.monotonic() + 10
        # The rename is the last thing a reconcile does.
        while not (call.inbox / f"dropped.{digest}.json").exists():
            assert time.monotonic() < deadline, "the watcher never imported the file"
            time.sleep(0.05)
    finally:
        watcher.stop()
    assert list(_manual(_hub).values()) == [("dropped", "inbox", _t2i_graph())]


# ---------------------------------------------------------------------------
# How each picture input is filled (#1305)
# ---------------------------------------------------------------------------


def _two_input_graph(bound: str | None = "81") -> dict:
    """Flux2BasicEdit's shape: two inputs, both titled Load Image.

    *bound* is the input the old import dialog put its token on, stored the way
    the migration leaves it: a binding, and a neutral value in its place.
    """
    graph = _t2i_graph()
    for node_id in ("76", "81"):
        graph[node_id] = _node("LoadImage", image="Logo.png")
        graph[node_id]["_meta"] = {"title": "Load Image"}
    if bound:
        graph = workflow_bindings.migrate_placeholders(
            {**graph, bound: _node("LoadImage", image="{{image_path}}")}
        )[0]
        graph[bound]["_meta"] = {"title": "Load Image"}
    return graph


def _inputs(graph: dict) -> dict[str, str]:
    found = detect_workflow_io(graph)
    return dict(zip(found.picture_inputs, found.picture_input_classes))


def _modes(resolved) -> dict[str, tuple]:
    return {item.node_id: (item.mode, item.pixel_sha) for item in resolved}


def test_the_bound_input_defaults_to_selection_and_the_rest_to_picker():
    graph = _two_input_graph(bound="81")
    resolved = resolve_input_modes(graph, _inputs(graph), [])
    assert _modes(resolved) == {"76": (PICKER, None), "81": (SELECTION, None)}
    assert [item.title for item in resolved] == ["Load Image", "Load Image"]


def test_with_nothing_filled_by_a_run_the_first_input_is_selection():
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


def test_modes_are_keyed_on_the_files_own_spelling(tmp_path):
    (tmp_path / "Edit.json").write_text("{}", encoding="utf-8")
    assert comfyui_module._on_disk_name(str(tmp_path / "edit.json")) == "Edit.json"
    assert comfyui_module._on_disk_name(str(tmp_path / "Edit.json")) == "Edit.json"


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
    # A loader detection does not recognise, bound by the old dialog.
    unrecognised = workflow_bindings.migrate_placeholders(
        {**_t2i_graph(), "7": _node("MyPictureSource", image="{{image_path}}")}
    )[0]
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
        # Two inputs and no binding: the run route fills each by its mode.
        "unbound.json": True,
        "t2i.json": False,
        # A loader detection misses keeps its binding.
        "custom.json": True,
    }


# A document check_comfy_workflow lets through carrying an editor hint: it
# validates what is under `prompt`, so the envelope may hold anything. The
# hint is on the envelope, which is not the graph (#1482).
def _hinted_envelope() -> dict:
    return {"prompt": _t2i_graph(), "last_node_id": 9, "last_link_id": 12}


# The shape that cannot be imported at all - `check_comfy_workflow` refuses a
# top-level `last_node_id`, so this one only ever arrives as a picture's
# embedded metadata, where nothing validates it. An editor export truncated to
# its header, with a body that would read as a flat node map.
_HINTS_WITH_A_FLAT_BODY = {
    "last_node_id": 9,
    "last_link_id": 12,
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m.ckpt"}},
    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
}


def test_an_editor_hint_on_the_envelope_does_not_make_the_graph_an_editor_graph(
    import_route,
):
    """A guard on this change, not a fix of an older one.

    **`develop` gets this document right already**, by unwrapping `prompt`
    before it looks at `nodes`; what it could not survive is the naive
    conversion, where `is_api_format` is asked about the envelope, says
    "editor format" on the hint and sends a file with no node list to the UI
    reducer, which raises - stored, and filed under no topology at all. So
    this passes before the change and after it, and fails only for the
    version of it that sniffs the wrong object. Nothing this repository writes
    puts a hint on an envelope; `check_comfy_workflow` validates what is
    *inside* one, which is why such a file imports at all.
    """
    call, _user_dir, _built_in, _hub = import_route
    envelope = _hinted_envelope()

    check_comfy_workflow(envelope)  # it really does get this far
    assert is_api_format(envelope) is False  # the hint calls it the editor graph
    assert api_graph(envelope) == envelope["prompt"]  # the graph inside is not
    with pytest.raises(WorkflowGraphError):
        reduce_ui_graph(envelope)  # what filing it as an editor graph would do

    body = call(name="envelope", workflow=envelope)
    assert api_graph(manual_document(_hub, body["workflow_id"])) == envelope["prompt"]
    assert run_targets(envelope)["caption"] == [
        {"path": ["prompt", "2", "inputs", "text"], "template": None}
    ]


def test_a_file_that_carries_both_graphs_is_read_as_the_one_it_can_run(import_route):
    """The reachable document the readers used to disagree about (#1482).

    `check_comfy_workflow` validates the node list and never looks at
    `prompt`, so an editor file carrying a prompt chunk imports. On `develop`
    the node list won every question: it was filed under the editor graph's
    topology, `run_targets` filled nothing because "a UI-format file fills
    nothing", and a title came off the editor's own node ids - while the
    executable graph sat in the file unread. One sniff makes the wrapped
    prompt the answer to all three, which is the graph this file can actually
    run. **Two of the moved sites are pinned here and nowhere else**: revert
    `run_targets` or `_raw_node` and one of these fails.
    """
    call, _user_dir, _built_in, hub = import_route
    prompt = _t2i_graph()
    prompt["7"] = _node("LoadImage", image="a.png")
    prompt["7"]["_meta"] = {"title": "Run title"}
    document = {
        # One editor node, deliberately sharing id 7 with a different node of
        # the prompt chunk, so a title read off the wrong graph is visible.
        "nodes": [
            {
                "id": 7,
                "type": "LoadImage",
                "title": "Editor title",
                "widgets_values": ["a.png"],
            }
        ],
        "links": [],
        "prompt": prompt,
    }
    check_comfy_workflow(document)  # the node list is all it checks
    assert api_graph(document) is prompt

    body = call(name="both", workflow=document)
    assert api_graph(manual_document(hub, body["workflow_id"])) is not None
    assert run_targets(document)["image"] == [
        {"path": ["prompt", "7", "inputs", "image"], "template": None}
    ]
    assert node_title(document, "7", "LoadImage") == "Run title"


def test_the_hints_decide_the_format_for_every_reader_of_a_picture():
    """One sniff for the readers a picture's own metadata reaches.

    `is_comfy_workflow` accepts this document on its hints, so it is offered to
    the extractors with nothing having validated it. They used to look at
    `nodes` alone, call it the API format and walk it; `is_api_format` calls it
    the editor format. **Reading it as one is a deliberate narrowing**: the
    models it would have yielded are the ones a document nothing wrote would
    have yielded, and `tests/comfyui_workflows/expected_results.csv` holds the
    real files to the letter either way.
    """
    assert is_comfy_workflow(_HINTS_WITH_A_FLAT_BODY) is True
    with pytest.raises(NotAWorkflowError):
        check_comfy_workflow(_HINTS_WITH_A_FLAT_BODY)  # so it is never imported

    assert is_api_format(_HINTS_WITH_A_FLAT_BODY) is False
    assert extract_generation_info(_HINTS_WITH_A_FLAT_BODY)["models"] == []
    assert loaded_model_widgets(_HINTS_WITH_A_FLAT_BODY) == []
    assert api_graph(_HINTS_WITH_A_FLAT_BODY) is None
    # An editor document is one the hints alone can name, so there may be no
    # `nodes` to read a title out of, and looking must not raise.
    assert node_title({"last_node_id": 9}, "1", "SaveImage") == "SaveImage"

    # The controls: a real API graph and a real editor file each still read as
    # what they are.
    graph = _t2i_graph()
    assert is_api_format(graph) is True
    assert api_graph(graph) is graph
    editor = _load(UI_FIXTURES / "image_z_image_turbo.json")
    assert is_api_format(editor) is False
    assert extract_generation_info(editor)["models"] == [
        "z_image_turbo_bf16.safetensors"
    ]


def _card_input(label: str, mode: str = PICKER, pixel_sha=None) -> CardInput:
    return CardInput(
        slot_label=label,
        input_name="image",
        title=label,
        mode=mode,
        pixel_sha=pixel_sha,
        node_ids=(label,),
        class_type="LoadImage",
    )


def test_a_selection_routed_on_one_card_does_not_move_another_cards():
    """`routed` is asked of the card's own addresses (#1457 review, L8).

    A request spanning several cards names inputs of one of them; another
    card's stored Selection must not yield to an address it does not have.
    """
    other_card = [_card_input("subject", SELECTION), _card_input("reference")]
    requested = {("elsewhere", "image"): None}
    fills = resolve_fills(other_card, requested, {}, has_selection=True)
    assert [fill.how for fill in fills] == ["selection", None]
    # On the card that has the address, the stored Selection does yield.
    this_card = other_card + [_card_input("elsewhere")]
    fills = resolve_fills(this_card, requested, {}, has_selection=True)
    assert [fill.how for fill in fills] == [None, None, "selection"]


def test_an_original_stands_in_for_the_selection_and_spends_it():
    """A replay's original fills its input, and the picture goes nowhere else.

    Without the second half the lone rule sees one input still open and hands
    the replayed picture to the reference beside the one its original took.
    """
    subject, reference = ("subject", "image"), ("reference", "image")
    card = [_card_input("subject", SELECTION), _card_input("reference")]
    fills = resolve_fills(card, {}, {}, True, {subject: 7})
    assert [(fill.how, fill.picture_id) for fill in fills] == [
        ("original", 7),
        (None, None),
    ]
    # The request and a pin both come first.
    fills = resolve_fills(card, {subject: None}, {}, True, {subject: 7})
    assert [fill.how for fill in fills] == ["selection", None]
    pinned = [_card_input("subject", "fixed", "sha"), _card_input("reference")]
    fills = resolve_fills(pinned, {}, {"sha": 3}, True, {subject: 7, reference: 8})
    assert [(fill.how, fill.picture_id) for fill in fills] == [
        ("fixed", 3),
        ("original", 8),
    ]
    # Spent for a stored Selection too, not only for the lone rule: the
    # original is in one input, and the picture does not go in the other.
    swapped = [_card_input("subject"), _card_input("reference", SELECTION)]
    fills = resolve_fills(swapped, {}, {}, True, {subject: 7})
    assert [fill.how for fill in fills] == ["original", None]
    # With no original named, the same card takes the selection as it always did.
    fills = resolve_fills(swapped, {}, {}, True, {})
    assert [fill.how for fill in fills] == [None, "selection"]


def test_a_converted_graph_keeps_the_workflows_own_keys():
    """The editor workflow's output choice wins over one the conversion carried."""
    editor = {"nodes": [], "links": [], "pixlstash_output": "mine"}
    graph = {
        "1": {"class_type": "SaveImage", "inputs": {}},
        "pixlstash_output": "theirs",
    }
    assert with_converted_graph(editor, graph)["pixlstash_output"] == "mine"
