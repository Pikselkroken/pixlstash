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
from pixlstash.server import Server
from pixlstash.services import comfyui_userdata, workflow_inbox
from pixlstash.services.comfyui_userdata import (
    MultiUserComfyUIError,
    list_saved_workflows,
    read_saved_workflow,
    saved_workflow_url,
)
from pixlstash.services.workflow_hash import ui_topology_hash
from pixlstash.tasks import comfyui_workflow_pull_task as pull_task_module
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
        # `GET /history`; None answers 404, as a ComfyUI without the route.
        self.history: dict | None = None

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
        if url.startswith(f"{BASE}/history?") and self.history is not None:
            return _Response(200, self.history)
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
    result = _pull(hub)
    assert result["pulled"] == 1
    # It filed a recipe row of its own; that is not a picture.
    assert hub.fetchone("SELECT 1 FROM workflow_recipe") is not None
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
    # One router, and so one pull gate, for both routes: the gate lives in it.
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


def _pulled_files(hub) -> set[str]:
    return {
        row["workflow_name"]
        for row in hub.fetchall("SELECT workflow_name FROM workflow_pulled_file")
    }


def test_a_file_the_owner_already_had_stays_theirs_after_a_pull(comfy, folders, hub):
    """``workflow_pulled_file`` is what the one-off count reads (#1440).

    Written by the pull: pull-written, and a later pull matching its own file
    keeps it so. Matched to a file the owner had imported: not pull-written,
    so their file does not turn into a hideable one-off because ComfyUI
    happens to hold it too.
    """
    user, _builtin = folders
    (user / "Mine.json").write_text(json.dumps(PLAIN), encoding="utf-8")
    _pull(hub)
    assert _pulled_files(hub) == {"Sub - Needs pack.json"}
    _pull(hub)
    assert _pulled_files(hub) == {"Sub - Needs pack.json"}


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


def test_importing_a_pulled_workflow_by_hand_makes_it_the_owners(comfy, folders, hub):
    """Dropping in the same document a pull wrote is the owner keeping it."""
    _pull(hub)
    router = comfyui_module.create_router(MagicMock(hub=hub))
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/comfyui/workflows/import"
    )
    endpoint(
        SimpleNamespace(state=SimpleNamespace(origin_client_id=None)),
        {"name": "dropped", "workflow": NEEDS_PACK},
    )
    assert _pulled_files(hub) == {"Plain.json"}


def test_a_file_dropped_in_the_watched_folder_is_the_owners_too(comfy, folders, hub):
    """The inbox is the other hand-over path, and claims the same way."""
    _pull(hub)
    server = SimpleNamespace(hub=hub, vault=None)
    Server._store_inbox_workflow(server, "dropped", NEEDS_PACK)
    assert _pulled_files(hub) == {"Plain.json"}


# ── what the independent review of #1502 reproduced ─────────────────────────


def _pull_with(hub, store=None, origin=BASE) -> dict:
    task = ComfyUIWorkflowPullTask(
        hub,
        origin,
        store=store
        or (lambda name, doc: comfyui_module.store_pulled_workflow(hub, name, doc)),
        lock=workflow_inbox.INBOX_LOCK,
    )
    return task._run_task()


def _delete_pulled(hub, remote_path: str) -> str:
    name = hub.fetchone(
        "SELECT workflow_name FROM workflow_origin WHERE remote_path = ?",
        (remote_path,),
    )["workflow_name"]
    comfyui_module.trash_user_workflow(hub, name)
    return name


def test_a_delete_made_while_a_pull_runs_is_not_undone_by_it(comfy, folders, hub):
    user, _builtin = folders
    _pull(hub)
    # The owner deletes Plain.json between the pull's start and its entry.
    # The delete takes INBOX_LOCK like the pull does, so it runs from the
    # store wrapper of the entry BEFORE Plain.json - between entries, as a
    # real delete landing mid-pull would.
    real = comfyui_module.store_pulled_workflow
    deleted = []

    def store(name, doc):
        return real(hub, name, doc)

    order = sorted(comfy.workflows)  # the listing is sorted
    assert order == ["Plain.json", "Sub/Needs pack.json"]
    comfy.workflows = {"A first.json": NEEDS_PACK, **comfy.workflows}

    def store_then_delete(name, doc):
        outcome = store(name, doc)
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
    assert not (user / "Plain.json").exists()


def test_an_empty_listing_forgets_no_dismissal(comfy, folders, hub):
    user, _builtin = folders
    _pull(hub)
    _delete_pulled(hub, "Plain.json")
    listing = comfy.workflows
    comfy.workflows = {}
    assert _pull(hub)["gone"] == 0
    comfy.workflows = listing
    again = _pull(hub)
    assert (again["pulled"], again["skipped_dismissed"]) == (0, 1)
    assert not (user / "Plain.json").exists()


def test_the_same_comfyui_under_another_spelling_is_still_dismissed(
    comfy, folders, hub, monkeypatch
):
    user, _builtin = folders
    _pull(hub)
    _delete_pulled(hub, "Plain.json")
    alias = "http://127.0.0.1:8188"

    def via_alias(url, **kwargs):
        return comfy.get(url.replace(alias, BASE), **kwargs)

    monkeypatch.setattr(requests, "get", via_alias)
    result = _pull_with(hub, origin=alias)
    assert result["skipped_dismissed"] == 1
    assert not (user / "Plain.json").exists()


def test_a_deleted_workflow_renamed_in_comfyui_stays_out(comfy, folders, hub):
    user, _builtin = folders
    _pull(hub)
    _delete_pulled(hub, "Plain.json")
    comfy.workflows["Renamed.json"] = comfy.workflows.pop("Plain.json")
    result = _pull(hub)
    assert result["skipped_dismissed"] == 1
    assert not (user / "Renamed.json").exists()


def test_a_workflow_edited_in_comfyui_is_changed_and_both_copies_stay_pulled(
    comfy, folders, hub
):
    user, _builtin = folders
    _pull(hub)
    edited = copy.deepcopy(PLAIN)
    edited["nodes"][0]["pos"] = [123, 456]
    edited["extra"] = {"edited": True}
    comfy.workflows["Plain.json"] = edited
    result = _pull(hub)
    assert (result["changed"], result["pulled"]) == (1, 0)
    # The copy the first pull wrote is still pull-written, not an owner's file.
    assert {"Plain.json", "Plain (2).json"} <= _pulled_files(hub)


def test_a_workflow_gone_from_comfyui_stays_pull_written(comfy, folders, hub):
    _pull(hub)
    del comfy.workflows["Plain.json"]
    assert _pull(hub)["gone"] == 1
    assert "Plain.json" in _pulled_files(hub)


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
