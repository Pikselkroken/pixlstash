"""Pulling a ComfyUI's saved workflows into the library (#1440).

No server: the pull is a task over a hub, the userdata client and the import's
own store, so each is driven directly against a tmp hub and tmp workflow
folders, with ComfyUI faked at ``requests.get``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest
import requests
from fastapi import HTTPException

from pixlstash.hub.db import HubDatabase
from pixlstash.routes import comfyui as comfyui_module
from pixlstash.services import comfyui_userdata, workflow_inbox
from pixlstash.services.comfyui_userdata import (
    MultiUserComfyUIError,
    list_saved_workflows,
    read_saved_workflow,
    saved_workflow_url,
)
from pixlstash.services.workflow_hash import ui_topology_hash
from pixlstash.tasks.comfyui_workflow_pull_task import (
    ComfyUIWorkflowPullTask,
    missing_node_classes,
    model_triage,
    stored_name_for,
)
from pixlstash.utils.comfyui_utilities import loaded_model_widgets

BASE = "http://comfy.test:8188"
FIXTURES = Path(__file__).parent / "comfyui_workflows"

# An editor-format workflow with a subgraph, and one using a class from a node
# pack (`LoRACharacterPromptBuilder`) the fake ComfyUI below does not have.
PLAIN = json.loads((FIXTURES / "image_z_image_turbo.json").read_text("utf-8"))
NEEDS_PACK = json.loads((FIXTURES / "image_flux2_klein_t2i.json").read_text("utf-8"))
ABSENT_CLASS = "LoRACharacterPromptBuilder"


# What the fake ComfyUI can load: everything PLAIN names, and every model of
# NEEDS_PACK except its UNET.
ABSENT_MODEL = "flux-2-klein-9b-fp8.safetensors"
_LOADERS = {
    "UNETLoader": {
        "input": {"required": {"unet_name": [["z_image_turbo_bf16.safetensors"], {}]}}
    },
    "CLIPLoader": {
        "input": {
            "required": {
                "clip_name": [
                    ["qwen_3_4b.safetensors", "qwen_3_8b_fp8mixed.safetensors"],
                    {},
                ]
            }
        }
    },
    "VAELoader": {
        "input": {
            "required": {
                "vae_name": [["ae.safetensors", "flux2/flux2-vae.safetensors"], {}]
            }
        }
    },
}


class _Response:
    def __init__(self, status_code: int, payload=None, *, text: str | None = None):
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)
        self.content = self.text.encode("utf-8")

    def json(self):
        # `fetch_object_info` reads the whole body; the userdata client streams.
        return json.loads(self.text)

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def close(self):
        return None


class FakeComfyUI:
    """Answers the three routes a pull asks, from in-memory state."""

    def __init__(self, workflows: dict[str, dict]):
        self.workflows = workflows
        self.multi_user = False
        self.reachable = True
        self.object_info_up = True
        self.classes = missing_node_classes(PLAIN, {}) + missing_node_classes(
            NEEDS_PACK, {}
        )
        self.requested: list[str] = []

    def get(self, url, **_kwargs):
        self.requested.append(url)
        if not self.reachable:
            raise requests.ConnectionError("refused")
        if url == f"{BASE}/api/users":
            if self.multi_user:
                return _Response(200, {"storage": "server", "users": {"a": "A"}})
            return _Response(200, {"storage": "server", "migrated": True})
        if url == f"{BASE}/object_info":
            if not self.object_info_up:
                return _Response(500, text="boom")
            info = {name: {} for name in self.classes if name != ABSENT_CLASS}
            info.update(_LOADERS)
            return _Response(200, info)
        if url.startswith(f"{BASE}/api/userdata?"):
            return _Response(
                200,
                [
                    {"path": path, "size": 10, "modified": 1780844045116}
                    for path in self.workflows
                ],
            )
        for path, document in self.workflows.items():
            if url == saved_workflow_url(BASE, path):
                return _Response(200, document)
        return _Response(404, text="not found")


@pytest.fixture
def comfy(monkeypatch):
    fake = FakeComfyUI({"Plain.json": PLAIN, "Sub/Needs pack.json": NEEDS_PACK})
    monkeypatch.setattr(requests, "get", fake.get)
    return fake


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """Point every workflow folder at tmp, and fake the trash."""
    user = tmp_path / "user"
    builtin = tmp_path / "built-in"
    inbox = tmp_path / "inbox"
    for folder in (user, builtin, inbox):
        folder.mkdir()

    def fake_trash(path):
        os.remove(path)

    monkeypatch.setattr(
        comfyui_module,
        "_workflow_dirs",
        lambda: [("user", str(user)), ("built-in", str(builtin))],
    )
    monkeypatch.setattr(comfyui_module, "workflow_user_dir", lambda: str(user))
    monkeypatch.setattr(workflow_inbox, "workflow_inbox_dir", lambda: str(inbox))
    monkeypatch.setattr(workflow_inbox, "send2trash", fake_trash)
    monkeypatch.setattr(comfyui_module, "send2trash", fake_trash)
    return user, builtin


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    try:
        yield database
    finally:
        database.close()


def _pull(hub, announced=None) -> dict:
    task = ComfyUIWorkflowPullTask(
        hub,
        BASE,
        store=lambda name, doc: comfyui_module.store_pulled_workflow(hub, name, doc),
        announce=announced.extend if announced is not None else None,
    )
    result = task._run_task()
    assert task._processed_count == task._total_count == result["listed"]
    return result


def _stored(user: Path) -> list[str]:
    return sorted(p.name for p in user.iterdir() if p.suffix == ".json")


# ── the userdata client ─────────────────────────────────────────────────────


def test_a_nested_path_is_one_encoded_segment():
    # The file route is a single path segment: an unencoded slash is a 404.
    url = saved_workflow_url(BASE, "Sub/Name one.json")
    assert url == f"{BASE}/api/userdata/workflows%2FSub%2FName%20one.json"
    assert "/api/userdata/workflows/" not in url
    assert url != f"{BASE}/api/userdata/{quote('workflows/Sub/Name one.json')}"


def test_the_listing_is_parsed_normalised_and_sorted(monkeypatch):
    payload = [
        {"path": "b.json", "size": 5, "modified": 1780844045116},
        "a.json",
        {"path": "Sub\\c.json", "size": 7, "modified": 1.5},
        {"path": "notes.txt", "size": 1},
        {"size": 3},
        42,
    ]
    monkeypatch.setattr(requests, "get", lambda url, **_kw: _Response(200, payload))
    listed = list_saved_workflows(BASE)
    assert [(e.path, e.size, e.modified_ms) for e in listed] == [
        ("Sub/c.json", 7, 1),
        ("a.json", None, None),
        ("b.json", 5, 1780844045116),
    ]


def test_no_workflows_folder_is_an_empty_listing(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, **_kw: _Response(404, text=""))
    assert list_saved_workflows(BASE) == []


@pytest.mark.parametrize(
    "response",
    [_Response(500, text="boom"), _Response(200, {"not": "a list"}), None],
    ids=["non-200", "not-a-list", "unreachable"],
)
def test_a_bad_listing_raises_runtime_error(monkeypatch, response):
    def get(url, **_kw):
        if response is None:
            raise requests.ConnectionError("refused")
        return response

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(RuntimeError):
        list_saved_workflows(BASE)


@pytest.mark.parametrize(
    "response",
    [_Response(200, ["a list"]), _Response(200, text="{nope"), _Response(404, text="")],
    ids=["not-an-object", "not-json", "gone"],
)
def test_a_bad_document_raises_runtime_error(monkeypatch, response):
    monkeypatch.setattr(requests, "get", lambda url, **_kw: response)
    with pytest.raises(RuntimeError):
        read_saved_workflow(BASE, "a.json")


def test_multi_user_comfyui_is_refused_and_single_user_is_not(monkeypatch):
    answers = {
        "multi": _Response(200, {"storage": "server", "users": {}}),
        "single": _Response(200, {"storage": "server", "migrated": True}),
        "old": _Response(404, text=""),
    }
    for kind, response in answers.items():
        monkeypatch.setattr(requests, "get", lambda url, r=response, **_kw: r)
        if kind == "multi":
            with pytest.raises(MultiUserComfyUIError):
                comfyui_userdata.ensure_single_user(BASE)
        else:
            comfyui_userdata.ensure_single_user(BASE)


@pytest.mark.parametrize(
    ("remote", "stored"),
    [
        ("Name.json", "Name.json"),
        ("Sub/Name.json", "Sub - Name.json"),
        ("https:/host/path.json", "https_ - host - path.json"),
        ("../../etc/passwd.json", "etc - passwd.json"),
        ("a\\b.json", "a - b.json"),
        ("con.json", "_con.json"),
        ("CON.foo.json", "_CON.foo.json"),
        ("NUL.txt.json", "_NUL.txt.json"),
        ("CONIN$.json", "_CONIN$.json"),
        ("COM\u00b9.json", "_COM\u00b9.json"),
        ("Console.json", "Console.json"),
        ("trailing. .json", "trailing.json"),
        ("../.json", "workflow.json"),
    ],
)
def test_a_remote_path_is_stored_as_one_flat_safe_name(remote, stored):
    assert stored_name_for(remote) == stored


def test_a_redirect_is_refused_rather_than_followed(monkeypatch):
    seen = {}

    def get(url, **kwargs):
        seen.update(kwargs)
        return _Response(302, text="")

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(RuntimeError, match="redirected"):
        read_saved_workflow(BASE, "a.json")
    assert seen["allow_redirects"] is False and seen["stream"] is True


def test_a_body_past_the_cap_is_refused_without_reading_it_all(monkeypatch):
    monkeypatch.setattr(comfyui_userdata, "MAX_SAVED_WORKFLOW_BYTES", 10)
    big = _Response(200, {"nodes": ["x" * 100]})
    read = []
    chunks = big.iter_content

    def counted(chunk_size=1):
        for chunk in chunks(chunk_size=4):
            read.append(chunk)
            yield chunk

    big.iter_content = counted
    monkeypatch.setattr(requests, "get", lambda url, **_kw: big)
    with pytest.raises(RuntimeError, match="past the 10 bytes"):
        read_saved_workflow(BASE, "a.json")
    assert sum(len(chunk) for chunk in read) < len(big.content)


def test_a_listing_past_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(comfyui_userdata, "MAX_LISTED_WORKFLOWS", 2)
    listing = _Response(200, ["a.json", "b.json", "c.json"])
    monkeypatch.setattr(requests, "get", lambda url, **_kw: listing)
    with pytest.raises(RuntimeError, match="more than the 2"):
        list_saved_workflows(BASE)


# ── the pull ────────────────────────────────────────────────────────────────


def test_a_pull_stores_every_workflow_and_a_second_matches_them(comfy, folders, hub):
    user, _builtin = folders
    announced: list[str] = []
    first = _pull(hub, announced)
    assert (first["pulled"], first["matched"], first["failed"]) == (2, 0, 0)
    assert _stored(user) == ["Plain.json", "Sub - Needs pack.json"]
    # Stored byte-for-byte what ComfyUI holds.
    assert json.loads((user / "Plain.json").read_text("utf-8")) == PLAIN
    assert announced == first["workflow_keys"] and len(announced) == 2

    second = _pull(hub)
    assert (second["pulled"], second["matched"]) == (0, 2)
    assert _stored(user) == ["Plain.json", "Sub - Needs pack.json"]


def test_the_triage_names_the_workflow_this_comfyui_cannot_run(comfy, folders, hub):
    result = _pull(hub)
    assert result["nodes_checked"] is True
    assert result["missing_nodes"] == 1
    assert result["nodes_unchecked"] == 0
    assert result["missing_node_classes"] == [ABSENT_CLASS]


def test_the_triage_names_the_model_file_this_comfyui_does_not_list(
    comfy, folders, hub
):
    # `flux2-vae.safetensors` is listed under a subfolder, which is present.
    result = _pull(hub)
    assert result["missing_models"] == 1
    assert result["missing_model_files"] == [ABSENT_MODEL]
    assert result["models_unread"] == 0
    assert result["models_unchecked"] == 0


def test_a_model_value_no_reader_could_name_is_counted_not_dropped():
    wan = json.loads((FIXTURES / "video_wan2_2_14B_t2v.json").read_text("utf-8"))
    # Everything the reader names is advertised, and it names 6 of the 8
    # model-shaped values in the file.
    named = {value for _widget, value in loaded_model_widgets(wan)}
    absent, unread = model_triage(wan, {"UNETLoader": {}}, named)
    assert absent == []
    assert unread == 2


def test_a_pulled_workflow_a_picture_already_made_is_counted(comfy, folders, hub):
    topology = ui_topology_hash(PLAIN)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workflow_topology (topology_hash, "
            "hash_version, node_count, first_seen_at) VALUES (?, ?, ?, ?)",
            (topology, "test", 1, "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO workflow_recipe (structural_hash, topology_hash, "
            "hash_version, node_count, first_seen_at) VALUES (?, ?, ?, ?, ?)",
            ("s" * 64, topology, "test", 1, "2026-01-01T00:00:00+00:00"),
        )
    result = _pull(hub)
    assert result["known_from_pictures"] == 1


def test_without_object_info_every_workflow_is_unchecked_never_fine(
    comfy, folders, hub
):
    comfy.object_info_up = False
    result = _pull(hub)
    assert result["pulled"] == 2
    assert result["nodes_checked"] is False
    assert result["missing_nodes"] == 0
    assert result["nodes_unchecked"] == 2
    assert result["models_unchecked"] == 2
    assert result["missing_models"] == 0


def test_a_workflow_deleted_here_is_not_pulled_back(comfy, folders, hub):
    user, _builtin = folders
    # A second path holding the same document: content matching files both
    # as one file, and the delete has to dismiss both paths or the twin
    # restores it.
    comfy.workflows["Copy of plain.json"] = copy.deepcopy(PLAIN)
    first = _pull(hub)
    assert (first["pulled"], first["matched"]) == (2, 1)
    stored_as = {
        row["remote_path"]: row["workflow_name"]
        for row in hub.fetchall(
            "SELECT remote_path, workflow_name FROM workflow_origin"
        )
    }
    assert stored_as["Copy of plain.json"] == stored_as["Plain.json"]

    comfyui_module.trash_user_workflow(hub, stored_as["Plain.json"])
    assert not (user / stored_as["Plain.json"]).exists()

    again = _pull(hub)
    assert again["skipped_dismissed"] == 2
    assert (again["pulled"], again["matched"]) == (0, 1)
    assert not (user / stored_as["Plain.json"]).exists()


def test_a_workflow_gone_from_comfyui_keeps_its_local_file(comfy, folders, hub):
    user, _builtin = folders
    _pull(hub)
    del comfy.workflows["Plain.json"]
    result = _pull(hub)
    assert result["gone"] == 1
    assert (user / "Plain.json").exists()
    paths = [r["remote_path"] for r in hub.fetchall("SELECT * FROM workflow_origin")]
    assert paths == ["Sub/Needs pack.json"]


def test_a_workflow_pixlstash_ships_is_reported_apart(comfy, folders, hub):
    user, builtin = folders
    (builtin / "Shipped.json").write_text(json.dumps(PLAIN), encoding="utf-8")
    result = _pull(hub)
    assert (result["already_shipped"], result["pulled"]) == (1, 1)
    assert _stored(user) == ["Sub - Needs pack.json"]


def test_a_different_workflow_under_a_taken_name_is_kept_beside_it(comfy, folders, hub):
    user, _builtin = folders
    (user / "Plain.json").write_text(json.dumps(NEEDS_PACK), encoding="utf-8")
    del comfy.workflows["Sub/Needs pack.json"]
    result = _pull(hub)
    assert result["pulled"] == 1
    assert _stored(user) == ["Plain (2).json", "Plain.json"]


def test_a_multi_user_comfyui_is_refused_before_anything_is_stored(comfy, folders, hub):
    user, _builtin = folders
    comfy.multi_user = True
    with pytest.raises(MultiUserComfyUIError):
        _pull(hub)
    assert _stored(user) == []


def test_an_unreadable_workflow_is_counted_failed_and_the_rest_still_pull(
    comfy, folders, hub
):
    user, _builtin = folders
    comfy.workflows["Broken.json"] = {"not": "a workflow"}
    result = _pull(hub)
    assert (result["failed"], result["pulled"]) == (1, 2)
    assert "Broken.json" not in _stored(user)


# ── the routes ──────────────────────────────────────────────────────────────


def _endpoint(server, method: str):
    router = comfyui_module.create_router(server)
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/comfyui/workflows/pull"
        and method in route.methods
    )


def _request():
    return SimpleNamespace(state=SimpleNamespace(origin_client_id=None))


@pytest.fixture
def pull_routes(hub, monkeypatch):
    """The two routes over a stub server whose runner queues and never runs."""
    monkeypatch.setattr(comfyui_module, "_last_pull", {})
    submitted: list[ComfyUIWorkflowPullTask] = []

    def submit(task):
        submitted.append(task)
        return task.id

    server = MagicMock(hub=hub)
    server.auth.get_user_for_request.return_value = SimpleNamespace(
        comfyui_url=f"{BASE}/"
    )
    server.vault.submit_task.side_effect = submit
    return server, _endpoint(server, "POST"), _endpoint(server, "GET"), submitted


def test_a_second_press_while_a_pull_is_queued_does_not_queue_another(pull_routes):
    _server, start, state, submitted = pull_routes
    assert state() == {"status": "idle"}
    first = start(_request())
    assert first["status"] == "started"
    assert submitted[0].params == {"comfyui_url": BASE}
    again = start(_request())
    assert again == {"status": "already_running", "task_id": first["task_id"]}
    assert len(submitted) == 1
    assert state()["status"] == "pending"


def test_a_finished_pull_answers_its_summary_and_a_failed_one_its_reason(
    pull_routes,
):
    _server, start, state, submitted = pull_routes
    start(_request())
    task = submitted[0]
    task.status = comfyui_module.TaskStatus.COMPLETED
    task.result = {"listed": 3, "pulled": 3, "nodes_checked": True}
    answered = state()
    assert answered["status"] == "completed"
    assert answered["summary"]["pulled"] == 3

    # A finished pull no longer holds the gate.
    assert start(_request())["status"] == "started"
    failed = submitted[1]
    failed.status = comfyui_module.TaskStatus.FAILED
    failed.error = "ComfyUI runs with --multi-user"
    assert state() == {
        "status": "failed",
        "task_id": failed.id,
        "comfyui_url": BASE,
        "error": "ComfyUI runs with --multi-user",
    }


def test_no_hub_or_no_runner_is_a_503_and_frees_the_gate(pull_routes):
    server, start, state, _submitted = pull_routes
    server.vault.submit_task.side_effect = None
    server.vault.submit_task.return_value = None
    with pytest.raises(HTTPException) as refused:
        start(_request())
    assert refused.value.status_code == 503
    assert state() == {"status": "idle"}

    server.hub = None
    with pytest.raises(HTTPException) as no_hub:
        start(_request())
    assert no_hub.value.status_code == 503


def test_a_model_named_with_a_folder_comfyui_lists_flat_is_present():
    # The file was saved on a machine that kept its VAEs in a subfolder; this
    # ComfyUI lists the same file at the top level. Present, not missing.
    moved = json.loads(
        json.dumps(NEEDS_PACK).replace(
            '"flux2-vae.safetensors"', '"vae\\\\flux2-vae.safetensors"'
        )
    )
    assert ("vae_name", "vae\\flux2-vae.safetensors") in loaded_model_widgets(moved)
    advertised = {
        "flux-2-klein-9b-fp8.safetensors",
        "qwen_3_8b_fp8mixed.safetensors",
        "flux2-vae.safetensors",
    }
    assert model_triage(moved, {"UNETLoader": {}}, advertised) == ([], 0)
