"""Security + functional tests for the ai-toolkit training HTTP API.

Every endpoint is asserted in **both directions**:
  * an owner (cookie) session → works (200 / expected status), and
  * a scoped/share READ token → 403 (the deny-by-default owner policies the
    authz gate declares for these routes).

The :class:`~pixlstash.services.training_service.TrainingService` is stubbed
with a fake on ``server.vault.training_service`` for the enqueue/cancel paths so
no GPU subprocess is ever spawned; read/download/delete paths seed
``TrainingJob`` rows (and real work-directory files) directly via the DB task
runner.

See the training rows of ``docs/authz-coverage-matrix.md`` for the coverage matrix
this file backs.
"""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

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


def test_create_job_refuses_path_overrides_before_enqueue():
    """A config_overrides key outside the tunables is a 422 naming the key, and
    nothing is queued."""
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        for overrides, key in (
            ({"config": {"process": [{"training_folder": "/etc"}]}}, "training_folder"),
            (
                {"config": {"process": [{"datasets": [{"folder_path": "/etc"}]}]}},
                "folder_path",
            ),
        ):
            r = owner.post(
                "/training/jobs",
                json={
                    "character_id": char_id,
                    "name": "x",
                    "trigger_word": "tw",
                    "config_overrides": overrides,
                },
            )
            assert r.status_code == 422, r.text
            assert key in r.text, r.text
        assert server.vault.training_service.enqueue_calls == []
    finally:
        _teardown(temp_dir, server)


def test_create_job_accepts_an_allowed_tunable_override():
    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        overrides = {"config": {"process": [{"train": {"steps": 50, "lr": 0.0002}}]}}
        r = owner.post(
            "/training/jobs",
            json={
                "character_id": char_id,
                "name": "x",
                "trigger_word": "tw",
                "config_overrides": overrides,
            },
        )
        assert r.status_code == 200, r.text
        assert server.vault.training_service.enqueue_calls[-1]["config_overrides"] == (
            overrides
        )
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


def test_patch_config_refuses_ai_toolkit_values_that_are_not_real():
    """Each invalid repo or python value is a 422 with a message, and nothing is
    stored. Clearing a value is accepted."""
    temp_dir, owner, server, read_token = _setup()
    try:
        repo_no_run = os.path.join(temp_dir.name, "repo-without-run")
        os.makedirs(repo_no_run)
        not_exec = os.path.join(temp_dir.name, "not-executable")
        with open(not_exec, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(not_exec, 0o644)
        bad = [
            {"ai_toolkit_repo_path": "relative/ai-toolkit"},
            {"ai_toolkit_repo_path": os.path.join(temp_dir.name, "missing")},
            {"ai_toolkit_repo_path": repo_no_run},
            {"ai_toolkit_python": "python3"},
            {"ai_toolkit_python": os.path.join(temp_dir.name, "missing-python")},
            {"ai_toolkit_python": not_exec},
        ]
        for patch in bad:
            r = owner.patch("/users/me/config", json=patch)
            assert r.status_code == 422, (patch, r.text)
            assert r.json()["detail"], r.text
        cfg = owner.get("/users/me/config").json()
        assert cfg["ai_toolkit_repo_path"] in (None, "")
        assert cfg["ai_toolkit_python"] in (None, "")

        r = owner.patch("/users/me/config", json={"ai_toolkit_python": None})
        assert r.status_code == 200, r.text
    finally:
        _teardown(temp_dir, server)
