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
import json
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

from pixlstash.hub import training_runs
from pixlstash.routes.training import _redact_host_paths
from pixlstash.server import Server


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _FakeTrainingService:
    """Records enqueue/cancel calls so tests need no GPU/subprocess.

    ``enqueue`` mirrors the real service's set/character invariant (raises
    ``ValueError`` unless exactly one is given) and writes a real hub
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

        run_id = training_runs.create_run(
            self._hub,
            library_uuid=self._library_uuid,
            uuid=_uuid.uuid4().hex,
            name=name,
            set_id=set_id,
            character_id=character_id,
            base_model=base_model,
            trigger_word=trigger_word,
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
    server.vault.training_service = _FakeTrainingService(
        server.vault.hub, server.vault.library_uuid
    )

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
        # base_model defaulted from the built-in template.
        assert body["base_model"]
        # The enqueue went through the (fake) service with a resolved template.
        call = server.vault.training_service.enqueue_calls[-1]
        assert call["trigger_word"] == "alice"
        assert call["template_path"].endswith(".json")
    finally:
        _teardown(temp_dir, server)


def test_create_job_takes_base_model_from_the_template_and_refuses_a_client_one():
    """The job's base model is what the trainer loads: the template's rendered
    model name, read by the server. A client-sent base_model is refused (review
    F4), so the row cannot claim a model the run did not train."""
    from pixlstash.routes.training import _template_builtin_dir

    temp_dir, owner, server, read_token = _setup()
    try:
        char_id = _make_character(owner)
        with open(
            os.path.join(_template_builtin_dir(), "zimage-turbo-lora.json"),
            encoding="utf-8",
        ) as fh:
            expected = json.load(fh)["config"]["process"][0]["model"]["name_or_path"]
        body = {"character_id": char_id, "name": "Alice", "trigger_word": "alice"}
        r = owner.post("/training/jobs", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["base_model"] == expected
        assert server.vault.training_service.enqueue_calls[-1]["base_model"] == expected

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
    name a spaced path and a file:// URI carry neither name back."""
    temp_dir, owner, server, read_token = _setup()
    try:
        job_id, _ = _seed_job(
            server,
            report="wrote /Users/Alice Smith/AI Toolkit/run.py",
            error="cannot open file:///home/alice/x",
        )
        detail = owner.get(f"/training/jobs/{job_id}").json()
        assert detail["report"] == "wrote <host path>", detail["report"]
        assert detail["error"] == "cannot open <host path>", detail["error"]
        for text in _walk_strings(detail):
            assert "Smith" not in text and "alice" not in text, text
    finally:
        _teardown(temp_dir, server)


def test_put_config_refuses_ai_toolkit_values_that_are_not_real():
    """Each invalid repo or python value is a 422 with a message, and nothing is
    stored. Both null (disconnect) is accepted."""
    temp_dir, owner, server, read_token = _setup()
    try:
        repo_no_run = os.path.join(temp_dir.name, "repo-without-run")
        os.makedirs(repo_no_run)
        not_exec = os.path.join(temp_dir.name, "not-executable")
        with open(not_exec, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(not_exec, 0o644)
        bad = [
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
    temp_dir, owner, server, read_token = _setup()
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


def _fake_checkout(root, version="0.12.26"):
    """An ai-toolkit checkout as far as the install routes can tell."""
    os.makedirs(os.path.join(root, "toolkit"), exist_ok=True)
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
    os.symlink(sys.executable, link)
    return link


def _probe_ok_runner(argv, cwd, timeout):
    """Stands in for the checkout's Python: torch sees CUDA, run.py --help exits 0."""
    if argv[1:2] == ["-c"]:
        return 0, '{"torch": "2.13.0+cu128", "cuda": true, "gpus": []}\n', ""
    return 0, "usage", ""


def test_ai_toolkit_status_is_unconfigured_until_a_checkout_is_saved():
    temp_dir, owner, server, read_token = _setup()
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
        python = sys.executable
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
        _save_checkout(owner, repo, sys.executable)
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
        _save_checkout(owner, repo, sys.executable)
        check = owner.post(
            "/training/ai-toolkit/check",
            json={"repo_path": repo, "python": sys.executable},
        ).json()
        status = owner.get("/training/ai-toolkit/status").json()
        assert check["python"] == sys.executable
        assert check["datasets"] == status["datasets"]
        assert check["runs"] == status["runs"]
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
        _save_checkout(owner, repo, sys.executable)
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
