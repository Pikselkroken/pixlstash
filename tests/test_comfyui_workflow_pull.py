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
from pixlstash.tasks.comfyui_workflow_pull_task import (
    ComfyUIWorkflowPullTask,
    missing_node_classes,
    stored_name_for,
)

BASE = "http://comfy.test:8188"
FIXTURES = Path(__file__).parent / "comfyui_workflows"

# An editor-format workflow with a subgraph, and one using a class from a node
# pack (`LoRACharacterPromptBuilder`) the fake ComfyUI below does not have.
PLAIN = json.loads((FIXTURES / "image_z_image_turbo.json").read_text("utf-8"))
NEEDS_PACK = json.loads((FIXTURES / "image_flux2_klein_t2i.json").read_text("utf-8"))
ABSENT_CLASS = "LoRACharacterPromptBuilder"


class _Response:
    def __init__(self, status_code: int, payload=None, *, text: str | None = None):
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)
        self.content = self.text.encode("utf-8")

    def json(self):
        return json.loads(self.text)


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

    def get(self, url, timeout=None):
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
            return _Response(
                200, {name: {} for name in self.classes if name != ABSENT_CLASS}
            )
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
    monkeypatch.setattr(requests, "get", lambda url, timeout: _Response(200, payload))
    listed = list_saved_workflows(BASE)
    assert [(e.path, e.size, e.modified_ms) for e in listed] == [
        ("Sub/c.json", 7, 1),
        ("a.json", None, None),
        ("b.json", 5, 1780844045116),
    ]


def test_no_workflows_folder_is_an_empty_listing(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout: _Response(404, text=""))
    assert list_saved_workflows(BASE) == []


@pytest.mark.parametrize(
    "response",
    [_Response(500, text="boom"), _Response(200, {"not": "a list"}), None],
    ids=["non-200", "not-a-list", "unreachable"],
)
def test_a_bad_listing_raises_runtime_error(monkeypatch, response):
    def get(url, timeout):
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
    monkeypatch.setattr(requests, "get", lambda url, timeout: response)
    with pytest.raises(RuntimeError):
        read_saved_workflow(BASE, "a.json")


def test_multi_user_comfyui_is_refused_and_single_user_is_not(monkeypatch):
    answers = {
        "multi": _Response(200, {"storage": "server", "users": {}}),
        "single": _Response(200, {"storage": "server", "migrated": True}),
        "old": _Response(404, text=""),
    }
    for kind, response in answers.items():
        monkeypatch.setattr(requests, "get", lambda url, timeout, r=response: r)
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
        ("trailing. .json", "trailing.json"),
        ("../.json", "workflow.json"),
    ],
)
def test_a_remote_path_is_stored_as_one_flat_safe_name(remote, stored):
    assert stored_name_for(remote) == stored


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


def test_without_object_info_every_workflow_is_unchecked_never_fine(
    comfy, folders, hub
):
    comfy.object_info_up = False
    result = _pull(hub)
    assert result["pulled"] == 2
    assert result["nodes_checked"] is False
    assert result["missing_nodes"] == 0
    assert result["nodes_unchecked"] == 2


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
