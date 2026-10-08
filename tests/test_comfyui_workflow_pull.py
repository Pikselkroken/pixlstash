"""Pulling a ComfyUI's saved workflows into the library (#1440).

No server: the pull is a task over a hub, the userdata client and the import's
own store, so each is driven directly against a tmp hub and tmp workflow
folders, with ComfyUI faked at ``requests.get``.
"""

from __future__ import annotations

import copy
import functools
import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock
from urllib.parse import parse_qs, quote, urlsplit

import pytest
import requests
from fastapi import HTTPException

from pixlstash.hub import workflow_origin
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_card_reads import card_index, manual_document
from pixlstash.hub import workflow_card_reads, workflow_versions
from pixlstash.hub.workflow_group_writes import (
    delete_manual_workflow,
    set_manual_api_document,
)
from pixlstash.routes import comfyui as comfyui_module
from pixlstash.server import Server
from pixlstash.utils.workflow_ids import tagged_workflow_id, untagged
from pixlstash.services import comfyui_userdata, workflow_inbox
from pixlstash.routes import workflows as workflows_routes
from pixlstash.services import comfyui_workflow_pulls as pull_comfyui_module
from pixlstash.services import workflow_bindings
from pixlstash.services.comfyui_workflow_pulls import WorkflowPulls
from pixlstash.services.comfyui_userdata import (
    MultiUserComfyUIError,
    list_saved_workflows,
    read_saved_workflow,
    saved_workflow_url,
)
from pixlstash.services.workflow_hash import ui_topology_hash
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks import base_task_finder
from pixlstash.tasks import comfyui_workflow_pull_task as pull_task_module
from pixlstash.tasks.comfyui_workflow_poll_finder import ComfyUIWorkflowPollFinder
from pixlstash.tasks.comfyui_workflow_pull_task import (
    ComfyUIWorkflowPullTask,
    missing_node_classes,
    model_triage,
    stored_name_for,
)
from pixlstash.utils.comfyui_utilities import loaded_model_widgets
from pixlstash.utils.service.user_settings_utils import apply_user_config_patch

BASE = "http://comfy.test:8188"
# What the fake ComfyUI lists as every file's `modified` until it is edited.
MODIFIED = 1780844045116
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
        # More node declarations `object_info` answers with, for a test that
        # needs a workflow the converter can actually read.
        self.extra_info: dict = {}
        self.classes = missing_node_classes(PLAIN, {}) + missing_node_classes(
            NEEDS_PACK, {}
        )
        self.requested: list[str] = []
        # `GET /history`; None answers 404, as a ComfyUI without the route.
        self.history: dict | None = None
        # Each path's `modified`; an edit made here must move it, as saving
        # in ComfyUI does.
        self.modified: dict[str, int] = {}

    def edit(self, path: str, document: dict) -> None:
        """Save *document* at *path*, as ComfyUI would: a new `modified`."""
        self.workflows[path] = document
        self.modified[path] = self.modified.get(path, MODIFIED) + 1000

    def _entry(self, path: str, relative_to: str = "") -> dict:
        return {
            "path": path[len(relative_to) :],
            "size": 10,
            "modified": self.modified.get(path, MODIFIED),
        }

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
            info.update(self.extra_info)
            return _Response(200, info)
        if url.startswith(f"{BASE}/history?") and self.history is not None:
            return _Response(200, self.history)
        if url.startswith(f"{BASE}/api/userdata?"):
            query = parse_qs(urlsplit(url).query)
            folder = query["dir"][0]
            if query.get("recurse") == ["true"]:
                return _Response(200, [self._entry(path) for path in self.workflows])
            # One folder, as the check Run and Open make asks for.
            prefix = folder.removeprefix("workflows").lstrip("/")
            prefix = f"{prefix}/" if prefix else ""
            return _Response(
                200,
                [
                    self._entry(path, prefix)
                    for path in self.workflows
                    if path.startswith(prefix) and "/" not in path[len(prefix) :]
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
        store=functools.partial(comfyui_module.store_pulled_workflow, hub),
        announce=announced.extend if announced is not None else None,
    )
    result = task._run_task()
    assert task._processed_count == task._total_count == result["listed"]
    return result


def _stored(hub) -> list[str]:
    """The names of the manual workflows a pull stored."""
    return sorted(
        row[0]
        for row in hub.fetchall(
            "SELECT a.name FROM workflow_document d JOIN workflow_group_attr a "
            "ON a.workflow_id = d.workflow_id WHERE d.origin = 'pull'"
        )
    )


def _manual_id(hub, name: str) -> str:
    return hub.fetchone(
        "SELECT workflow_id FROM workflow_group_attr WHERE name = ?", (name,)
    )[0]


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
    assert _stored(hub) == ["Plain", "Sub - Needs pack"]
    # Stored as ComfyUI holds it plus its own tag, as a manual workflow: no
    # file is written.
    plain = _manual_id(hub, "Plain")
    stored = manual_document(hub, plain)
    assert tagged_workflow_id(stored) == plain
    assert untagged(stored) == PLAIN
    assert list(user.iterdir()) == []
    assert announced == first["workflow_ids"]
    assert len(set(announced)) == 2
    assert all(workflow_id.startswith("manual:") for workflow_id in announced)

    # A restart's pull re-imports nothing, and reads no file: the origin rows
    # hold each path's `modified`, and the listing says it did not move.
    comfy.requested.clear()
    second = _pull(hub)
    assert (second["pulled"], second["matched"], second["unchanged"]) == (0, 0, 2)
    assert _stored(hub) == ["Plain", "Sub - Needs pack"]
    assert not any("workflows%2F" in url for url in comfy.requested)


def test_a_workflow_whose_origin_cannot_be_recorded_is_not_stored(
    comfy, folders, hub, monkeypatch
):
    # One transaction (#1694): a workflow stored without its origin row would
    # come back after a delete, so a failed origin write stores nothing.
    def refuse(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    with monkeypatch.context() as patch:
        patch.setattr(workflow_origin, "upsert", refuse)
        first = _pull(hub)
    assert (first["pulled"], first["failed"]) == (0, 2)
    assert _stored(hub) == []
    assert hub.fetchone("SELECT COUNT(*) FROM workflow_document")[0] == 0

    again = _pull(hub)
    assert again["pulled"] == 2
    assert {
        row["remote_path"]: row["workflow_name"]
        for row in hub.fetchall(
            "SELECT remote_path, workflow_name FROM workflow_origin"
        )
    } == {
        "Plain.json": _manual_id(hub, "Plain"),
        "Sub/Needs pack.json": _manual_id(hub, "Sub - Needs pack"),
    }


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


def _seed_picture_of(hub, topology: str) -> None:
    """A recipe of *topology* that a picture was made with: an instance row."""
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
        conn.execute(
            "INSERT INTO workflow_recipe_instance (library_uuid, instance_hash, "
            "structural_hash, hash_version, document, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("library", "i" * 64, "s" * 64, "test", "{}", "2026-01-01"),
        )


def test_a_pulled_workflow_a_picture_already_made_is_counted(comfy, folders, hub):
    _seed_picture_of(hub, ui_topology_hash(PLAIN))
    result = _pull(hub)
    assert result["known_from_pictures"] == 1


# A minimal API-format graph: an import writes a `workflow_recipe` row for it,
# which is exactly what must NOT count as picture evidence.
API_GRAPH = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "base.safetensors"},
    },
    "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 512}},
    "3": {
        "class_type": "KSampler",
        "inputs": {"model": ["1", 0], "latent_image": ["2", 0], "seed": 1},
    },
    "4": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["1", 2]}},
    "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
}


def test_a_pulled_api_graph_does_not_count_itself_as_known(comfy, folders, hub):
    comfy.workflows = {"Api.json": API_GRAPH}
    announced: list[str] = []
    result = _pull(hub, announced)
    assert result["pulled"] == 1
    assert announced == result["workflow_ids"] == [_manual_id(hub, "Api")]
    # A manual workflow files nothing in the picture tables.
    assert hub.fetchone("SELECT 1 FROM workflow_recipe") is None
    assert result["known_from_pictures"] == 0


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
    # A second path holding the same document: content matching stores both
    # as one workflow, and the delete has to dismiss both paths or the twin
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

    delete_manual_workflow(hub, stored_as["Plain.json"])

    again = _pull(hub)
    assert again["skipped_dismissed"] == 2
    assert (again["pulled"], again["matched"], again["unchanged"]) == (0, 0, 1)
    assert _stored(hub) == ["Sub - Needs pack"]


def test_a_workflow_gone_from_comfyui_keeps_its_workflow(comfy, folders, hub):
    _pull(hub)
    plain = comfy.workflows.pop("Plain.json")
    result = _pull(hub)
    assert result["gone"] == 1
    assert _stored(hub) == ["Plain", "Sub - Needs pack"]
    # The link is kept and marked, so Open can tell the file is gone.
    gone = {
        row["remote_path"]: row["gone_at"] is not None
        for row in hub.fetchall("SELECT remote_path, gone_at FROM workflow_origin")
    }
    assert gone == {"Plain.json": True, "Sub/Needs pack.json": False}
    workflow_id = _manual_id(hub, "Plain")
    assert workflow_origin.live_file(hub, BASE, workflow_id) is None

    # Saved again unchanged, the mark clears without a read.
    comfy.workflows["Plain.json"] = plain
    back = _pull(hub)
    assert (back["gone"], back["unchanged"]) == (0, 2)
    assert workflow_origin.live_file(hub, BASE, workflow_id)["remote_path"] == (
        "Plain.json"
    )


def test_a_workflow_pixlstash_ships_is_reported_apart(comfy, folders, hub):
    _user, builtin = folders
    (builtin / "Shipped.json").write_text(json.dumps(PLAIN), encoding="utf-8")
    result = _pull(hub)
    assert (result["already_shipped"], result["pulled"]) == (1, 1)
    assert _stored(hub) == ["Sub - Needs pack"]


def test_a_different_workflow_under_a_taken_name_is_a_workflow_of_its_own(
    comfy, folders, hub
):
    user, _builtin = folders
    # A legacy user file of that name is never compared: no folder is read.
    (user / "Plain.json").write_text(json.dumps(PLAIN), encoding="utf-8")
    del comfy.workflows["Sub/Needs pack.json"]
    result = _pull(hub)
    assert result["pulled"] == 1
    assert _stored(hub) == ["Plain"]


def test_a_multi_user_comfyui_is_refused_before_anything_is_stored(comfy, folders, hub):
    user, _builtin = folders
    comfy.multi_user = True
    with pytest.raises(MultiUserComfyUIError):
        _pull(hub)
    assert _stored(hub) == []


def test_an_unreadable_workflow_is_counted_failed_and_the_rest_still_pull(
    comfy, folders, hub
):
    user, _builtin = folders
    comfy.workflows["Broken.json"] = {"not": "a workflow"}
    result = _pull(hub)
    assert (result["failed"], result["pulled"]) == (1, 2)
    assert "Broken" not in _stored(hub)


def test_a_pulled_workflow_past_the_row_cap_is_failed_not_stored(
    comfy, folders, hub, monkeypatch
):
    """The listing may give no size; the stored row's cap still holds."""
    monkeypatch.setattr(
        comfyui_module, "MAX_WORKFLOW_FILE_BYTES", len(json.dumps(PLAIN)) + 1
    )
    result = _pull(hub)
    assert (result["failed"], result["pulled"]) == (1, 1)
    assert _stored(hub) == ["Plain"]


# ── the routes ──────────────────────────────────────────────────────────────


def _endpoint(router, method: str):
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
    submitted: list[ComfyUIWorkflowPullTask] = []

    def submit(task):
        submitted.append(task)
        return task.id

    server = MagicMock(hub=hub)
    server.auth.get_user_for_request.return_value = SimpleNamespace(
        comfyui_url=f"{BASE}/"
    )
    server.vault.submit_task.side_effect = submit
    # One gate for both routes, the poll and the Link: the server's.
    server.workflow_pulls = WorkflowPulls(server, comfyui_module.store_pulled_workflow)
    router = comfyui_module.create_router(server)
    return server, _endpoint(router, "POST"), _endpoint(router, "GET"), submitted


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


def test_a_submit_that_raises_frees_the_gate(pull_routes):
    server, start, _state, _submitted = pull_routes
    server.vault.submit_task.side_effect = ValueError("a bug in the runner")
    with pytest.raises(ValueError):
        start(_request())
    server.vault.submit_task.side_effect = lambda task: task.id
    assert start(_request())["status"] == "started"


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


def test_a_pulled_workflow_is_imported_and_never_a_one_off(comfy, folders, hub):
    """One-offs are workflows known from pictures alone: a pulled one is listed
    the moment it arrives, so every card the pull stored reads as imported."""
    _pull(hub)
    imported = [card.imported for card in card_index(hub) if card.manual]
    assert imported and all(imported)


def test_a_hub_made_before_content_hash_gains_the_column(tmp_path):
    """A development hub that ran an earlier commit of the pull (#1440)."""
    path = str(tmp_path / "older.db")
    database = HubDatabase(path)
    with database.transaction() as conn:
        conn.execute("DROP INDEX ix_workflow_origin_content")
        conn.execute("ALTER TABLE workflow_origin DROP COLUMN content_hash")
    database.close()

    reopened = HubDatabase(path)
    try:
        columns = {
            row[1] for row in reopened.fetchall("PRAGMA table_info(workflow_origin)")
        }
        assert "content_hash" in columns
    finally:
        reopened.close()


def test_a_file_dropped_in_the_watched_folder_matches_a_pulled_workflow(
    comfy, folders, hub
):
    """The inbox dedupes on the origin rows too: content a pull stored is
    not stored twice."""
    _pull(hub)
    server = SimpleNamespace(hub=hub, vault=None)
    result = Server._store_inbox_workflow(server, "dropped", NEEDS_PACK)
    assert (result["matched"], result["workflow_id"]) == (
        True,
        _manual_id(hub, "Sub - Needs pack"),
    )
    assert hub.fetchone("SELECT COUNT(*) FROM workflow_document")[0] == 2


# ── what the independent review of #1502 reproduced ─────────────────────────


def _pull_with(hub, store=None, origin=BASE) -> dict:
    task = ComfyUIWorkflowPullTask(
        hub,
        origin,
        store=store or functools.partial(comfyui_module.store_pulled_workflow, hub),
        lock=workflow_inbox.INBOX_LOCK,
    )
    result = task._run_task()
    assert task._processed_count == task._total_count == result["listed"]
    return result


def _delete_pulled(hub, remote_path: str) -> str:
    workflow_id = hub.fetchone(
        "SELECT workflow_name FROM workflow_origin WHERE remote_path = ?",
        (remote_path,),
    )["workflow_name"]
    delete_manual_workflow(hub, workflow_id)
    return workflow_id


def test_a_delete_made_while_a_pull_runs_is_not_undone_by_it(comfy, folders, hub):
    _pull(hub)
    # The owner deletes Plain.json between the pull's start and its entry.
    # The delete takes INBOX_LOCK like the pull does, so it runs from the
    # store wrapper of the entry BEFORE Plain.json - between entries, as a
    # real delete landing mid-pull would.
    real = comfyui_module.store_pulled_workflow
    deleted = []

    def store(name, doc, record):
        return real(hub, name, doc, record)

    order = sorted(comfy.workflows)  # the listing is sorted
    assert order == ["Plain.json", "Sub/Needs pack.json"]
    comfy.workflows = {"A first.json": NEEDS_PACK, **comfy.workflows}
    # Saved again in ComfyUI, so this pull reads it rather than passing it by.
    comfy.edit("Plain.json", comfy.workflows["Plain.json"])

    def store_then_delete(name, doc, record):
        outcome = store(name, doc, record)
        if name == "A first.json" and not deleted:
            deleted.append(True)
            # Released and re-taken around the delete, as a request thread
            # would take it between two of the pull's entries.
            workflow_inbox.INBOX_LOCK.release()
            try:
                _delete_pulled(hub, "Plain.json")
            finally:
                workflow_inbox.INBOX_LOCK.acquire()
        return outcome

    result = _pull_with(hub, store=store_then_delete)
    assert deleted
    assert result["skipped_dismissed"] == 1
    assert "Plain" not in _stored(hub)


def test_an_empty_listing_forgets_no_dismissal(comfy, folders, hub):
    _pull(hub)
    _delete_pulled(hub, "Plain.json")
    listing = comfy.workflows
    comfy.workflows = {}
    assert _pull(hub)["gone"] == 0
    comfy.workflows = listing
    again = _pull(hub)
    assert (again["pulled"], again["skipped_dismissed"]) == (0, 1)
    assert "Plain" not in _stored(hub)


def test_the_same_comfyui_under_another_spelling_is_still_dismissed(
    comfy, folders, hub, monkeypatch
):
    _pull(hub)
    _delete_pulled(hub, "Plain.json")
    alias = "http://127.0.0.1:8188"

    def via_alias(url, **kwargs):
        return comfy.get(url.replace(alias, BASE), **kwargs)

    monkeypatch.setattr(requests, "get", via_alias)
    result = _pull_with(hub, origin=alias)
    assert result["skipped_dismissed"] == 1
    assert "Plain" not in _stored(hub)


def test_a_deleted_workflow_renamed_in_comfyui_stays_out(comfy, folders, hub):
    _pull(hub)
    _delete_pulled(hub, "Plain.json")
    comfy.workflows["Renamed.json"] = comfy.workflows.pop("Plain.json")
    result = _pull(hub)
    assert result["skipped_dismissed"] == 1
    assert _stored(hub) == ["Sub - Needs pack"]


def _versions(hub, workflow_id: str) -> list[tuple[int, str]]:
    return [
        (row["version"], row["source"])
        for row in hub.fetchall(
            "SELECT version, source FROM workflow_version WHERE workflow_id = ? "
            "ORDER BY version",
            (workflow_id,),
        )
    ]


def test_a_workflow_edited_in_comfyui_is_a_new_version_of_its_workflow(
    comfy, folders, hub
):
    _pull(hub)
    plain = _manual_id(hub, "Plain")
    assert _versions(hub, plain) == [(1, "pull")]
    edited = copy.deepcopy(PLAIN)
    edited["nodes"][0]["pos"] = [123, 456]
    edited["extra"] = {"edited": True}
    comfy.edit("Plain.json", edited)
    announced: list[str] = []
    result = _pull(hub, announced)
    assert (result["changed"], result["pulled"], result["unchanged"]) == (1, 0, 1)
    # One workflow per file: the same card, now at version 2, which is what
    # every reader of its graph reads.
    assert _stored(hub) == ["Plain", "Sub - Needs pack"]
    assert _versions(hub, plain) == [(1, "pull"), (2, "pull")]
    assert untagged(manual_document(hub, plain))["nodes"][0]["pos"] == [123, 456]
    assert tagged_workflow_id(manual_document(hub, plain)) == plain
    assert announced == [plain]
    link = workflow_origin.live_file(hub, BASE, plain)
    assert link["remote_modified"] == comfy.modified["Plain.json"]

    # Undone in ComfyUI: the first content again is version 3, not a match.
    comfy.edit("Plain.json", copy.deepcopy(PLAIN))
    undone = _pull(hub)
    assert (undone["changed"], undone["matched"]) == (1, 0)
    assert _versions(hub, plain)[-1] == (3, "pull")
    assert untagged(manual_document(hub, plain)) == PLAIN


# A small editor workflow the fake ComfyUI can convert once it declares it.
_SAMPLER_EDITOR = {
    "last_node_id": 2,
    "last_link_id": 0,
    "links": [],
    "nodes": [
        {
            "id": 1,
            "type": "KSampler",
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "widgets_values": [123, 20, 7.0],
        },
        {
            "id": 2,
            "type": "SaveImage",
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "widgets_values": ["ComfyUI"],
        },
    ],
}
_SAMPLER_INFO = {
    "KSampler": {
        "input": {
            "required": {
                "seed": ["INT", {"default": 0}],
                "steps": ["INT", {"default": 20}],
                "cfg": ["FLOAT", {"default": 7.0}],
            }
        }
    },
    "SaveImage": {"input": {"required": {"filename_prefix": ["STRING", {}]}}},
}


def _stored_conversion(hub, workflow_id: str) -> tuple:
    """``(workflow_document's, the current version's)`` stored conversion."""
    row = hub.fetchone(
        "SELECT d.api_document, (SELECT v.api_document FROM workflow_version v "
        "WHERE v.workflow_id = d.workflow_id ORDER BY v.version DESC LIMIT 1) "
        "FROM workflow_document d WHERE d.workflow_id = ?",
        (workflow_id,),
    )
    return tuple(json.loads(value) if value else None for value in row)


def test_a_pull_stores_the_conversion_of_an_editor_workflow_it_can_read(
    comfy, folders, hub
):
    """The pull has ComfyUI's `object_info` in hand, so a pulled editor file is
    converted and stored then: it has parameters before anything reads it."""
    comfy.extra_info = _SAMPLER_INFO
    comfy.workflows["Sampler.json"] = copy.deepcopy(_SAMPLER_EDITOR)
    _pull(hub)
    sampler = _manual_id(hub, "Sampler")
    on_row, on_version = _stored_conversion(hub, sampler)
    assert on_row is not None and on_row == on_version
    assert on_row["1"]["inputs"]["steps"] == 20
    assert manual_document(hub, sampler)["1"]["class_type"] == "KSampler"

    # An edit is a new version, converted by the pull that took it.
    edited = copy.deepcopy(_SAMPLER_EDITOR)
    edited["nodes"][0]["widgets_values"] = [123, 33, 7.0]
    comfy.edit("Sampler.json", edited)
    _pull(hub)
    assert [version for version, _ in _versions(hub, sampler)] == [1, 2]
    on_row, on_version = _stored_conversion(hub, sampler)
    assert on_row == on_version and on_row["1"]["inputs"]["steps"] == 33


def test_a_pull_without_object_info_stores_no_conversion(comfy, folders, hub):
    """No map, no conversion: the workflow's first read converts it later."""
    comfy.extra_info = _SAMPLER_INFO
    comfy.object_info_up = False
    comfy.workflows["Sampler.json"] = copy.deepcopy(_SAMPLER_EDITOR)
    _pull(hub)
    assert _stored_conversion(hub, _manual_id(hub, "Sampler")) == (None, None)


def test_a_quiet_poll_reads_nothing_but_the_listing(comfy, folders, hub):
    """The minute poll: an unchanged ComfyUI costs one request, and tells no
    tab to reload."""
    _pull(hub)
    comfy.requested.clear()
    announced: list[list[str]] = []
    task = ComfyUIWorkflowPullTask(
        hub,
        BASE,
        store=functools.partial(comfyui_module.store_pulled_workflow, hub),
        announce=announced.append,
        background=True,
    )
    result = task._run_task()
    assert result["unchanged"] == 2
    assert comfy.requested == [
        f"{BASE}/api/userdata?dir=workflows&recurse=true&full_info=true"
    ]
    assert announced == []


def test_an_edit_undone_in_comfyui_is_a_version_while_a_copy_holds_the_old(
    comfy, folders, hub
):
    """A second path holding the first content is linked to the same
    workflow, so the content is "already stored" - as this workflow's earlier
    version, not its current one. Undone in ComfyUI, it is a version."""
    comfy.workflows["Copy of plain.json"] = copy.deepcopy(PLAIN)
    _pull(hub)
    # Listed first, so the workflow is named for the copy; both paths name it.
    plain = _manual_id(hub, "Copy of plain")
    assert workflow_origin.live_file(hub, BASE, plain) is not None
    edited = copy.deepcopy(PLAIN)
    edited["extra"] = {"edited": True}
    comfy.edit("Plain.json", edited)
    _pull(hub)
    comfy.edit("Plain.json", copy.deepcopy(PLAIN))
    undone = _pull(hub)
    assert (undone["changed"], undone["matched"]) == (1, 0)
    assert [version for version, _ in _versions(hub, plain)] == [1, 2, 3]
    assert untagged(manual_document(hub, plain)) == PLAIN


def test_a_document_no_reader_can_read_is_unchecked_never_fine(
    comfy, folders, hub, monkeypatch
):
    def unreadable(*_args, **_kwargs):
        raise KeyError("links")

    monkeypatch.setattr(pull_task_module, "reduce_ui_graph", unreadable)
    monkeypatch.setattr(pull_task_module, "loaded_model_widgets", unreadable)
    result = _pull(hub)
    assert result["pulled"] == 2
    assert (result["nodes_unchecked"], result["models_unchecked"]) == (2, 2)
    assert (result["missing_nodes"], result["missing_models"]) == (0, 0)


def test_a_server_that_trickles_bytes_hits_the_deadline(monkeypatch):
    clock = iter(range(0, 10_000, 30))
    monkeypatch.setattr(comfyui_userdata.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(comfyui_userdata, "USERDATA_DEADLINE_S", 60.0)
    # Several 64 KiB chunks, so the clock is read between them.
    slow = _Response(200, {"nodes": ["x" * 400_000]})
    monkeypatch.setattr(requests, "get", lambda url, **_kw: slow)
    with pytest.raises(RuntimeError, match="took longer than 60 s"):
        read_saved_workflow(BASE, "a.json")


def test_a_shipped_workflow_is_told_apart_by_folder_not_by_name(comfy, folders, hub):
    user, builtin = folders
    # A user file shares the shipped workflow's NAME and holds something else.
    (builtin / "Shipped.json").write_text(json.dumps(PLAIN), encoding="utf-8")
    (user / "Shipped.json").write_text(json.dumps(NEEDS_PACK), encoding="utf-8")
    del comfy.workflows["Sub/Needs pack.json"]
    result = _pull(hub)
    assert (result["already_shipped"], result["matched"]) == (1, 0)


def test_models_on_a_node_comfyui_lacks_are_counted_unread():
    graph = {
        "1": {"class_type": "SomePackLoader", "inputs": {"model": "a.safetensors"}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    absent, unread = model_triage(graph, {"SaveImage": {}}, set())
    assert (absent, unread) == ([], 1)


def test_nothing_that_keys_a_workflow_reads_the_advisory_model_names():
    """Plan §3.4: recovered model names never enter a hash or a card key."""
    import pixlstash.hub.workflow_cards as cards_module
    import pixlstash.hub.workflows as hub_workflows_module
    import pixlstash.services.workflow_hash as hash_module
    import pixlstash.services.workflow_identity as identity_module

    for module in (hash_module, identity_module, hub_workflows_module, cards_module):
        source = Path(module.__file__).read_text("utf-8")
        for name in (
            "loaded_model_widgets",
            "count_model_file_values_ui",
            "model_triage",
            "comfyui_workflow_pull_task",
        ):
            assert name not in source, f"{module.__name__} reads {name}"


def test_both_formats_count_a_case_only_difference_as_missing():
    """The two branches of `model_triage` agree on case (#1502 review).

    ComfyUI compares file names exactly, so a model it lists only under a
    different case will not load. The API branch hears that from
    `_match_option` ("present under a different case", which the pre-flight
    files as missing); the editor branch compares exactly. Both must say
    missing, or one format would pass what the other refuses.
    """
    listed_as = "Flux-2-Klein-9B-FP8.safetensors"
    object_info = {
        "UNETLoader": {"input": {"required": {"unet_name": [[listed_as], {}]}}}
    }
    api = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": ABSENT_MODEL}},
    }
    api_absent, _unread = model_triage(api, object_info, {listed_as})
    advertised = {
        listed_as,
        "qwen_3_8b_fp8mixed.safetensors",
        "flux2-vae.safetensors",
    }
    ui_absent, _unread = model_triage(NEEDS_PACK, object_info, advertised)
    assert api_absent == ui_absent == [ABSENT_MODEL]


def _shelf_model(hub, filename: str, file_kind: str) -> int:
    with hub.transaction() as conn:
        return conn.execute(
            "INSERT INTO model (file_kind, filename, provenance) "
            "VALUES (?, ?, 'external')",
            (file_kind, filename),
        ).lastrowid


def test_a_pull_files_comfyuis_run_history_as_model_evidence(comfy, folders, hub):
    """#1518: which shelf models ran together, kept for when ComfyUI is off."""
    checkpoint = _shelf_model(hub, "ckpt.safetensors", "checkpoint")
    vae = _shelf_model(hub, "ae.safetensors", "vae")
    graph = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sub/ckpt.safetensors"},
        },
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "3": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["1", 0], "vae": ["2", 0]},
        },
    }
    comfy.history = {
        "run-1": {
            "prompt": [0, "run-1", graph, {}, ["3"]],
            "status": {"status_str": "success", "completed": True},
        }
    }

    result = _pull(hub)

    assert result["history_runs"] == 1
    assert any(url.startswith(f"{BASE}/history?max_items=") for url in comfy.requested)
    assert {
        (row["prompt_id"], int(row["model_id"]))
        for row in hub.fetchall("SELECT prompt_id, model_id FROM comfyui_history_model")
    } == {("run-1", checkpoint), ("run-1", vae)}


def test_a_comfyui_without_history_still_pulls_its_workflows(comfy, folders, hub):
    result = _pull(hub)

    assert result["history_runs"] is None
    assert result["pulled"] == 2


# ── versions in the hub ─────────────────────────────────────────────────────


def test_every_way_in_stores_version_1_and_a_delete_takes_the_versions(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "mine", PLAIN, "import")
    assert _versions(hub, workflow_id) == [(1, "import")]
    converted = {"1": {"class_type": "KSampler", "inputs": {}}}
    set_manual_api_document(hub, [workflow_id], converted)
    # The conversion belongs to the version it converts, and the card reads it.
    row = hub.fetchone(
        "SELECT api_document FROM workflow_version WHERE workflow_id = ?",
        (workflow_id,),
    )
    assert json.loads(row[0]) == converted
    delete_manual_workflow(hub, workflow_id)
    assert _versions(hub, workflow_id) == []


def test_a_new_version_of_a_workflow_an_older_build_made_keeps_its_first(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "old", PLAIN, "import")
    converted = {"1": {"class_type": "KSampler", "inputs": {}}}
    set_manual_api_document(hub, [workflow_id], converted)
    with hub.transaction() as conn:
        conn.execute("DELETE FROM workflow_version")
    edited = copy.deepcopy(PLAIN)
    edited["extra"] = {"edited": True}
    with hub.transaction() as conn:
        assert workflow_versions.append_version(conn, workflow_id, edited) == 2
    assert _versions(hub, workflow_id) == [(1, "import"), (2, "pull")]
    first = hub.fetchone(
        "SELECT document, api_document FROM workflow_version "
        "WHERE workflow_id = ? AND version = 1",
        (workflow_id,),
    )
    assert untagged(json.loads(first["document"])) == PLAIN
    assert json.loads(first["api_document"]) == converted
    # The card now reads version 2, with no conversion: that was of version 1.
    current = hub.fetchone(
        "SELECT document, api_document FROM workflow_document WHERE workflow_id = ?",
        (workflow_id,),
    )
    assert json.loads(current["document"])["extra"]["edited"] is True
    assert current["api_document"] is None
    assert tagged_workflow_id(manual_document(hub, workflow_id)) == workflow_id


def test_data_step_12_gives_every_manual_workflow_its_version_1(folders, hub, tmp_path):
    workflow_id = comfyui_module.store_manual_workflow(hub, "mine", PLAIN, "inbox")
    with hub.transaction() as conn:
        conn.execute("DELETE FROM workflow_version")
        conn.execute("PRAGMA user_version = 11")
    path = hub.path
    hub.close()
    reopened = HubDatabase(path)
    try:
        assert _versions(reopened, workflow_id) == [(1, "inbox")]
        row = reopened.fetchone(
            "SELECT content_hash, document FROM workflow_version WHERE workflow_id = ?",
            (workflow_id,),
        )
        assert row["content_hash"] == workflow_inbox.content_hash(PLAIN)
        assert json.loads(row["document"]) == manual_document(reopened, workflow_id)
    finally:
        reopened.close()


# ── the minute poll ─────────────────────────────────────────────────────────


class _Pulls:
    """`WorkflowPulls` as the finder sees it."""

    def __init__(self, wants: Optional[str] = BASE):
        self.wants = wants
        self.claimed: list[bool] = []
        self.released: list[object] = []
        self.busy = False

    def owner_wants_pulls(self):
        return self.wants

    def claim(self, comfyui_url, *, background=False):
        self.claimed.append(background)
        return ("running" if self.busy else "task"), not self.busy

    def release(self, task):
        self.released.append(task)


def test_the_poll_offers_a_background_pull_at_most_once_a_minute(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(base_task_finder.time, "monotonic", lambda: clock[0])
    pulls = _Pulls()
    finder = ComfyUIWorkflowPollFinder(pulls)
    assert finder.find_task() == "task"
    assert pulls.claimed == [True]
    clock[0] += 59
    assert finder.find_task() is None
    clock[0] += 1
    pulls.busy = True
    assert finder.find_task() is None  # one in flight: the gate refuses
    clock[0] += 60
    pulls.busy = False
    pulls.wants = None  # no address saved, or the setting is off
    assert finder.find_task() is None
    assert len(pulls.claimed) == 2


def test_a_failed_poll_waits_ten_minutes_and_a_dropped_one_frees_the_gate(
    monkeypatch,
):
    clock = [1000.0]
    monkeypatch.setattr(base_task_finder.time, "monotonic", lambda: clock[0])
    pulls = _Pulls()
    finder = ComfyUIWorkflowPollFinder(pulls)
    finder.find_task()
    finder.on_task_complete("task", RuntimeError("Could not reach ComfyUI"))
    clock[0] += 60
    assert finder.find_task() is None
    clock[0] += 540
    assert finder.find_task() == "task"
    finder.on_task_complete("task", None)
    clock[0] += 60
    assert finder.find_task() == "task"
    finder.on_task_complete("task", TaskCancelledError("dropped"))
    assert pulls.released == ["task"]


def test_the_pull_setting_reads_the_string_false_as_off():
    user = SimpleNamespace(pull_comfyui_workflows=True)
    for value, expected in [
        ("false", False),
        ("False", False),
        ("0", False),
        (0, False),
        (False, False),
        ("", True),
        (None, True),
        (True, True),
    ]:
        user.pull_comfyui_workflows = not expected
        apply_user_config_patch(user, {"pull_comfyui_workflows": value})
        assert user.pull_comfyui_workflows is expected, value


def test_the_owner_setting_and_a_saved_address_gate_the_automatic_pulls():
    user = SimpleNamespace(comfyui_url=f"{BASE}/", pull_comfyui_workflows=True)
    server = SimpleNamespace(auth=SimpleNamespace(user=user))
    pulls = WorkflowPulls(server, comfyui_module.store_pulled_workflow)
    assert pulls.owner_wants_pulls() == BASE
    user.pull_comfyui_workflows = False
    assert pulls.owner_wants_pulls() is None
    user.pull_comfyui_workflows = True
    user.comfyui_url = None
    assert pulls.owner_wants_pulls() is None
    user.comfyui_url = BASE
    pulls.automatic = False
    assert pulls.owner_wants_pulls() is None


def test_freshen_reads_a_file_only_when_comfyui_saved_it_since(comfy, folders, hub):
    _pull(hub)
    plain = _manual_id(hub, "Plain")
    server = SimpleNamespace(hub=hub, vault=None)
    pulls = WorkflowPulls(server, comfyui_module.store_pulled_workflow)
    comfy.requested.clear()
    assert pulls.freshen(BASE, plain) is None
    assert comfy.requested == [f"{BASE}/api/userdata?dir=workflows&full_info=true"]

    edited = copy.deepcopy(PLAIN)
    edited["extra"] = {"edited": True}
    comfy.edit("Plain.json", edited)
    assert pulls.freshen(BASE, plain) == 2
    assert manual_document(hub, plain)["extra"]["edited"] is True

    # A file in a subfolder is asked for by its folder.
    needs = _manual_id(hub, "Sub - Needs pack")
    comfy.requested.clear()
    assert pulls.freshen(BASE, needs) is None
    assert comfy.requested == [
        f"{BASE}/api/userdata?dir=workflows%2FSub&full_info=true"
    ]

    comfy.reachable = False
    assert pulls.freshen(BASE, plain) is None


# ── what the review of the versions and the poll asked for ──────────────────


def test_a_workflow_keeps_its_first_version_and_the_newest_49(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "w", PLAIN, "pull")
    for n in range(60):
        edited = copy.deepcopy(PLAIN)
        edited["extra"] = {"n": n}
        with hub.transaction() as conn:
            workflow_versions.append_version(conn, workflow_id, edited)
    kept = [version for version, _ in _versions(hub, workflow_id)]
    assert len(kept) == workflow_versions.MAX_VERSIONS == 50
    assert kept == [1, *range(13, 62)]
    assert manual_document(hub, workflow_id)["extra"]["n"] == 59
    # The card says which version is current, not how many are kept.
    (card,) = [
        card
        for card in workflow_card_reads._manual_cards(hub)
        if card.workflow_key == workflow_id
    ]
    assert (card.versions, card.version) == (50, 61)


def test_a_pull_stops_at_its_budget_and_the_next_takes_the_rest(
    comfy, folders, hub, monkeypatch, caplog
):
    monkeypatch.setattr(pull_task_module, "MAX_NEW_WORKFLOWS_PER_PULL", 1)
    with caplog.at_level("WARNING"):
        first = _pull_with(hub)
    assert (first["pulled"], first["budget_exhausted"]) == (1, "1 new workflows")
    assert "1 listed file(s) are left for the next pull" in caplog.text
    second = _pull_with(hub)
    assert (second["pulled"], second["unchanged"]) == (1, 1)
    assert second["budget_exhausted"] is None
    assert _stored(hub) == ["Plain", "Sub - Needs pack"]

    monkeypatch.setattr(pull_task_module, "MAX_NEW_VERSIONS_PER_PULL", 1)
    for path in ("Plain.json", "Sub/Needs pack.json"):
        edited = copy.deepcopy(comfy.workflows[path])
        edited["extra"] = {"edited": True}
        comfy.edit(path, edited)
    third = _pull_with(hub)
    assert (third["changed"], third["budget_exhausted"]) == (1, "1 new versions")
    assert _pull_with(hub)["changed"] == 1


def test_a_pull_stops_before_a_file_that_would_pass_the_byte_budget(
    comfy, folders, hub, monkeypatch
):
    # The fake lists every file at 10 bytes: the first stored (A.json) leaves
    # 5 bytes, too few for the next file's listed size.
    budget = len(json.dumps(NEEDS_PACK)) + 5
    monkeypatch.setattr(pull_task_module, "MAX_WRITTEN_BYTES_PER_PULL", budget)
    del comfy.workflows["Sub/Needs pack.json"]
    comfy.workflows["A.json"] = NEEDS_PACK
    comfy.workflows["B.json"] = PLAIN
    result = _pull_with(hub)
    assert result["pulled"] == 1
    assert result["budget_exhausted"] == f"{budget // (1024 * 1024)} MB written"


def test_a_poll_that_hit_its_budget_backs_off_like_a_failure(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(base_task_finder.time, "monotonic", lambda: clock[0])
    finder = ComfyUIWorkflowPollFinder(_Pulls())
    finder.find_task()
    finder.on_task_complete(
        SimpleNamespace(result={"budget_exhausted": "200 new versions"}), None
    )
    clock[0] += 60
    assert finder.find_task() is None
    clock[0] += 540
    assert finder.find_task() == "task"


@pytest.mark.parametrize("origin", ["inbox", "import", "clone"])
def test_a_comfyui_file_never_versions_a_card_the_owner_made(
    comfy, folders, hub, origin
):
    own = comfyui_module.store_manual_workflow(hub, "Mine", PLAIN, origin)
    if origin == "inbox":
        # As the inbox records it, so content matching can find it.
        workflow_origin.record_pulled(
            hub,
            workflow_origin.INBOX_ORIGIN,
            "h",
            own,
            None,
            workflow_inbox.content_hash(PLAIN),
        )
    comfy.workflows = {"Copy.json": copy.deepcopy(PLAIN)}
    first = _pull(hub)
    # A pulled workflow of its own, not a link to the owner's card.
    assert (first["pulled"], first["matched"]) == (1, 0)
    edited = copy.deepcopy(PLAIN)
    edited["extra"] = {"from comfyui": True}
    comfy.edit("Copy.json", edited)
    second = _pull(hub)
    assert second["changed"] == 1
    assert _versions(hub, own) == [(1, origin)]
    assert untagged(manual_document(hub, own)) == PLAIN
    assert workflow_origin.live_file(hub, BASE, own) is None


def test_a_link_an_earlier_build_made_to_an_owners_card_is_not_followed(
    comfy, folders, hub
):
    own = comfyui_module.store_manual_workflow(hub, "Mine", PLAIN, "import")
    workflow_origin.record_pulled(
        hub, BASE, "Copy.json", own, MODIFIED, workflow_inbox.content_hash(PLAIN)
    )
    assert workflow_origin.live_file(hub, BASE, own) is None
    edited = copy.deepcopy(PLAIN)
    edited["extra"] = {"from comfyui": True}
    comfy.workflows = {"Copy.json": PLAIN}
    comfy.edit("Copy.json", edited)
    assert _pull(hub)["pulled"] == 1
    assert _versions(hub, own) == [(1, "import")]


@pytest.mark.parametrize(
    "path, safe",
    [
        ("a.json", True),
        ("Sub/b.json", True),
        ("../../etc/x.json", False),
        ("Sub/../x.json", False),
        ("./x.json", False),
        ("Sub//x.json", False),
        ("/abs/y.json", False),
        ("C:/w/z.json", False),
        ("c:z.json", False),
        ("Sub\\x.json", False),
        ("x\x00.json", False),
        ("notes.txt", False),
    ],
)
def test_only_a_plain_relative_json_path_is_safe(path, safe):
    assert comfyui_userdata.is_safe_workflow_path(path) is safe


def test_an_unsafe_listed_path_is_never_stored_or_opened(comfy, folders, hub):
    comfy.workflows = {
        "../../etc/x.json": PLAIN,
        "/abs/y.json": NEEDS_PACK,
        "C:\\win\\z.json": PLAIN,
        "Fine.json": PLAIN,
    }
    result = _pull(hub)
    assert (result["listed"], result["pulled"]) == (1, 1)
    assert [
        r["remote_path"] for r in hub.fetchall("SELECT * FROM workflow_origin")
    ] == ["Fine.json"]
    # A row an earlier build stored is never answered as the file to open.
    fine = _manual_id(hub, "Fine")
    with hub.transaction() as conn:
        conn.execute("UPDATE workflow_origin SET remote_path = '../../etc/x.json'")
    assert workflow_origin.live_file(hub, BASE, fine) is None


def test_the_run_and_open_check_is_bounded_as_a_whole(folders, hub, monkeypatch):
    workflow_id = comfyui_module.store_manual_workflow(
        hub, "w", PLAIN, "pull", record=(BASE, "w.json", 1000, None)
    )
    monkeypatch.setattr(pull_comfyui_module, "FRESHEN_TIMEOUT_S", 0.6)

    def slow_listing(base_url, path, timeout_s):
        time.sleep(0.4)
        return comfyui_userdata.SavedWorkflow(path, 10, 2000)

    def slow_read(base_url, path, timeout_s=None):
        time.sleep(0.4)
        raise RuntimeError("too slow")

    monkeypatch.setattr(comfyui_userdata, "saved_workflow_info", slow_listing)
    monkeypatch.setattr(comfyui_userdata, "read_saved_workflow", slow_read)
    pulls = WorkflowPulls(
        SimpleNamespace(hub=hub, vault=None), comfyui_module.store_pulled_workflow
    )
    started = time.monotonic()
    assert pulls.freshen(BASE, workflow_id) is None
    assert time.monotonic() - started < 0.75
    time.sleep(0.3)  # let the abandoned check end before the hub closes


def test_a_conversion_is_not_stored_on_a_version_made_since_it_matched(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "w", PLAIN, "pull")
    canonical = workflow_bindings.canonical(
        workflow_bindings.migrate_placeholders(PLAIN)[0]
    )
    edited = copy.deepcopy(PLAIN)
    edited["extra"] = {"edited": True}
    with hub.transaction() as conn:
        workflow_versions.append_version(conn, workflow_id, edited)
    converted = {"1": {"class_type": "KSampler", "inputs": {}}}
    assert set_manual_api_document(hub, [workflow_id], converted, canonical) == []
    assert (
        hub.fetchone(
            "SELECT api_document FROM workflow_document WHERE workflow_id = ?",
            (workflow_id,),
        )[0]
        is None
    )
    # Matching the current version, it is stored.
    assert set_manual_api_document(
        hub,
        [workflow_id],
        converted,
        workflow_bindings.canonical(workflow_bindings.migrate_placeholders(edited)[0]),
    ) == [workflow_id]


def test_a_cached_description_follows_the_version(folders, hub):
    workflow_id = comfyui_module.store_manual_workflow(hub, "w", PLAIN, "pull")
    assert workflows_routes._manual_model_widgets(hub, workflow_id) == (
        workflows_routes._model_widgets_of(workflow_id, json.dumps(PLAIN))
    )
    with hub.transaction() as conn:
        workflow_versions.append_version(conn, workflow_id, NEEDS_PACK)
    names = {
        name for _w, name in workflows_routes._manual_model_widgets(hub, workflow_id)
    }
    assert ABSENT_MODEL in names
    # Keyed on the version: the same id with another version is read afresh,
    # and no document is held.
    first = workflow_card_reads._manual_facts_of(workflow_id, json.dumps(PLAIN), 1)
    assert workflow_card_reads._manual_facts_of(workflow_id, "{", 1) == first
    assert workflow_card_reads._manual_facts_of(workflow_id, "{", 2) == (None, None)
    assert workflow_card_reads._MANUAL_FACTS[workflow_id] == (2, (None, None))


def test_a_cached_description_is_not_handed_to_a_workflow_reusing_the_id(folders, hub):
    # A workflow made from a file reuses its id after a delete: the same id at
    # the same version number, stored at another time, is read afresh.
    workflow_id = comfyui_module.store_manual_workflow(hub, "w", PLAIN, "pull")
    first = workflows_routes._manual_model_widgets(hub, workflow_id)
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_document SET document = ?, created_at = ? "
            "WHERE workflow_id = ?",
            (json.dumps(NEEDS_PACK), "2099-01-01T00:00:00+00:00", workflow_id),
        )
        conn.execute(
            "UPDATE workflow_version SET created_at = ? WHERE workflow_id = ?",
            ("2099-01-01T00:00:00+00:00", workflow_id),
        )
    again = workflows_routes._manual_model_widgets(hub, workflow_id)
    assert again != first
    assert ABSENT_MODEL in {name for _w, name in again}

    # The grid's description of a workflow is dropped once it is gone.
    workflow_card_reads._manual_cards(hub)
    assert workflow_id in workflow_card_reads._MANUAL_FACTS
    delete_manual_workflow(hub, workflow_id)
    workflow_card_reads._manual_cards(hub)
    assert workflow_id not in workflow_card_reads._MANUAL_FACTS


def test_an_address_makes_no_new_workflow_past_its_card_cap(
    comfy, folders, hub, monkeypatch
):
    monkeypatch.setattr(workflow_origin, "MAX_PULL_CARDS_PER_ORIGIN", 2)
    _pull(hub)
    assert workflow_origin.live_pull_cards(hub, BASE) == 2
    comfy.workflows["New.json"] = {**copy.deepcopy(PLAIN), "extra": {"new": 1}}
    edited = copy.deepcopy(PLAIN)
    edited["extra"] = {"edited": True}
    comfy.edit("Plain.json", edited)
    result = _pull(hub)
    # The new file makes no card; the existing one still takes its version.
    assert (result["pulled"], result["changed"]) == (0, 1)
    assert result["card_cap_reached"] is True
    assert _stored(hub) == ["Plain", "Sub - Needs pack"]
    assert workflow_origin.live_pull_cards(hub, BASE) == 2
    assert _versions(hub, _manual_id(hub, "Plain"))[-1] == (2, "pull")

    # A deleted card frees its place, and the file left out comes in.
    delete_manual_workflow(hub, _manual_id(hub, "Sub - Needs pack"))
    again = _pull(hub)
    assert (again["pulled"], again["card_cap_reached"]) == (1, False)


def test_a_poll_that_met_the_card_cap_backs_off(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(base_task_finder.time, "monotonic", lambda: clock[0])
    finder = ComfyUIWorkflowPollFinder(_Pulls())
    finder.find_task()
    finder.on_task_complete(SimpleNamespace(result={"card_cap_reached": True}), None)
    clock[0] += 60
    assert finder.find_task() is None
    clock[0] += 540
    assert finder.find_task() == "task"
