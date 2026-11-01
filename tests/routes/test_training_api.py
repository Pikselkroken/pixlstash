"""Security + functional tests for the ai-toolkit training HTTP API.

Every endpoint is asserted in **both directions**:
  * an owner (cookie) session → works (200 / expected status), and
  * a scoped/share READ token → 403 (the deny-by-default owner policies the
    authz gate declares for these routes).

The :class:`~pixlstash.services.training_service.TrainingService` is stubbed
with a fake on ``server.vault.training_service`` for the enqueue/cancel paths so
no GPU subprocess is ever spawned; read/download/delete paths seed
training-run rows (and real work-directory files) directly in the hub.

See the training rows of ``docs/authz-coverage-matrix.md`` for the coverage matrix
this file backs.
"""

import gc
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import types
import zipfile

import psutil
import pytest
from fastapi.testclient import TestClient

from pixlstash.hub import dataset_snapshots, training_runs
from pixlstash.resources.arbiter import Tenant, arbiter
from pixlstash.routes import training as training_routes
from pixlstash.routes.training import _redact_host_paths
from pixlstash.training import preset_updates, presets
from pixlstash.server import Server
from pixlstash.training.local_ai_toolkit import LocalAiToolkit
from pixlstash.utils.service.export_utils import ExportUtils


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

# What the arbiter gives a run on a 24 GB and a 16 GB card: the card less the
# safety margin and a desktop's few hundred MB.
_ROOM_24GB = {"vram_mb": 22_800, "ram_mb": 60_000}
_ROOM_16GB = {"vram_mb": 14_900, "ram_mb": 60_000}
_PICTURES = {"count": 24}
_COUNT_TRAINING_PICTURES = ExportUtils.count_training_pictures
_TRAINING_COUNTS = ExportUtils.training_counts


class _FakeTrainingService:
    """Records enqueue/cancel calls so tests need no GPU/subprocess.

    ``enqueue`` mirrors the real service's set/character invariant (raises
    ``ValueError`` when neither is given) and writes a real hub
    ``training_run`` row so the create response is realistic.
    """

    def __init__(self, hub, library_uuid):
        self._hub = hub
        self._library_uuid = library_uuid
        self.enqueue_calls = []
        self.cancel_calls = []
        self.cancel_result = True

    def enqueue(
        self,
        *,
        set_id=None,
        character_id=None,
        name,
        base_model,
        trigger_word,
        template_path,
        config_overrides=None,
        locked=None,
        resolution=None,
        pins=None,
    ):
        if set_id is None and character_id is None:
            raise ValueError("A training run needs a set_id or a character_id.")
        self.enqueue_calls.append(
            {
                "set_id": set_id,
                "character_id": character_id,
                "name": name,
                "base_model": base_model,
                "trigger_word": trigger_word,
                "template_path": template_path,
                "config_overrides": config_overrides,
                "locked": locked,
                "resolution": resolution,
                "pins": pins,
            }
        )

        import uuid as _uuid

        run_id = training_runs.create_run(
            self._hub,
            library_uuid=self._library_uuid,
            uuid=_uuid.uuid4().hex,
            name=name,
            set_id=set_id,
            character_id=character_id,
            base_model=base_model,
            trigger_word=trigger_word,
            preset=resolution["preset"],
            resolved_profile=json.dumps(resolution),
            advanced_pins=json.dumps(pins or {}),
        )
        return training_runs.get_run(self._hub, self._library_uuid, run_id)

    def cancel(self, job_id):
        self.cancel_calls.append(job_id)
        return self.cancel_result

    def stop(self):
        # No background worker to stop; called by Vault.close() at teardown.
        pass

    def reconcile_on_startup(self):
        # No-op: nothing to reconcile in the fake.
        pass

    def start(self):
        # No-op: the fake never spawns a worker thread.
        pass


def _setup(checkout=True):
    """Create a Server, log in as owner, and stub the training service.

    With *checkout*, a fake ai-toolkit checkout that ships the z_image model
    code is saved, as a job needs one to be queued.

    Returns ``(temp_dir, owner_client, server, read_token)`` where
    ``read_token`` is a global READ-scoped token (scoped, so the authz gate
    must reject it on every training endpoint).
    """
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        # Disable background workers so the real TrainingService worker never
        # starts and can never claim a queued job our fake enqueues.
        f.write(json.dumps({"port": 8000, "disable_background_workers": True}))
    server = Server(server_config_path)

    # Replace the real training service with a fake so enqueue/cancel never
    # touch a GPU or spawn a subprocess.
    server.vault.training_service = _FakeTrainingService(
        server.vault.hub, server.vault.library_uuid
    )
    # A 24 GB card, as the arbiter would report it, unless a test says otherwise.
    server.vault.training_room = lambda: dict(_ROOM_24GB)

    client = TestClient(server.api, raise_server_exceptions=True)
    resp = client.post("/login", json={"username": "owner", "password": "ownerpass1"})
    assert resp.status_code == 200, resp.text

    # Mint a global READ token (scoped → token_scope set → must be rejected).
    resp = client.post("/users/me/token", json={"description": "read", "scope": "READ"})
    assert resp.status_code == 200, resp.text
    read_token = resp.json()["token"]
    if checkout:
        repo = _fake_checkout(os.path.join(temp_dir.name, "aitk"))
        _save_checkout(client, repo, _venv_python(repo))

    # The sets and characters here hold no picture files; a test sets the
    # count the dataset would have. Patched last, so a failing step above
    # cannot leak it into other modules; restored in _teardown.
    _PICTURES["count"] = 24
    ExportUtils.count_training_pictures = staticmethod(
        lambda server, **kwargs: _PICTURES["count"]
    )
    ExportUtils.training_counts = staticmethod(
        lambda server, **kwargs: (_PICTURES["count"], _PICTURES.get("clips", 0))
    )

    return temp_dir, client, server, read_token


def _teardown(temp_dir, server):
    ExportUtils.count_training_pictures = staticmethod(_COUNT_TRAINING_PICTURES)
    ExportUtils.training_counts = staticmethod(_TRAINING_COUNTS)
    _PICTURES.pop("clips", None)
    server.close()
    temp_dir.cleanup()
    gc.collect()


def _scoped_client(server, read_token):
    """A TestClient that always sends the scoped READ bearer token."""
    client = TestClient(server.api, raise_server_exceptions=True)
    client.headers.update({"Authorization": f"Bearer {read_token}"})
    return client


def _seed_job(server, **overrides):
    """Insert a training run directly in the hub; return its id and uuid.

    ``library_uuid`` may be overridden to seed another library's run.
    """
    import uuid as _uuid

    creating = {
        "library_uuid",
        "name",
        "set_id",
        "character_id",
        "base_model",
        "trigger_word",
    }
    fields = {
        "name": "seeded-job",
        "library_uuid": server.vault.library_uuid,
        "status": "completed",
        "set_id": None,
        "character_id": None,
        "base_model": "Tongyi-MAI/Z-Image-Turbo",
        "trigger_word": "myperson",
        "step": 3000,
        "total_steps": 3000,
        "loss_curve": json.dumps([{"step": 1, "loss": 0.5}, {"step": 2, "loss": 0.4}]),
        "rendered_config": json.dumps(
            {"config": {"process": [{"train": {"steps": 3000}}]}}
        ),
        "report": "done",
        "sample_image_paths": json.dumps(["loras/abc_samples/sample_0.png"]),
        "dataset_picture_ids": json.dumps([1, 2, 3]),
        "ai_toolkit_commit": "deadbeef",
    }
    fields.update(overrides)
    # A completed run the worker is done with has a finish time; without one it
    # is still drawing previews or being imported, so a test asks for that.
    if fields["status"] == "completed":
        fields.setdefault("completed_at", "2026-10-26T09:00:00+00:00")
    created = {key: fields.pop(key) for key in list(fields) if key in creating}
    job_uuid = _uuid.uuid4().hex
    hub = server.vault.hub
    run_id = training_runs.create_run(hub, uuid=job_uuid, **created)
    training_runs.update_run(hub, job_uuid, **fields)
    return run_id, job_uuid


def _make_picture_set(owner_client, name="Set"):
    """Create a picture set via the owner API and return its id (real FK target)."""
    r = owner_client.post("/picture_sets", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["picture_set"]["id"]


def _make_character(owner_client, name="Char"):
    """Create a character via the owner API and return its id (real FK target)."""
    r = owner_client.post("/characters", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["character"]["id"]


# ---------------------------------------------------------------------------
# POST /training/jobs
# ---------------------------------------------------------------------------


def test_create_job_owner_works():
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        r = owner.post(
            "/training/jobs",
            json={
                "character_id": char_id,
                "name": "Alice LoRA",
                "trigger_word": "alice",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "queued"
        assert body["character_id"] == char_id
        assert body["uuid"]
        # The default recipe, resolved for this card: a character is a person.
        assert body["resolved_profile"]["preset"] == "zimage-turbo@1"
        assert body["resolved_profile"]["subject"] == "person"
        assert body["resolved_profile"]["profile"] == "q8"
        call = server.vault.training_service.enqueue_calls[-1]
        assert call["trigger_word"] == "alice"
        assert call["template_path"].endswith("zimage-turbo-lora.json")
        assert call["locked"]["config"]["process"][0]["model"]["arch"] == (
            "zimage:turbo"
        )
        assert call["resolution"]["vram_mb"] == 16000
    finally:
        _teardown(temp_dir, server)


def test_create_job_takes_base_model_from_the_recipe_and_refuses_a_client_one():
    """The job's base model is what the trainer loads: the recipe's locked
    model, read by the server. A client-sent base_model is refused (review
    F4), so the row cannot claim a model the run did not train."""
    from pixlstash.training import presets

    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        expected = presets.get_preset("zimage-turbo")["locked"]["model.name_or_path"]
        body = {"character_id": char_id, "name": "Alice", "trigger_word": "alice"}
        r = owner.post("/training/jobs", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["base_model"] == expected
        assert server.vault.training_service.enqueue_calls[-1]["base_model"] == expected
        # The listing and the live view carry the tile the run's card wears.
        listed = next(
            j for j in owner.get("/training/jobs").json() if j["name"] == "Alice"
        )
        assert listed["base_tile"]["label"] == "Z-Image Turbo"
        live = owner.get(f"/training/jobs/{listed['id']}/live").json()
        assert (live["base_model"], live["base_tile"]) == (
            expected,
            listed["base_tile"],
        )

        calls_before = len(server.vault.training_service.enqueue_calls)
        r = owner.post("/training/jobs", json={**body, "base_model": "Other/Model"})
        assert r.status_code == 422, r.text
        assert len(server.vault.training_service.enqueue_calls) == calls_before
    finally:
        _teardown(temp_dir, server)


def test_create_job_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        scoped = _scoped_client(server, read_token)
        r = scoped.post(
            "/training/jobs",
            json={"character_id": 7, "name": "x", "trigger_word": "alice"},
        )
        assert r.status_code == 403, r.text
        # The fake service must NOT have been called for a forbidden request.
        assert server.vault.training_service.enqueue_calls == []
    finally:
        _teardown(temp_dir, server)


def test_create_job_neither_source_is_400():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.post("/training/jobs", json={"name": "x", "trigger_word": "alice"})
        assert r.status_code == 400, r.text
        assert "set_id" in r.json()["detail"]
    finally:
        _teardown(temp_dir, server)


def test_create_job_both_sources_is_400():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.post(
            "/training/jobs",
            json={
                "set_id": 1,
                "character_id": 2,
                "name": "x",
                "trigger_word": "alice",
            },
        )
        assert r.status_code == 400, r.text
    finally:
        _teardown(temp_dir, server)


def test_create_job_unknown_preset_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.post(
            "/training/jobs",
            json={
                "character_id": 7,
                "name": "x",
                "trigger_word": "alice",
                "preset": "does-not-exist",
            },
        )
        assert r.status_code == 404, r.text
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /training/jobs (list)
# ---------------------------------------------------------------------------


def test_list_jobs_owner_works_and_filters():
    temp_dir, owner, server, read_token = _setup()
    try:
        set_id = _make_picture_set(owner)
        char_id = _make_character(owner)
        id_set, _ = _seed_job(server, set_id=set_id, name="set-job")
        id_char, _ = _seed_job(server, character_id=char_id, name="char-job")
        id_failed, _ = _seed_job(
            server,
            status="error",
            error="Dataset build failed: /home/me/pixlstash/work is full",
        )

        r = owner.get("/training/jobs")
        assert r.status_code == 200, r.text
        rows = {row["id"]: row for row in r.json()}
        assert {id_set, id_char} <= rows.keys()
        # A failed row says why, with the host path redacted like the rest.
        assert rows[id_failed]["error"] == "Dataset build failed: <host path>"
        assert rows[id_set]["error"] is None

        r = owner.get("/training/jobs", params={"set_id": set_id})
        assert r.status_code == 200, r.text
        ids = {row["id"] for row in r.json()}
        assert ids == {id_set}

        r = owner.get("/training/jobs", params={"character_id": char_id})
        assert r.status_code == 200, r.text
        ids = {row["id"] for row in r.json()}
        assert ids == {id_char}
    finally:
        _teardown(temp_dir, server)


def test_list_jobs_hides_another_librarys_runs():
    """A run of another library is neither listed nor addressable by id.

    Its set and character ids are that library's vault ids, so here they would
    name the wrong pictures. It answers as a run that does not exist.
    """
    temp_dir, owner, server, read_token = _setup()
    try:
        mine, _ = _seed_job(server, name="mine")
        theirs, _ = _seed_job(server, name="theirs", library_uuid="another-library")

        r = owner.get("/training/jobs")
        assert r.status_code == 200, r.text
        ids = {row["id"] for row in r.json()}
        assert mine in ids
        assert theirs not in ids

        assert owner.get(f"/training/jobs/{theirs}").status_code == 404
        assert owner.get(f"/training/jobs/{theirs}/status").status_code == 404
        assert owner.post(f"/training/jobs/{theirs}/cancel").status_code == 404
        assert owner.delete(f"/training/jobs/{theirs}").status_code == 404
    finally:
        _teardown(temp_dir, server)


def test_list_jobs_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        scoped = _scoped_client(server, read_token)
        r = scoped.get("/training/jobs")
        assert r.status_code == 403, r.text
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /training/jobs/{id} (detail)
# ---------------------------------------------------------------------------


def test_get_job_detail_owner_works():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server)
        r = owner.get(f"/training/jobs/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == job_id
        # Text columns parsed into structured JSON.
        assert body["loss_curve"] == [
            {"step": 1, "loss": 0.5},
            {"step": 2, "loss": 0.4},
        ]
        assert body["rendered_config"]["config"]["process"][0]["train"]["steps"] == 3000
        assert body["dataset_picture_ids"] == [1, 2, 3]
    finally:
        _teardown(temp_dir, server)


def test_get_job_detail_missing_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.get("/training/jobs/999999")
        assert r.status_code == 404, r.text
    finally:
        _teardown(temp_dir, server)


def test_get_job_detail_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server)
        scoped = _scoped_client(server, read_token)
        r = scoped.get(f"/training/jobs/{job_id}")
        assert r.status_code == 403, r.text
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /training/jobs/{id}/status
# ---------------------------------------------------------------------------


def test_job_status_owner_works():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server, step=1500, total_steps=3000)
        r = owner.get(f"/training/jobs/{job_id}/status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["step"] == 1500
        assert body["progress"] == 0.5
        assert body["latest_loss"] == 0.4
    finally:
        _teardown(temp_dir, server)


def test_job_status_progress_stops_at_one_past_the_planned_total():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server, step=3500, total_steps=3000)
        r = owner.get(f"/training/jobs/{job_id}/status")
        assert r.status_code == 200, r.text
        assert r.json()["progress"] == 1.0
    finally:
        _teardown(temp_dir, server)


def test_job_status_missing_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.get("/training/jobs/999999/status")
        assert r.status_code == 404, r.text
    finally:
        _teardown(temp_dir, server)


def test_job_status_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server)
        scoped = _scoped_client(server, read_token)
        r = scoped.get(f"/training/jobs/{job_id}/status")
        assert r.status_code == 403, r.text
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /training/jobs/{id}/live and PUT /training/jobs/{id}/cover (#2118)
# ---------------------------------------------------------------------------


def _live_run(server, temp_dir, **overrides):
    """A run training in a registered source folder, halfway through a preview.

    1,000 of 1,500 steps, previews every 250 at two prompts (one with the
    trigger word), the second sample of step 1000 being drawn, and a checkpoint
    kept at 750 and 1000 only (the trainer pruned 250 and 500).
    """
    from pixlstash.routes.model_folders import register_model_folder
    from pixlstash.services.training_service import handle_for_run

    output = os.path.join(temp_dir.name, "aitk-output")
    os.makedirs(output, exist_ok=True)
    register_model_folder(server, output, "source")
    folder = server.vault.hub.fetchone(
        "SELECT id, path FROM model_folder WHERE kind = 'source'"
    )
    config = {
        "config": {
            "process": [
                {
                    "train": {"steps": 1500},
                    "sample": {
                        "sample_every": 250,
                        "samples": [
                            {"prompt": "photo of myperson / raincoat, rain"},
                            {"prompt": "photo of a woman in a raincoat"},
                        ],
                    },
                    "model": {"layer_offloading": False},
                }
            ]
        }
    }
    fields = {
        "status": "running",
        "step": 1000,
        "total_steps": 1500,
        "training_folder": folder["path"],
        "rendered_config": json.dumps(config),
        "completed_at": None,
        "phase_log": json.dumps(
            [
                {"phase": "waiting_gpu", "at": "2026-10-26T01:00:00+00:00"},
                {"phase": "loading", "at": "2026-10-26T01:00:30+00:00"},
                {"phase": "training", "at": "2026-10-26T01:03:05+00:00"},
            ]
        ),
    }
    fields.update(overrides)
    job_id, job_uuid = _seed_job(server, **fields)
    job = training_runs.get_run(server.vault.hub, server.vault.library_uuid, job_id)
    handle = handle_for_run(os.path.join(server.vault.image_root, "training"), job)
    samples = os.path.join(handle.save_root, "samples")
    os.makedirs(samples)
    for step in (0, 250, 500, 750):
        for index in (0, 1):
            open(
                os.path.join(samples, f"1700000000000__{step:09d}_{index}.jpg"), "wb"
            ).close()
    open(os.path.join(samples, "1700000000000__000001000_0.jpg"), "wb").close()
    stem = os.path.basename(handle.save_root)
    for step in (750, 1000):
        open(
            os.path.join(handle.save_root, f"{stem}_{step:09d}.safetensors"), "wb"
        ).close()
    os.makedirs(os.path.dirname(handle.sqlite_db_path), exist_ok=True)
    conn = sqlite3.connect(handle.sqlite_db_path)
    conn.execute("CREATE TABLE Job (id TEXT PRIMARY KEY, info TEXT)")
    conn.execute("INSERT INTO Job VALUES (?, 'Generating images - 2/2')", (job_uuid,))
    conn.commit()
    conn.close()
    conn = sqlite3.connect(handle.loss_db_path)
    conn.execute("CREATE TABLE steps (step INTEGER PRIMARY KEY, wall_time REAL)")
    conn.executemany(
        "INSERT INTO steps VALUES (?, ?)",
        [(step, 1_700_000_000.0 + 2.0 * step) for step in range(1, 1001)],
    )
    conn.commit()
    conn.close()
    return job_id, job_uuid, folder["id"], stem


def test_live_view_draws_a_run_from_its_folder_and_its_logs():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _, folder_id, stem = _live_run(server, temp_dir)
        r = owner.get(f"/training/jobs/{job_id}/live")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["phase"] == "previews"
        assert body["previews"] == {"drawing": 2, "total": 2}
        assert body["columns"] == [0, 250, 500, 750, 1000, 1250, 1500]
        assert body["run_folder"] == {"folder_id": folder_id, "run_name": stem}
        with_word, without = body["rows"]
        assert (with_word["with_trigger"], without["with_trigger"]) == (True, False)
        # A slash in a prompt is not a host path: the prompt comes back whole.
        assert with_word["prompt"] == "photo of myperson / raincoat, rain"
        assert [c["state"] for c in with_word["cells"]] == [
            "sample",
            "sample",
            "sample",
            "sample",
            "sample",
            "future",
            "future",
        ]
        assert [c["state"] for c in without["cells"]][4:] == [
            "drawing",
            "future",
            "future",
        ]
        assert with_word["cells"][0]["filename"] == "1700000000000__000000000_0.jpg"
        # Pruned checkpoints cannot cover the run; the last step is its final.
        assert body["coverable"] == [750, 1000, 1500]
        assert body["imported"] is False and body["cover_step"] is None
        assert body["speed"]["seconds_per_step"] == 2.0
        assert body["speed"]["remaining_low_s"] == 1000.0
        assert [p["phase"] for p in body["phases"]] == [
            "waiting_gpu",
            "loading",
            "training",
        ]
        assert body["phases"][1]["seconds"] == 155.0
        # Nothing could be measured of the trainer itself: unknown, never 0.
        assert body["telemetry"]["gpu"]["ours_mb"] is None
        assert body["telemetry"]["need"] == {
            "vram_mb": None,
            "ram_mb": None,
            "basis": "estimated",
        }

        # Use this step: a kept checkpoint is recorded for the import.
        r = owner.put(f"/training/jobs/{job_id}/cover", json={"step": 750})
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": job_id, "cover_step": 750, "applied": False}
        assert owner.get(f"/training/jobs/{job_id}/live").json()["cover_step"] == 750
        # A pruned one is refused and changes nothing.
        r = owner.put(f"/training/jobs/{job_id}/cover", json={"step": 250})
        assert r.status_code == 409, r.text
        assert owner.get(f"/training/jobs/{job_id}/live").json()["cover_step"] == 750
        assert (
            owner.put("/training/jobs/999999/cover", json={"step": 0}).status_code
            == 404
        )

        # A failure moving the shelf puts the recorded step back.
        def broken(job, step):
            raise RuntimeError("the vault went away")

        server.vault.choose_training_cover = broken
        r = owner.put(f"/training/jobs/{job_id}/cover", json={"step": 1000})
        assert r.status_code == 500, r.text
        assert owner.get(f"/training/jobs/{job_id}/live").json()["cover_step"] == 750
    finally:
        _teardown(temp_dir, server)


def test_live_view_of_a_run_that_finished_while_pixlstash_was_closed():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _, _, _ = _live_run(
            server,
            temp_dir,
            status="completed",
            step=1500,
            completed_at="2026-10-26T09:00:00+00:00",
            finished_unwatched=1,
        )
        body = owner.get(f"/training/jobs/{job_id}/live").json()
        assert body["phase"] is None
        # The trainer's last logged step, not the moment PixlStash noticed.
        assert body["finished_while_closed"] == {"at": "2023-11-14T22:46:40+00:00"}
        assert body["speed"]["remaining_low_s"] is None
        assert body["telemetry"]["ram"]["total_mb"] is None
        # Cells the run passed without a picture are missing, not waiting.
        assert body["rows"][1]["cells"][4]["state"] == "missing"
    finally:
        _teardown(temp_dir, server)


def test_a_run_that_ended_without_a_finish_time_has_no_growing_last_phase():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _, _, _ = _live_run(server, temp_dir, status="error")
        phases = owner.get(f"/training/jobs/{job_id}/live").json()["phases"]
        assert phases[-1] == {"phase": "training", "seconds": None}
        assert phases[1]["seconds"] == 155.0

        # A queued run whose lease is granted is being probed: the worker's
        # phase telemetry on the lease says so, as the row cannot (#2120).
        queued_id, queued_uuid = _seed_job(server, status="queued", step=0)
        lease = arbiter().request(
            Tenant.AITOOLKIT, "probe", need_mb=0, device=None, job_id=queued_uuid
        )
        try:
            live = owner.get(f"/training/jobs/{queued_id}/live").json()
            assert live["phase"] == "waiting_gpu"
            lease.set_progress(step=0, total=6, phase="loading", shown_as="checking")
            live = owner.get(f"/training/jobs/{queued_id}/live").json()
            assert live["phase"] == "checking"
            assert live["downloading"] is False  # the probe has no log yet
            # The job list says so too, for the runs grid's card.
            listed = {j["uuid"]: j for j in owner.get("/training/jobs").json()}
            assert listed[queued_uuid]["phase"] == "checking"
            # The probe's log ends on a model download: the view says so, and
            # stops once the download is complete.
            probe_log = LocalAiToolkit.handle_for(
                os.path.join(server.vault.image_root, "training", queued_uuid, "probe"),
                queued_uuid,
                "seeded-job",
                None,
            ).log_path
            os.makedirs(os.path.dirname(probe_log), exist_ok=True)
            with open(probe_log, "w", encoding="utf-8") as fh:
                fh.write(
                    "Loading transformer\n\rDownloading bytes: ##  | 1.2GB, 30MB/s"
                )
            live = owner.get(f"/training/jobs/{queued_id}/live").json()
            assert live["downloading"] is True
            with open(probe_log, "a", encoding="utf-8") as fh:
                fh.write("\rDownload complete: : ##########| 6.97GB\nQuantizing\n")
            live = owner.get(f"/training/jobs/{queued_id}/live").json()
            assert live["downloading"] is False
        finally:
            lease.release()
        assert owner.get(f"/training/jobs/{queued_id}/live").json()["phase"] == (
            "waiting_gpu"
        )
        listed = {j["uuid"]: j for j in owner.get("/training/jobs").json()}
        assert listed[queued_uuid]["phase"] is None
    finally:
        _teardown(temp_dir, server)


def test_swap_traffic_is_paging_only_while_ram_is_tight(monkeypatch):
    moved = {"bytes": 0}
    monkeypatch.setattr(
        psutil,
        "swap_memory",
        lambda: types.SimpleNamespace(sin=moved["bytes"], sout=0),
    )
    lots_free = {"total_mb": 96_000, "available_mb": 70_000}
    tight = {"total_mb": 96_000, "available_mb": 4_000}
    burst = 2 * training_routes._PAGING_BYTES
    try:
        # The first read only records the counters.
        assert training_routes._paging(-1, tight) is False
        # Swap traffic with most of RAM free is the OS, not pressure.
        moved["bytes"] += burst
        assert training_routes._paging(-1, lots_free) is False
        moved["bytes"] += burst
        assert training_routes._paging(-1, tight) is True
        # Tight RAM but no traffic since the last read is not paging.
        assert training_routes._paging(-1, tight) is False
        # Unmeasured RAM never claims paging.
        moved["bytes"] += burst
        assert training_routes._paging(-1, {"total_mb": 0, "available_mb": 0}) is False
    finally:
        training_routes._swap_seen.pop(-1, None)


def test_live_and_cover_refuse_a_read_token():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server)
        scoped = _scoped_client(server, read_token)
        assert scoped.get(f"/training/jobs/{job_id}/live").status_code == 403
        r = scoped.put(f"/training/jobs/{job_id}/cover", json={"step": 3000})
        assert r.status_code == 403, r.text
        # The positive control beside each negative: the owner gets through.
        assert owner.get(f"/training/jobs/{job_id}/live").status_code == 200
        r = owner.put(f"/training/jobs/{job_id}/cover", json={"step": 3000})
        assert r.status_code == 200, r.text
    finally:
        _teardown(temp_dir, server)


def test_a_runs_snapshot_reads_back_and_exports_what_it_trained_on():
    """The snapshot is the frozen record (#2123): the zip holds each image whose
    bytes still match, every frozen caption, and says which image it left out.
    The set's runs name the LoRAs they made."""
    temp_dir, owner, server, read_token = _setup()
    try:
        hub = server.vault.hub
        set_id = _make_picture_set(owner)
        job_id, job_uuid = _seed_job(server, set_id=set_id, name="My run")
        bare_id, _ = _seed_job(server, set_id=set_id, name="bare")

        dataset = os.path.join(temp_dir.name, "datasets", f"pixlstash-{job_uuid}")
        os.makedirs(dataset)
        kept, changed = b"KEPT_BYTES", b"ORIGINAL_BYTES"
        for name, data in (("image_00001.png", kept), ("image_00002.png", changed)):
            with open(os.path.join(dataset, name), "wb") as fh:
                fh.write(data)
        training_runs.update_run(hub, job_uuid, dataset_snapshot_path=dataset)
        dataset_snapshots.record_snapshot(
            hub,
            library_uuid=server.vault.library_uuid,
            run_uuid=job_uuid,
            set_id=set_id,
            character_id=None,
            trigger_word="ohwx",
            caption_mode="tags",
            items=[
                {
                    "picture_id": 7,
                    "file_name": "image_00001.png",
                    "sha256": hashlib.sha256(kept).hexdigest(),
                    "caption": "ohwx, red hat",
                },
                {
                    "picture_id": 8,
                    "file_name": "image_00002.png",
                    "sha256": hashlib.sha256(changed).hexdigest(),
                    "caption": "ohwx, blue coat",
                },
            ],
        )
        # The dataset folder changes after the run: that image no longer matches.
        with open(os.path.join(dataset, "image_00002.png"), "wb") as fh:
            fh.write(b"EDITED_LATER")
        with hub.transaction() as conn:
            # A model no run made, first, so no LoRA id equals its run's id.
            conn.execute(
                "INSERT INTO model (file_kind, filename, sha256, provenance) "
                "VALUES ('checkpoint', 'base.safetensors', 'beef', 'scanned')"
            )
            # A row with no stack position and a stepped checkpoint, both
            # ahead of the cover in id order: the cover still leads, and the
            # unstacked row comes last.
            loose_id = conn.execute(
                "INSERT INTO model (file_kind, kind, filename, sha256, provenance, "
                "training_run_id) VALUES ('adapter', 'lora', "
                "'my_run_000000250.safetensors', 'c0de', 'trained', ?)",
                (job_id,),
            ).lastrowid
            step_id = conn.execute(
                "INSERT INTO model (file_kind, kind, filename, sha256, provenance, "
                "training_run_id, stack_position) VALUES ('adapter', 'lora', "
                "'my_run_000000500.safetensors', 'f00d', 'trained', ?, 1)",
                (job_id,),
            ).lastrowid
            lora_id = conn.execute(
                "INSERT INTO model (file_kind, kind, filename, sha256, provenance, "
                "training_run_id, stack_position) VALUES ('adapter', 'lora', "
                "'my_run.safetensors', 'feed', 'trained', ?, 0)",
                (job_id,),
            ).lastrowid

        r = owner.get(f"/training/jobs/{job_id}/snapshot")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job_id"] == job_id
        assert body["image_count"] == 2
        assert [item["caption"] for item in body["items"]] == [
            "ohwx, red hat",
            "ohwx, blue coat",
        ]
        assert "library_uuid" not in body

        r = owner.get(f"/training/jobs/{job_id}/snapshot/export")
        assert r.status_code == 200, r.text
        assert "My_run_dataset.zip" in r.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
            assert sorted(archive.namelist()) == [
                "image_00001.png",
                "image_00001.txt",
                "image_00002.txt",
                "snapshot.json",
            ]
            assert archive.read("image_00001.png") == kept
            assert archive.read("image_00002.txt") == b"ohwx, blue coat\n"
            listed = json.loads(archive.read("snapshot.json"))
        assert [item["image_included"] for item in listed["items"]] == [True, False]
        assert listed["trigger_word"] == "ohwx"

        # The set links to the LoRAs trained from it, and the run to its snapshot.
        rows = {
            row["id"]: row
            for row in owner.get("/training/jobs", params={"set_id": set_id}).json()
        }
        assert rows[job_id]["model_ids"] == [lora_id, step_id, loose_id]
        assert rows[job_id]["dataset_snapshot_id"] == body["id"]
        assert rows[bare_id]["model_ids"] == []
        detail = owner.get(f"/training/jobs/{job_id}").json()
        assert detail["model_ids"] == [lora_id, step_id, loose_id]
        live = owner.get(f"/training/jobs/{job_id}/live").json()
        assert live["dataset_snapshot_id"] == body["id"]

        # A run with no snapshot, and one that does not exist, answer 404.
        assert owner.get(f"/training/jobs/{bare_id}/snapshot").status_code == 404
        assert owner.get(f"/training/jobs/{bare_id}/snapshot/export").status_code == (
            404
        )
        assert owner.get("/training/jobs/999999/snapshot").status_code == 404

        # A READ token is refused both, beside the owner's positive above.
        scoped = _scoped_client(server, read_token)
        assert scoped.get(f"/training/jobs/{job_id}/snapshot").status_code == 403
        r = scoped.get(f"/training/jobs/{job_id}/snapshot/export")
        assert r.status_code == 403, r.text

        # Deleting the run takes its snapshot with it.
        assert owner.delete(f"/training/jobs/{job_id}").status_code == 200
        assert hub.fetchone("SELECT COUNT(*) FROM dataset_snapshot")[0] == 0
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# POST /training/jobs/{id}/cancel
# ---------------------------------------------------------------------------


def test_cancel_job_owner_works():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server, status="running")
        server.vault.training_service.cancel_result = True
        r = owner.post(f"/training/jobs/{job_id}/cancel")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"
        assert server.vault.training_service.cancel_calls == [job_id]
    finally:
        _teardown(temp_dir, server)


def test_cancel_job_unknown_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.post("/training/jobs/999999/cancel")
        assert r.status_code == 404, r.text
        # Unknown job: the service cancel must not have been invoked.
        assert server.vault.training_service.cancel_calls == []
    finally:
        _teardown(temp_dir, server)


def test_cancel_job_terminal_is_409():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server, status="completed")
        # The real service returns False for a terminal job; mirror that.
        server.vault.training_service.cancel_result = False
        r = owner.post(f"/training/jobs/{job_id}/cancel")
        assert r.status_code == 409, r.text
    finally:
        _teardown(temp_dir, server)


def test_cancel_409_names_the_status_the_service_saw():
    """A job that finishes while cancel runs is reported as it is now, not as read before."""
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, job_uuid = _seed_job(server, status="running")
        hub = server.vault.hub
        service = server.vault.training_service

        def finish_then_refuse(_job_id):
            training_runs.update_run(hub, job_uuid, status="completed")
            return False

        service.cancel = finish_then_refuse
        r = owner.post(f"/training/jobs/{job_id}/cancel")
        assert r.status_code == 409, r.text
        assert "completed" in r.json()["detail"], r.text
    finally:
        _teardown(temp_dir, server)


def test_cancel_job_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server, status="running")
        scoped = _scoped_client(server, read_token)
        r = scoped.post(f"/training/jobs/{job_id}/cancel")
        assert r.status_code == 403, r.text
        assert server.vault.training_service.cancel_calls == []
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# DELETE /training/jobs/{id}
# ---------------------------------------------------------------------------


def test_delete_job_owner_removes_rows_and_work_dir_only():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, job_uuid = _seed_job(server)
        work_dir = os.path.join(server.vault.image_root, "training", job_uuid)
        os.makedirs(work_dir, exist_ok=True)
        # A model already on the shelf is its own copy, not the job's: it stays.
        shelf_file = os.path.join(server.vault.image_root, "loras", "shelf.safetensors")
        os.makedirs(os.path.dirname(shelf_file), exist_ok=True)
        with open(shelf_file, "wb") as fh:
            fh.write(b"SHELF-WEIGHTS")

        r = owner.delete(f"/training/jobs/{job_id}")
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "deleted", "job_id": job_id}

        assert owner.get(f"/training/jobs/{job_id}").status_code == 404
        assert not os.path.isdir(work_dir)
        assert os.path.exists(shelf_file)
    finally:
        _teardown(temp_dir, server)


def test_delete_job_missing_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.delete("/training/jobs/999999")
        assert r.status_code == 404, r.text
    finally:
        _teardown(temp_dir, server)


@pytest.mark.parametrize("status", ["queued", "running", "stopping"])
def test_delete_job_refuses_a_live_job(status):
    """A job whose trainer may still run is never deleted; its work dir stays."""
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, job_uuid = _seed_job(server, status=status)
        work_dir = os.path.join(server.vault.image_root, "training", job_uuid)
        os.makedirs(work_dir, exist_ok=True)

        r = owner.delete(f"/training/jobs/{job_id}")
        assert r.status_code == 409, r.text
        assert owner.get(f"/training/jobs/{job_id}").status_code == 200
        assert os.path.isdir(work_dir)
    finally:
        _teardown(temp_dir, server)


def test_delete_job_refuses_a_run_still_drawing_previews_or_importing():
    """Completed with no completed_at: the worker still reads its work dir."""
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, job_uuid = _seed_job(server, completed_at=None)
        work_dir = os.path.join(server.vault.image_root, "training", job_uuid)
        os.makedirs(work_dir, exist_ok=True)

        r = owner.delete(f"/training/jobs/{job_id}")
        assert r.status_code == 409, r.text
        assert "previews" in r.json()["detail"], r.text
        assert owner.get(f"/training/jobs/{job_id}").status_code == 200
        assert os.path.isdir(work_dir)
    finally:
        _teardown(temp_dir, server)


def test_delete_job_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(server)
        scoped = _scoped_client(server, read_token)
        r = scoped.delete(f"/training/jobs/{job_id}")
        assert r.status_code == 403, r.text
        # Row must survive a forbidden delete.
        r = owner.get(f"/training/jobs/{job_id}")
        assert r.status_code == 200
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /training/presets
# ---------------------------------------------------------------------------


def test_list_presets_offers_only_what_the_saved_checkout_can_run(tmp_path):
    """No checkout: nothing is offered. One with the z_image model code: Z-Image
    Turbo is. The recipe is listed either way, so the dialog can say why."""
    temp_dir, owner, server, read_token = _setup(checkout=False)
    try:
        r = owner.get("/training/presets")
        assert r.status_code == 200, r.text
        entry = next(item for item in r.json() if item["id"] == "zimage-turbo")
        assert entry["base_model"] == "Tongyi-MAI/Z-Image-Turbo"
        assert entry["offered"] is False
        # Not offered means not queued: with no checkout the job is refused.
        char_id = _make_character(owner)
        r = owner.post(
            "/training/jobs",
            json={"character_id": char_id, "name": "x", "trigger_word": "tw"},
        )
        assert r.status_code == 422, r.text
        assert "not set up" in r.text
        assert server.vault.training_service.enqueue_calls == []

        repo = _fake_checkout(str(tmp_path / "kit"))
        _save_checkout(owner, repo, _venv_python(repo))
        r = owner.get("/training/presets")
        listed = r.json()
        # The dialog preselects the first offered recipe: the default leads.
        assert listed[0]["id"] == "zimage-turbo"
        assert listed[0]["offered"] is True
        # A checkout without their model code lists the others, not offered.
        others = {item["id"]: item["offered"] for item in listed[1:]}
        assert others == {
            "chroma1-base": False,
            "flux2-klein-9b-base": False,
            "krea2-raw": False,
            "minimax-h3-image": False,
            "minimax-h3-motion": False,
            "moss-image-base": False,
        }
        # Only the motion recipe trains on the set's clips.
        assert [item["id"] for item in listed if item["clips"]] == ["minimax-h3-motion"]
    finally:
        _teardown(temp_dir, server)


def test_list_presets_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        scoped = _scoped_client(server, read_token)
        r = scoped.get("/training/presets")
        assert r.status_code == 403, r.text
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# POST /training/presets/check
# ---------------------------------------------------------------------------


def test_preset_check_fetches_only_when_asked_or_opted_in(monkeypatch):
    """The automatic check does nothing until the owner turns it on; the
    button always checks. A READ token cannot trigger either."""
    temp_dir, owner, server, read_token = _setup()
    fetched = []

    def fetch(name):
        fetched.append(name)
        raise preset_updates.FetchError("offline")

    monkeypatch.setattr(preset_updates, "_fetch", fetch)
    try:
        cache = presets.cache_dir()
        assert os.path.dirname(cache) == os.path.dirname(server.server_config_path)

        r = owner.post("/training/presets/check", params={"automatic": True})
        assert r.status_code == 200, r.text
        assert r.json() == {"checked": False, "updated": [], "error": None}
        assert fetched == []

        r = owner.get("/users/me/config")
        assert r.json()["training_presets_auto_check"] is None
        r = owner.patch("/users/me/config", json={"training_presets_auto_check": True})
        assert r.status_code == 200, r.text
        r = owner.post("/training/presets/check", params={"automatic": True})
        assert r.json()["checked"] is True
        assert "pixlstash.dev" in r.json()["error"]
        assert fetched == ["index.json"]

        r = owner.post("/training/presets/check")
        assert r.json()["checked"] is True
        assert fetched == ["index.json", "index.json"]

        scoped = _scoped_client(server, read_token)
        r = scoped.post("/training/presets/check")
        assert r.status_code == 403, r.text
        assert fetched == ["index.json", "index.json"]
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /training/sample-prompts
# ---------------------------------------------------------------------------


def test_sample_prompts_are_two_pairs_and_become_the_runs_default():
    """A character with no captions gets a person's generic noun; the same
    prompts are what an unpinned run samples, and a pinned set wins."""
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        r = owner.get("/training/sample-prompts", params={"character_id": char_id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["subject"] == "person"
        assert body["description"] == "a person"
        assert body["description_source"] == "generic"
        written = [item["prompt"] for item in body["prompts"]]
        assert len(written) == 4
        assert sum("{{TRIGGER_WORD}}" in p for p in written) == 2
        assert (
            owner.get(
                "/training/sample-prompts", params={"character_id": char_id}
            ).json()
            == body
        )

        r = owner.post(
            "/training/jobs",
            json={"character_id": char_id, "name": "x", "trigger_word": "tw"},
        )
        assert r.status_code == 200, r.text
        values = r.json()["resolved_profile"]["values"]
        assert values["sample.samples[*].prompt"] == {
            "value": written,
            "source": "captions",
        }
        call = server.vault.training_service.enqueue_calls[-1]
        samples = call["config_overrides"]["config"]["process"][0]["sample"]
        assert [s["prompt"] for s in samples["samples"]] == written

        mine = ["{{TRIGGER_WORD}} on a boat"]
        r = owner.post(
            "/training/jobs",
            json={
                "character_id": char_id,
                "name": "x",
                "trigger_word": "tw",
                "pins": {"sample.samples[*].prompt": mine},
            },
        )
        assert r.status_code == 200, r.text
        values = r.json()["resolved_profile"]["values"]
        assert values["sample.samples[*].prompt"] == {
            "value": mine,
            "source": "pinned",
        }
    finally:
        _teardown(temp_dir, server)


def test_sample_prompts_keep_the_trigger_word_out_of_the_description():
    """Captions that carry the trigger word must not put it in the prompt
    written without it, on the GET and on a queued run alike."""
    temp_dir, owner, server, read_token = _setup()
    real = ExportUtils.training_captions
    ExportUtils.training_captions = staticmethod(
        lambda server, **kwargs: (["a photo of an ohwx elderly man reading"], [[]])
    )
    try:
        char_id = _make_character(owner)
        r = owner.get(
            "/training/sample-prompts",
            params={"character_id": char_id, "trigger_word": "ohwx"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["description"] == "an elderly man"
        r = owner.post(
            "/training/jobs",
            json={"character_id": char_id, "name": "x", "trigger_word": "ohwx"},
        )
        assert r.status_code == 200, r.text
        written = r.json()["resolved_profile"]["values"]["sample.samples[*].prompt"]
        assert not any("ohwx" in prompt for prompt in written["value"])
    finally:
        ExportUtils.training_captions = staticmethod(real)
        _teardown(temp_dir, server)


def test_sample_prompts_for_a_set_need_a_subject_and_one_source():
    temp_dir, owner, server, read_token = _setup()
    try:
        set_id = _make_picture_set(owner)
        r = owner.get("/training/sample-prompts", params={"set_id": set_id})
        assert r.status_code == 422, r.text
        r = owner.get(
            "/training/sample-prompts", params={"set_id": set_id, "subject": "style"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["prompts"][0]["prompt"].endswith("{{TRIGGER_WORD}} style")
        assert r.json()["description"] is None
        r = owner.get("/training/sample-prompts")
        assert r.status_code == 400, r.text
    finally:
        _teardown(temp_dir, server)


def test_sample_prompts_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        scoped = _scoped_client(server, read_token)
        r = scoped.get("/training/sample-prompts", params={"character_id": char_id})
        assert r.status_code == 403, r.text
        # In-scope control: the same request as the owner resolves the route.
        r = owner.get("/training/sample-prompts", params={"character_id": char_id})
        assert r.status_code == 200, r.text
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# POST /training/resolve and GET /training/dataset (the Train dialog's preview)
# ---------------------------------------------------------------------------


def test_create_job_strips_the_trigger_word_and_refuses_control_characters():
    """The stored trigger word is the stripped one, and a newline or control
    character in a name or trigger word is a 422 naming the field."""
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        base = {"character_id": char_id, "name": "Alice LoRA"}

        r = owner.post("/training/jobs", json={**base, "trigger_word": "  alice  "})
        assert r.status_code == 200, r.text
        assert server.vault.training_service.enqueue_calls[-1]["trigger_word"] == (
            "alice"
        )

        for bad in (
            {"trigger_word": "al\nice"},
            {"trigger_word": "alice\x00"},
            {"trigger_word": "al\u0085ice"},
            {"trigger_word": "al\u2028ice"},
            {**base, "name": "Alice\u2029LoRA"},
            {"trigger_word": "   "},
            {**base, "name": "Alice\nLoRA"},
        ):
            payload = {**base, "trigger_word": "alice", **bad}
            r = owner.post("/training/jobs", json=payload)
            assert r.status_code == 422, (bad, r.text)
        assert len(server.vault.training_service.enqueue_calls) == 1
    finally:
        _teardown(temp_dir, server)


def test_create_job_caps_the_name_and_the_trigger_word_length():
    """A name over 200 characters or a trigger word over 500 is refused (422)."""
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        body = {"character_id": char_id, "name": "x", "trigger_word": "alice"}
        r = owner.post("/training/jobs", json={**body, "name": "n" * 200})
        assert r.status_code == 200, r.text
        r = owner.post("/training/jobs", json={**body, "name": "n" * 201})
        assert r.status_code == 422, r.text
        r = owner.post("/training/jobs", json={**body, "trigger_word": "t" * 501})
        assert r.status_code == 422, r.text
    finally:
        _teardown(temp_dir, server)


def test_resolve_refuses_a_picture_count_no_set_can_hold():
    """An absurd picture count is a 422 at the boundary, never an overflow."""
    temp_dir, owner, server, read_token = _setup()
    try:
        body = {"subject": "person", "picture_count": 10**30}
        r = owner.post("/training/resolve", json=body)
        assert r.status_code == 422, r.text
        body["picture_count"] = 1_000_001
        assert owner.post("/training/resolve", json=body).status_code == 422
        body["picture_count"] = 1_000_000
        r = owner.post("/training/resolve", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["derived"]["train.steps"]["value"] == 3000
    finally:
        _teardown(temp_dir, server)


def test_resolve_plans_from_the_same_sample_prompts_a_job_is_created_with():
    """The previews resolve counts are the ones a run stores: four prompts,
    the same list POST /training/jobs plans from for the same scope."""
    from pixlstash.training import presets

    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        r = owner.post(
            "/training/resolve",
            json={
                "character_id": char_id,
                "subject": "person",
                "picture_count": 24,
                "trigger_word": " alice ",
            },
        )
        assert r.status_code == 200, r.text
        resolved = r.json()["resolution"]["values"][presets.PROMPTS_KEY]["value"]
        assert len(resolved) == 4

        r = owner.post(
            "/training/jobs",
            json={"character_id": char_id, "name": "x", "trigger_word": "alice"},
        )
        assert r.status_code == 200, r.text
        call = server.vault.training_service.enqueue_calls[-1]
        assert call["resolution"]["values"][presets.PROMPTS_KEY]["value"] == resolved
    finally:
        _teardown(temp_dir, server)


def _reference_set_of(server, char_id):
    from pixlstash.db_models import Character

    return server.vault.db.run_task(
        lambda session: session.get(Character, char_id).reference_picture_set_id
    )


def test_resolve_previews_the_run_and_every_recipes_fit():
    """The preview resolves exactly as a job would, gives the pin-free value
    a reset returns to, each value's rule, and a fit verdict per recipe; it
    queues nothing and a run that fits nowhere answers 200 with the reason."""
    from pixlstash.training import measurements, presets

    temp_dir, owner, server, read_token = _setup()
    try:
        body = {"subject": "person", "picture_count": 24}
        r = owner.post("/training/resolve", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        steps = out["resolution"]["values"]["train.steps"]
        assert steps == {"value": 2400, "source": "pictures"}
        assert out["derived"]["train.steps"] == {"value": 2400, "source": "pictures"}
        assert out["steps_rule"] == {
            "steps_per_picture": 100,
            "min_steps": 1200,
            "max_steps": 3000,
            "fits": {"lo": 12, "hi": 30},
        }
        assert out["steps_clamp"] is None
        assert out["rules"]["train.steps"]["kind"] == "int"
        assert out["rules"]["model.quantize"]["kind"] == "bool"
        assert "adamw8bit" in out["rules"]["train.optimizer"]["choices"]
        assert out["room"] == _ROOM_24GB
        assert out["presets"] == [
            {
                "id": "zimage-turbo",
                "label": "Z-Image Turbo",
                "offered": True,
                "verdict": "fits",
            },
            *(
                {
                    "id": pid,
                    "label": label,
                    "offered": False,
                    "verdict": "needs_checkout",
                }
                for pid, label in (
                    ("chroma1-base", "Chroma1 Base"),
                    ("flux2-klein-9b-base", "FLUX.2 Klein 9B Base"),
                    ("krea2-raw", "Krea 2 Raw"),
                    ("minimax-h3-image", "MiniMax H3 · image"),
                    ("minimax-h3-motion", "MiniMax H3 · motion"),
                    ("moss-image-base", "MOSS-Image Base"),
                )
            ),
        ]
        assert server.vault.training_service.enqueue_calls == []

        # Never measured on this machine: the run would check memory and speed
        # first, and no time can be given. Once its setup is measured, it is
        # admitted on the measurement and the time is known (#2120).
        first = out["resolution"]
        assert first["probe"] is True and first["fingerprint"]
        assert first["estimate"] == {"seconds": None, "basis": "none"}
        measurements.record(
            server.vault.hub,
            first["fingerprint"],
            preset=first["preset"],
            profile=first["profile"],
            device=None,
            phase_peaks={"training": {"vram_mb": 11_000, "ram_mb": 14_000}},
            seconds_per_update=1.5,
            setup_seconds=120.0,
            seconds_per_preview=5.0,
            source="run",
        )
        measured = owner.post("/training/resolve", json=body).json()["resolution"]
        assert (measured["probe"], measured["evidence"]) == (False, "measured")
        assert measured["vram_mb"] == 12_100
        assert measured["estimate"]["basis"] == "measured"
        assert measured["estimate"]["seconds"] > 2400 * 1.5
        with server.vault.hub.transaction() as conn:
            conn.execute("DELETE FROM training_profile_measurement")

        # A pin wins in the resolution; the derived value stays the choice's.
        r = owner.post(
            "/training/resolve", json={**body, "pins": {"train.steps": 2000}}
        )
        out = r.json()
        assert out["resolution"]["values"]["train.steps"]["source"] == "pinned"
        assert out["derived"]["train.steps"] == {"value": 2400, "source": "pictures"}

        # A big set is held at the effort's cap, a small one at its floor.
        r = owner.post("/training/resolve", json={**body, "picture_count": 112})
        assert r.json()["steps_clamp"] == "cap"
        assert r.json()["resolution"]["values"]["train.steps"]["value"] == 3000
        r = owner.post("/training/resolve", json={**body, "picture_count": 8})
        assert r.json()["steps_clamp"] == "floor"

        # A checkout without the model code cannot train the recipe.
        archs = presets.checkout_architectures
        presets.checkout_architectures = lambda repo: []
        try:
            r = owner.post("/training/resolve", json=body)
        finally:
            presets.checkout_architectures = archs
        assert r.json()["presets"][0]["verdict"] == "needs_checkout"
        assert r.json()["presets"][0]["offered"] is False

        # Save memory streams layers from RAM; a small card fits nowhere.
        r = owner.post("/training/resolve", json={**body, "fit": "save_memory"})
        assert r.json()["presets"][0]["verdict"] == "streams"
        server.vault.training_room = lambda: {"vram_mb": 7_000, "ram_mb": 60_000}
        r = owner.post("/training/resolve", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["resolution"] is None
        assert out["error"]["budget"] == "vram"
        assert out["presets"][0]["verdict"] == "no_gpu"

        # Unreadable RAM resolves without the RAM check, as a job does
        # (create_training_job warns and queues); only the GPU reading is
        # required.
        server.vault.training_room = lambda: {"vram_mb": 22_800, "ram_mb": None}
        r = owner.post("/training/resolve", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["error"] is None
        assert r.json()["resolution"]["profile"] == "q8"
        assert r.json()["presets"][0]["verdict"] == "fits"
        r = owner.post(
            "/training/jobs",
            json={
                "subject": "person",
                "set_id": _make_picture_set(owner),
                "name": "x",
                "trigger_word": "tw",
            },
        )
        assert r.status_code == 200, r.text

        # A locked key is named, not a 500.
        server.vault.training_room = lambda: dict(_ROOM_24GB)
        r = owner.post("/training/resolve", json={**body, "pins": {"model.arch": "x"}})
        assert r.status_code == 200, r.text
        assert "model.arch" in r.json()["error"]["message"]

        scoped = _scoped_client(server, read_token)
        assert scoped.post("/training/resolve", json=body).status_code == 403
    finally:
        _teardown(temp_dir, server)


def test_dataset_counts_and_checks_and_knows_a_reference_set():
    temp_dir, owner, server, read_token = _setup()
    facts = ExportUtils.training_facts
    ExportUtils.training_facts = staticmethod(
        lambda server, **scope: {
            "count": 3,
            "clips": 2,
            "untagged": 1,
            "near_duplicates": 2,
            "other_people": 0,
        }
    )
    _PICTURES["clips"] = 1
    try:
        set_id = _make_picture_set(owner)
        r = owner.get("/training/dataset", params={"set_id": set_id})
        assert r.status_code == 200, r.text
        out = r.json()
        assert (out["set_id"], out["character_id"]) == (set_id, None)
        assert (out["count"], out["untagged"], out["near_duplicates"]) == (3, 1, 2)
        assert out["min_pictures"] == 6
        assert out["character"] is None

        char_id = _make_character(owner, "Grandad")
        ref_id = _reference_set_of(server, char_id)
        assert ref_id is not None
        # A reference set alone is the character's.
        r = owner.get("/training/dataset", params={"set_id": ref_id})
        out = r.json()
        assert out["character_id"] == char_id
        assert out["character"]["name"] == "Grandad"
        assert out["character"]["reference_set_id"] == ref_id
        assert out["character"]["reference_count"] == 3
        assert out["character"]["all_count"] == _PICTURES["count"]
        # The clips a clip recipe adds ride beside each count.
        assert (out["character"]["reference_clips"], out["character"]["all_clips"]) == (
            2,
            1,
        )

        r = owner.get("/training/dataset", params={"character_id": char_id})
        out = r.json()
        assert out["set_id"] is None
        assert out["character"]["all_count"] == 3
        assert out["character"]["reference_count"] == _PICTURES["count"]
        assert (out["character"]["all_clips"], out["character"]["reference_clips"]) == (
            2,
            1,
        )

        # A set under the person, or under a set under the person, is theirs:
        # a character LoRA trained on that set's pictures (#2124).
        r = owner.post(
            "/picture_sets", json={"name": "LoRA v3", "parent_character_id": char_id}
        )
        child_id = r.json()["picture_set"]["id"]
        r = owner.post("/picture_sets", json={"name": "v3b", "parent_set_id": child_id})
        grandchild_id = r.json()["picture_set"]["id"]
        for sid, name in ((child_id, "LoRA v3"), (grandchild_id, "v3b")):
            r = owner.get("/training/dataset", params={"set_id": sid})
            assert r.status_code == 200, r.text
            out = r.json()
            assert (out["set_id"], out["character_id"]) == (sid, char_id)
            assert out["character"]["child_set"] == {
                "id": sid,
                "name": name,
                "count": 3,
                "clips": 2,
            }
            assert out["character"]["reference_count"] == _PICTURES["count"]
        r = owner.get(
            "/training/dataset", params={"set_id": child_id, "character_id": char_id}
        )
        assert r.status_code == 200, r.text
        # A set under the person's reference set is theirs too.
        r = owner.post(
            "/picture_sets", json={"name": "ref kid", "parent_set_id": ref_id}
        )
        ref_kid = r.json()["picture_set"]["id"]
        r = owner.get("/training/dataset", params={"set_id": ref_kid})
        assert r.json()["character_id"] == char_id
        assert r.json()["character"]["child_set"]["id"] == ref_kid
        # The reference set itself is not a child set.
        r = owner.get("/training/dataset", params={"set_id": ref_id})
        assert r.json()["character"]["child_set"] is None
        # A set under an ordinary set is a concept's: no character.
        r = owner.post("/picture_sets", json={"name": "Neon", "parent_set_id": set_id})
        concept_id = r.json()["picture_set"]["id"]
        r = owner.get("/training/dataset", params={"set_id": concept_id})
        assert r.json()["character"] is None
        assert r.json()["parent_set_id"] == set_id
        r = owner.get("/training/dataset", params={"set_id": set_id})
        assert r.json()["parent_set_id"] is None

        # A character that does not exist is a 404, not a 500.
        r = owner.get("/training/dataset", params={"character_id": 9999})
        assert r.status_code == 404, r.text

        # Another set with the character is refused.
        r = owner.get(
            "/training/dataset", params={"set_id": set_id, "character_id": char_id}
        )
        assert r.status_code == 400, r.text

        scoped = _scoped_client(server, read_token)
        r = scoped.get("/training/dataset", params={"set_id": set_id})
        assert r.status_code == 403, r.text
    finally:
        ExportUtils.training_facts = staticmethod(facts)
        _teardown(temp_dir, server)


def test_create_job_on_a_reference_set_is_filed_under_its_character():
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner, "Grandad")
        ref_id = _reference_set_of(server, char_id)
        body = {"name": "x", "trigger_word": "grandad"}
        for scope in ({"set_id": ref_id}, {"set_id": ref_id, "character_id": char_id}):
            r = owner.post("/training/jobs", json={**body, **scope})
            assert r.status_code == 200, r.text
            assert (r.json()["set_id"], r.json()["character_id"]) == (ref_id, char_id)
            # A reference set is a person, as a character is.
            assert r.json()["resolved_profile"]["subject"] == "person"
        call = server.vault.training_service.enqueue_calls[-1]
        assert (call["set_id"], call["character_id"]) == (ref_id, char_id)
        # Its default prompts are a person's too, without being told.
        r = owner.get("/training/sample-prompts", params={"set_id": ref_id})
        assert r.status_code == 200, r.text
        assert r.json()["subject"] == "person"
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# ALL + resource_type token footgun (CLAUDE.md §16.2)
#
# An ``ALL``-scope token narrowed to a ``resource_type`` leaves the middleware's
# ``request.state.token_scope`` unset (``None``), the same state a real owner
# session has. A guard that only checks ``token_scope is None`` would treat it
# as the owner. The authz gate's owner policies use ``require_unscoped_owner``,
# which additionally rejects ``matched_token.resource_type is not None``. These
# tests mint exactly that token and assert it is denied on both a read and a
# mutate endpoint (and that the mutate is never actioned).
# ---------------------------------------------------------------------------


def _all_resource_token(server) -> str:
    """Construct the ALL+resource_type "footgun" token directly in the DB.

    The supported token API (``auth.create_token``) now rejects this
    scope+resource combination at the source (the F3 fix in
    feature-slick-grid-updates), so it can no longer be minted via
    ``POST /users/me/token``. We insert the row directly to prove the training
    handler guard is genuine defense-in-depth: even if such a token somehow
    existed (legacy, or minted before that fix), every training endpoint must
    still reject it (403), because ``require_unscoped_owner`` checks
    ``matched_token.resource_type``.
    """
    import secrets

    from passlib.hash import bcrypt
    from sqlmodel import select

    from pixlstash.db_models.user import User
    from pixlstash.db_models.user_token import UserToken

    token_value = secrets.token_urlsafe(32)

    def _insert(session):
        # Identity and tokens live in the hub; the owner is its first User row.
        user = session.exec(select(User)).first()
        session.add(
            UserToken(
                user_id=user.id,
                library_uuid=server._active_library_uuid(),
                token_hash=bcrypt.hash(token_value),
                token_prefix=token_value[:8],
                description="all-narrowed (direct insert; unmintable via API)",
                scope="ALL",
                resource_type="picture",
                resource_id=1,
            )
        )
        session.commit()

    server.hub_engine.run_task(_insert)
    return token_value


def test_create_job_all_resource_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        token = _all_resource_token(server)
        client = TestClient(server.api, raise_server_exceptions=True)
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post(
            "/training/jobs",
            json={"set_id": 1, "name": "x", "trigger_word": "tw"},
        )
        assert r.status_code == 403, r.text
        # The guard must fire before the service is touched.
        assert server.vault.training_service.enqueue_calls == []
    finally:
        _teardown(temp_dir, server)


def _walk_strings(value):
    """Yield every string leaf of a JSON-decoded response body."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str):
        yield value


def test_create_job_refuses_locked_and_untunable_pins_before_enqueue():
    """A pin on a locked recipe key, or on a key outside the tunables, is a 422
    naming the key, and nothing is queued. The raw config patch is gone."""
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        for pins, key in (
            ({"model.name_or_path": "/evil"}, "model.name_or_path"),
            ({"model.arch": "flux"}, "model.arch"),
            ({"training_folder": "/etc"}, "training_folder"),
            ({"datasets[*].folder_path": "/etc"}, "folder_path"),
        ):
            r = owner.post(
                "/training/jobs",
                json={
                    "character_id": char_id,
                    "name": "x",
                    "trigger_word": "tw",
                    "pins": pins,
                },
            )
            assert r.status_code == 422, r.text
            assert key in r.text, r.text
        assert (
            "fixed by the Z-Image Turbo recipe"
            in owner.post(
                "/training/jobs",
                json={
                    "character_id": char_id,
                    "name": "x",
                    "trigger_word": "tw",
                    "pins": {"model.arch": "flux"},
                },
            ).json()["detail"]
        )
        r = owner.post(
            "/training/jobs",
            json={
                "character_id": char_id,
                "name": "x",
                "trigger_word": "tw",
                "config_overrides": {"config": {}},
            },
        )
        assert r.status_code == 422, r.text
        assert server.vault.training_service.enqueue_calls == []
    finally:
        _teardown(temp_dir, server)


def test_create_job_pins_win_and_are_recorded():
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        pins = {"train.steps": 50, "train.lr": 0.0002}
        r = owner.post(
            "/training/jobs",
            json={
                "character_id": char_id,
                "name": "x",
                "trigger_word": "tw",
                "pins": pins,
            },
        )
        assert r.status_code == 200, r.text
        call = server.vault.training_service.enqueue_calls[-1]
        assert call["pins"] == pins
        train = call["config_overrides"]["config"]["process"][0]["train"]
        assert train["steps"] == 50 and train["lr"] == 0.0002
        values = r.json()["resolved_profile"]["values"]
        assert values["train.steps"] == {"value": 50, "source": "pinned"}

        detail = owner.get(f"/training/jobs/{r.json()['id']}").json()
        assert detail["preset"] == "zimage-turbo@1"
        assert detail["advanced_pins"] == pins
        assert detail["resolved_profile"]["profile"] == "q8"
    finally:
        _teardown(temp_dir, server)


def test_create_job_resolves_a_16gb_card_and_refuses_a_small_one():
    """The fit is decided from the card: a 16 GB card trains with captions
    encoded once; an 8 GB card is refused naming GPU memory, and nothing is
    queued for it. Best quality on 16 GB is refused rather than shrunk."""
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        body = {"character_id": char_id, "name": "x", "trigger_word": "tw"}
        server.vault.training_room = lambda: dict(_ROOM_16GB)
        r = owner.post("/training/jobs", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["resolved_profile"]["profile"] == "q8_cached"

        calls = len(server.vault.training_service.enqueue_calls)
        r = owner.post("/training/jobs", json={**body, "fit": "best_quality"})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["budget"] == "vram"

        server.vault.training_room = lambda: {"vram_mb": 7_000, "ram_mb": 60_000}
        r = owner.post("/training/jobs", json=body)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["budget"] == "vram"
        assert r.json()["detail"]["free_mb"] == 7_000

        server.vault.training_room = lambda: {"vram_mb": None, "ram_mb": 60_000}
        r = owner.post("/training/jobs", json=body)
        assert r.status_code == 422, r.text
        assert len(server.vault.training_service.enqueue_calls) == calls
    finally:
        _teardown(temp_dir, server)


def test_create_job_refuses_a_set_with_no_pictures():
    temp_dir, owner, server, read_token = _setup()
    try:
        set_id = _make_picture_set(owner)
        _PICTURES["count"] = 0
        r = owner.post(
            "/training/jobs",
            json={
                "set_id": set_id,
                "name": "x",
                "trigger_word": "tw",
                "subject": "thing",
            },
        )
        assert r.status_code == 422, r.text
        assert "no pictures" in r.text
        assert server.vault.training_service.enqueue_calls == []

        # A set of clips alone: the motion recipe counts the clips a stills
        # recipe leaves out, so it gets past the empty-set refusal.
        ExportUtils.count_training_pictures = staticmethod(
            lambda server, clips=False, **kwargs: 3 if clips else 0
        )
        repo = _fake_checkout(
            os.path.join(temp_dir.name, "h3kit"), archs=("minimax_h3",)
        )
        _save_checkout(owner, repo, _venv_python(repo))
        body = {"set_id": set_id, "name": "x", "trigger_word": "tw"}
        for preset, empty in (("minimax-h3-image", True), ("minimax-h3-motion", False)):
            r = owner.post(
                "/training/jobs", json={**body, "subject": "thing", "preset": preset}
            )
            assert ("no pictures" in r.text) is empty, (preset, r.text)
    finally:
        _teardown(temp_dir, server)


def test_create_job_on_a_set_needs_a_subject_and_a_capable_checkout(tmp_path):
    temp_dir, owner, server, read_token = _setup()
    try:
        set_id = _make_picture_set(owner)
        body = {"set_id": set_id, "name": "x", "trigger_word": "tw"}
        r = owner.post("/training/jobs", json=body)
        assert r.status_code == 422, r.text
        assert "person, style or thing" in r.text

        r = owner.post("/training/jobs", json={**body, "subject": "style"})
        assert r.status_code == 200, r.text
        values = r.json()["resolved_profile"]["values"]
        assert values["train.content_or_style"] == {
            "value": "style",
            "source": "subject",
        }

        # A saved checkout without the z_image model code cannot train it.
        repo = _fake_checkout(str(tmp_path / "kit"), archs=("flux2",))
        _save_checkout(owner, repo, _venv_python(repo))
        calls = len(server.vault.training_service.enqueue_calls)
        r = owner.post("/training/jobs", json={**body, "subject": "style"})
        assert r.status_code == 422, r.text
        assert "z_image" in r.text
        assert len(server.vault.training_service.enqueue_calls) == calls
    finally:
        _teardown(temp_dir, server)


def test_job_responses_carry_no_absolute_host_paths():
    """Job detail, status and listing contain no absolute host path: the library
    root and the temp dir are redacted wherever they appear."""
    temp_dir, owner, server, read_token = _setup()
    try:
        image_root = server.vault.image_root
        config = {
            "config": {
                "process": [
                    {
                        "training_folder": os.path.join(image_root, "training", "j"),
                        "sqlite_db_path": os.path.join(image_root, "training", "j.db"),
                        "datasets": [
                            {"folder_path": os.path.join(image_root, "ds", "x")}
                        ],
                        "train": {"steps": 3000},
                    }
                ]
            }
        }
        job_id, _ = _seed_job(
            server,
            rendered_config=json.dumps(config),
            report=None,
            error=f"trainer failed reading {image_root}/training/j/log.txt",
        )
        detail = owner.get(f"/training/jobs/{job_id}").json()
        status = owner.get(f"/training/jobs/{job_id}/status").json()
        listing = owner.get("/training/jobs").json()
        for body in (detail, status, listing):
            for text in _walk_strings(body):
                assert not text.startswith(image_root), text
                assert not text.startswith(temp_dir.name), text
                assert image_root not in text, text
        process = detail["rendered_config"]["config"]["process"][0]
        assert process["training_folder"] == "<host path>"
        assert process["train"]["steps"] == 3000
        assert "<host path>" in detail["error"]
        assert "<host path>" in status["info"]
    finally:
        _teardown(temp_dir, server)


def test_host_path_redaction_covers_spaces_file_uris_and_windows_paths():
    """A path with spaces is redacted to its end, a file:// URI is redacted, and
    a Windows path still is. Found by review F5: the old pattern stopped at the
    first space and never matched a URI."""
    assert _redact_host_paths("/Users/Alice Smith/AI Toolkit/run.py") == "<host path>"
    assert _redact_host_paths("file:///home/alice/x") == "<host path>"
    assert _redact_host_paths(r"C:\Users\alice\ai\run.py") == "<host path>"
    assert (
        _redact_host_paths('{"python": "/home/alice/.venv/bin/python"}')
        == '{"python": "<host path>"}'
    )
    # URLs and ratios are not paths.
    assert _redact_host_paths("https://example.com/a/b") == "https://example.com/a/b"
    assert _redact_host_paths("1/2 and/or") == "1/2 and/or"


def test_job_error_with_spaced_path_and_file_uri_is_redacted():
    """The route-level half of the redaction test: a report and an error that
    name a spaced path and a file:// URI carry neither name back. The job's name
    and trigger word are the owner's own text and come back as written, even
    where the path pattern would read them as a path (#2294)."""
    temp_dir, owner, server, read_token = _setup()
    try:
        name, trigger = "Cats / Dogs (cdx)", "sky 1:1 / wide"
        job_id, _ = _seed_job(
            server,
            name=name,
            trigger_word=trigger,
            report="wrote /Users/Alice Smith/AI Toolkit/run.py",
            error="cannot open file:///home/alice/x",
        )
        detail = owner.get(f"/training/jobs/{job_id}").json()
        assert detail["report"] == "wrote <host path>", detail["report"]
        assert detail["error"] == "cannot open <host path>", detail["error"]
        for text in _walk_strings(detail):
            assert "Smith" not in text and "alice" not in text, text

        (listed,) = [j for j in owner.get("/training/jobs").json() if j["id"] == job_id]
        live = owner.get(f"/training/jobs/{job_id}/live").json()
        # The run page's "Full message" reads it here.
        assert live["report"] == "wrote <host path>", live.get("report")
        for body in (detail, listed, live):
            assert body["name"] == name, body["name"]
            assert body["trigger_word"] == trigger, body["trigger_word"]

        set_id = _make_picture_set(owner)
        r = owner.post(
            "/training/jobs",
            json={
                "set_id": set_id,
                "name": name,
                "trigger_word": trigger,
                "subject": "style",
            },
        )
        assert r.status_code == 200, r.text
        assert (r.json()["name"], r.json()["trigger_word"]) == (name, trigger)
    finally:
        _teardown(temp_dir, server)


def test_put_config_refuses_ai_toolkit_values_that_are_not_real():
    """Each invalid repo or python value is a 422 with a message, and nothing is
    stored. Both null (disconnect) is accepted."""
    temp_dir, owner, server, read_token = _setup(checkout=False)
    try:
        repo_no_run = os.path.join(temp_dir.name, "repo-without-run")
        os.makedirs(repo_no_run)
        not_exec = os.path.join(temp_dir.name, "not-executable")
        with open(not_exec, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(not_exec, 0o644)
        # #2295: run.py alone is not a checkout, and the Python must be its own.
        repo_no_toolkit = os.path.join(temp_dir.name, "repo-without-toolkit")
        os.makedirs(repo_no_toolkit)
        with open(os.path.join(repo_no_toolkit, "run.py"), "w") as fh:
            fh.write("")
        checkout = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        own_python = _venv_python(checkout)
        stranger = os.path.join(temp_dir.name, "stranger-python")
        with open(stranger, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(stranger, 0o755)
        bad = [
            {"repo_path": repo_no_toolkit, "python": None},
            {"repo_path": checkout, "python": stranger},
            {"repo_path": checkout, "python": sys.executable},
            {"repo_path": None, "python": own_python},
            {
                "repo_path": checkout,
                "python": os.path.join(
                    checkout, ".venv", "..", "..", "stranger-python"
                ),
            },
            {"repo_path": "relative/ai-toolkit", "python": None},
            {"repo_path": os.path.join(temp_dir.name, "missing"), "python": None},
            {"repo_path": repo_no_run, "python": None},
            {"repo_path": None, "python": "python3"},
            {
                "repo_path": None,
                "python": os.path.join(temp_dir.name, "missing-python"),
            },
            {"repo_path": None, "python": not_exec},
        ]
        for body in bad:
            r = owner.put("/training/ai-toolkit/config", json=body)
            assert r.status_code == 422, (body, r.text)
            assert r.json()["detail"], r.text
        cfg = owner.get("/users/me/config").json()
        assert cfg["ai_toolkit_repo_path"] in (None, "")
        assert cfg["ai_toolkit_python"] in (None, "")

        r = owner.put(
            "/training/ai-toolkit/config",
            json={"repo_path": checkout, "python": own_python},
        )
        assert r.status_code == 200, r.text

        r = owner.put(
            "/training/ai-toolkit/config", json={"repo_path": None, "python": None}
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"repo_path": None, "python": None}
    finally:
        _teardown(temp_dir, server)


def test_patch_config_refuses_the_ai_toolkit_keys_and_names_the_route():
    """PATCH /users/me/config no longer writes the ai-toolkit keys: each one is a
    422 that names PUT /api/v1/training/ai-toolkit/config, and nothing is stored.
    This holds from the owner's own machine too (review F2, F6)."""
    temp_dir, owner, server, read_token = _setup(checkout=False)
    try:
        for key in ("ai_toolkit_repo_path", "ai_toolkit_python", "ai_toolkit_commit"):
            r = owner.patch("/users/me/config", json={key: sys.executable})
            assert r.status_code == 422, (key, r.text)
            assert "PUT /api/v1/training/ai-toolkit/config" in r.json()["detail"]
        cfg = owner.get("/users/me/config").json()
        assert cfg["ai_toolkit_repo_path"] in (None, "")
        assert cfg["ai_toolkit_python"] in (None, "")
        assert cfg["ai_toolkit_commit"] in (None, "")
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# ai-toolkit install: discover, check, status (Settings › AI-toolkit)
# ---------------------------------------------------------------------------


def _fake_checkout(root, version="0.12.26", archs=("z_image",)):
    """An ai-toolkit checkout as far as the install routes can tell, shipping
    the model code of *archs*."""
    os.makedirs(os.path.join(root, "toolkit"), exist_ok=True)
    for arch in archs:
        os.makedirs(
            os.path.join(root, "extensions_built_in", "diffusion_models", arch),
            exist_ok=True,
        )
    with open(os.path.join(root, "run.py"), "w", encoding="utf-8") as fh:
        fh.write("")
    with open(os.path.join(root, "version.py"), "w", encoding="utf-8") as fh:
        fh.write(f'VERSION = "{version}"\n')
    return root


def _save_checkout(owner, repo, python=None):
    """Save the owner's ai-toolkit paths the way the Settings dialog does."""
    r = owner.put(
        "/training/ai-toolkit/config", json={"repo_path": repo, "python": python}
    )
    assert r.status_code == 200, r.text


def _venv_python(repo):
    """A ``.venv/bin/python`` that is an executable file (a link to ours)."""
    bin_dir = os.path.join(repo, ".venv", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    link = os.path.join(bin_dir, "python")
    try:
        os.symlink(sys.executable, link)
    except (OSError, NotImplementedError):
        # Windows without developer mode or admin rights cannot make symlinks.
        shutil.copy(sys.executable, link)
    return link


def _probe_ok_runner(argv, cwd, timeout):
    """Stands in for the checkout's Python: torch sees CUDA, run.py --help exits 0."""
    if argv[1:2] == ["-c"]:
        return 0, '{"torch": "2.13.0+cu128", "cuda": true, "gpus": []}\n', ""
    return 0, "usage", ""


def test_ai_toolkit_status_is_unconfigured_until_a_checkout_is_saved():
    temp_dir, owner, server, read_token = _setup(checkout=False)
    try:
        r = owner.get("/training/ai-toolkit/status")
        assert r.status_code == 200, r.text
        assert r.json() == {
            "configured": False,
            "repo_path": None,
            "python": None,
            "version": None,
            "datasets": None,
            "runs": None,
        }
    finally:
        _teardown(temp_dir, server)


def test_ai_toolkit_status_reports_the_saved_checkout_and_its_folders():
    temp_dir, owner, server, read_token = _setup()
    try:
        repo = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        python = _venv_python(repo)
        _save_checkout(owner, repo, python)

        status = owner.get("/training/ai-toolkit/status").json()
        assert status["configured"] is True
        assert status["repo_path"] == repo
        assert status["python"] == python
        assert status["version"] == "0.12.26"
        # No datasets folder yet: the checkout must be writable, since that is
        # where the first job creates it.
        assert status["datasets"] == {
            "path": os.path.join(repo, "datasets"),
            "writable": True,
        }
        # No shelf folder: runs go to <repo>/output and are not on the shelf.
        assert status["runs"] == {
            "path": os.path.join(repo, "output"),
            "run_count": 0,
            "on_shelf": False,
        }
    finally:
        _teardown(temp_dir, server)


def test_ai_toolkit_status_counts_runs_in_the_registered_shelf_folder():
    """Once a source folder is on the shelf, runs are counted there, and
    on_shelf says so. A run folder is one holding a checkpoint."""
    from pixlstash.routes.model_folders import register_model_folder

    temp_dir, owner, server, read_token = _setup()
    try:
        repo = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        _save_checkout(owner, repo, _venv_python(repo))
        shelf = os.path.join(temp_dir.name, "shelf-output")
        run = os.path.join(shelf, "trained_abcd1234")
        os.makedirs(run)
        open(os.path.join(run, "trained_abcd1234.safetensors"), "wb").close()
        register_model_folder(server, shelf, "source")

        runs = owner.get("/training/ai-toolkit/status").json()["runs"]
        assert runs == {
            "path": os.path.realpath(shelf),
            "run_count": 1,
            "on_shelf": True,
        }
    finally:
        _teardown(temp_dir, server)


def test_ai_toolkit_discover_finds_the_saved_checkout_and_its_python(monkeypatch):
    temp_dir, owner, server, read_token = _setup()
    try:
        repo = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        _venv_python(repo)
        _save_checkout(owner, repo)

        r = owner.get("/training/ai-toolkit/discover")
        assert r.status_code == 200, r.text
        found = r.json()
        assert found["found"] is True
        assert found["repo_path"] == repo
        assert found["python"] == os.path.join(repo, ".venv", "bin", "python")
        assert found["version"] == "0.12.26"
        assert found["searched"][0] == repo
    finally:
        _teardown(temp_dir, server)


def test_ai_toolkit_check_with_no_python_finds_the_checkouts_own_and_reports_folders(
    monkeypatch,
):
    """Omitted python: the checkout's .venv is used, the probe and the trial run
    through it, and the response carries the folders /status would report."""
    from pixlstash.training import ai_toolkit_install

    monkeypatch.setattr(ai_toolkit_install, "run_host_process", _probe_ok_runner)
    temp_dir, owner, server, read_token = _setup()
    try:
        repo = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        venv_python = _venv_python(repo)
        # Not saved: a checkout chosen by hand must be checkable before Connect.
        r = owner.post(
            "/training/ai-toolkit/check",
            json={"repo_path": repo},
        )
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["ok"] is True, result
        assert result["python"] == venv_python
        assert result["version"] == "0.12.26"
        assert result["datasets"] == {
            "path": os.path.join(repo, "datasets"),
            "writable": True,
        }
        assert result["runs"] == {
            "path": os.path.join(repo, "output"),
            "run_count": 0,
            "on_shelf": False,
        }
    finally:
        _teardown(temp_dir, server)


def test_ai_toolkit_check_folders_match_status_for_the_same_checkout(monkeypatch):
    from pixlstash.training import ai_toolkit_install

    monkeypatch.setattr(ai_toolkit_install, "run_host_process", _probe_ok_runner)
    temp_dir, owner, server, read_token = _setup()
    try:
        repo = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        python = _venv_python(repo)
        _save_checkout(owner, repo, python)
        check = owner.post(
            "/training/ai-toolkit/check",
            json={"repo_path": repo, "python": python},
        ).json()
        status = owner.get("/training/ai-toolkit/status").json()
        assert check["python"] == python
        assert check["datasets"] == status["datasets"]
        assert check["runs"] == status["runs"]
        # The checkout ships z_image, so the Z-Image Turbo recipe is offered.
        recipe = next(p for p in check["presets"] if p["id"] == "zimage-turbo")
        assert recipe["offered"] is True
    finally:
        _teardown(temp_dir, server)


def test_ai_toolkit_check_says_when_no_python_can_be_found(monkeypatch):
    from pixlstash.training import ai_toolkit_install

    monkeypatch.setattr(ai_toolkit_install, "run_host_process", _probe_ok_runner)
    monkeypatch.setattr(ai_toolkit_install, "find_python", lambda repo: None)
    temp_dir, owner, server, read_token = _setup()
    try:
        repo = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        r = owner.post("/training/ai-toolkit/check", json={"repo_path": repo})
        assert r.status_code == 422, r.text
        assert "No Python found" in r.json()["detail"]
    finally:
        _teardown(temp_dir, server)


def test_ai_toolkit_check_refuses_paths_that_are_not_real(monkeypatch):
    """Each bad value is a 422 with a message and no process is started."""
    from pixlstash.training import ai_toolkit_install

    started = []

    def runner(argv, cwd, timeout):
        started.append(argv)
        return _probe_ok_runner(argv, cwd, timeout)

    monkeypatch.setattr(ai_toolkit_install, "run_host_process", runner)
    temp_dir, owner, server, read_token = _setup()
    try:
        repo = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        bad = [
            {"repo_path": ""},
            {"repo_path": "relative/kit"},
            {"repo_path": os.path.join(temp_dir.name, "missing")},
            {"repo_path": repo, "python": "python3"},
            {"repo_path": repo, "python": os.path.join(temp_dir.name, "no-python")},
        ]
        for body in bad:
            r = owner.post("/training/ai-toolkit/check", json=body)
            assert r.status_code == 422, (body, r.text)
            assert r.json()["detail"], r.text
        assert started == [], "a refused path must not start a process"
    finally:
        _teardown(temp_dir, server)


def test_ai_toolkit_check_runs_one_at_a_time(monkeypatch):
    """#2295: a check can hold a worker thread for minutes, so a second one while
    it runs is a 409 and starts nothing; once it ends, the next one runs."""
    from pixlstash.training import ai_toolkit_install

    started = []
    release = threading.Event()

    def runner(argv, cwd, timeout):
        started.append(argv)
        release.wait(10)
        return _probe_ok_runner(argv, cwd, timeout)

    monkeypatch.setattr(ai_toolkit_install, "run_host_process", runner)
    temp_dir, owner, server, read_token = _setup()
    try:
        repo = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        _venv_python(repo)
        first = {}
        worker = threading.Thread(
            target=lambda: first.update(
                r=owner.post("/training/ai-toolkit/check", json={"repo_path": repo})
            )
        )
        worker.start()
        deadline = time.monotonic() + 10
        while not started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started, "the first check never started"
        r = owner.post("/training/ai-toolkit/check", json={"repo_path": repo})
        assert r.status_code == 409, r.text
        assert len(started) == 1, "a refused check must not start a process"
        release.set()
        worker.join(10)
        assert first["r"].status_code == 200, first["r"].text
        r = owner.post("/training/ai-toolkit/check", json={"repo_path": repo})
        assert r.status_code == 200, r.text
    finally:
        release.set()
        _teardown(temp_dir, server)


def test_ai_toolkit_install_routes_refuse_a_read_token():
    temp_dir, owner, server, read_token = _setup()
    try:
        scoped = _scoped_client(server, read_token)
        assert scoped.get("/training/ai-toolkit/discover").status_code == 403
        assert scoped.get("/training/ai-toolkit/status").status_code == 403
        assert (
            scoped.put(
                "/training/ai-toolkit/config", json={"repo_path": None, "python": None}
            ).status_code
            == 403
        )
        assert (
            scoped.post(
                "/training/ai-toolkit/check", json={"repo_path": "/x"}
            ).status_code
            == 403
        )
        # The owner reaches the same live routes, so the 403s are the scope.
        assert owner.get("/training/ai-toolkit/status").status_code == 200
        assert owner.get("/training/ai-toolkit/discover").status_code == 200

        # The second belt: with the gate rolled back, the READ-blocked GET paths
        # still refuse the share token (both GETs are untemplated).
        previously = server.authz._enforcing
        server.authz._enforcing = False
        try:
            for path in (
                "/training/ai-toolkit/discover",
                "/training/ai-toolkit/status",
            ):
                assert scoped.get(path).status_code == 403, path
                assert owner.get(path).status_code == 200, path
        finally:
            server.authz._enforcing = previously
    finally:
        _teardown(temp_dir, server)


def test_the_server_binds_the_shelf_folder_registrar_at_start_up():
    """A real Server gives its training service the same registration the folder
    routes use, so the first job can add <repo>/output to the shelf."""
    temp_dir = tempfile.TemporaryDirectory()
    try:
        server_config_path = os.path.join(temp_dir.name, "server-config.json")
        with open(server_config_path, "w") as f:
            f.write(json.dumps({"port": 8000, "disable_background_workers": True}))
        server = Server(server_config_path)
        try:
            service = server.vault.training_service
            assert service._register_source_folder is not None
            folder = os.path.join(temp_dir.name, "kit", "output")
            os.makedirs(folder)
            service._register_source_folder(folder)
            assert service._shelf_source_folder() == os.path.realpath(folder)
        finally:
            server.close()
    finally:
        temp_dir.cleanup()
        gc.collect()


def test_put_config_disconnect_clears_both_ai_toolkit_paths():
    """Disconnect is PUTting both to null: accepted, and the saved values read
    back as null."""
    temp_dir, owner, server, read_token = _setup()
    try:
        repo = _fake_checkout(os.path.join(temp_dir.name, "kit"))
        _save_checkout(owner, repo, _venv_python(repo))
        assert owner.get("/training/ai-toolkit/status").json()["configured"] is True

        r = owner.put(
            "/training/ai-toolkit/config", json={"repo_path": None, "python": None}
        )
        assert r.status_code == 200, r.text
        cfg = owner.get("/users/me/config").json()
        assert cfg["ai_toolkit_repo_path"] in (None, "")
        assert cfg["ai_toolkit_python"] in (None, "")
        assert owner.get("/training/ai-toolkit/status").json()["configured"] is False
    finally:
        _teardown(temp_dir, server)


def test_delete_job_removes_its_dataset_and_work_dir_never_the_shelf_run():
    """Job delete removes the work dir and the pixlstash-<uuid> dataset it wrote.
    A run folder in the shelf's source folder stays, and so does a dataset path
    that is not the one PixlStash names, or that sits inside a shelf folder."""
    from pixlstash.routes.model_folders import register_model_folder

    temp_dir, owner, server, read_token = _setup()
    try:
        shelf = os.path.join(temp_dir.name, "shelf-output")
        shelf_run = os.path.join(shelf, "trained_run")
        os.makedirs(shelf_run)
        shelf_file = os.path.join(shelf_run, "trained_run.safetensors")
        open(shelf_file, "wb").close()
        register_model_folder(server, shelf, "source")

        repo = os.path.join(temp_dir.name, "kit")
        datasets = os.path.join(repo, "datasets")
        job_id, job_uuid = _seed_job(server)
        dataset = os.path.join(datasets, f"pixlstash-{job_uuid}")
        os.makedirs(dataset)
        open(os.path.join(dataset, "img.png"), "wb").close()
        work_dir = os.path.join(server.vault.image_root, "training", job_uuid)
        os.makedirs(work_dir)
        training_runs.update_run(
            server.vault.hub, job_uuid, dataset_snapshot_path=dataset
        )

        # A recorded path named like PixlStash's snapshot but not under a
        # datasets/ folder: removing it would take a folder PixlStash did not make.
        other_id, other_uuid = _seed_job(server)
        other = os.path.join(temp_dir.name, "elsewhere", f"pixlstash-{other_uuid}")
        os.makedirs(other)
        training_runs.update_run(
            server.vault.hub, other_uuid, dataset_snapshot_path=other
        )
        # A PixlStash-named dataset that sits inside the shelf folder.
        in_shelf_id, in_shelf_uuid = _seed_job(server)
        # Named and parented like PixlStash's own snapshot, but inside the shelf.
        in_shelf = os.path.join(shelf, "datasets", f"pixlstash-{in_shelf_uuid}")
        os.makedirs(in_shelf)
        training_runs.update_run(
            server.vault.hub, in_shelf_uuid, dataset_snapshot_path=in_shelf
        )

        assert owner.delete(f"/training/jobs/{job_id}").status_code == 200
        assert not os.path.exists(work_dir)
        assert not os.path.exists(dataset)
        assert os.path.isdir(datasets), "the checkout's datasets folder stays"
        assert os.path.exists(shelf_file), "the shelf run is never removed"

        assert owner.delete(f"/training/jobs/{other_id}").status_code == 200
        assert os.path.isdir(other), "a path outside datasets/ is left alone"

        assert owner.delete(f"/training/jobs/{in_shelf_id}").status_code == 200
        assert os.path.isdir(in_shelf), "a dataset inside the shelf is left alone"
        assert os.path.exists(shelf_file)
    finally:
        _teardown(temp_dir, server)
