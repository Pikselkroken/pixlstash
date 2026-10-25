"""Who may see the GPUs and act on the work waiting for them.

The ``resources`` snapshot rides ``GET /workers/progress``, which any token
reaches, so it is narrowed in the handler: the owner sees it, every other token
gets null. The lease controls are OWNER_ONLY; the GPU list and the GPU choice
are LOCAL_OWNER_ONLY (host hardware, host capability). Every refusal sits next
to the owner's positive control, and every path is checked to resolve, so a
renamed route cannot pass as a refusal.
"""

import json
import os
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pixlstash import gpu_select
from pixlstash.routes import config as config_routes
from pixlstash.resources.arbiter import LeaseKind, Tenant, arbiter
from pixlstash.server import Server
from tests.authz_guard import assert_real_route

API = "/api/v1"
REMOTE = {"X-Forwarded-For": "8.8.8.8"}
FAKE_GPUS = [
    {
        "uuid": "GPU-00000000-0000-0000-0000-0000000000a1",
        "name": "NVIDIA GeForce RTX 4090",
        "vendor": "nvidia",
        "total_mb": 24564,
        "free_mb": 12000,
        "integrated": False,
        "backend": "cu128",
    },
    {
        "uuid": "GPU-00000000-0000-0000-0000-0000000000a2",
        "name": "NVIDIA GeForce RTX 3060",
        "vendor": "nvidia",
        "total_mb": 12288,
        "free_mb": 12000,
        "integrated": False,
        "backend": "cu128",
    },
]


@pytest.fixture(scope="module")
def env():
    tmp = tempfile.TemporaryDirectory()
    config_path = os.path.join(tmp.name, "server-config.json")
    with open(config_path, "w") as handle:
        json.dump({"port": 8000, "trusted_proxies": ["testclient"]}, handle)
    server = Server(config_path)
    server.__enter__()
    try:
        owner = TestClient(server.api, raise_server_exceptions=True)
        r = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert r.status_code == 200, r.text
        yield SimpleNamespace(server=server, owner=owner, config_path=config_path)
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


@pytest.fixture
def tokens(env):
    """A whole-library READ token and a resource-scoped one, minted per test."""
    minted = {}
    for name, extra in (
        ("unscoped", {}),
        ("scoped", {"resource_type": "picture_set", "resource_id": 1}),
    ):
        r = env.owner.post(
            f"{API}/users/me/token",
            json={"description": f"test-{name}", "scope": "READ", **extra},
        )
        assert r.status_code == 200, r.text
        minted[name] = r.json()["token"]
    anon = TestClient(env.server.api, raise_server_exceptions=True)
    yield SimpleNamespace(
        anon=anon,
        headers={
            name: {"Authorization": f"Bearer {value}"} for name, value in minted.items()
        },
    )


@pytest.fixture
def fake_gpus(monkeypatch):
    monkeypatch.setattr(gpu_select, "inventory", lambda nvml=None: list(FAKE_GPUS))


def test_the_snapshot_is_the_owners_alone(env, tokens):
    path = f"{API}/workers/progress"
    assert_real_route(env.server.api, "GET", path, path)
    r = env.owner.get(path)
    assert r.status_code == 200, r.text
    resources = r.json()["resources"]
    assert set(resources) >= {"enforcing", "devices", "leases", "holders", "summary"}
    for name, headers in tokens.headers.items():
        r = tokens.anon.get(path, headers=headers)
        assert r.status_code == 200, (name, r.text)
        assert r.json()["resources"] is None, name


def test_a_lease_label_reaches_only_the_owner(env, tokens):
    """The label names a workflow: the scoped token must not read it."""
    lease = arbiter().request(
        Tenant.COMFYUI, "ComfyUI 'Secret workflow'", kind=LeaseKind.HEAVY
    )
    try:
        owner_view = env.owner.get(f"{API}/workers/progress").json()["resources"]
        assert any(
            row["label"] == "ComfyUI 'Secret workflow'" for row in owner_view["leases"]
        )
        for headers in tokens.headers.values():
            body = tokens.anon.get(f"{API}/workers/progress", headers=headers).text
            assert "Secret workflow" not in body
    finally:
        lease.release()


def test_a_model_download_reaches_only_the_owner(env, tokens, monkeypatch):
    """A download names a folder on this machine and its source host."""
    monkeypatch.setattr(
        config_routes,
        "live_downloads",
        lambda: [
            {"id": "dl-1", "folder_path": "/home/me/models", "host": "example.com"}
        ],
    )
    owner_view = env.owner.get(f"{API}/workers/progress").json()["downloads"]
    assert [d["id"] for d in owner_view] == ["dl-1"]
    for name, headers in tokens.headers.items():
        r = tokens.anon.get(f"{API}/workers/progress", headers=headers)
        assert r.status_code == 200, (name, r.text)
        assert r.json()["downloads"] == [], name
        assert "/home/me/models" not in r.text, name


@pytest.mark.parametrize(
    "path, template",
    [
        (
            f"{API}/resources/leases/lease-none/run-anyway",
            f"{API}/resources/leases/{{lease_id}}/run-anyway",
        ),
        (
            f"{API}/resources/leases/lease-none/cancel",
            f"{API}/resources/leases/{{lease_id}}/cancel",
        ),
        (f"{API}/resources/comfyui/free", f"{API}/resources/comfyui/free"),
    ],
)
def test_lease_controls_refuse_every_token_and_answer_the_owner(
    env, tokens, path, template
):
    assert_real_route(env.server.api, "POST", path, template)
    # The owner reaches the handler: an unknown lease, or no ComfyUI to free.
    r = env.owner.post(path)
    assert r.status_code in (404, 409, 503), r.text
    for name, headers in tokens.headers.items():
        r = tokens.anon.post(path, headers=headers)
        assert r.status_code == 403, (name, r.status_code, r.text)


def test_run_anyway_and_cancel_act_on_a_waiting_lease_only(env):
    blocker = arbiter().request(
        Tenant.AITOOLKIT, "Training", kind=LeaseKind.EXCLUSIVE, device="GPU-x"
    )
    waiting = arbiter().request(Tenant.COMFYUI, "Second", device="GPU-x")
    try:
        # The suite's arbiter sees no card, so a lease on an unknown one waits
        # only behind the exclusive holder.
        assert waiting.state.value == "waiting"
        r = env.owner.post(f"{API}/resources/leases/{blocker.id}/run-anyway")
        assert r.status_code == 409, r.text
        r = env.owner.post(f"{API}/resources/leases/{waiting.id}/run-anyway")
        assert r.status_code == 200, r.text
        assert r.json()["lease"]["state"] == "granted"
    finally:
        waiting.release()
        blocker.release()


def test_cancelling_a_held_run_succeeds_when_its_thread_drops_the_lease_first(
    env, monkeypatch
):
    """The held run's thread wakes on the cancel and releases the lease itself."""
    from pixlstash.routes import resources as resources_routes

    blocker = arbiter().request(
        Tenant.AITOOLKIT, "Training", kind=LeaseKind.EXCLUSIVE, device="GPU-x"
    )
    waiting = arbiter().request(
        Tenant.COMFYUI, "Held run", device="GPU-x", run_id="run-held"
    )

    def cancelled_and_released(run_id):
        waiting.release()
        return [run_id]

    monkeypatch.setattr(resources_routes, "cancel_held_runs", cancelled_and_released)
    try:
        assert waiting.state.value == "waiting"
        r = env.owner.post(f"{API}/resources/leases/{waiting.id}/cancel")
        assert r.status_code == 200, r.text
    finally:
        waiting.release()
        blocker.release()


def test_the_gpu_list_is_for_a_local_owner(env, tokens, fake_gpus):
    path = f"{API}/compute/gpus"
    assert_real_route(env.server.api, "GET", path, path)
    r = env.owner.get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [gpu["uuid"] for gpu in body["gpus"]] == [g["uuid"] for g in FAKE_GPUS]
    assert body["selected"] == "auto"
    # ComfyUI never seen: assumed on the first card, so Auto takes the 3060.
    assert "RTX 3060" in body["auto_reason"]
    r = env.owner.get(path, headers=REMOTE)
    assert r.status_code == 403, r.text
    for name, headers in tokens.headers.items():
        r = tokens.anon.get(path, headers=headers)
        assert r.status_code == 403, (name, r.text)


def test_choosing_a_gpu_is_stored_for_the_next_start(env, tokens, fake_gpus):
    path = f"{API}/compute/gpu"
    assert_real_route(env.server.api, "PATCH", path, path)
    chosen = FAKE_GPUS[0]["uuid"]
    try:
        r = env.owner.patch(path, json={"gpu": chosen})
        assert r.status_code == 200, r.text
        assert r.json() == {
            "status": "success",
            "selected": chosen,
            "restart_required": True,
        }
        with open(env.config_path) as handle:
            assert json.load(handle)[gpu_select.SETTING] == chosen
        assert env.owner.get(f"{API}/compute/gpus").json()["selected"] == chosen
        r = env.owner.patch(path, json={"gpu": "GPU-not-here"})
        assert r.status_code == 422, r.text
        r = env.owner.patch(path, json={"gpu": "auto"}, headers=REMOTE)
        assert r.status_code == 403, r.text
        for name, headers in tokens.headers.items():
            r = tokens.anon.patch(path, json={"gpu": "auto"}, headers=headers)
            assert r.status_code == 403, (name, r.text)
    finally:
        assert env.owner.patch(path, json={"gpu": "auto"}).status_code == 200


def test_the_run_alone_setting_rides_the_user_config(env):
    path = f"{API}/users/me/config"
    assert env.owner.get(path).json()["auto_exclusive_after_oom"] is True
    try:
        r = env.owner.patch(path, json={"auto_exclusive_after_oom": False})
        assert r.status_code == 200, r.text
        assert r.json()["config"]["auto_exclusive_after_oom"] is False
        assert env.owner.get(path).json()["auto_exclusive_after_oom"] is False
        r = env.owner.patch(path, json={"auto_exclusive_after_oom": "no"})
        assert r.status_code == 400, r.text
    finally:
        env.owner.patch(path, json={"auto_exclusive_after_oom": True})
