"""Security + functional tests for the ai-toolkit training HTTP API.

Every endpoint is asserted in **both directions**:
  * an owner (cookie) session → works (200 / expected status), and
  * a scoped/share READ token → 403 (the deny-by-default owner policies the
    authz gate declares for these routes).

The :class:`~pixlstash.services.training_service.TrainingService` is stubbed
with a fake on ``server.vault.training_service`` for the enqueue/cancel paths so
no GPU subprocess is ever spawned; read/download/delete paths seed
``TrainingJob`` / ``TrainedLora`` rows (and real artifact files) directly via the
DB task runner.

See the training rows of ``docs/authz-coverage-matrix.md`` for the coverage matrix
this file backs.
"""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

from pixlstash.db_models.trained_lora import TrainedLora
from pixlstash.db_models.training_job import TrainingJob
from pixlstash.server import Server


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _FakeTrainingService:
    """Records enqueue/cancel calls so tests need no GPU/subprocess.

    ``enqueue`` mirrors the real service's set/character invariant (raises
    ``ValueError`` unless exactly one is given) and persists a real
    ``TrainingJob`` row so the create response is realistic.
    """

    def __init__(self, db):
        self._db = db
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
    ):
        if (set_id is None) == (character_id is None):
            raise ValueError(
                "Exactly one of set_id / character_id must be provided "
                f"(got set_id={set_id!r}, character_id={character_id!r})."
            )
        self.enqueue_calls.append(
            {
                "set_id": set_id,
                "character_id": character_id,
                "name": name,
                "base_model": base_model,
                "trigger_word": trigger_word,
                "template_path": template_path,
                "config_overrides": config_overrides,
            }
        )

        import uuid as _uuid

        job_uuid = _uuid.uuid4().hex

        def _create(session):
            job = TrainingJob(
                uuid=job_uuid,
                name=name,
                status="queued",
                set_id=set_id,
                character_id=character_id,
                base_model=base_model,
                trigger_word=trigger_word,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            session.expunge(job)
            return job

        return self._db.run_task(_create)

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


def _setup():
    """Create a Server, log in as owner, and stub the training service.

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
    server.vault.training_service = _FakeTrainingService(server.vault.db)

    client = TestClient(server.api, raise_server_exceptions=True)
    resp = client.post("/login", json={"username": "owner", "password": "ownerpass1"})
    assert resp.status_code == 200, resp.text

    # Mint a global READ token (scoped → token_scope set → must be rejected).
    resp = client.post("/users/me/token", json={"description": "read", "scope": "READ"})
    assert resp.status_code == 200, resp.text
    read_token = resp.json()["token"]

    return temp_dir, client, server, read_token


def _teardown(temp_dir, server):
    server.close()
    temp_dir.cleanup()
    gc.collect()


def _scoped_client(server, read_token):
    """A TestClient that always sends the scoped READ bearer token."""
    client = TestClient(server.api, raise_server_exceptions=True)
    client.headers.update({"Authorization": f"Bearer {read_token}"})
    return client


def _seed_job(server, **overrides):
    """Insert a TrainingJob row directly and return its id+uuid."""
    import uuid as _uuid

    fields = {
        "uuid": _uuid.uuid4().hex,
        "name": "seeded-job",
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

    def _create(session):
        job = TrainingJob(**fields)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id, job.uuid

    return server.vault.db.run_task(_create)


def _seed_lora(
    server, *, job_id=None, file_rel="loras/testlora.safetensors", **overrides
):
    """Insert a TrainedLora row and write a real artifact file under image_root.

    Returns ``(lora_id, lora_uuid, abs_file_path)``.
    """
    import uuid as _uuid

    lora_uuid = overrides.pop("uuid", _uuid.uuid4().hex)
    image_root = server.vault.image_root
    abs_path = os.path.join(image_root, file_rel) if file_rel else None
    if abs_path:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as fh:
            fh.write(b"SAFETENSORS-FAKE-WEIGHTS")

    fields = {
        "uuid": lora_uuid,
        "job_id": job_id,
        "set_id": None,
        "character_id": None,
        "name": "my-lora",
        "file_path": file_rel,
        "file_size_bytes": len(b"SAFETENSORS-FAKE-WEIGHTS") if file_rel else None,
        "file_sha": "abc123",
        "step": 3000,
    }
    fields.update(overrides)

    def _create(session):
        lora = TrainedLora(**fields)
        session.add(lora)
        session.commit()
        session.refresh(lora)
        return lora.id

    lora_id = server.vault.db.run_task(_create)
    return lora_id, lora_uuid, abs_path


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
        # base_model defaulted from the built-in template.
        assert body["base_model"]
        # The enqueue went through the (fake) service with a resolved template.
        call = server.vault.training_service.enqueue_calls[-1]
        assert call["trigger_word"] == "alice"
        assert call["template_path"].endswith(".json")
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
        assert "exactly one" in r.json()["detail"].lower()
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


def test_create_job_unknown_template_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.post(
            "/training/jobs",
            json={
                "character_id": 7,
                "name": "x",
                "trigger_word": "alice",
                "template": "does-not-exist",
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

        r = owner.get("/training/jobs")
        assert r.status_code == 200, r.text
        ids = {row["id"] for row in r.json()}
        assert {id_set, id_char} <= ids

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


def test_delete_job_owner_removes_rows_and_files():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, job_uuid = _seed_job(server)
        lora_id, lora_uuid, lora_abs = _seed_lora(
            server, job_id=job_id, file_rel="loras/job_lora.safetensors"
        )
        # Create the job work dir + a lora sample dir so we can prove removal.
        work_dir = os.path.join(server.vault.image_root, "training", job_uuid)
        os.makedirs(work_dir, exist_ok=True)
        sample_dir = os.path.join(
            server.vault.image_root, "loras", f"{lora_uuid}_samples"
        )
        os.makedirs(sample_dir, exist_ok=True)

        r = owner.delete(f"/training/jobs/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "deleted"
        assert lora_id in body["deleted_lora_ids"]

        # Rows gone.
        r = owner.get(f"/training/jobs/{job_id}")
        assert r.status_code == 404
        r = owner.get(f"/loras/{lora_id}")
        assert r.status_code == 404
        # Files + dirs gone.
        assert not os.path.exists(lora_abs)
        assert not os.path.isdir(work_dir)
        assert not os.path.isdir(sample_dir)
    finally:
        _teardown(temp_dir, server)


def test_delete_job_missing_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.delete("/training/jobs/999999")
        assert r.status_code == 404, r.text
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
# GET /loras (list)
# ---------------------------------------------------------------------------


def test_list_loras_owner_works_and_filters():
    temp_dir, owner, server, read_token = _setup()
    try:
        set_id = _make_picture_set(owner)
        char_id = _make_character(owner)
        id_set, _, _ = _seed_lora(
            server, set_id=set_id, file_rel="loras/set.safetensors", name="set-lora"
        )
        id_char, _, _ = _seed_lora(
            server,
            character_id=char_id,
            file_rel="loras/char.safetensors",
            name="char-lora",
        )
        r = owner.get("/loras")
        assert r.status_code == 200, r.text
        ids = {row["id"] for row in r.json()}
        assert {id_set, id_char} <= ids

        r = owner.get("/loras", params={"set_id": set_id})
        assert {row["id"] for row in r.json()} == {id_set}
        r = owner.get("/loras", params={"character_id": char_id})
        assert {row["id"] for row in r.json()} == {id_char}
    finally:
        _teardown(temp_dir, server)


def test_list_loras_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        scoped = _scoped_client(server, read_token)
        r = scoped.get("/loras")
        assert r.status_code == 403, r.text
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /loras/{id} (detail)
# ---------------------------------------------------------------------------


def test_get_lora_detail_owner_works_with_job_link():
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(
            server,
            base_model="some/model",
            trigger_word="zztrig",
            ai_toolkit_commit="c0ffee",
        )
        lora_id, _, _ = _seed_lora(server, job_id=job_id)
        r = owner.get(f"/loras/{lora_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == lora_id
        assert body["job_id"] == job_id
        # Reproducibility link back to the job.
        assert body["job_base_model"] == "some/model"
        assert body["job_trigger_word"] == "zztrig"
        assert body["job_ai_toolkit_commit"] == "c0ffee"
    finally:
        _teardown(temp_dir, server)


def test_get_lora_detail_missing_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.get("/loras/999999")
        assert r.status_code == 404, r.text
    finally:
        _teardown(temp_dir, server)


def test_get_lora_detail_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        lora_id, _, _ = _seed_lora(server)
        scoped = _scoped_client(server, read_token)
        r = scoped.get(f"/loras/{lora_id}")
        assert r.status_code == 403, r.text
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /loras/{id}/download
# ---------------------------------------------------------------------------


def test_download_lora_owner_streams_bytes():
    temp_dir, owner, server, read_token = _setup()
    try:
        lora_id, _, _ = _seed_lora(server, file_rel="loras/dl.safetensors")
        r = owner.get(f"/loras/{lora_id}/download")
        assert r.status_code == 200, r.text
        assert r.content == b"SAFETENSORS-FAKE-WEIGHTS"
        assert "attachment" in r.headers.get("content-disposition", "")
    finally:
        _teardown(temp_dir, server)


def test_download_lora_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        lora_id, _, _ = _seed_lora(server)
        scoped = _scoped_client(server, read_token)
        r = scoped.get(f"/loras/{lora_id}/download")
        assert r.status_code == 403, r.text
    finally:
        _teardown(temp_dir, server)


def test_download_lora_missing_file_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        # Seed a row, then delete the backing file so the file is missing.
        lora_id, _, abs_path = _seed_lora(server, file_rel="loras/gone.safetensors")
        os.remove(abs_path)
        r = owner.get(f"/loras/{lora_id}/download")
        assert r.status_code == 404, r.text
    finally:
        _teardown(temp_dir, server)


def test_download_lora_path_escaping_image_root_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        # A tampered row pointing outside the vault must be rejected, not served.
        outside = os.path.join(temp_dir.name, "outside_secret.safetensors")
        with open(outside, "wb") as fh:
            fh.write(b"SECRET-OUTSIDE-VAULT")
        # Relative path that climbs out of image_root via ../.
        lora_id, _, _ = _seed_lora(server, file_rel="../outside_secret.safetensors")
        r = owner.get(f"/loras/{lora_id}/download")
        assert r.status_code == 404, r.text
        assert b"SECRET-OUTSIDE-VAULT" not in r.content
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /loras/{id}/file-path  (highest-risk: discloses a local path)
# ---------------------------------------------------------------------------


def test_file_path_owner_returns_in_vault_path():
    temp_dir, owner, server, read_token = _setup()
    try:
        lora_id, _, abs_path = _seed_lora(server, file_rel="loras/fp.safetensors")
        r = owner.get(f"/loras/{lora_id}/file-path")
        assert r.status_code == 200, r.text
        returned = r.json()["file_path"]
        assert os.path.isabs(returned)
        # Resolved path is within image_root and matches the real file.
        assert os.path.realpath(returned) == os.path.realpath(abs_path)
        assert os.path.realpath(server.vault.image_root) in os.path.realpath(returned)
    finally:
        _teardown(temp_dir, server)


def test_file_path_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        lora_id, _, _ = _seed_lora(server)
        scoped = _scoped_client(server, read_token)
        r = scoped.get(f"/loras/{lora_id}/file-path")
        assert r.status_code == 403, r.text
    finally:
        _teardown(temp_dir, server)


def test_file_path_escaping_image_root_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        outside = os.path.join(temp_dir.name, "outside2.safetensors")
        with open(outside, "wb") as fh:
            fh.write(b"x")
        lora_id, _, _ = _seed_lora(server, file_rel="../outside2.safetensors")
        r = owner.get(f"/loras/{lora_id}/file-path")
        assert r.status_code == 404, r.text
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# DELETE /loras/{id}
# ---------------------------------------------------------------------------


def test_delete_lora_owner_removes_row_and_file():
    temp_dir, owner, server, read_token = _setup()
    try:
        lora_id, lora_uuid, abs_path = _seed_lora(
            server, file_rel="loras/del.safetensors"
        )
        sample_dir = os.path.join(
            server.vault.image_root, "loras", f"{lora_uuid}_samples"
        )
        os.makedirs(sample_dir, exist_ok=True)

        r = owner.delete(f"/loras/{lora_id}")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "deleted"
        assert not os.path.exists(abs_path)
        assert not os.path.isdir(sample_dir)
        r = owner.get(f"/loras/{lora_id}")
        assert r.status_code == 404
    finally:
        _teardown(temp_dir, server)


def test_delete_lora_missing_is_404():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.delete("/loras/999999")
        assert r.status_code == 404, r.text
    finally:
        _teardown(temp_dir, server)


def test_delete_lora_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        lora_id, _, abs_path = _seed_lora(server)
        scoped = _scoped_client(server, read_token)
        r = scoped.delete(f"/loras/{lora_id}")
        assert r.status_code == 403, r.text
        # Row + file must survive a forbidden delete.
        assert os.path.exists(abs_path)
        r = owner.get(f"/loras/{lora_id}")
        assert r.status_code == 200
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# GET /training/templates
# ---------------------------------------------------------------------------


def test_list_templates_owner_works():
    temp_dir, owner, server, read_token = _setup()
    try:
        r = owner.get("/training/templates")
        assert r.status_code == 200, r.text
        names = {item["display_name"] for item in r.json()}
        # The shipped built-in template must be discoverable.
        assert "zimage-turbo-lora" in names
        entry = next(
            item for item in r.json() if item["display_name"] == "zimage-turbo-lora"
        )
        assert entry["source"] == "built-in"
        assert entry["base_model"]
        assert entry["description"]
    finally:
        _teardown(temp_dir, server)


def test_list_templates_scoped_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        scoped = _scoped_client(server, read_token)
        r = scoped.get("/training/templates")
        assert r.status_code == 403, r.text
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


def test_list_loras_all_resource_token_forbidden():
    temp_dir, owner, server, read_token = _setup()
    try:
        token = _all_resource_token(server)
        client = TestClient(server.api, raise_server_exceptions=True)
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.get("/loras")
        assert r.status_code == 403, r.text
    finally:
        _teardown(temp_dir, server)


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
