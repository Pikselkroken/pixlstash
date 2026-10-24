"""Installing a known node pack through ComfyUI-Manager (#2054).

ComfyUI and its Manager are faked over ``requests``: no live HTTP. Every case
reads the calls the fake recorded, so a refusal is also proven to have queued
nothing, and the restart to have happened only on an empty ComfyUI queue.
"""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

from pixlstash.services import comfyui_link_service, node_pack_install_service
from pixlstash.services import node_packs
from pixlstash.services.workflow_run_service import MISSING_NODES, judge
from tests.network_vectors import LAN_IPV4

PACK = "comfyui-inpaint-cropandstitch"
URL = "http://127.0.0.1:18188/"


class _FakeManager:
    """A ComfyUI with a 3.x or 4.x Manager (or none), keyed ``"METHOD path"``.

    The pack shows up in Manager's installed list once its install is queued,
    unless ``installs`` is false.
    """

    def __init__(self, manager="v3", *, installed=None, installs=True, **override):
        self.calls: list[tuple[str, str, object]] = []
        self.installed = dict(installed or {})
        self.installs = installs
        self.queue = {"queue_running": [], "queue_pending": []}
        self.status = {"is_processing": False, "in_progress_count": 0}
        # ComfyUI does not offer the pack's nodes unless a test says it does.
        self.object_info = {}
        p = "v2/" if manager == "v4" else ""
        routes = {
            f"GET {p}customnode/installed": lambda: (200, self.installed),
            f"POST {p}manager/queue/start": 200,
            f"GET {p}manager/queue/status": lambda: (200, self.status),
            "GET queue": lambda: (200, self.queue),
            "GET object_info": lambda: (
                (200, self.object_info) if self.object_info is not None else 503
            ),
            f"POST {'v2/' if manager == 'v4' else ''}manager/reboot": 200,
        }
        if manager == "v4":
            routes["GET v2/manager/version"] = (200, "V4.2.2")
            routes["GET v2/manager/is_legacy_manager_ui"] = (
                200,
                {"is_legacy_manager_ui": False},
            )
            routes["POST v2/manager/queue/task"] = self._queue
        elif manager == "v3":
            routes["GET manager/version"] = (200, "V3.40")
            routes["POST manager/queue/install"] = self._queue
        routes.update(override)
        self.routes = routes

    def _queue(self):
        if self.installs:
            self.installed[PACK] = {"ver": "3.0.11", "cnr_id": PACK, "enabled": True}
        return 200

    def request(self, method, url, json=None, **kwargs):
        assert kwargs.get("proxies") == comfyui_link_service.NO_PROXY
        assert kwargs.get("allow_redirects") is False
        assert kwargs.get("timeout")
        path = urlsplit(url).path.lstrip("/")
        self.calls.append((method, path, json))
        answer = self.routes.get(f"{method} {path}", 404)
        if callable(answer):
            answer = answer()
        status, body = answer if isinstance(answer, tuple) else (answer, None)

        def as_json():
            if body is None or isinstance(body, str):
                raise ValueError("not JSON")
            return body

        return SimpleNamespace(
            status_code=status,
            json=as_json,
            text=body if isinstance(body, str) else "",
        )

    def asked(self, method, path):
        return [call for call in self.calls if call[:2] == (method, path)]

    @property
    def queued(self):
        return self.asked("POST", "manager/queue/install") + self.asked(
            "POST", "v2/manager/queue/task"
        )


def _timed_out():
    raise requests.Timeout("read timed out")


@pytest.fixture
def comfy(monkeypatch):
    def at(manager="v3", **kwargs):
        fake = _FakeManager(manager, **kwargs)
        monkeypatch.setattr(node_pack_install_service.requests, "request", fake.request)
        return fake

    monkeypatch.setattr(node_pack_install_service.time, "sleep", lambda s: None)
    return at


def test_manager_3_installs_the_pinned_version_and_restarts(comfy):
    fake = comfy("v3")
    result = node_pack_install_service.install(URL, PACK)
    assert result == {
        "pack": PACK,
        "version": "3.0.11",
        "manager": "v3",
        "restart": "requested",
        "detail": None,
    }
    [(_m, _p, body)] = fake.queued
    assert body["id"] == PACK
    assert body["selected_version"] == body["version"] == "3.0.11"
    assert (body["channel"], body["mode"]) == ("default", "remote")
    assert fake.asked("POST", "manager/queue/start")
    assert fake.asked("POST", "manager/reboot")


def test_manager_4_installs_through_its_task_queue(comfy):
    fake = comfy("v4")
    result = node_pack_install_service.install(URL, PACK)
    assert (result["manager"], result["restart"]) == ("v4", "requested")
    [(_m, _p, body)] = fake.queued
    assert body["kind"] == "install"
    assert body["ui_id"] and body["client_id"]
    assert body["params"]["id"] == PACK
    assert body["params"]["selected_version"] == "3.0.11"
    assert fake.asked("POST", "v2/manager/queue/start")[0][2] == {}, (
        "Manager refuses a form post to queue/start, so the body is JSON"
    )
    assert fake.asked("POST", "v2/manager/reboot")


def test_a_comfyui_on_the_local_network_installs_through_its_own_manager(comfy):
    fake = comfy("v3")
    result = node_pack_install_service.install(f"http://{LAN_IPV4}:8188/", PACK)
    assert result["restart"] == "requested"
    assert fake.queued


@pytest.mark.parametrize(
    "manager, override, said",
    [
        ("none", {}, "ComfyUI-Manager is not installed"),
        (
            "v4",
            {
                "GET v2/manager/is_legacy_manager_ui": (
                    200,
                    {"is_legacy_manager_ui": True},
                )
            },
            "legacy mode",
        ),
        # Not knowing the answer is refused like a yes, never read as a no.
        ("v4", {"GET v2/manager/is_legacy_manager_ui": _timed_out}, "legacy mode"),
        ("v4", {"GET v2/manager/is_legacy_manager_ui": 404}, "legacy mode"),
        (
            "v4",
            {"GET v2/manager/is_legacy_manager_ui": (200, {"other": False})},
            "legacy mode",
        ),
        ("v3", {"POST manager/queue/install": 403}, "security level"),
        (
            "v3",
            {"POST manager/queue/install": (404, "A security error has occurred")},
            "security level",
        ),
    ],
)
def test_a_manager_that_cannot_install_is_refused_and_queues_nothing(
    comfy, manager, override, said
):
    fake = comfy(manager, **override)
    with pytest.raises(node_pack_install_service.PackInstallRefused) as refused:
        node_pack_install_service.install(URL, PACK)
    assert said in str(refused.value)
    assert not fake.asked("POST", "manager/queue/start")
    assert not fake.asked("POST", "v2/manager/queue/start")
    assert not fake.asked("POST", "manager/reboot")
    if manager != "v3":
        assert fake.queued == [], "no payload goes to a Manager PixlStash cannot read"


@pytest.mark.parametrize("folder", [PACK, "ComfyUI-Inpaint-CropAndStitch"])
def test_a_pack_manager_already_lists_is_never_touched(comfy, folder):
    """Disabled, failed to import, another version: the owner's install."""
    fake = comfy(
        "v3", installed={folder: {"ver": "abc123", "cnr_id": "", "enabled": False}}
    )
    with pytest.raises(node_pack_install_service.PackInstallRefused) as refused:
        node_pack_install_service.install(URL, PACK)
    assert "installed but not loading" in str(refused.value)
    assert fake.queued == []


def test_a_pack_already_loading_is_said_to_be_so(comfy):
    fake = comfy("v3", installed={PACK: {"ver": "3.0.11", "cnr_id": PACK}})
    fake.object_info = {"InpaintCropImproved": {}, "InpaintStitchImproved": {}}
    with pytest.raises(node_pack_install_service.PackInstallRefused) as refused:
        node_pack_install_service.install(URL, PACK)
    assert "already installed and loaded" in str(refused.value)
    assert fake.queued == []


def test_a_busy_comfyui_is_never_restarted(comfy):
    fake = comfy("v3")
    fake.queue["queue_running"] = [[0, "a-running-prompt"]]
    result = node_pack_install_service.install(URL, PACK)
    assert result["restart"] == "manual"
    assert "busy" in result["detail"]
    assert fake.queued, "the install itself still happens"
    assert not [c for c in fake.calls if c[1].endswith("manager/reboot")]


def test_a_pending_prompt_also_holds_the_restart(comfy):
    fake = comfy("v4")
    fake.queue["queue_pending"] = [[1, "a-queued-prompt"]]
    result = node_pack_install_service.install(URL, PACK)
    assert result["restart"] == "manual"
    assert not [c for c in fake.calls if c[1].endswith("manager/reboot")]


def test_a_manager_that_finishes_without_the_pack_is_reported(comfy):
    fake = comfy("v3", installs=False)
    with pytest.raises(node_pack_install_service.PackInstallRefused) as refused:
        node_pack_install_service.install(URL, PACK)
    assert "did not install" in str(refused.value)
    assert not fake.asked("POST", "manager/reboot")


def test_a_manager_still_installing_at_the_ceiling_is_left_to_finish(
    comfy, monkeypatch
):
    fake = comfy("v3")
    fake.status = {"is_processing": True, "in_progress_count": 1}
    clock = iter(range(0, 10_000, 60))
    monkeypatch.setattr(
        node_pack_install_service.time, "monotonic", lambda: next(clock)
    )
    with pytest.raises(node_pack_install_service.PackInstallRefused) as refused:
        node_pack_install_service.install(URL, PACK)
    assert "still installing" in str(refused.value)
    assert not fake.asked("POST", "manager/reboot")


def test_a_queue_status_manager_never_answers_is_not_read_as_busy(comfy, monkeypatch):
    fake = comfy("v3", **{"GET manager/queue/status": 404})
    clock = iter(range(0, 10_000, 60))
    monkeypatch.setattr(
        node_pack_install_service.time, "monotonic", lambda: next(clock)
    )
    with pytest.raises(node_pack_install_service.PackInstallRefused) as refused:
        node_pack_install_service.install(URL, PACK)
    assert "could not read ComfyUI-Manager's queue" in str(refused.value)
    assert "still installing" not in str(refused.value)
    assert not fake.asked("POST", "manager/reboot")


@pytest.mark.parametrize(
    "pack_id",
    [
        "unknown-pack",
        f"{PACK}@nightly",
        f"{PACK}@3.0.20",
        f"../{PACK}",
        PACK.upper(),
        "",
    ],
)
def test_only_a_listed_id_installs_and_nothing_else_reaches_comfyui(comfy, pack_id):
    fake = comfy("v3")
    with pytest.raises(node_pack_install_service.UnknownPack):
        node_pack_install_service.install(URL, pack_id)
    with pytest.raises(node_pack_install_service.UnknownPack):
        node_pack_install_service.loaded(URL, pack_id)
    assert fake.calls == []


def test_loaded_needs_every_class_and_reads_unreachable_as_not_yet(comfy):
    fake = comfy("v3")
    fake.object_info = {"InpaintCropImproved": {}}
    assert node_pack_install_service.loaded(URL, PACK) is False
    fake.object_info["InpaintStitchImproved"] = {}
    assert node_pack_install_service.loaded(URL, PACK) is True
    fake.object_info = None
    assert node_pack_install_service.loaded(URL, PACK) is False
    # Pinned, proxy-free and redirect-free like every install call: the fake
    # asserts all three on each request it answers.
    assert fake.asked("GET", "object_info")


def test_the_refusal_names_the_pack_that_provides_the_missing_nodes():
    graph = {
        "1": {"class_type": "InpaintCropImproved", "inputs": {}},
        "2": {"class_type": "SomeNodeNobodyShips", "inputs": {}},
    }
    reasons, _preflight = judge(graph, {}, None)
    [reason] = [r for r in reasons if r.code == MISSING_NODES]
    assert reason.detail["packs"] == [
        {
            "id": PACK,
            "name": "ComfyUI-Inpaint-CropAndStitch",
            "version": "3.0.11",
            "nodes": ["InpaintCropImproved"],
        }
    ]
    reasons, _preflight = judge(
        {"2": {"class_type": "SomeNodeNobodyShips", "inputs": {}}}, {}, None
    )
    [reason] = [r for r in reasons if r.code == MISSING_NODES]
    assert "packs" not in reason.detail
    assert reason.detail["nodes"] == ["SomeNodeNobodyShips"]


def test_every_pin_is_a_release_and_not_a_moving_target():
    for known in node_packs.KNOWN_PACKS:
        assert known.version[0].isdigit(), known
        assert known.classes, known
