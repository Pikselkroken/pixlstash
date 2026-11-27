"""A second training plugin runs a job through the same routes (#2453).

:class:`~tests.training.fake_trainer_plugin.FakeTrainer` implements only the
required members of :class:`~pixlstash.training.backend.TrainerBackend`. Once
registered, its recipe is offered, a job of it trains, lands on the shelf, is
drawn by the live view and can be cancelled, all through the routes ai-toolkit
uses, with no ai-toolkit set up at all. The unit tests below pin the restart
paths: a run is re-attached by its own trainer, a run whose trainer is gone is
never left running, and an ai-toolkit run from before the column keeps its
paths.
"""

import gc
import json
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from pixlstash.hub import training_runs
from pixlstash.server import Server
from pixlstash.services.training_service import TrainingService, handle_for_run
from pixlstash.training import presets, registry
from pixlstash.utils.service.export_utils import ExportUtils
from tests.training.fake_trainer_plugin import RECIPE_ID, FakeTrainer, write_recipe
from tests.training.test_training_service import _StubLease

_ENV: dict = {}
_COUNT_TRAINING_PICTURES = ExportUtils.count_training_pictures


def _dataset_builder(*, dest_dir, **_):
    """One picture's worth of dataset; the fake trainer never reads it."""
    os.makedirs(dest_dir, exist_ok=True)
    return {"dest_dir": dest_dir, "image_count": 1, "picture_ids": [], "items": []}


@pytest.fixture(scope="module")
def env():
    """One Server, the fake trainer registered, and a real TrainingService
    whose worker runs, granted the GPU at once."""
    root = tempfile.TemporaryDirectory()
    config = os.path.join(root.name, "server-config.json")
    with open(config, "w") as fh:
        json.dump({"port": 8000, "disable_background_workers": True}, fh)
    server = Server(config)
    client = TestClient(server.api, raise_server_exceptions=True)
    r = client.post(
        "/login", json={"username": "owner", "password": "example-ownerpass1"}
    )
    assert r.status_code == 200, r.text

    class Fake(FakeTrainer):
        presets_dir = os.path.join(root.name, "fake-presets")
        template_dir = os.path.join(root.name, "fake-templates")

    write_recipe(
        Fake.presets_dir, Fake.template_dir, presets.get_preset("zimage-turbo")
    )
    registry.register(Fake)
    vault = server.vault
    lease = _StubLease()
    service = TrainingService(
        vault.hub,
        vault.library_uuid,
        vault.image_root,
        request_gpu=lease.request,
        import_run=vault._import_training_run,
        notify=lambda *args: None,
        config_provider=vault.training_config,
        dataset_builder=_dataset_builder,
        poll_interval_s=0.05,
    )
    real_service = vault.training_service
    vault.training_service = service
    vault.training_room = lambda: {"vram_mb": 22_800, "ram_mb": 60_000}
    ExportUtils.count_training_pictures = staticmethod(lambda server, **kw: 24)
    service.start()
    r = client.post("/picture_sets", json={"name": "Fake set"})
    assert r.status_code == 200, r.text
    _ENV.update(
        server=server,
        client=client,
        service=service,
        set_id=r.json()["picture_set"]["id"],
    )
    try:
        yield _ENV
    finally:
        service.stop()
        ExportUtils.count_training_pictures = staticmethod(_COUNT_TRAINING_PICTURES)
        vars(vault).pop("training_room", None)
        vault.training_service = real_service
        registry.unregister(FakeTrainer.name)
        server.close()
        root.cleanup()
        _ENV.clear()
        gc.collect()


def _create(env, name: str) -> dict:
    r = env["client"].post(
        "/training/jobs",
        json={
            "set_id": env["set_id"],
            "name": name,
            "trigger_word": "fakeword",
            "preset": RECIPE_ID,
            "subject": "thing",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _wait_for(env, job_id: int, statuses: set, timeout: float = 30.0) -> dict:
    """Poll until the job reaches one of *statuses*.

    A ``completed`` row with no ``completed_at`` is still importing its
    checkpoints, so it has not arrived at ``completed`` yet.
    """
    deadline = time.monotonic() + timeout
    while True:
        r = env["client"].get(f"/training/jobs/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        importing = body["status"] == "completed" and not body["completed_at"]
        if (body["status"] in statuses and not importing) or (
            time.monotonic() > deadline
        ):
            return body
        time.sleep(0.05)


def test_the_fake_recipe_is_offered_with_no_trainer_set_up(env):
    r = env["client"].get("/training/presets")
    assert r.status_code == 200, r.text
    items = {item["id"]: item for item in r.json()}
    assert items[RECIPE_ID]["offered"] is True
    # The owner set nothing up for it: its settings are empty.
    assert env["server"].vault.training_config(FakeTrainer.name) == {}


def test_a_fake_run_trains_lands_on_the_shelf_and_refuses_train_longer(env):
    job = _create(env, "fake run")
    status = _wait_for(env, job["id"], {"completed", "error", "stopped"})
    assert status["status"] == "completed", status
    assert status["completed_at"], f"the run never finished importing: {status}"
    run = training_runs.get_run(
        env["server"].vault.hub, env["server"].vault.library_uuid, job["id"]
    )
    assert run["trainer"] == FakeTrainer.name
    assert run["error"] is None
    landed = env["server"].vault.hub.fetchall(
        "SELECT id FROM model WHERE training_run_id = ?", (job["id"],)
    )
    assert landed, "the fake run's checkpoints did not reach the shelf"

    r = env["client"].get(f"/training/jobs/{job['id']}/live")
    assert r.status_code == 200, r.text
    assert "phase" in r.json()

    r = env["client"].get(f"/training/jobs/{job['id']}/train-longer")
    assert r.status_code == 200, r.text
    assert r.json()["steps"] == []
    assert "cannot train a run on" in r.json()["reason"]
    r = env["client"].post(
        f"/training/jobs/{job['id']}/train-longer",
        json={"from_step": 2, "more_steps": 2},
    )
    assert r.status_code == 409, r.text


def test_a_running_fake_run_is_cancelled_through_the_route(env):
    FakeTrainer.run_seconds = 30.0
    try:
        job = _create(env, "fake cancel")
        running = _wait_for(env, job["id"], {"running"})
        assert running["status"] == "running", running
        live = env["client"].get(f"/training/jobs/{job['id']}/live")
        assert live.status_code == 200, live.text
        # The fake writes no status line: the default phase reads the step.
        assert live.json()["phase"] == "loading"
        r = env["client"].post(f"/training/jobs/{job['id']}/cancel")
        assert r.status_code == 200, r.text
        ended = _wait_for(env, job["id"], {"stopped", "error", "completed"})
        assert ended["status"] == "stopped", ended
    finally:
        FakeTrainer.run_seconds = 0.3


def test_a_run_whose_trainer_is_gone_is_refused_not_a_500(env):
    """The runs list still lists it; its own views say the plugin is missing."""
    hub = env["server"].vault.hub
    job_uuid = "beef000000000000"
    run_id = training_runs.create_run(
        hub,
        library_uuid=env["server"].vault.library_uuid,
        uuid=job_uuid,
        name="orphaned run",
        set_id=env["set_id"],
        character_id=None,
        base_model="m",
        trigger_word="t",
        trainer="gone-trainer",
    )
    training_runs.update_run(
        hub,
        job_uuid,
        status="completed",
        completed_at="2026-11-18T09:00:00+00:00",
        training_folder=os.path.join(tempfile.gettempdir(), "no-such-runs"),
    )
    client = env["client"]

    r = client.get("/training/jobs")
    assert r.status_code == 200, r.text
    assert run_id in {job["id"] for job in r.json()}

    r = client.get(f"/training/jobs/{run_id}/live")
    assert r.status_code == 409, r.text
    assert "training plugin 'gone-trainer' is not installed" in r.json()["detail"]

    r = client.get(f"/training/jobs/{run_id}/train-longer")
    assert r.status_code == 200, r.text
    assert r.json()["steps"] == []
    assert "training plugin 'gone-trainer' is not installed" in r.json()["reason"]


def test_a_plugin_without_its_recipe_folder_leaves_the_others(tmp_path):
    class Missing(FakeTrainer):
        name = "missing-folder"
        presets_dir = str(tmp_path / "not-there")

    registry.register(Missing)
    try:
        ids = {preset["id"] for preset in presets.list_presets()}
    finally:
        registry.unregister(Missing.name)
    assert "zimage-turbo" in ids


def test_every_shipped_recipe_file_loads():
    """A broken shipped recipe is skipped at run time, so it must fail here."""
    files = {
        name[: -len(".json")]
        for name in os.listdir(presets.PRESET_DIR)
        if name.endswith(".json")
    }
    assert files
    assert files <= set(presets.shipped_presets())


def test_one_broken_plugin_recipe_leaves_the_rest(tmp_path):
    class Broken(FakeTrainer):
        name = "broken-recipe"
        presets_dir = str(tmp_path)

    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "half.json").write_text('{"id": "half"}')
    registry.register(Broken)
    try:
        ids = {preset["id"] for preset in presets.list_presets()}
    finally:
        registry.unregister(Broken.name)
    assert "zimage-turbo" in ids and "half" not in ids


def test_a_recipe_id_shipped_twice_keeps_the_first_plugins(tmp_path):
    shipped = presets.get_preset("zimage-turbo")

    class Twin(FakeTrainer):
        name = "twin-trainer"
        presets_dir = str(tmp_path / "presets")

    os.makedirs(Twin.presets_dir)
    with open(os.path.join(Twin.presets_dir, "zimage-turbo.json"), "w") as fh:
        json.dump({k: v for k, v in shipped.items() if k != "trainer"}, fh)
    registry.register(Twin)
    try:
        assert presets.get_preset("zimage-turbo")["trainer"] == "ai-toolkit"
    finally:
        registry.unregister(Twin.name)


# ---------------------------------------------------------------------------
# Restart paths, without a server
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_registered(tmp_path):
    registry.register(FakeTrainer)
    try:
        yield
    finally:
        registry.unregister(FakeTrainer.name)


def _service(tmp_path, monkeypatch, row: dict, backends: list) -> TrainingService:
    """A service whose hub is *row* alone, recording what it builds."""
    monkeypatch.setattr(training_runs, "get_run_by_uuid", lambda hub, uuid: dict(row))
    updates: list = []
    monkeypatch.setattr(
        training_runs,
        "update_run",
        lambda hub, uuid, **fields: updates.append(fields) or row.update(fields) or 1,
    )

    def factory(name, cfg):
        backend = registry.get(name).from_owner_config(cfg)
        backends.append(backend)
        return backend

    service = TrainingService(
        object(),
        "lib",
        str(tmp_path),
        request_gpu=None,
        import_run=None,
        notify=lambda *args: None,
        config_provider=lambda *_: {},
        dataset_builder=None,
        backend_factory=factory,
    )
    service.updates = updates
    return service


def test_a_reattached_fake_run_is_cancelled_with_its_own_trainer(
    tmp_path, monkeypatch, fake_registered
):
    row = {
        "id": 1,
        "uuid": "feedface00000000",
        "name": "survivor",
        "status": "running",
        "pid": None,
        "training_folder": None,
        "trainer": FakeTrainer.name,
    }
    backends: list = []
    service = _service(tmp_path, monkeypatch, row, backends)
    calls = []
    monkeypatch.setattr(
        FakeTrainer, "cancel", lambda self, handle: calls.append(handle)
    )
    monkeypatch.setattr(training_runs, "get_run", lambda hub, lib, run_id: dict(row))

    assert service.cancel(1) is True

    assert [type(b) for b in backends] == [FakeTrainer]
    (handle,) = calls
    # The fake's own layout, not ai-toolkit's <work_dir>/output/<name>.
    assert os.path.basename(handle.save_root) == "fake_feedface"


def test_a_run_whose_trainer_is_not_installed_ends_as_error(tmp_path, monkeypatch):
    row = {
        "id": 1,
        "uuid": "deadbeef00000000",
        "name": "orphan",
        "status": "running",
        "pid": 4242,
        "training_folder": str(tmp_path / "out"),
        "trainer": "gone-trainer",
    }
    service = _service(tmp_path, monkeypatch, row, [])

    service._reconcile_one(row)

    assert row["status"] == "error"
    assert "training plugin 'gone-trainer' is not installed" in row["error"]


def test_a_queued_run_whose_trainer_is_not_installed_fails_and_frees_the_queue(
    tmp_path, monkeypatch
):
    """Left queued, it would be claimed first on every poll, ahead of the rest."""
    row = {
        "id": 1,
        "uuid": "cafe000000000000",
        "name": "queued orphan",
        "status": "queued",
        "pid": None,
        "training_folder": None,
        "resolved_profile": json.dumps({"vram_mb": 1, "ram_mb": 1}),
        "trainer": "gone-trainer",
    }
    service = _service(tmp_path, monkeypatch, row, [])

    service._run_job(row)

    assert row["status"] == "error"
    assert row["error"] == "training plugin 'gone-trainer' is not installed"


def test_a_run_from_before_trainers_keeps_its_ai_toolkit_paths():
    """A live run started before the trainer column (trainer NULL) is found
    again at the paths ai-toolkit gave it, or a restart marks it interrupted.

    The expected paths are literals, as the code before the move produced them.
    """
    root = os.path.abspath(os.path.join(os.sep, "home", "me", "training"))
    shelf = os.path.abspath(os.path.join(os.sep, "home", "me", "aitk", "output"))
    work = os.path.join(root, "0123456789abcdef")
    info = {
        "uuid": "0123456789abcdef",
        "name": "My LoRA!",
        "pid": 4242,
        "training_folder": shelf,
        "trainer": None,
    }

    handle = handle_for_run(root, info)

    assert vars(handle) == {
        "job_id": "0123456789abcdef",
        "work_dir": work,
        "training_folder": shelf,
        "save_root": os.path.join(shelf, "My_LoRA_01234567"),
        "sqlite_db_path": os.path.join(work, "job.db"),
        "loss_db_path": os.path.join(shelf, "My_LoRA_01234567", "loss_log.db"),
        "config_path": os.path.join(work, ".job_config.json"),
        "log_path": os.path.join(shelf, "My_LoRA_01234567", "log.txt"),
        "pid": 4242,
    }
    # A run from before the shelf took runs keeps its work-dir layout.
    legacy = handle_for_run(root, {**info, "training_folder": None})
    assert legacy.save_root == os.path.join(work, "output", "My_LoRA")
